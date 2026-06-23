"""
Sector Benchmark Service — Pre-computes median financial metrics per GICS sector
from S&P 500 constituents and stores them in Supabase.

Runs as a daily background job. Any service (Growth, Profit Power, Health Check, etc.)
can then look up sector benchmarks via a fast DB query instead of fetching peer data
on every request.
"""

import asyncio
import logging
import statistics
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client, FMPClient

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────

BATCH_SIZE = 10           # concurrent FMP calls per batch of companies
BATCH_DELAY_SECONDS = 1.0 # delay between batches to avoid rate limits
MIN_SAMPLE_SIZE = 5       # minimum companies needed to compute a reliable median
UPSERT_BATCH_SIZE = 100   # rows per Supabase upsert call
WINSORIZE_FLOOR = -500.0  # cap extreme negative YoY/QoQ values (%)
WINSORIZE_CEIL = 500.0    # cap extreme positive YoY/QoQ values (%)
FMP_SEMAPHORE_LIMIT = 10  # max concurrent FMP API calls across all sectors

# Backfill limits (one-time deep historical computation)
FMP_ANNUAL_LIMIT_BACKFILL = 16      # 16 records → 15 YoY data points (15 years)
FMP_QUARTERLY_LIMIT_BACKFILL = 80   # deep quarterly history (FMP may return fewer)

# Daily limits (only refresh recent/current periods)
FMP_ANNUAL_LIMIT_DAILY = 3          # 3 records → 2 YoY points (current + prior year)
FMP_QUARTERLY_LIMIT_DAILY = 12      # ~3 years of quarters (covers recent YoY + QoQ)

# FMP sector names → canonical app sector names
_FMP_SECTOR_MAP: Dict[str, str] = {
    "Technology": "Technology",
    "Information Technology": "Technology",
    "Healthcare": "Healthcare",
    "Health Care": "Healthcare",
    "Financial Services": "Financial Services",
    "Financials": "Financial Services",
    "Consumer Cyclical": "Consumer Cyclical",
    "Consumer Discretionary": "Consumer Cyclical",
    "Communication Services": "Communication Services",
    "Telecommunication Services": "Communication Services",
    "Industrials": "Industrials",
    "Consumer Defensive": "Consumer Defensive",
    "Consumer Staples": "Consumer Defensive",
    "Energy": "Energy",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
    "Basic Materials": "Basic Materials",
    "Materials": "Basic Materials",
}

