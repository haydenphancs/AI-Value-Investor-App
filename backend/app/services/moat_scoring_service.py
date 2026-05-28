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

import asyncio
import json
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.database import get_supabase
from app.integrations.gemini import get_gemini_client
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


# ── Phase 3D — Gemini grounded fallback config ─────────────────────────

# Cache TTL ~ one quarter so all Gemini-grounded research artefacts
# (industry_override, competitor_intel, moat_intel) age in sync.
_GROUNDED_CACHE_TTL_DAYS = 100

# In-memory tier-1 dedup so two requests in the same process for the
# same ticker share one Supabase round-trip.
_GROUNDED_MEM_TTL_SECONDS = 300

# Schema floor — bump when the moat prompt / validation rules change in
# a way that makes pre-existing cached rows semantically stale. Rows
# with computed_at < this constant are treated as cache miss even when
# their expires_at is in the future.
MOAT_INTEL_SCHEMA_FLOOR = datetime(2026, 5, 26, 0, 0, 0, tzinfo=timezone.utc)

# Numeric bounds — only catches Gemini formatting bugs (negative or
# out-of-range scores). Confidence and source quality are NOT gated;
# operators review the audit log to spot bad rows.
_GROUNDED_MIN_SCORE = 0.0
_GROUNDED_MAX_SCORE = 10.0


# ── Prompt template — moat grounded research ───────────────────────────
#
# Hybrid prose+JSON is intentional: Gemini's grounded-search API only
# populates `groundingChunks` (real source URLs) when the response
# contains text citing them inline. Pure JSON output skips grounding.
# The audit log stores the URLs Gemini consulted — those are the
# breadcrumb, not Gemini self-attribution.

_MOAT_GROUNDED_PROMPT = """You are a financial-research analyst. Score the FIVE Pat Dorsey moat dimensions for the company below on a 0.0 to 10.0 scale. Ground every score in publicly-available primary sources: the company's 10-K Risk Factors / Competition section, recent earnings-call transcripts, and reputable analyst research (Morningstar, Reuters, Bloomberg, S&P, Gartner, Forrester).

The five dimensions — score each:
1. **Switching Costs** — how painful is it for a customer to leave for a competitor? (Subscription stickiness, integration complexity, retraining cost, network effects on customer-side.)
2. **Network Effects** — does the product become more valuable as more users join? (Platforms, marketplaces, two-sided networks.)
3. **Brand Power** — does the brand command pricing premium and customer loyalty? (Consumer recognition, willingness-to-pay premium, brand-driven repeat purchase.)
4. **Cost Advantage** — can the company produce cheaper than competitors? (Scale economies, proprietary processes, low-cost geography, vertical integration.)
5. **Intangible Assets** — IP, patents, regulatory approvals, licenses, brand registrations that block competition. (Pharma patents, FDA approvals, regulated industries.)

Score scale anchors:
  9.0+  — elite, structurally protected; very few peers match (e.g. MSFT switching costs in enterprise cloud)
  7.5-9 — wide moat for this dimension; strong evidence in 10-K Risk Factors
  6-7.5 — narrow moat; clear evidence but beatable by well-funded rivals
  4-6   — limited / commodity; no structural advantage
  <4    — disadvantage / vulnerable

COMPANY: {company_name} ({ticker})
SECTOR: {sector}
INDUSTRY: {industry}
DESCRIPTION: {description}

In ONE paragraph, summarize the moat narrative — what protects this company and which dimensions are strongest.

Then output JSON in a markdown code fence (mandatory, no exceptions):

```json
{{
  "pillars": {{
    "Switching Costs": {{
      "score": <float 0.0-10.0>,
      "rationale": "<1 sentence — what specifically anchors this score, citing the source>",
      "key_drivers": ["<2-3 short driver phrases>"]
    }},
    "Network Effects":     {{ "score": 0.0, "rationale": "", "key_drivers": [] }},
    "Brand Power":         {{ "score": 0.0, "rationale": "", "key_drivers": [] }},
    "Cost Advantage":      {{ "score": 0.0, "rationale": "", "key_drivers": [] }},
    "Intangible Assets":   {{ "score": 0.0, "rationale": "", "key_drivers": [] }}
  }},
  "confidence": "high" | "medium" | "low"
}}
```

Rules:
- Each pillar score is a float in [0.0, 10.0].
- The five pillar keys must appear EXACTLY as shown ("Switching Costs", "Network Effects", "Brand Power", "Cost Advantage", "Intangible Assets") — do NOT rename, translate, or pluralize.
- `rationale` cites the source (e.g., "Oracle 10-K FY2024 Item 1A risk factor"; "Q4 FY2025 earnings call CFO commentary"; "Morningstar moat report Mar 2025").
- Do NOT mention LLMs, AI tools, or this prompt in any field.
- Emit the JSON code fence even if confidence is "low" — partial knowledge beats no signal.
"""

