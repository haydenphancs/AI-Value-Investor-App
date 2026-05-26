"""
Unit tests for moat_scoring_service.

Covers:
  - Pure scoring helpers (geometric log-ratio, absolute-delta, HHI band,
    lifecycle, YoY).
  - The <2-metrics → None confidence-low gate.
  - End-to-end pillar scoring with a fake sector-benchmark lookup
    injected, so no Supabase / FMP traffic.

Self-contained per the project testing rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

from app.services.moat_scoring_service import (
    MetricDriver,
    MoatScoringService,
    PILLAR_BRAND,
    PILLAR_COST,
    PILLAR_INTANGIBLE,
    PILLAR_NETWORK,
    PILLAR_ORDER,
    PILLAR_SWITCHING,
    PillarResult,
    _absolute_delta_score,
    _assemble_pillar,
    _compute_yoy_pct,
    _hhi_to_score,
    _latest,
    _lifecycle_to_score,
    _safe_float,
    _score_from_median_ratio,
)


# ── _score_from_median_ratio (geometric log-ratio) ─────────────────────


@pytest.mark.parametrize(
    "focal,median,higher_is_better,expected",
    [
        (10.0, 10.0, True, 5.0),     # at median → 5.0
        (20.0, 10.0, True, 7.5),     # 2× median (one doubling) → 7.5
        (40.0, 10.0, True, 10.0),    # 4× median (two doublings) → cap at 10
        (80.0, 10.0, True, 10.0),    # well past cap
        (5.0, 10.0, True, 2.5),      # 0.5× median → 2.5
        (2.5, 10.0, True, 0.0),      # 0.25× median → floor at 0
        # Lower-is-better metrics (e.g. SG&A/Revenue):
        (10.0, 10.0, False, 5.0),    # at median
        (5.0, 10.0, False, 7.5),     # half the median → great
        (20.0, 10.0, False, 2.5),    # double → bad
    ],
)
def test_score_from_median_ratio_anchors(focal, median, higher_is_better, expected):
    assert _score_from_median_ratio(
        focal, median, higher_is_better=higher_is_better
    ) == expected


def test_score_from_median_ratio_returns_none_when_inputs_missing():
    assert _score_from_median_ratio(None, 10.0) is None
    assert _score_from_median_ratio(10.0, None) is None
    assert _score_from_median_ratio(10.0, 0) is None        # zero median undefined
    assert _score_from_median_ratio(10.0, -1.0) is None     # negative median undefined


def test_score_from_median_ratio_zero_focal_edge_cases():
    # Higher-is-better with zero focal → worst (0.0)
    assert _score_from_median_ratio(0.0, 10.0, higher_is_better=True) == 0.0
    # Lower-is-better with zero focal → best (10.0)  — e.g., zero SG&A is ideal
    assert _score_from_median_ratio(0.0, 10.0, higher_is_better=False) == 10.0


# ── _absolute_delta_score ──────────────────────────────────────────────


def test_absolute_delta_score_above_median_premium():
    # Focal beats sector by 4 percentage points → +2.0 score above neutral
    assert _absolute_delta_score(12.0, 8.0, delta_per_point=2.0) == 7.0


def test_absolute_delta_score_below_median_penalty():
    # Focal lags sector by 6 points → -3.0 below neutral
    assert _absolute_delta_score(2.0, 8.0, delta_per_point=2.0) == 2.0


def test_absolute_delta_score_handles_none():
    assert _absolute_delta_score(None, 5.0) is None
    assert _absolute_delta_score(5.0, None) is None


def test_absolute_delta_score_clamps():
    # Massive premium → capped at 10
    assert _absolute_delta_score(100.0, 0.0, delta_per_point=2.0) == 10.0
    # Massive shortfall → floored at 0
    assert _absolute_delta_score(-100.0, 0.0, delta_per_point=2.0) == 0.0


# ── _hhi_to_score (concentration → network effect) ─────────────────────


@pytest.mark.parametrize(
    "hhi,expected",
    [
        (500, 2.5),     # fragmented
        (1200, 4.0),    # moderately fragmented
        (2000, 5.5),    # moderately concentrated
        (3500, 7.0),    # highly concentrated
        (7000, 8.5),    # monopoly-adjacent
    ],
)
def test_hhi_to_score_bands(hhi, expected):
    assert _hhi_to_score(hhi) == expected


def test_hhi_to_score_handles_none():
    assert _hhi_to_score(None) is None
    assert _hhi_to_score(-100) is None


# ── _lifecycle_to_score ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("emerging", 7.5),
        ("secular_growth", 7.0),
        ("mature", 5.0),
        ("declining", 3.0),
    ],
)
def test_lifecycle_to_score(phase, expected):
    assert _lifecycle_to_score(phase) == expected


def test_lifecycle_to_score_unknown_returns_none():
    assert _lifecycle_to_score(None) is None
    assert _lifecycle_to_score("turbocharged") is None


# ── _compute_yoy_pct ────────────────────────────────────────────────────


def test_compute_yoy_pct_basic():
    records = [
        {"date": "2024-12-31", "revenue": 110},
        {"date": "2023-12-31", "revenue": 100},
    ]
    assert _compute_yoy_pct(records, "revenue") == 10.0


def test_compute_yoy_pct_handles_decline():
    records = [
        {"date": "2024-12-31", "revenue": 80},
        {"date": "2023-12-31", "revenue": 100},
    ]
    assert _compute_yoy_pct(records, "revenue") == -20.0


def test_compute_yoy_pct_returns_none_when_insufficient_data():
    assert _compute_yoy_pct([], "revenue") is None
    assert _compute_yoy_pct([{"date": "2024", "revenue": 100}], "revenue") is None
    # Prior is zero → undefined
    assert _compute_yoy_pct([
        {"date": "2024-12-31", "revenue": 100},
        {"date": "2023-12-31", "revenue": 0},
    ], "revenue") is None


# ── _assemble_pillar (confidence buckets) ──────────────────────────────


def test_assemble_pillar_returns_none_when_below_min():
    """<2 metrics resolved → confidence='low', score=None."""
    drivers = [
        MetricDriver(metric="m1", focal=1.0, sector_median=1.0, sub_score=None),
    ]
    result = _assemble_pillar("Test", drivers)
    assert result.score is None
    assert result.confidence == "low"


def test_assemble_pillar_medium_confidence_for_two_metrics():
    drivers = [
        MetricDriver(metric="m1", focal=1.0, sector_median=1.0, sub_score=6.0),
        MetricDriver(metric="m2", focal=2.0, sector_median=2.0, sub_score=8.0),
    ]
    result = _assemble_pillar("Test", drivers)
    assert result.score == 7.0  # average of 6 and 8
    assert result.confidence == "medium"


def test_assemble_pillar_high_confidence_for_three_or_more():
    drivers = [
        MetricDriver(metric="m1", focal=1.0, sector_median=1.0, sub_score=6.0),
        MetricDriver(metric="m2", focal=2.0, sector_median=2.0, sub_score=7.0),
        MetricDriver(metric="m3", focal=3.0, sector_median=3.0, sub_score=8.0),
    ]
    result = _assemble_pillar("Test", drivers)
    assert result.score == 7.0  # average of 6, 7, 8
    assert result.confidence == "high"


def test_assemble_pillar_partial_resolution_ignores_none_drivers():
    """Drivers with sub_score=None don't drag down the average and don't
    count toward the minimum metric threshold."""
    drivers = [
        MetricDriver(metric="m1", focal=1.0, sector_median=1.0, sub_score=8.0),
        MetricDriver(metric="m2", focal=None, sector_median=None, sub_score=None),
        MetricDriver(metric="m3", focal=2.0, sector_median=2.0, sub_score=6.0),
    ]
    result = _assemble_pillar("Test", drivers)
    assert result.score == 7.0  # only m1 and m3 averaged
    assert result.confidence == "medium"   # 2 metrics resolved


# ── _safe_float and _latest ─────────────────────────────────────────────


def test_safe_float_handles_garbage():
    assert _safe_float({"x": 1.5}, "x") == 1.5
    assert _safe_float({"x": "1.5"}, "x") == 1.5
    assert _safe_float({"x": None}, "x") is None
    assert _safe_float({"x": "garbage"}, "x") is None
    assert _safe_float({}, "x") is None
    assert _safe_float(None, "x") is None  # type: ignore[arg-type]


def test_latest_picks_max_date():
    records = [
        {"date": "2022-12-31", "v": 1},
        {"date": "2024-12-31", "v": 3},
        {"date": "2023-12-31", "v": 2},
    ]
    assert _latest(records)["v"] == 3  # type: ignore[index]


def test_latest_returns_none_for_empty():
    assert _latest([]) is None
    assert _latest([None, "garbage"]) is None  # type: ignore[list-item]


# ── End-to-end pillar scoring with injected fake lookup ────────────────


class _FakeLookup:
    """Stand-in for SectorBenchmarkLookup. Returns the fixed medians given
    at construction, regardless of which metrics / period_type are asked.
    """

    def __init__(self, medians: Dict[str, float]):
        # Wrap each in the {period_label: value} shape get_sector_benchmarks returns
        self._data = {m: {"2024": v} for m, v in medians.items()}

    def get_sector_benchmarks(
        self, sector: str, metrics: List[str], period_type: str,
    ) -> Dict[str, Dict[str, float]]:
        return {m: dict(self._data.get(m, {})) for m in metrics}


def _make_service_with_fake_medians(medians: Dict[str, float]) -> MoatScoringService:
    svc = MoatScoringService()
    svc._lookup = _FakeLookup(medians)  # type: ignore[assignment]
    return svc


@dataclass
class _FakeIndustryTAM:
    hhi: Optional[float] = None
    lifecycle_phase: Optional[str] = None


def test_brand_power_above_median_scores_high():
    """Gross margin and P/S both 2× sector median → 7.5 + 7.5 = 7.5 avg."""
    svc = _make_service_with_fake_medians({
        "gross_margin": 40.0,   # sector median 40%
        "ps_ratio": 4.0,        # sector median P/S 4x
    })
    results = svc.score(
        sector="Technology", industry="Software - Infrastructure",
        profile={"sector": "Technology"},
        income=[],
        balance=[],
        ratios=[{
            "date": "2024-12-31",
            "grossProfitMargin": 0.80,    # 80%, 2× sector
            "priceToSalesRatio": 8.0,     # 8x, 2× sector
        }],
        industry_tam=None,
    )
    brand = results[PILLAR_BRAND]
    assert brand.confidence == "medium"
    assert brand.score == 7.5


def test_cost_advantage_with_three_metrics_resolves_high_confidence():
    svc = _make_service_with_fake_medians({
        "operating_margin": 15.0,
        "asset_turnover": 0.8,
        "sga_to_revenue": 25.0,
    })
    results = svc.score(
        sector="Technology", industry="Software - Infrastructure",
        profile={"sector": "Technology"},
        income=[{
            "date": "2024-12-31",
            "revenue": 100_000_000,
            "sellingGeneralAndAdministrativeExpenses": 12_500_000,  # 12.5% — better than median
        }],
        balance=[],
        ratios=[{
            "date": "2024-12-31",
            "operatingProfitMargin": 0.30,    # 30%, 2× sector
            "assetTurnover": 1.6,             # 2× sector
        }],
        industry_tam=None,
    )
    cost = results[PILLAR_COST]
    assert cost.confidence == "high"
    # Op margin 30 vs 15 → 7.5; asset turnover 1.6 vs 0.8 → 7.5;
    # SGA 12.5 vs 25 (lower=better, half the median) → 7.5.
    # Average = 7.5.
    assert cost.score == 7.5


def test_switching_costs_single_metric_returns_low_confidence_none():
    """Phase 3A v1 only has deferred_revenue_to_revenue for switching costs.
    With just 1 metric, confidence is low and score is None — caller
    falls back to legacy AI dimension.
    """
    svc = _make_service_with_fake_medians({
        "deferred_revenue_to_revenue": 10.0,
    })
    results = svc.score(
        sector="Technology", industry="Software - Infrastructure",
        profile={},
        income=[{"date": "2024-12-31", "revenue": 100}],
        balance=[{"date": "2024-12-31", "deferredRevenue": 20}],
        ratios=[],
        industry_tam=None,
    )
    sc = results[PILLAR_SWITCHING]
    assert sc.score is None
    assert sc.confidence == "low"


def test_network_effects_uses_industry_tam_hhi_and_lifecycle():
    svc = _make_service_with_fake_medians({
        "revenue_yoy": 5.0,   # sector grows 5%
    })
    results = svc.score(
        sector="Technology", industry="Software - Infrastructure",
        profile={},
        income=[
            {"date": "2024-12-31", "revenue": 110},
            {"date": "2023-12-31", "revenue": 100},
        ],
        balance=[],
        ratios=[],
        industry_tam=_FakeIndustryTAM(hhi=3500, lifecycle_phase="secular_growth"),
    )
    nw = results[PILLAR_NETWORK]
    # HHI 3500 → 7.0; lifecycle "secular_growth" → 7.0;
    # revenue_yoy 10% vs sector 5% → +5 / 2 = +2.5 → 7.5.
    # Average of (7.0, 7.0, 7.5) ≈ 7.17 → round to 7.2
    assert nw.confidence == "high"
    assert nw.score == 7.2


def test_intangible_assets_two_metrics_medium_confidence():
    svc = _make_service_with_fake_medians({
        "rd_to_revenue": 5.0,
        "intangibles_to_assets": 10.0,
    })
    results = svc.score(
        sector="Healthcare", industry="Biotechnology",
        profile={},
        income=[{
            "date": "2024-12-31",
            "revenue": 100,
            "researchAndDevelopmentExpenses": 20,   # 20% — 4× sector
        }],
        balance=[{
            "date": "2024-12-31",
            "totalAssets": 1000,
            "goodwill": 100,
            "intangibleAssets": 100,                # 20% — 2× sector
        }],
        ratios=[],
        industry_tam=None,
    )
    iassets = results[PILLAR_INTANGIBLE]
    assert iassets.confidence == "medium"
    # R&D 20% vs sector 5% → ratio 4 = log2 → +5 → cap at 10
    # Intangibles 20% vs 10% → 7.5
    # Average = (10 + 7.5) / 2 = 8.75 → round 8.8
    assert iassets.score == 8.8


def test_all_pillars_returned_in_canonical_order():
    """Order matters for downstream merging with AI fallback dimensions."""
    svc = _make_service_with_fake_medians({})
    results = svc.score(
        sector=None, industry=None, profile={},
        income=[], balance=[], ratios=[], industry_tam=None,
    )
    assert list(results.keys()) == PILLAR_ORDER


def test_missing_sector_returns_all_pillars_with_low_confidence():
    """When sector is missing the lookup short-circuits → no medians →
    all pillars except those using industry_tam land at confidence='low'.
    """
    svc = MoatScoringService()
    results = svc.score(
        sector=None, industry=None, profile={},
        income=[], balance=[], ratios=[], industry_tam=None,
    )
    for pillar in PILLAR_ORDER:
        assert pillar in results
        # With no data at all every pillar should be low / None
        assert results[pillar].confidence == "low"
        assert results[pillar].score is None


def test_pillar_result_serializes_to_dict():
    """to_dict() output matches the iOS-decoded MoatDimensionResponse shape."""
    result = PillarResult(
        name=PILLAR_BRAND, score=7.5, peer_score=5.0,
        drivers=[MetricDriver(metric="gross_margin", focal=80.0,
                              sector_median=40.0, sub_score=7.5)],
        confidence="medium",
    )
    out = result.to_dict()
    assert out["name"] == PILLAR_BRAND
    assert out["score"] == 7.5
    assert out["peer_score"] == 5.0
    assert out["confidence"] == "medium"
    assert out["drivers"][0]["metric"] == "gross_margin"
    assert out["drivers"][0]["focal"] == 80.0
    assert out["drivers"][0]["sector_median"] == 40.0
    assert out["drivers"][0]["sub_score"] == 7.5
