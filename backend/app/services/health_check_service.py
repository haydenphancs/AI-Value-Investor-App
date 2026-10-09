"""
Health Check service — fetches financial ratios from FMP, compares them
to pre-computed peer median benchmarks (the industry's, else the sector's),
computes gauge positions, status colors, and dynamic insight text.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``health_check_cache`` table (24-hour TTL + earnings-aware)

Matches the iOS HealthCheckSectionData struct.
"""

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.services._earnings_common import (
    CALENDAR_UNKNOWN,
    EarningsStamp,
    next_earnings_stamp,
    stamp_is_persistable,
)
from app.database import get_supabase
from app.utils.inflight import fail_shared_future
from app.integrations.fmp import FMPNotEntitledException, get_fmp_client
from app.schemas.health_check import HealthCheckMetricSchema, HealthCheckResponse
from app.services.asset_class import profile_is_fund
# The financials gate lives in ONE module shared with the snapshot cards and the benchmark
# producer. `_industry_key`, `_LIQUIDITY_NA_INDUSTRIES` and `liquidity_ratios_applicable`
# stay importable from here under their old names (tests and older callers use them).
from app.services.financials_metric_gate import (  # noqa: F401  (re-exported names)
    GATED_METRICS,
    _NO_LIQUIDITY_INDUSTRIES as _LIQUIDITY_NA_INDUSTRIES,
    company_metric_applicable,
    industry_key as _industry_key,
    interest_coverage_applicable,
    liquidity_ratios_applicable,
    peer_median_comparable,
    peer_metric_applicable,
    resolve_payment_network,
    resolve_withheld_company_rows,
    trailing_interest_row,
)
from app.services.sector_benchmark_lookup import get_sector_benchmark_lookup, lookup_failed
from app.services.sector_benchmark_service import _normalize_sector

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes

# Version stamped into every health_check_cache row. Bump it whenever a stored value's
# MEANING changes, so rows written by the old rules are rebuilt on their next read
# instead of being served for up to 24h (same pattern as signal_of_confidence_service).
# 2 (2026-09-30): Altman Z is omitted for financials / REITs; ROE on negative equity is
#     "N/M" and unscored; IC=0 with no interest expense and P/E<=0 are omitted; a
#     non-positive peer median is no benchmark; Z status uses the 2-dp score.
# 3 (2026-09-30, round 2): the Z insight prints 2 dp (it printed "3.0" beside "Safe");
#     a barely-positive ROE / IC / D/E median is no benchmark (`_MIN_USABLE_MEDIAN`); a
#     balance-sheet equity of 0 beside a positive D/E no longer makes ROE "N/M".
# 4 (2026-10-01, P20): the earnings calendar is fetched with raise_errors=True and a
#     failed fetch is never persisted, so a NULL `next_earnings_date` now means "no
#     pending announcement". An older row's NULL may be a swallowed calendar 429 (no
#     report-day bound for up to 24h), so those rows are rebuilt.
# 5 (2026-10-07): banks, insurers and capital-markets firms no longer get interest
#     coverage, current ratio or quick ratio (`liquidity_ratios_applicable` — C showed all
#     three red, BAC a 0.54 current ratio as "positive"); every compared metric carries
#     `peer_level`, and its text names that level ("industry average" when the median is
#     the industry's — it always said "sector"); the median itself comes from the lookup's
#     new current-benchmark picker (never a thin or incomplete-period cell).
# 6 (2026-10-07, review round 2): the gate moved to `financials_metric_gate`, and insurance
#     brokers (MMC, AON, AJG) get interest coverage BACK — a fee business whose lenders
#     watch that ratio (only current / quick ratio are distorted by fiduciary funds). A
#     current / quick ratio / interest-coverage median that is the Financial Services
#     SECTOR's is no comparison (`_bank_pooled_sector_cell`, permanent): that aggregate
#     pools bank and insurer values, and once the producer leaves those out it is shells,
#     exchanges and developers — never a peer group. A v5 row (a local run may have
#     written one) is rebuilt.
# 7 (2026-10-08, owner decision 3, first cut — never deployed): card networks and payment
#     processors in "Financial - Credit Services" kept current ratio, quick ratio and
#     interest coverage when their interest income was under 25% of revenue. A v6 row of a
#     network lacks the three rows, so it is rebuilt.
# 8 (2026-10-08, same day): the 25% share misread real LENDERS whose interest income FMP
#     zero-fills (ENVA, SEZL, QFIN, FINV looked exactly like V). A Credit Services company
#     is now a payment network only when its ticker is on the curated
#     `financials_metric_gate.PAYMENT_NETWORKS` (V, MA, PYPL, WU, GPN) and its own income
#     does not read as a lender's; every other member stays gated. A network's current
#     ratio, quick ratio, interest coverage AND debt-to-equity are judged on ABSOLUTE bands
#     only (the industry's medians pool lenders: `peer_median_comparable`, `peer_level`
#     None). A v7 row is rebuilt: a local run of the first cut (`uvicorn --reload` against
#     the shared cache) may have written one for an unlisted lender with the three rows,
#     or a network with its D/E compared against lenders. v7 never shipped, so in
#     production this bump rebuilds nothing that v7 would not.
# 9 (2026-10-09, owner decisions NET-4 / NET-5): a listed non-lender member of "Financial -
#     Credit Services" is never compared with that industry's median on ANY metric — its
#     P/E and ROE rows join D/E and the three liquidity / coverage rows on the absolute
#     bands (`peer_median_comparable`, `peer_level` None), and when nothing is left to ask
#     no benchmark lookup is made (so a Supabase blip cannot mark that build degraded).
#     The list grows by the non-lender fee businesses TREE and PMTS
#     (`financials_metric_gate.NON_LENDER_MEMBERS`), and a curated per-company fact
#     withholds WU's current ratio, quick ratio and interest coverage
#     (`CURATED_WITHHELD_ROWS`: FMP's split and its interest expense are not WU's). A v8
#     row of V / MA / PYPL / GPN compared P/E and ROE with lenders; a v8 row of WU (local
#     runs only — v8 never shipped) carries its made-up rows; TREE / PMTS lacked theirs.
_HC_PAYLOAD_VERSION = 9
_HC_VERSION_KEY = "payload_version"

# ── Fund-shaped empty builds (2026-10-01) ─────────────────────────────────────────
# A fund (VB, SPY …) that reaches this card has no ratios, ROE or balance-sheet metrics, so
# its build scores nothing and is marked `no_metrics` — which the writer refuses as a
# possible outage (a 429'd leg coerced to [] looks the same), so every view of a fund
# rebuilt it from FMP every 5 min. The ONE degraded shape the cache admits is that build,
# and only when a POSITIVE fund flag on the FMP profile fetched in the SAME build says so
# (`asset_class.profile_is_fund`), the ratios-TTM, key-metrics-TTM, balance-sheet and
# income legs each answered a RAW list (checked before the builder coerces an error dict
# to {} / [] without a reason), and `no_metrics` is the build's ONLY reason — never
# inferred from the empty answer itself. The row carries `security_kind: "fund"`; the
# reader admits a degraded row only with that marker and only in that shape. The response
# is unchanged: it still says `degraded == ["no_metrics"]`, so health_snapshot, the report
# collector and iOS treat it exactly as before. No payload_version bump: no existing row
# carries the marker, and an older reader refuses a marked row (it is degraded).
# This is the ONE degraded build written to health_check_cache: the `HealthCheckResponse.
# degraded` comment and SYSTEM_DESIGN_GUIDELINES "A partial build is served, never stored"
# must name it too (tests/test_fund_shape_caching_soc_hc.py pins both).
_SECURITY_KIND_KEY = "security_kind"
_SECURITY_KIND_FUND = "fund"
_FUND_SHAPE_DEGRADED = ["no_metrics"]


def _is_fund_shape(resp: Any) -> bool:
    """True for exactly the build a fund produces: no metric, and `no_metrics` as the ONLY
    degraded reason (every leg answered, the peer lookup did not fail)."""
    return (
        list(getattr(resp, "degraded", None) or []) == _FUND_SHAPE_DEGRADED
        and not getattr(resp, "metrics", None)
    )


@dataclass
class _HcBuild:
    """`_build_health_check_full`'s answer. ``fund_shape`` is True only for a fund's
    empty build (see the block above)."""

    response: HealthCheckResponse
    next_earnings: EarningsStamp
    fund_shape: bool = False


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


# Hard cap on the in-memory tier. Without it this dict grew with the number of DISTINCT
# keys ever requested and was never pruned: `_cache_get` only deletes an entry when that
# SAME key is read again after expiry, so a ticker fetched once and never revisited stayed
# resident for the life of the process. Across ~17 services on a long-lived Railway
# container that is a slow leak whose only resolution is an OOM restart — which drops every
# in-flight report with it. Bounded LRU-ish: evict from the head (least recently WRITTEN).
_CACHE_MAX_ENTRIES = 1024


def _cache_set(key: str, value: Any) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


# ── In-flight deduplication ───────────────────────────────────────
_inflight: Dict[str, asyncio.Future] = {}

# ── Ticker validation ────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _validate_ticker(ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


# ── Helpers ───────────────────────────────────────────────────────

def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    val = record.get(key)
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


# ── Metric definitions ───────────────────────────────────────────

