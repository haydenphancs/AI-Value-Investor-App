"""Outlier/edge-case math tests for the TTM industry-benchmark compute.

Covers two confirmed bugs from the deep review:
  * `_num` dropped NaN but NOT +/-inf → an inf TTM field rode into the median.
  * `_extract_ttm` fcf_margin admitted negative/zero revenue (sign flip), diverging
    from the fiscal path's `rev > 0` gate.

Pure functions — no network, no Supabase. `_num`/`_extract_ttm` are module-level;
`_ttm_median` is a @staticmethod, callable without an instance.
"""

import math

import pytest

from app.services.industry_benchmark_service import (
    IndustryBenchmarkService,
    _extract_ttm,
    _num,
)
from app.services.sector_benchmark_service import MIN_SAMPLE_SIZE

_ttm_median = IndustryBenchmarkService._ttm_median


# ── _num: drop NaN AND +/-inf ────────────────────────────────────────

def test_num_drops_infinity_string():
    assert _num({"x": "Infinity"}, "x") is None
    assert _num({"x": "-inf"}, "x") is None


def test_num_drops_float_inf():
    assert _num({"x": float("inf")}, "x") is None
    assert _num({"x": float("-inf")}, "x") is None


def test_num_drops_nan():
    assert _num({"x": float("nan")}, "x") is None
    assert _num({"x": "nan"}, "x") is None


def test_num_keeps_finite_and_parses_strings():
    assert _num({"x": 0.15}, "x") == 0.15
    assert _num({"x": "12.5"}, "x") == 12.5
    assert _num({"x": 0}, "x") == 0.0


def test_num_missing_or_none_is_none():
    assert _num({}, "x") is None
    assert _num({"x": None}, "x") is None
    assert _num({"x": "n/a"}, "x") is None


# ── _extract_ttm: fcf_margin denominator guard (rev > 0) ─────────────

def test_extract_ttm_negative_revenue_fcf_margin_is_none():
    # NEGATIVE revenue/share would sign-flip a cash-generating firm into a deep
    # negative margin. The fix gates on rev > 0 (matching the fiscal path).
    out = _extract_ttm({"freeCashFlowPerShareTTM": 10, "revenuePerShareTTM": -2}, {})
    assert out["fcf_margin"] is None


def test_extract_ttm_zero_revenue_fcf_margin_is_none():
    out = _extract_ttm({"freeCashFlowPerShareTTM": 10, "revenuePerShareTTM": 0.0}, {})
    assert out["fcf_margin"] is None


def test_extract_ttm_tiny_positive_revenue_admitted_like_fiscal():
    # A tiny positive denominator still divides (huge ratio) — this is the SAME
    # accepted behavior as the fiscal path (which also only gates rev > 0, no clamp).
    # Asserted explicitly so nobody adds a TTM-only clamp the fiscal side lacks.
    out = _extract_ttm({"freeCashFlowPerShareTTM": 5, "revenuePerShareTTM": 0.001}, {})
    assert out["fcf_margin"] == 5000.0


def test_extract_ttm_normal_fcf_margin():
    out = _extract_ttm({"freeCashFlowPerShareTTM": 10, "revenuePerShareTTM": 100}, {})
    assert out["fcf_margin"] == 0.1


def test_extract_ttm_inf_field_dropped_to_none():
    # roe has cap=None + positive_only=False → no downstream guard. _num must drop the
    # inf at extraction so it never reaches the median.
    out = _extract_ttm({}, {"returnOnEquityTTM": float("inf")})
    assert out["roe"] is None


def test_extract_ttm_reads_ratios_vs_key_metrics_sources():
    # pe_ratio comes from ratios (r0); roe from key-metrics (k0).
    out = _extract_ttm(
        {"priceToEarningsRatioTTM": 25.0}, {"returnOnEquityTTM": 0.18},
    )
    assert out["pe_ratio"] == 25.0
    assert out["roe"] == 0.18


# ── _ttm_median: positive-only + cap (winsorize, not trim) + min sample ──

def test_ttm_median_positive_only_and_cap_for_pe():
    # pe_ratio: positive_only=True (drop <=0), cap=200 (clamp, keep the row).
    med, n = _ttm_median("pe_ratio", [-5, 10, 20, 300, 40, 50])
    # -5 dropped; 300 clamped to 200 → median([10,20,40,50,200]) = 40, n stays 5.
    assert med == 40.0
    assert n == 5


def test_ttm_median_below_min_sample_returns_none():
    med, n = _ttm_median("pe_ratio", [10, 12, 14, 16])  # n=4 < MIN_SAMPLE_SIZE(5)
    assert med is None
    assert n == 4
    assert MIN_SAMPLE_SIZE == 5


def test_ttm_median_fcf_margin_has_no_self_filter_gate_is_upstream():
    # fcf_margin is (positive_only=False, cap=None) → _ttm_median applies NO filter.
    # A leaked negative-rev contributor would skew the median: this proves the gate
    # MUST live in _extract_ttm (above), not here.
    med, n = _ttm_median("fcf_margin", [-5.0, -5.0, -5.0, 0.1, 0.1])
    assert med == -5.0
    assert n == 5
    # And a clean list medians correctly.
    med2, n2 = _ttm_median("fcf_margin", [0.1, 0.1, 0.1, 0.1, 0.1, 0.1])
    assert med2 == 0.1
    assert n2 == 6


def test_ttm_median_empty_and_all_none():
    assert _ttm_median("pe_ratio", []) == (None, 0)
    # _industry_ttm_values already drops None before calling, but be defensive.
    med, n = _ttm_median("roe", [None, None])  # type: ignore[list-item]
    assert med is None
    assert n == 0
