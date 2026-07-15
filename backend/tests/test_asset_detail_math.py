"""
Outlier / edge-case tests for the asset-detail (TickerDetailView) data path.

These pin the degraded behaviors an adversarial cross-layer review surfaced in
the stock / ETF / crypto / index / commodity detail services:

  * _compute_return must NOT relabel a since-inception return as 3Y/5Y/10Y when
    the asset is younger than the requested window (return None instead).
  * historical.sort() / YTD must survive an explicit null `date` from FMP
    (None<str would raise TypeError and 500 the whole screen).
  * employees must tolerate comma-grouped / decimal FMP strings (a bare int()
    would 500 the entire /overview).
  * price change / change% must not discard a legitimate 0.0 (flat day) for a
    staler profile value.
  * ETF weight / price must reject non-finite (NaN/Inf) before it reaches a
    required response float (Starlette allow_nan=False would 500 the response).
  * commodity/gold ETFs land in a dedicated `commodities` bucket (never "100%
    cash" nor "equities"), consistently across both allocation builders.
  * an index's "vs market" is not its own return echoed back.
  * a young coin's computed all-time return is surfaced, not silently discarded.

No network, no Supabase. Pure functions are imported directly; the few stateless
instance methods are exercised on an __init__-bypassed instance.
"""

from __future__ import annotations

import math

import pytest

from app.services.stock_overview_service import (
    _compute_return as stock_compute_return,
    _compute_ytd_return as stock_compute_ytd,
    _parse_historical,
    _safe_int,
    _first_present_float,
)
from app.services.etf_service import (
    _compute_return as etf_compute_return,
    _finite_num,
    ETFService,
)
from app.services.crypto_service import (
    _compute_return as crypto_compute_return,
    _compute_all_time_return,
    CryptoService,
)
from app.services.index_service import (
    _compute_return as index_compute_return,
    _compute_ytd_return as index_compute_ytd,
    IndexService,
)
from app.services.commodity_service import CommodityService


def _rows(closes):
    """Build ascending FMP-style [{date, close}] rows (oldest first)."""
    return [
        {"date": f"2020-01-{(i % 28) + 1:02d}", "close": float(c)}
        for i, c in enumerate(closes)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# _compute_return: short history must return None, not a mislabeled full-range
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "fn", [stock_compute_return, etf_compute_return, crypto_compute_return, index_compute_return]
)
def test_compute_return_none_when_history_shorter_than_window(fn):
    # 500 rows but a 1260-day (5Y) window: cannot cover it -> None (was a
    # since-inception return mislabeled "5 Years").
    prices = _rows([100.0 + i for i in range(500)])
    assert fn(prices, 1260) is None
    assert fn(prices, 2520) is None  # 10Y also None


@pytest.mark.parametrize(
    "fn", [stock_compute_return, etf_compute_return, crypto_compute_return, index_compute_return]
)
def test_compute_return_boundary_equal_length_is_none(fn):
    # len(prices) == days_back is still insufficient (need days_back+1 to slice).
    prices = _rows([10.0 * (i + 1) for i in range(10)])
    assert fn(prices, 10) is None


@pytest.mark.parametrize(
    "fn", [stock_compute_return, etf_compute_return, crypto_compute_return, index_compute_return]
)
def test_compute_return_correct_when_history_covers_window(fn):
    # 300 rows, 252-day window: real trailing return over the last 252 rows.
    # start = prices[-(252+1)] , end = prices[-1]
    prices = _rows([float(i) for i in range(1, 301)])  # 1..300
    start = prices[-(252 + 1)]["close"]
    end = prices[-1]["close"]
    expected = ((end - start) / start) * 100
    assert fn(prices, 252) == pytest.approx(expected)


@pytest.mark.parametrize(
    "fn", [stock_compute_return, etf_compute_return, crypto_compute_return, index_compute_return]
)
def test_compute_return_empty_and_single(fn):
    assert fn([], 252) is None
    assert fn(_rows([100.0]), 252) is None  # single row


# ─────────────────────────────────────────────────────────────────────────────
# Null-date resilience (sort key + YTD startswith)
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_historical_survives_explicit_null_date():
    # An explicit null date used to make .get("date","") return None and crash
    # the sort with "None < str" TypeError.
    raw = [
        {"date": "2024-01-02", "close": 1900.0},
        {"date": None, "close": 1901.0},   # malformed row
        {"date": "2024-01-01", "close": 1899.0},
    ]
    out = _parse_historical(list(raw))  # must not raise
    assert len(out) == 3
    # null-date row sorts first (treated as ""), real dates ascending after it
    assert out[0]["close"] == 1901.0
    assert out[-1]["date"] == "2024-01-02"


