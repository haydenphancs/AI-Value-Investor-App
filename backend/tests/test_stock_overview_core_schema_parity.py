"""
Schema-parity + fast-path guard for GET /stocks/{ticker}/overview/core.

The core endpoint exists so the stock detail screen paints price + chart in
~0.5s (reusing only the live quote + intraday chart + profile name), while the
full /overview (fired in parallel by the client) supersedes it. Two invariants:

1. Schema parity (backend ↔ iOS): `StockOverviewCoreResponse.model_dump(mode="json")`
   must serialize exactly the keys the iOS `StockOverviewCoreResponseDTO`
   (StockOverviewResponseModels.swift) decodes — a drift = a JSONDecoder crash on
   the stock detail screen. Field names mirror `StockOverviewResponse` so iOS
   reuses its chart-point decode (`StockOverviewPricePointDTO`, `close` required).

2. Fast-path: `get_overview_core` must NEVER call a slow upstream
   (`get_historical_prices` / the `_get_fundamentals` bundle / snapshots / IPO
   fetch). On a daily range it makes exactly two FMP calls — the quote and the
   company profile — and ships an empty chart (the full /overview supplies the
   daily chart). A regression that reintroduces the historical fetch here would
   silently restore the multi-second first paint the endpoint exists to avoid.

No network — the FMP client is stubbed; any forbidden method call fails loudly.
"""

import pytest

from app.schemas.etf import MarketStatusResponse
from app.schemas.stock_overview import StockOverviewCoreResponse
from app.services.stock_overview_service import (
    StockOverviewService,
    _cache,
    _get_market_status,
)


# The exact snake_case keys the iOS StockOverviewCoreResponseDTO.CodingKeys decode.
_CORE_KEYS = {
    "symbol",
    "company_name",
    "current_price",
    "price_change",
    "price_change_percent",
    "market_status",
    "chart_data",
}


# ── 1. Schema parity ──────────────────────────────────────────────────


def test_core_response_keys_match_ios_dto():
    resp = StockOverviewCoreResponse(
        symbol="AAPL",
        company_name="Apple Inc.",
        current_price=233.45,
        price_change=2.10,
        price_change_percent=0.91,
        market_status=_get_market_status(),
        chart_data=[
            {"date": "2026-01-02 10:00:00", "open": 1.0, "high": 2.0,
             "low": 0.5, "close": 1.5, "volume": 100},
        ],
    )
    dumped = resp.model_dump(mode="json")
    assert set(dumped.keys()) == _CORE_KEYS
    # The chart point carries `close` (the iOS StockOverviewPricePointDTO requires
    # close; date/open/high/low/volume are optional there).
    assert "close" in dumped["chart_data"][0]
    # market_status serializes as a nested object (iOS MarketStatusDTO), not a scalar.
    assert isinstance(dumped["market_status"], dict)


def test_core_response_is_market_status_response():
    # The core response reuses the SAME MarketStatusResponse type the full
    # /overview uses, so the iOS MarketStatusDTO decodes both identically.
    resp = StockOverviewCoreResponse(
        symbol="AAPL", company_name="Apple Inc.", current_price=1.0,
        price_change=0.0, price_change_percent=0.0,
        market_status=_get_market_status(), chart_data=[],
    )
    assert isinstance(resp.market_status, MarketStatusResponse)


# ── 2. Fast-path guard (no slow upstream calls) ───────────────────────


class _FakeFMP:
    """Stub FMP client: serves quote + profile, forbids every other method.

    A forbidden call (historical prices, fundamentals, snapshots, …) raises, so a
    regression that reintroduces a slow fetch into the core path fails this test.
    """

    def __init__(self):
        self.calls: list[str] = []

    async def get_stock_price_quote(self, ticker: str):
        self.calls.append("get_stock_price_quote")
        return {
            "price": 194.83,
            "change": -2.74,
            "changePercentage": -1.39,
            "name": "NVIDIA Corporation",
        }

    async def get_company_profile(self, ticker: str):
        self.calls.append("get_company_profile")
        return {"companyName": "NVIDIA Corporation", "price": 194.83}

    def __getattr__(self, name: str):
        # Any other FMP method must NOT be reached by the core path. Record the
        # name BEFORE raising: get_overview_core swallows a _get_volatile exception
        # (return_exceptions=True), so a forbidden call would otherwise be invisible
        # to the test — recording it lets the assertions catch a slow-path regression.
        async def _forbidden(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"get_overview_core must not call fmp.{name}()")
        return _forbidden


