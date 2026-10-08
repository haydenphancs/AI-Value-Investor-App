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
that single pillar. (Sub-phase 3D put a Gemini GROUNDED-research tier in
between, cached 100 days in `moat_intel_cache`; it was retired 2026-10-02
with Google Search grounding, whose terms forbid that cache and its
cross-user display — tests/test_no_google_search_grounding.py.)

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
from typing import Any, Dict, List, Optional, Tuple

from app.services.sector_benchmark_lookup import (
    MATURE_SAMPLE_FLOOR,
    get_sector_benchmark_lookup,
    lookup_failed,
)
from app.utils.period_labels import annual_benchmark_key

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

# Year selection (2026-10-07). A focal value is compared with the sector median of the
# SAME year: the company's latest annual record, keyed with the benchmark join key
# (`period_labels.annual_benchmark_key`), against the stored row of that year. When that
# year has no servable mature median, the newest OLDER year with one is used — never a
# newer year (a company whose latest filing is FY2024 was scored against FY2025 medians),
# and never a cell from fewer than MATURE_SAMPLE_FLOOR (20) companies. The old rule took
# the newest year with n >= 20, else n >= 10, whatever the company's own year.
# Incomplete years never arrive here: the lookup hides an annual row computed less than
# 75 days after its year ended (`sector_benchmark_lookup.servable_benchmark_rows`).


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


def _year_of(label: Any) -> Optional[int]:
    """A 4-digit annual label ("2025") as an int; None for anything else."""
    text = label.strip() if isinstance(label, str) else ""
    return int(text) if len(text) == 4 and text.isdigit() else None


def _finite_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pick_year_at_or_before(
    year_to_payload: Dict[str, Dict[str, Any]],
    target_year: Any,
    floor: int = MATURE_SAMPLE_FLOOR,
) -> Optional[Dict[str, Any]]:
    """The sector median a focal value from `target_year` is compared with: that year's
    when it holds at least `floor` companies, else the newest OLDER year that does.
    Never a newer year, never a thinner cell. None when `target_year` is not a year
    (the company's record has no usable date) or no year qualifies.

    Returned shape: {"median": float, "period": str, "n": int}.
    """
    target = _year_of(target_year)
    if target is None or not isinstance(year_to_payload, dict):
        return None
    candidates = sorted(
        (
            (year, label)
            for label in year_to_payload
            if (year := _year_of(label)) is not None and year <= target
        ),
        reverse=True,
    )
    for _year, label in candidates:
        payload = year_to_payload.get(label)
        if not isinstance(payload, dict):
            continue
        median = _finite_number(payload.get("median"))
        n = _finite_number(payload.get("n"))
        if median is None or n is None:
            continue
        if n >= floor:
            return {"median": median, "period": label, "n": int(n)}
    return None


def _older_key(*keys: str) -> str:
    """The oldest of the usable year keys ("" when none is usable) — a ratio built from
    two statements is compared with no year newer than either of them."""
    years = [k for k in keys if _year_of(k) is not None]
    return min(years, key=int) if years else ""


def _canonical_sector(sector: Any) -> str:
    """The canonical sector name the benchmark table is keyed by (the same
    `_normalize_sector` the snapshot cards and the drill-down use). A GICS-style spelling
    ("Information Technology", "Financials", "Health Care") read no rows when passed raw,
    so every median-anchored driver silently went unscored."""
    raw = sector.strip() if isinstance(sector, str) else ""
    if not raw:
        return ""
    # Local import: sector_benchmark_service pulls in the FMP client at import time.
    from app.services.sector_benchmark_service import _normalize_sector

    return (_normalize_sector(raw) or "").strip()


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


