"""
Regression tests for the Analysis-tab defects surfaced by the adversarial review:

  * Technical gauge counted only BUY signals, so an all-NEUTRAL indicator set
    (e.g. a freshly-listed ticker with too few candles) collapsed to gauge 0.0 →
    a fabricated "Strong Sell". Now NEUTRAL sits at the 0.5 midpoint (HOLD).
  * Pivot points / fibonacci / volume ingested OHLC via raw float() while the df is
    dropna'd on close only, so a NaN high/low/volume poisoned a REQUIRED response
    float → allow_nan=False 500s the whole detail sheet.
  * _classify_ma_signal divided by ma_val with no zero guard (ZeroDivisionError 500).
  * analyst_service coerced FMP price/target fields with bare float() → float(None)
    TypeError 502 on a present-null field (and NaN into required floats).

Pure/stateless method tests; no network, no Supabase.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.services.technical_analysis_service import (
    TechnicalAnalysisService,
    _gauge_to_signal,
)
from app.schemas.technical_analysis import TechnicalSignal, IndicatorSignal
from app.services.analyst_service import _num


def _df(rows):
    """Build an OHLCV DataFrame (date-indexed) from a list of (o,h,l,c,v) tuples."""
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


# ── Gauge: NEUTRAL is the midpoint, not "Strong Sell" ────────────────────────

def test_gauge_all_neutral_is_hold_not_strong_sell():
    svc = object.__new__(TechnicalAnalysisService)
    # 5 rows → every indicator is gated out (len < window) → all NEUTRAL.
    df = _df([(10.0, 10.5, 9.5, 10.0, 1000.0)] * 5)
    result, gauge, mas, oscs = svc._compute_timeframe_signal(df)
    assert gauge == 0.5                       # was 0.0 (buy-only ratio)
    assert result.signal == TechnicalSignal.HOLD   # was STRONG_SELL
    # Uncomputable rows are listed (so the sheet names them) but NOT counted: this used
    # to read "18 of 18 indicators" for a listing with five candles — eighteen phantom
    # neutrals. Now it is "0 of 0"; iOS renders that as "Not enough history".
    assert len(mas) + len(oscs) == 18 and all(i.value is None for i in (*mas, *oscs))
    assert result.total_indicators == 0
    assert result.matching_indicators == 0


def test_gauge_uptrend_is_finite_and_bullish_biased():
    svc = object.__new__(TechnicalAnalysisService)
    rows = [(float(i), float(i) + 1, float(i) - 1, float(i), 1000.0 + i)
            for i in range(1, 261)]  # 260-row monotonic uptrend
    result, gauge, _, _ = svc._compute_timeframe_signal(_df(rows))
    assert math.isfinite(gauge) and 0.0 <= gauge <= 1.0
    # Price sits above every moving average → net BUY bias → gauge above midpoint.
    assert gauge > 0.5


def test_gauge_to_signal_boundaries():
    assert _gauge_to_signal(0.0) == TechnicalSignal.STRONG_SELL
    assert _gauge_to_signal(0.5) == TechnicalSignal.HOLD
    assert _gauge_to_signal(1.0) == TechnicalSignal.STRONG_BUY


# ── Pivot points: NaN high/low degrades to empty, never a NaN value ──────────

def test_pivot_points_nan_high_returns_empty_levels():
    svc = object.__new__(TechnicalAnalysisService)
    # Prior bar (iloc[-2]) has a NaN high; close is valid (df dropna's close only).
    df = _df([
        (10.0, np.nan, 9.0, 10.0, 1000.0),   # <- prev row, NaN high
        (10.0, 11.0, 9.5, 10.5, 1200.0),
    ])
    pivots = svc._compute_pivot_points(df)
    assert pivots.levels == []               # degraded, no NaN in a required float


def test_pivot_points_normal_case_is_finite():
    svc = object.__new__(TechnicalAnalysisService)
    df = _df([(10.0, 11.0, 9.0, 10.0, 1000.0), (10.5, 11.5, 9.5, 10.5, 1100.0)])
    pivots = svc._compute_pivot_points(df)
    assert pivots.levels and all(math.isfinite(l.value) for l in pivots.levels)


# ── Volume: all-null volume → finite avg_volume_30d, never NaN ───────────────

def test_volume_analysis_all_nan_volume_is_finite():
    svc = object.__new__(TechnicalAnalysisService)
    df = _df([(10.0, 11.0, 9.0, 10.0, np.nan)] * 40)   # every volume NaN
    vol = svc._compute_volume_analysis(df)
    assert math.isfinite(vol.avg_volume_30d)
    assert math.isfinite(vol.current_volume)
    assert math.isfinite(vol.obv) and math.isfinite(vol.money_flow_index)


# ── Fibonacci: all-NaN high/low → NO levels (was: seven identical ones) ──────

def test_fibonacci_without_high_low_is_drawn_from_closing_extremes_and_says_so():
    """A source with no intraday range draws the retracement between the 52-week CLOSING
    high and low, and labels the basis — never the flat fabrication.

    History of this pin: the original fallback was `high = low = last close`, which put
    the same price on all seven rows (a fully drawn card of identical levels — fabricated,
    not degraded). The second contract emitted NO levels for a close-only source, which
    left every coin's Fibonacci card as a bare title ("i don't see any for both daily and
    weekly" — developer, 2026-09-21). A retracement between closing swing extremes is an
    accepted convention, so that is the third contract; the flat case still emits nothing.
    """
    svc = object.__new__(TechnicalAnalysisService)
    df = _df([(10.0, np.nan, np.nan, 10.0 + i, 1000.0) for i in range(30)])
    fib = svc._compute_fibonacci(df)
    assert len(fib.levels) == 7
    values = [l.value for l in fib.levels]
    assert all(math.isfinite(v) for v in values)
    assert len(set(values)) == 7, "seven identical levels is the fabrication this replaces"
    assert values[0] == 39.0 and values[-1] == 10.0, "0% is the closing high, 100% the closing low"
    assert fib.timeframe == "52-Week Levels · closing prices"


def test_fibonacci_on_a_flat_close_only_series_emits_no_levels():
    svc = object.__new__(TechnicalAnalysisService)
    df = _df([(10.0, np.nan, np.nan, 10.0, 1000.0) for _ in range(30)])
    fib = svc._compute_fibonacci(df)
    assert fib.levels == []


def test_fibonacci_with_real_high_low_still_draws_seven_distinct_levels():
    """The equity path is untouched: real high/low still yields a full retracement."""
    svc = object.__new__(TechnicalAnalysisService)
    df = _df([(10.0, 10.0 + i + 1.0, 10.0 + i - 1.0, 10.0 + i, 1000.0) for i in range(30)])
    fib = svc._compute_fibonacci(df)
    assert len(fib.levels) == 7
    assert all(math.isfinite(l.value) for l in fib.levels)
    # Distinct — the degenerate flat retracement above would collapse these to 1.
    assert len({l.value for l in fib.levels}) == 7
    assert fib.timeframe == "52-Week Levels"


# ── MA classifier: MA == 0 must not ZeroDivisionError ────────────────────────

def test_classify_ma_signal_zero_ma_is_neutral():
    svc = TechnicalAnalysisService
    assert svc._classify_ma_signal(100.0, 0.0) == IndicatorSignal.NEUTRAL
    assert svc._classify_ma_signal(100.0, None) == IndicatorSignal.NEUTRAL
    assert svc._classify_ma_signal(100.0, 90.0) == IndicatorSignal.BUY


# ── analyst _num: None / NaN / Inf / non-numeric all degrade to default ──────

@pytest.mark.parametrize("raw,expected", [
    (None, 0.0),
    (float("nan"), 0.0),
    (float("inf"), 0.0),
    ("not-a-number", 0.0),
    (5.5, 5.5),
    ("12.25", 12.25),
    (0, 0.0),
])
def test_analyst_num_coercion(raw, expected):
    assert _num(raw) == expected
