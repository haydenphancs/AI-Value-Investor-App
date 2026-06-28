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
)
from app.services.home_dashboard_service import (
    HomeDashboardService,
    _PULSE_SYMBOLS,
    _extract_sparkline,
    _market_status,
)


# ── 1. Schema parity ──────────────────────────────────────────────────

# The exact snake_case keys the iOS `MarketPulseItemDTO.CodingKeys` expects.
_ITEM_KEYS = {"symbol", "name", "type", "price", "change_percent", "spark"}
# The exact snake_case keys the iOS `HomeDashboardResponseDTO.CodingKeys` expects.
_RESPONSE_KEYS = {"market_status_text", "market_is_open", "pulse"}


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


# ── 2. Sparkline transform ────────────────────────────────────────────


def test_extract_sparkline_empty_and_bad_shapes_return_empty():
    assert _extract_sparkline(None) == []
    assert _extract_sparkline([]) == []
    assert _extract_sparkline({}) == []
    assert _extract_sparkline({"historical": []}) == []
    assert _extract_sparkline("garbage") == []
    assert _extract_sparkline(123) == []


def test_extract_sparkline_orders_oldest_first_regardless_of_input_order():
    # FMP commonly returns newest-first.
    newest_first = [
        {"date": "2026-01-03", "close": 30.0},
        {"date": "2026-01-02", "close": 20.0},
        {"date": "2026-01-01", "close": 10.0},
    ]
    assert _extract_sparkline(newest_first) == [10.0, 20.0, 30.0]

    # …but even if it arrives oldest-first, the result is still oldest-first.
    oldest_first = list(reversed(newest_first))
    assert _extract_sparkline(oldest_first) == [10.0, 20.0, 30.0]


def test_extract_sparkline_handles_dict_wrapper_and_adjclose_fallback():
    raw = {
        "historical": [
            {"date": "2026-01-02", "adjClose": 12.5},  # no `close`
            {"date": "2026-01-01", "close": 11.0},
        ]
    }
    assert _extract_sparkline(raw) == [11.0, 12.5]


def test_extract_sparkline_skips_none_zero_negative_and_nonnumeric_closes():
    raw = [
        {"date": "2026-01-05", "close": 50.0},
        {"date": "2026-01-04", "close": None},      # skipped
        {"date": "2026-01-03", "close": 0.0},       # skipped (non-positive)
        {"date": "2026-01-02", "close": -3.0},      # skipped (negative)
        {"date": "2026-01-01", "close": "oops"},    # skipped (non-numeric)
    ]
    assert _extract_sparkline(raw) == [50.0]


def test_extract_sparkline_caps_to_requested_points_keeping_most_recent():
    # 30 ascending closes by date; ask for the most recent 3.
    raw = [
        {"date": f"2026-02-{i:02d}", "close": float(i)} for i in range(1, 31)
    ]
    out = _extract_sparkline(raw, points=3)
    assert out == [28.0, 29.0, 30.0]  # most recent 3, oldest-first


def test_extract_sparkline_tolerates_missing_dates_and_nondict_rows():
    raw = [
        {"close": 5.0},            # no date → sorts as ""
        "not-a-dict",              # ignored
        {"date": "2026-01-01", "close": 9.0},
    ]
    out = _extract_sparkline(raw)
    # Both numeric rows survive; exact order isn't asserted (one has no date),
    # but the result must contain only the valid closes and nothing fabricated.
    assert sorted(out) == [5.0, 9.0]


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
    """Stub FMP client: canned quote + history, counting calls."""

    def __init__(self):
        self.quote_calls = 0
        self.history_calls = 0

    async def get_stock_price_quote(self, ticker: str):
        self.quote_calls += 1
        await asyncio.sleep(0)  # force a real await so dedup has a window
        return {"price": 100.0 + self.quote_calls, "changesPercentage": 1.23}

    async def get_historical_prices(self, ticker, from_date=None, to_date=None):
        self.history_calls += 1
        await asyncio.sleep(0)
        return [
            {"date": "2026-01-02", "close": 11.0},
            {"date": "2026-01-01", "close": 10.0},
        ]


def _fresh_service() -> tuple[HomeDashboardService, _FakeFMP]:
    HomeDashboardService._cache.clear()
    HomeDashboardService._inflight.clear()
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
    assert first.spark == [10.0, 11.0]              # oldest-first
    assert first.change_percent == 1.23
    # One quote + one history call per symbol.
    assert fake.quote_calls == len(_PULSE_SYMBOLS)
    assert fake.history_calls == len(_PULSE_SYMBOLS)


@pytest.mark.asyncio
async def test_concurrent_loads_dedup_to_single_fanout():
    svc, fake = _fresh_service()
    # Two simultaneous Home opens after a cold cache → ONE FMP fan-out.
    a, b = await asyncio.gather(svc.get_dashboard(), svc.get_dashboard())
    assert a is b  # same cached object returned to both awaiters
    assert fake.quote_calls == len(_PULSE_SYMBOLS)   # not doubled


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
