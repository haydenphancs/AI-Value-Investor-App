"""
Tracking service — pure transforms + the assets/sparkline data path.

Covers the money-format helper (band-boundary rollover), the sparkline
downsample + latest-trading-day filter + crypto extended-hours branch, and the
quote day-change field-name read (equities vs crypto/index/commodity). All
inline fakes — no network / Supabase, per the suite rules.

Run: cd backend && ./venv/bin/pytest tests/test_tracking_service_math.py -x
"""

from __future__ import annotations

import json
import math

import pytest

from app.schemas.tracking import TrackingFeedResponse
from app.services import tracking_service as tsvc
from app.services.tracking_service import (
    TrackingService,
    _amount_sort_key,
    _downsample,
    _format_amount,
)


# ════════════════════════════ _format_amount ═════════════════════════════


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "$0"),
        (999, "$999"),
        (1_000, "$1K"),
        (700_000, "$700K"),
        (999_499, "$999K"),        # rounds to 999K, stays in K band
        (999_600, "$1.0M"),        # would render "$1000K" — must roll to M
        (1_000_000, "$1.0M"),
        (1_040_000, "$1.0M"),
        (5_234_567, "$5.2M"),
        (999_960_000, "$1.00B"),   # would render "$1000.0M" — must roll to B
        (1_000_000_000, "$1.00B"),
        (2_340_000_000, "$2.34B"),
    ],
)
def test_format_amount_band_rollover(value, expected):
    assert _format_amount(value) == expected


def test_format_amount_is_sign_agnostic():
    # abs() — a "sold" magnitude stored negative still formats positive.
    assert _format_amount(-5_200_000) == "$5.2M"


def test_amount_sort_key_roundtrips_magnitude():
    # Sanity: the label parser recovers the order of magnitude. (Insider items
    # now sort by raw_amount, not this — but the helper is still used elsewhere.)
    assert _amount_sort_key("$5.2M") == pytest.approx(5_200_000.0)
    assert _amount_sort_key("$700K") == pytest.approx(700_000.0)
    assert _amount_sort_key("$2.34B") == pytest.approx(2_340_000_000.0)
    assert _amount_sort_key("") == 0.0
    assert _amount_sort_key("garbage") == 0.0


# ════════════════════════════ _downsample ════════════════════════════════


def test_downsample_passthrough_when_small():
    vals = [1.0, 2.0, 3.0]
    assert _downsample(vals, 30) is vals or _downsample(vals, 30) == vals


def test_downsample_two_points_survive():
    assert _downsample([10.0, 20.0], 30) == [10.0, 20.0]


def test_downsample_caps_and_keeps_first_last():
    vals = [float(i) for i in range(200)]
    out = _downsample(vals, 30)
    assert len(out) <= 30
    assert out[0] == 0.0            # first always kept (iOS colors off data[0])
    assert out[-1] == 199.0        # last always kept (iOS dots data.last)


def test_downsample_indices_strictly_ascending_and_unique():
    vals = [float(i) for i in range(137)]
    out = _downsample(vals, 30)
    # Output is a subsequence of the input in order, no repeats.
    assert out == sorted(out)
    assert len(set(out)) == len(out)


# ═══════════════════ sparkline data path (fetch_chart_data faked) ═════════


def _bar(date: str, close: float) -> dict:
    return {"date": date, "close": close}


def _closes(sparklines: dict, ticker: str) -> list:
    """Just the series. `_get_all_sparklines` values are
    ``(closes, span_from, span_to)``; the span's own math is covered in
    test_intraday_span.py, and its wiring in the span tests below."""
    return sparklines[ticker][0]


@pytest.mark.asyncio
async def test_sparkline_keeps_only_latest_trading_day(monkeypatch):
    tsvc._sparkline_cache.clear()

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        # Two sessions — the mini-chart must fold to the latest one only.
        return [
            _bar("2026-07-08 09:30:00", 10.0),
            _bar("2026-07-08 12:00:00", 11.0),
            _bar("2026-07-09 09:30:00", 20.0),
            _bar("2026-07-09 12:00:00", 21.0),
            _bar("2026-07-09 16:00:00", 22.0),
        ]

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    svc = TrackingService()
    out = await svc._get_all_sparklines(["ORCL"], {"ORCL": "stock"})
    assert _closes(out, "ORCL") == [20.0, 21.0, 22.0]   # only 2026-07-09 bars, rounded


