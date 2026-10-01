"""
Health Check service — fetches financial ratios from FMP, compares them
to pre-computed sector median benchmarks, computes gauge positions,
status colors, and dynamic insight text.

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
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.services._earnings_common import next_pending_earnings_date
from app.database import get_supabase
from app.utils.inflight import fail_shared_future
from app.integrations.fmp import FMPNotEntitledException, get_fmp_client
from app.schemas.health_check import HealthCheckMetricSchema, HealthCheckResponse
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
_HC_PAYLOAD_VERSION = 3
_HC_VERSION_KEY = "payload_version"


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


def _find_next_earnings_date(ec_records: List[Dict[str, Any]]) -> Optional[str]:
    """Next pending earnings date (yyyy-MM-dd) — includes TODAY's pending report and
    skips a stale reschedule row. Shared rule: ``_earnings_common.next_pending_earnings``."""
    return next_pending_earnings_date(ec_records)


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


# ── Metric definitions ───────────────────────────────────────────

# FMP field name, sector_benchmarks metric name, lower_is_better flag
# Note: FMP stable API field names match sector_benchmark_service.py
# ROE comes from key-metrics endpoint, not ratios.
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
    pct_diff: float, value: float, sector: float,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Debt-to-Equity.

    Returns (main_text, highlighted_value, highlighted_label) where the
    frontend renders: ``{value} {label} {main_text}``
    e.g.  **43%** **below** sector average. Conservative leverage.
    """
    label = _format_diff_label(pct_diff)

    if pct_diff < -50:
        return (
            "sector average. Very conservative leverage.",
            label,
            "below",
        )
    elif pct_diff < -25:
        return (
            "sector average. Conservative leverage.",
            label,
            "below",
        )
    elif pct_diff < -10:
        return (
            "sector average. Healthy debt position.",
            label,
            "below",
        )
    elif pct_diff <= 15:
        direction = "above" if pct_diff > 0 else "below"
        return (
            "sector average. Leverage in line with peers.",
            label,
            direction,
        )
    elif pct_diff <= 50:
        return (
            "sector average. Moderately higher leverage.",
            label,
            "above",
        )
    elif pct_diff <= 100:
        return (
            "sector average. Elevated leverage.",
            label,
            "above",
        )
    else:
        return (
            "sector average. Significantly leveraged vs peers.",
            label,
            "well above",
        )