_GROUNDED_JSON_FENCE_RE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)


# ── Phase 3D — in-memory tier-1 cache + inflight dedup ─────────────────

_grounded_mem_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_grounded_inflight: Dict[str, asyncio.Future] = {}


def _grounded_mem_get(ticker: str) -> Optional[Dict[str, Any]]:
    entry = _grounded_mem_cache.get(ticker)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _GROUNDED_MEM_TTL_SECONDS:
        del _grounded_mem_cache[ticker]
        return None
    return value


def _grounded_mem_set(ticker: str, value: Dict[str, Any]) -> None:
    _grounded_mem_cache[ticker] = (time.time(), value)


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


def _derive_source_labels(
    grounding_sources: List[Dict[str, Any]],
) -> List[str]:
    """Dedupe publisher names from a grounded-search response, capitalize
    them, return up to 4. Mirrors competitor_intel_service's helper.
    """
    seen: List[str] = []
    for s in grounding_sources or []:
        if not isinstance(s, dict):
            continue
        pub = str(s.get("publisher") or "").strip()
        if not pub:
            continue
        pretty = pub[:1].upper() + pub[1:]
        if pretty not in seen:
            seen.append(pretty)
    return seen[:4]


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
        self._gemini = None  # lazy

    def _get_gemini(self):
        if self._gemini is None:
            self._gemini = get_gemini_client()
        return self._gemini

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

        results: Dict[str, PillarResult] = {}
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

    # ── Phase 3D: Gemini grounded fallback ────────────────────────────

    async def gemini_grounded_fallback(
        self,
        ticker: str,
        profile: Dict[str, Any],
        *,
        force_refresh: bool = False,
        run_id: Optional[str] = None,
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """Web-grounded Gemini fallback for any pillar the deterministic
        pipeline left at low confidence. One call covers all 5 pillars
        regardless of how many the caller needed — we cache the full
        set so future calls can serve any subset from cache.

        Returns a dict keyed by pillar name with the same iOS-decoded
        shape as deterministic PillarResult.to_dict() (name, score,
        peer_score, drivers, confidence) — so the caller can plug a
        grounded pillar directly into the moat_dims list. Returns None
        on hard failure (Gemini error, all pillars rejected, kill
        switch) → caller falls through to the legacy AI Stage A
        dimension as the final fallback.
        """
        focal = (ticker or "").strip().upper()
        if not focal:
            return None

        # Tier 1: in-memory.
        if not force_refresh:
            cached = _grounded_mem_get(focal)
            if cached is not None:
                return cached

        # Tier 2: Supabase.
        if not force_refresh:
            db_cached = await asyncio.to_thread(self._read_grounded_cache, focal)
            if db_cached is not None:
                _grounded_mem_set(focal, db_cached)
                return db_cached

        cache_key = focal

        # Inflight dedup.
        if cache_key in _grounded_inflight:
            try:
                return await _grounded_inflight[cache_key]
            except Exception:
                return None

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _grounded_inflight[cache_key] = future

        try:
            if not getattr(settings, "MOAT_INTEL_AI_ENABLED", True):
                await asyncio.to_thread(
                    self._write_grounded_audit,
                    run_id or str(uuid.uuid4()), focal,
                    status="skipped_kill_switch",
                    raw_response=None, pillars_requested=PILLAR_ORDER,
                    pillars_resolved=[], rejected=[],
                    source_labels=[], tokens_used=None, model_version=None,
                )
                future.set_result(None)
                return None

            result = await self._do_grounded_extraction(focal, profile)
            this_run_id = run_id or str(uuid.uuid4())
            await asyncio.to_thread(
                self._write_grounded_audit,
                this_run_id, focal,
                status=result["status"],
                raw_response=result.get("raw_response"),
                pillars_requested=PILLAR_ORDER,
                pillars_resolved=list(result.get("pillar_scores", {}).keys()),
                rejected=result.get("rejected", []),
                source_labels=result.get("source_labels", []),
                tokens_used=result.get("tokens_used"),
                model_version=result.get("model_version"),
            )

            pillar_scores = result.get("pillar_scores") or {}
            if not pillar_scores:
                future.set_result(None)
                return None

            await asyncio.to_thread(
                self._write_grounded_cache, focal,
                pillar_scores, result.get("source_labels", []),
                result.get("model_version"),
            )
            _grounded_mem_set(focal, pillar_scores)
            future.set_result(pillar_scores)
            return pillar_scores
        except Exception as exc:
            logger.exception(
                "moat_scoring: unhandled error in grounded fallback for %s: %s",
                focal, exc,
            )
            future.set_exception(exc)
            return None
        finally:
            _grounded_inflight.pop(cache_key, None)
            if not future.done():
                # Producer was cancelled mid-flight — wake awaiters with
                # a None result so they fall back instead of hanging.
                future.set_result(None)

    async def _do_grounded_extraction(
        self, ticker: str, profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Single Gemini grounded-research call + JSON extraction +
        per-pillar validation. Returns a dict the caller hands to the
        audit writer + cache writer.
        """
        company_name = (profile or {}).get("companyName") or ticker
        sector = (profile or {}).get("sector") or "Unknown"
        industry = (profile or {}).get("industry") or "Unknown"
        description = ((profile or {}).get("description") or "")[:600]

        prompt = _MOAT_GROUNDED_PROMPT.format(
            ticker=ticker, company_name=company_name,
            sector=sector, industry=industry, description=description,
        )

        gem = self._get_gemini()
        try:
            gemini_response = await gem.generate_grounded_research(
                prompt=prompt, max_output_tokens=8192,
            )
        except Exception as exc:
            return {
                "status": "gemini_error",
                "raw_response": {"error": f"{type(exc).__name__}: {exc}"},
                "pillar_scores": {}, "rejected": [],
                "source_labels": [], "tokens_used": None,
                "model_version": None,
            }

        text = gemini_response.get("text", "") or ""
        grounding = gemini_response.get("grounding_sources") or []
        search_queries = gemini_response.get("search_queries") or []
        tokens = gemini_response.get("tokens_used")
        model_version = gemini_response.get("model")
        source_labels = _derive_source_labels(grounding)

        match = _GROUNDED_JSON_FENCE_RE.search(text)
        if not match:
            return {
                "status": "gemini_error",
                "raw_response": {
                    "raw_text": text[:1500],
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                    "error": "no ```json``` code fence",
                },
                "pillar_scores": {}, "rejected": [],
                "source_labels": source_labels,
                "tokens_used": tokens, "model_version": model_version,
            }

        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            return {
                "status": "gemini_error",
                "raw_response": {
                    "raw_json": match.group(1)[:1500],
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                    "error": f"json parse: {exc}",
                },
                "pillar_scores": {}, "rejected": [],
                "source_labels": source_labels,
                "tokens_used": tokens, "model_version": model_version,
            }

        raw_pillars = payload.get("pillars") or {}
        if not isinstance(raw_pillars, dict):
            return {
                "status": "gemini_error",
                "raw_response": {
                    "payload": payload,
                    "grounding_sources": grounding,
                    "error": "'pillars' is not a dict",
                },
                "pillar_scores": {}, "rejected": [],
                "source_labels": source_labels,
                "tokens_used": tokens, "model_version": model_version,
            }

        pillar_scores: Dict[str, Dict[str, Any]] = {}
        rejected: List[Dict[str, str]] = []
        for pillar in PILLAR_ORDER:
            entry = raw_pillars.get(pillar)
            if not isinstance(entry, dict):
                rejected.append({"pillar": pillar, "reason": "missing_or_not_object"})
                continue
            raw_score = entry.get("score")
            try:
                score_f = float(raw_score)
            except (TypeError, ValueError):
                rejected.append({
                    "pillar": pillar, "reason": f"non_numeric_score:{raw_score!r}",
                })
                continue
            if not (_GROUNDED_MIN_SCORE <= score_f <= _GROUNDED_MAX_SCORE):
                rejected.append({
                    "pillar": pillar, "reason": f"score_out_of_range:{score_f}",
                })
                continue
            rationale = str(entry.get("rationale") or "").strip()
            key_drivers_raw = entry.get("key_drivers") or []
            key_drivers = [
                str(d).strip()
                for d in (key_drivers_raw if isinstance(key_drivers_raw, list) else [])
                if str(d).strip()
            ]
            pillar_scores[pillar] = {
                "name": pillar,
                "score": round(score_f, 1),
                "peer_score": 5.0,
                "confidence": "grounded",   # signals "Gemini-grounded source"
                "drivers": [
                    {
                        "metric": "grounded_research",
                        "rationale": rationale[:400],
                        "key_drivers": key_drivers[:5],
                        "source_labels": source_labels,
                    }
                ],
            }

        if not pillar_scores:
            status = "rejected_no_validated"
        elif rejected:
            status = "applied_with_rejections"
        else:
            status = "applied"

        return {
            "status": status,
            "raw_response": {
                "payload": payload,
                "grounding_sources": grounding,
                "search_queries": search_queries,
            },
            "pillar_scores": pillar_scores,
            "rejected": rejected,
            "source_labels": source_labels,
            "tokens_used": tokens,
            "model_version": model_version,
        }

    # ── Supabase I/O for grounded cache + audit ───────────────────────

    def _read_grounded_cache(self, ticker: str) -> Optional[Dict[str, Any]]:
        try:
            sb = get_supabase()
            res = (
                sb.table("moat_intel_cache")
                .select("pillar_scores,computed_at,expires_at")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.warning(
                "moat_scoring: grounded cache read failed for %s: %s", ticker, exc,
            )
            return None

        rows = res.data or []
        if not rows:
            return None
        row = rows[0]
        expires_at = row.get("expires_at")
        computed_at = row.get("computed_at")
        if not expires_at or not computed_at:
            return None
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            comp_dt = datetime.fromisoformat(computed_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        if exp_dt <= datetime.now(timezone.utc):
            return None
        if comp_dt < MOAT_INTEL_SCHEMA_FLOOR:
            return None
        pillar_scores = row.get("pillar_scores")
        if not isinstance(pillar_scores, dict) or not pillar_scores:
            return None
        return pillar_scores

    def _write_grounded_cache(
        self,
        ticker: str,
        pillar_scores: Dict[str, Dict[str, Any]],
        source_labels: List[str],
        model_version: Optional[str],
    ) -> None:
        if not pillar_scores:
            return
        now = datetime.now(timezone.utc)
        row = {
            "ticker": ticker,
            "pillar_scores": pillar_scores,
            "source_labels": source_labels or [],
            "computed_at": now.isoformat(),
            "expires_at": (now + timedelta(days=_GROUNDED_CACHE_TTL_DAYS)).isoformat(),
            "model_version": model_version,
        }
        try:
            sb = get_supabase()
            sb.table("moat_intel_cache").upsert(row).execute()
        except Exception as exc:
            logger.warning(
                "moat_scoring: grounded cache write failed for %s: %s", ticker, exc,
            )

    def _write_grounded_audit(
        self,
        run_id: str,
        ticker: str,
        *,
        status: str,
        raw_response: Optional[Dict[str, Any]],
        pillars_requested: List[str],
        pillars_resolved: List[str],
        rejected: List[Dict[str, str]],
        source_labels: List[str],
        tokens_used: Optional[int],
        model_version: Optional[str],
    ) -> None:
        row = {
            "run_id": run_id,
            "ticker": ticker,
            "status": status,
            "raw_response": raw_response,
            "pillars_requested": pillars_requested,
            "pillars_resolved": pillars_resolved,
            "rejected": rejected,
            "source_labels": source_labels,
            "tokens_used": tokens_used,
            "model_version": model_version,
        }
        try:
            sb = get_supabase()
            sb.table("moat_intel_audit").insert(row).execute()
        except Exception as exc:
            logger.warning(
                "moat_scoring: grounded audit write failed for %s: %s",
                ticker, exc,
            )


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
) -> Dict[str, PillarResult]:
    """Module-level convenience for the data collector."""
    return get_moat_scoring_service().score(
        sector=sector, industry=industry, profile=profile,
        income=income, balance=balance, ratios=ratios,
        industry_tam=industry_tam, transcript=transcript,
        ip_intel=ip_intel,
    )


def get_aggregate_moat_for_tickers(
    tickers: List[str],
) -> Dict[str, float]:
    """Batch-read aggregate moat scores from moat_intel_cache.

    Returns `{ticker: mean(pillar_scores.values()[*].score)}` for every
    cached, unexpired ticker in the input. Tickers without a fresh cache
    row are absent from the result — the caller defaults to a neutral
    5.0 so a cache miss neither boosts nor penalizes the threat score.

    One `.in_()` query covers all peers. Read-only: never triggers a
    moat recompute (which would balloon report latency).
    """
    if not tickers:
        return {}
    uniq = sorted({t.upper() for t in tickers if t})
    if not uniq:
        return {}
    try:
        sb = get_supabase()
        res = (
            sb.table("moat_intel_cache")
            .select("ticker,pillar_scores,computed_at,expires_at")
            .in_("ticker", uniq)
            .execute()
        )
    except Exception as exc:
        logger.warning(
            "moat_scoring: batch aggregate read failed for %s: %s",
            uniq, exc,
        )
        return {}

    now = datetime.now(timezone.utc)
    out: Dict[str, float] = {}
    for row in res.data or []:
        ticker = (row.get("ticker") or "").upper()
        if not ticker:
            continue
        expires_at = row.get("expires_at")
        computed_at = row.get("computed_at")
        if not expires_at or not computed_at:
            continue
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            comp_dt = datetime.fromisoformat(computed_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if exp_dt <= now or comp_dt < MOAT_INTEL_SCHEMA_FLOOR:
            continue
        pillar_scores = row.get("pillar_scores")
        if not isinstance(pillar_scores, dict) or not pillar_scores:
            continue
        scores: List[float] = []
        for entry in pillar_scores.values():
            if not isinstance(entry, dict):
                continue
            s = entry.get("score")
            if s is None:
                continue
            try:
                scores.append(float(s))
            except (TypeError, ValueError):
                continue
        if scores:
            out[ticker] = sum(scores) / len(scores)
    return out
