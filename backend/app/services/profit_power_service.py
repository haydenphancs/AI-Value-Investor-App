"""
Profit Power service — fetches income + cash flow statements from FMP,
computes margin percentages (gross, operating, FCF, net), and looks up
pre-computed sector median net margin from the sector_benchmarks table.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``profit_power_cache`` table (24-hour TTL + earnings-aware)

Matches the iOS ProfitPowerSectionData struct.
"""

import asyncio
import math
import logging
import re
import time
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
from app.integrations.fmp import get_fmp_client
from app.utils.period_labels import (
    annual_benchmark_key,
    annual_fiscal_year,
    calendar_quarter_label,
    extract_year as _extract_year,
    quarterly_period_label,
)
from app.schemas.profit_power import ProfitPowerDataPointSchema, ProfitPowerResponse
from app.services.financials_metric_gate import (
    comparable_peer_metrics,
    resolve_payment_network,
    trailing_interest_row,
)
from app.services.sector_benchmark_lookup import (
    CALENDAR_QUARTER_PERIOD_TYPE,
    benchmark_levels,
    flatten_benchmark_values,
    get_sector_benchmark_lookup,
    lookup_failed,
    series_peer_level,
)
from app.services.sector_benchmark_service import _normalize_sector

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes
# A DEGRADED build (a failed FMP leg) is never persisted to Supabase and lives only this
# long in memory: long enough to absorb a burst of re-opens during an outage, short
# enough that the next visit after the upstream recovers gets a complete build. The TTL
# is read off the cached value itself, so a Tier-1 hit can never outlive its own
# degradation.
_DEGRADED_CACHE_TTL = 60

# Bumped whenever the MEANING of a persisted payload changes, so rows written by an
# older build are rebuilt instead of served for up to 24h. v2 (2026-09-30): negative /
# zero revenue is a gap (was a sign-flipped margin or a dropped year), annual labels are
# the FISCAL year, thin benchmark cells are held back, the quarterly peer line is hidden
# for off-calendar filers, and `degraded` was added. v3 (2026-09-30, round 2): a quarter
# whose legacy benchmark key names another calendar quarter (a Jan 1-7 Q4) has no peer
# value, and a failed benchmark lookup is `degraded` — a v2 row (e.g. written by a local
# run of the first pass against the shared database) can hold either defect.
# v4 (2026-09-30): quarterly peers from calendar-quarter rows. v5 (2026-10-01, P20): the
# earnings calendar is fetched with raise_errors=True and a failed fetch is never
# persisted, so a NULL `next_earnings_date` now means "no pending announcement"; an older
# row's NULL may be a swallowed calendar 429 (no report-day bound), so it is rebuilt.
# Growth copies this table's column WITHOUT a version check, so this bump does not reach it.
# v6 (2026-10-07): ONE peer group per chart line (`get_benchmark_series`): a metric's
# line is its industry's medians when that industry is mature (>= 20 companies) at the
# line's NEWEST period — its older, thinner periods included, each that industry's own
# median — else the sector's. A period that is not fully reported is hidden, and no
# period borrows another period's value. The legend level is read off the drawn points.
# v7 (2026-10-08, PP-LEVEL-1): `peer_group_levels` names each drawn line on its own —
# "annual"/"quarterly" are now the NET-margin line's level (was a vote pooled over all
# four margins), plus per-metric "annual.<metric>" / "quarterly.<metric>" keys — so a v6
# row can label a sector line "Industry" and lacks the keys the report drill-down reads.
# v8 (2026-10-09, owner decision NET-4): a listed non-lender member of "Financial - Credit
# Services" (`financials_metric_gate.NON_LENDER_MEMBERS`) is never compared with that
# industry's median (a lenders' yardstick): no margin peer line, `peer_group_levels` empty,
# no benchmark read (`comparable_peer_metrics`) — so Cay AI's peer net-margin sentence is
# gone for them too. A v7 row of V drew the lenders' margins beside V's.
_PP_PAYLOAD_VERSION = 8
_VERSION_KEY = "payload_version"

