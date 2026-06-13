"""
Growth service — fetches income statements from FMP, computes YoY growth
percentages for EPS & Revenue, and looks up pre-computed sector median YoY
from the sector_benchmarks table.

Matches the iOS GrowthSectionData struct.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client
from app.schemas.growth import GrowthDataPointSchema, GrowthResponse
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


def _compute_yoy(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """Compute YoY growth %. Returns None ("not meaningful") whenever a
    percentage would mislead rather than inform:
      - missing data,
      - a non-positive base (previous <= 0 — there is no meaningful growth rate
        off zero or a loss), or
      - a sign change into negative (e.g. FCF swinging from +$0.4B to -$23.7B
        computes a real but absurd -5911%; downstream renders "—" instead).
    The growth-rate domain is positive→positive; outside it we report n/m so a
    sign-flip can't surface a nonsense figure in the report or Financials tab.
    """
    if current is None or previous is None:
        return None
    if previous <= 0 or current < 0:
        return None
    return round((current - previous) / previous * 100, 2)


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
    """Extract annual period label like '2021' from FMP income statement."""
    return _extract_year(record)


def _quarterly_period_label(
    record: Dict[str, Any], use_fiscal_year: bool = False
) -> str:
    """Build quarterly period label like \"Q1'21\" from FMP income statement."""
    period = record.get("period", "")  # "Q1", "Q2", etc.
    # Off-calendar fiscal years (e.g. Oracle, FY ends May 31) get non-monotonic
    # quarter LABELS when the fiscal quarter is paired with the calendar year
    # (fiscal Q1/Aug shares a calendar year with the prior fiscal Q4/May).
    # use_fiscal_year pairs it with FMP's fiscalYear ("Q1'26") for DISPLAY only;
    # the sector-benchmark join stays on the calendar label (see `_match_period`).
    if use_fiscal_year and record.get("fiscalYear"):
        year = str(record.get("fiscalYear"))
    else:
        year = _extract_year(record)
    if len(year) >= 4:
        return f"{period}'{year[-2:]}"
    return f"{period}'{year}"


def _compute_growth_points(
    records: List[Dict[str, Any]],
    metric_key: str,
    is_quarterly: bool,
) -> List[Dict[str, Any]]:
    """
    Compute YoY growth data points from sorted income statement records.

    For annual: compare consecutive years.
    For quarterly: compare same quarter in prior year.

    Returns list of dicts with period, value, yoy_change_percent.
    The oldest record(s) used only as baseline are excluded from output.
    """
    if not records:
        return []

    # Sort by date ascending (oldest first)
    sorted_recs = sorted(records, key=lambda r: r.get("date", ""))

    results = []

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
                continue  # no prior year to compare

            current_val = _safe_float(rec, metric_key)
            prev_val = _safe_float(prev_rec, metric_key)
            if current_val is None:
                continue

            results.append({
                # period = fiscal label for DISPLAY; _match_period = calendar
                # label for the sector-benchmark join (identical to the
                # calendar-keyed sector_benchmarks rows so the overlay matches).
                "period": _quarterly_period_label(rec, use_fiscal_year=True),
                "_match_period": _quarterly_period_label(rec),
                "value": current_val,
                "yoy_change_percent": _compute_yoy(current_val, prev_val),
                "cal_year": cal_year,
                "quarter": period,
            })
    else:
        # Annual: compare consecutive years (only if exactly 1 year apart)
        for i in range(1, len(sorted_recs)):
            rec = sorted_recs[i]
            prev_rec = sorted_recs[i - 1]

            # Validate year gap is exactly 1 to avoid multi-year growth mislabeled as YoY
            try:
                cur_year = int(_extract_year(rec))
                prev_year = int(_extract_year(prev_rec))
                if cur_year - prev_year != 1:
                    continue
            except (ValueError, TypeError):
                continue

            current_val = _safe_float(rec, metric_key)
            prev_val = _safe_float(prev_rec, metric_key)
            if current_val is None:
                continue

            results.append({
                "period": _annual_period_label(rec),
                "value": current_val,
                "yoy_change_percent": _compute_yoy(current_val, prev_val),
                "cal_year": _extract_year(rec),
                "quarter": None,
            })

    return results


# ── Service ───────────────────────────────────────────────────────

