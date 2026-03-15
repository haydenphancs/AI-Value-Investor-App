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


def _compute_yoy(current: Optional[float], previous: Optional[float]) -> float:
    """Compute YoY growth %. Returns 0.0 on division by zero or missing data."""
    if current is None or previous is None or previous == 0:
        return 0.0
    return round((current - previous) / abs(previous) * 100, 2)


def _extract_year(record: Dict[str, Any]) -> str:
    """Extract calendar year from the date field (e.g., '2024' from '2024-09-28').

    For quarterly records, uses the fiscal date to determine the calendar year.
    For Apple-like fiscal years (FY ending Sep), Q1 ending Dec belongs to the
    calendar year of the date field.
    """
    date_str = record.get("date", "")
    if len(date_str) >= 4:
        return date_str[:4]
    return ""


def _annual_period_label(record: Dict[str, Any]) -> str:
    """Extract annual period label like '2021' from FMP income statement."""
    return _extract_year(record)


def _quarterly_period_label(record: Dict[str, Any]) -> str:
    """Build quarterly period label like \"Q1'21\" from FMP income statement."""
    period = record.get("period", "")  # "Q1", "Q2", etc.
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
                "period": _quarterly_period_label(rec),
                "value": current_val,
                "yoy_change_percent": _compute_yoy(current_val, prev_val),
                "cal_year": cal_year,
                "quarter": period,
            })
    else:
        # Annual: compare consecutive years
        for i in range(1, len(sorted_recs)):
            rec = sorted_recs[i]
            prev_rec = sorted_recs[i - 1]

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
        """Fetch income statements, compute YoY growth, look up sector benchmarks."""

        # Phase 1: parallel fetch — profile + annual + quarterly income (3 FMP calls)
        profile, annual_income, quarterly_income = await asyncio.gather(
            self.fmp.get_company_profile(ticker),
            self.fmp.get_income_statement(ticker, period="annual", limit=6),
            self.fmp.get_income_statement(ticker, period="quarter", limit=24),
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

        # Phase 2: get sector from profile
        sector = profile.get("sector", "") if isinstance(profile, dict) else ""

        # Phase 3: compute target ticker's YoY growth
        eps_annual_points = _compute_growth_points(annual_income, "epsDiluted", is_quarterly=False)
        eps_quarterly_points = _compute_growth_points(quarterly_income, "epsDiluted", is_quarterly=True)
        rev_annual_points = _compute_growth_points(annual_income, "revenue", is_quarterly=False)
        rev_quarterly_points = _compute_growth_points(quarterly_income, "revenue", is_quarterly=True)

        # Phase 4: look up pre-computed sector benchmarks (fast DB lookup, cached)
        benchmarks_annual: Dict[str, Dict[str, float]] = {}
        benchmarks_quarterly: Dict[str, Dict[str, float]] = {}
        if sector:
            lookup = get_sector_benchmark_lookup()
            benchmarks_annual = lookup.get_sector_benchmarks(
                sector, ["eps_yoy", "revenue_yoy"], "annual"
            )
            benchmarks_quarterly = lookup.get_sector_benchmarks(
                sector, ["eps_yoy", "revenue_yoy"], "quarterly"
            )

        # Phase 5: assemble response with sector averages matched by period label
        def _to_schemas(
            points: List[Dict], metric_name: str, benchmarks: Dict[str, Dict[str, float]]
        ) -> List[GrowthDataPointSchema]:
            metric_benchmarks = benchmarks.get(metric_name, {})
            return [
                GrowthDataPointSchema(
                    period=p["period"],
                    value=p["value"],
                    yoy_change_percent=p["yoy_change_percent"],
                    sector_average_yoy=metric_benchmarks.get(p["period"], 0.0),
                )
                for p in points
            ]

        return GrowthResponse(
            symbol=ticker,
            eps_annual=_to_schemas(eps_annual_points, "eps_yoy", benchmarks_annual),
            eps_quarterly=_to_schemas(eps_quarterly_points, "eps_yoy", benchmarks_quarterly),
            revenue_annual=_to_schemas(rev_annual_points, "revenue_yoy", benchmarks_annual),
            revenue_quarterly=_to_schemas(rev_quarterly_points, "revenue_yoy", benchmarks_quarterly),
        )


# ── Singleton ─────────────────────────────────────────────────────

_growth_service: Optional[GrowthService] = None


def get_growth_service() -> GrowthService:
    global _growth_service
    if _growth_service is None:
        _growth_service = GrowthService()
    return _growth_service
