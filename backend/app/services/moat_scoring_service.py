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
from app.utils.margins import revenue_backed_margin, revenue_scaled_ratio
from app.utils.currency import currency_code
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

# The moat rating's max-pillar thresholds: wide at >= 8.5, narrow at >= 7.0, else none. ONE
# backend source for the report vital (`_derive_moat_vital`) and the Insight prompt's tone;
# iOS `MoatOverallRating.from(dimensions:)` holds the same pair
# (tests/test_ios_moat_rating_threshold_parity.py).
MOAT_RATING_WIDE = 8.5
MOAT_RATING_NARROW = 7.0


# ── Configuration ──────────────────────────────────────────────────────

# The scorer's vintage. The industry peer averages (`industry_moat_benchmark_service.
# MODEL_VERSION`) and the report's `moat_competition.model_version` stamp both use it, so a
# change to any pillar's math gets a new value here.
# 2026-10-28 (owner decisions after the JOBY audit): revenue growth needs a $10M prior year
# and is scored on a gentler slope; Network Effects needs a company input; P/S counts only
# with a positive gross margin; Intangible Assets needs both sector-relative inputs; an
# exact-zero SG&A and a non-finite input are no reading.
SCORER_VERSION = "moat_v1.2026-10-28"

# Below this # of resolved metrics, return None for the pillar so the
# caller falls back to the legacy AI dimension instead.
_MIN_METRICS_FOR_SCORE = 2

# Revenue growth is a Network Effects reading only off a material base. Off a near-zero year
# it measures the base, not the business: JOBY's $136K → $53.4M (the Blade acquisition) read
# 39,183% and pinned the driver at 10/10, making Network Effects its "primary moat" (owner
# decision 2026-10-28, "Floor + gentler curve"). Growth above the producer's own clamp
# (`sector_benchmark_service.WINSORIZE_CEIL`, 500%) is no reading either: every peer value in
# the median was capped there.
_GROWTH_PRIOR_REVENUE_FLOOR = 10_000_000.0
# The floor is in US DOLLARS, but statements are in the filer's reporting currency (TSM's rows
# are TWD): ¥12M is $80K. Units of each reporting currency per US dollar, ROUNDED — used ONLY
# for this materiality floor, an order-of-magnitude test a ±25% rate drift does not change;
# never for a displayed or valued figure. A row with no currency reads as USD (the
# `money_prefix` convention for an unknown currency); a currency missing here is no reading.
_FLOOR_UNITS_PER_USD: Dict[str, float] = {
    "USD": 1.0, "EUR": 0.9, "GBP": 0.8, "CHF": 0.9, "CAD": 1.4, "AUD": 1.5, "NZD": 1.7,
    "JPY": 150.0, "CNY": 7.2, "HKD": 7.8, "TWD": 32.0, "KRW": 1400.0, "INR": 85.0,
    "SGD": 1.35, "SEK": 10.5, "NOK": 10.5, "DKK": 6.8, "PLN": 4.0, "ILS": 3.7,
    "BRL": 5.5, "MXN": 18.0, "ZAR": 18.0, "IDR": 16000.0, "THB": 35.0, "MYR": 4.5,
    "PHP": 57.0, "CLP": 950.0, "COP": 4000.0, "ARS": 1000.0, "TRY": 35.0,
    # Added 2026-10-29: the first peer recompute under this floor logged 7 universe filers
    # reporting in these (PEN 4, VND, AED, KZT 1 each) with no reading.
    "AED": 3.67, "PEN": 3.75, "VND": 25500.0, "KZT": 500.0,
}
# Percentage points of growth premium per score point. At 2.0 a +10pp premium already scored
# 10/10, so the driver read 0 or 10 for 7 of the 10 reports cached 2026-10-15..28 (META +18pp,
# NVDA +55pp). At 5.0, +25pp over the sector median is 10 and -25pp is 0.
_GROWTH_DELTA_PER_POINT = 5.0

# A pillar is measured only when its own kind of evidence resolved (owner decisions
# 2026-10-28). Network Effects: at least one COMPANY input — the HHI and the lifecycle phase
# are the same for every company in the industry, so on their own they gave every airline
# without a growth figure the same "measured" 4.5. Intangible Assets: both SECTOR-RELATIVE
# inputs — the patent / FDA inputs are absolute scales the peer averages never compute, and
# a single patent (4.0) used to be the second input that made JOBY's pillar "measured".
_NETWORK_COMPANY_METRICS: Tuple[str, ...] = ("revenue_yoy", "platform_user_count")
# Inputs only the FOCAL company is scored on: the industry peer averages never fetch patents,
# FDA approvals or transcripts (`industry_moat_benchmark_service`), so a pillar that used one
# is not like-for-like with its industry peer and is never narrated as an edge or a gap.
FOCAL_ONLY_METRICS: Tuple[str, ...] = (
    "patents_per_employee", "fda_active_approvals", "nrr_pct", "platform_user_count",
)
_INTANGIBLE_SECTOR_METRICS: Tuple[str, ...] = ("rd_to_revenue", "intangibles_to_assets")

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
    """A FINITE float, else None. A NaN survives `float()` and every comparison against it
    is False, so it used to score a perfect 10.0 on all three mappers and land NaN in
    `drivers[].focal` (which jsonb cannot store)."""
    if not isinstance(record, dict):
        return None
    val = record.get(key)
    if val is None or isinstance(val, bool):
        return None
    try:
        number = float(val)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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


