"""`GET /{etfs|indices|commodities}/{symbol}/chart` — one range's bars alone (2026-10-21).

Why they exist: `/core` carries no 3M+ bars (`fast_only`) and `/quote` is a projection of
the FULL build, so a detail screen opening on a remembered 3M+ — or a pill tapped during
the cold first load — waited 2-5 s for the whole detail to draw a chart these answer in
~0.1-0.7 s.

Pinned here, per router:
  * the rows are EXACTLY what the detail's chart builder (`_get_chart`) returned, under
    the same symbol mapping the detail uses (index: the normalised `^` symbol, which
    `_get_chart` maps to its proxy; commodity: `_resolve_fmp_symbol`), dumped to the
    `{symbol, prices}` shape iOS `StockChartResponse` decodes;
  * the account gate (router-level), range / interval / symbol validation (422), and a
    failure answered as a typed `FMP_UNAVAILABLE` — never an untyped 500 and never the
    report pipeline's fallback code;
  * the detail itself still calls `_get_chart` with the same arguments, so the two
    surfaces cannot drift apart.

Hermetic: the service getters the routes import are replaced with fresh instances whose
`_get_chart` is a recording stub; nothing reaches FMP.
"""

from __future__ import annotations

import inspect
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import commodities as commodities_ep
from app.api.v1.endpoints import etfs as etfs_ep
from app.api.v1.endpoints import indices as indices_ep
from app.dependencies import get_current_user_id
from app.main import app
from app.schemas.index import ChartDataPointResponse
from app.services import commodity_service, etf_service, index_service

_BARS = [
    {"date": "2026-10-15", "open": 10.0, "high": 12.0, "low": 9.5, "close": 11.0, "volume": 100.0},
    {"date": "2026-10-16", "open": 11.0, "high": 13.0, "low": 10.5, "close": 12.5, "volume": None},
]
_ROUTES = {
    "etf": "/api/v1/etfs/QQQ/chart",
    "index": "/api/v1/indices/%5EGSPC/chart",
    "commodity": "/api/v1/commodities/GC/chart",
}


class _Recorder:
    def __init__(self, rows=None, exc=None):
        self.rows = _BARS if rows is None else rows
        self.exc = exc
        self.calls = []

    async def __call__(self, symbol, chart_range, interval, fast_only=False):
        self.calls.append((symbol, chart_range, interval, fast_only))
        if self.exc is not None:
            raise self.exc
        return list(self.rows)


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user_id] = lambda: f"u-{uuid.uuid4()}"
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)


@pytest.fixture
def services(monkeypatch):
    """Fresh service instances behind the getters the ROUTE modules bound at import."""
    etf = etf_service.ETFService.__new__(etf_service.ETFService)
    idx = index_service.IndexService.__new__(index_service.IndexService)
    com = commodity_service.CommodityService.__new__(commodity_service.CommodityService)
    rec = {
        "etf": _Recorder(),
        "index": _Recorder(rows=[ChartDataPointResponse(**b) for b in _BARS]),
        "commodity": _Recorder(),
    }
    etf._get_chart = rec["etf"]
    idx._get_chart = rec["index"]
    com._get_chart = rec["commodity"]
    monkeypatch.setattr(etfs_ep, "get_etf_service", lambda: etf)
    monkeypatch.setattr(indices_ep, "get_index_service", lambda: idx)
    monkeypatch.setattr(commodities_ep, "get_commodity_service", lambda: com)
    return rec