# The margin whose peer line the live Profit Power card draws (and Cay AI quotes): its
# level is the per-tab "annual" / "quarterly" key and the legacy `peer_group_level`.
_HEADLINE_PEER_METRIC = "net_margin"


def _drawable_cells(table: Dict[str, Any]) -> Dict[str, float]:
    """The benchmark cells `_to_schemas` actually draws: finite numbers only. A NaN / inf /
    non-numeric cell is dropped there (loudly), so it must not vote on the line's legend
    level either — a line's level names the points on screen, nothing else."""
    return {
        key: raw for key, raw in table.items()
        if not isinstance(raw, bool) and isinstance(raw, (int, float)) and math.isfinite(raw)
    }


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    ttl = _DEGRADED_CACHE_TTL if getattr(value, "degraded", None) else _CACHE_TTL
    if time.time() - ts > ttl:
        _cache.pop(key, None)
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
# Prevents thundering herd: if two requests arrive for the same ticker
# while the cache is cold, only one FMP fetch runs; the other awaits.
_inflight: Dict[str, asyncio.Future] = {}


# ── Ticker validation ────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _validate_ticker(ticker: str) -> str:
    """Validate and normalize ticker symbol. Raises ValueError if invalid."""
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


# ── Helpers ───────────────────────────────────────────────────────

def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    """Safely extract a float value from a dict."""
    val = record.get(key)
    if val is None:
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def _as_list(payload: Any) -> List[Dict[str, Any]]:
    """Normalize an FMP payload to a list of record dicts.

    ``FMPClient._make_request`` is typed ``-> Any`` ("list or dict"): an error
    shape returned with a 200 iterates as string KEYS, and the first
    ``rec.get(...)`` raises ``AttributeError`` → a bare 502. Degrade loudly.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if payload:
        logger.warning(
            "profit_power: expected a list from FMP, got %s — degrading to empty series",
            type(payload).__name__,
        )
    return []


def _sort_key_date(record: Dict[str, Any]) -> str:
    """Null-safe period-end sort key — ``.get("date", "")`` yields None for a
    present-but-null key, and ``None < str`` raises TypeError."""
    return record.get("date") or ""


# ``_extract_year`` is imported from app.utils.period_labels (null-safe); the
# local copy this replaces did ``len(record.get("date", ""))`` → TypeError on a
# null date.


def _annual_period_label(record: Dict[str, Any]) -> str:
    """Annual period label like '2024' — the FISCAL year (``annual_fiscal_year``).

    Was the period-end calendar year (``date[:4]``; /stable no longer ships
    ``calendarYear``). A 52/53-week filer whose year ends on the Saturday nearest
    Dec 31 (Cadence: FY2021 ended 2022-01-01, FY2022 2022-12-31) then read
    ... 2019, 2021, 2022, 2022: one year missing, one duplicated — while the
    Quarterly tab of the same card named that year by its fiscal year.
    """
    return annual_fiscal_year(record)


def _quarterly_period_label(
    record: Dict[str, Any], use_fiscal_year: bool = False
) -> str:
    """Quarterly period label like \"Q1'24\"."""
    period = record.get("period") or ""  # "Q1", "Q2", etc. (null-safe)
    # Off-calendar fiscal years (e.g. Oracle, FY ends May 31) get non-monotonic
    # quarter LABELS when the fiscal quarter is paired with the calendar year
    # (fiscal Q1/Aug shares a calendar year with the prior fiscal Q4/May).
    # use_fiscal_year pairs it with FMP's fiscalYear ("Q1'26") for DISPLAY only;
    # the sector-benchmark join is the CALENDAR quarter (`_quarterly_join_key`).
    if use_fiscal_year and record.get("fiscalYear"):
        year = str(record.get("fiscalYear"))
    else:
        year = _extract_year(record)
    if len(year) >= 4:
        return f"{period}'{year[-2:]}"
    return f"{period}'{year}"


