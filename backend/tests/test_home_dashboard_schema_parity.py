"""
Home dashboard — schema-parity + transform tests.

Two guard rails:

1. Schema parity (backend ↔ iOS): the snake_case keys the iOS DTOs decode
   (`HomeDashboardResponseDTO` / `MarketPulseItemDTO` in
   `Models/HomeDashboardModels.swift`) must stay pinned to the Pydantic
   response shape. A drift here = a decode crash in the app.

2. The pure `_extract_sparkline` / `_market_status` transforms must behave
   correctly on the messy / outlier inputs FMP actually returns — never a
   wrong number, never a synthetic series.

No network, no Supabase — pure inputs constructed inline.
"""

import asyncio
from datetime import datetime

import pytest

from app.schemas.home_dashboard import (
    HomeDashboardResponse,
    MarketPulseItemResponse,
    ScannerGroupsResponse,
)
from app.services.home_dashboard_service import (
    HomeDashboardService,
    _PULSE_SYMBOLS,
    _SCANNER_CACHE_KEY,
    _downsample,
    _intraday_sparkline,
    _market_status,
)
import time as _time


# ── 1. Schema parity ──────────────────────────────────────────────────

# The exact snake_case keys the iOS `MarketPulseItemDTO.CodingKeys` expects.
_ITEM_KEYS = {"symbol", "name", "type", "price", "change_percent", "previous_close", "spark"}
# The exact snake_case keys the iOS `HomeDashboardResponseDTO.CodingKeys` expects.
_RESPONSE_KEYS = {"market_status_text", "market_is_open", "pulse", "scanners"}


def test_market_pulse_item_keys_match_ios_dto():
    item = MarketPulseItemResponse(
        symbol="^GSPC",
        name="S&P 500",
        type="index",
        price=6952.40,
        change_percent=0.62,
        spark=[1.0, 2.0, 3.0],
    )
    assert set(item.model_dump().keys()) == _ITEM_KEYS


def test_dashboard_response_keys_match_ios_dto():
    resp = HomeDashboardResponse(
        market_status_text="Markets Open",
        market_is_open=True,
        pulse=[],
    )
    dumped = resp.model_dump()
    assert set(dumped.keys()) == _RESPONSE_KEYS
    assert dumped["pulse"] == []  # empty strip is valid → iOS hides the section


def test_dashboard_response_validates_worst_case_inputs():
    """Empty spark, zero/negative prices, all market states still validate."""
    for status_text, is_open in [
        ("Markets Open", True),
        ("Markets Closed", False),
        ("Pre-Market", False),
        ("After Hours", False),
    ]:
        resp = HomeDashboardResponse.model_validate(
            {
                "market_status_text": status_text,
                "market_is_open": is_open,
                "pulse": [
                    {
                        "symbol": "BTCUSD",
                        "name": "Bitcoin",
                        "type": "crypto",
                        "price": 0.0,            # degenerate but must not crash
                        "change_percent": -1.85,
                        "spark": [],             # empty series is allowed
                    }
                ],
            }
        )
        assert resp.market_is_open is is_open
        assert resp.pulse[0].spark == []


# ── 2. Intraday sparkline transform ───────────────────────────────────


def test_intraday_sparkline_empty_and_bad_shapes_return_empty():
    assert _intraday_sparkline(None) == []
    assert _intraday_sparkline([]) == []
    assert _intraday_sparkline([{"date": "2026-01-01 10:00:00", "close": 5.0}]) == []  # 1 bar
    assert _intraday_sparkline("garbage") == []
    assert _intraday_sparkline(["not-a-dict", "also-not"]) == []


def test_intraday_sparkline_keeps_only_most_recent_day():
    # chart_helper returns oldest-first; bars span two sessions.
    bars = [
        {"date": "2026-06-25 10:00:00", "close": 90.0},   # prior day → dropped
        {"date": "2026-06-25 11:00:00", "close": 91.0},   # prior day → dropped
        {"date": "2026-06-26 10:00:00", "close": 100.0},
        {"date": "2026-06-26 11:00:00", "close": 101.0},
        {"date": "2026-06-26 12:00:00", "close": 102.0},
    ]
    assert _intraday_sparkline(bars) == [100.0, 101.0, 102.0]


