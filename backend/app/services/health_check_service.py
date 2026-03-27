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

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.schemas.health_check import HealthCheckMetricSchema, HealthCheckResponse
from app.services.sector_benchmark_lookup import get_sector_benchmark_lookup
from app.services.sector_benchmark_service import _normalize_sector

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


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
    """Return the first future earnings date as yyyy-MM-dd, or None."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for ec in sorted(ec_records, key=lambda r: r.get("date", "")):
        ec_date = (ec.get("date") or "")[:10]
        if not ec_date or ec_date <= today_str:
            continue
        if ec.get("eps") is not None:
            continue
        return ec_date
    return None


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

# ── Status thresholds (percent difference) ────────────────────────
# For lower_is_better: negative pct_diff = company below sector = good
# For higher_is_better: positive pct_diff = company above sector = good
_STATUS_THRESHOLDS = {
    "debt_to_equity": {"positive_below": -10, "negative_above": 100},
    "pe_ratio":       {"positive_below": -10, "negative_above": 35},
    "roe":            {"positive_above": 10,  "negative_below": -20},
    "current_ratio":  {"positive_above": 10,  "negative_below": -25},
    # altman_z_score uses absolute thresholds, not percent difference
}


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

def _format_diff_label(abs_pct: float) -> str:
    """Format the highlighted_value as percentage or multiplier."""
    if abs_pct >= 200:
        multiplier = round(abs_pct / 100 + 1, 1)
        return f"{multiplier}x"
    return f"{int(round(abs_pct))}%"


def _generate_de_insight(
    pct_diff: float, value: float, sector: float,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Debt-to-Equity.

    Returns (main_text, highlighted_value, highlighted_label) where the
    frontend renders: ``{value} {label} {main_text}``
    e.g.  **43%** **below** sector average. Conservative leverage.
    """
    abs_pct = abs(pct_diff)
    label = _format_diff_label(abs_pct)

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
    abs_pct = abs(pct_diff)
    label = _format_diff_label(abs_pct)

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
    abs_pct = abs(pct_diff)
    label = _format_diff_label(abs_pct)

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
    abs_pct = abs(pct_diff)
    label = _format_diff_label(abs_pct)

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