def _focal_revenue_yoy(
    income: List[Dict[str, Any]], year_key: str,
) -> Tuple[Optional[float], bool]:
    """``(revenue growth % of the year keyed `year_key`, whether it may be scored)``.

    Paired exactly as the sector median it is compared with
    (`sector_benchmark_service._compute_yoy_for_records`: one row per year key, a 300-430-day
    gap, no transition stub on either side) — the old date-sorted last-two-rows pairing read
    a repeated year as 0% and a missing year as one year's growth. Scorable only off a prior
    year of at least `_GROWTH_PRIOR_REVENUE_FLOOR` and inside the producer's clamp; a value
    outside them is still returned, so the driver shows the figure it refused."""
    if _year_of(year_key) is None:
        return None, False
    # Local import: sector_benchmark_service pulls in the FMP client at import time.
    from app.services.sector_benchmark_service import (
        WINSORIZE_CEIL,
        _by_annual_key_with_spans,
        _compute_yoy_for_records,
    )

    rows = [r for r in (income or []) if isinstance(r, dict)]
    yoy = _finite_number(
        _compute_yoy_for_records(rows, "revenue", is_quarterly=False).get(year_key)
    )
    if yoy is None:
        return None, False
    by_year, _spans = _by_annual_key_with_spans(rows)
    prior_row = by_year.get(str(int(year_key) - 1)) or {}
    prior = _safe_float(prior_row, "revenue")
    raw_currency = prior_row.get("reportedCurrency")
    units_per_usd = (
        1.0 if raw_currency is None
        else _FLOOR_UNITS_PER_USD.get(currency_code(raw_currency) or "")
    )
    if prior is not None and units_per_usd is None:
        logger.warning(
            "moat_scoring: %s revenue growth not scored — reporting currency %r has no "
            "floor rate (_FLOOR_UNITS_PER_USD)", prior_row.get("symbol") or "?", raw_currency,
        )
    scorable = (
        prior is not None
        and units_per_usd is not None
        and prior / units_per_usd >= _GROWTH_PRIOR_REVENUE_FLOOR
        # At -100% or below the current revenue is zero (FMP's zero-fill for an unreported
        # line, as `revenue_backed_margin` reads it) or negative: no growth reading.
        and -100.0 < yoy <= WINSORIZE_CEIL
    )
    return yoy, scorable


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
            # `_focal_revenue_yoy` scores the year keyed `inc_year` (the newest record).
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
            income, industry_tam, sector_medians, transcript_sig, inc_year,
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
        inc_year: str = "",
    ) -> PillarResult:
        """Network Effects: HHI band + lifecycle phase + revenue growth
        premium vs sector median (+ the platform user count when a transcript
        exists). Measured only when a COMPANY input resolved
        (`_NETWORK_COMPANY_METRICS`): the HHI and the lifecycle are industry-wide.
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

        # Lifecycle phase — enum mapping, no median. A broad stand-in (a sector or
        # all-industry dossier) carries its SECTOR's growth phase, which the report card
        # hides (`_apply_tam_source` shows only industry-grain figures), so it scores as
        # mature rather than lending this industry another one's growth (2026-10-22). A
        # plain `IndustryTAM` has no grain and is industry-level.
        phase = getattr(industry_tam, "lifecycle_phase", None) if industry_tam else None
        grain = getattr(industry_tam, "source_grain", None) if industry_tam else None
        if phase is not None and grain not in (None, "industry"):
            phase = "mature"
        drivers.append(MetricDriver(
            metric="lifecycle_phase",
            focal=None, sector_median=None,
            sub_score=_lifecycle_to_score(phase),
        ))

        # Revenue growth premium (absolute delta to sector median). A figure off a near-zero
        # prior year or past the producer's clamp is shown but not scored.
        focal_rev_yoy, growth_scorable = _focal_revenue_yoy(income, inc_year)
        payload = medians.get("revenue_yoy")
        median_rev_yoy, period, n = _unpack_median(payload)
        drivers.append(MetricDriver(
            metric="revenue_yoy",
            focal=focal_rev_yoy, sector_median=median_rev_yoy,
            sub_score=(
                _absolute_delta_score(
                    focal_rev_yoy, median_rev_yoy,
                    delta_per_point=_GROWTH_DELTA_PER_POINT,
                )
                if growth_scorable else None
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

        return _assemble_pillar(
            PILLAR_NETWORK, drivers,
            required_metrics=_NETWORK_COMPANY_METRICS, min_required=1,
        )

    def _score_brand_power(
        self,
        latest_ratios: Optional[Dict[str, Any]],
        medians: Dict[str, Optional[Dict[str, Any]]],
    ) -> PillarResult:
        """Brand Power: gross margin percentile + P/S percentile (the
        market pays for brand). Both higher = better.

        P/S is brand evidence only when the company earns a POSITIVE gross margin (owner
        decision 2026-10-28): selling below cost shows no pricing power, and its multiple is
        a bet on a tiny revenue base — JOBY's 204× P/S (revenue $53M, gross margin −30%)
        scored 10/10 and lifted Brand Power to 5.0. The driver still shows the figure.
        """
        gross_margin = self._gross_margin_ratio(latest_ratios)
        ps_driver = _build_higher_better_driver(
            "ps_ratio",
            # FMP answers 0 with no revenue: no reading, never a 0.0/10 driver.
            revenue_scaled_ratio(_safe_float(latest_ratios or {}, "priceToSalesRatio")),
            medians.get("ps_ratio"),
        )
        if gross_margin is None or gross_margin <= 0:
            ps_driver.sub_score = None
        drivers = [
            _build_higher_better_driver(
                "gross_margin", gross_margin, medians.get("gross_margin"),
            ),
            ps_driver,
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
                # Revenue / assets: FMP's 0 is "no revenue", not a reading (2026-10-11).
                revenue_scaled_ratio(_safe_float(latest_ratios or {}, "assetTurnover")),
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
        Measured only when BOTH sector-relative inputs resolved
        (`_INTANGIBLE_SECTOR_METRICS`); the patent / FDA inputs then join the average.
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

        return _assemble_pillar(
            PILLAR_INTANGIBLE, drivers,
            required_metrics=_INTANGIBLE_SECTOR_METRICS,
            min_required=len(_INTANGIBLE_SECTOR_METRICS),
        )

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

        A company with no revenue reads None (`revenue_backed_margin`): FMP answers its gross
        margin 0, and with its P/S (also 0) that was two "resolved" drivers, so every
        pre-revenue company published a deterministic Brand Power of 0.0/10 (VKTX,
        2026-10-11) instead of falling back to the AI dimension.
        """
        return revenue_backed_margin(
            _safe_float(ratios or {}, "grossProfitMargin"),
            _safe_float(ratios or {}, "revenuePerShare"),
        )

    def _operating_margin_ratio(self, ratios: Optional[Dict[str, Any]]) -> Optional[float]:
        """Operating margin on the stored 0-1 scale — see `_gross_margin_ratio`.

        `operating_margin` medians read from production are 0.0319-0.0821 for Consumer
        Cyclical, so a percentage here saturated this driver exactly as it did above.
        No revenue reads None, as for the gross margin: with the asset turnover (also 0)
        it published Cost Advantage 0.0/10 for QS and OKLO (2026-10-11).
        """
        return revenue_backed_margin(
            _safe_float(ratios or {}, "operatingProfitMargin"),
            _safe_float(ratios or {}, "revenuePerShare"),
        )

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
        # A company with revenue and exactly zero SG&A is FMP's zero-fill for an unreported
        # line, not a reading — and as a lower-is-better driver it scored a perfect 10.0.
        if not rev or rev <= 0 or sga is None or sga <= 0:
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
    name: str,
    drivers: List[MetricDriver],
    *,
    required_metrics: Tuple[str, ...] = (),
    min_required: int = 0,
) -> PillarResult:
    """Aggregate driver sub-scores into a pillar score + confidence.

    Unscored (None, so the caller falls back) below `_MIN_METRICS_FOR_SCORE` resolved
    drivers, or below `min_required` resolved drivers among `required_metrics` — the
    pillar's own kind of evidence (see `_NETWORK_COMPANY_METRICS`)."""
    valid_subs = [d.sub_score for d in drivers if d.sub_score is not None]
    required_resolved = sum(
        1 for d in drivers if d.metric in required_metrics and d.sub_score is not None
    )
    if len(valid_subs) < _MIN_METRICS_FOR_SCORE or required_resolved < min_required:
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


def report_moat_model_is_current(payload: Any) -> bool:
    """True unless a report's moat pillars were built by another scorer vintage.

    A report stamps `moat_competition.model_version` (= `SCORER_VERSION`) from 2026-10-28; a
    block with no stamp was built before it and fails closed (False), like the DCF gate's
    missing `model_version`. No moat block at all has nothing to compare: True.

    Used ONLY by the paid deep door's cross-user reuse (`research_service._lookup_shared_cache`):
    that buyer pays 20 credits on a reuse hit, so a report scored by older math is a miss and
    they get a fresh one at the same price. The FREE cached paths deliberately do not call it
    (a miss there would charge the next opener 20 credits; their rows age out at the close) —
    the same split as `dcf_report_gate.report_dcf_model_is_current` (owner, 2026-10-25:
    "Leave the free cached path as is")."""
    if not isinstance(payload, dict):
        return True
    block = payload.get("moat_competition")
    if not isinstance(block, dict):
        return True
    return block.get("model_version") == SCORER_VERSION


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