# FMP field name, sector_benchmarks metric name, lower_is_better flag
# Note: FMP stable API field names match sector_benchmark_service.py
# ROE comes from key-metrics endpoint, not ratios.
# The peer median is the INDUSTRY's when that group is mature, else the sector's
# (`sector_benchmark_lookup.get_current_benchmarks`); each metric reports which as
# `peer_level`, and the insight text names the same level.
METRIC_DEFS = [
    {
        "type": "debt_to_equity",
        "source": "ratios",
        "fmp_field": "debtToEquityRatio",
        "benchmark_name": "debt_to_equity",
        "lower_is_better": True,
        "is_percentage": False,
    },
    {
        "type": "pe_ratio",
        "source": "ratios",
        "fmp_field": "priceToEarningsRatio",
        "benchmark_name": "pe_ratio",
        "lower_is_better": True,
        "is_percentage": False,
    },
    {
        "type": "roe",
        "source": "key_metrics",
        "fmp_field": "returnOnEquity",
        "benchmark_name": "roe",
        "lower_is_better": False,
        "is_percentage": True,  # FMP returns as decimal (0.12 = 12%)
    },
    {
        "type": "current_ratio",
        "source": "ratios",
        "fmp_field": "currentRatio",
        "benchmark_name": "current_ratio",
        "lower_is_better": False,
        "is_percentage": False,
    },
    {
        "type": "interest_coverage",
        "source": "ratios",
        # FMP renamed this in 2026: `interestCoverage` → `interestCoverageRatio`
        # (and on /ratios-ttm it's `interestCoverageRatioTTM`). The extractor
        # below auto-appends TTM and falls back to the bare name, so listing
        # the un-suffixed form is correct. Matches the rename in
        # sector_benchmark_service METRIC_CONFIGS.
        "fmp_field": "interestCoverageRatio",
        "benchmark_name": "interest_coverage",
        "lower_is_better": False,
        "is_percentage": False,
    },
    {
        "type": "quick_ratio",
        "source": "ratios",
        "fmp_field": "quickRatio",
        "benchmark_name": "quick_ratio",
        "lower_is_better": False,
        "is_percentage": False,
    },
    # Altman Z-Score uses absolute thresholds (no sector benchmark)
    # Computed separately from balance sheet + income statement + market cap
    {
        "type": "altman_z_score",
        "source": "computed",
        "fmp_field": None,
        "benchmark_name": None,
        "lower_is_better": False,
        "is_percentage": False,
    },
]

# highlighted_value of a row the card SHOWS but cannot judge (ROE on negative equity).
# iOS keys its "N/M" rendering on this exact token (HealthCheckMetric.notMeaningfulToken),
# and the row is left out of passed_count / total_count.
NOT_MEANINGFUL = "N/M"

# ── Status thresholds (percent difference) ────────────────────────
# For lower_is_better: negative pct_diff = company below sector = good
# For higher_is_better: positive pct_diff = company above sector = good
_STATUS_THRESHOLDS = {
    "debt_to_equity":    {"positive_below": -10, "negative_above": 100},
    "pe_ratio":          {"positive_below": -10, "negative_above": 35},
    "roe":               {"positive_above": 10,  "negative_below": -20},
    "current_ratio":     {"positive_above": 10,  "negative_below": -25},
    "interest_coverage": {"positive_above": 50,  "negative_below": -25},
    "quick_ratio":       {"positive_above": 10,  "negative_below": -25},
    # altman_z_score uses absolute thresholds, not percent difference
}

# The smallest peer median (in the metric's RAW units — ROE as a decimal) that is still a
# usable anchor for a percent gap. At or below it the metric falls back to the absolute
# heuristics with no comparison, exactly as for a non-positive median. A percent gap
# divides by the median, so a median near zero turns a small absolute difference into an
# absurd verdict: ROE 5% against a 1% median read "5.0x well above sector average.
# Exceptional capital efficiency." and became the card's top pass, where the absolute
# reading is "Modest returns"; a 1e-6 median read "50000.0x".
#   roe 0.02 — a thin or loss-heavy industry whose median peer barely earns anything;
#       a 2% ROE is no yardstick for "capital efficiency" (FMP medians are not floored
#       at zero: `industry_benchmark_service._TTM_METRICS` keeps negatives).
#   interest_coverage 1.0 — a median peer that cannot cover its own interest is a
#       distressed anchor; "6.0x well above. Outsized capacity" for an IC of 3.0 against
#       0.5 overstates the absolute "Adequate coverage".
#   debt_to_equity 0.05 — a near debt-free peer group (D/E is not positive_only either);
#       a D/E of 0.5 against 0.04 read "12.5x well above. Significantly leveraged vs
#       peers", where 0.5 is moderate leverage on any absolute reading.
# P/E, current and quick ratio medians are positive_only and never plausibly this close
# to zero, so they keep the bare non-positive rule. Normal medians are untouched.
_MIN_USABLE_MEDIAN: Dict[str, float] = {
    "roe": 0.02,
    "interest_coverage": 1.0,
    "debt_to_equity": 0.05,
}
_NON_POSITIVE_MEDIAN = 1e-9


def _min_usable_median(metric_type: str) -> float:
    """A median at or below this value is no benchmark for `metric_type`."""
    return _MIN_USABLE_MEDIAN.get(metric_type, _NON_POSITIVE_MEDIAN)


def _determine_status(metric_type: str, pct_diff: float, lower_is_better: bool) -> str:
    thresholds = _STATUS_THRESHOLDS[metric_type]
    if lower_is_better:
        if pct_diff < thresholds["positive_below"]:
            return "positive"
        elif pct_diff > thresholds["negative_above"]:
            return "negative"
        else:
            return "neutral"
    else:
        if pct_diff > thresholds["positive_above"]:
            return "positive"
        elif pct_diff < thresholds["negative_below"]:
            return "negative"
        else:
            return "neutral"


def _gauge_position(value: float, sector: float) -> float:
    """Map company value to 0.0–1.0 gauge.

    Sector median anchors at ~0.5.  Works for both directions because
    the iOS gauge bar handles gradient direction per metric type.
    """
    if sector <= 0:
        return 0.5
    return _clamp(value / sector / 2.0, 0.02, 0.98)


# ── Dynamic insight text ─────────────────────────────────────────

def _format_diff_label(pct_diff: float) -> str:
    """Format the highlighted_value for a SIGNED percent difference vs the peer median.

    A multiplier only reads correctly ABOVE the median (+400% is "5.0x" the median). It
    used to be applied to |pct|, so ROE -25% against a 12% median (pct -308) printed
    "4.1x well below sector average" — "N times below" means nothing. Below the median the
    gap is a share of the median, which can only pass 100% once the company value has
    crossed zero; the metric loop diverts that case before any generator runs
    (`_crossed_zero_insight`), and anything that still slips through is capped at 100%.
    """
    if pct_diff >= 200:
        multiplier = round(pct_diff / 100 + 1, 1)
        return f"{multiplier}x"
    if pct_diff < 0:
        return f"{min(int(round(-pct_diff)), 100)}%"
    return f"{int(round(pct_diff))}%"


