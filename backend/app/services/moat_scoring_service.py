"""Moat scoring — Phase 3A deterministic foundation.

Replaces the Gemini Stage A `moat_competition.dimensions[*].score` (an
ungrounded LLM judgment) with sector-relative percentile-based scoring
grounded in real FMP financials + the existing `sector_benchmarks` long-
format median table + the `industry_dossier` HHI / lifecycle data.

Per-pillar scoring formula:
    score = mean(score_from_median_ratio(metric_i)) over metrics that resolved
    confidence = high if ≥3 metrics resolved, medium if 2, low if <2.

When confidence is low for a pillar, this service returns None for that
pillar — the caller falls back to the legacy AI Stage A dimension for
that single pillar (and, post sub-phase 3D, falls back to Gemini
GROUNDED research with web citations instead).

Each score comes with a `drivers` array listing the exact metrics, focal
values, sector medians, and per-metric sub-scores — so the user can see
exactly why a pillar scored what it did. No fabrication.

Architecture note: this service makes NO upstream FMP calls. Per-ticker
data (income/balance/ratios/profile) and per-industry data (HHI,
lifecycle) are already fetched by the ticker-report collector's pass-1
and pass-2 loops. The only external call is the `sector_benchmark_lookup`
read (1-hour in-memory cached), which hits Supabase.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.sector_benchmark_lookup import get_sector_benchmark_lookup

logger = logging.getLogger(__name__)


# ── Pillar names — must match the iOS radar chart labels ───────────────

PILLAR_SWITCHING = "Switching Costs"
PILLAR_NETWORK = "Network Effects"
PILLAR_BRAND = "Brand Power"
PILLAR_COST = "Cost Advantage"
PILLAR_INTANGIBLE = "Intangible Assets"

PILLAR_ORDER: List[str] = [
    PILLAR_SWITCHING,
    PILLAR_NETWORK,
    PILLAR_BRAND,
    PILLAR_COST,
    PILLAR_INTANGIBLE,
]


# ── Configuration ──────────────────────────────────────────────────────

# Below this # of resolved metrics, return None for the pillar so the
# caller falls back to Gemini grounded (or legacy AI) instead.
_MIN_METRICS_FOR_SCORE = 2

# Confidence buckets — used by the caller to decide fallback policy.
_CONFIDENCE_HIGH = "high"      # 3+ metrics resolved
_CONFIDENCE_MEDIUM = "medium"  # 2 metrics resolved
_CONFIDENCE_LOW = "low"        # <2 — service returns None instead

# Sample-size thresholds for year selection. The recompute pipeline
# already drops sectors below MIN_SAMPLE_SIZE=5 at compute time, so all
# stored rows have n>=5. Within the stored set we still prefer fuller
# samples to avoid partial-year noise (e.g., FY2026 with n=12 for
# Technology vs. FY2025 with n=85). Walks years latest→oldest:
#   - first year with n >= _N_PREFERRED → use it (high statistical confidence)
#   - else first year with n >= _N_ACCEPTABLE → use it (fallback)
#   - else return None for this metric
_N_PREFERRED = 20
_N_ACCEPTABLE = 10


# ── Helpers ────────────────────────────────────────────────────────────


def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    if not isinstance(record, dict):
        return None
    val = record.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _latest(records: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the latest record by `date` field. Returns None if empty."""
    if not records:
        return None
    valid = [r for r in records if isinstance(r, dict)]
    if not valid:
        return None
    return max(valid, key=lambda r: r.get("date") or "")


