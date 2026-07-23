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
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.utils.period_labels import extract_year as _extract_year, quarterly_period_label
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


# ── In-flight deduplication ───────────────────────────────────────
# One growth MISS costs TEN FMP calls. Without this, N concurrent viewers of the
# same cold ticker each fired the whole fan-out.
_inflight: Dict[str, asyncio.Future] = {}


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


def _as_list(payload: Any) -> List[Dict[str, Any]]:
    """Normalize an FMP payload to a list of record dicts.

    ``FMPClient._make_request`` is typed ``-> Any`` and documented "list or dict":
    on some error shapes FMP answers 200 with a bare object. Iterating that dict
    yields its string KEYS, and the first ``rec.get(...)`` raises
    ``AttributeError: 'str' object has no attribute 'get'`` → a bare 502 for the
    whole section. Degrade to an empty series instead, loudly.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if payload:
        logger.warning(
            "growth: expected a list from FMP, got %s — degrading to empty series",
            type(payload).__name__,
        )
    return []


def _sort_key_date(record: Dict[str, Any]) -> str:
    """Sort key for an FMP record's period-end date.

    ``record.get("date", "")`` returns **None** when the key is present and null
    (the default only applies to a MISSING key), and ``None < str`` raises
    ``TypeError`` — 502-ing the section on one malformed upstream row. Coerce.
    """
    return record.get("date") or ""


# ``_extract_year`` is imported from app.utils.period_labels — the shared version
# is null-safe (``record.get("date") or ""``); the local copy this replaces did
# ``len(record.get("date", ""))`` and raised TypeError on a null date.


def _annual_period_label(record: Dict[str, Any]) -> str:
    """Extract annual period label like '2021' from FMP income statement."""
    return _extract_year(record)


def _quarterly_period_label(
    record: Dict[str, Any], use_fiscal_year: bool = False
) -> str:
    """Build quarterly period label like \"Q1'21\" from FMP income statement."""
    period = record.get("period") or ""  # "Q1", "Q2", etc. (null-safe)
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
    records = _as_list(records)
    if not records:
        return []

    # Sort by date ascending (oldest first)
    sorted_recs = sorted(records, key=_sort_key_date)

    results = []

    if is_quarterly:
        # Build lookup: (period, year) -> record
        lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for rec in sorted_recs:
            p = rec.get("period") or ""
            cy = _extract_year(rec)
            lookup[(p, cy)] = rec

        for rec in sorted_recs:
            period = rec.get("period") or ""
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
        self.supabase = get_supabase()

    async def get_growth(self, ticker: str) -> GrowthResponse:
        """Main entry point — two-tier cache-aside with in-flight dedup.

        Tier 1: in-memory dict (5 min) · Tier 2: Supabase ``growth_cache``
        (24h, invalidated early by the next earnings date). Mirrors
        profit_power_service, the reference template.
        """
        # UNIQUE(ticker) in growth_cache is case-SENSITIVE, so "aapl" and "AAPL"
        # would occupy two rows and cost two FMP fan-outs (and the
        # profit_power_cache lookup below would miss). Every current caller
        # already uppercases; normalise here so that stays true.
        ticker = ticker.upper().strip()
        cache_key = f"growth:{ticker}"

        # ── Tier 1: in-memory ──
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        # ── Tier 2: Supabase (in a thread — the SDK is sync) ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Growth Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight dedup ──
        if cache_key in _inflight:
            logger.info(f"Growth in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Growth cache MISS for {ticker} — fetching from FMP")
            result = await self._build_growth(ticker)
            next_earnings = await asyncio.to_thread(
                self._next_earnings_date_safe, ticker
            )

            # Best-effort write-through (never blocks the response).
            loop.run_in_executor(
                None, self._upsert_supabase_cache_safe, ticker, result, next_earnings
            )

            _cache_set(cache_key, result)
            future.set_result(result)
            return result
        except Exception as e:
            future.set_exception(e)
            raise
        finally:
            # CancelledError is a BaseException, so the `except Exception` above
            # does NOT resolve the future when this coroutine is cancelled (a
            # client disconnect mid-fetch). Any joiner awaiting this future would
            # then hang forever. Cancel it so joiners get a CancelledError.
            if not future.done():
                future.cancel()
            _inflight.pop(cache_key, None)

    # ── Supabase helpers ──────────────────────────────────────────

    def _check_supabase_cache(self, ticker: str) -> Optional[GrowthResponse]:
        """Return the cached response if fresh (<24h and before next earnings).
        Synchronous — call via asyncio.to_thread()."""
        try:
            row = (
                self.supabase.table("growth_cache")
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
            if cached_at.tzinfo is None:  # defensive: column is timestamptz
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Growth Supabase cache STALE (age={age}) for {ticker}")
                return None

            next_earnings = entry.get("next_earnings_date")
            if next_earnings:
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today_str >= next_earnings:
                    logger.info(
                        f"Growth Supabase cache STALE (past earnings {next_earnings}) "
                        f"for {ticker}"
                    )
                    return None

            return GrowthResponse(**entry["response_json"])
        except Exception as e:
            logger.warning(f"Growth Supabase cache check failed for {ticker}: {e}")
            return None

    def _next_earnings_date_safe(self, ticker: str) -> Optional[str]:
        """Reuse the profit-power cache's next-earnings date when present.

        Growth doesn't fetch the earnings calendar itself (it would be an 11th
        FMP call); the sibling cache for the same ticker already stores it, so
        read it opportunistically. None just means "expire on the 24h TTL".

        The date MUST still be in the future. profit_power writes a strictly
        future date, but that date decays as its row ages and only refreshes
        when someone hits the profit-power path — so on any day after a company
        reports but before profit_power is re-fetched, copying it verbatim would
        write a row that our own freshness check (``today >= next_earnings``)
        rejects on the very next read. That row is born stale: the Supabase tier
        would never hit for that ticker and every 5-minute window would re-run
        the 10-call FMP fan-out this cache exists to prevent.
        """
        try:
            row = (
                self.supabase.table("profit_power_cache")
                .select("next_earnings_date")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
            if row.data:
                candidate = row.data[0].get("next_earnings_date")
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if candidate and candidate > today_str:
                    return candidate
                if candidate:
                    logger.info(
                        "Growth %s: ignoring stale next_earnings_date %s from "
                        "profit_power_cache (<= today) — using the 24h TTL",
                        ticker, candidate,
                    )
        except Exception as e:
            logger.warning(f"Growth next-earnings lookup failed for {ticker}: {e}")
        return None

    def _upsert_supabase_cache_safe(
        self,
        ticker: str,
        result: GrowthResponse,
        next_earnings: Optional[str],
    ) -> None:
        """Write-through to the Supabase tier. Best-effort: logged, never fatal."""
        try:
            self.supabase.table("growth_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": next_earnings,
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(f"Growth Supabase upsert failed for {ticker}: {e}")

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