@pytest.mark.asyncio
async def test_sparkline_single_point_day_returns_empty(monkeypatch):
    tsvc._sparkline_cache.clear()

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return [_bar("2026-07-09 09:30:00", 20.0)]  # only one bar in latest day

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    svc = TrackingService()
    out = await svc._get_all_sparklines(["ORCL"], {"ORCL": "stock"})
    # <2 closes → honest empty at FULL span, never a 1-point chart.
    assert out["ORCL"] == ([], 0.0, 1.0)


@pytest.mark.asyncio
async def test_sparkline_empty_bars_returns_empty(monkeypatch):
    tsvc._sparkline_cache.clear()

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return []

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    svc = TrackingService()
    out = await svc._get_all_sparklines(["ORCL"], {"ORCL": "stock"})
    assert out["ORCL"] == ([], 0.0, 1.0)


@pytest.mark.asyncio
async def test_sparkline_crypto_uses_extended_hours(monkeypatch):
    tsvc._sparkline_cache.clear()
    seen: dict[str, bool] = {}

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        seen[ticker] = extended_hours
        return [_bar("2026-07-09 09:30:00", 1.0), _bar("2026-07-09 12:00:00", 2.0)]

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    svc = TrackingService()
    await svc._get_all_sparklines(
        ["BTCUSD", "AAPL"], {"BTCUSD": "crypto", "AAPL": "stock"}
    )
    assert seen["BTCUSD"] is True    # 24/7 asset keeps the full intraday series
    assert seen["AAPL"] is False     # equities stay clipped to regular hours


# ══════════════════ quote day-change field-name (assets merge) ════════════


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        class _R:
            pass
        r = _R()
        r.data = self._rows
        return r


class _FakeSupabase:
    def __init__(self, watchlist):
        self._watchlist = watchlist

    def table(self, name):
        return _FakeTable(self._watchlist if name == "watchlist_items" else [])


class _QuoteOnlyFMP:
    """Only the batch-quote path returns data; every other alert source is empty."""

    def __init__(self, quotes):
        self._quotes = quotes

    async def get_batch_quotes_bulk(self, symbols):
        return [self._quotes[s] for s in symbols if s in self._quotes]

    async def get_earnings_calendar(self, from_date, to_date):
        return []

    async def get_grades(self, ticker, limit=20):
        return []

    async def get_insider_trading(self, ticker, limit=30):
        return []


@pytest.mark.asyncio
async def test_change_percent_reads_plural_key_for_non_stock(monkeypatch):
    """Crypto/index/commodity /quote rows expose the day-change as
    `changesPercentage` (plural); the merge must not report a flat +0.00%."""
    tsvc._feed_cache.clear()
    tsvc._sparkline_cache.clear()

    watchlist = [{"ticker": "BTCUSD", "company_name": "Bitcoin", "asset_type": "crypto"}]
    quotes = {
        "BTCUSD": {
            "symbol": "BTCUSD", "name": "Bitcoin USD", "price": 65000.0,
            "previousClose": 63000.0, "changesPercentage": 3.2,  # plural only
        }
    }
    monkeypatch.setattr(tsvc, "get_supabase", lambda: _FakeSupabase(watchlist))

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return []
    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)

    svc = TrackingService()
    svc.fmp = _QuoteOnlyFMP(quotes)
    feed = await svc.get_tracking_feed("u-crypto")

    assert len(feed.assets) == 1
    asset = feed.assets[0]
    assert asset.price == pytest.approx(65000.0)
    assert asset.change_percent == pytest.approx(3.2)   # NOT 0.0


@pytest.mark.asyncio
async def test_change_percent_reads_singular_key_for_stock(monkeypatch):
    tsvc._feed_cache.clear()
    tsvc._sparkline_cache.clear()

    watchlist = [{"ticker": "ORCL", "company_name": "Oracle", "asset_type": "Stock"}]
    quotes = {
        "ORCL": {
            "symbol": "ORCL", "name": "Oracle", "price": 144.27,
            "previousClose": 140.5, "changePercentage": -2.45,  # singular
        }
    }
    monkeypatch.setattr(tsvc, "get_supabase", lambda: _FakeSupabase(watchlist))

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return []
    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)

    svc = TrackingService()
    svc.fmp = _QuoteOnlyFMP(quotes)
    feed = await svc.get_tracking_feed("u-stock")

    assert feed.assets[0].change_percent == pytest.approx(-2.45)