def _score_from_median_ratio(
    focal: Optional[float],
    median: Optional[float],
    *,
    higher_is_better: bool = True,
) -> Optional[float]:
    """Map a (focal, sector-median) pair to a 0-10 score.

    Geometric scale anchored at the median:
      focal == median  → 5.0
      focal == 2×median → 7.5 (one doubling above)
      focal == 4×median → 10.0 (two doublings above; capped)
      focal == 0.5×median → 2.5
      focal == 0.25×median → 0.0 (floored)

    For "lower is better" metrics (e.g. SG&A/Revenue), the ratio is
    flipped so focal < median scores high.

    Returns None when either input is missing or the median is non-positive
    (the geometric formula is undefined). Special cases:
      - focal <= 0 AND higher_is_better → 0.0 (worst)
      - focal <= 0 AND lower_is_better → 10.0 (best — e.g., zero SG&A)
    """
    if focal is None or median is None:
        return None
    if median <= 0:
        return None  # Can't anchor without a positive reference
    if focal <= 0:
        return 0.0 if higher_is_better else 10.0
    ratio = focal / median
    if not higher_is_better:
        ratio = 1.0 / ratio
    score = 5.0 + 2.5 * math.log2(ratio)
    return max(0.0, min(10.0, round(score, 2)))


def _absolute_delta_score(
    focal: Optional[float],
    median: Optional[float],
    *,
    delta_per_point: float = 2.0,
) -> Optional[float]:
    """Score by absolute delta from sector median, useful for metrics that
    legitimately go negative (e.g., YoY growth %).

      score = 5 + clamp((focal - median) / delta_per_point, -5, 5)

    delta_per_point=2.0 means a 2-percentage-point premium above sector
    median adds 1.0 to the score; 10-point premium reaches the cap (10.0).
    """
    if focal is None or median is None:
        return None
    delta = focal - median
    return max(0.0, min(10.0, round(5.0 + delta / delta_per_point, 2)))


def _hhi_to_score(hhi: Optional[float]) -> Optional[float]:
    """DOJ HHI bands mapped to network-effect strength."""
    if hhi is None or hhi < 0:
        return None
    if hhi < 1000:
        return 2.5  # fragmented
    if hhi < 1500:
        return 4.0  # moderately fragmented
    if hhi < 2500:
        return 5.5  # moderately concentrated
    if hhi < 5000:
        return 7.0  # highly concentrated
    return 8.5      # monopoly-adjacent


def _lifecycle_to_score(phase: Optional[str]) -> Optional[float]:
    """Industry lifecycle → network-effect maturity."""
    return {
        "emerging": 7.5,         # network is forming, compounding fast
        "secular_growth": 7.0,   # network is well-established and growing
        "mature": 5.0,           # network is stable
        "declining": 3.0,        # network is eroding
    }.get(phase)