# ── the answer ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("asset", sorted(_ROUTES))
def test_the_route_answers_the_detail_builders_rows_as_symbol_and_prices(asset, client, services):
    r = client.get(_ROUTES[asset], params={"range": "6M", "interval": "daily"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"symbol", "prices"}
    assert [p["date"] for p in body["prices"]] == ["2026-10-15", "2026-10-16"]
    assert body["prices"][0] == {
        "date": "2026-10-15", "open": 10.0, "high": 12.0, "low": 9.5, "close": 11.0, "volume": 100.0,
    }
    assert body["prices"][1]["volume"] is None, "a missing volume stays null, never 0"
    # Never the first-paint shortcut: the detail's own (complete) build.
    (call,) = services[asset].calls
    assert call[1:] == ("6M", "daily", False)


def test_each_route_maps_the_symbol_exactly_as_its_detail_does(client, services):
    client.get("/api/v1/etfs/qqq/chart", params={"range": "1Y"})
    client.get("/api/v1/indices/GSPC/chart", params={"range": "1Y"})
    client.get("/api/v1/commodities/GCUSD/chart", params={"range": "1Y"})
    assert services["etf"].calls[0][0] == "QQQ"
    # The index builder takes the `^` symbol and maps it to its proxy itself.
    assert services["index"].calls[0][0] == "^GSPC"
    # The commodity builder takes the FMP symbol the detail resolves.
    assert services["commodity"].calls[0][0] == commodity_service._resolve_fmp_symbol("GC")


@pytest.mark.parametrize("asset", sorted(_ROUTES))
def test_an_empty_series_is_an_answer_not_an_error(asset, client, services):
    services[asset].rows = []
    r = client.get(_ROUTES[asset], params={"range": "1D"})
    assert r.status_code == 200, r.text
    assert r.json()["prices"] == []


# ── the gates ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("asset", sorted(_ROUTES))
def test_a_signed_out_caller_is_refused_before_any_data_is_read(asset, services):
    app.dependency_overrides.pop(get_current_user_id, None)
    r = TestClient(app).get(_ROUTES[asset], params={"range": "6M"})
    assert r.status_code == 401, r.text
    assert r.json()["error_code"] == "AUTH_REQUIRED"
    assert not services[asset].calls


@pytest.mark.parametrize("asset", sorted(_ROUTES))
@pytest.mark.parametrize("params", [{"range": "2Y"}, {"range": "10Y"}, {"range": "6M", "interval": "2hour"}])
def test_a_range_or_interval_outside_the_pattern_is_a_422(asset, params, client, services):
    r = client.get(_ROUTES[asset], params=params)
    assert r.status_code == 422, r.text
    assert not services[asset].calls


@pytest.mark.parametrize("path", [
    "/api/v1/etfs/BAD$$/chart", "/api/v1/indices/%5E%24%24/chart", "/api/v1/commodities/TOOLONGSYM/chart",
])
def test_a_malformed_symbol_is_a_typed_422(path, client, services):
    r = client.get(path, params={"range": "6M"})
    assert r.status_code == 422, r.text
    assert r.json()["error_code"] == "INVALID_INPUT"
    assert not any(rec.calls for rec in services.values())


@pytest.mark.parametrize("asset", sorted(_ROUTES))
def test_a_failed_build_is_a_typed_upstream_error(asset, client, services):
    services[asset].exc = RuntimeError("upstream exploded")
    r = client.get(_ROUTES[asset], params={"range": "6M"})
    assert r.status_code == 502, r.text
    assert r.json()["error_code"] == "FMP_UNAVAILABLE"
    assert r.json()["error_code"] != "REPORT_GENERATION_FAILED"


def test_a_withdrawn_commodity_is_refused_with_the_contract_code_never_an_empty_chart(client, services):
    """Every commodity entry point refuses a screen FMP's package no longer covers with
    FMP_NOT_ENTITLED (iOS branches on it); an empty 200 would read as "no data" and invite
    a retry."""
    withdrawn = sorted(commodity_service._WITHDRAWN_COMMODITIES)
    assert withdrawn, "guard is stale — no withdrawn commodity left to test"
    r = client.get(f"/api/v1/commodities/{withdrawn[0]}/chart", params={"range": "1Y"})
    assert r.json().get("error_code") == "FMP_NOT_ENTITLED", r.text
    assert not services["commodity"].calls


# ── the two surfaces cannot drift ───────────────────────────────────────────

@pytest.mark.parametrize("svc,builder,method,call", [
    (etf_service.ETFService, "_build_etf_detail", "get_etf_chart",
     "self._get_chart(symbol, chart_range, interval)"),
    (index_service.IndexService, "_build_index_detail", "get_index_chart",
     "self._get_chart(symbol, chart_range, interval)"),
    (commodity_service.CommodityService, "_build_commodity_detail", "get_commodity_chart",
     "self._get_chart(fmp_symbol, chart_range, interval)"),
])
def test_the_chart_route_and_the_detail_call_the_same_builder(svc, builder, method, call):
    def _code(fn):
        src = inspect.getsource(fn)
        return "\n".join(re.sub(r"#.*$", "", line) for line in src.splitlines())

    assert call in _code(getattr(svc, builder)), f"{builder} no longer builds its chart with {call}"
    assert call in _code(getattr(svc, method)), f"{method} must build with {call}, like the detail"
    assert not re.search(r"fast_only\s*=", _code(getattr(svc, method))), (
        "the chart route serves the complete build, never the first-paint shortcut"
    )
