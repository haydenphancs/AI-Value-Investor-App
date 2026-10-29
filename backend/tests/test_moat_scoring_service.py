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


# ── _focal_revenue_yoy (paired like the producer) ───────────────────────


def test_focal_revenue_yoy_basic_and_decline():
    from app.services.moat_scoring_service import _focal_revenue_yoy
    rise = [{"date": "2024-12-31", "revenue": 110_000_000},
            {"date": "2023-12-31", "revenue": 100_000_000}]
    fall = [{"date": "2024-12-31", "revenue": 80_000_000},
            {"date": "2023-12-31", "revenue": 100_000_000}]
    assert _focal_revenue_yoy(rise, "2024") == (10.0, True)
    assert _focal_revenue_yoy(fall, "2024") == (-20.0, True)


def test_focal_revenue_yoy_returns_none_when_insufficient_data():
    from app.services.moat_scoring_service import _focal_revenue_yoy
    assert _focal_revenue_yoy([], "2024") == (None, False)
    assert _focal_revenue_yoy([{"date": "2024-12-31", "revenue": 100}], "2024") == (None, False)
    assert _focal_revenue_yoy([{"date": "2024-12-31", "revenue": 100}], "") == (None, False)
    # Prior is zero → undefined
    assert _focal_revenue_yoy([
        {"date": "2024-12-31", "revenue": 100_000_000},
        {"date": "2023-12-31", "revenue": 0},
    ], "2024") == (None, False)


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
    one row per metric at period="2024" with n=50 (well above the
    mature sample floor). Use `_FakeLookupMultiYear` when you need to
    test year-selection edge cases.

    ⚠️ "2024" because every focal record in these tests ends 2024-12-31: since
    2026-10-07 a focal value is compared with the median of ITS OWN year (or an older
    mature one), never a newer year. This fake used "2025", which only ever scored
    because the old picker took the newest year whatever the company's own year.
    """

    def __init__(self, medians: Dict[str, float]):
        # Single year, healthy sample size — covers the common test path.
        self._data = {
            m: {"2024": {"median": v, "n": 50}} for m, v in medians.items()
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
    source_grain: Optional[str] = None


def test_brand_power_above_median_scores_high():
    """Gross margin and P/S both 2× sector median → 7.5 + 7.5 = 7.5 avg."""
    svc = _make_service_with_fake_medians({
        # ⚠️ DECIMAL, not a percentage. `sector_benchmarks` stores gross_margin /
        # operating_margin as 0-1 (`type: "direct"` straight from FMP's ratios, no
        # x100) — production medians read 0.34-0.40 for Consumer Cyclical. These
        # fakes previously used 40.0 / 15.0, which is how the 100x scale bug in
        # `_gross_margin_pct` survived: the tests validated the code against
        # fabricated medians on a scale the table never uses, and both drivers
        # saturated at 10.0/10 against real data.
        # sga_to_revenue and ps_ratio are NOT on this scale and are unchanged.
        "gross_margin": 0.40,   # sector median 40%, stored as a decimal
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
        # ⚠️ DECIMAL, not a percentage. `sector_benchmarks` stores gross_margin /
        # operating_margin as 0-1 (`type: "direct"` straight from FMP's ratios, no
        # x100) — production medians read 0.34-0.40 for Consumer Cyclical. These
        # fakes previously used 40.0 / 15.0, which is how the 100x scale bug in
        # `_gross_margin_pct` survived: the tests validated the code against
        # fabricated medians on a scale the table never uses, and both drivers
        # saturated at 10.0/10 against real data.
        # sga_to_revenue and ps_ratio are NOT on this scale and are unchanged.
        "operating_margin": 0.15,
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
            {"date": "2024-12-31", "revenue": 110_000_000},
            {"date": "2023-12-31", "revenue": 100_000_000},
        ],
        balance=[],
        ratios=[],
        industry_tam=_FakeIndustryTAM(hhi=3500, lifecycle_phase="secular_growth"),
    )
    nw = results[PILLAR_NETWORK]
    # HHI 3500 → 7.0; lifecycle "secular_growth" → 7.0;
    # revenue_yoy 10% vs sector 5% → +5 / 5 (2026-10-28 slope) = +1.0 → 6.0.
    # Average of (7.0, 7.0, 6.0) ≈ 6.67 → round to 6.7
    assert nw.confidence == "high"
    assert nw.score == 6.7


def _network_sub_scores(industry_tam: Any) -> Dict[str, Optional[float]]:
    svc = _make_service_with_fake_medians({})
    results = svc.score(
        sector="Industrials", industry="Aerospace & Defense",
        profile={}, income=[], balance=[], ratios=[],
        industry_tam=industry_tam,
    )
    return {d.metric: d.sub_score for d in results[PILLAR_NETWORK].drivers}


@pytest.mark.parametrize(
    "grain,phase,expected",
    [
        # A broad stand-in carries its SECTOR's phase, which the card hides: mature.
        ("sector", "declining", 5.0),
        ("all_industry", "declining", 5.0),
        ("sector", "emerging", 5.0),
        # The industry's own phase is scored as it stands.
        ("industry", "declining", 3.0),
        (None, "declining", 3.0),           # a plain IndustryTAM (Census / industry FRED)
        ("industry", "secular_growth", 7.0),
        # No phase stays no reading, whatever the grain — never promoted to mature.
        ("sector", None, None),
    ],
)
def test_broad_grain_lifecycle_scores_as_mature(grain, phase, expected):
    subs = _network_sub_scores(
        _FakeIndustryTAM(hhi=867, lifecycle_phase=phase, source_grain=grain),
    )
    assert subs["lifecycle_phase"] == expected


def test_broad_grain_gate_reads_an_object_without_a_grain_attribute():
    """A plain `IndustryTAM` has no `source_grain` at all; it is industry-level."""

    class _NoGrain:
        hhi = None
        lifecycle_phase = "declining"

    assert _network_sub_scores(_NoGrain())["lifecycle_phase"] == 3.0


def test_revenue_hhi_867_scores_2_5():
    """Aerospace & Defense's revenue-share HHI (measured 2026-10-22) is 867: fragmented,
    2.5 — the market-cap HHI would have read SpaceX's $1.9T of $3.5T as concentrated."""
    assert _hhi_to_score(867) == 2.5
    subs = _network_sub_scores(_FakeIndustryTAM(hhi=867, lifecycle_phase="mature"))
    assert subs["industry_hhi"] == 2.5
    assert subs["lifecycle_phase"] == 5.0


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
    Technology deferred_revenue_to_revenue scenario from production.

    The focal record is the company's FY2026 (ends 2026-06-30) so 2026 is its own
    year: the thin same-year cell is skipped for the newest OLDER mature year. (With a
    2024 focal record, as before 2026-10-07, neither year may be used any more.)"""
    svc = _make_service_with_multi_year({
        "gross_margin": {
            "2025": {"median": 0.40, "n": 85},   # preferred — n high enough
            "2026": {"median": 0.99, "n": 12},  # noisy partial year — skip (decimal)
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
            "date": "2026-06-30",
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


def test_year_selection_never_uses_a_thin_year():
    """A year with fewer than MATURE_SAMPLE_FLOOR (20) companies is never used, even
    when nothing better exists: the metric goes unscored instead.

    Flipped 2026-10-07 (was `..._falls_back_to_n_gte_10_when_no_preferred`, which
    accepted n >= 10 "for some noise"). The lookup's own single-value picker
    (`pick_mature_benchmark`) stopped returning thin cells the same day — a 10-15 company
    median is not a sector benchmark, and the pillar falls to the AI dimension instead
    of being scored against one."""
    svc = _make_service_with_multi_year({
        "gross_margin": {
            "2024": {"median": 0.40, "n": 15},   # acceptable fallback
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
    assert brand.score is None and brand.confidence == "low"
    drivers = {d.metric: d for d in brand.drivers}
    assert drivers["gross_margin"].sector_median is None
    assert drivers["gross_margin"].period_used is None


def test_year_selection_returns_none_when_all_below_acceptable():
    """If every year is below the mature floor, the metric is skipped — sector median
    is too unstable to score against."""
    svc = _make_service_with_multi_year({
        "gross_margin": {
            "2025": {"median": 0.40, "n": 6},
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
    """When multiple years clear the mature floor, take the latest one that is not
    newer than the company's own year — here the focal FY2025, so 2025 itself."""
    svc = _make_service_with_multi_year({
        "gross_margin": {
            # Decimal scale, like the rest of this file — this test only asserts WHICH
            # YEAR is chosen, so the values just need to be distinguishable, but keeping
            # the scale consistent stops a future reader copying the wrong one.
            "2023": {"median": 0.30, "n": 70},
            "2024": {"median": 0.35, "n": 80},
            "2025": {"median": 0.40, "n": 85},   # latest with n>=20 → pick this
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
            "date": "2025-12-31",             # FY2025 — compared with 2025 (2026-10-07)
            "grossProfitMargin": 0.80,
            "priceToSalesRatio": 8.0,
        }],
        industry_tam=None,
    )
    brand = results[PILLAR_BRAND]
    drivers = {d.metric: d for d in brand.drivers}
    assert drivers["gross_margin"].period_used == "2025"
    assert drivers["gross_margin"].sector_median == 0.40