# ═══════════ watchlist read failure must NOT look like an empty feed ══════
# Regression: a Supabase read error was caught and turned into
# `TrackingFeedResponse()` — HTTP 200 with zero assets, byte-identical to a
# genuinely empty watchlist. iOS treats that as a successful load and purges
# every portfolio ticker missing from the feed, so the client permanently
# deleted every ticker AND every hand-entered shares/market_value from every
# portfolio, on all devices, on one transient blip.


class _ExplodingTable:
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        raise RuntimeError("PostgREST 503: connection reset by peer")


class _ExplodingSupabase:
    def table(self, name):
        return _ExplodingTable()


@pytest.mark.asyncio
async def test_watchlist_read_failure_raises_instead_of_empty_feed(monkeypatch):
    tsvc._feed_cache.clear()
    monkeypatch.setattr(tsvc, "get_supabase", lambda: _ExplodingSupabase())

    svc = TrackingService()
    with pytest.raises(tsvc.WatchlistUnavailableError):
        await svc.get_tracking_feed("u-boom")

    # And the failure must not be cached — the next request has to retry.
    assert "u-boom" not in tsvc._feed_cache


def test_watchlist_unavailable_maps_to_a_dedicated_error_code():
    """The endpoint must answer 503 WATCHLIST_UNAVAILABLE, not a generic 502.

    Pinned because `classify_exception` falls through to FMP_UNAVAILABLE for
    anything whose message contains "timeout" — which a PostgREST read timeout
    does — and that would point the user (and the logs) at the wrong system.
    """
    from app.api.error_response import ErrorCode, classify_exception

    code, status = classify_exception(
        tsvc.WatchlistUnavailableError("read timeout talking to postgrest")
    )
    assert code is ErrorCode.WATCHLIST_UNAVAILABLE
    assert status == 503


def test_invalidate_feed_cache_drops_only_that_user():
    tsvc._feed_cache.clear()
    tsvc._feed_cache_set("u-a", TrackingFeedResponse())
    tsvc._feed_cache_set("u-b", TrackingFeedResponse())

    tsvc.invalidate_feed_cache("u-a")

    # Without this, a star-add followed by the immediate refresh read the
    # PRE-ADD cached feed, so the new ticker looked like an orphan and the
    # client's purge deleted it again — the add silently undid itself.
    assert tsvc._feed_cache_get("u-a") is None
    assert tsvc._feed_cache_get("u-b") is not None
    tsvc.invalidate_feed_cache("u-never-cached")  # no-op, must not raise


# ═══════════════ non-finite quote cells must not 500 the feed ═════════════
# FastAPI renders via Starlette's JSONResponse → json.dumps(..., allow_nan=False),
# so ONE NaN on ONE ticker used to raise ValueError and blank the entire Assets
# tab. A bare float() could not catch it: float("nan") is truthy (survives the
# `or` fallbacks) and round(nan, 2) never raises (so the per-row except is dead).


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.asyncio
async def test_non_finite_quote_field_never_reaches_the_wire(monkeypatch, bad):
    tsvc._feed_cache.clear()
    tsvc._sparkline_cache.clear()

    watchlist = [
        {"ticker": "BAD", "company_name": "Bad Co"},
        {"ticker": "ORCL", "company_name": "Oracle"},
    ]
    quotes = {
        "BAD": {
            "symbol": "BAD", "price": bad, "changePercentage": bad,
            "previousClose": bad, "marketCap": bad,
        },
        "ORCL": {
            "symbol": "ORCL", "price": 144.27, "changePercentage": -2.45,
            "previousClose": 147.9, "marketCap": 4.1e11,
        },
    }
    monkeypatch.setattr(tsvc, "get_supabase", lambda: _FakeSupabase(watchlist))

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return []
    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)

    svc = TrackingService()
    svc.fmp = _QuoteOnlyFMP(quotes)
    feed = await svc.get_tracking_feed("u-nan")

    # This is the exact mechanism that 500s in production.
    json.dumps(feed.model_dump(), allow_nan=False)

    assert len(feed.assets) == 2                      # bad row degrades, not drops
    healthy = next(a for a in feed.assets if a.ticker == "ORCL")
    assert healthy.price == pytest.approx(144.27)     # untouched by its neighbour
    assert healthy.change_percent == pytest.approx(-2.45)
    bad_row = next(a for a in feed.assets if a.ticker == "BAD")
    assert math.isfinite(bad_row.price) and math.isfinite(bad_row.change_percent)
    assert bad_row.previous_close is None             # honest null, not a fake number
    assert bad_row.market_cap is None