def test_intraday_sparkline_skips_none_zero_negative_and_nonnumeric_closes():
    bars = [
        {"date": "2026-06-26 10:00:00", "close": 100.0},
        {"date": "2026-06-26 10:05:00", "close": None},    # skipped
        {"date": "2026-06-26 10:10:00", "close": 0.0},     # skipped (non-positive)
        {"date": "2026-06-26 10:15:00", "close": -3.0},    # skipped (negative)
        {"date": "2026-06-26 10:20:00", "close": "oops"},  # skipped (non-numeric)
        {"date": "2026-06-26 10:25:00", "close": 101.0},
    ]
    assert _intraday_sparkline(bars) == [100.0, 101.0]


def test_intraday_sparkline_downsamples_keeping_first_and_last():
    # 78 five-min bars in one session, ascending (all > 0) → downsample to 30.
    bars = [
        {"date": f"2026-06-26 {9 + i // 12:02d}:{(i % 12) * 5:02d}:00", "close": float(i + 1)}
        for i in range(78)
    ]
    out = _intraday_sparkline(bars, points=30)
    assert 2 <= len(out) <= 30
    assert out[0] == 1.0       # first survives
    assert out[-1] == 78.0     # last survives


def test_intraday_sparkline_requires_two_usable_closes():
    # Latest day has only one valid close → honest empty.
    bars = [
        {"date": "2026-06-25 10:00:00", "close": 90.0},
        {"date": "2026-06-26 10:00:00", "close": 100.0},  # only one on the last day
    ]
    assert _intraday_sparkline(bars) == []


def test_downsample_returns_input_when_within_target():
    vals = [1.0, 2.0, 3.0]
    assert _downsample(vals, 30) == vals


def test_downsample_caps_and_preserves_endpoints():
    vals = [float(i) for i in range(100)]
    out = _downsample(vals, 10)
    assert len(out) <= 10
    assert out[0] == 0.0 and out[-1] == 99.0


# ── 3. Market status ──────────────────────────────────────────────────


def test_market_status_weekend_is_closed():
    saturday = datetime(2026, 6, 27, 12, 0)  # a Saturday, midday
    assert _market_status(saturday) == ("Markets Closed", False)


def test_market_status_session_boundaries_on_a_weekday():
    # Monday 2026-06-29.
    cases = {
        (3, 0): ("Markets Closed", False),   # 3:00 AM — before pre-market
        (8, 0): ("Pre-Market", False),       # 8:00 AM
        (9, 29): ("Pre-Market", False),      # 9:29 AM — still pre
        (9, 30): ("Markets Open", True),     # 9:30 AM — open
        (12, 0): ("Markets Open", True),     # midday
        (15, 59): ("Markets Open", True),    # 3:59 PM
        (16, 0): ("After Hours", False),     # 4:00 PM
        (19, 59): ("After Hours", False),    # 7:59 PM
        (20, 0): ("Markets Closed", False),  # 8:00 PM
        (23, 30): ("Markets Closed", False), # late night
    }
    for (hour, minute), expected in cases.items():
        now = datetime(2026, 6, 29, hour, minute)
        assert _market_status(now) == expected, f"{hour:02d}:{minute:02d}"


# ── 4. Service build / dedup / cache (fake FMP, no network) ────────────


