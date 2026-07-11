"""
Tracking service — pure transforms + the assets/sparkline data path.

Covers the money-format helper (band-boundary rollover), the sparkline
downsample + latest-trading-day filter + crypto extended-hours branch, and the
quote day-change field-name read (equities vs crypto/index/commodity). All
inline fakes — no network / Supabase, per the suite rules.

Run: cd backend && ./venv/bin/pytest tests/test_tracking_service_math.py -x
"""

from __future__ import annotations

import pytest

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
    assert out["ORCL"] == [20.0, 21.0, 22.0]   # only 2026-07-09 bars, rounded


@pytest.mark.asyncio
async def test_sparkline_single_point_day_returns_empty(monkeypatch):
    tsvc._sparkline_cache.clear()

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return [_bar("2026-07-09 09:30:00", 20.0)]  # only one bar in latest day

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    svc = TrackingService()
    out = await svc._get_all_sparklines(["ORCL"], {"ORCL": "stock"})
    assert out["ORCL"] == []   # <2 closes → honest empty, never a 1-point chart


@pytest.mark.asyncio
async def test_sparkline_empty_bars_returns_empty(monkeypatch):
    tsvc._sparkline_cache.clear()

    async def fake_fetch(fmp, ticker, rng, extended_hours=False):
        return []

    monkeypatch.setattr(tsvc, "fetch_chart_data", fake_fetch)
    svc = TrackingService()
    out = await svc._get_all_sparklines(["ORCL"], {"ORCL": "stock"})
    assert out["ORCL"] == []


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

    async def get_batch_quotes(self, symbols):
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
