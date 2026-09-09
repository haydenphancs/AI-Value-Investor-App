"""
Technical analysis over a CLOSE-ONLY price source (CoinGecko crypto history).

Phase 5 moved crypto off FMP onto CoinGecko, whose `market_chart` carries close and
volume but **no intraday high/low**. Five of the eight oscillators need high/low, and
the adversarial review found this breaks four ways — three of them silently:

  1. `df[["open","high","low","close","volume"]]` raises **KeyError** if the columns
     are absent → a hard 500. They must exist as all-NaN instead.
  2. MFI degraded via `_safe_float(NaN) or 50.0` → a confident "Neutral 50" that was
     never measured. Now flagged with `money_flow_index_known`.
  3. Fibonacci fell back to `high = low = last close` → seven IDENTICAL levels, a
     fully-drawn card that is pure fabrication. Now emits no levels
     (`test_analysis_tab_guards.py::test_fibonacci_all_nan_high_low_emits_no_levels`).
  4. 🔴 The gauge denominator was the constant 18. Dropping five indicators while
     dividing by 18 leaves five phantom neutrals in the count AND caps the reachable
     range at [0.139, 0.861] — so crypto could **never** read STRONG BUY or STRONG
     SELL however unanimous the real signals were.

These assert on indicator IDENTIFIERS, not counts, so renaming a row cannot make the
guard vacuous. Pure/stateless method tests; no network, no Supabase.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.services.technical_analysis_service import TechnicalAnalysisService
from app.schemas.technical_analysis import IndicatorSignal, TechnicalSignal


# Indicators that genuinely require an intraday range.
NEEDS_HIGH_LOW = {"Stoch(14,3)", "ADX(14)", "Williams %R", "CCI(14)", "ATR(14)"}
# Oscillators computable from close alone.
CLOSE_ONLY_OSCILLATORS = {"RSI(14)", "StochRSI(14)", "MACD(12,26)"}


def _frame(closes, *, high_low: bool, volume: float = 1_000_000.0) -> pd.DataFrame:
    """Mirror `_fetch_daily_ohlcv_uncached`'s output shape.

    `high_low=False` reproduces the CoinGecko frame: the columns are PRESENT but
    entirely NaN — never absent, which is the KeyError above.
    """
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": closes if high_low else np.full(len(closes), np.nan),
            "high": closes + 1.0 if high_low else np.full(len(closes), np.nan),
            "low": closes - 1.0 if high_low else np.full(len(closes), np.nan),
            "close": closes,
            "volume": np.full(len(closes), volume),
        },
        index=idx,
    )


def _rising(n: int = 300):
    return np.linspace(100.0, 400.0, n)


def _falling(n: int = 300):
    return np.linspace(400.0, 100.0, n)


# ── 1. The five high/low indicators are dropped, not faked ───────────────────

def test_close_only_source_omits_exactly_the_high_low_indicators():
    svc = object.__new__(TechnicalAnalysisService)
    _, _, mas, oscs = svc._compute_timeframe_signal(_frame(_rising(), high_low=False))
    names = {o.name for o in oscs}

    # Assert on identifiers: none of the five may ship at all.
    assert names & NEEDS_HIGH_LOW == set(), f"fabricated without high/low: {names & NEEDS_HIGH_LOW}"
    # ...and the close-only ones must still be there — otherwise "drop everything"
    # would pass this test vacuously.
    assert CLOSE_ONLY_OSCILLATORS <= names, f"lost a computable oscillator: {CLOSE_ONLY_OSCILLATORS - names}"
    # Moving averages are close-only and must be untouched.
    assert len(mas) == 10


def test_full_ohlcv_source_still_ships_all_eighteen():
    """The equity path must be completely unaffected by the crypto degrade."""
    svc = object.__new__(TechnicalAnalysisService)
    result, _, mas, oscs = svc._compute_timeframe_signal(_frame(_rising(), high_low=True))
    names = {o.name for o in oscs}

    assert NEEDS_HIGH_LOW <= names, f"equity path lost: {NEEDS_HIGH_LOW - names}"
    assert CLOSE_ONLY_OSCILLATORS <= names
    assert len(mas) + len(oscs) == 18
    assert result.total_indicators == 18


# ── 2. The gauge denominator is the count actually shipped ───────────────────

def test_total_indicators_matches_the_rows_actually_shipped():
    """iOS renders `total_indicators` verbatim as "N of M indicators"."""
    svc = object.__new__(TechnicalAnalysisService)
    for high_low in (True, False):
        result, _, mas, oscs = svc._compute_timeframe_signal(
            _frame(_rising(), high_low=high_low)
        )
        assert result.total_indicators == len(mas) + len(oscs), (
            f"high_low={high_low}: wire says {result.total_indicators} but "
            f"{len(mas) + len(oscs)} rows were shipped"
        )
    # And the close-only frame really is the smaller one (anti-vacuity).
    r_hl, *_ = svc._compute_timeframe_signal(_frame(_rising(), high_low=True))
    r_co, *_ = svc._compute_timeframe_signal(_frame(_rising(), high_low=False))
    assert r_co.total_indicators == 13 and r_hl.total_indicators == 18


def test_matching_indicators_never_exceeds_the_total():
    svc = object.__new__(TechnicalAnalysisService)
    for closes in (_rising(), _falling()):
        for high_low in (True, False):
            result, _, _, _ = svc._compute_timeframe_signal(
                _frame(closes, high_low=high_low)
            )
            assert 0 <= result.matching_indicators <= result.total_indicators


# ── 3. 🔴 STRONG BUY / STRONG SELL stay reachable without high/low ───────────

@pytest.mark.parametrize(
    "closes,expected",
    [(_rising(), TechnicalSignal.STRONG_BUY), (_falling(), TechnicalSignal.STRONG_SELL)],
)
def test_extreme_verdicts_are_reachable_on_a_close_only_source(closes, expected):
    """The constant-18 denominator capped the gauge at [0.139, 0.861].

    Both STRONG bands sit outside that range, so under the old code a unanimous
    close-only set could not produce either verdict — every crypto reading was
    pulled toward neutral. A monotonic ramp makes every computable indicator agree.
    """
    svc = object.__new__(TechnicalAnalysisService)
    result, gauge, _, _ = svc._compute_timeframe_signal(_frame(closes, high_low=False))
    assert result.signal == expected, f"gauge={gauge:.3f} gave {result.signal}"
    # Explicitly outside the old reachable band.
    assert gauge > 0.861 or gauge < 0.139


def test_gauge_stays_within_zero_and_one_for_both_sources():
    svc = object.__new__(TechnicalAnalysisService)
    for closes in (_rising(), _falling()):
        for high_low in (True, False):
            _, gauge, _, _ = svc._compute_timeframe_signal(
                _frame(closes, high_low=high_low)
            )
            assert 0.0 <= gauge <= 1.0 and math.isfinite(gauge)


# ── 4. MFI says "unknown" instead of a fabricated neutral 50 ─────────────────

def test_mfi_is_flagged_unknown_when_the_source_has_no_high_low():
    svc = object.__new__(TechnicalAnalysisService)
    vol = svc._compute_volume_analysis(_frame(_rising(60), high_low=False))

    assert vol.money_flow_index_known is False
    # The wire field is a non-Optional Double that shipped iOS builds decode, so it
    # must stay finite — the FLAG is what carries the honesty, and iOS hides the row.
    assert math.isfinite(vol.money_flow_index)
    # OBV needs only close+volume, so it must still be real.
    assert math.isfinite(vol.obv)


def test_mfi_is_known_and_real_when_high_low_is_present():
    svc = object.__new__(TechnicalAnalysisService)
    vol = svc._compute_volume_analysis(_frame(_rising(60), high_low=True))

    assert vol.money_flow_index_known is True
    assert math.isfinite(vol.money_flow_index)
    # A monotonic ramp is pure accumulation → MFI pins high, not at the old 50.0
    # default. This is what proves the number was actually computed.
    assert vol.money_flow_index > 50.0


# ── 5. Pivots and support/resistance degrade to empty, never to NaN ──────────

def test_pivots_and_support_resistance_are_empty_without_high_low():
    svc = object.__new__(TechnicalAnalysisService)
    df = _frame(_rising(60), high_low=False)

    pivots = svc._compute_pivot_points(df)
    assert pivots.levels == []

    sr = svc._compute_support_resistance(df)
    assert sr.resistance_levels == [] and sr.support_levels == []
    # current_price comes from close, which this source does have.
    assert math.isfinite(sr.current_price) and sr.current_price > 0


# ── 6. The whole detail frame stays JSON-serializable (no NaN on the wire) ───

def test_close_only_frame_produces_no_non_finite_floats():
    """allow_nan=False would 500 the detail sheet; NaN also crashes the iOS decode."""
    import json

    svc = object.__new__(TechnicalAnalysisService)
    df = _frame(_rising(300), high_low=False)

    for payload in (
        svc._compute_volume_analysis(df),
        svc._compute_pivot_points(df),
        svc._compute_fibonacci(df),
        svc._compute_support_resistance(df),
    ):
        # allow_nan=False is exactly what FastAPI's encoder enforces.
        json.dumps(payload.model_dump(), allow_nan=False)