class GrowthService:
    def __init__(self):
        self.fmp = get_fmp_client()

    async def get_growth(self, ticker: str) -> GrowthResponse:
        """Main entry point — returns cached or freshly built growth data."""
        cache_key = f"growth:{ticker}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        result = await self._build_growth(ticker)
        _cache_set(cache_key, result)
        return result

    async def _build_growth(self, ticker: str) -> GrowthResponse:
        """Fetch income + cash flow statements, compute YoY growth, look up sector benchmarks."""

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

        # Phase 2: get sector from profile (normalize to canonical name for benchmark lookup)
        raw_sector = profile.get("sector", "") if isinstance(profile, dict) else ""
        sector = _normalize_sector(raw_sector)

        # Phase 3: compute target ticker's YoY growth for all 5 metrics
        # EPS & Revenue (from income statement)
        eps_annual_points = _compute_growth_points(annual_income, "epsDiluted", is_quarterly=False)
        eps_quarterly_points = _compute_growth_points(quarterly_income, "epsDiluted", is_quarterly=True)
        rev_annual_points = _compute_growth_points(annual_income, "revenue", is_quarterly=False)
        rev_quarterly_points = _compute_growth_points(quarterly_income, "revenue", is_quarterly=True)
        # Net Income & Operating Income (from income statement)
        ni_annual_points = _compute_growth_points(annual_income, "netIncome", is_quarterly=False)
        ni_quarterly_points = _compute_growth_points(quarterly_income, "netIncome", is_quarterly=True)
        op_annual_points = _compute_growth_points(annual_income, "operatingIncome", is_quarterly=False)
        op_quarterly_points = _compute_growth_points(quarterly_income, "operatingIncome", is_quarterly=True)
        # Free Cash Flow (from cash flow statement)
        fcf_annual_points = _compute_growth_points(annual_cashflow, "freeCashFlow", is_quarterly=False)
        fcf_quarterly_points = _compute_growth_points(quarterly_cashflow, "freeCashFlow", is_quarterly=True)

        # Phase 4: look up pre-computed sector benchmarks (fast DB lookup, cached)
        all_yoy_metrics = [
            "eps_yoy", "revenue_yoy", "net_income_yoy",
            "operating_income_yoy", "fcf_yoy",
        ]
        all_qoq_metrics = ["eps_qoq", "revenue_qoq"]

        benchmarks_annual: Dict[str, Dict[str, float]] = {}
        benchmarks_quarterly: Dict[str, Dict[str, float]] = {}
        benchmarks_qoq_quarterly: Dict[str, Dict[str, float]] = {}
        if sector:
            lookup = get_sector_benchmark_lookup()
            benchmarks_annual = lookup.get_sector_benchmarks(
                sector, all_yoy_metrics, "annual"
            )
            benchmarks_quarterly = lookup.get_sector_benchmarks(
                sector, all_yoy_metrics, "quarterly"
            )
            benchmarks_qoq_quarterly = lookup.get_sector_benchmarks(
                sector, all_qoq_metrics, "quarterly"
            )

        # Phase 5: assemble response with sector averages matched by period label
        def _to_schemas(
            points: List[Dict],
            metric_name: str,
            benchmarks: Dict[str, Dict[str, float]],
            qoq_metric_name: str = "",
            qoq_benchmarks: Optional[Dict[str, Dict[str, float]]] = None,
        ) -> List[GrowthDataPointSchema]:
            metric_benchmarks = benchmarks.get(metric_name, {})
            qoq_metric_benchmarks = (qoq_benchmarks or {}).get(qoq_metric_name, {})
            return [
                GrowthDataPointSchema(
                    period=p["period"],
                    value=p["value"],
                    yoy_change_percent=p["yoy_change_percent"],
                    # Match on the calendar key (_match_period); annual points
                    # have no _match_period and fall back to period (also calendar).
                    sector_average_yoy=metric_benchmarks.get(
                        p.get("_match_period", p["period"])
                    ),
                    sector_average_qoq=qoq_metric_benchmarks.get(
                        p.get("_match_period", p["period"])
                    ),
                )
                for p in points
            ]

        return GrowthResponse(
            symbol=ticker,
            eps_annual=_to_schemas(eps_annual_points, "eps_yoy", benchmarks_annual),
            eps_quarterly=_to_schemas(
                eps_quarterly_points, "eps_yoy", benchmarks_quarterly,
                "eps_qoq", benchmarks_qoq_quarterly,
            ),
            revenue_annual=_to_schemas(rev_annual_points, "revenue_yoy", benchmarks_annual),
            revenue_quarterly=_to_schemas(
                rev_quarterly_points, "revenue_yoy", benchmarks_quarterly,
                "revenue_qoq", benchmarks_qoq_quarterly,
            ),
            net_income_annual=_to_schemas(ni_annual_points, "net_income_yoy", benchmarks_annual),
            net_income_quarterly=_to_schemas(
                ni_quarterly_points, "net_income_yoy", benchmarks_quarterly,
            ),
            operating_profit_annual=_to_schemas(op_annual_points, "operating_income_yoy", benchmarks_annual),
            operating_profit_quarterly=_to_schemas(
                op_quarterly_points, "operating_income_yoy", benchmarks_quarterly,
            ),
            free_cash_flow_annual=_to_schemas(fcf_annual_points, "fcf_yoy", benchmarks_annual),
            free_cash_flow_quarterly=_to_schemas(
                fcf_quarterly_points, "fcf_yoy", benchmarks_quarterly,
            ),
        )


# ── Singleton ─────────────────────────────────────────────────────

_growth_service: Optional[GrowthService] = None


def get_growth_service() -> GrowthService:
    global _growth_service
    if _growth_service is None:
        _growth_service = GrowthService()
    return _growth_service