@pytest.mark.asyncio
async def test_non_finite_stored_holding_fields_are_dropped(monkeypatch):
    """`shares` / `market_value` come from Supabase, not FMP — same allow_nan risk."""
    tsvc._feed_cache.clear()
    tsvc._sparkline_cache.clear()

    watchlist = [{
        "ticker": "ORCL", "company_name": "Oracle",
        "shares": float("nan"), "market_value": float("inf"),
    }]
    quotes = {"ORCL": {"symbol": "ORCL", "price": 144.27, "changePercentage": 1.0}}
    monkeypatch.setattr(tsvc, "get_supabase", lambda: _FakeSupabase(watchlist))

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return []
    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)

    svc = TrackingService()
    svc.fmp = _QuoteOnlyFMP(quotes)
    feed = await svc.get_tracking_feed("u-holding-nan")

    json.dumps(feed.model_dump(), allow_nan=False)
    assert feed.assets[0].shares is None
    assert feed.assets[0].market_value is None


# ══════════════════════════ signed zero ═══════════════════════════════════


@pytest.mark.parametrize("raw", [-0.001, -0.004, -0.0])
@pytest.mark.asyncio
async def test_barely_negative_change_never_serializes_as_signed_zero(monkeypatch, raw):
    """round(-0.001, 2) is -0.0, and iOS reads `-0.0 >= 0` as TRUE (green up
    arrow) while formatting it as "-0.00" — the row rendered "+-0.00%"."""
    tsvc._feed_cache.clear()
    tsvc._sparkline_cache.clear()

    watchlist = [{"ticker": "ORCL", "company_name": "Oracle"}]
    quotes = {"ORCL": {"symbol": "ORCL", "price": 144.27, "changePercentage": raw}}
    monkeypatch.setattr(tsvc, "get_supabase", lambda: _FakeSupabase(watchlist))

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return []
    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)

    svc = TrackingService()
    svc.fmp = _QuoteOnlyFMP(quotes)
    feed = await svc.get_tracking_feed(f"u-signed-{raw}")

    change = feed.assets[0].change_percent
    assert change == 0.0
    # `copysign` is the only way to see the sign bit on a zero.
    assert math.copysign(1.0, change) == 1.0, "signed zero leaked to the wire"


# ═════════════ extended-hours resolution + cache-key separation ════════════


@pytest.mark.asyncio
async def test_extended_hours_resolved_from_symbol_when_asset_type_is_useless(monkeypatch):
    """`watchlist_items.asset_type` defaults to 'Stock' and POST /watchlist never
    writes it, so the old `asset_type == "crypto"` test was ALWAYS false — the
    documented crypto fix never fired in production. Resolution must fall back to
    the symbol, and must cover commodities (which the literal test excluded)."""
    tsvc._sparkline_cache.clear()
    seen: dict[str, bool] = {}

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        seen[ticker] = extended_hours
        return [_bar("2026-07-09 09:30:00", 1.0), _bar("2026-07-09 12:00:00", 2.0)]

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    svc = TrackingService()
    await svc._get_all_sparklines(
        ["BTCUSD", "GCUSD", "CLUSD", "AAPL", "^GSPC"],
        # Exactly what the DB actually holds for rows added via the iOS flow.
        {"BTCUSD": "stock", "GCUSD": "", "CLUSD": "stock", "AAPL": "stock", "^GSPC": "stock"},
    )
    assert seen["BTCUSD"] is True     # 24/7
    assert seen["GCUSD"] is True      # gold future, ~23h
    assert seen["CLUSD"] is True      # crude future, ~23h
    assert seen["AAPL"] is False      # equity stays clipped
    assert seen["^GSPC"] is False     # index tracks the equity session


