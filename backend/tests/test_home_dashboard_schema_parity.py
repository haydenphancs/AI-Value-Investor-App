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
    SignalsGroupResponse,
    ThemesGroupResponse,
)
from app.services.home_dashboard_service import (
    HomeDashboardService,
    _PULSE_SYMBOLS,
    _SCANNER_CACHE_KEY,
    _THEMES_CACHE_KEY,
    _downsample,
    _intraday_sparkline,
    _market_status,
)
from app.services.signals_service import SignalsService, _SIGNALS_CACHE_KEY
from app.services import home_dashboard_service as hds
import time as _time


# ── 1. Schema parity ──────────────────────────────────────────────────

# The exact snake_case keys the iOS `MarketPulseItemDTO.CodingKeys` expects.
_ITEM_KEYS = {"symbol", "name", "type", "price", "change_percent", "previous_close", "spark"}
# The exact snake_case keys the iOS `HomeDashboardResponseDTO.CodingKeys` expects.
_RESPONSE_KEYS = {"market_status_text", "market_is_open", "pulse", "scanners", "signals", "themes"}


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
    # Additive + defaulted: a response built without signals ships all-null groups
    # (iOS omits the whole section) rather than a decode-breaking absent key.
    assert dumped["signals"] == {"congress": None, "whale": None, "earnings": None}
    # Themes likewise defaults to an empty list → iOS hides the Emerging Frontiers section.
    assert dumped["themes"] == {"themes": []}


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
        self.intraday_extended_hours: dict[str, bool | None] = {}

    async def get_stock_price_quote(self, ticker: str):
        self.quote_calls += 1
        await asyncio.sleep(0)  # force a real await so dedup has a window
        return {"price": 101.0, "changesPercentage": 1.23, "previousClose": 100.0}

    async def get_intraday_prices(self, ticker, interval="5min", from_date=None, to_date=None):
        self.intraday_calls += 1
        self.intraday_extended_hours[ticker] = None  # set by fetch_chart_data caller
        await asyncio.sleep(0)
        # NOTE: the 02:00 and 20:00 ET bars are load-bearing. This fake used to
        # emit ONLY 10:00/11:00/12:00 — all inside 09:30–16:00 — so every test
        # passed identically whether or not the regular-hours filter ran. That
        # blind spot is exactly why the Bitcoin tile shipped clipped to the equity
        # session. Off-hours bars make the two windows distinguishable.
        return [
            {"date": "2026-06-26 02:00:00", "open": 98.0, "high": 98.5, "low": 97.5, "close": 98.0, "volume": 5},
            {"date": "2026-06-26 10:00:00", "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 10},
            {"date": "2026-06-26 11:00:00", "open": 100.0, "high": 101.2, "low": 100.0, "close": 101.0, "volume": 12},
            {"date": "2026-06-26 12:00:00", "open": 101.0, "high": 102.4, "low": 100.8, "close": 102.0, "volume": 14},
            {"date": "2026-06-26 20:00:00", "open": 102.0, "high": 103.5, "low": 101.8, "close": 103.0, "volume": 8},
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
    # Prime an empty signals cache too — get_dashboard() now fans out a 3rd branch
    # (App-Exclusive Signals). These are PULSE tests; the signals aggregation (and
    # its real FMP/Supabase calls) is exercised in test_signals_service.py.
    SignalsService._cache.clear()
    SignalsService._inflight.clear()
    SignalsService._cache[_SIGNALS_CACHE_KEY] = (_time.time(), SignalsGroupResponse())
    # Prime an empty themes cache too — get_dashboard() fans out a 4th branch
    # (Emerging Frontiers). These are PULSE tests; the themes build (Supabase + FMP)
    # is exercised in test_home_dashboard_themes.py.
    HomeDashboardService._themes_inflight.clear()
    HomeDashboardService._themes_cache.clear()
    HomeDashboardService._themes_cache[_THEMES_CACHE_KEY] = (_time.time(), ThemesGroupResponse())
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


@pytest.mark.asyncio
async def test_pulse_non_finite_price_drops_tile_and_never_serializes_nan():
    """Regression (adversarial review): a NaN/Inf quote price must drop the tile
    (via _finite_float), never reaching the REQUIRED MarketPulseItemResponse.price
    as a non-standard JSON `NaN`/`Infinity` token — which would 500 the WHOLE
    dashboard for the cache window and defeat the per-tile degradation."""
    import json
    import math as _m

    svc, _fake = _fresh_service()

    async def nan_quote(ticker: str):
        if ticker == "^GSPC":
            return {"price": float("nan"), "changesPercentage": 0.5, "previousClose": 100.0}
        # A non-finite CHANGE (not price) must degrade to 0.0, not drop the tile.
        return {"price": 42.0, "changesPercentage": float("inf"), "previousClose": 41.0}

    svc.fmp.get_stock_price_quote = nan_quote  # type: ignore[assignment]
    resp = await svc.get_dashboard()

    symbols = {p.symbol for p in resp.pulse}
    assert "^GSPC" not in symbols                    # NaN-price tile dropped
    assert len(resp.pulse) == len(_PULSE_SYMBOLS) - 1
    for p in resp.pulse:
        assert _m.isfinite(p.price) and p.price > 0
        assert _m.isfinite(p.change_percent)         # Inf change → 0.0
        assert p.previous_close is None or _m.isfinite(p.previous_close)
    # The whole response serializes with NO non-standard tokens.
    json.dumps(resp.model_dump(), allow_nan=False)


# ── 5. Session window per asset class (24/7 tiles) ────────────────────
# Regression: _fetch_sparkline called fetch_chart_data(..., "1D") with the
# DEFAULT extended_hours=False for every tile, so chart_helper._filter_regular_hours
# (a pure time-of-day test) stripped ~70% of Bitcoin's and gold/crude's bars, and
# _intraday_sparkline then pinned "latest day" to the last SURVIVING bar. The tile
# drew an equity-session slice next to a 24/7 live price — and contradicted the
# crypto/commodity DETAIL charts, which both pass extended_hours=True.


@pytest.mark.asyncio
async def test_pulse_sparkline_window_follows_the_asset_class(monkeypatch):
    svc, _fake = _fresh_service()
    seen: dict[str, bool] = {}

    real_fetch = hds.fetch_chart_data

    async def spy_fetch(fmp, symbol, range_code, interval=None, extended_hours=False):
        seen[symbol] = extended_hours
        return await real_fetch(
            fmp, symbol, range_code, interval=interval, extended_hours=extended_hours
        )

    monkeypatch.setattr(hds, "fetch_chart_data", spy_fetch)
    await svc.get_dashboard()

    by_symbol = {c["symbol"]: c["type"] for c in _PULSE_SYMBOLS}
    for symbol, kind in by_symbol.items():
        expected = kind in ("crypto", "commodity")
        assert seen[symbol] is expected, f"{symbol} ({kind}) window mismatch"


@pytest.mark.asyncio
async def test_off_hours_bars_survive_for_crypto_and_are_clipped_for_indices():
    """The behavioural half of the test above: same upstream bars, different series."""
    svc, _fake = _fresh_service()
    resp = await svc.get_dashboard()
    tiles = {p.symbol: p for p in resp.pulse}

    # ^GSPC (index) → only the 09:30–16:00 ET bars.
    assert tiles["^GSPC"].spark == [100.0, 101.0, 102.0]
    # BTCUSD (crypto) → the 02:00 and 20:00 ET bars survive too.
    assert tiles["BTCUSD"].spark == [98.0, 100.0, 101.0, 102.0, 103.0]
    # GCUSD / CLUSD are continuously-quoted futures — same treatment as crypto.
    assert tiles["GCUSD"].spark == [98.0, 100.0, 101.0, 102.0, 103.0]
    assert tiles["CLUSD"].spark == [98.0, 100.0, 101.0, 102.0, 103.0]


# ── 6. Signed zero ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_barely_negative_pulse_change_never_serializes_signed_zero():
    """round(-0.001, 2) is -0.0. iOS reads `changePercent >= 0` as TRUE for -0.0
    (tile paints green) while "%+.2f" prints "-0.00%" — a red number in green."""
    import math as _m

    svc, _fake = _fresh_service()

    async def tiny_negative_quote(ticker: str):
        return {"price": 42.0, "changesPercentage": -0.001, "previousClose": 42.0}

    svc.fmp.get_stock_price_quote = tiny_negative_quote  # type: ignore[assignment]
    resp = await svc.get_dashboard()

    assert resp.pulse, "expected tiles"
    for p in resp.pulse:
        assert p.change_percent == 0.0
        assert _m.copysign(1.0, p.change_percent) == 1.0, "signed zero on the wire"


# ── 7. Market status: holidays + half-days ────────────────────────────
# _market_status knew only weekday + time-of-day, so on ~10 holidays and 2
# half-days a year the strip read "Markets Open" over stale closes — and the same
# helper gates main.py's FMP pre-warmer and chat_service's "current numbers" line.


def test_market_status_closed_on_a_weekday_holiday():
    # Thanksgiving 2026 falls on Thursday 26 Nov — a weekday, mid-session.
    assert _market_status(datetime(2026, 11, 26, 11, 0)) == ("Markets Closed", False)
    # Christmas 2026 is a Friday.
    assert _market_status(datetime(2026, 12, 25, 11, 0)) == ("Markets Closed", False)
    # Good Friday 2026.
    assert _market_status(datetime(2026, 4, 3, 11, 0)) == ("Markets Closed", False)


def test_market_status_half_day_closes_at_1pm():
    # Day after Thanksgiving 2026 — trades to 13:00 ET, then shut (no after-hours).
    half_day = (2026, 11, 27)
    assert _market_status(datetime(*half_day, 11, 0)) == ("Markets Open", True)
    assert _market_status(datetime(*half_day, 12, 59)) == ("Markets Open", True)
    assert _market_status(datetime(*half_day, 13, 0)) == ("Markets Closed", False)
    assert _market_status(datetime(*half_day, 17, 0)) == ("Markets Closed", False)


def test_market_status_naive_datetime_is_read_as_eastern():
    """Guard rail for the delegation: utils.market_hours.session_phase stamps a
    naive datetime as UTC, but every case in this file means ET. _market_status
    must localize before delegating, or every boundary shifts 4-5 hours."""
    # 09:30 ET is 13:30 UTC. If the naive value were read as UTC it would land in
    # pre-market (04:00-09:30 ET), not the open.
    assert _market_status(datetime(2026, 6, 29, 9, 30)) == ("Markets Open", True)
    # 20:00 ET is midnight UTC — read as UTC it would be "closed" for the wrong
    # reason on the wrong day.
    assert _market_status(datetime(2026, 6, 29, 19, 59)) == ("After Hours", False)


# ── 8. Degraded pulse: TTL + timeout guard ────────────────────────────


@pytest.mark.asyncio
async def test_partial_pulse_is_not_pinned_for_the_full_ttl():
    """_build_pulse swallows per-tile failures and can never raise, so "didn't
    raise" was not "succeeded": one 2s FMP blip pinned a partial strip in a
    CLASS-level cache for the full 5 minutes, for every user."""
    svc, _fake = _fresh_service()

    async def flaky_quote(ticker: str):
        if ticker == "BTCUSD":
            raise RuntimeError("FMP boom")
        return {"price": 42.0, "changesPercentage": 0.5, "previousClose": 41.0}

    svc.fmp.get_stock_price_quote = flaky_quote  # type: ignore[assignment]
    resp = await svc.get_dashboard()
    assert len(resp.pulse) == len(_PULSE_SYMBOLS) - 1

    ts, cached = HomeDashboardService._cache[hds._CACHE_KEY]
    assert len(cached) < len(_PULSE_SYMBOLS)
    # Age it past the DEGRADED ttl but well inside the healthy one.
    HomeDashboardService._cache[hds._CACHE_KEY] = (
        ts - (hds._CACHE_DEGRADED_TTL_SECONDS + 1), cached
    )
    svc.fmp.get_stock_price_quote = _FakeFMP().get_stock_price_quote  # type: ignore[assignment]
    again = await svc.get_dashboard()
    assert len(again.pulse) == len(_PULSE_SYMBOLS), "degraded strip was pinned"


@pytest.mark.asyncio
async def test_healthy_pulse_is_cached_for_the_full_ttl():
    svc, fake = _fresh_service()
    await svc.get_dashboard()
    ts, cached = HomeDashboardService._cache[hds._CACHE_KEY]
    assert len(cached) == len(_PULSE_SYMBOLS)

    # Just past the degraded TTL — a COMPLETE strip must still be served.
    HomeDashboardService._cache[hds._CACHE_KEY] = (
        ts - (hds._CACHE_DEGRADED_TTL_SECONDS + 1), cached
    )
    calls_before = fake.quote_calls
    await svc.get_dashboard()
    assert fake.quote_calls == calls_before, "healthy strip re-fetched too early"


@pytest.mark.asyncio
async def test_slow_pulse_build_does_not_block_the_dashboard(monkeypatch):
    """Scanners/signals/themes each had an 8s ceiling; pulse was a bare await in
    the same gather, so one hung FMP call (30s httpx timeout + retries) blew past
    the iOS 30s URLSession ceiling and turned all of Home into an error banner."""
    svc, _fake = _fresh_service()
    monkeypatch.setattr(hds, "_PULSE_BUILD_TIMEOUT_SECONDS", 0.05)

    async def slow_quote(ticker: str):
        await asyncio.sleep(0.3)      # slower than the ceiling, fast enough to finish
        return {"price": 1.0, "changesPercentage": 0.0, "previousClose": 1.0}

    svc.fmp.get_stock_price_quote = slow_quote  # type: ignore[assignment]

    resp = await asyncio.wait_for(svc.get_dashboard(), timeout=2.0)
    # Cold cache → ships without the strip rather than stalling the whole screen.
    assert resp.pulse == []
    assert isinstance(resp, HomeDashboardResponse)

    # The `shield` is the load-bearing half: the timeout must NOT cancel the
    # shared build (CancelledError is a BaseException and would poison every
    # awaiter parked on `_inflight`). It keeps running and warms the cache, so
    # the NEXT request is served instantly.
    await asyncio.sleep(0.5)
    assert hds._CACHE_KEY in HomeDashboardService._cache
    assert len(HomeDashboardService._cache[hds._CACHE_KEY][1]) == len(_PULSE_SYMBOLS)
    assert HomeDashboardService._inflight == {}, "in-flight future leaked"


@pytest.mark.asyncio
async def test_pulse_timeout_serves_the_last_good_strip(monkeypatch):
    svc, _fake = _fresh_service()
    await svc.get_dashboard()                       # warm the cache
    good = list(HomeDashboardService._cache[hds._CACHE_KEY][1])
    assert len(good) == len(_PULSE_SYMBOLS)

    # Expire it, then make the rebuild slower than the ceiling.
    HomeDashboardService._cache[hds._CACHE_KEY] = (0.0, good)
    monkeypatch.setattr(hds, "_PULSE_BUILD_TIMEOUT_SECONDS", 0.05)

    async def slow_quote(ticker: str):
        await asyncio.sleep(0.3)
        return {"price": 7.0, "changesPercentage": 0.0, "previousClose": 7.0}

    svc.fmp.get_stock_price_quote = slow_quote  # type: ignore[assignment]

    resp = await asyncio.wait_for(svc.get_dashboard(), timeout=2.0)
    # Stale beats blank.
    assert [p.symbol for p in resp.pulse] == [p.symbol for p in good]

    # …and the shielded rebuild still lands, replacing the stale strip.
    await asyncio.sleep(0.5)
    assert HomeDashboardService._cache[hds._CACHE_KEY][1][0].price == 7.0
    assert HomeDashboardService._inflight == {}