def test_pillar_drivers_to_dict_includes_period_and_sample_size():
    """Audit output exposes period_used + sample_size so we can debug
    'why is the median so weird' without re-running the query."""
    svc = _make_service_with_multi_year({
        # decimal scale — see the note above
        "gross_margin": {"2025": {"median": 0.40, "n": 85}},
        "ps_ratio": {"2025": {"median": 4.0, "n": 85}},
    })
    results = svc.score(
        sector="Technology", industry="Software",
        profile={"sector": "Technology"},
        income=[], balance=[],
        ratios=[{
            "date": "2025-12-31",             # FY2025, the year of the fake's rows
            "grossProfitMargin": 0.50,
            "priceToSalesRatio": 5.0,
        }],
        industry_tam=None,
    )
    brand_dict = results[PILLAR_BRAND].to_dict()
    gm_driver = next(d for d in brand_dict["drivers"] if d["metric"] == "gross_margin")
    assert gm_driver["period_used"] == "2025"
    assert gm_driver["sample_size"] == 85


# ── 2026-10-28 owner decisions (JOBY audit): outliers score None, never 10 ──
#
# JOBY's report read Network Effects 6.3 ("Primary Driver") off 39,183% growth from a $136K
# prior year, and Brand Power 5.0 off a 204× P/S with a −30% gross margin. Each case below
# asserts the DEGRADED output, plus a twin that still scores.