class MoatScores(dict):
    """``{pillar name: PillarResult}`` from `MoatScoringService.score`.

    ``lookup_failed`` is True when the sector-median read FAILED (a Supabase error or a
    raising lookup — not "this sector has no rows"). The median-anchored drivers then
    resolved without a median, so pillars fall to the AI dimension for a reason that is
    an outage, not the company's shape: the report collector records it on the report's
    degraded sections so the result is delivered to its caller but never shared-cached.
    `sector_benchmark_lookup.lookup_failed(result)` reads the flag, as for every lookup."""

    lookup_failed = False


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
        transcript: Optional[str] = None,     # Phase 3B — earnings-call text for NRR / user-count extraction
        ip_intel: Optional[Dict[str, Any]] = None,  # Phase 3C — USPTO patents + FDA approvals
    ) -> MoatScores:
        """Score all five pillars. Returns a `MoatScores` dict keyed by pillar name
        (its ``lookup_failed`` flag says whether the sector-median read failed).

        Each PillarResult has either a valid score + drivers + confidence
        ("high"/"medium"), or score=None + confidence="low" indicating
        the caller should fall back.

        No upstream FMP calls. All inputs are already-fetched.
        """
        # Latest period records (annual). Each focal value is compared with the sector
        # median of ITS record's year (see `_pick_year_at_or_before`).
        latest_inc = _latest(income)
        latest_bs = _latest(balance)
        latest_ratios = _latest(ratios)

        inc_year = annual_benchmark_key(latest_inc) if latest_inc else ""
        bs_year = annual_benchmark_key(latest_bs) if latest_bs else ""
        ratios_year = annual_benchmark_key(latest_ratios) if latest_ratios else ""
        # metric → the benchmark year of the record its focal value is read from.
        target_years: Dict[str, str] = {
            "gross_margin": ratios_year,
            "operating_margin": ratios_year,
            "ps_ratio": ratios_year,
            "asset_turnover": ratios_year,
            # `_compute_yoy_pct` reads the newest income record — the same as `_latest`.
            "revenue_yoy": inc_year,
            "rd_to_revenue": inc_year,
            "sga_to_revenue": inc_year,
            "intangibles_to_assets": bs_year,
            # balance-sheet deferred revenue ÷ income-statement revenue
            "deferred_revenue_to_revenue": _older_key(bs_year, inc_year),
        }

        # Resolve sector medians for every metric in one Supabase call.
        sector_medians, medians_failed = self._fetch_sector_medians(
            sector=sector,
            metrics=list(target_years),
            target_years=target_years,
        )

        # Phase 3B — extract NRR + user-count from the earnings transcript
        # once and reuse across pillars. Pure regex, no LLM cost.
        transcript_sig = None
        if transcript:
            try:
                from app.services.transcript_signals_service import (
                    extract_signals,
                )
                transcript_sig = extract_signals(transcript)
            except Exception as exc:
                logger.warning(
                    "moat_scoring: transcript signal extraction failed: %s", exc,
                )

        results = MoatScores()
        if medians_failed:
            results.lookup_failed = True
        results[PILLAR_SWITCHING] = self._score_switching_costs(
            latest_inc, latest_bs, sector_medians, transcript_sig,
        )
        results[PILLAR_NETWORK] = self._score_network_effects(
            income, industry_tam, sector_medians, transcript_sig,
        )
        results[PILLAR_BRAND] = self._score_brand_power(
            latest_ratios, sector_medians,
        )
        results[PILLAR_COST] = self._score_cost_advantage(
            latest_inc, latest_ratios, sector_medians,
        )
        results[PILLAR_INTANGIBLE] = self._score_intangible_assets(
            latest_inc, latest_bs, sector_medians, ip_intel,
        )
        return results

    # ── Sector benchmark lookup ──────────────────────────────────────

    def _fetch_sector_medians(
        self,
        sector: Optional[str],
        metrics: List[str],
        target_years: Dict[str, str],
    ) -> Tuple[Dict[str, Optional[Dict[str, Any]]], bool]:
        """For each metric, the sector median its focal value is compared with
        (`_pick_year_at_or_before` on ``target_years[metric]``: the same year, else the
        newest older year with a mature median), plus whether the read FAILED.

        Returns ``({metric: {"median": float, "period": str, "n": int} | None}, failed)``.
        None means "skip this metric in scoring". ``failed`` is True for a raising lookup
        or a `BenchmarkLookupFailed` answer — never for a sector with no rows.
        """
        out: Dict[str, Optional[Dict[str, Any]]] = {m: None for m in metrics}
        canonical = _canonical_sector(sector)
        if not canonical:
            return out, False
        try:
            benchmarks = self._lookup.get_sector_benchmarks_with_n(
                canonical, metrics, period_type="annual",
            )
        except Exception as exc:
            logger.warning(
                "moat_scoring: sector benchmark lookup raised for sector=%r (raw %r): "
                "%s: %s — every median-anchored driver is unscored; flagged so the "
                "report is not shared-cached",
                canonical, sector, type(exc).__name__, exc,
            )
            return out, True
        if not isinstance(benchmarks, dict):
            logger.warning(
                "moat_scoring: sector benchmark lookup for sector=%r answered %s, not a "
                "mapping — treated as a failed read", canonical, type(benchmarks).__name__,
            )
            return out, True
        failed = lookup_failed(benchmarks)
        if failed:
            logger.warning(
                "moat_scoring: sector benchmark read FAILED for sector=%r — scored with "
                "what loaded; flagged so the report is not shared-cached", canonical,
            )
        for metric in metrics:
            year_to_payload = benchmarks.get(metric) or {}
            if not isinstance(year_to_payload, dict) or not year_to_payload:
                continue
            out[metric] = _pick_year_at_or_before(
                year_to_payload, target_years.get(metric) or "",
            )
        return out, failed

    # ── Per-pillar scorers ───────────────────────────────────────────

    def _score_switching_costs(
        self,
        latest_inc: Optional[Dict[str, Any]],
        latest_bs: Optional[Dict[str, Any]],
        medians: Dict[str, Optional[Dict[str, Any]]],
        transcript_sig: Optional[Any] = None,
    ) -> PillarResult:
        """Switching Costs: deferred-revenue/revenue + (Phase 3B) NRR
        from earnings-transcript extraction. With NRR available, the
        pillar usually reaches medium/high confidence instead of falling
        through to grounded fallback.
        """
        drivers: List[MetricDriver] = []

        focal_def = self._deferred_rev_pct(latest_bs, latest_inc)
        drivers.append(_build_higher_better_driver(
            "deferred_revenue_to_revenue", focal_def,
            medians.get("deferred_revenue_to_revenue"),
        ))

        # Phase 3B — NRR from earnings transcript regex extraction.
        # No sector median for this one (the NRR scale itself is the
        # reference). Sub-score derived via the anchor formula in
        # transcript_signals_service.
        if transcript_sig is not None and transcript_sig.nrr_pct is not None:
            from app.services.transcript_signals_service import nrr_to_sub_score
            drivers.append(MetricDriver(
                metric="nrr_pct",
                focal=transcript_sig.nrr_pct,
                sector_median=None,
                sub_score=nrr_to_sub_score(transcript_sig.nrr_pct),
                period_used="earnings_transcript",
                sample_size=None,
            ))

        return _assemble_pillar(PILLAR_SWITCHING, drivers)

    def _score_network_effects(
        self,
        income: List[Dict[str, Any]],
        industry_tam: Optional[Any],
        medians: Dict[str, Optional[Dict[str, Any]]],
        transcript_sig: Optional[Any] = None,
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

        # Phase 3B — platform user count from earnings transcript
        # extraction. Maps log-scaled to a 0-10 score (10K → 0;
        # 100M → 7.5; 1B+ → 9-10). No sector median — the user-count
        # scale itself is the reference.
        if transcript_sig is not None and transcript_sig.user_count is not None:
            from app.services.transcript_signals_service import user_count_to_sub_score
            drivers.append(MetricDriver(
                metric="platform_user_count",
                focal=float(transcript_sig.user_count),
                sector_median=None,
                sub_score=user_count_to_sub_score(transcript_sig.user_count),
                period_used="earnings_transcript",
                sample_size=None,
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
                self._gross_margin_ratio(latest_ratios),
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
                self._operating_margin_ratio(latest_ratios),
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
        ip_intel: Optional[Dict[str, Any]] = None,
    ) -> PillarResult:
        """Intangible Assets: R&D intensity + on-balance-sheet intangibles
        share of total assets + (Phase 3C) USPTO patents + FDA approvals.
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

        # Phase 3C — patents per employee + FDA active approvals.
        if isinstance(ip_intel, dict):
            from app.services.ip_intel_service import (
                fda_approvals_to_sub_score,
                patents_per_employee_to_sub_score,
            )
            patents_pe = ip_intel.get("patents_per_employee")
            patents_score = patents_per_employee_to_sub_score(patents_pe)
            if patents_score is not None:
                drivers.append(MetricDriver(
                    metric="patents_per_employee",
                    focal=patents_pe,
                    sector_median=None,
                    sub_score=patents_score,
                    period_used="uspto_recent_5y",
                    sample_size=ip_intel.get("patents_recent_5y"),
                ))
            fda_active = ip_intel.get("fda_active_approvals")
            fda_score = fda_approvals_to_sub_score(fda_active)
            if fda_score is not None:
                drivers.append(MetricDriver(
                    metric="fda_active_approvals",
                    focal=(float(fda_active) if isinstance(fda_active, (int, float)) else None),
                    sector_median=None,
                    sub_score=fda_score,
                    period_used="openfda_current",
                    sample_size=None,
                ))

        return _assemble_pillar(PILLAR_INTANGIBLE, drivers)

    # ── Focal-value extractors ───────────────────────────────────────

    def _gross_margin_ratio(self, ratios: Optional[Dict[str, Any]]) -> Optional[float]:
        """Gross margin on the SAME 0-1 scale `sector_benchmarks` stores.

        ⚠️ Renamed from `_gross_margin_pct`, and the `* 100.0` removed, because the two
        were in direct contradiction: the docstring said "multiply to match
        sector_benchmarks scale (percentage)" while the comment one line below said
        "sector_benchmarks stores them in the same 0-1 scale, so no scaling needed" — and
        the code multiplied.

        The stored data settles it. `gross_margin` medians read from production are
        0.3419-0.3991 for Consumer Cyclical and top out at 1.082 (Insurance - Life), i.e.
        DECIMALS. Feeding a percentage against those made the ratio ~100x too large and
        the driver saturated: a 6%-margin distributor and a 46%-margin firm BOTH scored
        10.0/10, so `gross_margin` contributed nothing to Brand Power for any company
        whose margin exceeded ~2% of revenue — essentially all of them.

        Not every "…to revenue" benchmark is a decimal, which is why this was easy to get
        wrong: `rd_to_revenue` and `sga_to_revenue` really are stored as PERCENTAGES
        (Biotechnology's R&D median is 200.0 — pre-revenue biotechs genuinely spend 200%
        of revenue on R&D), and their helpers correctly multiply. Check the stored medians
        before changing any of these.
        """
        v = _safe_float(ratios or {}, "grossProfitMargin")
        if v is None:
            return None
        return v

    def _operating_margin_ratio(self, ratios: Optional[Dict[str, Any]]) -> Optional[float]:
        """Operating margin on the stored 0-1 scale — see `_gross_margin_ratio`.

        `operating_margin` medians read from production are 0.0319-0.0821 for Consumer
        Cyclical, so a percentage here saturated this driver exactly as it did above.
        """
        v = _safe_float(ratios or {}, "operatingProfitMargin")
        if v is None:
            return None
        return v

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
    transcript: Optional[str] = None,
    ip_intel: Optional[Dict[str, Any]] = None,
) -> MoatScores:
    """Module-level convenience for the data collector (``lookup_failed(result)`` tells
    a failed sector-median read from a sector with no rows)."""
    return get_moat_scoring_service().score(
        sector=sector, industry=industry, profile=profile,
        income=income, balance=balance, ratios=ratios,
        industry_tam=industry_tam, transcript=transcript,
        ip_intel=ip_intel,
    )