def _quarterly_join_key(record: Dict[str, Any], ticker: str = "") -> str:
    """Benchmark JOIN key of one QUARTERLY row: the CALENDAR quarter its period ends in
    (``period_labels.calendar_quarter_label``; an end on day 1-7 counts as the previous
    month, so Cadence's Q4 closing 2026-01-03 is Q4'25). The stored quarterly
    benchmarks are keyed the same way (period_type ``calendar_quarter``, migration 184),
    so an off-calendar company (Apple, Microsoft, Nvidia, the Jan-year-end retailers)
    meets its peers' SAME calendar quarter — no off-calendar hide is needed any more.
    ``""`` (no peer value) for an undated row. Same rule as growth_service, so the two
    cards agree on which quarter a peer value belongs to.
    """
    key = calendar_quarter_label(record)
    if not key:
        logger.info(
            "profit_power[%s]: quarterly row with no usable period end (%r) — no peer "
            "value for it", ticker, record.get("date") if isinstance(record, dict) else None,
        )
    return key


#: |margin| beyond this (in %) is almost always a tiny-revenue period — a biotech's
#: $0.2M collaboration quarter against an $80M loss is −40,000%. It is REAL data (the
#: tooltip shows it; the chart pins it to the plot edge with an off-scale marker), so it
#: is kept, but it is logged: a vendor glitch (a dropped digit in revenue) looks the same.
_EXTREME_MARGIN_PCT = 1000.0

#: Cap on how many periods one log line lists (an 80-quarter pre-revenue history).
_LOG_PERIODS_MAX = 8


def _compute_margin(numerator: Optional[float], revenue: Optional[float]) -> Optional[float]:
    """Margin as a percentage. None when the numerator is missing or revenue is
    missing, zero or NEGATIVE.

    A negative revenue (an mREIT / BDC booking hedge losses through revenue, a revenue
    reversal, a derived Q4 = FY − 9M that goes negative) SIGN-FLIPS the ratio: a $1.25B
    loss on −$1.1B revenue read as a +113.6% net margin and scored 5/5 in the
    profitability snapshot. The benchmark side already refuses it
    (industry_benchmark_service requires rev > 0); this is the company-side twin.
    """
    if numerator is None or revenue is None or revenue <= 0:
        return None
    return round(numerator / revenue * 100, 2)


_MARGIN_KEYS = ("gross_margin", "operating_margin", "fcf_margin", "net_margin")


def _collapse_duplicate_periods(
    points: List[Dict[str, Any]], ticker: str,
) -> List[Dict[str, Any]]:
    """Last resort after the fiscal-year label: two annual rows that still share a
    display label (an FMP restatement row, a fiscal-year-end change) keep only the
    LATEST-dated one. Points arrive sorted by date, so the last occurrence wins."""
    last_index: Dict[str, int] = {}
    for i, p in enumerate(points):
        last_index[p["period"]] = i
    if len(last_index) == len(points):
        return points
    dupes = sorted({p["period"] for i, p in enumerate(points) if last_index[p["period"]] != i})
    logger.warning(
        "profit_power[%s]: duplicate annual period label(s) %s after the fiscal-year "
        "label — keeping the latest-dated row of each",
        ticker or "?", dupes,
    )
    keep = set(last_index.values())
    return [p for i, p in enumerate(points) if i in keep]