from app.services.moat_scoring_service import (  # noqa: E402
    SCORER_VERSION,
    _focal_revenue_yoy,
    report_moat_model_is_current,
)
from app.services.transcript_signals_service import nrr_to_sub_score  # noqa: E402


def _drivers(pillar: PillarResult) -> Dict[str, MetricDriver]:
    return {d.metric: d for d in pillar.drivers}


def _score_network(income: List[Dict[str, Any]], *, median: float = 8.94,
                   hhi: float = 1040.77, phase: str = "mature") -> PillarResult:
    svc = _make_service_with_fake_medians({"revenue_yoy": median})
    return svc.score(
        sector="Industrials", industry="Airlines, Airports & Air Services",
        profile={}, income=income, balance=[], ratios=[],
        industry_tam=_FakeIndustryTAM(hhi=hhi, lifecycle_phase=phase),
    )[PILLAR_NETWORK]


def test_joby_growth_off_a_near_zero_base_is_shown_but_not_scored():
    nw = _score_network([
        {"date": "2024-12-31", "revenue": 53_425_000},
        {"date": "2023-12-31", "revenue": 136_000},
    ])
    growth = _drivers(nw)["revenue_yoy"]
    assert growth.focal == 39183.09          # the figure is still on the driver
    assert growth.sub_score is None          # ...but never pins the pillar at 10
    # HHI + lifecycle are industry constants: no company input → the AI fallback.
    assert nw.score is None and nw.confidence == "low"


def test_growth_from_a_prior_year_just_under_10m_is_not_scored():
    nw = _score_network([
        {"date": "2024-12-31", "revenue": 12_000_000},
        {"date": "2023-12-31", "revenue": 9_999_999},
    ])
    assert _drivers(nw)["revenue_yoy"].sub_score is None
    assert nw.score is None


