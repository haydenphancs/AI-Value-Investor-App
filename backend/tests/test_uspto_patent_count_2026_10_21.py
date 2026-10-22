"""The USPTO patent count asks for one record, and a refusal is logged and remembered.

Found 2026-10-20 by the benchmark check: `uspto.py` asked for 100 full file-wrapper records
to read only `count`. One record is 11-141 KB, so for Google, Tesla, NIKE, Lucid and Archer
Aviation the page went over the API's 6 MB response cap and came back HTTP 413 on EVERY report
collection: not logged, nothing cached, a new `rejected_no_data` audit row each time (53 since
2026-10-04), and those companies' Intangible Assets pillar never got its patents input.

Pinned here:
  * the request carries `limit: 1` (live probe 2026-10-21: all five answer 200 with it);
  * a 413 / 5xx is logged at WARNING with the ticker and status, never the key, and is not
    cached as data (no `ip_intel_cache` row, and a failed source is None, never a count of 0);
  * a definite refusal is not re-asked for a day; a transient failure is;
  * a payload built while a source failed is cached for a day, not 180 days.

Hermetic: the real `USPTOClient` on an `httpx.MockTransport` (no socket), a fake OpenFDA client
and a small in-memory stand-in for the two Supabase tables.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest

import app.services.ip_intel_service as ips
from app.integrations import uspto
from app.integrations.openfda import OpenFDAException
from app.integrations.uspto import (
    COUNT_PAGE_LIMIT,
    USPTOClient,
    USPTORateLimitException,
    USPTORejectedException,
    USPTOTimeoutException,
    USPTOUnavailableException,
)

_KEY = "uspto-test-key-7f3a9c"
_BODY_413 = (
    '{"code":413,"message":"Response payload exceeds allowed limit of 6MB. '
    'Please reduce page size."}'
)


def _client(monkeypatch, handler: Callable[[httpx.Request], httpx.Response],
            *, key: Optional[str] = _KEY) -> USPTOClient:
    monkeypatch.setattr(USPTOClient, "_api_key", lambda self: key)
    client = USPTOClient()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


class _Recorder:
    """A MockTransport handler that records every request and answers from a script."""

    def __init__(self, *responses: Callable[[httpx.Request], httpx.Response]) -> None:
        self._responses = list(responses)
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self._responses[min(len(self.requests), len(self._responses)) - 1]
        return answer(request)

    def bodies(self) -> List[Dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


def _status(code: int, text: str = "") -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(code, text=text)


def _count(n: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(
        200, json={"count": n, "patentFileWrapperDataBag": [{}], "requestIdentifier": "r"},
    )


# ── The integration ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_count_query_asks_for_one_record(monkeypatch):
    rec = _Recorder(_count(14507))
    client = _client(monkeypatch, rec)
    try:
        out = await client.count_patents_for_assignee("Google", since_year=2021)
    finally:
        await client.close()

    assert COUNT_PAGE_LIMIT == 1
    assert out == {"total_hits": 14507, "since_year": 2021}
    [body] = rec.bodies()
    assert body["pagination"] == {"offset": 0, "limit": 1}
    assert 'assigneeNameText:"Google"' in body["q"]
    assert "filingDate:[2021-01-01 TO 2099-12-31]" in body["q"]
    [request] = rec.requests
    assert request.headers["X-API-KEY"] == _KEY
    assert _KEY not in str(request.url)


@pytest.mark.asyncio
async def test_no_matches_is_a_count_of_zero(monkeypatch):
    client = _client(monkeypatch, _Recorder(_status(404, '{"count":0}')))
    try:
        out = await client.count_patents_for_assignee("Nobody Holdings")
    finally:
        await client.close()
    assert out == {"total_hits": 0, "since_year": None}


@pytest.mark.asyncio
async def test_a_zero_count_on_200_is_zero(monkeypatch):
    client = _client(monkeypatch, _Recorder(_count(0)))
    try:
        out = await client.count_patents_for_assignee("Tiny")
    finally:
        await client.close()
    assert out["total_hits"] == 0


@pytest.mark.parametrize("code", [400, 401, 403, 413, 422])
@pytest.mark.asyncio
async def test_a_4xx_is_a_definite_refusal(monkeypatch, code):
    client = _client(monkeypatch, _Recorder(_status(code, _BODY_413)))
    try:
        with pytest.raises(USPTORejectedException) as info:
            await client.count_patents_for_assignee("Archer Aviation")
    finally:
        await client.close()
    assert info.value.status_code == code
    assert f"HTTP {code}" in str(info.value)
    assert "Archer Aviation" in str(info.value)
    assert _KEY not in str(info.value)


@pytest.mark.asyncio
async def test_the_413_message_names_the_reason_on_one_capped_line(monkeypatch):
    long_body = _BODY_413 + "\n" + ("x" * 5000)
    client = _client(monkeypatch, _Recorder(_status(413, long_body)))
    try:
        with pytest.raises(USPTORejectedException) as info:
            await client.count_patents_for_assignee("Tesla")
    finally:
        await client.close()
    message = str(info.value)
    assert "exceeds allowed limit of 6MB" in message
    assert "\n" not in message
    assert len(message) < 300


@pytest.mark.parametrize("code", [408, 500, 502, 503, 504])
@pytest.mark.asyncio
async def test_a_5xx_or_408_is_transient(monkeypatch, code):
    client = _client(monkeypatch, _Recorder(_status(code, "upstream down")))
    try:
        with pytest.raises(USPTOUnavailableException) as info:
            await client.count_patents_for_assignee("NIKE")
    finally:
        await client.close()
    assert info.value.status_code == code
    assert not isinstance(info.value, USPTORejectedException)


@pytest.mark.asyncio
async def test_429_stays_the_rate_limit_exception(monkeypatch):
    client = _client(monkeypatch, _Recorder(_status(429)))
    try:
        with pytest.raises(USPTORateLimitException):
            await client.count_patents_for_assignee("NIKE")
    finally:
        await client.close()


@pytest.mark.parametrize(
    "answer",
    [
        lambda r: httpx.Response(200, text="<html>not json</html>"),
        lambda r: httpx.Response(200, json=[{"count": 5}]),
        lambda r: httpx.Response(200, json={"patentFileWrapperDataBag": []}),
        _count(None),
        _count("12"),
        _count(12.5),
        _count(True),
        _count(-1),
    ],
    ids=["not-json", "list", "no-count", "null", "string", "float", "bool", "negative"],
)
@pytest.mark.asyncio
async def test_a_200_without_a_usable_count_is_unknown_never_zero(monkeypatch, answer):
    client = _client(monkeypatch, _Recorder(answer))
    try:
        with pytest.raises(USPTOUnavailableException) as info:
            await client.count_patents_for_assignee("Lucid")
    finally:
        await client.close()
    assert info.value.status_code == 200


@pytest.mark.asyncio
async def test_network_errors_are_typed(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    def stall(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    client = _client(monkeypatch, refuse)
    try:
        with pytest.raises(USPTOUnavailableException) as info:
            await client.count_patents_for_assignee("Apple")
    finally:
        await client.close()
    assert info.value.status_code is None
    assert _KEY not in str(info.value)

    client = _client(monkeypatch, stall)
    try:
        with pytest.raises(USPTOTimeoutException):
            await client.count_patents_for_assignee("Apple")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_no_key_or_no_assignee_makes_no_call_and_no_count(monkeypatch):
    rec = _Recorder(_count(5))
    client = _client(monkeypatch, rec, key=None)
    try:
        no_key = await client.count_patents_for_assignee("Apple")
        no_name = await client.count_patents_for_assignee("")
    finally:
        await client.close()
    assert rec.requests == []
    assert no_key["error"] == "no_api_key" and no_key["total_hits"] is None
    assert no_name["error"] == "empty_assignee" and no_name["total_hits"] is None


# ── The service ─────────────────────────────────────────────────────────


class _Result:
    def __init__(self, data: List[Dict[str, Any]]) -> None:
        self.data = data


class _Query:
    def __init__(self, db: "_FakeSupabase", table: str) -> None:
        self._db, self._table, self._ticker = db, table, None
        self._op: Optional[str] = None
        self._row: Optional[Dict[str, Any]] = None

    def select(self, *_):
        self._op = "select"
        return self

    def eq(self, column, value):
        assert column == "ticker"
        self._ticker = value
        return self

    def limit(self, _):
        return self

    def upsert(self, row):
        self._op, self._row = "upsert", row
        return self

    def insert(self, row):
        self._op, self._row = "insert", row
        return self

    def execute(self):
        if self._op == "select":
            return _Result([r for r in self._db.cache if r["ticker"] == self._ticker])
        if self._op == "upsert":
            assert self._table == "ip_intel_cache"
            self._db.cache = [r for r in self._db.cache if r["ticker"] != self._row["ticker"]]
            self._db.cache.append(self._row)
        else:
            assert self._table == "ip_intel_audit"
            self._db.audit.append(self._row)
        return _Result([self._row])


class _FakeSupabase:
    """`ip_intel_cache` (one row per ticker) and `ip_intel_audit` (append-only)."""

    def __init__(self) -> None:
        self.cache: List[Dict[str, Any]] = []
        self.audit: List[Dict[str, Any]] = []

    def table(self, name):
        return _Query(self, name)


class _FakeFDA:
    def __init__(self, answer: Any) -> None:
        self._answer = answer
        self.calls = 0

    async def get_drug_approvals(self, sponsor):
        self.calls += 1
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


class _Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


_ACHR = {"companyName": "Archer Aviation Inc.", "sector": "Industrials",
         "industry": "Aerospace & Defense", "fullTimeEmployees": 800}
_PHARMA = {"companyName": "Moderna, Inc.", "sector": "Healthcare",
           "industry": "Biotechnology", "fullTimeEmployees": 5000}


@pytest.fixture
def env(monkeypatch, caplog):
    """A fresh service over a fake Supabase and a controllable clock."""
    db = _FakeSupabase()
    clock = _Clock()
    monkeypatch.setattr(ips, "get_supabase", lambda: db)
    monkeypatch.setattr(ips, "_mem_cache", {})
    monkeypatch.setattr(ips, "_inflight", {})
    monkeypatch.setattr(ips, "_uspto_refused_until", {})
    monkeypatch.setattr(ips, "time", SimpleNamespace(time=clock))  # this module only
    caplog.set_level(logging.INFO, logger=ips.logger.name)

    def make(monkeypatch_, handler, fda: Any = None, key: Optional[str] = _KEY) -> ips.IPIntelService:
        svc = ips.IPIntelService()
        svc._uspto = _client(monkeypatch_, handler, key=key)
        svc._openfda = _FakeFDA(
            fda if fda is not None else {"total_hits": 0, "approvals": [], "active_count": 0}
        )
        return svc

    return db, clock, make


def _warnings(caplog) -> List[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == ips.logger.name and r.levelno == logging.WARNING]


@pytest.mark.asyncio
async def test_a_413_is_logged_not_cached_and_not_reasked_for_a_day(monkeypatch, caplog, env):
    db, clock, make = env
    rec = _Recorder(_status(413, _BODY_413), _count(228))
    svc = make(monkeypatch, rec)
    try:
        assert await svc.get_ip_intel("ACHR", _ACHR) is None

        # Logged once at WARNING with the ticker and the status — never the key.
        [warning] = _warnings(caplog)
        assert "ACHR" in warning and "HTTP 413" in warning
        assert "exceeds allowed limit of 6MB" in warning
        assert _KEY not in caplog.text

        # Not cached as data: no cache row, and the audit row says unknown, not 0 patents.
        assert db.cache == []
        [audit] = db.audit
        assert audit["status"] == "rejected_no_data"
        assert audit["uspto_recent_5y"] is None and audit["uspto_total"] is None
        assert audit["error_detail"].startswith("http_413")

        # Inside the day: no request, no new audit row (the daily pre-warm 23 h later included).
        clock.now += 23 * 3600
        assert await svc.get_ip_intel("ACHR", _ACHR) is None
        assert len(rec.requests) == 1
        assert len(db.audit) == 1

        # A day later it is asked again (and this time answers).
        clock.now += 3600 + 60
        payload = await svc.get_ip_intel("ACHR", _ACHR)
    finally:
        await svc._uspto.close()
    assert len(rec.requests) == 2
    assert payload["patents_recent_5y"] == 228
    assert payload["patents_per_employee"] == round(228 / 800, 4)
    assert [r["ticker"] for r in db.cache] == ["ACHR"]
    assert ips._uspto_refused_until == {}


@pytest.mark.parametrize("code", [500, 503])
@pytest.mark.asyncio
async def test_a_5xx_is_logged_not_cached_and_asked_again(monkeypatch, caplog, env, code):
    db, clock, make = env
    rec = _Recorder(_status(code, "Service Unavailable"))
    svc = make(monkeypatch, rec)
    try:
        assert await svc.get_ip_intel("TSLA", {"companyName": "Tesla, Inc."}) is None
        [warning] = _warnings(caplog)
        assert "TSLA" in warning and f"HTTP {code}" in warning
        assert _KEY not in caplog.text
        assert db.cache == []
        assert db.audit[0]["uspto_recent_5y"] is None
        assert ips._uspto_refused_until == {}

        # Transient: the next collection asks again.
        assert await svc.get_ip_intel("TSLA", {"companyName": "Tesla, Inc."}) is None
    finally:
        await svc._uspto.close()
    assert len(rec.requests) == 2


@pytest.mark.asyncio
async def test_rate_limit_and_unexpected_errors_are_logged_with_the_ticker(monkeypatch, caplog, env):
    db, clock, make = env
    svc = make(monkeypatch, _Recorder(_status(429)))
    try:
        assert await svc.get_ip_intel("NKE", {"companyName": "NIKE, Inc."}) is None
    finally:
        await svc._uspto.close()
    assert any("NKE" in w and "429" in w for w in _warnings(caplog))
    assert ips._uspto_refused_until == {}

    class _Broken:
        async def count_patents_for_assignee(self, *a, **k):
            raise RuntimeError("boom")

    caplog.clear()
    svc = make(monkeypatch, _Recorder(_count(1)))
    await svc._uspto.close()
    svc._uspto = _Broken()
    assert await svc.get_ip_intel("LCID", {"companyName": "Lucid Group, Inc."}) is None
    [record] = [r for r in caplog.records
                if r.name == ips.logger.name and r.levelno == logging.WARNING]
    assert "LCID" in record.getMessage() and "RuntimeError" in record.getMessage()
    assert record.exc_info is not None
    assert db.audit[-1]["uspto_recent_5y"] is None


@pytest.mark.asyncio
async def test_force_refresh_asks_through_a_remembered_refusal(monkeypatch, caplog, env):
    db, clock, make = env
    rec = _Recorder(_status(413, _BODY_413), _count(40))
    svc = make(monkeypatch, rec)
    try:
        assert await svc.get_ip_intel("ACHR", _ACHR) is None
        assert "ACHR" in ips._uspto_refused_until
        payload = await svc.get_ip_intel("ACHR", _ACHR, force_refresh=True)
    finally:
        await svc._uspto.close()
    assert len(rec.requests) == 2
    assert payload["patents_recent_5y"] == 40
    # An answer clears the memory, so a later failed cache write cannot leave it blocking.
    assert ips._uspto_refused_until == {}


@pytest.mark.asyncio
async def test_a_zero_count_is_not_a_refusal(monkeypatch, caplog, env):
    db, clock, make = env
    rec = _Recorder(_status(404))
    svc = make(monkeypatch, rec)
    try:
        assert await svc.get_ip_intel("ACHR", _ACHR) is None
        assert await svc.get_ip_intel("ACHR", _ACHR) is None
    finally:
        await svc._uspto.close()
    assert _warnings(caplog) == []
    assert ips._uspto_refused_until == {}
    assert len(rec.requests) == 2
    assert db.audit[0]["uspto_recent_5y"] == 0
    assert db.audit[0]["error_detail"] == "both_sources_empty"


@pytest.mark.asyncio
async def test_no_api_key_records_patents_as_unknown(monkeypatch, caplog, env):
    db, clock, make = env
    rec = _Recorder(_count(5))
    svc = make(monkeypatch, rec, key=None)
    try:
        assert await svc.get_ip_intel("ACHR", _ACHR) is None
    finally:
        await svc._uspto.close()
    assert rec.requests == []
    [audit] = db.audit
    assert audit["uspto_recent_5y"] is None and audit["error_detail"] == "no_api_key"
    assert ips._uspto_refused_until == {}


def _ttl_seconds(row: Dict[str, Any]) -> float:
    computed = datetime.fromisoformat(row["computed_at"])
    expires = datetime.fromisoformat(row["expires_at"])
    return (expires - computed).total_seconds()


@pytest.mark.asyncio
async def test_a_partial_payload_is_cached_for_a_day_with_patents_unknown(monkeypatch, caplog, env):
    """Pharma: USPTO refused, FDA answered. The FDA data is cached — for a day, not 180 —
    and patents are None (unknown), not the 0 the old code stored for half a year."""
    db, clock, make = env
    fda = {"total_hits": 30, "approvals": [], "active_count": 12}
    svc = make(monkeypatch, _Recorder(_status(413, _BODY_413)), fda=fda)
    try:
        payload = await svc.get_ip_intel("MRNA", _PHARMA)
    finally:
        await svc._uspto.close()
    assert payload["fda_active_approvals"] == 12
    assert payload["patents_recent_5y"] is None and payload["patents_total"] is None
    assert payload["patents_per_employee"] is None
    assert payload["source_labels"] == ["OpenFDA"]
    [row] = db.cache
    assert _ttl_seconds(row) == pytest.approx(ips._RETRY_AFTER_SECONDS, abs=5)
    assert db.audit[0]["status"] == "applied_partial"
    # Cached instead of remembered: the cache row answers until it expires.
    assert ips._uspto_refused_until == {}


@pytest.mark.asyncio
async def test_a_complete_payload_keeps_the_half_year_ttl(monkeypatch, caplog, env):
    db, clock, make = env
    svc = make(monkeypatch, _Recorder(_count(11837)))
    try:
        payload = await svc.get_ip_intel(
            "AAPL", {"companyName": "Apple Inc.", "sector": "Technology",
                     "fullTimeEmployees": 166000},
        )
    finally:
        await svc._uspto.close()
    assert payload["patents_recent_5y"] == 11837
    [row] = db.cache
    assert _ttl_seconds(row) == pytest.approx(ips._CACHE_TTL_DAYS * 86400, abs=5)


@pytest.mark.asyncio
async def test_a_refusal_beside_a_failed_fda_call_is_not_held_for_a_day(monkeypatch, caplog, env):
    db, clock, make = env
    rec = _Recorder(_status(413, _BODY_413))
    svc = make(monkeypatch, rec, fda=OpenFDAException("FDA 503"))
    try:
        assert await svc.get_ip_intel("MRNA", _PHARMA) is None
        assert await svc.get_ip_intel("MRNA", _PHARMA) is None
    finally:
        await svc._uspto.close()
    assert db.cache == []
    assert ips._uspto_refused_until == {}
    assert len(rec.requests) == 2
    assert svc._openfda.calls == 2


@pytest.mark.asyncio
async def test_an_fda_error_dict_is_unknown_not_zero(monkeypatch, caplog, env):
    db, clock, make = env
    fda = {"total_hits": 0, "approvals": [], "active_count": 0, "error": "http_500: x"}
    svc = make(monkeypatch, _Recorder(_count(300)), fda=fda)
    try:
        payload = await svc.get_ip_intel("MRNA", _PHARMA)
    finally:
        await svc._uspto.close()
    assert payload["fda_active_approvals"] is None and payload["fda_total_approvals"] is None
    assert payload["patents_recent_5y"] == 300
    [row] = db.cache
    assert _ttl_seconds(row) == pytest.approx(ips._RETRY_AFTER_SECONDS, abs=5)


def test_the_service_calls_the_count_method_that_exists():
    assert hasattr(uspto.USPTOClient, "count_patents_for_assignee")
    assert not hasattr(uspto.USPTOClient, "get_patents_for_assignee")
