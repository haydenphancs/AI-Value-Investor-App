"""
Growth service — fetches income statements from FMP, computes YoY growth
percentages for EPS & Revenue, and looks up pre-computed sector median YoY
from the sector_benchmarks table.

Matches the iOS GrowthSectionData struct.
"""

import asyncio
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client
from app.utils.period_labels import quarterly_period_label
from app.schemas.growth import GrowthDataPointSchema, GrowthResponse
from app.services.sector_benchmark_lookup import (
    MATURE_SAMPLE_FLOOR,
    _period_sort_key,
    get_sector_benchmark_lookup,
)
from app.services.sector_benchmark_service import _normalize_sector

logger = logging.getLogger(__name__)


def _hold_back_thin_benchmarks(
    rich: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Dict[str, float]]:
    """Flatten rich benchmark cells to ``{metric: {period: value}}``, replacing any
    THIN period's value (sample_size < MATURE_SAMPLE_FLOOR) with the latest mature
    value AT OR BEFORE that period.

    The just-completed fiscal period is only partially reported — e.g. the
    Semiconductors FY2026 EPS-growth median is +79% from n=9 early reporters
    (mostly hypergrowth names) vs a credible +4.9% from n=77 in FY2025. Without the
    hold-back a genuine 65%-grower is scored "below sector" against a contaminated
    benchmark (see the persona-scoring validation). Mirrors the mature-sample-floor
    hold-back the current-snapshot pickers already apply (sector_benchmark_lookup).

    CRITICAL: hold back to the latest mature value that is NOT chronologically LATER
    than the thin period — never the global-latest. An OLDER thin period (e.g. an
    early year frozen at n<20 while later years grew past 20) must NOT be painted
    with a FUTURE year's median (a lookahead that corrupts that year's chart point).
    If no mature period exists at-or-before a thin period, keep its own value.
    """
    out: Dict[str, Dict[str, float]] = {}
    for metric, cells in rich.items():
        # Mature cells (n >= floor, non-null value) as (sort_key, value), oldest→newest.
        mature_sorted = sorted(
            (
                (_period_sort_key(lab), c["value"])
                for lab, c in cells.items()
                if (c.get("n") or 0) >= MATURE_SAMPLE_FLOOR and c.get("value") is not None
            ),
            key=lambda t: t[0],
        )
        flat: Dict[str, float] = {}
        for period, cell in cells.items():
            if (cell.get("n") or 0) >= MATURE_SAMPLE_FLOOR:
                flat[period] = cell["value"]
                continue
            pk = _period_sort_key(period)
            prior = [v for (sk, v) in mature_sorted if sk <= pk]
            flat[period] = prior[-1] if prior else cell["value"]
        out[metric] = flat
    return out

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
    """Safely extract a FINITE float from a dict.

    NaN / +-Inf coerce to None so a bad upstream value never reaches the
    (non-optional) growth-point ``value``. When the report freezes ``growth_chart``,
    a non-finite value would otherwise break serialization — Postgres JSONB rejects
    bare ``NaN`` / ``Infinity``, so the conditional report write would raise and the
    whole report would flip to ``status="failed"`` rather than degrading the point.
    A None value is skipped by the callers (``if current_val is None: continue``)."""
    val = record.get(key)
    if val is None:
        return None
    try:
        result = float(val)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def _compute_yoy(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """Year-over-year % change, SIGN-CORRECTED for negative bases.

    Uses abs(previous) in the denominator so the SIGN is always meaningful — an
    improvement (current > previous) reads positive and a deterioration reads
    negative, even when the base is negative (a deepening loss correctly reads
    negative instead of the +% that naive negative÷negative would give). The
    magnitude can be large across a sign change (e.g. +$0.4B → -$23.7B ≈ -5900%);
    that value is still CORRECT and is shown verbatim — the chart's YoY line uses
    a robust/compressed scale so one outlier doesn't flatten the rest. Matches
    the collector's _safe_pct_change convention. None only when an endpoint is
    missing or the base is exactly zero (undefined).
    """
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 2)


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

            current_val = _safe_float(rec, metric_key)
            if current_val is None:
                continue  # no chartable value for this quarter

            # A missing prior-year same quarter (FMP gap) must NOT drop the bar —
            # it has a real, chartable value. Emit it with a null YoY, mirroring
            # the annual branch's 'always emit the bar' invariant.
            prev_rec = lookup.get((period, prev_year))
            prev_val = _safe_float(prev_rec, metric_key) if prev_rec is not None else None

            results.append({
                # period = fiscal label for DISPLAY; _match_period = calendar
                # label for the sector-benchmark join (identical to the
                # calendar-keyed sector_benchmarks rows so the overlay matches).
                "period": quarterly_period_label(rec, use_fiscal_year=True),
                "_match_period": _quarterly_period_label(rec),
                "value": current_val,
                "yoy_change_percent": _compute_yoy(current_val, prev_val),
                "cal_year": cal_year,
                "quarter": period,
            })
    else:
        # Annual: every later year with a finite value gets a bar. The year-gap
        # check only governs whether a YoY is MEANINGFUL — it must NOT drop the
        # bar (a gap year still has a real, chartable value). Mirror the
        # negative-value path: emit the bar, null the YoY, break the line.
        for i in range(1, len(sorted_recs)):
            rec = sorted_recs[i]
            prev_rec = sorted_recs[i - 1]

            current_val = _safe_float(rec, metric_key)
            if current_val is None:
                continue  # non-finite / missing value: genuinely unchartable

            # YoY only when prev is exactly the prior calendar year; otherwise
            # emit the bar with a null YoY (a multi-year gap is a discontinuity,
            # not zero growth) so the value still charts.
            try:
                cur_year = int(_extract_year(rec))
                prev_year = int(_extract_year(prev_rec))
                if cur_year - prev_year == 1:
                    yoy = _compute_yoy(current_val, _safe_float(prev_rec, metric_key))
                else:
                    logger.warning(
                        "growth annual year gap %s->%s for metric=%s; "
                        "emitting bar with null YoY",
                        prev_year, cur_year, metric_key,
                    )
                    yoy = None
            except (ValueError, TypeError):
                yoy = None

            results.append({
                "period": _annual_period_label(rec),
                "value": current_val,
                "yoy_change_percent": yoy,
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
        # Industry-relative benchmarks: prefer the company's INDUSTRY peer group,
        # fall back to its sector per (metric, period). FMP industry names match
        # the benchmark table directly, so no normalization is needed.
        industry = profile.get("industry", "") if isinstance(profile, dict) else ""

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
            # Hold thin just-completed periods back to the last mature (n>=20) value
            # so a contaminated latest-FY median can't make a real grower read weak.
            benchmarks_annual = _hold_back_thin_benchmarks(
                lookup.get_benchmarks(industry, sector, all_yoy_metrics, "annual")
            )
            benchmarks_quarterly = _hold_back_thin_benchmarks(
                lookup.get_benchmarks(industry, sector, all_yoy_metrics, "quarterly")
            )
            benchmarks_qoq_quarterly = _hold_back_thin_benchmarks(
                lookup.get_benchmarks(industry, sector, all_qoq_metrics, "quarterly")
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