def test_parse_historical_all_null_dates_no_crash():
    raw = [{"date": None, "close": 1.0}, {"date": None, "close": 2.0}]
    assert len(_parse_historical(list(raw))) == 2


def test_ytd_return_survives_null_date_row():
    # A None date used to crash date_str.startswith(...) with AttributeError.
    prices = [
        {"date": None, "close": 100.0},
        {"date": "2024-06-01", "close": 110.0},
    ]
    # Should not raise; None-date row is skipped by the year match.
    result = stock_compute_ytd(prices)
    assert result is None or isinstance(result, float)


# ─────────────────────────────────────────────────────────────────────────────
# _safe_int: dirty FMP employee strings must not 500 the whole /overview
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [
        ("164,000", 164000),
        ("12345.0", 12345),
        (" 1,234 ", 1234),
        (164000, 164000),
        (12345.6, 12345),
        (None, 0),
        ("", 0),
        ("N/A", 0),
        ("inf", 0),
        ("nan", 0),
        (True, 0),  # bool treated as absent, not 1
    ],
)
def test_safe_int(value, expected):
    assert _safe_int(value) == expected


# ─────────────────────────────────────────────────────────────────────────────
# _first_present_float: a genuine 0.0 must win over a staler fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_first_present_float_keeps_genuine_zero():
    quote = {"change": 0.0, "changePercentage": 0.0}
    profile = {"change": -1.85, "changePercentage": -1.20}
    # A flat quote must NOT fall through to the stale profile.
    assert _first_present_float((quote, "change"), (profile, "change")) == 0.0
    assert _first_present_float(
        (quote, "changePercentage"), (profile, "changePercentage")
    ) == 0.0


def test_first_present_float_falls_back_when_key_absent():
    quote = {}  # key genuinely missing
    profile = {"change": -1.85}
    assert _first_present_float((quote, "change"), (profile, "change")) == -1.85


def test_first_present_float_skips_nonfinite_and_defaults():
    quote = {"change": float("nan")}
    profile = {"change": float("inf")}
    assert _first_present_float((quote, "change"), (profile, "change"), default=7.0) == 7.0


# ─────────────────────────────────────────────────────────────────────────────
# _finite_num + ETF weight builders: NaN/Inf must never reach a required float
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [
        (float("nan"), 0.0),
        (float("inf"), 0.0),
        (float("-inf"), 0.0),
        ("NaN", 0.0),
        ("inf", 0.0),
        ("12.5", 12.5),
        (3.2, 3.2),
        (None, 0.0),
        ("N/A", 0.0),
    ],
)
def test_finite_num(value, expected):
    out = _finite_num(value)
    assert math.isfinite(out)
    assert out == expected


def test_etf_top_holdings_rejects_nan_weight_row():
    svc = object.__new__(ETFService)  # bypass __init__ (no FMP/Supabase)
    holders = [
        {"asset": "AAA", "weightPercentage": 12.5},
        {"asset": "XYZ", "weightPercentage": "NaN"},   # malformed -> would 500
        {"asset": "BBB", "weightPercentage": float("inf")},
    ]
    out = svc._build_top_holdings(holders)
    assert len(out) == 3
    for h in out:
        assert math.isfinite(h.weight)
    assert out[1].weight == 0.0
    assert out[2].weight == 0.0


def test_etf_sector_weights_rejects_nan_weight_row():
    svc = object.__new__(ETFService)
    sectors = [{"sector": "Tech", "weightPercentage": "NaN"}]
    out = svc._build_sector_weights(sectors)
    assert len(out) == 1
    assert math.isfinite(out[0].weight)
    assert out[0].weight == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# ETF gold/commodity allocation: dedicated bucket, consistent across builders
# ─────────────────────────────────────────────────────────────────────────────

def test_infer_asset_allocation_commodity_uses_commodities_bucket():
    svc = object.__new__(ETFService)
    alloc = svc._infer_asset_allocation(asset_class="Commodity", total_assets=1e9)
    assert alloc.commodities == 95
    assert alloc.equities == 0
    assert alloc.cash == 5
    assert alloc.commodities > 0 and alloc.cash < 100  # not "100% cash"


def test_build_asset_allocation_commodity_uses_commodities_bucket():
    svc = object.__new__(ETFService)
    alloc = svc._build_asset_allocation(
        sectors_list=[], asset_class="Gold", total_assets=1e9
    )
    # No cash sector -> remaining 100% into commodities, none into equities.
    assert alloc.commodities > 0
    assert alloc.equities == 0.0