def _build_margin_points(
    income_records: List[Dict[str, Any]],
    cashflow_records: List[Dict[str, Any]],
    is_quarterly: bool,
    ticker: str = "",
) -> List[Dict[str, Any]]:
    """
    Compute margin data points from income + cash flow statements.

    For each income statement period, computes gross/operating/net margins
    from income data and FCF margin from cash flow data (matched by date).

    A period whose revenue is missing, zero or negative is KEPT with all four margins
    None (a gap), not dropped. Dropping it made `annual[-1]` an OLDER year, which the
    profitability snapshot then presented and scored as "current" (a biotech's 2023
    milestone year shown in 2026), and the chart's right edge silently skipped years.
    Only a LEADING run of such periods is trimmed: before the first period with any
    margin there is no line to break, and an all-gap history keeps the card's honest
    "not available" state instead of an empty axis.
    """
    income_records = _as_list(income_records)
    cashflow_records = _as_list(cashflow_records)
    if not income_records:
        return []

    # Sort by date ascending
    sorted_income = sorted(income_records, key=_sort_key_date)

    # Build cash flow lookup by date for FCF matching
    cf_by_date: Dict[str, Dict[str, Any]] = {}
    for rec in cashflow_records:
        date = rec.get("date") or ""
        if date:
            cf_by_date[date] = rec

    results: List[Dict[str, Any]] = []
    no_revenue: List[str] = []          # revenue missing / zero → kept as a gap
    negative_revenue: List[str] = []    # revenue < 0 → kept as a gap (would sign-flip)
    extreme: List[str] = []             # |margin| > _EXTREME_MARGIN_PCT (kept, logged)
    for rec in sorted_income:
        if is_quarterly:
            label = quarterly_period_label(rec, use_fiscal_year=True)   # fiscal display
            match_period = _quarterly_join_key(rec, ticker)              # calendar-quarter join key
        else:
            # Display on the FISCAL year; JOIN on `annual_benchmark_key` (the year of
            # the period end minus 7 days). The fiscal year is the wrong join for a
            # start-year-named FY (Home Depot's FY2025 ends 2026-02-01 → it would meet
            # its same-calendar retail peers' PRIOR year), and the raw end-date year is
            # wrong for a Jan 1-7 year end (Cadence FY2021 ends 2022-01-01 → its peers'
            # NEXT year). Shared with growth_service so the two cards agree.
            label = _annual_period_label(rec)
            match_period = annual_benchmark_key(rec) or label
        if not label:
            continue

        revenue = _safe_float(rec, "revenue")
        if revenue is None or revenue <= 0:
            if revenue is not None and revenue < 0:
                negative_revenue.append(f"{label} (revenue {revenue:,.0f})")
            else:
                no_revenue.append(label)
            results.append({
                "period": label,
                "_match_period": match_period,
                **{k: None for k in _MARGIN_KEYS},
            })
            continue

        gross_profit = _safe_float(rec, "grossProfit")
        operating_income = _safe_float(rec, "operatingIncome")
        net_income = _safe_float(rec, "netIncome")

        # Match cash flow by date for FCF
        cf_rec = cf_by_date.get(rec.get("date") or "", {})
        free_cash_flow = _safe_float(cf_rec, "freeCashFlow")

        point = {
            "period": label,                 # fiscal label (display)
            "_match_period": match_period,    # benchmark join key ("" = no peer value)
            "gross_margin": _compute_margin(gross_profit, revenue),
            "operating_margin": _compute_margin(operating_income, revenue),
            "fcf_margin": _compute_margin(free_cash_flow, revenue),
            "net_margin": _compute_margin(net_income, revenue),
        }
        if any(
            point[k] is not None and abs(point[k]) > _EXTREME_MARGIN_PCT for k in _MARGIN_KEYS
        ):
            extreme.append(label)
        results.append(point)

    # Trim the LEADING run of all-gap periods (see the docstring); interior and trailing
    # gaps stay so the latest period is really the latest.
    first = next(
        (i for i, p in enumerate(results) if any(p[k] is not None for k in _MARGIN_KEYS)),
        len(results),
    )
    results = results[first:]

    kind = "quarterly" if is_quarterly else "annual"
    if negative_revenue:
        logger.warning(
            "profit_power[%s]: %d %s period(s) with NEGATIVE revenue — margins left empty "
            "(a gap), never sign-flipped: %s",
            ticker or "?", len(negative_revenue), kind, negative_revenue[-_LOG_PERIODS_MAX:],
        )
    if no_revenue:
        logger.info(
            "profit_power[%s]: %d %s period(s) with zero/missing revenue — no margins "
            "(gap; leading ones trimmed): %s",
            ticker or "?", len(no_revenue), kind, no_revenue[-_LOG_PERIODS_MAX:],
        )
    if extreme:
        logger.warning(
            "profit_power[%s]: %d %s period(s) with |margin| > %.0f%% (tiny revenue, or a "
            "vendor revenue glitch) — served as-is, pinned on the chart: %s",
            ticker or "?", len(extreme), kind, _EXTREME_MARGIN_PCT, extreme[-_LOG_PERIODS_MAX:],
        )

    if not is_quarterly:
        results = _collapse_duplicate_periods(results, ticker)
    return results