def test_growth_above_the_producers_clamp_is_not_scored():
    # A $50M prior year passes the floor, but 600% is past WINSORIZE_CEIL (500%), the
    # bound every peer value in the median was clamped to.
    nw = _score_network([
        {"date": "2024-12-31", "revenue": 350_000_000},
        {"date": "2023-12-31", "revenue": 50_000_000},
    ])
    assert _drivers(nw)["revenue_yoy"].focal == 600.0
    assert _drivers(nw)["revenue_yoy"].sub_score is None


def test_a_40pct_grower_still_scores_on_the_gentler_slope():
    nw = _score_network([
        {"date": "2024-12-31", "revenue": 140_000_000},
        {"date": "2023-12-31", "revenue": 100_000_000},
    ])
    growth = _drivers(nw)["revenue_yoy"]
    # 5 + (40 − 8.94) / 5 = 11.2 → capped 10.0; one notch lower stays on the scale.
    assert growth.sub_score == 10.0
    nw2 = _score_network([
        {"date": "2024-12-31", "revenue": 118_940_000},
        {"date": "2023-12-31", "revenue": 100_000_000},
    ])
    # 18.94% vs 8.94 → +10pp → 5 + 10/5 = 7.0 (it read 10.0 at the old 2pp slope).
    assert _drivers(nw2)["revenue_yoy"].sub_score == 7.0
    # HHI 1040.77 → 4.0, mature → 5.0, growth 7.0 → 5.33 → 5.3, high.
    assert nw2.score == 5.3 and nw2.confidence == "high"


def test_growth_is_paired_like_the_sector_median():
    # A repeated latest year is one year (the old date-sorted pairing read it as 0%).
    dup = _score_network([
        {"date": "2024-12-31", "revenue": 120_000_000},
        {"date": "2024-12-31", "revenue": 120_000_000},
        {"date": "2023-12-31", "revenue": 100_000_000},
    ])
    assert _drivers(dup)["revenue_yoy"].focal == 20.0
    # A missing year is no one-year growth figure (the old pairing read 2022→2024 as YoY).
    gap = _score_network([
        {"date": "2024-12-31", "revenue": 150_000_000},
        {"date": "2022-12-31", "revenue": 100_000_000},
    ])
    assert _drivers(gap)["revenue_yoy"].focal is None
    assert gap.score is None


def test_focal_revenue_yoy_refuses_a_transition_stub():
    # Dec-2022 FY, a 6-month stub to Jun-2023, then Jun-2024: the stub is never paired.
    income = [
        {"date": "2024-06-30", "revenue": 220_000_000},
        {"date": "2023-06-30", "revenue": 60_000_000},
        {"date": "2022-12-31", "revenue": 200_000_000},
    ]
    yoy, scorable = _focal_revenue_yoy(income, "2024")
    assert yoy is None and scorable is False


def test_focal_revenue_yoy_refuses_negative_current_revenue():
    yoy, scorable = _focal_revenue_yoy(
        [{"date": "2024-12-31", "revenue": -5_000_000},
         {"date": "2023-12-31", "revenue": 100_000_000}], "2024")
    assert yoy == -105.0 and scorable is False


def test_network_effects_with_a_platform_user_count_is_measured():
    """The transcript's user count is a company input too (latent while transcripts are
    unlicensed): HHI + lifecycle + users is measured even with no growth figure."""
    from types import SimpleNamespace
    svc = _make_service_with_fake_medians({})
    pillar = svc._score_network_effects(
        income=[], industry_tam=_FakeIndustryTAM(hhi=1040.77, lifecycle_phase="mature"),
        medians={}, transcript_sig=SimpleNamespace(user_count=100_000_000, nrr_pct=None),
    )
    assert pillar.score is not None and pillar.confidence == "high"


def _score_brand(ratios: Dict[str, Any]) -> PillarResult:
    svc = _make_service_with_fake_medians({"gross_margin": 0.3099, "ps_ratio": 2.4685})
    return svc.score(
        sector="Industrials", industry="Airlines, Airports & Air Services",
        profile={}, income=[], balance=[],
        ratios=[{"date": "2024-12-31", **ratios}],
    )[PILLAR_BRAND]


