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
    """Stand-in for SectorBenchmarkLookup. Supports both the legacy
    `get_sector_benchmarks` shape and the sample-size-aware
    `get_sector_benchmarks_with_n` shape Phase 3A uses.

    Constructed from a simple {metric: median_value} dict; defaults to
    one row per metric at period="2025" with n=50 (well above the
    preferred sample-size threshold). Use `_FakeLookupMultiYear` when
    you need to test year-selection edge cases.
    """

    def __init__(self, medians: Dict[str, float]):
        # Single year, healthy sample size — covers the common test path.
        self._data = {
            m: {"2025": {"median": v, "n": 50}} for m, v in medians.items()
        }

    def get_sector_benchmarks(
        self, sector: str, metrics: List[str], period_type: str,
    ) -> Dict[str, Dict[str, float]]:
        return {
            m: {p: payload["median"] for p, payload in (self._data.get(m) or {}).items()}
            for m in metrics
        }

    def get_sector_benchmarks_with_n(
        self, sector: str, metrics: List[str], period_type: str,
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        return {m: dict(self._data.get(m, {})) for m in metrics}


class _FakeLookupMultiYear:
    """Same protocol as _FakeLookup but lets each metric have multiple
    years with explicit (median, n) per year — useful for testing the
    tiered year-selection logic.
    """

    def __init__(self, data: Dict[str, Dict[str, Dict[str, Any]]]):
        # Expected shape:
        #   {"rd_to_revenue": {"2025": {"median": 5.0, "n": 85},
        #                       "2026": {"median": 27.0, "n": 12}}}
        self._data = data

    def get_sector_benchmarks_with_n(
        self, sector: str, metrics: List[str], period_type: str,
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        return {m: dict(self._data.get(m, {})) for m in metrics}


def _make_service_with_fake_medians(medians: Dict[str, float]) -> MoatScoringService:
    svc = MoatScoringService()
    svc._lookup = _FakeLookup(medians)  # type: ignore[assignment]
    return svc


def _make_service_with_multi_year(
    data: Dict[str, Dict[str, Dict[str, Any]]],
) -> MoatScoringService:
    svc = MoatScoringService()
    svc._lookup = _FakeLookupMultiYear(data)  # type: ignore[assignment]
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


# ── Tiered year selection (partial-year sample-size guard) ─────────────


def test_year_selection_prefers_year_with_n_gte_20():
    """When 2026 is partial (n=12) and 2025 is full (n=85), the scorer
    must pick 2025 to avoid noisy partial-year medians. This is the
    Technology deferred_revenue_to_revenue scenario from production."""
    svc = _make_service_with_multi_year({
        "gross_margin": {
            "2025": {"median": 40.0, "n": 85},   # preferred — n high enough
            "2026": {"median": 99.0, "n": 12},   # noisy partial year — skip
        },
        "ps_ratio": {
            "2025": {"median": 4.0, "n": 85},
            "2026": {"median": 50.0, "n": 12},
        },
    })
    results = svc.score(
        sector="Technology", industry="Software - Infrastructure",
        profile={"sector": "Technology"},
        income=[], balance=[],
        ratios=[{
            "date": "2024-12-31",
            "grossProfitMargin": 0.80,        # 80% (2× the real 2025 median)
            "priceToSalesRatio": 8.0,         # 2× the real 2025 median
        }],
        industry_tam=None,
    )
    brand = results[PILLAR_BRAND]
    # If the scorer had wrongly used 2026's median=99% for gross margin,
    # focal=80% would score BELOW median (~3.2). Picking 2025's 40% gives
    # the correct 2× ratio → 7.5.
    assert brand.score == 7.5
    # Drivers should record period_used="2025" and sample_size=85
    drivers = {d.metric: d for d in brand.drivers}
    assert drivers["gross_margin"].period_used == "2025"
    assert drivers["gross_margin"].sample_size == 85
    assert drivers["ps_ratio"].period_used == "2025"


def test_year_selection_falls_back_to_n_gte_10_when_no_preferred():
    """If no year has n>=20 but a year has n>=10, use that (accept some
    noise but better than dropping the metric entirely)."""
    svc = _make_service_with_multi_year({
        "gross_margin": {
            "2024": {"median": 40.0, "n": 15},   # acceptable fallback
            "2023": {"median": 38.0, "n": 6},    # too low — skip
        },
        "ps_ratio": {
            "2024": {"median": 4.0, "n": 15},
        },
    })
    results = svc.score(
        sector="Communication Services", industry="Telecom",
        profile={"sector": "Communication Services"},
        income=[], balance=[],
        ratios=[{
            "date": "2024-12-31",
            "grossProfitMargin": 0.80,
            "priceToSalesRatio": 8.0,
        }],
        industry_tam=None,
    )
    brand = results[PILLAR_BRAND]
    assert brand.score == 7.5  # 2024 median used
    drivers = {d.metric: d for d in brand.drivers}
    assert drivers["gross_margin"].period_used == "2024"
    assert drivers["gross_margin"].sample_size == 15


def test_year_selection_returns_none_when_all_below_acceptable():
    """If every year has n<10, the metric is skipped — sector median
    is too unstable to score against."""
    svc = _make_service_with_multi_year({
        "gross_margin": {
            "2025": {"median": 40.0, "n": 6},
            "2024": {"median": 38.0, "n": 5},
        },
        "ps_ratio": {
            "2025": {"median": 4.0, "n": 7},
        },
    })
    results = svc.score(
        sector="MicroSector", industry="Niche",
        profile={"sector": "MicroSector"},
        income=[], balance=[],
        ratios=[{
            "date": "2024-12-31",
            "grossProfitMargin": 0.80,
            "priceToSalesRatio": 8.0,
        }],
        industry_tam=None,
    )
    brand = results[PILLAR_BRAND]
    # Both metric drivers dropped (no usable sector median) → confidence low
    assert brand.confidence == "low"
    assert brand.score is None
    # Driver entries still present (for audit) but with no sector_median
    drivers = {d.metric: d for d in brand.drivers}
    assert drivers["gross_margin"].sector_median is None
    assert drivers["gross_margin"].sub_score is None
    assert drivers["gross_margin"].period_used is None


def test_year_selection_picks_latest_among_preferred():
    """When multiple years clear the preferred threshold, take the
    latest one (most recent fundamentals)."""
    svc = _make_service_with_multi_year({
        "gross_margin": {
            "2023": {"median": 30.0, "n": 70},
            "2024": {"median": 35.0, "n": 80},
            "2025": {"median": 40.0, "n": 85},   # latest with n>=20 → pick this
        },
        "ps_ratio": {
            "2025": {"median": 4.0, "n": 85},
        },
    })
    results = svc.score(
        sector="Technology", industry="Software",
        profile={"sector": "Technology"},
        income=[], balance=[],
        ratios=[{
            "date": "2024-12-31",
            "grossProfitMargin": 0.80,
            "priceToSalesRatio": 8.0,
        }],
        industry_tam=None,
    )
    brand = results[PILLAR_BRAND]
    drivers = {d.metric: d for d in brand.drivers}
    assert drivers["gross_margin"].period_used == "2025"
    assert drivers["gross_margin"].sector_median == 40.0


def test_pillar_drivers_to_dict_includes_period_and_sample_size():
    """Audit output exposes period_used + sample_size so we can debug
    'why is the median so weird' without re-running the query."""
    svc = _make_service_with_multi_year({
        "gross_margin": {"2025": {"median": 40.0, "n": 85}},
        "ps_ratio": {"2025": {"median": 4.0, "n": 85}},
    })
    results = svc.score(
        sector="Technology", industry="Software",
        profile={"sector": "Technology"},
        income=[], balance=[],
        ratios=[{
            "date": "2024-12-31",
            "grossProfitMargin": 0.50,
            "priceToSalesRatio": 5.0,
        }],
        industry_tam=None,
    )
    brand_dict = results[PILLAR_BRAND].to_dict()
    gm_driver = next(d for d in brand_dict["drivers"] if d["metric"] == "gross_margin")
    assert gm_driver["period_used"] == "2025"
    assert gm_driver["sample_size"] == 85


# ── Phase 3D: Gemini grounded fallback ────────────────────────────────


class _FakeGemini:
    """Stand-in for the Gemini client used by gemini_grounded_fallback.
    Returns whatever response dict is given at construction.
    """

    def __init__(self, response: Dict[str, Any]):
        self._response = response

    async def generate_grounded_research(self, **_: Any) -> Dict[str, Any]:
        return self._response


def _grounded_response(pillars: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Build a synthetic Gemini grounded-research response payload with
    a JSON code fence containing the given per-pillar scores.
    """
    pillars_json = ",\n    ".join(
        '"{name}": {{"score": {score}, "rationale": "{rationale}", "key_drivers": {drivers}}}'.format(
            name=name,
            score=p.get("score", 5.0),
            rationale=p.get("rationale", ""),
            drivers=str(p.get("drivers", [])).replace("'", '"'),
        )
        for name, p in pillars.items()
    )
    text = (
        "Some moat narrative for the company.\n\n"
        "```json\n{\n"
        f'  "pillars": {{\n    {pillars_json}\n  }},\n'
        '  "confidence": "high"\n'
        "}\n```\n"
    )
    return {
        "text": text,
        "tokens_used": 4321,
        "grounding_sources": [
            {"publisher": "reuters", "title": "reuters.com", "uri": "https://reuters.com/x"},
            {"publisher": "sec", "title": "sec.gov", "uri": "https://sec.gov/y"},
        ],
        "search_queries": ["oracle competitive moat 10-K"],
        "model": "gemini-2.5-flash",
    }


@pytest.mark.asyncio
async def test_grounded_extraction_returns_validated_scores():
    """Happy path: Gemini returns 5 valid pillar scores → all 5 land in
    the returned pillar_scores dict with status='applied' and source
    labels derived from grounding metadata.
    """
    from app.services.moat_scoring_service import PILLAR_ORDER
    svc = MoatScoringService()
    svc._gemini = _FakeGemini(_grounded_response({  # type: ignore[assignment]
        PILLAR_ORDER[0]: {"score": 8.5, "rationale": "10-K cites lock-in",
                          "drivers": ["high migration cost"]},
        PILLAR_ORDER[1]: {"score": 6.0, "rationale": "moderate network",
                          "drivers": ["growing user base"]},
        PILLAR_ORDER[2]: {"score": 7.0, "rationale": "premium gross margin",
                          "drivers": ["loyalty"]},
        PILLAR_ORDER[3]: {"score": 7.5, "rationale": "scale advantage",
                          "drivers": ["low unit cost"]},
        PILLAR_ORDER[4]: {"score": 9.0, "rationale": "patent portfolio",
                          "drivers": ["50+ active patents"]},
    }))

    result = await svc._do_grounded_extraction(
        ticker="ORCL",
        profile={"companyName": "Oracle Corporation", "sector": "Technology",
                 "industry": "Software - Infrastructure",
                 "description": "Enterprise software."},
    )
    assert result["status"] == "applied"
    assert set(result["pillar_scores"].keys()) == set(PILLAR_ORDER)
    # Each pillar score has the expected iOS-decoder shape.
    for pillar_name, payload in result["pillar_scores"].items():
        assert payload["name"] == pillar_name
        assert 0 <= payload["score"] <= 10
        assert payload["peer_score"] == 5.0
        assert payload["confidence"] == "grounded"
        assert payload["drivers"][0]["metric"] == "grounded_research"
        assert payload["drivers"][0]["rationale"]
    # Source labels derived from publisher field on grounding sources
    assert "Reuters" in result["source_labels"]
    assert "Sec" in result["source_labels"]
    assert result["rejected"] == []


@pytest.mark.asyncio
async def test_grounded_extraction_rejects_out_of_range_scores():
    """Gemini hallucinates a 15.0 score → rejected; other valid pillars
    kept; status='applied_with_rejections'.
    """
    from app.services.moat_scoring_service import PILLAR_ORDER
    svc = MoatScoringService()
    svc._gemini = _FakeGemini(_grounded_response({  # type: ignore[assignment]
        PILLAR_ORDER[0]: {"score": 15.0},      # out of range — rejected
        PILLAR_ORDER[1]: {"score": -2.0},      # negative — rejected
        PILLAR_ORDER[2]: {"score": 7.5},
        PILLAR_ORDER[3]: {"score": 6.0},
        PILLAR_ORDER[4]: {"score": 8.0},
    }))
    result = await svc._do_grounded_extraction(
        ticker="X", profile={"companyName": "X"},
    )
    assert result["status"] == "applied_with_rejections"
    assert PILLAR_ORDER[0] not in result["pillar_scores"]
    assert PILLAR_ORDER[1] not in result["pillar_scores"]
    assert PILLAR_ORDER[2] in result["pillar_scores"]
    # Rejected list captures both bad ones with the right reason prefix
    rejected_pillars = {r["pillar"]: r["reason"] for r in result["rejected"]}
    assert "score_out_of_range" in rejected_pillars[PILLAR_ORDER[0]]
    assert "score_out_of_range" in rejected_pillars[PILLAR_ORDER[1]]


@pytest.mark.asyncio
async def test_grounded_extraction_missing_pillar_key():
    """If Gemini drops a pillar entirely, it gets rejected with reason
    'missing_or_not_object' (not silently invented)."""
    from app.services.moat_scoring_service import PILLAR_ORDER
    svc = MoatScoringService()
    # Send only 3 of 5 pillars.
    svc._gemini = _FakeGemini(_grounded_response({  # type: ignore[assignment]
        PILLAR_ORDER[0]: {"score": 7.0},
        PILLAR_ORDER[2]: {"score": 6.0},
        PILLAR_ORDER[4]: {"score": 8.0},
    }))
    result = await svc._do_grounded_extraction(
        ticker="X", profile={"companyName": "X"},
    )
    assert result["status"] == "applied_with_rejections"
    rejected_pillars = {r["pillar"]: r["reason"] for r in result["rejected"]}
    assert rejected_pillars.get(PILLAR_ORDER[1]) == "missing_or_not_object"
    assert rejected_pillars.get(PILLAR_ORDER[3]) == "missing_or_not_object"


@pytest.mark.asyncio
async def test_grounded_extraction_no_json_fence():
    """Plain text without ```json``` block → status='gemini_error',
    no pillar scores returned, raw text preserved in audit payload.
    """
    svc = MoatScoringService()
    svc._gemini = _FakeGemini({  # type: ignore[assignment]
        "text": "Just a paragraph of moat narrative without a JSON block.",
        "tokens_used": 100, "grounding_sources": [],
        "search_queries": [], "model": "gemini-2.5-flash",
    })
    result = await svc._do_grounded_extraction(
        ticker="X", profile={"companyName": "X"},
    )
    assert result["status"] == "gemini_error"
    assert result["pillar_scores"] == {}
    assert "no ```json``` code fence" in result["raw_response"].get("error", "")


@pytest.mark.asyncio
async def test_grounded_extraction_malformed_json():
    """JSON code fence with bad JSON inside → gemini_error, raw_json
    preserved in audit payload."""
    svc = MoatScoringService()
    svc._gemini = _FakeGemini({  # type: ignore[assignment]
        "text": '```json\n{ "pillars": this is not valid json }\n```',
        "tokens_used": 100, "grounding_sources": [],
        "search_queries": [], "model": "gemini-2.5-flash",
    })
    result = await svc._do_grounded_extraction(
        ticker="X", profile={"companyName": "X"},
    )
    assert result["status"] == "gemini_error"
    assert "json parse" in result["raw_response"].get("error", "")


@pytest.mark.asyncio
async def test_grounded_extraction_gemini_throws():
    """Underlying gemini call raises → caught, status='gemini_error',
    no propagation up to caller."""
    class _Throwing:
        async def generate_grounded_research(self, **_):
            raise RuntimeError("quota exhausted")

    svc = MoatScoringService()
    svc._gemini = _Throwing()  # type: ignore[assignment]
    result = await svc._do_grounded_extraction(
        ticker="X", profile={"companyName": "X"},
    )
    assert result["status"] == "gemini_error"
    assert "quota exhausted" in result["raw_response"]["error"]
    assert result["pillar_scores"] == {}


def test_derive_source_labels_dedupe_and_cap():
    """Helper mirrors the competitor_intel publisher dedupe logic."""
    from app.services.moat_scoring_service import _derive_source_labels
    sources = [
        {"publisher": "reuters"},
        {"publisher": "reuters"},   # dup
        {"publisher": "sec"},
        {"publisher": "bloomberg"},
        {"publisher": "wsj"},
        {"publisher": "ft"},        # over the cap of 4
    ]
    labels = _derive_source_labels(sources)
    assert labels == ["Reuters", "Sec", "Bloomberg", "Wsj"]


def test_derive_source_labels_empty_and_non_dict():
    from app.services.moat_scoring_service import _derive_source_labels
    assert _derive_source_labels([]) == []
    assert _derive_source_labels([None, "garbage", {"publisher": ""}]) == []  # type: ignore[list-item]
