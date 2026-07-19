"""
Regression tests for the Financials-tab defects surfaced by the adversarial review:

  * 5 section services (signal_of_confidence, earnings, profit_power, revenue_breakdown,
    health_snapshot) had a _safe_float WITHOUT the isfinite guard that growth/health_check
    have — a NaN/Inf FMP field flowed into a REQUIRED response float and 500'd the section.
  * profitability _to_pct under-scaled any ratio >= 1.0 by 100x (AAPL ROE 154% -> 1.54%).
  * profitability _profitability_score divided by a NEGATIVE sector median (only ==0 was
    guarded), scoring a profitable company in a loss-making sector as the WORST.
  * health_check overall rating counted only 'positive' as passed, so an all-neutral
    (in-line-with-sector) company was rated 'poor [0/N]'; neutrals now get half credit.

Pure-function tests; no network, no Supabase.
"""

from __future__ import annotations

import math

import pytest

from app.services.profitability_snapshot_service import _to_pct, _profitability_score
from app.services.health_check_service import _overall_rating
from app.services.signal_of_confidence_service import _safe_float as soc_safe_float
from app.services.earnings_service import _safe_float as earn_safe_float
from app.services.profit_power_service import _safe_float as pp_safe_float
from app.services.revenue_breakdown_service import _safe_float as rev_safe_float
from app.services.health_snapshot_service import _safe_float as hs_safe_float


# ── _safe_float now rejects NaN/Inf across all 5 section services ─────────────

@pytest.mark.parametrize("fn,default", [
    (soc_safe_float, None), (earn_safe_float, None), (pp_safe_float, None),
    (hs_safe_float, None), (rev_safe_float, 0.0),
])
def test_safe_float_rejects_non_finite(fn, default):
    assert fn({"x": float("nan")}, "x") == default   # None==None or 0.0==0.0
    assert fn({"x": float("inf")}, "x") == default
    assert fn({"x": "NaN"}, "x") == default           # string non-finite too
    assert fn({"x": 12.5}, "x") == 12.5               # finite passes through


def test_safe_float_finite_and_missing():
    assert soc_safe_float({}, "missing") is None
    assert rev_safe_float({}, "missing") == 0.0
    assert rev_safe_float({"x": float("-inf")}, "x") == 0.0


# ── _to_pct scales unconditionally (fixes ROE/ROA >= 100%) ────────────────────

@pytest.mark.parametrize("raw,expected", [
    (0.25, 25.0),     # normal margin decimal
    (1.54, 154.0),    # AAPL-style ROE >= 100% (was wrongly returned as 1.54)
    (-0.10, -10.0),   # negative margin
    (0.0, 0.0),
    (None, None),
])
def test_to_pct_unconditional_scaling(raw, expected):
    assert _to_pct(raw) == expected


# ── _profitability_score: negative sector median falls back to absolutes ──────

def test_profitability_score_negative_sector_not_worst():
    # Company +5% margin in a loss-making sector (median -10%). Old code: 5/-10 = -0.5
    # -> score 1 (worst). Now: negative median -> absolute thresholds -> 5% -> score 3.
    assert _profitability_score(5.0, -0.10) == 3


def test_profitability_score_zero_sector_uses_absolutes():
    assert _profitability_score(25.0, 0.0) == 5     # >=20 absolute -> 5


def test_profitability_score_normal_relative():
    # 25% vs 15% sector median -> ratio 1.67 -> score 5
    assert _profitability_score(25.0, 0.15) == 5
    # 5% vs 15% -> ratio 0.33 -> score 1
    assert _profitability_score(5.0, 0.15) == 1


# ── overall rating: all-neutral company is 'mix', not 'poor' ─────────────────

def test_overall_rating_all_neutral_is_mix():
    # 7 metrics, 0 positive, 0 negative, 7 neutral -> passed = 0 + 0.5*7 = 3.5
    # ratio 3.5/7 = 0.5 -> "mix" (was "poor" when only positives counted).
    assert _overall_rating(3.5, 7) == "mix"


def test_overall_rating_buckets():
    assert _overall_rating(7, 7) == "excellent"
    assert _overall_rating(0, 7) == "poor"
    assert _overall_rating(0, 0) == "mix"        # no metrics -> safe default