def test_joby_ps_with_a_negative_gross_margin_is_not_brand_evidence():
    brand = _score_brand({"grossProfitMargin": -0.3006, "priceToSalesRatio": 204.14,
                          "revenuePerShare": 0.0647})
    ps = _drivers(brand)["ps_ratio"]
    assert ps.focal == 204.14 and ps.sub_score is None
    assert _drivers(brand)["gross_margin"].sub_score == 0.0
    # One driver left → the AI fallback, not mean(0.0, 10.0) = 5.0.
    assert brand.score is None


def test_ps_with_a_zero_gross_margin_is_not_brand_evidence():
    brand = _score_brand({"grossProfitMargin": 0.0, "priceToSalesRatio": 6.0,
                          "revenuePerShare": 3.0})
    assert _drivers(brand)["ps_ratio"].sub_score is None
    assert brand.score is None


def test_ps_with_a_positive_gross_margin_still_scores():
    brand = _score_brand({"grossProfitMargin": 0.45, "priceToSalesRatio": 6.0,
                          "revenuePerShare": 3.0})
    assert _drivers(brand)["ps_ratio"].sub_score is not None
    assert brand.score is not None and brand.confidence == "medium"


def _score_intangible(income, balance, ip_intel, medians) -> PillarResult:
    svc = _make_service_with_fake_medians(medians)
    return svc.score(
        sector="Industrials", industry="Airlines, Airports & Air Services",
        profile={}, income=income, balance=balance, ratios=[], ip_intel=ip_intel,
    )[PILLAR_INTANGIBLE]


def test_joby_patents_plus_one_sector_input_is_not_measured():
    # Industrials' R&D median is 0.0 (zero-fills) → the R&D driver is None; the patent
    # input (4.0) used to be the second driver that made the pillar "measured" at 2.0.
    pillar = _score_intangible(
        income=[{"date": "2024-12-31", "revenue": 53_425_000,
                 "researchAndDevelopmentExpenses": 581_101_000}],
        balance=[{"date": "2024-12-31", "totalAssets": 1_795_069_000,
                  "goodwillAndIntangibleAssets": 108_281_000}],
        ip_intel={"patents_per_employee": 0.0059, "patents_recent_5y": 15},
        medians={"rd_to_revenue": 0.0, "intangibles_to_assets": 33.12},
    )
    assert _drivers(pillar)["patents_per_employee"].sub_score == 4.0
    assert pillar.score is None and pillar.confidence == "low"


def test_rd_plus_intangibles_plus_fda_is_measured():
    pillar = _score_intangible(
        income=[{"date": "2024-12-31", "revenue": 1_000_000_000,
                 "researchAndDevelopmentExpenses": 200_000_000}],
        balance=[{"date": "2024-12-31", "totalAssets": 2_000_000_000,
                  "goodwillAndIntangibleAssets": 500_000_000}],
        ip_intel={"fda_active_approvals": 12},
        medians={"rd_to_revenue": 15.0, "intangibles_to_assets": 20.0},
    )
    assert pillar.score is not None and pillar.confidence == "high"


def test_a_nan_input_is_no_reading():
    assert _safe_float({"x": float("nan")}, "x") is None
    assert _safe_float({"x": float("inf")}, "x") is None
    assert _safe_float({"x": True}, "x") is None
    brand = _score_brand({"grossProfitMargin": float("nan"), "priceToSalesRatio": 6.0,
                          "revenuePerShare": 3.0})
    assert _drivers(brand)["gross_margin"].focal is None
    assert brand.score is None


def test_an_exact_zero_sga_with_revenue_is_no_reading():
    """FMP zero-fills an unreported SG&A line; as a lower-is-better driver it scored 10.0."""
    svc = _make_service_with_fake_medians({"sga_to_revenue": 18.0})
    cost = svc.score(
        sector="Industrials", industry="X", profile={},
        income=[{"date": "2024-12-31", "revenue": 500_000_000,
                 "sellingGeneralAndAdministrativeExpenses": 0}],
        balance=[], ratios=[],
    )[PILLAR_COST]
    assert _drivers(cost)["sga_to_revenue"].focal is None
    assert _drivers(cost)["sga_to_revenue"].sub_score is None