def test_both_allocation_builders_agree_commodity_is_not_equities_or_cash():
    svc = object.__new__(ETFService)
    a = svc._infer_asset_allocation(asset_class="Commodity", total_assets=1e9)
    b = svc._build_asset_allocation(sectors_list=[], asset_class="Commodity", total_assets=1e9)
    for alloc in (a, b):
        assert alloc.commodities > 0        # both route to commodities
        assert alloc.equities == 0          # neither mislabels as equities
        assert alloc.cash < 100             # neither is "100% cash"


def test_equity_etf_allocation_unchanged():
    svc = object.__new__(ETFService)
    alloc = svc._infer_asset_allocation(asset_class="Equity", total_assets=1e9)
    assert alloc.equities > 90
    assert alloc.commodities == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Index: vs_market must not echo the index's own return
# ─────────────────────────────────────────────────────────────────────────────

def test_index_performance_periods_vs_market_is_none():
    svc = object.__new__(IndexService)
    periods = svc._build_performance_periods(
        one_month=1.0, ytd=5.0, one_year=22.0,
        three_year=None, five_year=None, ten_year=None,
    )
    assert len(periods) == 3  # None rows skipped
    for p in periods:
        assert p.vs_market_percent is None
    one_year = next(p for p in periods if p.label == "1 Year")
    assert one_year.change_percent == 22.0  # the real return still shown


# ─────────────────────────────────────────────────────────────────────────────
# Crypto: young coin surfaces its all-time return instead of dropping the row
# ─────────────────────────────────────────────────────────────────────────────

def _crypto_periods(svc, *, ten_year, all_time):
    return svc._build_performance_periods(
        one_month=2.0, ytd=3.0, one_year=None,
        three_year=None, five_year=None, ten_year=ten_year, all_time=all_time,
        bench_1m=None, bench_ytd=None, bench_1y=None,
        bench_3y=None, bench_5y=None, bench_10y=None, bench_all_time=None,
        benchmark_label="BTC",
    )


def test_crypto_all_time_surfaced_when_no_ten_year():
    svc = object.__new__(CryptoService)
    periods = _crypto_periods(svc, ten_year=None, all_time=150.0)
    labels = [p.label for p in periods]
    assert "All Time" in labels
    at = next(p for p in periods if p.label == "All Time")
    assert at.change_percent == 150.0
    assert at.vs_market_percent is None  # no aligned benchmark for since-inception


def test_crypto_ten_year_preferred_when_available():
    svc = object.__new__(CryptoService)
    periods = _crypto_periods(svc, ten_year=300.0, all_time=999.0)
    labels = [p.label for p in periods]
    assert "10 Years" in labels
    assert "All Time" not in labels


def test_crypto_no_long_horizon_when_both_none():
    svc = object.__new__(CryptoService)
    periods = _crypto_periods(svc, ten_year=None, all_time=None)
    labels = [p.label for p in periods]
    assert "10 Years" not in labels and "All Time" not in labels


def test_compute_all_time_return_direct():
    prices = _rows([100.0, 150.0])
    assert _compute_all_time_return(prices) == pytest.approx(50.0)
    assert _compute_all_time_return([]) is None


# ─────────────────────────────────────────────────────────────────────────────
# ETF gold/commodity: 100%-"Cash & Others" sectorsList must NOT render 100% cash
# (FMP lumps a non-decomposable physical-gold fund's holdings into one cash row)
# ─────────────────────────────────────────────────────────────────────────────

def test_build_asset_allocation_gold_all_cash_sector_is_not_100pct_cash():
    svc = object.__new__(ETFService)
    # FMP's shape for a fund it can't break down: one 100% "Cash & Others" row.
    alloc = svc._build_asset_allocation(
        sectors_list=[{"industry": "Cash & Others", "exposure": 100}],
        asset_class="Gold",
        total_assets=1e9,
    )
    assert alloc.commodities > 0        # routed to the commodities bucket
    assert alloc.cash < 100             # NOT "100% cash"
    assert alloc.equities == 0.0        # and not mislabeled as equities


def test_build_asset_allocation_all_cash_sector_agrees_across_asset_classes():
    svc = object.__new__(ETFService)
    for ac, bucket in (("Commodity", "commodities"), ("Bond", "bonds")):
        alloc = svc._build_asset_allocation(
            sectors_list=[{"industry": "Cash & Others", "exposure": 100}],
            asset_class=ac, total_assets=1e9,
        )
        assert getattr(alloc, bucket) > 0
        assert alloc.cash < 100


def test_build_asset_allocation_real_cash_pct_still_respected():
    # A genuine partial cash allocation (not the all-cash sentinel) is preserved.
    svc = object.__new__(ETFService)
    alloc = svc._build_asset_allocation(
        sectors_list=[{"industry": "Cash & Others", "exposure": 3}],
        asset_class="Equity", total_assets=1e9,
    )
    assert alloc.cash == 3
    assert alloc.equities == pytest.approx(97.0)