@pytest.mark.asyncio
async def test_get_overview_core_only_calls_quote_and_profile_on_daily():
    _cache.clear()  # avoid a cached response from a prior test short-circuiting the calls
    svc = StockOverviewService()
    fake = _FakeFMP()
    svc.fmp = fake  # type: ignore[assignment]

    # Daily range → _get_volatile fetches NO chart (would come from the slow
    # bundle in the full path); core ships an empty chart. Exactly 2 FMP calls.
    resp = await svc.get_overview_core("NVDA", chart_range="3M")

    assert sorted(fake.calls) == ["get_company_profile", "get_stock_price_quote"]
    dumped = resp.model_dump(mode="json")
    assert set(dumped.keys()) == _CORE_KEYS
    assert dumped["symbol"] == "NVDA"
    assert dumped["company_name"] == "NVIDIA Corporation"
    assert dumped["current_price"] == 194.83
    assert dumped["price_change"] == -2.74
    assert dumped["price_change_percent"] == -1.39
    assert dumped["chart_data"] == []   # daily range → empty core chart, filled by /overview


@pytest.mark.asyncio
async def test_get_overview_core_degrades_on_partial_failure():
    _cache.clear()
    svc = StockOverviewService()

    class _ProfileOnlyFMP(_FakeFMP):
        async def get_stock_price_quote(self, ticker: str):
            self.calls.append("get_stock_price_quote")
            raise RuntimeError("quote upstream down")

    svc.fmp = _ProfileOnlyFMP()  # type: ignore[assignment]
    # Quote fails, profile succeeds → still returns a valid response (price falls
    # back to the profile), never raises.
    resp = await svc.get_overview_core("NVDA", chart_range="3M")
    assert resp.company_name == "NVIDIA Corporation"
    assert resp.current_price == 194.83   # from the profile fallback
    assert resp.chart_data == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rng", ["ALL", "5Y"])
async def test_get_overview_core_no_slow_historical_on_all_or_5y(rng):
    """Regression (adversarial review): core must NOT trigger the slow historical
    fetch on ANY range. ALL (→monthly) and 5Y (→weekly) resolve to AGGREGATED
    intervals whose chart is built from the multi-second historical bundle in the
    full path (ALL = up to 5 sequential paginated pulls). The core defers those
    charts (fast_only) and ships chart_data=[]. Without the fix, `_get_volatile`
    would call `get_historical_prices` — recorded by `_FakeFMP` and caught here."""
    _cache.clear()
    svc = StockOverviewService()
    fake = _FakeFMP()
    svc.fmp = fake  # type: ignore[assignment]

    resp = await svc.get_overview_core("NVDA", chart_range=rng)

    assert sorted(fake.calls) == ["get_company_profile", "get_stock_price_quote"], (
        f"core made a slow/forbidden upstream call on range={rng}: {fake.calls}"
    )
    assert resp.chart_data == []          # aggregated-range chart deferred to the full /overview
    assert resp.symbol == "NVDA"


@pytest.mark.asyncio
async def test_get_overview_core_non_finite_price_becomes_finite_default():
    """Regression (adversarial review): a NaN/Inf quote price must NOT reach the
    required Double — it would serialize as a JSON `NaN`/`Infinity` token and crash
    the iOS JSONDecoder on the whole response. _safe_float rejects non-finite."""
    import math as _m
    _cache.clear()

    class _NaNFMP(_FakeFMP):
        async def get_stock_price_quote(self, ticker: str):
            self.calls.append("get_stock_price_quote")
            return {"price": float("nan"), "change": float("inf"),
                    "changePercentage": float("nan"), "name": "NVIDIA"}

        async def get_company_profile(self, ticker: str):
            self.calls.append("get_company_profile")
            return {"companyName": "NVIDIA Corporation"}  # no numeric fallback either

    svc = StockOverviewService()
    svc.fmp = _NaNFMP()  # type: ignore[assignment]
    resp = await svc.get_overview_core("NVDA", chart_range="3M")
    assert _m.isfinite(resp.current_price) and resp.current_price == 0.0
    assert _m.isfinite(resp.price_change) and resp.price_change == 0.0
    assert _m.isfinite(resp.price_change_percent)
    # Serializes cleanly (no NaN/Infinity tokens) — the whole point.
    import json
    json.dumps(resp.model_dump(), allow_nan=False)


@pytest.mark.asyncio
async def test_get_overview_core_fetches_intraday_chart_on_1d():
    """The common cold-open path (1D → 5min, intraday) DOES carry a real chart in
    core — that's the whole point. Uses a fake whose intraday fetch returns bars."""
    _cache.clear()

    class _IntradayFMP(_FakeFMP):
        async def get_intraday_prices(self, ticker, interval="5min", from_date=None, to_date=None):
            self.calls.append("get_intraday_prices")
            return [
                {"date": "2026-01-02 09:30:00", "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 10},
                {"date": "2026-01-02 09:35:00", "open": 1.0, "high": 1.2, "low": 1.0, "close": 1.1, "volume": 12},
            ]

    svc = StockOverviewService()
    svc.fmp = _IntradayFMP()  # type: ignore[assignment]
    resp = await svc.get_overview_core("NVDA", chart_range="1D")
    # Intraday fetch happened (fast) and never a historical/fundamentals call.
    assert "get_intraday_prices" in svc.fmp.calls
    assert "get_historical_prices" not in svc.fmp.calls
    assert len(resp.chart_data) >= 1