class _FakeFMP:
    """Stub FMP client: canned quote + 1D intraday bars, counting calls.

    The sparkline path runs through the shared chart_helper, which calls
    ``get_intraday_prices`` and filters to regular US market hours — so the
    canned bars use ET timestamps inside 09:30–16:00 on one session.
    """

    def __init__(self):
        self.quote_calls = 0
        self.intraday_calls = 0

    async def get_stock_price_quote(self, ticker: str):
        self.quote_calls += 1
        await asyncio.sleep(0)  # force a real await so dedup has a window
        return {"price": 101.0, "changesPercentage": 1.23, "previousClose": 100.0}

    async def get_intraday_prices(self, ticker, interval="5min", from_date=None, to_date=None):
        self.intraday_calls += 1
        await asyncio.sleep(0)
        return [
            {"date": "2026-06-26 10:00:00", "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 10},
            {"date": "2026-06-26 11:00:00", "open": 100.0, "high": 101.2, "low": 100.0, "close": 101.0, "volume": 12},
            {"date": "2026-06-26 12:00:00", "open": 101.0, "high": 102.4, "low": 100.8, "close": 102.0, "volume": 14},
        ]


def _fresh_service() -> tuple[HomeDashboardService, _FakeFMP]:
    HomeDashboardService._cache.clear()
    HomeDashboardService._inflight.clear()
    HomeDashboardService._scanner_inflight.clear()
    HomeDashboardService._float_cache.clear()
    # Prime an empty scanner cache so get_dashboard's scanner path is satisfied
    # from cache — these are PULSE tests; the scanner path (and its real FMP/FINRA
    # network calls) is exercised in test_home_dashboard_scanners.py.
    HomeDashboardService._scanner_cache.clear()
    HomeDashboardService._scanner_cache[_SCANNER_CACHE_KEY] = (
        _time.time(), ScannerGroupsResponse()
    )
    svc = HomeDashboardService()
    fake = _FakeFMP()
    svc.fmp = fake  # type: ignore[assignment]
    return svc, fake


@pytest.mark.asyncio
async def test_build_returns_all_symbols_mapped_and_validated():
    svc, fake = _fresh_service()
    resp = await svc.get_dashboard()

    assert isinstance(resp, HomeDashboardResponse)
    assert len(resp.pulse) == len(_PULSE_SYMBOLS)
    # Order + identity preserved from the configured universe.
    assert [p.symbol for p in resp.pulse] == [c["symbol"] for c in _PULSE_SYMBOLS]
    first = resp.pulse[0]
    assert first.name == "S&P 500" and first.type == "index"
    assert first.spark == [100.0, 101.0, 102.0]     # latest-session intraday, oldest-first
    assert first.previous_close == 100.0            # → dashed reference line on iOS
    assert first.change_percent == 1.23
    # One quote + one intraday call per symbol.
    assert fake.quote_calls == len(_PULSE_SYMBOLS)
    assert fake.intraday_calls == len(_PULSE_SYMBOLS)


@pytest.mark.asyncio
async def test_concurrent_loads_dedup_to_single_fanout():
    svc, fake = _fresh_service()
    # Two simultaneous Home opens after a cold cache → ONE pulse FMP fan-out.
    # The dedup signal is the call count (the responses are fresh objects and
    # Pydantic copies the pulse list on construction, so identity can't be used).
    a, b = await asyncio.gather(svc.get_dashboard(), svc.get_dashboard())
    assert fake.quote_calls == len(_PULSE_SYMBOLS)   # not doubled → deduped
    assert [p.symbol for p in a.pulse] == [p.symbol for p in b.pulse]


@pytest.mark.asyncio
async def test_second_call_hits_in_memory_cache():
    svc, fake = _fresh_service()
    await svc.get_dashboard()
    calls_after_first = fake.quote_calls
    await svc.get_dashboard()  # within TTL → served from cache
    assert fake.quote_calls == calls_after_first  # no new upstream calls


@pytest.mark.asyncio
async def test_symbol_failure_drops_only_that_tile():
    svc, fake = _fresh_service()

    async def flaky_quote(ticker: str):
        if ticker == "BTCUSD":
            raise RuntimeError("FMP boom")
        return {"price": 42.0, "changesPercentage": 0.5}

    svc.fmp.get_stock_price_quote = flaky_quote  # type: ignore[assignment]
    resp = await svc.get_dashboard()

    symbols = {p.symbol for p in resp.pulse}
    assert "BTCUSD" not in symbols                       # the one failure dropped
    assert len(resp.pulse) == len(_PULSE_SYMBOLS) - 1    # everyone else survives