# ─────────────────────────────────────────────────────────────────────────────
# Commodity performance: NaN-safe + short-history omission (not clamped mislabel)
# ─────────────────────────────────────────────────────────────────────────────

def test_commodity_performance_empty_when_latest_close_nonfinite():
    svc = object.__new__(CommodityService)
    hist = _rows([100.0 + i for i in range(300)])
    hist[-1]["close"] = float("nan")  # FMP NaN token parsed to float('nan')
    assert svc._build_performance(hist) == []  # no NaN change_percent leaks out


def test_commodity_performance_skips_period_with_nonfinite_past_close():
    svc = object.__new__(CommodityService)
    hist = _rows([100.0 + i for i in range(300)])
    hist[-21]["close"] = float("inf")  # the 1M (21-back) reference is non-finite
    periods = svc._build_performance(hist)
    for p in periods:
        assert math.isfinite(p.change_percent)  # nothing serializes to invalid JSON
    assert "1M" not in [p.label for p in periods]  # that one period omitted


def test_commodity_performance_omits_long_horizon_on_short_history():
    svc = object.__new__(CommodityService)
    hist = _rows([100.0 + i for i in range(800)])  # ~3.2y of daily data
    labels = [p.label for p in svc._build_performance(hist)]
    # 10Y/5Y need 2520/1260 rows — must be OMITTED, not a clamped mislabel of the
    # ~3y (earliest-available) return under a longer horizon (the original bug).
    assert "10Y" not in labels and "5Y" not in labels
    # 3Y (756) and 1Y (252) DO have enough history here, so they must still appear.
    assert "3Y" in labels and "1Y" in labels


def test_commodity_performance_omits_3y_when_under_three_years():
    svc = object.__new__(CommodityService)
    hist = _rows([100.0 + i for i in range(400)])  # ~1.6y: enough for 1Y, not 3Y
    labels = [p.label for p in svc._build_performance(hist)]
    assert "1Y" in labels
    assert "3Y" not in labels and "5Y" not in labels and "10Y" not in labels


# ─────────────────────────────────────────────────────────────────────────────
# Index return helpers: a NaN/Inf close must degrade to None, never a NaN return
# ─────────────────────────────────────────────────────────────────────────────

def test_index_compute_return_nonfinite_end_returns_none():
    rows = _rows([100.0] * 300)
    rows[-1]["close"] = float("nan")
    assert index_compute_return(rows, 30) is None


def test_index_compute_return_nonfinite_start_returns_none():
    rows = _rows([100.0] * 300)
    rows[-31]["close"] = float("inf")  # the 30-back start row
    assert index_compute_return(rows, 30) is None


def test_index_compute_return_finite_history_still_works():
    rows = _rows([100.0] * 269 + [110.0] * 31)  # 30-back close is 110
    # end=110, start=110 → 0.0 (sanity: guard doesn't break the happy path)
    assert index_compute_return(rows, 30) == pytest.approx(0.0)


def test_index_compute_ytd_nonfinite_returns_none():
    # First row of the current year carries a NaN close → YTD omitted, not NaN.
    from datetime import datetime, timezone
    year = datetime.now(tz=timezone.utc).year
    rows = [
        {"date": f"{year}-01-02", "close": float("nan")},
        {"date": f"{year}-06-01", "close": 120.0},
    ]
    assert index_compute_ytd(rows) is None


# ─────────────────────────────────────────────────────────────────────────────
# ALL asset services: a NaN/Inf close must degrade to None, never a NaN return —
# change_percent is a NON-optional Double on iOS, and a NaN token anywhere breaks
# the decode of the WHOLE detail screen (stock/etf/crypto/index share the DTO).
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "fn", [stock_compute_return, etf_compute_return, crypto_compute_return, index_compute_return]
)
def test_compute_return_nonfinite_close_returns_none_all_services(fn):
    rows = _rows([100.0] * 300)
    rows[-1]["close"] = float("nan")     # latest close NaN → end non-finite
    assert fn(rows, 30) is None
    rows2 = _rows([100.0] * 300)
    rows2[-31]["close"] = float("inf")   # 30-back start non-finite
    assert fn(rows2, 30) is None


def test_crypto_all_time_return_nonfinite_returns_none():
    rows = _rows([100.0, 150.0, 200.0])
    rows[-1]["close"] = float("nan")
    assert _compute_all_time_return(rows) is None
    rows2 = _rows([100.0, 150.0, 200.0])
    rows2[0]["close"] = float("inf")
    assert _compute_all_time_return(rows2) is None