# Fallback tickers if FMP sp500-constituent endpoint is unavailable
_FALLBACK_SECTOR_TICKERS: Dict[str, List[str]] = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "CRM"],
    "Healthcare": ["UNH", "JNJ", "LLY", "PFE", "ABBV"],
    "Financial Services": ["JPM", "BAC", "WFC", "GS", "MS"],
    "Consumer Cyclical": ["AMZN", "TSLA", "HD", "MCD", "NKE"],
    "Communication Services": ["META", "GOOGL", "NFLX", "DIS", "CMCSA"],
    "Industrials": ["CAT", "UNP", "HON", "GE", "RTX"],
    "Consumer Defensive": ["PG", "KO", "PEP", "WMT", "COST"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG"],
    "Real Estate": ["AMT", "PLD", "CCI", "EQIX", "SPG"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP"],
    "Basic Materials": ["LIN", "APD", "SHW", "ECL", "NEM"],
}

# The 11 canonical sectors (derived from fallback keys)
CANONICAL_SECTORS: frozenset = frozenset(_FALLBACK_SECTOR_TICKERS.keys())

# All metrics to compute
METRIC_CONFIGS: List[Dict[str, str]] = [
    # YoY growth metrics (from income statement)
    {"name": "eps_yoy",              "source": "income",   "field": "epsDiluted",             "type": "yoy"},
    {"name": "revenue_yoy",          "source": "income",   "field": "revenue",                "type": "yoy"},
    {"name": "net_income_yoy",       "source": "income",   "field": "netIncome",              "type": "yoy"},
    {"name": "operating_income_yoy", "source": "income",   "field": "operatingIncome",        "type": "yoy"},
    {"name": "gross_profit_yoy",     "source": "income",   "field": "grossProfit",            "type": "yoy"},
    # YoY growth from cash flow
    {"name": "fcf_yoy",             "source": "cashflow",  "field": "freeCashFlow",           "type": "yoy"},
    # QoQ growth metrics (sequential quarter comparison, quarterly only)
    {"name": "eps_qoq",             "source": "income",    "field": "epsDiluted",             "type": "qoq"},
    {"name": "revenue_qoq",         "source": "income",    "field": "revenue",                "type": "qoq"},
    # Profit Power (direct ratio values)
    {"name": "gross_margin",        "source": "ratios",    "field": "grossProfitMargin",      "type": "direct"},
    {"name": "operating_margin",    "source": "ratios",    "field": "operatingProfitMargin",  "type": "direct"},
    {"name": "net_margin",          "source": "ratios",    "field": "netProfitMargin",        "type": "direct"},
    # FCF margin = freeCashFlow ÷ revenue — a JOIN across cashflow + income, so it
    # is "computed" (not a /ratios field). Stored as a DECIMAL (no ×100) so the
    # consumer's ×100 matches the direct margins; NEGATIVES are kept (cash-burning
    # companies are real), so it is EXCLUDED from the multiple-winsorization band.
    {"name": "fcf_margin",          "type": "computed",      "compute": "fcf_margin"},
    # ROA and ROE both come from /key-metrics — FMP's /ratios doesn't reliably
    # expose returnOnAssets across the S&P 500, so sourcing from /ratios drops
    # the sample size below MIN_SAMPLE_SIZE and the sector_benchmarks table
    # ends up with no `roa` row (visible as a missing asterisk on the ROA
    # snapshot row).
    {"name": "roa",                 "source": "key_metrics", "field": "returnOnAssets",       "type": "direct"},
    {"name": "roe",                 "source": "key_metrics", "field": "returnOnEquity",       "type": "direct"},
    {"name": "roic",                "source": "ratios",    "field": "returnOnCapitalEmployed","type": "direct"},
    # Health Check (direct ratio values)
    {"name": "current_ratio",       "source": "ratios",    "field": "currentRatio",           "type": "direct"},
    {"name": "quick_ratio",         "source": "ratios",    "field": "quickRatio",             "type": "direct"},
    {"name": "debt_to_equity",      "source": "ratios",    "field": "debtToEquityRatio",      "type": "direct"},
    {"name": "interest_coverage",   "source": "ratios",    "field": "interestCoverageRatio",  "type": "direct"},
    {"name": "debt_to_assets",      "source": "ratios",    "field": "debtRatio",              "type": "direct"},
    # Valuation
    {"name": "pe_ratio",            "source": "ratios",    "field": "priceToEarningsRatio",   "type": "direct"},
    {"name": "pb_ratio",            "source": "ratios",    "field": "priceToBookRatio",       "type": "direct"},
    {"name": "ps_ratio",            "source": "ratios",    "field": "priceToSalesRatio",      "type": "direct"},
    # P/FCF and EV/EBITDA are RECONSTRUCTED from raw fundamentals, not
    # extracted as pre-computed ratios. FMP's pre-computed `pfcfRatio` and
    # `enterpriseValueOverEBITDA` fields come back null for too much of the
    # S&P 500 (across /ratios AND /key-metrics) — sample size per sector
    # drops below MIN_SAMPLE_SIZE and the table never populates. Computing
    # from raw `marketCap` / `freeCashFlow` / `enterpriseValue` / `ebitda`
    # mirrors what valuation_snapshot_service does per-ticker (lines 369–454),
    # so the sector median and the company's own metric use identical math.
    # Dispatch is by the "compute" key — see _compute_ratio_values.
    {"name": "pfcf_ratio",          "type": "computed",      "compute": "pfcf"},
    {"name": "ev_ebitda",           "type": "computed",      "compute": "ev_ebitda"},
    # Earnings yield = netIncome / marketCap (a DECIMAL, e.g. 0.04). FMP's
    # pre-computed `earningsYield` field is null across the S&P 500 (annual AND
    # quarterly), so a direct extraction yielded ZERO rows. Reconstructing from
    # raw mirrors P/FCF / EV/EBITDA and matches the per-ticker fallback
    # (valuation_snapshot_service: ratios.earningsYield → 1/PE), so the sector
    # median and a company's own yield use the same definition.
    {"name": "earnings_yield",      "type": "computed",      "compute": "earnings_yield"},
    {"name": "dividend_yield",      "source": "ratios",    "field": "dividendYield",          "type": "direct"},
    # Efficiency
    {"name": "asset_turnover",      "source": "ratios",    "field": "assetTurnover",          "type": "direct"},
    # ── Moat-scoring metrics (Phase 3A) ──────────────────────────────
    # All four are RECONSTRUCTED from raw income/balance fields rather
    # than pulled from pre-computed FMP ratios because (a) FMP doesn't
    # expose them as named ratios on /stable/ratios for most tickers,
    # and (b) reconstructing here means a sector median and a company's
    # own metric use identical math. Stored as percentages (×100) so
    # the scale matches gross_margin/operating_margin/etc.
    {"name": "rd_to_revenue",       "type": "computed",      "compute": "rd_to_revenue"},
    {"name": "sga_to_revenue",      "type": "computed",      "compute": "sga_to_revenue"},
    {"name": "intangibles_to_assets","type": "computed",     "compute": "intangibles_to_assets"},
    {"name": "deferred_revenue_to_revenue", "type": "computed", "compute": "deferred_revenue_to_revenue"},
]


# ── Helpers ───────────────────────────────────────────────────────

def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    """Safely extract a float value from a dict."""
    val = record.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _extract_year(record: Dict[str, Any]) -> str:
    """Extract calendar year from the record.

    Prefers FMP's ``calendarYear`` field which correctly maps fiscal quarters
    to their reporting calendar year (e.g., Apple's fiscal Q1 ending Dec 2020
    is reported as calendar year 2021).  Falls back to the date field.
    """
    cal_year = record.get("calendarYear")
    if cal_year:
        return str(cal_year)
    date_str = record.get("date", "")
    if len(date_str) >= 4:
        return date_str[:4]
    return ""


def _annual_period_label(record: Dict[str, Any]) -> str:
    """Annual period label like '2024'."""
    return _extract_year(record)


def _quarterly_period_label(record: Dict[str, Any]) -> str:
    """Quarterly period label like \"Q1'24\"."""
    period = record.get("period", "")  # "Q1", "Q2", etc.
    year = _extract_year(record)
    if len(year) >= 4:
        return f"{period}'{year[-2:]}"
    return f"{period}'{year}"


def _compute_yoy_for_records(
    records: List[Dict[str, Any]],
    field: str,
    is_quarterly: bool,
) -> Dict[str, float]:
    """
    Compute YoY growth % for each period in the records.
    Returns {period_label: yoy_percent}.
    """
    if not records:
        return {}

    sorted_recs = sorted(records, key=lambda r: r.get("date", ""))
    result: Dict[str, float] = {}

    if is_quarterly:
        # Build lookup: (period, year) -> record
        lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for rec in sorted_recs:
            p = rec.get("period", "")
            cy = _extract_year(rec)
            lookup[(p, cy)] = rec

        for rec in sorted_recs:
            period = rec.get("period", "")
            cal_year = _extract_year(rec)
            try:
                prev_year = str(int(cal_year) - 1)
            except ValueError:
                continue

            prev_rec = lookup.get((period, prev_year))
            if prev_rec is None:
                continue

            current_val = _safe_float(rec, field)
            prev_val = _safe_float(prev_rec, field)
            if current_val is not None and prev_val is not None and prev_val != 0:
                yoy = round((current_val - prev_val) / abs(prev_val) * 100, 2)
                label = _quarterly_period_label(rec)
                if label:
                    result[label] = yoy
    else:
        # Annual: compare consecutive sorted records (only if exactly 1 year apart)
        for i in range(1, len(sorted_recs)):
            rec = sorted_recs[i]
            prev_rec = sorted_recs[i - 1]
            # Validate year gap is exactly 1
            try:
                cur_year = int(_extract_year(rec))
                prv_year = int(_extract_year(prev_rec))
                if cur_year - prv_year != 1:
                    continue
            except (ValueError, TypeError):
                continue
            current_val = _safe_float(rec, field)
            prev_val = _safe_float(prev_rec, field)
            if current_val is not None and prev_val is not None and prev_val != 0:
                yoy = round((current_val - prev_val) / abs(prev_val) * 100, 2)
                label = _annual_period_label(rec)
                if label:
                    result[label] = yoy

    return result


def _compute_qoq_for_records(
    records: List[Dict[str, Any]],
    field: str,
) -> Dict[str, float]:
    """
    Compute sequential Quarter-over-Quarter growth % for each period.
    Compares each quarter to the immediately preceding quarter (Q2 vs Q1, Q3 vs Q2, etc.).
    Returns {period_label: qoq_percent}.
    """
    if not records:
        return {}

    sorted_recs = sorted(records, key=lambda r: r.get("date", ""))
    result: Dict[str, float] = {}

    for i in range(1, len(sorted_recs)):
        current_val = _safe_float(sorted_recs[i], field)
        prev_val = _safe_float(sorted_recs[i - 1], field)
        if current_val is not None and prev_val is not None and prev_val != 0:
            qoq = round((current_val - prev_val) / abs(prev_val) * 100, 2)
            label = _quarterly_period_label(sorted_recs[i])
            if label:
                result[label] = qoq

    return result


def _winsorize(values: List[float], floor: float = WINSORIZE_FLOOR, ceil: float = WINSORIZE_CEIL) -> List[float]:
    """Cap extreme values to prevent outliers from distorting the median."""
    return [max(floor, min(ceil, v)) for v in values]


# ── Ratio reconstruction (for "computed" metric type) ────────────
#
# FMP's pre-computed pfcfRatio / enterpriseValueOverEBITDA come back null
# for too much of the S&P 500. Reconstruct from raw building blocks instead.
# These mirror the per-ticker reconstruction in valuation_snapshot_service.py
# so a sector median and a company's own ratio use identical arithmetic.

# Cap ratios at a sane upper bound before taking the sector median —
# multiples above 200 are almost always artefacts of near-zero denominators
# (e.g. EBITDA approaching zero) and would yank the median upward.
COMPUTED_RATIO_FLOOR = 0.0
COMPUTED_RATIO_CEIL = 200.0


def _pfcf_from_raw(km: Dict[str, Any], cf: Dict[str, Any]) -> Optional[float]:
    """P/FCF = market cap ÷ free cash flow.

    Returns None for non-positive FCF — a negative multiple is meaningless
    for sector aggregation. This matches valuation_snapshot_service which
    surfaces "Neg." rather than mixing the sign into the ratio.
    """
    mcap = _safe_float(km, "marketCap")
    fcf = _safe_float(cf, "freeCashFlow")
    if mcap and mcap > 0 and fcf and fcf > 0:
        return mcap / fcf
    return None


def _ev_ebitda_from_raw(
    km: Dict[str, Any], cf: Dict[str, Any], inc: Dict[str, Any],
) -> Optional[float]:
    """EV / EBITDA with EBITDA fallback chain.

    Rungs (in order):
      1. inc.ebitda
      2. operatingIncome + D&A  (D&A from cf or inc)
    Returns None when EV or EBITDA can't be derived positively. Matches the
    rungs valuation_snapshot_service uses for the per-ticker reconstruction.
    """
    ev = _safe_float(km, "enterpriseValue")
    if not ev or ev <= 0:
        return None

    ebitda = _safe_float(inc, "ebitda")
    if not ebitda or ebitda <= 0:
        op_income = _safe_float(inc, "operatingIncome")
        d_and_a = (
            _safe_float(cf, "depreciationAndAmortization")
            or _safe_float(inc, "depreciationAndAmortization")
        )
        if op_income is not None and d_and_a is not None:
            ebitda = op_income + d_and_a

    if ebitda and ebitda > 0:
        return ev / ebitda
    return None


def _index_by_period(
    records: List[Dict[str, Any]], period_type: str,
) -> Dict[str, Dict[str, Any]]:
    """Key each record by its period label so we can join across endpoints."""
    out: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        label = (
            _quarterly_period_label(rec) if period_type == "quarterly"
            else _annual_period_label(rec)
        )
        if label:
            out[label] = rec
    return out


def _ratio_pct_from_income(
    inc: Dict[str, Any], numerator_field: str,
) -> Optional[float]:
    """numerator / revenue as a percentage (×100). Returns None when
    revenue is missing or non-positive. Allows numerator==0 (legitimate
    signal — e.g., zero R&D for non-tech companies).
    """
    rev = _safe_float(inc, "revenue")
    if not rev or rev <= 0:
        return None
    num = _safe_float(inc, numerator_field)
    if num is None:
        return None
    return (num / rev) * 100.0


def _intangibles_to_assets_pct(bs: Dict[str, Any]) -> Optional[float]:
    """(Goodwill + Intangible Assets) / Total Assets, as percentage."""
    assets = _safe_float(bs, "totalAssets")
    if not assets or assets <= 0:
        return None
    goodwill = _safe_float(bs, "goodwill") or 0.0
    intangibles = _safe_float(bs, "intangibleAssets") or 0.0
    # FMP sometimes reports `goodwillAndIntangibleAssets` instead;
    # prefer the combined field when it exists.
    combined = _safe_float(bs, "goodwillAndIntangibleAssets")
    if combined is not None and combined > 0:
        total_intang = combined
    else:
        total_intang = goodwill + intangibles
    return (total_intang / assets) * 100.0


def _deferred_rev_to_rev_pct(
    bs: Dict[str, Any], inc: Dict[str, Any],
) -> Optional[float]:
    """Deferred Revenue / Revenue, as percentage. High = subscription
    stickiness (Switching Costs proxy).
    """
    rev = _safe_float(inc, "revenue")
    if not rev or rev <= 0:
        return None
    deferred = _safe_float(bs, "deferredRevenue")
    if deferred is None:
        # Some FMP responses split into current / non-current.
        cur = _safe_float(bs, "deferredRevenueCurrent") or 0.0
        non = _safe_float(bs, "deferredRevenueNonCurrent") or 0.0
        if cur == 0 and non == 0:
            return None
        deferred = cur + non
    return (deferred / rev) * 100.0


def _compute_ratio_values(
    all_company_data: List[Dict[str, List]],
    compute_name: str,
    period_type: str,
) -> Dict[str, List[float]]:
    """Reconstruct a ratio per company per year and bucket by year.

    Returns {period_label: [ratio, ...]} matching the shape that
    _compute_yoy_for_records / direct extraction produce, so downstream
    median computation stays identical.
    """
    out: Dict[str, List[float]] = {}
    km_key = f"key_metrics_{period_type}"
    cf_key = f"cashflow_{period_type}"
    inc_key = f"income_{period_type}"
    bs_key = f"balance_{period_type}"

    # Phase 3A moat metrics — income-only ratios. Pure inc-based loop;
    # no balance / km / cf needed, so we can short-circuit and avoid
    # rejecting years where km is missing.
    if compute_name in ("rd_to_revenue", "sga_to_revenue"):
        field_map = {
            "rd_to_revenue": "researchAndDevelopmentExpenses",
            "sga_to_revenue": "sellingGeneralAndAdministrativeExpenses",
        }
        numerator_field = field_map[compute_name]
        for company in all_company_data:
            inc_by_year = _index_by_period(
                company.get(inc_key, []), period_type,
            )
            for year, inc in inc_by_year.items():
                value = _ratio_pct_from_income(inc, numerator_field)
                # Bucket non-None (allows 0 — zero R&D is a legitimate signal).
                if value is not None and value >= 0:
                    out.setdefault(year, []).append(value)
        return out

    # Phase 3A moat metric — balance-only ratio.
    if compute_name == "intangibles_to_assets":
        for company in all_company_data:
            bs_by_year = _index_by_period(
                company.get(bs_key, []), period_type,
            )
            for year, bs in bs_by_year.items():
                value = _intangibles_to_assets_pct(bs)
                if value is not None and value >= 0:
                    out.setdefault(year, []).append(value)
        return out

    # Phase 3A moat metric — balance + income.
    if compute_name == "deferred_revenue_to_revenue":
        for company in all_company_data:
            bs_by_year = _index_by_period(
                company.get(bs_key, []), period_type,
            )
            inc_by_year = _index_by_period(
                company.get(inc_key, []), period_type,
            )
            for year in set(bs_by_year) & set(inc_by_year):
                value = _deferred_rev_to_rev_pct(
                    bs_by_year[year], inc_by_year[year],
                )
                if value is not None and value >= 0:
                    out.setdefault(year, []).append(value)
        return out

    # FCF margin — cashflow ∩ income join, stored as a DECIMAL (freeCashFlow ÷
    # revenue). Unlike P/FCF / EV/EBITDA (multiples, profitable-only), a margin's
    # NEGATIVES are real and are kept in the median (mirrors net_margin). No >0 gate.
    if compute_name == "fcf_margin":
        for company in all_company_data:
            cf_by_year = _index_by_period(company.get(cf_key, []), period_type)
            inc_by_year = _index_by_period(company.get(inc_key, []), period_type)
            for year in set(cf_by_year) & set(inc_by_year):
                fcf = _safe_float(cf_by_year[year], "freeCashFlow")
                rev = _safe_float(inc_by_year[year], "revenue")
                if fcf is not None and rev and rev > 0:
                    out.setdefault(year, []).append(fcf / rev)
        return out

    # Existing P/FCF and EV/EBITDA paths — unchanged.
    for company in all_company_data:
        km_by_year = _index_by_period(company.get(km_key, []), period_type)
        cf_by_year = _index_by_period(company.get(cf_key, []), period_type)
        inc_by_year = _index_by_period(company.get(inc_key, []), period_type)

        # P/FCF needs km ∩ cf; EV/EBITDA needs km ∩ (inc OR cf-for-D&A).
        # Union of cf/inc keys is correct for both — the reconstruction
        # functions return None when a required input is missing.
        years = set(km_by_year) & (set(cf_by_year) | set(inc_by_year))
        for year in years:
            km = km_by_year.get(year, {})
            cf = cf_by_year.get(year, {})
            inc = inc_by_year.get(year, {})

            if compute_name == "pfcf":
                value = _pfcf_from_raw(km, cf)
            elif compute_name == "ev_ebitda":
                value = _ev_ebitda_from_raw(km, cf, inc)
            elif compute_name == "earnings_yield":
                # netIncome / marketCap → a DECIMAL (e.g. 0.04). Profitable
                # companies only (>0 gate below), matching the other computed
                # ratios; loss-makers are excluded from the sector median.
                ni = _safe_float(inc, "netIncome")
                mcap = _safe_float(km, "marketCap")
                value = (ni / mcap) if (ni is not None and mcap and mcap > 0) else None
            else:
                continue

            if value is not None and value > 0:
                out.setdefault(year, []).append(value)

    return out


def _normalize_sector(raw_sector: str) -> str:
    """Map FMP sector name to canonical app sector name."""
    return _FMP_SECTOR_MAP.get(raw_sector, raw_sector)


# ── Service ───────────────────────────────────────────────────────

class SectorBenchmarkService:
    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()
        self.supabase = get_supabase()
        self._fmp_semaphore = asyncio.Semaphore(FMP_SEMAPHORE_LIMIT)

    async def _fmp_call(self, coro):
        """Wrap an FMP coroutine with the global semaphore to cap concurrency."""
        async with self._fmp_semaphore:
            return await coro

    def _benchmarks_are_fresh(self, max_age_hours: float = 23.0) -> bool:
        """Check if benchmarks were computed recently enough to skip recomputation."""
        try:
            response = (
                self.supabase.table("sector_benchmarks")
                .select("computed_at")
                .order("computed_at", desc=True)
                .limit(1)
                .execute()
            )
            if not response.data:
                return False
            last_computed = response.data[0]["computed_at"]
            # Parse ISO timestamp from Supabase
            from datetime import datetime, timezone
            if last_computed.endswith("Z"):
                last_computed = last_computed.replace("Z", "+00:00")
            last_dt = datetime.fromisoformat(last_computed)
            age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if age_hours < max_age_hours:
                logger.info(
                    f"Sector benchmarks are fresh ({age_hours:.1f}h old), skipping recomputation"
                )
                return True
            return False
        except Exception as e:
            logger.warning(f"Could not check benchmark freshness: {e}")
            return False

    def _get_existing_periods(self, sector: str) -> set:
        """Return set of (metric_name, period_type, period_label) already in DB for this sector."""
        try:
            response = (
                self.supabase.table("sector_benchmarks")
                .select("metric_name,period_type,period_label")
                .eq("sector", sector)
                .execute()
            )
            return {
                (r["metric_name"], r["period_type"], r["period_label"])
                for r in (response.data or [])
            }
        except Exception as e:
            logger.warning(f"Could not fetch existing periods for {sector}: {e}")
            return set()

    def _sector_has_history(self, existing_periods: set) -> bool:
        """Check if a sector already has deep historical data (year 2015)."""
        return any(pt == "annual" and pl == "2015" for (_, pt, pl) in existing_periods)

    async def compute_all_benchmarks(
        self,
        force: bool = False,
        backfill: bool = False,
        sectors: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Main entry: fetch constituents, group by sector, compute medians, upsert.

        Args:
            force: Skip freshness check and recompute.
            backfill: Force deep historical limits (16 annual, 80 quarterly) for all sectors.
                      If False, auto-detects per sector: sectors without 2015 data get
                      backfill, sectors with history get daily limits.
            sectors: Optional list of canonical sector names to process (default: all).
        """
        if not force and self._benchmarks_are_fresh():
            return {"rows_upserted": 0, "skipped": True, "reason": "benchmarks are fresh"}

        start = time.time()
        logger.info("Starting sector benchmark computation")

        # Step 1: get S&P 500 constituents grouped by sector
        sector_tickers = await self._get_sector_tickers()

        # Filter to requested sectors if specified
        if sectors:
            sector_tickers = {k: v for k, v in sector_tickers.items() if k in sectors}

        logger.info(
            f"Sectors to process: {list(sector_tickers.keys())} "
            f"({sum(len(v) for v in sector_tickers.values())} total companies)"
        )

        # Step 2: compute benchmarks for each sector (per-sector mode detection)
        total_upserted = 0
        for sector, tickers in sector_tickers.items():
            try:
                existing = self._get_existing_periods(sector)

                # Determine limits for this sector
                if backfill:
                    al, ql = FMP_ANNUAL_LIMIT_BACKFILL, FMP_QUARTERLY_LIMIT_BACKFILL
                    sector_mode = "backfill (forced)"
                elif not self._sector_has_history(existing):
                    al, ql = FMP_ANNUAL_LIMIT_BACKFILL, FMP_QUARTERLY_LIMIT_BACKFILL
                    sector_mode = "backfill (no historical data)"
                else:
                    al, ql = FMP_ANNUAL_LIMIT_DAILY, FMP_QUARTERLY_LIMIT_DAILY
                    sector_mode = "daily"

                logger.info(
                    f"Computing {sector} ({len(tickers)} tickers, mode={sector_mode})..."
                )
                count = await self._compute_sector(
                    sector, tickers, al, ql, existing_periods=existing,
                )
                total_upserted += count
                logger.info(f"  {sector}: done — {count} new benchmark rows upserted")
            except Exception as e:
                logger.error(f"  {sector} sector failed: {e}", exc_info=True)

        elapsed = time.time() - start
        logger.info(f"Sector benchmarks complete: {total_upserted} rows in {elapsed:.1f}s")
        return {"rows_upserted": total_upserted, "elapsed_seconds": round(elapsed, 1)}

    async def _get_sector_tickers(self) -> Dict[str, List[str]]:
        """Fetch S&P 500 constituents and group by canonical sector name."""
        constituents = await self.fmp.get_sp500_constituents()

        if not constituents:
            logger.warning("FMP sp500-constituent returned empty, using fallback tickers")
            return dict(_FALLBACK_SECTOR_TICKERS)

        sector_map: Dict[str, List[str]] = {}
        for c in constituents:
            raw_sector = c.get("sector", "")
            symbol = c.get("symbol", "")
            if not raw_sector or not symbol:
                continue
            sector = _normalize_sector(raw_sector)
            if sector not in CANONICAL_SECTORS:
                logger.debug(f"Skipping unknown sector '{sector}' (raw: '{raw_sector}') for {symbol}")
                continue
            sector_map.setdefault(sector, []).append(symbol)

        if not sector_map:
            logger.warning("No valid sectors from constituents, using fallback")
            return dict(_FALLBACK_SECTOR_TICKERS)

        return sector_map

    async def _compute_sector(
        self,
        sector: str,
        tickers: List[str],
        annual_limit: int,
        quarterly_limit: int,
        existing_periods: Optional[set] = None,
    ) -> int:
        """Fetch financial data for all tickers in a sector, compute medians, upsert."""
        all_company_data: List[Dict[str, List]] = []

        for batch_start in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[batch_start:batch_start + BATCH_SIZE]
            tasks = [self._fetch_company_data(ticker, annual_limit, quarterly_limit) for ticker in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f"  Skipping {batch[i]}: {result}")
                    continue
                if result:
                    all_company_data.append(result)

            # Delay between batches (but not after the last one)
            if batch_start + BATCH_SIZE < len(tickers):
                await asyncio.sleep(BATCH_DELAY_SECONDS)

        if not all_company_data:
            logger.warning(f"  No company data collected for {sector}")
            return 0

        # Compute medians for each metric × period_type × period_label
        now = datetime.now(timezone.utc).isoformat()
        rows_to_upsert: List[Dict[str, Any]] = []

        for metric_config in METRIC_CONFIGS:
            for period_type in ("annual", "quarterly"):
                period_values = self._collect_metric_values(
                    all_company_data, metric_config, period_type
                )
                metric_type = metric_config["type"]
                for period_label, values in period_values.items():
                    if len(values) < MIN_SAMPLE_SIZE:
                        continue
                    # Skip periods already stored (historical benchmarks never change)
                    if existing_periods and (
                        metric_config["name"], period_type, period_label
                    ) in existing_periods:
                        continue
                    # Winsorize to cap extreme outliers.
                    #   yoy / qoq → wide bounds (growth % can swing huge)
                    #   computed ratios (P/FCF, EV/EBITDA) → tight 0–200 bounds:
                    #     near-zero denominators produce 4-digit multiples that
                    #     pull the median upward; healthy ratios are <50.
                    if metric_type in ("yoy", "qoq"):
                        cleaned = _winsorize(values)
                    elif (
                        metric_type == "computed"
                        and metric_config["name"] != "fcf_margin"
                    ):
                        cleaned = _winsorize(
                            values,
                            floor=COMPUTED_RATIO_FLOOR,
                            ceil=COMPUTED_RATIO_CEIL,
                        )
                    else:
                        # direct ratios + fcf_margin (a signed decimal margin):
                        # no multiple-clamp, keep sign (negatives are real).
                        cleaned = values
                    rows_to_upsert.append({
                        "sector": sector,
                        "metric_name": metric_config["name"],
                        "period_type": period_type,
                        "period_label": period_label,
                        "median_value": round(statistics.median(cleaned), 4),
                        "sample_size": len(cleaned),
                        "computed_at": now,
                    })

        # Upsert in batches
        upserted = 0
        for i in range(0, len(rows_to_upsert), UPSERT_BATCH_SIZE):
            batch = rows_to_upsert[i:i + UPSERT_BATCH_SIZE]
            try:
                self.supabase.table("sector_benchmarks").upsert(
                    batch,
                    on_conflict="sector,metric_name,period_type,period_label",
                ).execute()
                upserted += len(batch)
            except Exception as e:
                logger.error(f"  Upsert batch failed for {sector}: {e}")

        return upserted

    async def _fetch_company_data(self, ticker: str, annual_limit: int, quarterly_limit: int) -> Dict[str, List]:
        """Fetch income, cash flow, balance sheet, ratios, and key metrics for one company (annual + quarterly).

        Balance sheet was added in Phase 3A for moat-scoring metrics
        (intangibles_to_assets, deferred_revenue_to_revenue).
        """
        results = await asyncio.gather(
            self._fmp_call(self.fmp.get_income_statement(ticker, period="annual", limit=annual_limit)),
            self._fmp_call(self.fmp.get_income_statement(ticker, period="quarter", limit=quarterly_limit)),
            self._fmp_call(self.fmp.get_cash_flow_statement(ticker, period="annual", limit=annual_limit)),
            self._fmp_call(self.fmp.get_cash_flow_statement(ticker, period="quarter", limit=quarterly_limit)),
            self._fmp_call(self.fmp.get_financial_ratios(ticker, period="annual", limit=annual_limit)),
            self._fmp_call(self.fmp.get_financial_ratios(ticker, period="quarter", limit=quarterly_limit)),
            self._fmp_call(self.fmp.get_key_metrics(ticker, period="annual", limit=annual_limit)),
            self._fmp_call(self.fmp.get_key_metrics(ticker, period="quarter", limit=quarterly_limit)),
            self._fmp_call(self.fmp.get_balance_sheet(ticker, period="annual", limit=annual_limit)),
            self._fmp_call(self.fmp.get_balance_sheet(ticker, period="quarter", limit=quarterly_limit)),
            return_exceptions=True,
        )

        def _safe_list(r: Any) -> List:
            return r if isinstance(r, list) else []

        return {
            "income_annual": _safe_list(results[0]),
            "income_quarterly": _safe_list(results[1]),
            "cashflow_annual": _safe_list(results[2]),
            "cashflow_quarterly": _safe_list(results[3]),
            "ratios_annual": _safe_list(results[4]),
            "ratios_quarterly": _safe_list(results[5]),
            "key_metrics_annual": _safe_list(results[6]),
            "key_metrics_quarterly": _safe_list(results[7]),
            "balance_annual": _safe_list(results[8]),
            "balance_quarterly": _safe_list(results[9]),
        }

    def _collect_metric_values(
        self,
        all_company_data: List[Dict[str, List]],
        metric_config: Dict[str, str],
        period_type: str,
    ) -> Dict[str, List[float]]:
        """
        For a given metric, collect values per period_label across all companies.
        Returns {"2024": [12.5, 8.3, ...], "2023": [...], ...}
        """
        metric_type = metric_config["type"]  # "yoy", "qoq", "direct", "computed"
        is_quarterly = period_type == "quarterly"

        # QoQ metrics only make sense for quarterly data
        if metric_type == "qoq" and not is_quarterly:
            return {}

        # Computed ratios (P/FCF, EV/EBITDA) need a per-company join across
        # multiple endpoints — delegated to the module-level helper.
        if metric_type == "computed":
            return _compute_ratio_values(
                all_company_data,
                compute_name=metric_config["compute"],
                period_type=period_type,
            )

        source = metric_config["source"]   # "income", "cashflow", "ratios"
        field = metric_config["field"]
        data_key = f"{source}_{period_type}"
        period_values: Dict[str, List[float]] = {}

        for company_data in all_company_data:
            records = company_data.get(data_key, [])
            if not records:
                continue

            if metric_type == "yoy":
                # Compute per-company YoY, then collect
                yoy_points = _compute_yoy_for_records(records, field, is_quarterly)
                for label, yoy_val in yoy_points.items():
                    period_values.setdefault(label, []).append(yoy_val)
            elif metric_type == "qoq":
                # Compute per-company QoQ (sequential quarter), then collect
                qoq_points = _compute_qoq_for_records(records, field)
                for label, qoq_val in qoq_points.items():
                    period_values.setdefault(label, []).append(qoq_val)
            else:
                # Direct value extraction
                for rec in records:
                    val = _safe_float(rec, field)
                    if val is None:
                        continue
                    label = (
                        _quarterly_period_label(rec) if is_quarterly
                        else _annual_period_label(rec)
                    )
                    if label:
                        period_values.setdefault(label, []).append(val)

        return period_values



# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[SectorBenchmarkService] = None


def get_sector_benchmark_service() -> SectorBenchmarkService:
    global _service
    if _service is None:
        _service = SectorBenchmarkService()
    return _service