# ── Service ───────────────────────────────────────────────────────

class ProfitPowerService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_profit_power(self, ticker: str) -> ProfitPowerResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"profit_power:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Profit power in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache (run in thread to avoid blocking event loop) ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Profit power Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        # If another request is already fetching this ticker, wait for it
        if cache_key in _inflight:
            logger.info(f"Profit power in-flight JOIN for {ticker}")
            return await asyncio.shield(_inflight[cache_key])

        # Create a future so other concurrent requests can wait on us
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            # ── Cache miss: build from FMP ──
            logger.info(f"Profit power cache MISS for {ticker} — fetching from FMP")
            result, next_earnings, degraded = await self._build_profit_power(ticker)

            # Persist to Supabase in background thread (truly fire-and-forget) — but ONLY
            # when the build was complete. Persisting a degraded build pins the hole for 24h
            # and bakes it into paid reports; the 5-min in-memory tier below still absorbs
            # the retry storm, so the cost of skipping is one extra FMP fan-out per 5 min.
            if degraded:
                logger.warning(
                    "Profit power NOT persisted for %s (degraded: %s) — will rebuild after "
                    "the %ds in-memory TTL",
                    ticker, ", ".join(degraded), _DEGRADED_CACHE_TTL,
                )
            elif next_earnings is CALENDAR_UNKNOWN:
                # Complete values (the calendar feeds none), but no report-day bound.
                logger.warning(
                    "Profit power NOT persisted for %s (earnings calendar unavailable — no "
                    "report-day bound for the 24h row); served from memory", ticker,
                )
            else:
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

    def _check_supabase_cache(self, ticker: str) -> Optional[ProfitPowerResponse]:
        """Return cached response if fresh (< 24h and before next earnings).
        This is a synchronous method — call via asyncio.to_thread().
        """
        try:
            row = (
                self.supabase.table("profit_power_cache")
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

            # Parse cached_at and check 24-hour freshness
            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Supabase cache STALE (age={age}) for {ticker}")
                return None

            # Check next earnings date — invalidate if we've passed it
            next_earnings = entry.get("next_earnings_date")
            if next_earnings:
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today_str >= next_earnings:
                    logger.info(f"Supabase cache STALE (past earnings {next_earnings}) for {ticker}")
                    return None

            # A row written by an older build means something different (see
            # `_PP_PAYLOAD_VERSION`) — rebuild it rather than serve it for up to 24h.
            json_data = entry["response_json"]
            version = json_data.get(_VERSION_KEY) if isinstance(json_data, dict) else None
            if version != _PP_PAYLOAD_VERSION:
                logger.info(
                    "Supabase cache STALE for %s (payload_version=%r, want %d) — rebuilding",
                    ticker, version, _PP_PAYLOAD_VERSION,
                )
                return None
            json_data = {k: v for k, v in json_data.items() if k != _VERSION_KEY}
            return ProfitPowerResponse(**json_data)

        except Exception as e:
            logger.warning(f"Supabase cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache_safe(
        self,
        ticker: str,
        result: ProfitPowerResponse,
        next_earnings: EarningsStamp,
    ) -> None:
        """Upsert to Supabase cache — safe wrapper that logs and swallows errors.
        This is a synchronous method — call via run_in_executor().
        """
        if not stamp_is_persistable(next_earnings):
            # Belt-and-braces: the getter already refuses a CALENDAR_UNKNOWN build.
            logger.warning(
                "Profit power upsert REFUSED for %s — next_earnings_date %r is not a date "
                "(an unread earnings calendar is never persisted)", ticker, next_earnings,
            )
            return
        if result.degraded:
            # Belt-and-braces: the caller already gates on the build's degraded list.
            logger.warning(
                "Profit power upsert REFUSED for %s — the build is degraded (%s)",
                ticker, ", ".join(result.degraded),
            )
            return
        try:
            self.supabase.table("profit_power_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": {
                        **result.model_dump(), _VERSION_KEY: _PP_PAYLOAD_VERSION,
                    },
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": next_earnings,
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(f"Supabase upsert failed for {ticker}: {e}")

    # ── Builder ───────────────────────────────────────────────────

    async def _build_profit_power(
        self, ticker: str
    ) -> Tuple[ProfitPowerResponse, EarningsStamp, list]:
        """Fetch income + cash flow, compute margins, look up sector benchmarks.
        Returns (response, next_earnings_date, degraded) — ``degraded`` is also carried on
        ``response.degraded`` so every consumer of the served value can see it.
        ``next_earnings_date`` is CALENDAR_UNKNOWN when the calendar fetch failed: the
        response is complete, but the getter never persists it.
        """

        # Phase 1: parallel fetch — profile + income + cash flow + earnings calendar (6 FMP calls)
        (
            profile,
            annual_income,
            quarterly_income,
            annual_cashflow,
            quarterly_cashflow,
            ec_raw,
        ) = await asyncio.gather(
            self.fmp.get_company_profile(ticker),
            self.fmp.get_income_statement(ticker, period="annual", limit=16),
            self.fmp.get_income_statement(ticker, period="quarter", limit=80),
            self.fmp.get_cash_flow_statement(ticker, period="annual", limit=16),
            self.fmp.get_cash_flow_statement(ticker, period="quarter", limit=80),
            # raise_errors=True: the default swallows a 429 / 5xx / non-list body into
            # [] ("no announcements"), and the row was persisted with no report-day bound.
            self.fmp.get_earning_calendar_full(ticker, raise_errors=True),
            return_exceptions=True,
        )

        # Legs that failed and were replaced by an empty default. A build missing any of
        # these still RENDERS — which is why it used to be persisted — but writing it to the
        # 24-hour Supabase tier pins the hole for a full day and freezes it into every paid
        # report generated in that window. Same reasoning as holders_service.
        degraded: list[str] = []

        # Handle failures gracefully
        if isinstance(profile, Exception):
            logger.warning(f"Profile fetch failed for {ticker}: {profile}")
            profile = {}
            degraded.append("profile")
        if isinstance(annual_income, Exception):
            logger.error(f"Annual income fetch failed for {ticker}: {annual_income}")
            annual_income = []
            degraded.append("annual_income")
        if isinstance(quarterly_income, Exception):
            logger.error(f"Quarterly income fetch failed for {ticker}: {quarterly_income}")
            quarterly_income = []
            degraded.append("quarterly_income")
        if isinstance(annual_cashflow, Exception):
            logger.error(f"Annual cash flow fetch failed for {ticker}: {annual_cashflow}")
            annual_cashflow = []
            degraded.append("annual_cashflow")
        if isinstance(quarterly_cashflow, Exception):
            logger.error(f"Quarterly cash flow fetch failed for {ticker}: {quarterly_cashflow}")
            quarterly_cashflow = []
            degraded.append("quarterly_cashflow")
        # The calendar is not a leg (it feeds no served value): its only use is the cache
        # row's next-earnings stamp. A failed fetch is CALENDAR_UNKNOWN, never `degraded`;
        # the getter serves that build from memory and never persists it.
        next_earnings = next_earnings_stamp(ec_raw, ticker=ticker, service="profit_power")
        ec_raw = []

        # Phase 2: get sector from profile
        raw_sector = profile.get("sector", "") if isinstance(profile, dict) else ""
        sector = _normalize_sector(raw_sector)
        # Industry-relative benchmarks: prefer the company's INDUSTRY peer group,
        # fall back to its sector per (metric, period).
        industry = profile.get("industry", "") if isinstance(profile, dict) else ""

        # Phase 3: compute company margins for each period
        annual_points = _build_margin_points(
            annual_income, annual_cashflow, is_quarterly=False, ticker=ticker,
        )
        quarterly_points = _build_margin_points(
            quarterly_income, quarterly_cashflow, is_quarterly=True, ticker=ticker,
        )

        # Phase 4: look up pre-computed sector benchmarks for ALL four margins.
        # net is the original (the live detail chart's dashed line); gross/operating/
        # fcf were added so the report's per-metric Profitability drill-down can draw
        # a sector line for each margin. fcf_margin stays sparse until its historical
        # backfill runs — the chart degrades to a gapped line, not a crash.
        _MARGIN_BENCHMARK_METRICS = [
            "net_margin", "gross_margin", "operating_margin", "fcf_margin",
        ]
        # Only the margins this company may be compared on at all (`financials_metric_gate`):
        # none for a listed non-lender member of a mixed industry (its industry median is a
        # lenders' yardstick). The verdict reads the trailing four of the quarters fetched
        # above (one input on every surface; unreadable → the curated list stands).
        network = resolve_payment_network(
            ticker, industry, trailing_interest_row(quarterly_income), source="profit_power",
        )
        bench_metrics = comparable_peer_metrics(
            _MARGIN_BENCHMARK_METRICS, industry, network=network,
        )
        benchmarks_annual: Dict[str, Dict[str, float]] = {}
        benchmarks_quarterly: Dict[str, Dict[str, float]] = {}
        # Peer level of the value each benchmark cell SHOWS, per metric and period.
        levels_annual: Dict[str, Dict[str, Optional[str]]] = {}
        levels_quarterly: Dict[str, Dict[str, Optional[str]]] = {}
        if sector and not bench_metrics:
            # Nothing comparable: no read, no peer line, and NOT degraded (a company state,
            # not an outage).
            logger.info(
                "profit_power[%s]: no margin is compared with a peer median (industry=%r) — "
                "no benchmark read, no peer line", ticker, industry,
            )
        elif sector:
            lookup = get_sector_benchmark_lookup()
            # The lookup is SYNCHRONOUS (sync supabase-py + a time.sleep retry): run it on
            # a worker thread so a cold key cannot stall the event loop.
            #
            # RICH cells, one peer group per line (`get_benchmark_series`), and the lookup
            # hides a period that is not fully reported (a just-closed fiscal year draws NO
            # peer value, never an earlier year's median).
            rich = await asyncio.to_thread(
                lookup.get_benchmark_series,
                industry, sector, bench_metrics, "annual",
            )
            # Quarterly peers are the CALENDAR-quarter rows (migration 184), never the
            # legacy fiscal-keyed 'quarterly' rows (peer quarters 3-10 months apart for
            # every off-calendar company).
            q_rich: Optional[Dict[str, Any]] = await asyncio.to_thread(
                lookup.get_benchmark_series,
                industry, sector, bench_metrics, CALENDAR_QUARTER_PERIOD_TYPE,
            )
            # A FAILED lookup (a Supabase blip that outlasted the lookup's retry) answers
            # the same empty shape as "this peer group has no rows", flagged only by its
            # type — which `flatten_benchmark_values` drops. Read the flag first:
            # a peer-less build caused by an outage is DEGRADED (60 s in memory, never
            # persisted, refused by the report), not a 24-hour fact about the company.
            failed_types = [
                period_type
                for period_type, raw in (("annual", rich), ("quarterly", q_rich))
                if raw is not None and lookup_failed(raw)
            ]
            if failed_types:
                logger.warning(
                    "profit_power[%s]: benchmark lookup FAILED (%s, industry=%r sector=%r) — "
                    "peer lines missing; build marked degraded, not persisted",
                    ticker, "+".join(failed_types), industry, sector,
                )
                degraded.append("benchmarks")
            benchmarks_annual = flatten_benchmark_values(rich)
            levels_annual = benchmark_levels(rich)
            if q_rich is not None:
                benchmarks_quarterly = flatten_benchmark_values(q_rich)
                levels_quarterly = benchmark_levels(q_rich)

        # Legend levels, one per DRAWN LINE (2026-10-08, PP-LEVEL-1). `get_benchmark_series`
        # picks industry-or-sector separately for every metric, so the four margin lines
        # of one tab can come from different groups (fcf_margin's cash-flow ∩ income join
        # is the thinnest). A vote pooled over all four named the FCF line "Industry" over
        # a sector median, and could name the net-margin line — the only one the live card
        # draws and the one Cay AI quotes — by the other margins' group.
        #   "<period>.<metric>"  the level of THAT metric's drawn points (report drill-down)
        #   "<period>"           the NET-margin line's level (live card legend + tooltip)
        # A line with no drawn peer point gets no key, so no client names a missing line.
        peer_group_levels: Dict[str, str] = {}
        for series, pts, bench, lvls in (
            ("annual", annual_points, benchmarks_annual, levels_annual),
            ("quarterly", quarterly_points, benchmarks_quarterly, levels_quarterly),
        ):
            for metric in _MARGIN_BENCHMARK_METRICS:
                level = series_peer_level(
                    pts, _drawable_cells(bench.get(metric, {})), lvls.get(metric, {}),
                )
                if level is not None:
                    peer_group_levels[f"{series}.{metric}"] = level
            headline = peer_group_levels.get(f"{series}.{_HEADLINE_PEER_METRIC}")
            if headline is not None:
                peer_group_levels[series] = headline
        # Single-level field kept for shipped clients, the report and Cay AI's peer sentence:
        # the annual net-margin line's level, else the quarterly one's.
        peer_group_level = peer_group_levels.get("annual") or peer_group_levels.get("quarterly")

        # Phase 5: attach sector averages and build response.
        # sector_benchmarks stores every margin as a raw DECIMAL (0.12 = 12%), so
        # ×100 for the percentage scale the chart uses — uniform across all four.
        def _to_schemas(
            points: List[Dict],
            benchmarks: Dict[str, Dict[str, float]],
        ) -> List[ProfitPowerDataPointSchema]:
            net_b = benchmarks.get("net_margin", {})
            gross_b = benchmarks.get("gross_margin", {})
            op_b = benchmarks.get("operating_margin", {})
            fcf_b = benchmarks.get("fcf_margin", {})

            def _pct(table: Dict[str, float], key: str) -> Optional[float]:
                raw = table.get(key)
                if raw is None:
                    return None
                # A NaN / non-numeric cell would 500 the whole response at JSON encoding
                # (allow_nan=False): drop that one peer point, loudly.
                if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
                    logger.warning(
                        "profit_power[%s]: unusable benchmark cell %r at %s — peer point dropped",
                        ticker, raw, key,
                    )
                    return None
                return round(raw * 100, 2)

            schemas = []
            for p in points:
                # Join on `_match_period` (`annual_benchmark_key` for annual points; the
                # legacy quarterly key for quarters, or "" — no peer value — for a quarter
                # that key would join to the wrong calendar quarter: `_quarterly_join_key`).
                k = p.get("_match_period", p["period"])
                schemas.append(ProfitPowerDataPointSchema(
                    period=p["period"],
                    gross_margin=p["gross_margin"],
                    operating_margin=p["operating_margin"],
                    fcf_margin=p["fcf_margin"],
                    net_margin=p["net_margin"],
                    sector_average_net_margin=_pct(net_b, k),
                    sector_average_gross_margin=_pct(gross_b, k),
                    sector_average_operating_margin=_pct(op_b, k),
                    sector_average_fcf_margin=_pct(fcf_b, k),
                ))
            return schemas

        response = ProfitPowerResponse(
            symbol=ticker,
            annual=_to_schemas(annual_points, benchmarks_annual),
            quarterly=_to_schemas(quarterly_points, benchmarks_quarterly),
            peer_group_level=peer_group_level,
            peer_group_levels=peer_group_levels,
            degraded=list(degraded),
        )

        # `next_earnings` was stamped from the calendar slot right after the gather.
        return response, next_earnings, degraded


# ── Singleton ─────────────────────────────────────────────────────

_profit_power_service: Optional[ProfitPowerService] = None


def get_profit_power_service() -> ProfitPowerService:
    global _profit_power_service
    if _profit_power_service is None:
        _profit_power_service = ProfitPowerService()
    return _profit_power_service