def _pick_year_by_sample_size(
    year_to_payload: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Walk years latest → oldest and return the first one whose
    sample_size clears the preferred threshold. If none clear preferred,
    fall back to the latest one that clears the acceptable threshold.
    Returns None when nothing meets the acceptable floor.

    Returned shape: {"median": float, "period": str, "n": int}.
    """
    if not year_to_payload:
        return None
    # Sort years descending. Year labels are strings like "2025"; rely
    # on numeric sort where possible, fall back to lexical for safety.
    def _year_key(label: str) -> int:
        try:
            return int(label)
        except (TypeError, ValueError):
            return -1
    years_desc = sorted(year_to_payload.keys(), key=_year_key, reverse=True)

    # Pass 1: preferred threshold.
    for year in years_desc:
        payload = year_to_payload.get(year) or {}
        median = payload.get("median")
        n = payload.get("n") or 0
        if median is None or not isinstance(n, (int, float)):
            continue
        if n >= _N_PREFERRED:
            return {"median": float(median), "period": year, "n": int(n)}

    # Pass 2: acceptable fallback.
    for year in years_desc:
        payload = year_to_payload.get(year) or {}
        median = payload.get("median")
        n = payload.get("n") or 0
        if median is None or not isinstance(n, (int, float)):
            continue
        if n >= _N_ACCEPTABLE:
            return {"median": float(median), "period": year, "n": int(n)}

    return None


def _compute_yoy_pct(
    records: List[Dict[str, Any]], field_name: str,
) -> Optional[float]:
    """YoY % growth from a sorted-by-date records list. Returns None
    when fewer than 2 valid datapoints OR prior is zero.
    """
    if not records:
        return None
    sorted_recs = sorted(
        [r for r in records if isinstance(r, dict)],
        key=lambda r: r.get("date") or "",
    )
    if len(sorted_recs) < 2:
        return None
    current = _safe_float(sorted_recs[-1], field_name)
    prior = _safe_float(sorted_recs[-2], field_name)
    if current is None or prior is None or prior == 0:
        return None
    return round((current - prior) / abs(prior) * 100, 2)


# ── Driver + result types ──────────────────────────────────────────────


@dataclass
class MetricDriver:
    metric: str
    focal: Optional[float]
    sector_median: Optional[float]
    sub_score: Optional[float]   # 0-10 contribution; None if didn't resolve
    period_used: Optional[str] = None       # e.g. "2025" — which year's median we selected
    sample_size: Optional[int] = None       # n at that period; helps explain partial-year skips

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric": self.metric,
            "focal": self.focal,
            "sector_median": self.sector_median,
            "sub_score": self.sub_score,
            "period_used": self.period_used,
            "sample_size": self.sample_size,
        }


@dataclass
class PillarResult:
    name: str
    score: Optional[float]
    peer_score: float = 5.0  # sector median by definition (50th percentile anchor)
    drivers: List[MetricDriver] = field(default_factory=list)
    confidence: str = _CONFIDENCE_LOW

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "score": self.score,
            "peer_score": self.peer_score,
            "drivers": [d.to_dict() for d in self.drivers],
            "confidence": self.confidence,
        }


# ── Service ────────────────────────────────────────────────────────────


class MoatScoringService:

    def __init__(self) -> None:
        self._lookup = get_sector_benchmark_lookup()

    def score(
        self,
        *,
        sector: Optional[str],
        industry: Optional[str],
        profile: Dict[str, Any],
        income: List[Dict[str, Any]],
        balance: List[Dict[str, Any]],
        ratios: List[Dict[str, Any]],
        industry_tam: Optional[Any] = None,   # IndustryDossier or None
    ) -> Dict[str, PillarResult]:
        """Score all five pillars. Returns a dict keyed by pillar name.

        Each PillarResult has either a valid score + drivers + confidence
        ("high"/"medium"), or score=None + confidence="low" indicating
        the caller should fall back.

        No upstream FMP calls. All inputs are already-fetched.
        """
        # Latest period records (annual). For percentile rank, we compare
        # latest focal value vs latest sector median.
        latest_inc = _latest(income)
        latest_bs = _latest(balance)
        latest_ratios = _latest(ratios)

        # Resolve sector medians for every metric in one Supabase call.
        # `_fetch_sector_medians` returns a dict of
        # {metric → (median, period_used, sample_size) | None} using the
        # tiered year-selection rule (prefer n>=20, fall back to n>=10,
        # else None).
        sector_medians = self._fetch_sector_medians(
            sector=sector,
            metrics=[
                "gross_margin", "operating_margin", "ps_ratio",
                "asset_turnover", "revenue_yoy",
                "rd_to_revenue", "sga_to_revenue",
                "intangibles_to_assets", "deferred_revenue_to_revenue",
            ],
        )

        results: Dict[str, PillarResult] = {}
        results[PILLAR_SWITCHING] = self._score_switching_costs(
            latest_inc, latest_bs, sector_medians,
        )
        results[PILLAR_NETWORK] = self._score_network_effects(
            income, industry_tam, sector_medians,
        )
        results[PILLAR_BRAND] = self._score_brand_power(
            latest_ratios, sector_medians,
        )
        results[PILLAR_COST] = self._score_cost_advantage(
            latest_inc, latest_ratios, sector_medians,
        )
        results[PILLAR_INTANGIBLE] = self._score_intangible_assets(
            latest_inc, latest_bs, sector_medians,
        )
        return results

    # ── Sector benchmark lookup ──────────────────────────────────────

    def _fetch_sector_medians(
        self, sector: Optional[str], metrics: List[str],
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """For each metric, pick the latest annual median that has
        adequate sample size and return it along with its period label
        and n.

        Year-selection rule (walks years latest → oldest):
            1. First year with n >= _N_PREFERRED (20) → use.
            2. Else first year with n >= _N_ACCEPTABLE (10) → use as fallback.
            3. Else None — metric won't resolve in scoring.

        Returns a dict {metric: {"median": float, "period": str, "n": int}
        | None}. None means "skip this metric in scoring."
        """
        out: Dict[str, Optional[Dict[str, Any]]] = {m: None for m in metrics}
        if not sector:
            return out
        try:
            benchmarks = self._lookup.get_sector_benchmarks_with_n(
                sector, metrics, period_type="annual",
            )
        except Exception as exc:
            logger.warning(
                "moat_scoring: sector benchmark lookup failed for %s: %s",
                sector, exc,
            )
            return out
        for metric in metrics:
            year_to_payload = benchmarks.get(metric) or {}
            if not year_to_payload:
                continue
            out[metric] = _pick_year_by_sample_size(year_to_payload)
        return out

    # ── Per-pillar scorers ───────────────────────────────────────────

    def _score_switching_costs(
        self,
        latest_inc: Optional[Dict[str, Any]],
        latest_bs: Optional[Dict[str, Any]],
        medians: Dict[str, Optional[Dict[str, Any]]],
    ) -> PillarResult:
        """Switching Costs proxy: deferred revenue / revenue (subscription
        stickiness). v1 has only one strong deterministic signal — most
        tickers will land at low confidence and fall back to Gemini
        grounded research (or legacy AI) until Phase 3B adds NDR/NRR
        extraction from earnings transcripts.
        """
        drivers: List[MetricDriver] = []

        focal_def = self._deferred_rev_pct(latest_bs, latest_inc)
        drivers.append(_build_higher_better_driver(
            "deferred_revenue_to_revenue", focal_def,
            medians.get("deferred_revenue_to_revenue"),
        ))

        return _assemble_pillar(PILLAR_SWITCHING, drivers)

    def _score_network_effects(
        self,
        income: List[Dict[str, Any]],
        industry_tam: Optional[Any],
        medians: Dict[str, Optional[Dict[str, Any]]],
    ) -> PillarResult:
        """Network Effects: HHI band + lifecycle phase + revenue growth
        premium vs sector median. Three inputs → high confidence common.
        """
        drivers: List[MetricDriver] = []

        # HHI position (industry-level concentration) — no sector
        # median; the score is mapped directly from the DOJ bands.
        hhi = getattr(industry_tam, "hhi", None) if industry_tam else None
        drivers.append(MetricDriver(
            metric="industry_hhi",
            focal=hhi, sector_median=None,
            sub_score=_hhi_to_score(hhi),
        ))

        # Lifecycle phase — enum mapping, no median.
        phase = getattr(industry_tam, "lifecycle_phase", None) if industry_tam else None
        drivers.append(MetricDriver(
            metric="lifecycle_phase",
            focal=None, sector_median=None,
            sub_score=_lifecycle_to_score(phase),
        ))

        # Revenue growth premium (absolute delta to sector median)
        focal_rev_yoy = _compute_yoy_pct(income, "revenue")
        payload = medians.get("revenue_yoy")
        median_rev_yoy, period, n = _unpack_median(payload)
        drivers.append(MetricDriver(
            metric="revenue_yoy",
            focal=focal_rev_yoy, sector_median=median_rev_yoy,
            sub_score=_absolute_delta_score(
                focal_rev_yoy, median_rev_yoy, delta_per_point=2.0,
            ),
            period_used=period, sample_size=n,
        ))

        return _assemble_pillar(PILLAR_NETWORK, drivers)

    def _score_brand_power(
        self,
        latest_ratios: Optional[Dict[str, Any]],
        medians: Dict[str, Optional[Dict[str, Any]]],
    ) -> PillarResult:
        """Brand Power: gross margin percentile + P/S percentile (the
        market pays for brand). Both higher = better.
        """
        drivers = [
            _build_higher_better_driver(
                "gross_margin",
                self._gross_margin_pct(latest_ratios),
                medians.get("gross_margin"),
            ),
            _build_higher_better_driver(
                "ps_ratio",
                _safe_float(latest_ratios or {}, "priceToSalesRatio"),
                medians.get("ps_ratio"),
            ),
        ]
        return _assemble_pillar(PILLAR_BRAND, drivers)

    def _score_cost_advantage(
        self,
        latest_inc: Optional[Dict[str, Any]],
        latest_ratios: Optional[Dict[str, Any]],
        medians: Dict[str, Optional[Dict[str, Any]]],
    ) -> PillarResult:
        """Cost Advantage: operating margin (higher) + asset turnover
        (higher) + SG&A/Revenue (lower is better).
        """
        drivers = [
            _build_higher_better_driver(
                "operating_margin",
                self._operating_margin_pct(latest_ratios),
                medians.get("operating_margin"),
            ),
            _build_higher_better_driver(
                "asset_turnover",
                _safe_float(latest_ratios or {}, "assetTurnover"),
                medians.get("asset_turnover"),
            ),
            _build_lower_better_driver(
                "sga_to_revenue",
                self._sga_to_revenue_pct(latest_inc),
                medians.get("sga_to_revenue"),
            ),
        ]
        return _assemble_pillar(PILLAR_COST, drivers)

    def _score_intangible_assets(
        self,
        latest_inc: Optional[Dict[str, Any]],
        latest_bs: Optional[Dict[str, Any]],
        medians: Dict[str, Optional[Dict[str, Any]]],
    ) -> PillarResult:
        """Intangible Assets: R&D intensity + on-balance-sheet intangibles
        share of total assets. Tier 3 (3C) will add USPTO patent count
        + FDA approvals.
        """
        drivers = [
            _build_higher_better_driver(
                "rd_to_revenue",
                self._rd_to_revenue_pct(latest_inc),
                medians.get("rd_to_revenue"),
            ),
            _build_higher_better_driver(
                "intangibles_to_assets",
                self._intangibles_to_assets_pct(latest_bs),
                medians.get("intangibles_to_assets"),
            ),
        ]
        return _assemble_pillar(PILLAR_INTANGIBLE, drivers)

    # ── Focal-value extractors ───────────────────────────────────────

    def _gross_margin_pct(self, ratios: Optional[Dict[str, Any]]) -> Optional[float]:
        """Gross margin as percentage. FMP's grossProfitMargin is 0-1
        scale; multiply to match sector_benchmarks scale (percentage).
        """
        v = _safe_float(ratios or {}, "grossProfitMargin")
        if v is None:
            return None
        # FMP convention: ratios are 0-1 (e.g., 0.40 = 40%). sector_benchmarks
        # stores them in the same 0-1 scale, so no scaling needed.
        return v * 100.0

    def _operating_margin_pct(self, ratios: Optional[Dict[str, Any]]) -> Optional[float]:
        v = _safe_float(ratios or {}, "operatingProfitMargin")
        if v is None:
            return None
        return v * 100.0

    def _rd_to_revenue_pct(self, inc: Optional[Dict[str, Any]]) -> Optional[float]:
        if not inc:
            return None
        rev = _safe_float(inc, "revenue")
        rd = _safe_float(inc, "researchAndDevelopmentExpenses")
        if not rev or rev <= 0 or rd is None:
            return None
        return (rd / rev) * 100.0

    def _sga_to_revenue_pct(self, inc: Optional[Dict[str, Any]]) -> Optional[float]:
        if not inc:
            return None
        rev = _safe_float(inc, "revenue")
        sga = _safe_float(inc, "sellingGeneralAndAdministrativeExpenses")
        if not rev or rev <= 0 or sga is None:
            return None
        return (sga / rev) * 100.0

    def _intangibles_to_assets_pct(
        self, bs: Optional[Dict[str, Any]],
    ) -> Optional[float]:
        if not bs:
            return None
        assets = _safe_float(bs, "totalAssets")
        if not assets or assets <= 0:
            return None
        combined = _safe_float(bs, "goodwillAndIntangibleAssets")
        if combined is not None and combined > 0:
            total_intang = combined
        else:
            goodwill = _safe_float(bs, "goodwill") or 0.0
            intangibles = _safe_float(bs, "intangibleAssets") or 0.0
            total_intang = goodwill + intangibles
        return (total_intang / assets) * 100.0

    def _deferred_rev_pct(
        self,
        bs: Optional[Dict[str, Any]],
        inc: Optional[Dict[str, Any]],
    ) -> Optional[float]:
        if not bs or not inc:
            return None
        rev = _safe_float(inc, "revenue")
        if not rev or rev <= 0:
            return None
        deferred = _safe_float(bs, "deferredRevenue")
        if deferred is None:
            cur = _safe_float(bs, "deferredRevenueCurrent") or 0.0
            non = _safe_float(bs, "deferredRevenueNonCurrent") or 0.0
            if cur == 0 and non == 0:
                return None
            deferred = cur + non
        return (deferred / rev) * 100.0


# ── Pillar assembly ────────────────────────────────────────────────────


def _unpack_median(
    payload: Optional[Dict[str, Any]],
) -> tuple[Optional[float], Optional[str], Optional[int]]:
    """Pull median / period / sample_size out of the lookup payload.
    Returns (None, None, None) when the payload is missing (no year
    passed the sample-size gate).
    """
    if not payload:
        return None, None, None
    median = payload.get("median")
    period = payload.get("period")
    n = payload.get("n")
    if not isinstance(median, (int, float)):
        return None, None, None
    return float(median), period, (int(n) if isinstance(n, (int, float)) else None)


def _build_higher_better_driver(
    metric: str,
    focal: Optional[float],
    median_payload: Optional[Dict[str, Any]],
) -> MetricDriver:
    """Build a MetricDriver for a 'higher is better' metric with full
    period/sample-size attribution."""
    median, period, n = _unpack_median(median_payload)
    return MetricDriver(
        metric=metric,
        focal=focal,
        sector_median=median,
        sub_score=_score_from_median_ratio(focal, median, higher_is_better=True),
        period_used=period,
        sample_size=n,
    )


def _build_lower_better_driver(
    metric: str,
    focal: Optional[float],
    median_payload: Optional[Dict[str, Any]],
) -> MetricDriver:
    """Build a MetricDriver for a 'lower is better' metric (SG&A/Rev)."""
    median, period, n = _unpack_median(median_payload)
    return MetricDriver(
        metric=metric,
        focal=focal,
        sector_median=median,
        sub_score=_score_from_median_ratio(focal, median, higher_is_better=False),
        period_used=period,
        sample_size=n,
    )


def _assemble_pillar(
    name: str, drivers: List[MetricDriver],
) -> PillarResult:
    """Aggregate driver sub-scores into a pillar score + confidence."""
    valid_subs = [d.sub_score for d in drivers if d.sub_score is not None]
    if len(valid_subs) < _MIN_METRICS_FOR_SCORE:
        return PillarResult(
            name=name, score=None,
            drivers=drivers, confidence=_CONFIDENCE_LOW,
        )
    avg = sum(valid_subs) / len(valid_subs)
    confidence = _CONFIDENCE_HIGH if len(valid_subs) >= 3 else _CONFIDENCE_MEDIUM
    return PillarResult(
        name=name, score=round(avg, 1),
        drivers=drivers, confidence=confidence,
    )


# ── Singleton + module-level helper ────────────────────────────────────


_service_singleton: Optional[MoatScoringService] = None


def get_moat_scoring_service() -> MoatScoringService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = MoatScoringService()
    return _service_singleton


def score_moat_dimensions(
    *,
    sector: Optional[str],
    industry: Optional[str],
    profile: Dict[str, Any],
    income: List[Dict[str, Any]],
    balance: List[Dict[str, Any]],
    ratios: List[Dict[str, Any]],
    industry_tam: Optional[Any] = None,
) -> Dict[str, PillarResult]:
    """Module-level convenience for the data collector."""
    return get_moat_scoring_service().score(
        sector=sector, industry=industry, profile=profile,
        income=income, balance=balance, ratios=ratios,
        industry_tam=industry_tam,
    )