def _generate_de_insight(
    pct_diff: float, value: float, sector: float, peer: str = "sector",
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Debt-to-Equity.

    Returns (main_text, highlighted_value, highlighted_label) where the
    frontend renders: ``{value} {label} {main_text}``
    e.g.  **43%** **below** sector average. Conservative leverage.

    ``peer`` is the level of the median compared against, "industry" or "sector"
    (`_peer_noun`), and every generator below takes it: the text said "sector average"
    even when the median was the INDUSTRY's. iOS renders these strings verbatim and
    parses none of them (verified 2026-10-07), so the word can follow the level.
    """
    label = _format_diff_label(pct_diff)

    if pct_diff < -50:
        return (
            f"{peer} average. Very conservative leverage.",
            label,
            "below",
        )
    elif pct_diff < -25:
        return (
            f"{peer} average. Conservative leverage.",
            label,
            "below",
        )
    elif pct_diff < -10:
        return (
            f"{peer} average. Healthy debt position.",
            label,
            "below",
        )
    elif pct_diff <= 15:
        direction = "above" if pct_diff > 0 else "below"
        return (
            f"{peer} average. Leverage in line with peers.",
            label,
            direction,
        )
    elif pct_diff <= 50:
        return (
            f"{peer} average. Moderately higher leverage.",
            label,
            "above",
        )
    elif pct_diff <= 100:
        return (
            f"{peer} average. Elevated leverage.",
            label,
            "above",
        )
    else:
        return (
            f"{peer} average. Significantly leveraged vs peers.",
            label,
            "well above",
        )


def _generate_pe_insight(
    pct_diff: float, value: float, sector: float, peer: str = "sector",
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for P/E Ratio.

    Returns (main_text, highlighted_value, highlighted_label) where the
    frontend renders: ``{value} {label} {main_text}``
    e.g.  **15%** **below** sector average. Fair value opportunity.
    """
    label = _format_diff_label(pct_diff)

    if pct_diff < -30:
        return (
            f"{peer} average. Deep value opportunity.",
            label,
            "below",
        )
    elif pct_diff < -15:
        return (
            f"{peer} average. Fair value opportunity.",
            label,
            "below",
        )
    elif pct_diff < -5:
        return (
            f"{peer} average. Slight valuation edge.",
            label,
            "below",
        )
    elif pct_diff <= 10:
        direction = "above" if pct_diff > 0 else "below"
        return (
            f"{peer} average. Valued in line with peers.",
            label,
            direction,
        )
    elif pct_diff <= 35:
        return (
            f"{peer} average. Premium valuation.",
            label,
            "above",
        )
    elif pct_diff <= 75:
        return (
            f"{peer} average. Priced for high growth.",
            label,
            "well above",
        )
    else:
        return (
            f"{peer} average. Richly valued vs peers.",
            label,
            "well above",
        )


def _generate_roe_insight(
    pct_diff: float, value: float, sector: float, peer: str = "sector",
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Return on Equity.

    Returns (main_text, highlighted_value, highlighted_label) where the
    frontend renders: ``{value} {label} {main_text}``
    e.g.  **22%** **above** sector average. Strong capital efficiency.
    """
    label = _format_diff_label(pct_diff)

    if pct_diff > 100:
        return (
            f"{peer} average. Exceptional capital efficiency.",
            label,
            "well above",
        )
    elif pct_diff > 40:
        return (
            f"{peer} average. Strong capital efficiency.",
            label,
            "above",
        )
    elif pct_diff > 10:
        return (
            f"{peer} average. Solid returns on equity.",
            label,
            "above",
        )
    elif pct_diff >= -10:
        direction = "above" if pct_diff >= 0 else "below"
        return (
            f"{peer} average. Average capital efficiency.",
            label,
            direction,
        )
    elif pct_diff >= -30:
        return (
            f"{peer} average. Below-average capital efficiency.",
            label,
            "below",
        )
    elif pct_diff >= -50:
        return (
            f"{peer} average. Low capital efficiency.",
            label,
            "below",
        )
    else:
        return (
            f"{peer} average. Significantly underperforming.",
            label,
            "well below",
        )


def _generate_cr_insight(
    pct_diff: float, value: float, sector: float, peer: str = "sector",
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Current Ratio."""
    label = _format_diff_label(pct_diff)

    if pct_diff > 75:
        return (
            f"{peer} average. Ample liquidity cushion.",
            label,
            "well above",
        )
    elif pct_diff > 30:
        return (
            f"{peer} average. Healthy short-term liquidity position.",
            label,
            "above",
        )
    elif pct_diff > 10:
        return (
            f"{peer} average, normal short-term liquidity position.",
            label,
            "above",
        )
    elif pct_diff >= -10:
        return (
            f"Liquidity roughly in line with {peer} peers.",
            label,
            f"near {peer} average.",
        )
    elif pct_diff >= -25:
        return (
            f"{peer} average. Adequate but tight liquidity.",
            label,
            "a little below",
        )
    elif pct_diff >= -40:
        return (
            f"{peer} average. Tight but manageable liquidity.",
            label,
            "below",
        )
    else:
        return (
            f"{peer} average. Constrained liquidity position.",
            label,
            "well below",
        )


def _generate_ic_insight(
    pct_diff: float, value: float, sector: float, peer: str = "sector",
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Interest Coverage (higher is better)."""
    label = _format_diff_label(pct_diff)

    if pct_diff > 100:
        return (
            f"{peer} average. Outsized capacity to service debt.",
            label,
            "well above",
        )
    elif pct_diff > 50:
        return (
            f"{peer} average. Strong debt service coverage.",
            label,
            "above",
        )
    elif pct_diff > 10:
        return (
            f"{peer} average. Comfortable interest coverage.",
            label,
            "above",
        )
    elif pct_diff >= -25:
        direction = "above" if pct_diff >= 0 else "below"
        return (
            f"{peer} average. Adequate coverage of interest expense.",
            label,
            direction,
        )
    elif pct_diff >= -50:
        return (
            f"{peer} average. Thin coverage of interest expense.",
            label,
            "below",
        )
    else:
        return (
            f"{peer} average. Vulnerable to interest expense pressure.",
            label,
            "well below",
        )


def _generate_qr_insight(
    pct_diff: float, value: float, sector: float, peer: str = "sector",
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Quick Ratio (higher is better)."""
    label = _format_diff_label(pct_diff)

    if pct_diff > 50:
        return (
            f"{peer} average. Strong cash-equivalent liquidity.",
            label,
            "well above",
        )
    elif pct_diff > 20:
        return (
            f"{peer} average. Healthy near-cash coverage.",
            label,
            "above",
        )
    elif pct_diff > 5:
        return (
            f"{peer} average. Solid quick-asset cushion.",
            label,
            "above",
        )
    elif pct_diff >= -10:
        direction = "above" if pct_diff >= 0 else "below"
        return (
            f"{peer} average. Quick-asset coverage in line with peers.",
            label,
            direction,
        )
    elif pct_diff >= -25:
        return (
            f"{peer} average. Thin near-cash cushion.",
            label,
            "below",
        )
    else:
        return (
            f"{peer} average. Limited quick-asset coverage.",
            label,
            "well below",
        )


def _generate_zscore_insight(
    value: float,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Altman Z-Score using absolute thresholds.

    Returns (main_text, highlighted_value, highlighted_label).
    No sector comparison — uses Altman's universal bankruptcy-risk zones.

    The zone is judged and PRINTED on the same 2-dp number. The status is judged at 2 dp
    (`_zscore_status`, the report's precision) but this used to print 1 dp, so a Z of
    3.03 read "3.0 Z-Score. Safe zone" beside the card's "1.8 – 3.0" Grey label, and
    1.83 read "1.8 · Grey zone" beside "≤ 1.8" Distress. Rounding here as well keeps the
    text self-consistent even for a caller that passes an unrounded score.
    """
    value = round(value, 2)
    formatted = f"{value:.2f}"

    if value > 4.5:
        return (
            "Fortress balance sheet. Very low bankruptcy risk.",
            formatted,
            "Z-Score.",
        )
    elif value > 3.0:
        return (
            "Safe zone. Low probability of financial distress.",
            formatted,
            "Z-Score.",
        )
    elif value > 2.5:
        return (
            "Grey zone, leaning safe. Monitor closely.",
            formatted,
            "Z-Score.",
        )
    elif value > 1.8:
        return (
            "Grey zone. Moderate financial stress signals.",
            formatted,
            "Z-Score.",
        )
    elif value > 1.0:
        return (
            "Distress zone. Elevated bankruptcy risk.",
            formatted,
            "Z-Score.",
        )
    else:
        return (
            "Deep distress. Imminent default risk.",
            formatted,
            "Z-Score.",
        )


def _sum_ttm_income(quarterly: List[Dict[str, Any]]) -> Dict[str, float]:
    """Sum the last 4 quarters of income statement into TTM totals.

    Z-Score uses EBIT and Revenue — both flow figures, so summing the
    trailing four quarters gives the correct denominator vs. the latest
    annual fiscal year (which can be 12+ months stale).
    """
    if not isinstance(quarterly, list):
        logger.warning(
            "health_check TTM: expected a list of income statements, got %s",
            type(quarterly).__name__,
        )
        return {}
    quarterly = [r for r in quarterly if isinstance(r, dict)]
    if not quarterly:
        return {}
    sorted_q = sorted(quarterly, key=lambda r: r.get("date") or "", reverse=True)[:4]
    # A TTM total needs FOUR quarters. There was no length check, so a company with fewer
    # filings — a recent IPO, or a symbol FMP has partial history for (verified live:
    # GMRS returns exactly 2 quarterly records) — had its 2-quarter EBIT and revenue
    # summed and published AS a trailing-twelve-month figure. Altman Z weights
    # ebit/assets at 3.3 and revenue/assets at 1.0, so a half-year numerator roughly
    # halves both terms and can move the verdict a whole band. `_compute_z_score` already
    # OMITS the metric when a field is None — that is the honest degradation, and this
    # makes it fire.
    if len(sorted_q) < 4:
        logger.warning(
            "health_check TTM: need 4 quarters, got %d — omitting the TTM totals rather "
            "than publishing a partial-period sum as trailing-twelve-month",
            len(sorted_q),
        )
        return {}
    summed: Dict[str, float] = {}
    for field in ("operatingIncome", "interestExpense", "revenue", "netIncome", "ebitda"):
        vals: List[float] = []
        for rec in sorted_q:
            v = _safe_float(rec, field)
            if v is None:
                vals = []
                break
            vals.append(v)
        if vals:
            summed[field] = sum(vals)
        else:
            # A partial sum would understate a TTM flow figure, so the field is
            # dropped — but SILENTLY dropping it let `(ebit or 0)` downstream
            # fabricate a 0 EBIT. Log it so the degradation is diagnosable.
            logger.warning(
                "health_check TTM: %r missing in at least one of the last %d "
                "quarters — field omitted from the TTM sum",
                field, len(sorted_q),
            )
    return summed


# Industries whose balance sheet the Altman model was never built for. Matched as whole
# words (so "Banks - Regional", "Investment - Banking & Investment Services",
# "Insurance - Life", "REIT - Retail", "Financial - Credit Services", "Financial -
# Mortgages") and only as a backstop: the sector test below already covers every
# correctly classified financial / real-estate company. It catches a misclassified row.
_ALTMAN_NA_SECTORS = frozenset({"financial services", "real estate"})
_ALTMAN_NA_INDUSTRY_RE = re.compile(
    r"\b(?:bank\w*|insurance|reits?|asset management|capital markets|credit services|"
    r"mortgages?)\b",
    re.IGNORECASE,
)


def altman_z_applicable(sector: Optional[str], industry: Optional[str]) -> bool:
    """False for banks, insurers, REITs and the rest of Financial Services / Real Estate.

    Altman's Z is a manufacturing-firm model: for a bank, liabilities ARE the operating
    funding (deposits) and there is no current-asset/current-liability split, so the
    formula lands far below 1.8 on every healthy lender. JPM-shaped inputs scored 0.4 —
    "Deep distress. Imminent default risk." — and a Realty-Income-shaped REIT scored 1.0,
    cached for 24h and fed into the Overview and report health ratings. An unknown or
    empty sector/industry is NOT a reason to omit: the score is applicable by default.
    """
    # FMP sends null for an unclassified symbol; anything that is not a string is unknown.
    sector = sector.strip() if isinstance(sector, str) else ""
    industry = industry if isinstance(industry, str) else ""
    if (_normalize_sector(sector) or "").lower() in _ALTMAN_NA_SECTORS:
        return False
    if industry and _ALTMAN_NA_INDUSTRY_RE.search(industry):
        return False
    return True


def _compute_z_score(
    bs: Dict, inc: Dict, mcap: Optional[float], *, ndigits: int = 1,
    sector: Optional[str] = None, industry: Optional[str] = None,
) -> Optional[float]:
    """Compute Altman Z-Score from balance sheet, income, and market cap.

    Returns None — so the caller OMITS the metric — whenever a term that
    materially moves the score is unavailable, rather than substituting 0.

    It also returns None when ``altman_z_applicable(sector, industry)`` is False. The gate
    lives HERE, in the one implementation, so no caller can publish a bank's Z by
    forgetting to check: every caller passes the profile's ``sector=`` / ``industry=``
    (pinned by tests/test_health_check_deepcheck_altman.py). Omitted keywords mean
    "unknown", which scores as before.

    This used to do ``0.6 * ((mcap or 0) / tl)``: a failed company-profile fetch
    (only a logger.warning upstream) silently valued the equity at ZERO. On
    Apple-shaped inputs that is Z=8.9 ("positive", fortress) vs Z=2.1
    ("neutral", "Grey zone. Moderate financial stress signals") — a wrong,
    confident verdict that also moved overall_rating and was cached for 24h.
    The same reasoning applies to EBIT and revenue, which ``_sum_ttm_income``
    drops entirely when any one quarter lacks the field.

    ⚠️ **This is the ONE implementation.** It was fixed here and three other copies were
    left carrying the original bug — `health_snapshot_service`, `stock_overview_service`
    and `agents/ticker_report_data_collector` each had their own transcription, and each
    still substituted 0 for a missing market cap / EBIT / revenue. They all delegate here
    now; `ndigits` exists so a caller can keep its own published precision (the report
    collector rounds to 2) without needing a private copy of the formula.
    `tests/test_altman_z_single_implementation.py` fails the build on a fifth copy.
    """
    if not altman_z_applicable(sector, industry):
        logger.info(
            "altman_z_score: not applicable to sector=%r industry=%r (financial / real "
            "estate balance sheet) — omitting the metric", sector, industry,
        )
        return None

    ta = _safe_float(bs, "totalAssets")
    tl = _safe_float(bs, "totalLiabilities")
    ca = _safe_float(bs, "totalCurrentAssets")
    cl = _safe_float(bs, "totalCurrentLiabilities")
    ret_earnings = _safe_float(bs, "retainedEarnings")
    ebit = _safe_float(inc, "operatingIncome")
    rev = _safe_float(inc, "revenue")

    if not ta or ta <= 0 or not tl or tl <= 0:
        return None

    # Market value of equity (0.6 weight) and the two income terms are not
    # optional inputs — a missing one changes the ZONE, not just the precision.
    missing = [
        name for name, val in (
            ("market_cap", mcap), ("ebit", ebit), ("revenue", rev),
        ) if val is None
    ]
    if missing:
        logger.warning(
            "altman_z_score: omitting metric — missing required input(s) %s "
            "(substituting 0 would fabricate a distress verdict)",
            ", ".join(missing),
        )
        return None

    # Working capital and retained earnings legitimately default to 0 when the
    # filing omits them (small/foreign issuers); they carry lower weights and a
    # 0 is the neutral value, not a distorting one.
    wc = (ca or 0) - (cl or 0)
    z = (
        1.2 * (wc / ta)
        + 1.4 * ((ret_earnings or 0) / ta)
        + 3.3 * (ebit / ta)
        + 0.6 * (mcap / tl)
        + 1.0 * (rev / ta)
    )
    return round(z, ndigits)


def _zscore_gauge(z: float) -> float:
    """Map Z-Score to 0.0–1.0 gauge using Altman's zones.

    0.0 = deep distress (Z ≤ 0), 0.5 ≈ grey zone boundary (Z = 1.8),
    1.0 = fortress (Z ≥ 4.5).
    """
    return _clamp(z / 4.5, 0.02, 0.98)


def _zscore_status(z: float) -> str:
    """Determine status from Altman Z-Score absolute thresholds.

    THE zone convention, used on every surface: Distress <= 1.8, Grey (1.8, 3.0],
    Safe > 3.0. The report collector, iOS `MetricThresholdZones.zoneIndex` and the card's
    zone labels follow it; they used to put 1.8 in Grey and 3.0 in Safe, so one company
    read "Grey zone" on the Financials tab and "Safe" in its report. Feed it the 2-dp
    score (the report's precision), never the 1-dp display value: a raw 3.04 rounded to
    3.0 first read Grey here while the report, at 2 dp, read Safe.
    """
    if z > 3.0:
        return "positive"
    elif z > 1.8:
        return "neutral"
    return "negative"


_INSIGHT_GENERATORS = {
    "debt_to_equity": _generate_de_insight,
    "pe_ratio": _generate_pe_insight,
    "roe": _generate_roe_insight,
    "current_ratio": _generate_cr_insight,
    "interest_coverage": _generate_ic_insight,
    "quick_ratio": _generate_qr_insight,
}


# ── Fallback logic when no sector benchmark ──────────────────────

def _absolute_gauge(metric_type: str, value: float) -> float:
    """Heuristic gauge position based on absolute value (no sector data)."""
    if metric_type == "debt_to_equity":
        # D/E: 0 is best (0.0), ~1 is mid (0.5), 3+ is worst (1.0)
        return _clamp(value / 3.0, 0.02, 0.98)
    elif metric_type == "pe_ratio":
        # P/E: 0-10 is cheap (0.0-0.2), 20 is mid (0.4), 50+ is high (1.0)
        return _clamp(value / 50.0, 0.02, 0.98)
    elif metric_type == "roe":
        # ROE (as %): 0 is bad (0.0), 15% is mid (0.5), 30%+ is great (1.0)
        return _clamp(value / 30.0, 0.02, 0.98)
    elif metric_type == "current_ratio":
        # CR: 0 is bad (0.0), 1.5 is mid (0.5), 3+ is great (1.0)
        return _clamp(value / 3.0, 0.02, 0.98)
    elif metric_type == "interest_coverage":
        # IC: 0 is bad (0.0), 5 is mid (0.5), 10+ is great (1.0)
        return _clamp(value / 10.0, 0.02, 0.98)
    elif metric_type == "quick_ratio":
        # QR: 0 is bad (0.0), 1.0 is mid (0.5), 2+ is great (1.0)
        return _clamp(value / 2.0, 0.02, 0.98)
    elif metric_type == "altman_z_score":
        return _zscore_gauge(value)
    return 0.5


def _absolute_status(metric_type: str, value: float) -> str:
    """Heuristic status based on absolute value (no sector data)."""
    if metric_type == "debt_to_equity":
        if value < 0.5:
            return "positive"
        elif value < 2.0:
            return "neutral"
        return "negative"
    elif metric_type == "pe_ratio":
        if value < 15:
            return "positive"
        elif value < 40:
            return "neutral"
        return "negative"
    elif metric_type == "roe":
        # value is already in percentage form
        if value > 15:
            return "positive"
        elif value > 5:
            return "neutral"
        return "negative"
    elif metric_type == "current_ratio":
        if value > 1.5:
            return "positive"
        elif value > 0.8:
            return "neutral"
        return "negative"
    elif metric_type == "interest_coverage":
        if value > 5:
            return "positive"
        elif value > 2:
            return "neutral"
        return "negative"
    elif metric_type == "quick_ratio":
        if value > 1.0:
            return "positive"
        elif value > 0.5:
            return "neutral"
        return "negative"
    elif metric_type == "altman_z_score":
        return _zscore_status(value)
    return "neutral"


def _fallback_insight(
    metric_type: str, value: float,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text when no sector benchmark is available."""
    two_decimal_types = ("debt_to_equity", "current_ratio", "quick_ratio")
    formatted = f"{value:.2f}" if metric_type in two_decimal_types else f"{value:.1f}"

    if metric_type == "debt_to_equity":
        if value < 0.5:
            return ("Low leverage indicates conservative financing.", formatted, "D/E ratio.")
        elif value < 1.0:
            return ("Moderate debt levels. Balanced capital structure.", formatted, "D/E ratio.")
        elif value < 2.0:
            return ("Meaningful leverage. Monitor debt sustainability.", formatted, "D/E ratio.")
        return ("High leverage. Elevated reliance on debt financing.", formatted, "D/E ratio.")

    elif metric_type == "pe_ratio":
        if value < 12:
            return ("Low valuation. Potential value opportunity.", formatted, "P/E ratio.")
        elif value < 20:
            return ("Reasonable valuation. Fairly priced earnings.", formatted, "P/E ratio.")
        elif value < 35:
            return ("Moderate premium. Priced for steady growth.", formatted, "P/E ratio.")
        return ("Premium valuation. High growth expectations.", formatted, "P/E ratio.")

    elif metric_type == "roe":
        if value > 25:
            return ("Strong returns on equity. Efficient capital use.", f"{value:.1f}%", "ROE.")
        elif value > 12:
            return ("Decent returns on equity. Solid profitability.", f"{value:.1f}%", "ROE.")
        elif value > 0:
            return ("Modest returns on equity. Room for improvement.", f"{value:.1f}%", "ROE.")
        return ("Negative or negligible returns on equity.", f"{value:.1f}%", "ROE.")

    elif metric_type == "current_ratio":
        if value > 2.0:
            return ("Ample short-term liquidity. Strong coverage.", formatted, "current ratio.")
        elif value > 1.2:
            return ("Healthy liquidity. Can cover short-term obligations.", formatted, "current ratio.")
        elif value > 0.8:
            return ("Tight liquidity. Adequate but limited cushion.", formatted, "current ratio.")
        return ("Low liquidity. May face short-term payment challenges.", formatted, "current ratio.")

    elif metric_type == "interest_coverage":
        if value <= 0:
            # An operating loss against real interest expense ("thin" understated it).
            return ("Operating earnings do not cover interest expense.", formatted, "interest coverage.")
        if value > 10:
            return ("Outsized capacity to service debt obligations.", formatted, "interest coverage.")
        elif value > 5:
            return ("Comfortable headroom over interest expense.", formatted, "interest coverage.")
        elif value > 2:
            return ("Adequate coverage of interest expense.", formatted, "interest coverage.")
        return ("Thin coverage. Sensitive to earnings pressure.", formatted, "interest coverage.")

    elif metric_type == "quick_ratio":
        if value > 1.5:
            return ("Ample near-cash assets relative to current obligations.", formatted, "quick ratio.")
        elif value > 1.0:
            return ("Healthy quick-asset coverage of liabilities.", formatted, "quick ratio.")
        elif value > 0.5:
            return ("Tight near-cash cushion. Limited liquidity slack.", formatted, "quick ratio.")
        return ("Limited quick-asset coverage of current liabilities.", formatted, "quick ratio.")

    elif metric_type == "altman_z_score":
        return _generate_zscore_insight(value)

    return ("", None, None)


# Metrics whose company value can legitimately cross zero (a net loss, an operating loss)
# while the peer median stays positive. D/E has its own negative-equity row and P/E <= 0
# is omitted, so only these two reach the comparison with a value <= 0.
_CROSSES_ZERO_TYPES = frozenset({"roe", "interest_coverage"})


def _crossed_zero_insight(
    metric_type: str, value: float, peer: str = "sector",
) -> Tuple[str, Optional[str], Optional[str]]:
    """Insight for a company value <= 0 against a POSITIVE peer median.

    A percent gap is meaningless here: ROE -25% vs a 12% median is pct -308, which the
    generators rendered "4.1x well below sector average", and IC -3 vs 20 rendered
    "115% well below". Say what the number means instead; the status stays "negative".
    ``peer`` names the median's level, as in the generators.
    """
    word = "Negative" if value < 0 else "Zero"
    if metric_type == "roe":
        text = ("The company is losing money on its equity." if value < 0
                else "No return on shareholder equity.")
        return (text, word, f"ROE vs a positive {peer} average.")
    text = ("Operating losses leave interest expense uncovered." if value < 0
            else "Operating earnings do not cover interest expense.")
    return (text, word, f"interest coverage vs a positive {peer} average.")


# ── Peer cells (2026-10-07) ──────────────────────────────────────────────────────────
# `get_current_benchmarks` answers {metric: cell | None}, a cell carrying value / level /
# peer_group_name / n. The flat `get_current_benchmark_values` dropped the level, so the
# card could not say whose median it compared against and always said "sector".
_PEER_LEVELS = frozenset({"industry", "sector"})


def _peer_cell_value(metric: str, cell: Any) -> Optional[float]:
    """The cell's median as a finite float, else None (no benchmark). A NaN / inf / bool /
    non-numeric value is refused with a WARNING: NaN slips every `<=` floor check below
    and turned the whole response into a JSON encode failure."""
    if cell is None:
        return None
    if not isinstance(cell, dict):
        logger.warning(
            "Health check peer cell for %s is a %s, not a dict — treated as no benchmark",
            metric, type(cell).__name__,
        )
        return None
    raw = cell.get("value")
    if raw is None:
        return None
    if isinstance(raw, bool):
        logger.warning("Health check peer median for %s is a bool (%r) — no benchmark",
                       metric, raw)
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        logger.warning("Health check peer median for %s is not a number (%r) — no benchmark",
                       metric, raw)
        return None
    if not math.isfinite(val):
        logger.warning("Health check peer median for %s is not finite (%r) — no benchmark",
                       metric, raw)
        return None
    return val


def _peer_cell_level(metric: str, cell: Any) -> Optional[str]:
    """"industry" | "sector" for a cell that has one, else None (logged: every cell the
    lookup builds carries its level, so a missing one is a contract drift)."""
    if not isinstance(cell, dict):
        return None
    level = cell.get("level")
    if level in _PEER_LEVELS:
        return level
    logger.warning(
        "Health check peer cell for %s has level %r (want industry/sector) — the comparison "
        "is kept, its level is reported as unknown", metric, level,
    )
    return None


def _peer_noun(level: Optional[str]) -> str:
    """The word the insight text uses for the median's level. An unknown level keeps the
    pre-2026-10-07 wording ("sector"), which is also what iOS falls back to."""
    return "industry" if level == "industry" else "sector"


# ── Liquidity / coverage ratios on a financial balance sheet (2026-10-07) ───────────
# Interest coverage, current ratio and quick ratio assume an OPERATING company. A bank,
# insurer, broker-dealer, asset manager or lender has no current/non-current split on its
# balance sheet (FMP's "current" figures for it are a classification artefact), and its
# interest expense is the cost of the money it lends or invests — not a debt-service
# burden on operating earnings. Production rows showed it: C read all three red, BAC a
# 0.54 current ratio as "positive". Those rows are OMITTED (not shown, not scored), the
# way `altman_z_applicable` omits Altman Z — for a NARROWER set, named industry by
# industry, in `app/services/financials_metric_gate.py` (shared with the snapshot cards
# and the benchmark producer): Financial Services also holds the data vendors and
# exchanges ("Financial - Data & Stock Exchanges": SPGI, MCO, MSCI) and shell companies,
# and an equity REIT's interest coverage is the number its lenders watch. Those keep the
# three rows, deliberately. Insurance brokers keep interest coverage and lose only the
# current / quick ratio (review round 2: their fiduciary funds distort only those two).
# "Financial - Credit Services" is MIXED (2026-10-08, owner decision 3; widened 2026-10-09):
# a listed non-lender member in it (`financials_metric_gate.NON_LENDER_MEMBERS`: the
# payment networks V, MA, PYPL, WU, GPN and the fee businesses TREE, PMTS, unless its own
# income reads as a lender's) keeps the three rows, and EVERY row of it — D/E, P/E and ROE
# included — is judged on absolute bands only; every other member — a lender, an unlisted
# company, a build with no ticker — does not (`resolve_payment_network` /
# `omitted_financial_rows(industry, network=..., ticker=...)`). A curated per-company fact
# withholds rows whatever the industry (`CURATED_WITHHELD_ROWS`: WU's current ratio, quick
# ratio and interest coverage).
# `tests/test_health_check_2026_10_07*.py` walks every Financial Services and Real Estate
# industry in the universe files, so a new FMP industry name fails the build until someone
# decides which side it is on.
# Kept importable from this module under their pre-gate names (imported above; nothing
# here calls them any more).
_GATE_REEXPORTS = (
    _LIQUIDITY_NA_INDUSTRIES, _industry_key, interest_coverage_applicable,
    liquidity_ratios_applicable,
)

# PERMANENT rule (owner decision 2026-10-08, review round 3, R3-CARDS-5): the Financial
# Services SECTOR median of current ratio, quick ratio and interest coverage is never a
# peer group, so a financial company is never compared with it. Before the gated producer
# rebuilt it, that aggregate was ~95% banks, insurers, asset managers, lenders and
# capital-markets firms — the values the gate above calls meaningless (review 2026-10-07,
# HC-2): SPGI's interest coverage of ~20x read "well above sector average" against banks'
# funding-cost coverage of ~1. AFTER the rebuild (`industry_benchmark_service` pools only
# the industries where these metrics mean something) it is still no peer group: what is
# left of Financial Services for these three metrics is exchanges and data vendors plus a
# shell company or two (and insurance brokers, for interest coverage) — on the US-only
# universe of 2026-10-08 about a dozen unrelated businesses. So this guard does NOT
# go away once the producer has rebuilt the rows. Such a cell is no comparison: absolute
# heuristics, `peer_level` None. An INDUSTRY cell is the company's own peers and is kept
# (an exchange is compared with exchanges once its industry has a mature cell); every
# other sector is unaffected. The producer keeps pooling these industries into the FS
# sector rows (no new exclusions there): the refusal lives here, at the reader.
_BANK_POOLED_SECTORS = frozenset({"financial services"})


def _bank_pooled_sector_cell(metric: str, level: Optional[str], sector: Any) -> bool:
    """True when ``metric``'s peer cell is the Financial Services SECTOR median of a gated
    metric (current ratio, quick ratio, interest coverage): never a comparison (see the
    comment above — permanent, not a stop-gap until a producer rebuild; the name is kept
    because other modules import it). ``sector`` is the company's sector, raw or
    normalised; a non-string is unknown."""
    if metric not in GATED_METRICS or level != "sector" or not isinstance(sector, str):
        return False
    return (_normalize_sector(sector.strip()) or "").lower() in _BANK_POOLED_SECTORS


def omitted_financial_rows(
    industry: Optional[str], *, network: bool = False, ticker: Any,
) -> frozenset:
    """The Health Check metric types omitted for a company in ``industry``
    (`financials_metric_gate`): current + quick ratio and interest coverage for banks,
    insurers, capital-markets firms, asset managers and lenders; current + quick ratio only
    for insurance brokers; none for every other industry, and for an unknown or empty one.

    In a MIXED industry ("Financial - Credit Services") the company decides: ``network``
    True (a listed non-lender member, `financials_metric_gate.resolve_payment_network`)
    keeps all three; False — the default, a lender, an unlisted member or no ticker —
    omits them, as before. ``ticker`` (REQUIRED keyword; None = no ticker) adds the rows a
    curated per-company fact withholds in every industry (`CURATED_WITHHELD_ROWS`: WU)."""
    return frozenset(
        m for m in GATED_METRICS
        if not company_metric_applicable(m, industry, network=network, ticker=ticker)
    )


def _overall_rating(passed: int, total: int) -> str:
    """Rate based on pass ratio so fewer-than-4 metrics still score fairly."""
    if total == 0:
        return "mix"
    ratio = passed / total
    if ratio >= 1.0:
        return "excellent"
    elif ratio >= 0.75:
        return "good"
    elif ratio >= 0.5:
        return "mix"
    elif ratio >= 0.25:
        return "caution"
    else:
        return "poor"


# ── Service ───────────────────────────────────────────────────────

class HealthCheckService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_health_check(self, ticker: str) -> HealthCheckResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"health_check:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Health check in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Health check Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Health check in-flight JOIN for {ticker}")
            return await asyncio.shield(_inflight[cache_key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Health check cache MISS for {ticker} — fetching from FMP")
            build = await self._build_health_check_full(ticker)
            result, next_earnings = build.response, build.next_earnings
            # A fund's empty build (see `_HcBuild`) is its permanent answer, not an outage.
            # Re-checked against what is actually served.
            fund_shape = build.fund_shape and _is_fund_shape(result)

            # NEVER persist a degraded build. Every FMP exception in the fan-out is
            # converted to [] / {} by `return_exceptions=True`, so a single 429 on one
            # call yields a structurally valid response full of zeros — and writing that
            # to the 24-hour Supabase tier pins a FABRICATED verdict on this ticker for
            # a day, for every user, and freezes it into the 20-credit report. The
            # 5-minute in-memory tier still absorbs a retry storm, so the cost of
            # skipping is one extra fan-out. Mirrors profit_power_service's gate.
            #
            # The gate used to be "no metrics survived". A 429 on /ratios-ttm alone drops
            # D/E, P/E, CR, IC and QR but leaves ROE and Z, so a 2-metric "Excellent
            # [2/2]" was persisted for 24h. `degraded` now names every failed leg, and it
            # rides on the response so health_snapshot (and iOS) can see it too.
            #
            # The one exception is a fund's empty build (`fund_shape`): `no_metrics` is what
            # a fund IS, so it is persisted, marked, and still served as `no_metrics`.
            hc_degraded = list(getattr(result, "degraded", None) or [])
            if hc_degraded and not fund_shape:
                logger.warning(
                    "Health check NOT persisted for %s (degraded: %s) — will rebuild "
                    "after the in-memory TTL", ticker, ", ".join(hc_degraded),
                )
            elif next_earnings is CALENDAR_UNKNOWN:
                # The values are complete — the calendar feeds none of them — but the row
                # would carry no report-day bound and could outlive a release by up to 24h.
                logger.warning(
                    "Health check NOT persisted for %s (earnings calendar unavailable — no "
                    "report-day bound for the 24h row); served from memory, rebuilt after "
                    "the in-memory TTL", ticker,
                )
            else:
                if fund_shape:
                    logger.info("[fund-shape] ticker=%s step=%s", ticker, "health_check_write")
                # Persist to Supabase in background (fire-and-forget). `fund_shape` is
                # POSITIONAL: `run_in_executor` forwards no keywords.
                asyncio.get_running_loop().run_in_executor(
                    None,
                    self._upsert_supabase_cache_safe,
                    ticker,
                    result,
                    next_earnings,
                    fund_shape,
                )

            _cache_set(cache_key, result)
            if not future.done():
                future.set_result(result)
            return result
        except asyncio.CancelledError:
            # CancelledError is a BaseException, NOT an Exception, so it skips the handler
            # below and used to leave this future unresolved forever — every joiner attached
            # via `await _inflight[...]` then hung for the life of the process. Reachable
            # whenever the LEADER is a cancellable caller: a report run hitting
            # RESEARCH_PIPELINE_TIMEOUT_SECONDS, or any pre-warm task cancelled at shutdown.
            # Hand waiters a normal exception so they fail fast through their own error path.
            fail_shared_future(future, RuntimeError("in-flight fetch was cancelled"))
            raise
        except Exception as e:
            fail_shared_future(future, e)
            raise
        finally:
            _inflight.pop(cache_key, None)

    # ── Supabase helpers ──────────────────────────────────────────

    def _check_supabase_cache(self, ticker: str) -> Optional[HealthCheckResponse]:
        try:
            row = (
                self.supabase.table("health_check_cache")
                .select("response_json, cached_at, next_earnings_date")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None

            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Supabase cache STALE (age={age}) for {ticker}")
                return None

            next_earnings = entry.get("next_earnings_date")
            if next_earnings:
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today_str >= next_earnings:
                    logger.info(f"Supabase cache STALE (past earnings {next_earnings}) for {ticker}")
                    return None

            json_data = dict(entry.get("response_json") or {})
            # A VERSION, not a key probe: rows written under the old rules (a bank's
            # "Deep distress" Z, a negative-equity ROE read as "Exceptional") carry no
            # field this code could test for. They are rebuilt on their next read.
            version = json_data.pop(_HC_VERSION_KEY, 1)
            # Not a response field either (see `_SECURITY_KIND_KEY`).
            security_kind = json_data.pop(_SECURITY_KIND_KEY, None)
            if version != _HC_PAYLOAD_VERSION:
                logger.info(
                    "Health check Supabase cache STALE for %s (payload_version=%r, want %d) "
                    "— recomputing", ticker, version, _HC_PAYLOAD_VERSION,
                )
                return None
            resp = HealthCheckResponse(**json_data)
            if resp.degraded:
                # The writer refuses every degraded build but a fund's empty one, which it
                # marks. Anything else that has reasons was written by hand or by a bug —
                # do not serve it; nor a marked row that is not exactly the fund shape.
                if security_kind != _SECURITY_KIND_FUND or not _is_fund_shape(resp):
                    logger.warning(
                        "Health check Supabase row for %s carries degraded=%r "
                        "(security_kind=%r, %d metric(s)) — ignoring it",
                        ticker, resp.degraded, security_kind, len(resp.metrics),
                    )
                    return None
                logger.info("[fund-shape] ticker=%s step=%s", ticker, "health_check_read")
            return resp

        except Exception as e:
            logger.warning(
                "Supabase cache check failed for health_check %s: %s: %s",
                ticker, type(e).__name__, e,
            )
            return None

    def _upsert_supabase_cache_safe(
        self,
        ticker: str,
        result: HealthCheckResponse,
        next_earnings: EarningsStamp,
        fund_shape: bool = False,
    ) -> None:
        """``fund_shape`` is the getter's verdict (`_HcBuild.fund_shape`); it stamps the
        `security_kind: "fund"` marker the reader needs to admit the `no_metrics` build —
        and only onto a result that really is the fund shape."""
        if not stamp_is_persistable(next_earnings):
            # Belt-and-braces: the getter already refuses a CALENDAR_UNKNOWN build.
            logger.warning(
                "Health check upsert REFUSED for %s — next_earnings_date %r is not a date "
                "(an unread earnings calendar is never persisted)", ticker, next_earnings,
            )
            return
        try:
            response_json: Dict[str, Any] = {
                **result.model_dump(), _HC_VERSION_KEY: _HC_PAYLOAD_VERSION,
            }
            if fund_shape:
                if _is_fund_shape(result):
                    response_json[_SECURITY_KIND_KEY] = _SECURITY_KIND_FUND
                else:
                    logger.warning(
                        "[fund-shape-refused] ticker=%s step=health_check_write: a fund "
                        "verdict came with a build that is not the fund shape (degraded=%r, "
                        "%d metric(s)) — written WITHOUT the fund marker",
                        ticker, result.degraded, len(result.metrics),
                    )
            self.supabase.table("health_check_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": response_json,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": next_earnings,
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(
                "Supabase upsert failed for health_check %s: %s: %s",
                ticker, type(e).__name__, e,
            )

    # ── Builder ───────────────────────────────────────────────────

    async def _build_health_check(
        self, ticker: str
    ) -> Tuple[HealthCheckResponse, EarningsStamp]:
        """``(response, next_earnings_date)``. A thin adapter over
        `_build_health_check_full` (which also carries the fund verdict the getter needs);
        the 2-tuple is kept because callers unpack it."""
        build = await self._build_health_check_full(ticker)
        return build.response, build.next_earnings

    async def _build_health_check_full(self, ticker: str) -> _HcBuild:
        # Phase 1: parallel FMP fetch. TTM endpoints for ratios + key-metrics
        # so D/E, P/E, ROE reflect the trailing twelve months instead of an
        # up-to-12-months-stale fiscal year-end. Balance sheet uses the latest
        # quarterly filing (point-in-time = the most recent number available).
        # Income statement is fetched as 4 quarters to support TTM Z-Score
        # later in this method.
        profile, ratios_list, key_metrics_list, ec_raw, bs_raw, inc_raw = await asyncio.gather(
            self.fmp.get_company_profile(ticker),
            self.fmp.get_ratios_ttm(ticker),
            self.fmp.get_key_metrics_ttm(ticker),
            # raise_errors=True: the default swallows a 429 / 5xx / non-list body into
            # [] ("no announcements"), and the row was persisted with no report-day bound.
            self.fmp.get_earning_calendar_full(ticker, raise_errors=True),
            self.fmp.get_balance_sheet(ticker, period="quarter", limit=1),
            self.fmp.get_income_statement(ticker, period="quarter", limit=4),
            return_exceptions=True,
        )

        # Fund-shape inputs (`_HcBuild.fund_shape`), read from the RAW answers before the
        # substitutions below: a leg that raised (even a permanent not-entitled one) or
        # answered an error dict (coerced to {} / [] further down WITHOUT a reason) is never
        # a fund's empty answer. The profile must be a dict that POSITIVELY says fund.
        raw_profile_is_fund = profile_is_fund(profile)
        raw_legs_answered_lists = all(
            isinstance(leg, list) for leg in (ratios_list, key_metrics_list, bs_raw, inc_raw)
        )

        # Every leg that failed TRANSIENTLY. Each one is replaced by an empty default and
        # the build still renders, which is exactly why it must not reach the 24h tier
        # (see get_health_check). A permanent FMPNotEntitledException is not degradation:
        # that slice will not come back on retry, so the build without it is the answer.
        # The earnings calendar is not a leg: it feeds no served value, only the cache
        # row's next-earnings stamp. A failed calendar is CALENDAR_UNKNOWN (below), which
        # the getter serves from memory but never persists.
        degraded: List[str] = []
        for leg_name, leg in (
            ("profile", profile), ("ratios", ratios_list), ("key_metrics", key_metrics_list),
            ("balance_sheet", bs_raw), ("income", inc_raw),
        ):
            if isinstance(leg, Exception) and not isinstance(leg, FMPNotEntitledException):
                degraded.append(leg_name)

        if isinstance(profile, Exception):
            logger.warning(f"Profile fetch failed for {ticker}: {type(profile).__name__}: {profile}")
            profile = {}
        if isinstance(ratios_list, Exception):
            logger.error(f"Ratios fetch failed for {ticker}: {type(ratios_list).__name__}: {ratios_list}")
            ratios_list = []
        if isinstance(key_metrics_list, Exception):
            logger.error(
                f"Key metrics fetch failed for {ticker}: "
                f"{type(key_metrics_list).__name__}: {key_metrics_list}"
            )
            key_metrics_list = []
        # The calendar's only use: the cache row's next-earnings stamp (or CALENDAR_UNKNOWN).
        next_earnings = next_earnings_stamp(ec_raw, ticker=ticker, service="health_check")
        ec_raw = []
        if isinstance(bs_raw, Exception):
            logger.warning(f"Balance sheet fetch failed for {ticker}: {type(bs_raw).__name__}: {bs_raw}")
            bs_raw = []
        if isinstance(inc_raw, Exception):
            logger.warning(f"Income statement fetch failed for {ticker}: {type(inc_raw).__name__}: {inc_raw}")
            inc_raw = []

        # Phase 2: extract company ratios from both sources
        ratios = ratios_list[0] if isinstance(ratios_list, list) and ratios_list else {}
        key_metrics = key_metrics_list[0] if isinstance(key_metrics_list, list) and key_metrics_list else {}
        balance_sheet = bs_raw[0] if isinstance(bs_raw, list) and bs_raw else {}
        if not isinstance(ratios, dict):
            ratios = {}
        if not isinstance(key_metrics, dict):
            key_metrics = {}
        if not isinstance(balance_sheet, dict):
            balance_sheet = {}
        # Sum 4 quarters into TTM so Z-Score's EBIT and Revenue inputs
        # reflect the trailing twelve months, not the latest single quarter.
        income_stmt = _sum_ttm_income(inc_raw) if isinstance(inc_raw, list) else {}

        # Extract market cap for Z-Score
        profile_data = profile if isinstance(profile, dict) else (profile[0] if isinstance(profile, list) and profile else {})
        mcap = _safe_float(profile_data, "mktCap")
        if mcap is None:
            mcap = _safe_float(profile_data, "marketCap")

        # Phase 3: get sector and look up benchmarks
        raw_sector = profile.get("sector", "") if isinstance(profile, dict) else ""
        sector = _normalize_sector(raw_sector)
        # Industry-relative: prefer INDUSTRY peers, fall back to sector per cell.
        industry = profile.get("industry", "") if isinstance(profile, dict) else ""
        logger.info(f"Health check {ticker}: raw_sector={raw_sector!r}, normalized={sector!r}, industry={industry!r}")

        # Banks, insurers, capital-markets firms, asset managers, lenders: no interest
        # coverage, current or quick ratio; insurance brokers: no current or quick ratio
        # (`omitted_financial_rows`, from `financials_metric_gate`). Decided once, logged once.
        # In a MIXED industry (payment networks and fee businesses beside lenders) only a
        # listed non-lender member — `NON_LENDER_MEMBERS`: a payment network (V, MA, PYPL,
        # WU, GPN) or a non-lender fee business (TREE, PMTS) — keeps them, unless a curated
        # fact withholds one (`resolve_payment_network`: the ticker is on the curated list
        # and its trailing-four-quarter income, from the quarters already fetched — no extra
        # FMP call — does not read as a lender's; unreadable income leaves the list's
        # answer standing, and a failed income leg already marks the build degraded).
        network = resolve_payment_network(
            ticker, industry, trailing_interest_row(inc_raw), source="health_check",
        )
        # A curated per-company fact (WU: no current/non-current split in its own filings;
        # FMP's interest expense is not its own) withholds rows whatever the industry —
        # logged once here, applied by `omitted_financial_rows` below.
        curated_withheld = resolve_withheld_company_rows(ticker, source="health_check")
        omitted_rows = omitted_financial_rows(industry, network=network, ticker=ticker)
        industry_omitted = omitted_rows - curated_withheld
        if industry_omitted:
            logger.info(
                "Health check %s: %s omitted — not meaningful for industry=%r (a financial "
                "balance sheet has no current/non-current split, and a lender's interest "
                "expense is a funding cost)",
                ticker, ", ".join(sorted(industry_omitted)), industry,
            )
        # A kept row whose peer median is no peer group — EVERY row of a listed non-lender
        # member of a mixed industry (V's P/E, ROE, D/E and liquidity rows against a median
        # of lenders), and the liquidity / coverage rows of a gated industry: judged on the
        # absolute bands, never compared, so its median is not even asked for
        # (`peer_median_comparable`).
        absolute_only = sorted(
            mdef["type"] for mdef in METRIC_DEFS
            if mdef["benchmark_name"] and mdef["type"] not in omitted_rows
            and not peer_median_comparable(mdef["type"], industry, network=network)
        )
        if absolute_only:
            logger.info(
                "Health check %s: %s judged on absolute bands only — no peer median of "
                "industry=%r is a comparison for this company (a gated ratio, or a listed "
                "non-lender member against a lenders' median)",
                ticker, ", ".join(absolute_only), industry,
            )
        bench_metrics = [
            mdef["benchmark_name"] for mdef in METRIC_DEFS
            if mdef["benchmark_name"] and mdef["type"] not in omitted_rows
            and mdef["type"] not in absolute_only
        ]

        # CURRENT peer cell per metric (`get_current_benchmarks`): the industry TTM median
        # when mature, else the sector TTM, else the newest complete mature annual year,
        # else None. Its value is compared; its level is reported (`peer_level`) and named
        # in the text.
        cur_bench: Dict[str, Optional[float]] = {}
        cur_levels: Dict[str, Optional[str]] = {}
        if sector and not bench_metrics:
            # Nothing is comparable (a listed non-lender member: every row on absolute
            # bands). No lookup at all — a failed read could only mark a peer-free build
            # degraded (served, never persisted, rebuilt from FMP on every view) for
            # medians it would never use.
            logger.info(
                "Health check %s: no metric is compared with a peer median (industry=%r) — "
                "no benchmark lookup; every row on absolute bands", ticker, industry,
            )
        elif sector:
            lookup = get_sector_benchmark_lookup()
            try:
                # Sync lookup (supabase-py + time.sleep retry): keep it off the loop.
                cur_cells = await asyncio.to_thread(
                    lookup.get_current_benchmarks, industry, sector, bench_metrics,
                )
            except Exception as e:
                # A lookup FAILURE is not the same answer as "this peer group has no
                # rows": every metric falls back to the absolute heuristics, which is a
                # fine thing to SERVE and a wrong thing to pin for 24h.
                logger.warning(
                    "Health check %s: benchmark lookup failed (%s: %s) — absolute "
                    "heuristics, build marked degraded", ticker, type(e).__name__, e,
                )
                cur_cells = {}
                degraded.append("benchmarks")
            else:
                # The lookup SWALLOWS a DB error into the empty shape; it flags that
                # shape so a transient failure is not persisted as "no peer group".
                if lookup_failed(cur_cells):
                    logger.warning(
                        "Health check %s: benchmark lookup returned a FAILED shape — "
                        "absolute heuristics, build marked degraded", ticker,
                    )
                    degraded.append("benchmarks")
            if not isinstance(cur_cells, dict):
                logger.warning(
                    "Health check %s: benchmark lookup answered a %s, not a dict — "
                    "absolute heuristics", ticker, type(cur_cells).__name__,
                )
                cur_cells = {}
            for bm_name in bench_metrics:
                cell = cur_cells.get(bm_name)
                cur_bench[bm_name] = _peer_cell_value(bm_name, cell)
                cur_levels[bm_name] = (
                    _peer_cell_level(bm_name, cell) if cur_bench[bm_name] is not None else None
                )
                if cur_bench[bm_name] is not None and _bank_pooled_sector_cell(
                    bm_name, cur_levels[bm_name], sector,
                ):
                    logger.info(
                        "Health check %s: %s peer median %r is the Financial Services "
                        "SECTOR's (industry=%r has no mature cell) — that aggregate is no "
                        "peer group for this ratio (banks/insurers, or shells, exchanges "
                        "and developers once they are excluded): absolute heuristics",
                        ticker, bm_name, cur_bench[bm_name], industry,
                    )
                    cur_bench[bm_name] = None
                    cur_levels[bm_name] = None
            logger.info(
                f"Health check {ticker}: current benchmarks={cur_bench} levels={cur_levels}"
            )
            for bm_name, bm_val in cur_bench.items():
                if bm_val is None:
                    logger.warning(f"Health check {ticker}: NO benchmark data for {bm_name}")
        else:
            logger.warning(f"Health check {ticker}: no sector found, skipping benchmark lookup")

        # Pre-compute Altman Z-Score for use in the metric loop. Two decimals: the status,
        # gauge and insight are judged on the same precision the report uses, so the
        # two surfaces cannot disagree about a Z of 3.04 (see _zscore_status). The
        # sector/industry gate inside omits it for banks, insurers and REITs.
        z_score_val = _compute_z_score(
            balance_sheet, income_stmt, mcap, ndigits=2,
            sector=raw_sector, industry=industry,
        )

        # Negative shareholder equity, decided ONCE before the loop. FMP's ROE is net
        # income / equity, so a negative denominator flips its sign: a loss-maker
        # (Boeing-shaped, ROE +303%) read "Exceptional capital efficiency" and a
        # profitable buyback-heavy company (McDonald's-shaped, ROE -216%) read
        # "Significantly underperforming", while the same card's D/E row said
        # "Negative equity". D/E comes from the ratios leg, which can fail on its own,
        # so the balance sheet is a second witness.
        de_ratio = _safe_float(ratios, "debtToEquityRatioTTM")
        if de_ratio is None:
            de_ratio = _safe_float(ratios, "debtToEquityRatio")
        bs_equity = _safe_float(balance_sheet, "totalStockholdersEquity")
        # A strictly negative witness is always believed (ratios-TTM can lag a new
        # quarter that turned equity negative — that is what the second witness is for).
        # A balance-sheet equity of EXACTLY 0 is believed only when D/E does not
        # contradict it: FMP zero-fills unreported statement fields, and a 0 next to a
        # positive D/E (equity clearly positive in the ratios leg) used to mark a 30% ROE
        # "N/M … equity is negative" beside "Healthy debt position", dropping a real pass.
        de_negative = de_ratio is not None and de_ratio < 0
        bs_negative = bs_equity is not None and bs_equity < 0
        bs_zero = bs_equity is not None and bs_equity == 0
        zero_equity = bs_zero and (de_ratio is None or de_ratio <= 0)
        if bs_zero and not zero_equity:
            logger.warning(
                f"Health check {ticker}: balance-sheet totalStockholdersEquity is 0 but "
                f"D/E={de_ratio!r} is positive — treating the 0 as unreported, ROE judged"
            )
        negative_equity = de_negative or bs_negative or zero_equity
        # The N/M row says which it is: "negative" only on a strictly negative witness.
        equity_state = "negative" if (de_negative or bs_negative) else "reported as zero"

        # Phase 4: build each metric
        metrics: List[HealthCheckMetricSchema] = []
        # Rows shown on the card but left out of passed / neutral / total: a value with
        # no meaning cannot pass or fail (and half credit for "neutral" would still
        # move the rating).
        unscored_types: set = set()
        for mdef in METRIC_DEFS:
            # Altman Z-Score is computed separately, not from a single FMP field
            if mdef["type"] == "altman_z_score":
                if z_score_val is None:
                    if not altman_z_applicable(raw_sector, industry):
                        logger.info(
                            f"Health check {ticker}: altman_z_score omitted — not "
                            f"applicable to sector={raw_sector!r} industry={industry!r}"
                        )
                    else:
                        logger.warning(f"Health check {ticker}: altman_z_score — insufficient data to compute")
                    continue

                gauge = _zscore_gauge(z_score_val)
                status = _zscore_status(z_score_val)
                insight_text, highlighted_value, highlighted_label = _generate_zscore_insight(z_score_val)

                metrics.append(
                    HealthCheckMetricSchema(
                        type="altman_z_score",
                        # 2 dp, like the status and the insight's printed score: iOS
                        # places the zone-gauge marker from it, and its header must print
                        # it at 2 dp too, or a 3.03 reads "3.0 · Safe zone" under a
                        # "1.8 – 3.0" Grey label.
                        value=round(z_score_val, 2),
                        comparison_value=None,
                        percent_difference=None,
                        gauge_position=round(gauge, 2),
                        status=status,
                        insight_text=insight_text,
                        highlighted_value=highlighted_value,
                        highlighted_label=highlighted_label,
                    )
                )
                continue

            # Bank / insurer / capital-markets / broker row: omitted, not judged (logged
            # once above).
            if mdef["type"] in omitted_rows:
                continue

            source = ratios if mdef["source"] == "ratios" else key_metrics
            # /ratios-ttm and /key-metrics-ttm return field names suffixed
            # with "TTM"; try the TTM-suffixed name first, then the legacy
            # bare name so this still works if FMP rolls the schema back.
            ttm_field = f"{mdef['fmp_field']}TTM"
            company_val = _safe_float(source, ttm_field)
            if company_val is None:
                company_val = _safe_float(source, mdef["fmp_field"])
            if company_val is None:
                logger.warning(
                    f"Health check {ticker}: {mdef['type']} — FMP field "
                    f"{mdef['fmp_field']!r} not found in {mdef['source']} response"
                )
                continue

            # Skip a P/E of zero or below: a loss-maker's ratio is meaningless, and FMP
            # reports 0 when there are no earnings. The benchmark medians drop P/E <= 0
            # too (positive_only), so a 0 compared against them read "Deep value
            # opportunity, 100% below".
            if mdef["type"] == "pe_ratio" and company_val <= 0:
                continue

            # Interest coverage of exactly 0 with no interest expense on the books is how
            # FMP reports a company with nothing to cover (the benchmark side drops it,
            # positive_only). It used to score as a fail — "Vulnerable to interest
            # expense pressure" — on a debt-free balance sheet. Omitted rather than
            # passed: zero reported interest expense does not prove there is no debt
            # (some issuers net interest into other income). With interest expense on
            # the books, a 0 is a real operating-loss reading and stays below.
            if mdef["type"] == "interest_coverage" and company_val == 0:
                ttm_interest = income_stmt.get("interestExpense")
                if ttm_interest is None or ttm_interest == 0:
                    logger.warning(
                        f"Health check {ticker}: interest_coverage is 0 with no TTM "
                        f"interest expense ({ttm_interest!r}) — omitting the metric"
                    )
                    continue

            # Negative D/E means negative equity — force to worst-case
            if mdef["type"] == "debt_to_equity" and company_val < 0:
                metrics.append(
                    HealthCheckMetricSchema(
                        type="debt_to_equity",
                        value=round(company_val, 2),
                        comparison_value=None,
                        percent_difference=None,
                        gauge_position=0.98,
                        status="negative",
                        # iOS renders "{value} {label} {text}" — the highlight already
                        # says "Negative shareholder equity.", so the text must not
                        # repeat it ("Negative shareholder equity. Negative equity. …").
                        insight_text="Liabilities exceed total assets.",
                        highlighted_value="Negative",
                        highlighted_label="shareholder equity.",
                    )
                )
                continue

            # Convert ROE from decimal to percentage for display
            display_val = company_val
            if mdef["is_percentage"]:
                display_val = round(company_val * 100, 2)

            # ROE on negative equity: shown, never judged (see `negative_equity`).
            if mdef["type"] == "roe" and negative_equity:
                logger.info(
                    f"Health check {ticker}: roe {display_val}% is not meaningful — "
                    f"shareholder equity {equity_state} (D/E={de_ratio!r}, "
                    f"equity={bs_equity!r}) — shown as N/M, left out of the score"
                )
                metrics.append(
                    HealthCheckMetricSchema(
                        type="roe",
                        value=round(display_val, 2),
                        comparison_value=None,
                        percent_difference=None,
                        gauge_position=0.5,
                        status="neutral",
                        insight_text=f"Not meaningful: shareholder equity is {equity_state}.",
                        highlighted_value=NOT_MEANINGFUL,
                        highlighted_label="ROE.",
                    )
                )
                unscored_types.add("roe")
                continue

            # CURRENT sector/industry benchmark (TTM row if present, else latest
            # mature annual value) for this metric.
            sector_val = cur_bench.get(mdef["benchmark_name"])
            median_floor = _min_usable_median(mdef["type"])
            if sector_val is not None and sector_val <= median_floor:
                # A peer median at or below zero is no benchmark. Dividing by its
                # absolute value FLIPPED the verdict: in a loss-making industry (ROE
                # median -30%) a company at -10% read "+67% above sector. Strong capital
                # efficiency.", while the gauge — which already refused a non-positive
                # anchor — sat in the red. A barely-positive median is no anchor either
                # (`_MIN_USABLE_MEDIAN`). Status, gauge, text and the iOS peer tick
                # all fall back to the absolute heuristics together.
                logger.info(
                    f"Health check {ticker}: {mdef['type']} peer median {sector_val!r} "
                    f"is at or below the usable floor {median_floor!r} — treating as "
                    f"no benchmark"
                )
                sector_val = None
            # The level of the median actually compared against; None with no comparison.
            peer_level = (
                cur_levels.get(mdef["benchmark_name"]) if sector_val is not None else None
            )
            peer = _peer_noun(peer_level)
            sector_display = None
            if sector_val is not None:
                if mdef["is_percentage"]:
                    sector_display = round(sector_val * 100, 2)
                else:
                    sector_display = round(sector_val, 2)

            # Calculate percent difference
            pct_diff = None
            if sector_val is not None:
                pct_diff = round((company_val - sector_val) / abs(sector_val) * 100, 1)

            # Gauge position (uses raw values, not display values)
            if sector_val is not None:
                gauge = _gauge_position(company_val, sector_val)
            else:
                # No sector benchmark — use absolute-value heuristic
                # Pass display_val so ROE is in % form for correct thresholds
                gauge = _absolute_gauge(mdef["type"], display_val)

            if (
                pct_diff is not None
                and mdef["type"] in _CROSSES_ZERO_TYPES
                and company_val <= 0
            ):
                # Crossed zero against a positive median: a percent gap has no meaning
                # (see _crossed_zero_insight). Keep the comparison value — the median is
                # real context — but drop the percentage.
                status = "negative"
                insight_text, highlighted_value, highlighted_label = _crossed_zero_insight(
                    mdef["type"], display_val, peer=peer,
                )
                pct_diff = None
            elif pct_diff is not None:
                status = _determine_status(
                    mdef["type"], pct_diff, mdef["lower_is_better"]
                )
                gen = _INSIGHT_GENERATORS[mdef["type"]]
                insight_text, highlighted_value, highlighted_label = gen(
                    pct_diff, display_val, sector_display or 0, peer=peer,
                )
            else:
                # No sector benchmark — absolute-value heuristic and its fallback text
                status = _absolute_status(mdef["type"], display_val)
                insight_text, highlighted_value, highlighted_label = _fallback_insight(
                    mdef["type"], display_val,
                )

            metrics.append(
                HealthCheckMetricSchema(
                    type=mdef["type"],
                    value=round(display_val, 2),
                    comparison_value=sector_display,
                    percent_difference=pct_diff,
                    gauge_position=round(gauge, 2),
                    status=status,
                    insight_text=insight_text,
                    highlighted_value=highlighted_value,
                    highlighted_label=highlighted_label,
                    peer_level=peer_level,
                )
            )

        # Phase 5: overall rating, over the SCORED metrics only
        scored = [m for m in metrics if m.type not in unscored_types]
        passed = sum(1 for m in scored if m.status == "positive")
        neutrals = sum(1 for m in scored if m.status == "neutral")
        total = len(scored)
        # Neutral (in-line-with-sector) metrics get HALF credit in the rating, so a
        # company that matches its sector on everything reads "mix", not "poor
        # [0/N]". passed_count stays the strict positive count (honest "N of M beat
        # sector" badge); only a genuine "negative" is a full miss.
        rating = _overall_rating(passed + 0.5 * neutrals, total)

        if not metrics:
            degraded.append("no_metrics")

        response = HealthCheckResponse(
            symbol=ticker,
            overall_rating=rating,
            passed_count=passed,
            total_count=total,
            metrics=metrics,
            degraded=degraded,
        )

        fund_shape = bool(
            raw_profile_is_fund and raw_legs_answered_lists and degraded == _FUND_SHAPE_DEGRADED
        )
        if raw_profile_is_fund and not metrics and not fund_shape:
            # Diagnosable from logs alone: the profile says fund, but the empty build is
            # NOT admitted — a leg failed, answered a non-list, or another reason is set.
            logger.info(
                "[fund-shape-refused] ticker=%s step=fund_check: the profile says fund but the "
                "empty build is not admitted (legs answered lists: %s, degraded=%r) — it "
                "stays unpersisted",
                ticker, raw_legs_answered_lists, degraded,
            )

        # `next_earnings` was stamped from the calendar slot right after the gather.
        return _HcBuild(response=response, next_earnings=next_earnings, fund_shape=fund_shape)


# ── Singleton ─────────────────────────────────────────────────────
_health_check_service: Optional[HealthCheckService] = None


def get_health_check_service() -> HealthCheckService:
    global _health_check_service
    if _health_check_service is None:
        _health_check_service = HealthCheckService()
    return _health_check_service