@pytest.mark.parametrize("nrr,expected", [
    (129.9, 10.0), (125.0, 10.0), (72.0, 0.0), (75.0, 0.0), (100.0, 5.0), (110.0, 7.0),
])
def test_nrr_sub_score_stays_on_the_0_to_10_scale(nrr, expected):
    assert nrr_to_sub_score(nrr) == expected


def test_nrr_sub_score_refuses_a_non_finite_input():
    assert nrr_to_sub_score(float("nan")) is None


def test_report_moat_model_gate():
    stamped = {"moat_competition": {"model_version": SCORER_VERSION}}
    assert report_moat_model_is_current(stamped) is True
    assert report_moat_model_is_current({"moat_competition": {}}) is False   # pre-stamp
    assert report_moat_model_is_current(
        {"moat_competition": {"model_version": "moat_v1.2026-10-22"}}) is False
    assert report_moat_model_is_current({}) is True                          # no moat block
    assert report_moat_model_is_current(None) is True


def test_peer_averages_carry_the_scorer_vintage():
    from app.services import industry_moat_benchmark_service as imb
    assert imb.MODEL_VERSION == SCORER_VERSION
    assert imb._MODEL_VERSION_ORDER[-1] == SCORER_VERSION
    assert imb._vintage_rank("moat_v1.2026-10-22") < imb._vintage_rank(SCORER_VERSION)


@pytest.mark.parametrize("prior,current,scorable", [
    (10_000_000, 12_000_000, True),       # the floor is inclusive
    (50_000_000, 300_000_000, True),      # exactly the producer's 500% clamp
    (50_000_000, 0, False),               # current revenue 0: a zero-fill, no reading
])
def test_growth_scoring_boundaries(prior, current, scorable):
    yoy, ok = _focal_revenue_yoy(
        [{"date": "2024-12-31", "revenue": current},
         {"date": "2023-12-31", "revenue": prior}], "2024")
    assert ok is scorable


def test_revenue_falling_to_zero_never_makes_network_effects_measured():
    nw = _score_network([
        {"date": "2024-12-31", "revenue": 0},
        {"date": "2023-12-31", "revenue": 50_000_000},
    ])
    assert _drivers(nw)["revenue_yoy"].sub_score is None
    assert nw.score is None


def test_a_non_finite_expense_line_is_no_reading_at_pillar_level():
    svc = _make_service_with_fake_medians(
        {"sga_to_revenue": 18.0, "rd_to_revenue": 15.0, "intangibles_to_assets": 20.0})
    res = svc.score(
        sector="Technology", industry="X", profile={},
        income=[{"date": "2024-12-31", "revenue": 500_000_000,
                 "sellingGeneralAndAdministrativeExpenses": float("nan"),
                 "researchAndDevelopmentExpenses": float("inf")}],
        balance=[], ratios=[],
    )
    sga = _drivers(res[PILLAR_COST])["sga_to_revenue"]
    rd = _drivers(res[PILLAR_INTANGIBLE])["rd_to_revenue"]
    assert sga.focal is None and sga.sub_score is None
    assert rd.focal is None and rd.sub_score is None


@pytest.mark.parametrize("currency,prior,scorable", [
    ("JPY", 12_000_000, False),          # ¥12M ≈ $80K: a near-zero base
    ("JPY", 3_000_000_000, True),        # ¥3B ≈ $20M
    ("TWD", 200_000_000, False),         # NT$200M ≈ $6.3M
    ("TWD", 400_000_000, True),          # NT$400M ≈ $12.5M
    (" usd ", 12_000_000, True),         # normalized like every currency reader
    (None, 12_000_000, True),            # no field: read as USD (money_prefix convention)
    ("XYZ", 3_000_000_000, False),       # a currency with no floor rate: no reading
])
def test_the_growth_floor_is_in_dollars_whatever_the_reporting_currency(currency, prior, scorable):
    rows = [{"date": "2024-12-31", "revenue": prior * 2},
            {"date": "2023-12-31", "revenue": prior}]
    if currency is not None:
        for r in rows:
            r["reportedCurrency"] = currency
    yoy, ok = _focal_revenue_yoy(rows, "2024")
    assert yoy == 100.0 and ok is scorable