@pytest.mark.asyncio
async def test_sparkline_cache_key_separates_extended_from_regular(monkeypatch):
    """The series DEPENDS on the window, so the cache key must too. Latent while
    extended_hours was uniformly False — live the moment resolution was fixed."""
    tsvc._sparkline_cache.clear()

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        if extended_hours:
            return [_bar("2026-07-09 02:00:00", 5.0), _bar("2026-07-09 22:00:00", 9.0)]
        return [_bar("2026-07-09 09:30:00", 1.0), _bar("2026-07-09 15:55:00", 2.0)]

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    svc = TrackingService()

    # Same ticker, both windows — the second must NOT be served the first's series.
    ext = await svc._get_all_sparklines(["XYZUSD"], {"XYZUSD": "crypto"})
    reg = await svc._get_all_sparklines(["XYZUSD"], {"XYZUSD": "equity-forced"})

    assert _closes(ext, "XYZUSD") == [5.0, 9.0]
    # 'equity-forced' isn't a trusted class, so the symbol decides → still crypto.
    assert _closes(reg, "XYZUSD") == [5.0, 9.0]
    # Direct key check: the two variants occupy distinct slots.
    tsvc._sparkline_cache_set("ZZZ", [1.0, 2.0], extended_hours=True, span=(0.0, 0.4))
    tsvc._sparkline_cache_set("ZZZ", [3.0, 4.0], extended_hours=False, span=(0.0, 0.9))
    # The SPAN is cached with the series it describes. Storing only the closes
    # would let a fresh span be paired with a stale series, drawing the line out
    # to a time its last bar never reached.
    assert tsvc._sparkline_cache_get("ZZZ", True) == ([1.0, 2.0], 0.0, 0.4)
    assert tsvc._sparkline_cache_get("ZZZ", False) == ([3.0, 4.0], 0.0, 0.9)


# ══════════════════════ sparkline session span ════════════════════════════


@pytest.mark.asyncio
async def test_sparkline_span_marks_a_partial_session_as_partial(monkeypatch):
    """A mid-morning series must NOT claim the whole card.

    This is the reported bug: iOS spreads the closes between `width * span_from`
    and `width * span_to`, so a full-width span on a 09:30–12:15 series draws a
    34-bar morning exactly like a completed 78-bar day.
    """
    tsvc._sparkline_cache.clear()

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return [_bar("2026-08-13 09:30:00", 100.0), _bar("2026-08-13 12:15:00", 101.0)]

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    out = await TrackingService()._get_all_sparklines(["ORCL"], {"ORCL": "stock"})

    _series, lo, hi = out["ORCL"]
    assert lo == 0.0
    assert 0.40 < hi < 0.45, "a 09:30-12:15 equity session is ~2/5 of 09:30-16:00"


@pytest.mark.asyncio
async def test_sparkline_span_uses_the_asset_s_own_session_window(monkeypatch):
    """Crypto is measured on 00:00-24:00, equities on 09:30-16:00.

    The window must follow the SAME `extended_hours` flag the bars were fetched
    with, or a 24/7 series gets positioned against the equity bell and reads as
    finished by lunchtime.
    """
    tsvc._sparkline_cache.clear()

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return [_bar("2026-08-13 00:00:00", 100.0), _bar("2026-08-13 12:15:00", 101.0)]

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    svc = TrackingService()
    crypto = await svc._get_all_sparklines(["BTCUSD"], {"BTCUSD": "crypto"})

    _series, lo, hi = crypto["BTCUSD"]
    assert (lo, hi) == (0.0, pytest.approx(740 / 1440, abs=1e-3))


@pytest.mark.asyncio
async def test_sparkline_span_ignores_bars_whose_close_was_dropped(monkeypatch):
    """The span must describe the bars that SURVIVED filtering.

    A non-finite close is removed from the series; reading the span off the
    unfiltered day would still stretch the line out to that bar's time — drawing
    to a moment whose price was thrown away.
    """
    tsvc._sparkline_cache.clear()

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return [
            _bar("2026-08-13 09:30:00", 100.0),
            _bar("2026-08-13 12:15:00", 101.0),
            _bar("2026-08-13 15:55:00", float("nan")),   # dropped from the series
        ]

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    out = await TrackingService()._get_all_sparklines(["ORCL"], {"ORCL": "stock"})

    series, _lo, hi = out["ORCL"]
    assert series == [100.0, 101.0]
    assert hi < 1.0, "span stretched to a bar whose close was discarded"
    assert 0.40 < hi < 0.45