def _generate_zscore_insight(
    value: float,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text for Altman Z-Score using absolute thresholds.

    Returns (main_text, highlighted_value, highlighted_label).
    No sector comparison — uses Altman's universal bankruptcy-risk zones.
    """
    formatted = f"{value:.1f}"

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


def _compute_z_score(bs: Dict, inc: Dict, mcap: Optional[float]) -> Optional[float]:
    """Compute Altman Z-Score from balance sheet, income, and market cap."""
    ta = _safe_float(bs, "totalAssets")
    tl = _safe_float(bs, "totalLiabilities")
    ca = _safe_float(bs, "totalCurrentAssets")
    cl = _safe_float(bs, "totalCurrentLiabilities")
    re = _safe_float(bs, "retainedEarnings")
    ebit = _safe_float(inc, "operatingIncome")
    rev = _safe_float(inc, "revenue")

    if not ta or ta <= 0 or not tl or tl <= 0:
        return None

    wc = (ca or 0) - (cl or 0)
    z = (
        1.2 * (wc / ta)
        + 1.4 * ((re or 0) / ta)
        + 3.3 * ((ebit or 0) / ta)
        + 0.6 * ((mcap or 0) / tl)
        + 1.0 * ((rev or 0) / ta)
    )
    return round(z, 1)


def _zscore_gauge(z: float) -> float:
    """Map Z-Score to 0.0–1.0 gauge using Altman's zones.

    0.0 = deep distress (Z ≤ 0), 0.5 ≈ grey zone boundary (Z = 1.8),
    1.0 = fortress (Z ≥ 4.5).
    """
    return _clamp(z / 4.5, 0.02, 0.98)


def _zscore_status(z: float) -> str:
    """Determine status from Altman Z-Score absolute thresholds."""
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
    elif metric_type == "altman_z_score":
        return _zscore_status(value)
    return "neutral"


def _fallback_insight(
    metric_type: str, value: float,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Generate insight text when no sector benchmark is available."""
    formatted = f"{value:.2f}" if metric_type in ("debt_to_equity", "current_ratio") else f"{value:.1f}"

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

    elif metric_type == "altman_z_score":
        return _generate_zscore_insight(value)

    return ("", None, None)


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
            return await _inflight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Health check cache MISS for {ticker} — fetching from FMP")
            result, next_earnings = await self._build_health_check(ticker)

            # Persist to Supabase in background (fire-and-forget)
            asyncio.get_running_loop().run_in_executor(
                None,
                self._upsert_supabase_cache_safe,
                ticker,
                result,
                next_earnings,
            )

            _cache_set(cache_key, result)
            future.set_result(result)
            return result
        except Exception as e:
            future.set_exception(e)
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

            json_data = entry["response_json"]
            return HealthCheckResponse(**json_data)

        except Exception as e:
            logger.warning(f"Supabase cache check failed for {ticker}: {e}")
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
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": next_earnings,
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(f"Supabase upsert failed for {ticker}: {e}")

    # ── Builder ───────────────────────────────────────────────────

    async def _build_health_check(
        self, ticker: str
    ) -> Tuple[HealthCheckResponse, Optional[str]]:
        # Phase 1: parallel FMP fetch (profile + ratios + key-metrics + earnings calendar + BS + IS for Z-Score)
        profile, ratios_list, key_metrics_list, ec_raw, bs_raw, inc_raw = await asyncio.gather(
            self.fmp.get_company_profile(ticker),
            self.fmp.get_financial_ratios(ticker, period="annual", limit=1),
            self.fmp.get_key_metrics(ticker, period="annual", limit=1),
            self.fmp.get_earning_calendar_full(ticker),
            self.fmp.get_balance_sheet(ticker, period="annual", limit=1),
            self.fmp.get_income_statement(ticker, period="annual", limit=1),
            return_exceptions=True,
        )

        if isinstance(profile, Exception):
            logger.warning(f"Profile fetch failed for {ticker}: {profile}")
            profile = {}
        if isinstance(ratios_list, Exception):
            logger.error(f"Ratios fetch failed for {ticker}: {ratios_list}")
            ratios_list = []
        if isinstance(key_metrics_list, Exception):
            logger.error(f"Key metrics fetch failed for {ticker}: {key_metrics_list}")
            key_metrics_list = []
        if isinstance(ec_raw, Exception):
            logger.warning(f"Earnings calendar failed for {ticker}: {ec_raw}")
            ec_raw = []
        if isinstance(bs_raw, Exception):
            logger.warning(f"Balance sheet fetch failed for {ticker}: {bs_raw}")
            bs_raw = []
        if isinstance(inc_raw, Exception):
            logger.warning(f"Income statement fetch failed for {ticker}: {inc_raw}")
            inc_raw = []

        # Phase 2: extract company ratios from both sources
        ratios = ratios_list[0] if isinstance(ratios_list, list) and ratios_list else {}
        key_metrics = key_metrics_list[0] if isinstance(key_metrics_list, list) and key_metrics_list else {}
        balance_sheet = bs_raw[0] if isinstance(bs_raw, list) and bs_raw else {}
        income_stmt = inc_raw[0] if isinstance(inc_raw, list) and inc_raw else {}

        # Extract market cap for Z-Score
        profile_data = profile if isinstance(profile, dict) else (profile[0] if isinstance(profile, list) and profile else {})
        mcap = _safe_float(profile_data, "mktCap")
        if mcap is None:
            mcap = _safe_float(profile_data, "marketCap")

        # Phase 3: get sector and look up benchmarks
        raw_sector = profile.get("sector", "") if isinstance(profile, dict) else ""
        sector = _normalize_sector(raw_sector)
        logger.info(f"Health check {ticker}: raw_sector={raw_sector!r}, normalized={sector!r}")

        benchmarks: Dict[str, Dict[str, float]] = {}
        if sector:
            lookup = get_sector_benchmark_lookup()
            benchmarks = lookup.get_sector_benchmarks(
                sector,
                ["debt_to_equity", "pe_ratio", "roe", "current_ratio"],
                "annual",
            )
            logger.info(
                f"Health check {ticker}: benchmarks returned keys="
                f"{{k: list(v.keys()) for k, v in benchmarks.items()}}"
            )
            for bm_name, bm_data in benchmarks.items():
                if not bm_data:
                    logger.warning(f"Health check {ticker}: NO benchmark data for {bm_name}")
        else:
            logger.warning(f"Health check {ticker}: no sector found, skipping benchmark lookup")

        # Pre-compute Altman Z-Score for use in the metric loop
        z_score_val = _compute_z_score(balance_sheet, income_stmt, mcap)

        # Phase 4: build each metric
        metrics: List[HealthCheckMetricSchema] = []
        for mdef in METRIC_DEFS:
            # Altman Z-Score is computed separately, not from a single FMP field
            if mdef["type"] == "altman_z_score":
                if z_score_val is None:
                    logger.warning(f"Health check {ticker}: altman_z_score — insufficient data to compute")
                    continue

                gauge = _zscore_gauge(z_score_val)
                status = _zscore_status(z_score_val)
                insight_text, highlighted_value, highlighted_label = _generate_zscore_insight(z_score_val)

                metrics.append(
                    HealthCheckMetricSchema(
                        type="altman_z_score",
                        value=round(z_score_val, 1),
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
            company_val = _safe_float(source, mdef["fmp_field"])
            if company_val is None:
                logger.warning(
                    f"Health check {ticker}: {mdef['type']} — FMP field "
                    f"{mdef['fmp_field']!r} not found in {mdef['source']} response"
                )
                continue

            # Skip negative P/E (loss-making company — ratio is meaningless)
            if mdef["type"] == "pe_ratio" and company_val < 0:
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
                        insight_text="Negative equity. Liabilities exceed total assets.",
                        highlighted_value="Negative",
                        highlighted_label="shareholder equity.",
                    )
                )
                continue

            # Convert ROE from decimal to percentage for display
            display_val = company_val
            if mdef["is_percentage"]:
                display_val = round(company_val * 100, 2)

            # Get most recent sector benchmark
            metric_benchmarks = benchmarks.get(mdef["benchmark_name"], {})
            sector_val = None
            sector_display = None
            if metric_benchmarks:
                latest_key = max(metric_benchmarks.keys())
                sector_val = metric_benchmarks[latest_key]
                if mdef["is_percentage"]:
                    sector_display = round(sector_val * 100, 2)
                else:
                    sector_display = round(sector_val, 2)

            # Calculate percent difference
            pct_diff = None
            if sector_val is not None and abs(sector_val) > 1e-9:
                pct_diff = round((company_val - sector_val) / abs(sector_val) * 100, 1)

            # Gauge position (uses raw values, not display values)
            if sector_val is not None and sector_val > 0:
                gauge = _gauge_position(company_val, sector_val)
            else:
                # No sector benchmark — use absolute-value heuristic
                # Pass display_val so ROE is in % form for correct thresholds
                gauge = _absolute_gauge(mdef["type"], display_val)

            # Status
            if pct_diff is not None:
                status = _determine_status(
                    mdef["type"], pct_diff, mdef["lower_is_better"]
                )
            else:
                # No sector benchmark — use absolute-value heuristic
                status = _absolute_status(mdef["type"], display_val)

            # Dynamic insight text
            if pct_diff is not None:
                gen = _INSIGHT_GENERATORS[mdef["type"]]
                insight_text, highlighted_value, highlighted_label = gen(
                    pct_diff, display_val, sector_display or 0,
                )
            else:
                # Fallback text when no sector data is available
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

        # Phase 5: overall rating
        passed = sum(1 for m in metrics if m.status == "positive")
        total = len(metrics)
        rating = _overall_rating(passed, total)

        response = HealthCheckResponse(
            symbol=ticker,
            overall_rating=rating,
            passed_count=passed,
            total_count=total,
            metrics=metrics,
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
