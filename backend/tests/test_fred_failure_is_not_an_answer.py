"""A FRED transient must not become "this series has no observations" for six hours.

WTI (`DCOILWTICO`) and Henry Hub (`DHHNGSP`) are FRED-sourced end to end since Phase 4.
`get_observations` memoised its own failure into the SUCCESS cache (`_CACHE_TTL_SECONDS`
= 6 h), so one `httpx.ReadTimeout` made `_fred_quote` return `{}` → `get_commodity_core`
raise `FMPUnavailableException` → a 503 telling the user to retry — and every retry for
the next six hours short-circuited on the memo without ever touching FRED.

`commodity_service` deliberately refuses to cache its OWN empty results for exactly this
reason; the memo one layer down defeated that (found 2026-09-12).
"""

from __future__ import annotations

import httpx
import pytest

import app.integrations.fred as fred


@pytest.fixture(autouse=True)
def _isolate():
    fred._CACHE.clear()
    fred._FAILED_AT.clear()
    yield
    fred._CACHE.clear()
    fred._FAILED_AT.clear()


def _client(monkeypatch, responder):
    class _Resp:
        def __init__(self, payload, status=200):
            self._payload, self.status_code = payload, status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("boom", request=None, response=None)

        def json(self):
            return self._payload

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp(*responder())

    monkeypatch.setattr(fred.httpx, "AsyncClient", lambda **k: _C())


def _svc():
    svc = fred.FREDClient.__new__(fred.FREDClient)
    svc.api_key, svc.base_url, svc._timeout = "k", "https://fred", 5
    return svc


@pytest.mark.asyncio
async def test_a_transient_failure_is_retried_within_minutes_not_hours(monkeypatch):
    calls = []

    def _responder():
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("")
        return ({"observations": [{"date": "2026-09-10", "value": "62.5"}]},)

    _client(monkeypatch, _responder)
    svc = _svc()
    assert await svc.get_observations("DCOILWTICO", limit=4000) == []
    assert len(calls) == 1

    # Inside the window: served from the failure memo, no second request (herd guard).
    assert await svc.get_observations("DCOILWTICO", limit=4000) == []
    assert len(calls) == 1

    # Past the SHORT failure window — and far inside the 6 h success TTL, which is the
    # whole point: the old code was still serving the empty list here.
    key = ("DCOILWTICO", "obs:4000")
    fred._FAILED_AT[key] = fred._FAILED_AT[key] - fred._FAILURE_TTL_SECONDS - 1
    out = await svc.get_observations("DCOILWTICO", limit=4000)
    assert [o.value for o in out] == [62.5], "the transient was still pinned as an answer"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_the_failure_window_is_far_shorter_than_the_success_ttl():
    assert fred._FAILURE_TTL_SECONDS < fred._CACHE_TTL_SECONDS / 10


def test_a_success_retires_the_failure_memo():
    """Asserted on `_cache_set` itself, not through an aged memo.

    Ageing the entry first makes `_failed_recently` delete it on read, so the assertion
    would pass with or without the pop — a vacuous guard. The invariant is: writing a
    value for a key clears that key's failure mark, so the NEXT caller never sees a stale
    failure alongside a good answer.
    """
    key = ("DHHNGSP", "obs:10")
    fred._mark_failed(key)
    assert fred._failed_recently(key), "the memo did not take — test would be vacuous"
    fred._cache_set(key, ["a good answer"])
    assert key not in fred._FAILED_AT, "a good answer must retire the failure memo"
    assert fred._failed_recently(key) is False


@pytest.mark.asyncio
async def test_a_genuinely_empty_series_is_still_cached(monkeypatch):
    """Control: 'looked, found nothing' is a real answer and keeps the 6 h TTL."""
    calls = []

    def _responder():
        calls.append(1)
        return ({"observations": []},)

    _client(monkeypatch, _responder)
    svc = _svc()
    assert await svc.get_observations("EMPTY", limit=5) == []
    assert await svc.get_observations("EMPTY", limit=5) == []
    assert len(calls) == 1, "a real empty answer must not be re-fetched"
    assert ("EMPTY", "obs:5") not in fred._FAILED_AT


@pytest.mark.asyncio
async def test_the_snapshot_does_not_pin_none_after_a_failed_observations_read(monkeypatch):
    calls = []

    def _responder():
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("")
        return ({"observations": [{"date": "2026-09-10", "value": "62.5"}]},)

    _client(monkeypatch, _responder)
    svc = _svc()
    assert await svc.get_snapshot("DCOILWTICO") is None
    assert ("DCOILWTICO", "snapshot") not in fred._CACHE, (
        "caching None here re-creates the 6-hour outage one level up"
    )
    fred._FAILED_AT[("DCOILWTICO", "obs:14")] -= fred._FAILURE_TTL_SECONDS + 1
    snap = await svc.get_snapshot("DCOILWTICO")
    assert snap is not None and snap.latest == 62.5