def _generate_pe_insight(
    pct_diff: float, value: float, sector: float,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for P/E Ratio.

    Returns (main_text, highlighted_value, highlighted_label) where the
    frontend renders: ``{value} {label} {main_text}``
    e.g.  **15%** **below** sector average. Fair value opportunity.
    """
    label = _format_diff_label(pct_diff)

    if pct_diff < -30:
        return (
            "sector average. Deep value opportunity.",
            label,
            "below",
        )
    elif pct_diff < -15:
        return (
            "sector average. Fair value opportunity.",
            label,
            "below",
        )
    elif pct_diff < -5:
        return (
            "sector average. Slight valuation edge.",
            label,
            "below",
        )
    elif pct_diff <= 10:
        direction = "above" if pct_diff > 0 else "below"
        return (
            "sector average. Valued in line with peers.",
            label,
            direction,
        )
    elif pct_diff <= 35:
        return (
            "sector average. Premium valuation.",
            label,
            "above",
        )
    elif pct_diff <= 75:
        return (
            "sector average. Priced for high growth.",
            label,
            "well above",
        )
    else:
        return (
            "sector average. Richly valued vs peers.",
            label,
            "well above",
        )


def _generate_roe_insight(
    pct_diff: float, value: float, sector: float,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Return on Equity.

    Returns (main_text, highlighted_value, highlighted_label) where the
    frontend renders: ``{value} {label} {main_text}``
    e.g.  **22%** **above** sector average. Strong capital efficiency.
    """
    label = _format_diff_label(pct_diff)

    if pct_diff > 100:
        return (
            "sector average. Exceptional capital efficiency.",
            label,
            "well above",
        )
    elif pct_diff > 40:
        return (
            "sector average. Strong capital efficiency.",
            label,
            "above",
        )
    elif pct_diff > 10:
        return (
            "sector average. Solid returns on equity.",
            label,
            "above",
        )
    elif pct_diff >= -10:
        direction = "above" if pct_diff >= 0 else "below"
        return (
            "sector average. Average capital efficiency.",
            label,
            direction,
        )
    elif pct_diff >= -30:
        return (
            "sector average. Below-average capital efficiency.",
            label,
            "below",
        )
    elif pct_diff >= -50:
        return (
            "sector average. Low capital efficiency.",
            label,
            "below",
        )
    else:
        return (
            "sector average. Significantly underperforming.",
            label,
            "well below",
        )


def _generate_cr_insight(
    pct_diff: float, value: float, sector: float,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Current Ratio."""
    label = _format_diff_label(pct_diff)

    if pct_diff > 75:
        return (
            "sector average. Ample liquidity cushion.",
            label,
            "well above",
        )
    elif pct_diff > 30:
        return (
            "sector average. Healthy short-term liquidity position.",
            label,
            "above",
        )
    elif pct_diff > 10:
        return (
            "sector average, normal short-term liquidity position.",
            label,
            "above",
        )
    elif pct_diff >= -10:
        return (
            "Liquidity roughly in line with sector peers.",
            label,
            "near sector average.",
        )
    elif pct_diff >= -25:
        return (
            "sector average. Adequate but tight liquidity.",
            label,
            "a little below",
        )
    elif pct_diff >= -40:
        return (
            "sector average. Tight but manageable liquidity.",
            label,
            "below",
        )
    else:
        return (
            "sector average. Constrained liquidity position.",
            label,
            "well below",
        )


def _generate_ic_insight(
    pct_diff: float, value: float, sector: float,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Interest Coverage (higher is better)."""
    label = _format_diff_label(pct_diff)

    if pct_diff > 100:
        return (
            "sector average. Outsized capacity to service debt.",
            label,
            "well above",
        )
    elif pct_diff > 50:
        return (
            "sector average. Strong debt service coverage.",
            label,
            "above",
        )
    elif pct_diff > 10:
        return (
            "sector average. Comfortable interest coverage.",
            label,
            "above",
        )
    elif pct_diff >= -25:
        direction = "above" if pct_diff >= 0 else "below"
        return (
            "sector average. Adequate coverage of interest expense.",
            label,
            direction,
        )
    elif pct_diff >= -50:
        return (
            "sector average. Thin coverage of interest expense.",
            label,
            "below",
        )
    else:
        return (
            "sector average. Vulnerable to interest expense pressure.",
            label,
            "well below",
        )


def _generate_qr_insight(
    pct_diff: float, value: float, sector: float,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Quick Ratio (higher is better)."""
    label = _format_diff_label(pct_diff)

    if pct_diff > 50:
        return (
            "sector average. Strong cash-equivalent liquidity.",
            label,
            "well above",
        )
    elif pct_diff > 20:
        return (
            "sector average. Healthy near-cash coverage.",
            label,
            "above",
        )
    elif pct_diff > 5:
        return (
            "sector average. Solid quick-asset cushion.",
            label,
            "above",
        )
    elif pct_diff >= -10:
        direction = "above" if pct_diff >= 0 else "below"
        return (
            "sector average. Quick-asset coverage in line with peers.",
            label,
            direction,
        )
    elif pct_diff >= -25:
        return (
            "sector average. Thin near-cash cushion.",
            label,
            "below",
        )
    else:
        return (
            "sector average. Limited quick-asset coverage.",
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
    metric_type: str, value: float,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Insight for a company value <= 0 against a POSITIVE peer median.

    A percent gap is meaningless here: ROE -25% vs a 12% median is pct -308, which the
    generators rendered "4.1x well below sector average", and IC -3 vs 20 rendered
    "115% well below". Say what the number means instead; the status stays "negative".
    """
    word = "Negative" if value < 0 else "Zero"
    if metric_type == "roe":
        text = ("The company is losing money on its equity." if value < 0
                else "No return on shareholder equity.")
        return (text, word, "ROE vs a positive sector average.")
    text = ("Operating losses leave interest expense uncovered." if value < 0
            else "Operating earnings do not cover interest expense.")
    return (text, word, "interest coverage vs a positive sector average.")


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
            result, next_earnings = await self._build_health_check(ticker)

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
            hc_degraded = list(getattr(result, "degraded", None) or [])
            if hc_degraded:
                logger.warning(
                    "Health check NOT persisted for %s (degraded: %s) — will rebuild "
                    "after the in-memory TTL", ticker, ", ".join(hc_degraded),
                )
            else:
                # Persist to Supabase in background (fire-and-forget)
                asyncio.get_running_loop().run_in_executor(
                    None,
                    self._upsert_supabase_cache_safe,
                    ticker,
                    result,
                    next_earnings,
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
            if version != _HC_PAYLOAD_VERSION:
                logger.info(
                    "Health check Supabase cache STALE for %s (payload_version=%r, want %d) "
                    "— recomputing", ticker, version, _HC_PAYLOAD_VERSION,
                )
                return None
            if json_data.get("degraded"):
                # Never written by this code (the writer refuses a degraded build), so
                # a row that has it was written by hand or by a bug — do not serve it.
                logger.warning(
                    "Health check Supabase row for %s carries degraded=%r — ignoring it",
                    ticker, json_data.get("degraded"),
                )
                return None
            return HealthCheckResponse(**json_data)

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
        next_earnings: Optional[str],
    ) -> None:
        try:
            self.supabase.table("health_check_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": {**result.model_dump(),
                                      _HC_VERSION_KEY: _HC_PAYLOAD_VERSION},
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
    ) -> Tuple[HealthCheckResponse, Optional[str]]:
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
            self.fmp.get_earning_calendar_full(ticker),
            self.fmp.get_balance_sheet(ticker, period="quarter", limit=1),
            self.fmp.get_income_statement(ticker, period="quarter", limit=4),
            return_exceptions=True,
        )

        # Every leg that failed TRANSIENTLY. Each one is replaced by an empty default and
        # the build still renders, which is exactly why it must not reach the 24h tier
        # (see get_health_check). A permanent FMPNotEntitledException is not degradation:
        # that slice will not come back on retry, so the build without it is the answer.
        # The earnings calendar only feeds the cache's next-earnings stamp: optional.
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
        if isinstance(ec_raw, Exception):
            logger.warning(f"Earnings calendar failed for {ticker}: {type(ec_raw).__name__}: {ec_raw}")
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

        # CURRENT benchmark per metric: TTM row if present, else latest mature annual
        # value (fallback). Flat {metric: value | None}.
        cur_bench: Dict[str, Optional[float]] = {}
        if sector:
            lookup = get_sector_benchmark_lookup()
            try:
                # Sync lookup (supabase-py + time.sleep retry): keep it off the loop.
                cur_bench = await asyncio.to_thread(
                    lookup.get_current_benchmark_values,
                    industry,
                    sector,
                    [
                        "debt_to_equity",
                        "pe_ratio",
                        "roe",
                        "current_ratio",
                        "interest_coverage",
                        "quick_ratio",
                    ],
                )
            except Exception as e:
                # A lookup FAILURE is not the same answer as "this peer group has no
                # rows": every metric falls back to the absolute heuristics, which is a
                # fine thing to SERVE and a wrong thing to pin for 24h.
                logger.warning(
                    "Health check %s: benchmark lookup failed (%s: %s) — absolute "
                    "heuristics, build marked degraded", ticker, type(e).__name__, e,
                )
                cur_bench = {}
                degraded.append("benchmarks")
            else:
                # The lookup SWALLOWS a DB error into the empty shape; it flags that
                # shape so a transient failure is not persisted as "no peer group".
                if lookup_failed(cur_bench):
                    logger.warning(
                        "Health check %s: benchmark lookup returned a FAILED shape — "
                        "absolute heuristics, build marked degraded", ticker,
                    )
                    degraded.append("benchmarks")
            logger.info(f"Health check {ticker}: current benchmarks={cur_bench}")
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
                    mdef["type"], display_val,
                )
                pct_diff = None
            elif pct_diff is not None:
                status = _determine_status(
                    mdef["type"], pct_diff, mdef["lower_is_better"]
                )
                gen = _INSIGHT_GENERATORS[mdef["type"]]
                insight_text, highlighted_value, highlighted_label = gen(
                    pct_diff, display_val, sector_display or 0,
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

        # Phase 6: next earnings for cache invalidation
        next_earnings = _find_next_earnings_date(
            ec_raw if isinstance(ec_raw, list) else []
        )

        return response, next_earnings


# ── Singleton ─────────────────────────────────────────────────────
_health_check_service: Optional[HealthCheckService] = None


def get_health_check_service() -> HealthCheckService:
    global _health_check_service
    if _health_check_service is None:
        _health_check_service = HealthCheckService()
    return _health_check_service