@pytest.mark.asyncio
async def test_sparkline_span_survives_a_cache_round_trip(monkeypatch):
    """Series and span are cached TOGETHER. A cache holding only the closes would
    pair a fresh span with a stale series and draw past its real last bar."""
    tsvc._sparkline_cache.clear()
    calls = {"n": 0}

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        calls["n"] += 1
        return [_bar("2026-08-13 09:30:00", 100.0), _bar("2026-08-13 12:15:00", 101.0)]

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    svc = TrackingService()
    cold = await svc._get_all_sparklines(["ORCL"], {"ORCL": "stock"})
    warm = await svc._get_all_sparklines(["ORCL"], {"ORCL": "stock"})

    assert calls["n"] == 1, "second call should have been served from cache"
    assert cold["ORCL"] == warm["ORCL"]


@pytest.mark.asyncio
async def test_sparkline_failure_degrades_to_full_span_not_zero_width(monkeypatch):
    """Full width is the pre-span behaviour. A degraded row must fall back to it
    rather than to a collapsed line — the fallback must never make a card that
    used to render fine render worse."""
    tsvc._sparkline_cache.clear()

    async def boom(fmp, ticker, rng, extended_hours=False):
        raise RuntimeError("FMP down")

    monkeypatch.setattr(tsvc, "fetch_chart_data", boom)
    out = await TrackingService()._get_all_sparklines(["ORCL"], {"ORCL": "stock"})
    assert out["ORCL"] == ([], 0.0, 1.0)


# ════════════════════ sub-dollar sparkline keeps its shape ════════════════


@pytest.mark.asyncio
async def test_sub_dollar_sparkline_is_not_flattened_to_one_level(monkeypatch):
    """round(c, 2) collapsed a $0.20 holding's whole session into 1-2 levels, so
    the card drew a dead-flat line next to a live non-zero % change."""
    tsvc._sparkline_cache.clear()
    closes = [0.2015, 0.2021, 0.2033, 0.2028, 0.2044, 0.2049, 0.2037]

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return [_bar(f"2026-07-09 10:{i:02d}:00", c) for i, c in enumerate(closes)]

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    svc = TrackingService()
    out = await svc._get_all_sparklines(["PENNY"], {"PENNY": "stock"})

    series = _closes(out, "PENNY")
    assert len(series) == len(closes)
    assert min(series) != max(series), "series flattened — chart would be a dead line"
    assert len(set(series)) >= 5      # real shape preserved, not 1-2 levels


@pytest.mark.asyncio
async def test_large_price_sparkline_stays_at_two_decimals(monkeypatch):
    """Precision scales to magnitude — normal equities must not gain noise digits."""
    tsvc._sparkline_cache.clear()

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return [
            _bar("2026-07-09 10:00:00", 144.2712),
            _bar("2026-07-09 10:05:00", 144.9988),
        ]

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    svc = TrackingService()
    out = await svc._get_all_sparklines(["ORCL"], {"ORCL": "stock"})
    assert _closes(out, "ORCL") == [144.27, 145.0]


# ═════════════ a fully-degraded feed must not be pinned in cache ══════════


@pytest.mark.asyncio
async def test_feed_with_zero_resolved_quotes_is_not_cached(monkeypatch):
    """Otherwise one FMP blip looked like a 30-second outage on the tab: every
    row shows a placeholder price and the client's retries re-read the same
    cached placeholders."""
    tsvc._feed_cache.clear()
    tsvc._sparkline_cache.clear()

    watchlist = [{"ticker": "ORCL", "company_name": "Oracle"}]
    monkeypatch.setattr(tsvc, "get_supabase", lambda: _FakeSupabase(watchlist))

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return []
    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)

    svc = TrackingService()
    svc.fmp = _QuoteOnlyFMP({})           # every quote unresolved
    feed = await svc.get_tracking_feed("u-degraded")

    assert len(feed.assets) == 1
    assert tsvc._feed_cache_get("u-degraded") is None, "degraded feed was pinned"

    # A feed that DID resolve its quotes is cached as normal.
    svc.fmp = _QuoteOnlyFMP({"ORCL": {"symbol": "ORCL", "price": 1.0, "changePercentage": 0.0}})
    await svc.get_tracking_feed("u-healthy")
    assert tsvc._feed_cache_get("u-healthy") is not None
