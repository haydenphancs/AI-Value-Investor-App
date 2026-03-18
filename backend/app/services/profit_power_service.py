"""
Profit Power service — fetches income + cash flow statements from FMP,
computes margin percentages (gross, operating, FCF, net), and looks up
pre-computed sector median net margin from the sector_benchmarks table.

Matches the iOS ProfitPowerSectionData struct.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client
from app.schemas.profit_power import ProfitPowerDataPointSchema, ProfitPowerResponse
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
    to their reporting calendar year.
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


def _compute_margin(numerator: Optional[float], revenue: Optional[float]) -> Optional[float]:
    """Compute margin as percentage. Returns None if revenue is zero/missing."""
    if numerator is None or revenue is None or revenue == 0:
        return None
    return round(numerator / revenue * 100, 2)


def _build_margin_points(
    income_records: List[Dict[str, Any]],
    cashflow_records: List[Dict[str, Any]],
    is_quarterly: bool,
) -> List[Dict[str, Any]]:
    """
    Compute margin data points from income + cash flow statements.

    For each income statement period, computes gross/operating/net margins
    from income data and FCF margin from cash flow data (matched by date).
    """
    if not income_records:
        return []

    # Sort by date ascending
    sorted_income = sorted(income_records, key=lambda r: r.get("date", ""))

    # Build cash flow lookup by date for FCF matching
    cf_by_date: Dict[str, Dict[str, Any]] = {}
    for rec in cashflow_records:
        date = rec.get("date", "")
        if date:
            cf_by_date[date] = rec

    results = []
    for rec in sorted_income:
        revenue = _safe_float(rec, "revenue")
        if revenue is None or revenue == 0:
            continue

        label = _quarterly_period_label(rec) if is_quarterly else _annual_period_label(rec)
        if not label:
            continue

        gross_profit = _safe_float(rec, "grossProfit")
        operating_income = _safe_float(rec, "operatingIncome")
        net_income = _safe_float(rec, "netIncome")

        # Match cash flow by date for FCF
        cf_rec = cf_by_date.get(rec.get("date", ""), {})
        free_cash_flow = _safe_float(cf_rec, "freeCashFlow")

        results.append({
            "period": label,
            "gross_margin": _compute_margin(gross_profit, revenue),
            "operating_margin": _compute_margin(operating_income, revenue),
            "fcf_margin": _compute_margin(free_cash_flow, revenue),
            "net_margin": _compute_margin(net_income, revenue),
        })

    return results


# ── Service ───────────────────────────────────────────────────────

class ProfitPowerService:
    def __init__(self):
        self.fmp = get_fmp_client()

    async def get_profit_power(self, ticker: str) -> ProfitPowerResponse:
        """Main entry point — returns cached or freshly built profit power data."""
        cache_key = f"profit_power:{ticker}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        result = await self._build_profit_power(ticker)
        _cache_set(cache_key, result)
        return result

    async def _build_profit_power(self, ticker: str) -> ProfitPowerResponse:
        """Fetch income + cash flow, compute margins, look up sector benchmarks."""

        # Phase 1: parallel fetch — profile + income + cash flow (5 FMP calls)
        (
            profile,
            annual_income,
            quarterly_income,
            annual_cashflow,
            quarterly_cashflow,
        ) = await asyncio.gather(
            self.fmp.get_company_profile(ticker),
            self.fmp.get_income_statement(ticker, period="annual", limit=16),
            self.fmp.get_income_statement(ticker, period="quarter", limit=80),
            self.fmp.get_cash_flow_statement(ticker, period="annual", limit=16),
            self.fmp.get_cash_flow_statement(ticker, period="quarter", limit=80),
            return_exceptions=True,
        )

        # Handle failures gracefully
        if isinstance(profile, Exception):
            logger.warning(f"Profile fetch failed for {ticker}: {profile}")
            profile = {}
        if isinstance(annual_income, Exception):
            logger.error(f"Annual income fetch failed for {ticker}: {annual_income}")
            annual_income = []
        if isinstance(quarterly_income, Exception):
            logger.error(f"Quarterly income fetch failed for {ticker}: {quarterly_income}")
            quarterly_income = []
        if isinstance(annual_cashflow, Exception):
            logger.error(f"Annual cash flow fetch failed for {ticker}: {annual_cashflow}")
            annual_cashflow = []
        if isinstance(quarterly_cashflow, Exception):
            logger.error(f"Quarterly cash flow fetch failed for {ticker}: {quarterly_cashflow}")
            quarterly_cashflow = []

        # Phase 2: get sector from profile
        raw_sector = profile.get("sector", "") if isinstance(profile, dict) else ""
        sector = _normalize_sector(raw_sector)

        # Phase 3: compute company margins for each period
        annual_points = _build_margin_points(annual_income, annual_cashflow, is_quarterly=False)
        quarterly_points = _build_margin_points(quarterly_income, quarterly_cashflow, is_quarterly=True)

        # Phase 4: look up pre-computed sector benchmark for net_margin
        benchmarks_annual: Dict[str, Dict[str, float]] = {}
        benchmarks_quarterly: Dict[str, Dict[str, float]] = {}
        if sector:
            lookup = get_sector_benchmark_lookup()
            benchmarks_annual = lookup.get_sector_benchmarks(
                sector, ["net_margin"], "annual"
            )
            benchmarks_quarterly = lookup.get_sector_benchmarks(
                sector, ["net_margin"], "quarterly"
            )

        # Phase 5: attach sector averages and build response
        # Note: FMP stores netProfitMargin as decimal (0.12 = 12%).
        # sector_benchmarks stores the raw decimal. Multiply by 100 for percentage.
        def _to_schemas(
            points: List[Dict],
            benchmarks: Dict[str, Dict[str, float]],
        ) -> List[ProfitPowerDataPointSchema]:
            net_margin_benchmarks = benchmarks.get("net_margin", {})
            schemas = []
            for p in points:
                raw_benchmark = net_margin_benchmarks.get(p["period"])
                sector_avg = round(raw_benchmark * 100, 2) if raw_benchmark is not None else None
                schemas.append(ProfitPowerDataPointSchema(
                    period=p["period"],
                    gross_margin=p["gross_margin"],
                    operating_margin=p["operating_margin"],
                    fcf_margin=p["fcf_margin"],
                    net_margin=p["net_margin"],
                    sector_average_net_margin=sector_avg,
                ))
            return schemas

        return ProfitPowerResponse(
            symbol=ticker,
            annual=_to_schemas(annual_points, benchmarks_annual),
            quarterly=_to_schemas(quarterly_points, benchmarks_quarterly),
        )


# ── Singleton ─────────────────────────────────────────────────────

_profit_power_service: Optional[ProfitPowerService] = None


def get_profit_power_service() -> ProfitPowerService:
    global _profit_power_service
    if _profit_power_service is None:
        _profit_power_service = ProfitPowerService()
    return _profit_power_service
