"""Census AIES client: the per-vintage `aiesbasic` dataset, and a loud failure
when no year answers.

The old time-series dataset (`/data/timeseries/aies/basic`, `NAICS=`) began
answering 404 for every code and year. A 404 is also what an unpublished vintage
returns, so `_http_get_rows` folded it into "no data", and the Census tier died
silently for every mapped industry (found 2026-10-01 while tracing PLUG's
missing TAM). These tests pin the replacement endpoint and the warning.

Hermetic: `httpx.AsyncClient` is faked inside the census module.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
import pytest

import app.integrations.census as census
import app.services.industry_tam_service as its

YEAR = datetime.now(timezone.utc).year
LOGGER = "app.integrations.census"


class _Resp:
    """Builds a REAL `httpx.Response` per request, so `raise_for_status` raises the
    genuine `httpx.HTTPStatusError` the client must classify."""

    def __init__(self, status: int, payload: Any = None, raw: Optional[bytes] = None,
                 exc: Optional[BaseException] = None) -> None:
        self.status, self.payload, self.raw, self.exc = status, payload, raw, exc

    def build(self, url: str, params: Dict[str, Any]):
        if self.exc is not None:
            raise self.exc
        req = httpx.Request("GET", url, params=params)
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw, request=req)
        if self.payload is None:
            return httpx.Response(self.status, request=req)
        return httpx.Response(self.status, json=self.payload, request=req)


class _FakeHTTP:
    """Routes (url, NAICS2017) → response. Records every request."""

    def __init__(self, routes: Dict[str, _Resp]) -> None:
        self.routes = routes
        self.requests: List[tuple] = []

    def factory(self, *a, **k):
        fake = self

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, params=None):
                fake.requests.append((url, dict(params or {})))
                return fake.routes.get(url, _Resp(404)).build(url, dict(params or {}))

        return _Client()


@pytest.fixture(autouse=True)
def _clear_census_cache():
    census._cache.clear()
    yield
    census._cache.clear()


def _aies(value: Optional[str], naics: str = "335") -> _Resp:
    return _Resp(200, [["RCPT_TOT_VAL", "NAICS2017", "us"], [value, naics, "1"]])


def _ecn(value: str = "122950000", label: str = "Electrical equipment, appliance, and component manufacturing") -> _Resp:
    return _Resp(200, [["RCPTOT", "NAICS2017_LABEL", "NAICS2017", "us"], [value, label, "335", "1"]])


def _client(monkeypatch, routes: Dict[str, _Resp]) -> tuple:
    http = _FakeHTTP(routes)
    monkeypatch.setattr(census.httpx, "AsyncClient", http.factory)
    return census.CensusClient(api_key="test-key"), http


BASE = "https://api.census.gov/data"


@pytest.mark.asyncio
async def test_uses_the_per_vintage_aiesbasic_dataset_with_naics2017(monkeypatch):
    client, http = _client(monkeypatch, {
        f"{BASE}/{YEAR - 1}/aiesbasic": _aies("195000604"),
        f"{BASE}/2017/ecnbasic": _ecn(),
    })
    snap = await client.get_industry_revenue_snapshot("335")
    aies_requests = [(u, p) for u, p in http.requests if "aies" in u]
    assert aies_requests, "no AIES request was made"
    url, params = aies_requests[0]
    assert url == f"{BASE}/{YEAR - 1}/aiesbasic"
    assert params["NAICS2017"] == "335"
    assert "NAICS" not in params and "YEAR" not in params
    assert params["get"] == "RCPT_TOT_VAL"
    assert not any("timeseries" in u for u, _ in http.requests)
    # $1,000s → dollars
    assert snap.revenue_usd == 195_000_604_000.0
    assert snap.year == YEAR - 1
    assert snap.revenue_usd_baseline == 122_950_000_000.0
    assert snap.naics_label.startswith("Electrical equipment")


@pytest.mark.asyncio
async def test_unpublished_vintage_404_falls_back_to_the_year_before(monkeypatch):
    client, http = _client(monkeypatch, {
        # YEAR-1 not published → 404 (default route)
        f"{BASE}/{YEAR - 2}/aiesbasic": _aies("195000604"),
        f"{BASE}/2017/ecnbasic": _ecn(),
    })
    snap = await client.get_industry_revenue_snapshot("335")
    assert snap.year == YEAR - 2
    probed = [u for u, _ in http.requests if "aiesbasic" in u]
    assert probed[:2] == [f"{BASE}/{YEAR - 1}/aiesbasic", f"{BASE}/{YEAR - 2}/aiesbasic"]


@pytest.mark.asyncio
@pytest.mark.parametrize("suppressed", [None, "", "D", "S", "N", "X"])
async def test_suppressed_value_falls_through_to_an_earlier_year(monkeypatch, suppressed):
    client, _ = _client(monkeypatch, {
        f"{BASE}/{YEAR - 1}/aiesbasic": _aies(suppressed),
        f"{BASE}/{YEAR - 2}/aiesbasic": _aies("57310225", "3353"),
        f"{BASE}/2017/ecnbasic": _ecn(),
    })
    snap = await client.get_industry_revenue_snapshot("335")
    assert snap.year == YEAR - 2
    assert snap.revenue_usd == 57_310_225_000.0


@pytest.mark.asyncio
async def test_no_year_answers_logs_a_warning_and_returns_none(monkeypatch, caplog):
    client, http = _client(monkeypatch, {})   # every URL → 404, like the dead endpoint
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        snap = await client.get_industry_revenue_snapshot("335")
    assert snap is None
    assert len([u for u, _ in http.requests if "aiesbasic" in u]) == census._AIES_PROBE_YEARS
    assert "no revenue for NAICS 335" in caplog.text


@pytest.mark.asyncio
async def test_missing_baseline_still_returns_tam_without_cagr(monkeypatch):
    client, _ = _client(monkeypatch, {f"{BASE}/{YEAR - 1}/aiesbasic": _aies("195000604")})
    snap = await client.get_industry_revenue_snapshot("335")
    assert snap.revenue_usd == 195_000_604_000.0
    assert snap.revenue_usd_baseline is None and snap.years_apart is None


@pytest.mark.asyncio
async def test_unconfigured_client_makes_no_request(monkeypatch):
    http = _FakeHTTP({})
    monkeypatch.setattr(census.httpx, "AsyncClient", http.factory)
    assert await census.CensusClient(api_key="").get_industry_revenue_snapshot("335") is None
    assert http.requests == []


# ── FRED: a non-finite latest value is not a TAM ─────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -1.0])
async def test_fred_tam_rejects_a_non_finite_or_non_positive_latest_value(monkeypatch, bad):
    class _FRED:
        is_configured = True

        async def get_observations(self, series_id, *, limit=8):
            vals = [bad, 82300, 78500, 69600, 61200, 57700]
            return [SimpleNamespace(date=f"{2025 - i}-01-01", value=float(v)) for i, v in enumerate(vals)]

    monkeypatch.setattr(its, "get_fred_client", lambda: _FRED())
    assert await its.fred_tam_for_series("USELCEQAPMANNGSP") is None


# ── A transient failure is not an answer (review 2026-10-01) ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("resp", [
    _Resp(503),
    _Resp(429),
    _Resp(200, raw=b"<html>not json</html>"),
    _Resp(0, exc=httpx.ConnectError("connection refused")),
    _Resp(0, exc=httpx.ReadTimeout("slow")),
])
async def test_transient_failure_raises_and_is_not_cached(monkeypatch, caplog, resp):
    url = f"{BASE}/{YEAR - 1}/aiesbasic"
    client, http = _client(monkeypatch, {url: resp})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(census.CensusUnavailableException):
            await client.get_industry_revenue_snapshot("335")
    # Recovery is seen on the very next call — the blip was never memoized as a miss.
    http.routes[url] = _aies("195000604")
    http.routes[f"{BASE}/2017/ecnbasic"] = _ecn()
    snap = await client.get_industry_revenue_snapshot("335")
    assert snap.revenue_usd == 195_000_604_000.0
    # The API key is a query parameter: it must never reach a log line.
    assert "test-key" not in caplog.text


@pytest.mark.asyncio
async def test_baseline_blip_raises_instead_of_a_cagr_less_tam(monkeypatch):
    """One failed 2017 baseline call used to be cached as "no baseline" for 24 h,
    serving the TAM without a CAGR — the tester's exact "CAGR —"."""
    client, _ = _client(monkeypatch, {
        f"{BASE}/{YEAR - 1}/aiesbasic": _aies("195000604"),
        f"{BASE}/2017/ecnbasic": _Resp(502),
    })
    with pytest.raises(census.CensusUnavailableException):
        await client.get_industry_revenue_snapshot("335")


@pytest.mark.asyncio
async def test_get_industry_tam_falls_through_to_fred_on_a_census_blip(monkeypatch):
    class _Census:
        is_configured = True

        async def get_industry_revenue_snapshot(self, naics):
            raise census.CensusUnavailableException("Census HTTP 503")

    class _FRED:
        is_configured = True

        async def get_observations(self, series_id, *, limit=8):
            vals = [87100, 82300, 78500, 69600, 61200, 57700]
            return [SimpleNamespace(date=f"{2025 - i}-01-01", value=float(v)) for i, v in enumerate(vals)]

    monkeypatch.setattr(its, "get_census_client", lambda: _Census())
    monkeypatch.setattr(its, "get_fred_client", lambda: _FRED())
    tam = await its.get_industry_tam("Electrical Equipment & Parts")
    assert tam.current_tam == 87.1
