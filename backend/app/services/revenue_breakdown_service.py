"""
Revenue Breakdown service — fetches product segmentation + income statement
from FMP, groups small segments into "Other", and caches the result in
Supabase for 24 hours (or until next earnings date).

Matches the iOS RevenueBreakdownData struct.
"""

import asyncio
import logging
import math
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.utils.inflight import fail_shared_future
from app.integrations.fmp import get_fmp_client
from app.schemas.revenue_breakdown import (
    RevenueBreakdownResponse,
    RevenueSourceSchema,
)

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

def _as_list(payload: Any) -> List[Dict[str, Any]]:
    """Normalize an FMP payload to a list of record dicts.

    ``FMPClient._make_request`` is typed ``-> Any`` ("list or dict"). A bare dict
    (an FMP error shape returned with a 200) would make ``income_raw[0]`` raise
    ``KeyError`` and iteration yield string keys → a bare 502 for the section.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if payload:
        logger.warning(
            "revenue_breakdown: expected a list from FMP, got %s — degrading to empty",
            type(payload).__name__,
        )
    return []


def _record_year(record: Dict[str, Any]) -> str:
    """Fiscal/calendar year of an FMP record as a string, or "" when unknown.

    Used to PAIR a product-segmentation record with the income statement for the
    same fiscal year. Prefers ``fiscalYear`` (present on the stable segmentation
    payload), then ``calendarYear``, then the period-end date. Null-safe.
    """
    for key in ("fiscalYear", "calendarYear"):
        val = record.get(key)
        if val:
            return str(val)
    date_str = record.get("date") or ""
    return date_str[:4] if len(date_str) >= 4 else ""


def _safe_float(record: Dict[str, Any], key: str, default: float = 0.0) -> float:
    """Safely extract a float value, returning *default* on None/error."""
    val = record.get(key)
    if val is None:
        return default
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (ValueError, TypeError):
        return default


def _safe_float_opt(record: Dict[str, Any], key: str) -> Optional[float]:
    """Like `_safe_float`, but returns None instead of a substitute value.

    Used for the composition fields (`netIncome`, `revenue`). Their whole purpose is to
    replace a wrong DERIVED number with a reported one, so "upstream did not report it"
    has to stay distinguishable from "upstream reported zero" — a 0.0 default here would
    render a company as having earned exactly nothing, which is precisely the class of
    fabricated-number bug this change exists to remove.
    """
    val = record.get(key)
    if val is None:
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


# Keys that are metadata, not segment names, in FMP segmentation response
_SEGMENT_META_KEYS = {"date", "symbol", "reportedCurrency", "cik", "fillingDate",
                      "acceptedDate", "calendarYear", "period", "link", "finalLink",
                      "fiscalYear", "data"}

# Minimum percentage of total revenue for a segment to keep its own name
_OTHER_THRESHOLD_PCT = 5.0

# ── Reconciling the segments to REPORTED revenue (2026-09-17) ────────────────────
# FMP's product segmentation is a mix HINT, not the revenue. Surveyed live across 22 large
# caps: INTC's four segments sum to 134% of revenue (Intel Foundry's $17.7B of sales to
# Intel's own product groups, with an explicit "Intersegment Eliminations" row we used to
# DROP as "negative"); AMD 111% ("Gaming" listed inside "Client and Gaming"); CAT 172%
# (a "Reportable Subsegments" total line); while KO covers 78%, BA 46% and Ford 7% (only
# "Ford Credit"). The card stacked whatever it was given, so INTC's revenue bar towered
# over its costs in a loss year and Ford's shrank to a sliver. The income statement's
# `revenue` is the authority; the stack is reconciled to it with an EXPLICIT item.
_RECONCILE_TOLERANCE = 0.03      # ±3%: ordinary FMP rounding (LMT −0.9%, XOM −2.1%) is left alone
_MIN_SEGMENT_COVERAGE = 0.50     # below this the "breakdown" is one segment of many — not a breakdown
_UNALLOCATED_NAME = "Unallocated"
_ELIMINATION_RE = re.compile(r"eliminat|intersegment|inter-segment|reconcil|corporate (?:items|adjust)", re.I)
# "Total …" and the CAT "Reportable Subsegments" line by prefix; "Consolidated" only when
# it names a total (a segment can be "Consolidated Edison Company of New York"); a bare
# "Revenue(s)" / "Net revenue" only as an EXACT name — "Revenue from Services" is a segment.
_TOTAL_LIKE_RE = re.compile(
    r"^total\b|^reportable subsegments?$|^segment totals?$|^consolidated (?:revenues?|total|net)\b|^(?:net )?revenues?$",
    re.I,
)
# The cache row carries the reconciled stack. Rows written before reconciliation existed
# hold the gross / thin stacks and must rebuild rather than serve for up to 24 h.
_RB_PAYLOAD_VERSION = 2
_VERSION_KEY = "payload_version"


def _explicit_eliminations(record: Dict[str, Any]) -> float:
    """Magnitude of the NEGATIVE rows FMP labels as eliminations / reconciling items.

    `_extract_segments` drops every negative row (a negative segment is not revenue); this
    reads the same record for the one negative that carries meaning — INTC FY2025
    "Intersegment Eliminations": −17,683,000,000 — so the builder can cross-check the gap
    it derives. Other negatives (CAT's "Power & Energy": −5.06B, a mangled feed row) are
    not eliminations and stay ignored. Returns 0.0 when there is none.
    """
    segment_dict = record.get("data")
    if not isinstance(segment_dict, dict):
        segment_dict = {k: v for k, v in record.items() if k not in _SEGMENT_META_KEYS}
    total = 0.0
    for key, val in segment_dict.items():
        try:
            amount = float(val)
        except (ValueError, TypeError):
            continue
        # Whatever the sign: a feed that books the row as +17.68B means the same thing.
        if math.isfinite(amount) and amount != 0 and _ELIMINATION_RE.search(str(key)):
            total += abs(amount)
    return total


def _reconcile_segments(
    sources: List[RevenueSourceSchema],
    reported_revenue: Optional[float],
    explicit_eliminations: float = 0.0,
    ticker: str = "",
) -> Tuple[List[RevenueSourceSchema], Optional[float], str]:
    """Fit the segment stack to reported revenue. Returns (sources, eliminations, outcome).

    outcome ∈ {"exact", "gross", "subline", "unallocated", "thin", "unreconciled"}:
      * within ±3% → untouched ("exact");
      * sum ABOVE revenue → first try dropping a segment whose name is a sub-line of another
        ("Gaming" inside "Client and Gaming" — AMD lands exactly on revenue once it goes);
        otherwise the stack is gross of intersegment sales and the excess is returned as
        `eliminations` (a positive magnitude) for iOS to draw as the first waterfall step
        ("gross"). The segments themselves stay AS REPORTED — INTC's CCG really did sell
        $32.2B — and the eliminations line is what makes the legend add to 100%;
      * sum BELOW revenue but covering ≥ 50% → an explicit "Unallocated" segment closes
        the gap ("unallocated"). Below 50% the feed listed one segment of many (Ford:
        "Ford Credit" 7%), which is not a breakdown: return [] so the caller falls back to
        the single Total Revenue bar ("thin");
      * no usable reported revenue → nothing to reconcile against ("unreconciled").
    Never changes a reported segment's value.
    """
    if not sources:
        return sources, None, "unreconciled"
    if reported_revenue is None or not math.isfinite(reported_revenue) or reported_revenue <= 0:
        return sources, None, "unreconciled"

    pos_sum = sum(s.value for s in sources)
    if pos_sum <= 0:
        return sources, None, "unreconciled"
    ratio = pos_sum / reported_revenue
    if abs(ratio - 1.0) <= _RECONCILE_TOLERANCE:
        return sources, None, "exact"

    if ratio > 1.0:
        # Sub-line double count: keep the drop only if it actually reconciles.
        names = [s.name.strip().lower() for s in sources]
        for i, s in enumerate(sources):
            if any(j != i and names[i] and names[i] in names[j] for j in range(len(sources))):
                trial = [t for j, t in enumerate(sources) if j != i]
                trial_sum = sum(t.value for t in trial)
                if trial_sum > 0 and abs(trial_sum / reported_revenue - 1.0) <= _RECONCILE_TOLERANCE:
                    logger.info("[revenue-seg-subline] %s: dropped %r (%.3g) — a sub-line of a "
                                "listed segment; the rest reconcile to revenue",
                                ticker, s.name, s.value)
                    return trial, None, "subline"
        eliminations = pos_sum - reported_revenue
        if explicit_eliminations > 0 and abs(explicit_eliminations - eliminations) > 0.05 * eliminations:
            logger.warning("[revenue-seg-eliminations-mismatch] %s: FMP eliminations row %.4g vs "
                           "derived gap %.4g — using the derived gap so the card reconciles",
                           ticker, explicit_eliminations, eliminations)
        else:
            logger.info("[revenue-seg-gross] %s: segments %.4g vs revenue %.4g — %.4g of "
                        "intersegment sales eliminated", ticker, pos_sum, reported_revenue, eliminations)
        return sources, eliminations, "gross"

    # ratio < 1
    if ratio < _MIN_SEGMENT_COVERAGE:
        logger.warning("[revenue-seg-thin] %s: segments cover %.0f%% of revenue — not a "
                       "breakdown; falling back to Total Revenue", ticker, ratio * 100)
        return [], None, "thin"
    gap = reported_revenue - pos_sum
    logger.info("[revenue-seg-unallocated] %s: segments cover %.0f%% of revenue — %.4g unallocated",
                ticker, ratio * 100, gap)
    return sources + [RevenueSourceSchema(name=_UNALLOCATED_NAME, value=gap)], None, "unallocated"


def _extract_segments(record: Dict[str, Any]) -> List[RevenueSourceSchema]:
    """
    Extract revenue segments from an FMP product-segmentation record.

    FMP stable API returns:
      {"symbol": "AAPL", "fiscalYear": 2025, "date": "...", "data": {"iPhone": 209586000000, ...}}
    Segments live inside the nested "data" dict.
    Falls back to flat-key extraction if "data" is absent.

    IMPORTANT: The nested "data" dict may also contain metadata keys like
    "fiscalYear" (value: 2025) that must be filtered out — otherwise a year
    like 2025 gets treated as a $2K revenue segment and poisons percentages.
    """
    # Prefer nested "data" dict (stable API format)
    segment_dict = record.get("data")
    if isinstance(segment_dict, dict):
        # Filter metadata keys even inside the nested "data" dict
        segment_dict = {k: v for k, v in segment_dict.items() if k not in _SEGMENT_META_KEYS}
    else:
        # Fallback: treat top-level keys minus metadata as segments
        segment_dict = {k: v for k, v in record.items() if k not in _SEGMENT_META_KEYS}

    segments: List[Tuple[str, float]] = []
    for key, val in segment_dict.items():
        try:
            amount = float(val)
        except (ValueError, TypeError):
            continue
        if not math.isfinite(amount):
            continue  # NaN/Inf segment -> REQUIRED RevenueSourceSchema.value -> 500
        if amount <= 0:
            continue  # skip zero/negative segments (eliminations are read separately)
        # An eliminations / reconciling row is never revenue, even when the feed signs it
        # POSITIVE — stacked, it would double the very amount it is meant to remove.
        if _ELIMINATION_RE.search(str(key)):
            logger.info("[revenue-seg-elimination-row] dropped %r (%.3g) from the stack", key, amount)
            continue
        # Skip values that look like years (e.g. 2024, 2025) — not revenue
        if 1900 <= amount <= 2100:
            continue
        # A TOTAL line beside its components (CAT: "Reportable Subsegments" 73.95B next to
        # Construction / Resource / ...) is not a segment — stacking it doubles the bar.
        if _TOTAL_LIKE_RE.search(str(key).strip()):
            logger.info("[revenue-seg-total-row] dropped %r (%.3g): a total, not a segment", key, amount)
            continue
        segments.append((key, amount))

    if not segments:
        return []

    # Sort descending by value
    segments.sort(key=lambda s: s[1], reverse=True)

    total = sum(v for _, v in segments)
    if total <= 0:
        return []

    # Group small segments into "Other"
    result: List[RevenueSourceSchema] = []
    other_total = 0.0

    for name, value in segments:
        pct = (value / total) * 100
        if pct < _OTHER_THRESHOLD_PCT:
            other_total += value
        else:
            result.append(RevenueSourceSchema(name=name, value=value))

    if other_total > 0:
        result.append(RevenueSourceSchema(name="Other", value=other_total))

    return result


def _find_next_earnings_date_simple(
    ec_records: List[Dict[str, Any]],
) -> Optional[str]:
    """Return the first future earnings date as yyyy-MM-dd, or None."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for ec in sorted(ec_records, key=lambda r: r.get("date") or ""):
        ec_date = (ec.get("date") or "")[:10]
        if not ec_date or ec_date <= today_str:
            continue
        # Skip if actual EPS is already reported (past quarter)
        if ec.get("eps") is not None:
            continue
        return ec_date
    return None


# ── Service ───────────────────────────────────────────────────────

class RevenueBreakdownService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_revenue_breakdown(self, ticker: str) -> RevenueBreakdownResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"rev_breakdown:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Revenue breakdown in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache (run in thread to avoid blocking event loop) ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Revenue breakdown Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        # If another request is already fetching this ticker, wait for it
        if cache_key in _inflight:
            logger.info(f"Revenue breakdown in-flight JOIN for {ticker}")
            return await asyncio.shield(_inflight[cache_key])

        # Create a future so other concurrent requests can wait on us
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            # ── Cache miss: build from FMP ──
            logger.info(f"Revenue breakdown cache MISS for {ticker} — fetching from FMP")
            result, next_earnings = await self._build_revenue_breakdown(ticker)

            # Persist to Supabase in background thread (truly fire-and-forget)
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

    def _check_supabase_cache(self, ticker: str) -> Optional[RevenueBreakdownResponse]:
        """Return cached response if fresh (< 24h and before next earnings).
        This is a synchronous method — call via asyncio.to_thread().
        """
        try:
            row = (
                self.supabase.table("revenue_breakdown_cache")
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

            # Deserialize — refusing a row written before the stack was reconciled to
            # reported revenue (it holds INTC's gross 70.5B stack / Ford's 7% sliver).
            json_data = entry["response_json"]
            if not isinstance(json_data, dict) or json_data.get(_VERSION_KEY) != _RB_PAYLOAD_VERSION:
                logger.info("Supabase cache STALE (%s=%s, want %s) for %s", _VERSION_KEY,
                            (json_data or {}).get(_VERSION_KEY) if isinstance(json_data, dict) else None,
                            _RB_PAYLOAD_VERSION, ticker)
                return None
            return RevenueBreakdownResponse(**json_data)

        except Exception as e:
            logger.warning(f"Supabase cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache_safe(
        self,
        ticker: str,
        result: RevenueBreakdownResponse,
        next_earnings: Optional[str],
    ) -> None:
        """Upsert to Supabase cache — safe wrapper that logs and swallows errors.
        This is a synchronous method — call via run_in_executor().
        """
        try:
            self.supabase.table("revenue_breakdown_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": {**result.model_dump(), _VERSION_KEY: _RB_PAYLOAD_VERSION},
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": next_earnings,
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(f"Supabase upsert failed for {ticker}: {e}")

    # ── Builder ───────────────────────────────────────────────────

    async def _build_revenue_breakdown(
        self, ticker: str
    ) -> Tuple[RevenueBreakdownResponse, Optional[str]]:
        """
        Fetch FMP data and assemble the response.
        Returns (response, next_earnings_date).
        """
        # Parallel FMP calls
        seg_raw, income_raw, ec_raw = await asyncio.gather(
            self.fmp.get_revenue_product_segmentation(ticker, period="annual"),
            # limit=5 (was 1): the segmentation feed often lags the income
            # statement by a fiscal year, so we need several years available to
            # pair the segments with the SAME year's costs (see below).
            self.fmp.get_income_statement(ticker, period="annual", limit=5),
            self.fmp.get_earning_calendar_full(ticker),
            return_exceptions=True,
        )

        # Handle exceptions gracefully
        if isinstance(seg_raw, Exception):
            logger.warning(f"Segment fetch failed for {ticker}: {seg_raw}")
            seg_raw = []
        if isinstance(income_raw, Exception):
            logger.warning(f"Income statement fetch failed for {ticker}: {income_raw}")
            income_raw = []
        if isinstance(ec_raw, Exception):
            logger.warning(f"Earnings calendar fetch failed for {ticker}: {ec_raw}")
            ec_raw = []

        # A non-list FMP payload (error dict returned with a 200) would make
        # income_raw[0] raise KeyError and the segment sort iterate string keys.
        seg_raw = _as_list(seg_raw)
        income_raw = _as_list(income_raw)
        ec_raw = _as_list(ec_raw)

        # ── Pair the segmentation year with the SAME fiscal year's income ──
        # The segments (revenue) and the cost/tax figures must come from ONE
        # fiscal year: FMP's product segmentation commonly lags the income
        # statement by a year, so blindly pairing "latest segmentation" with
        # "latest income" reported one year's revenue mix against another
        # year's costs — and labelled the card with the income year. Walk the
        # segmentation records newest→oldest and take the first year that also
        # has an income statement; degrade to the income-only Total Revenue
        # fallback rather than mixing years.
        income_by_year: Dict[str, Dict[str, Any]] = {}
        for rec in income_raw:
            yr = _record_year(rec)
            if yr and yr not in income_by_year:
                income_by_year[yr] = rec

        sorted_seg = sorted(seg_raw, key=lambda r: r.get("date") or "", reverse=True)

        revenue_sources: List[RevenueSourceSchema] = []
        income: Dict[str, Any] = {}
        intersegment_eliminations: Optional[float] = None
        thin_segmentation = False
        for seg_rec in sorted_seg:
            seg_year = _record_year(seg_rec)
            matched_income = income_by_year.get(seg_year) if seg_year else None
            if matched_income is None:
                continue
            sources = _extract_segments(seg_rec)
            if not sources:
                continue
            # Reconcile to THIS year's reported revenue (see _reconcile_segments). A thin
            # feed returns [] here and the Total Revenue fallback below takes over — the
            # income record is still this year's, so the costs stay paired correctly.
            sources, intersegment_eliminations, outcome = _reconcile_segments(
                sources, _safe_float_opt(matched_income, "revenue"),
                _explicit_eliminations(seg_rec), ticker=ticker,
            )
            if outcome == "thin":
                # Keep walking: an older year with real coverage beats a single bar. If
                # every year is thin, the newest paired income still labels the fallback.
                thin_segmentation = True
                if not income:
                    income = matched_income
                continue
            revenue_sources = sources
            income = matched_income
            break

        if not revenue_sources and sorted_seg and not thin_segmentation:
            logger.warning(
                "revenue_breakdown %s: segmentation years %s have no matching income "
                "statement (income years %s) — falling back to income-only Total Revenue "
                "rather than pairing mismatched fiscal years",
                ticker,
                [_record_year(r) for r in sorted_seg[:3]],
                sorted(income_by_year.keys(), reverse=True),
            )

        # Costs/tax/label come from the SAME record the segments were paired
        # with; only the no-segment path falls back to the latest income year.
        if not income:
            income = income_raw[0] if income_raw else {}

        cost_of_sales = _safe_float(income, "costOfRevenue")
        # ⚠️ FMP's `operatingExpenses` is legitimately NEGATIVE for some filers, and it is
        # NOT an error to pass through. It is SG&A + R&D + otherExpenses, so a company that
        # books most SG&A inside cost of sales and carries a large other-income credit nets
        # below zero — LMT FY2025 is exactly 50M + 2,000M + (-2,162M) = -112M. A negative
        # cost is a CREDIT; iOS relabels it rather than pretending it is an expense. Do not
        # clamp it here: clamping would silently move 112M into net profit.
        operating_expense = _safe_float(income, "operatingExpenses")
        tax = _safe_float(income, "incomeTaxExpense")
        total_revenue_income = _safe_float(income, "revenue")
        fiscal_year = _record_year(income)

        # ── The composition (see RevenueBreakdownResponse's docstring) ──
        # Optional on purpose: absent must stay distinguishable from zero.
        net_income = _safe_float_opt(income, "netIncome")
        reported_revenue = _safe_float_opt(income, "revenue")

        # The plug that closes the waterfall: interest, non-operating items, minority
        # interest, discontinued ops. Only computable when BOTH ends are reported —
        # deriving it from a substituted zero would recreate the bug being fixed.
        other_expense: Optional[float] = None
        if net_income is not None and reported_revenue is not None:
            other_expense = (
                reported_revenue - cost_of_sales - operating_expense - tax - net_income
            )
        else:
            logger.info(
                "revenue_breakdown %s: netIncome=%s revenue=%s — composition omitted, "
                "iOS will fall back to the derived residual",
                ticker, net_income, reported_revenue,
            )

        # Fallback: if no segments, use total revenue from income statement
        if not revenue_sources and total_revenue_income > 0:
            revenue_sources = [
                RevenueSourceSchema(name="Total Revenue", value=total_revenue_income)
            ]
            logger.info(f"No segment data for {ticker}, using Total Revenue fallback")

        # If still nothing, return zeros (company may have no data at all)
        if not revenue_sources:
            logger.warning(f"No revenue data at all for {ticker}")
            revenue_sources = [RevenueSourceSchema(name="Total Revenue", value=0.0)]

        # ── Next earnings date ──
        next_earnings = _find_next_earnings_date_simple(ec_raw if isinstance(ec_raw, list) else [])

        response = RevenueBreakdownResponse(
            symbol=ticker,
            fiscal_year=fiscal_year,
            revenue_sources=revenue_sources,
            cost_of_sales=cost_of_sales,
            operating_expense=operating_expense,
            tax=tax,
            net_income=net_income,
            reported_revenue=reported_revenue,
            other_expense=other_expense,
            intersegment_eliminations=intersegment_eliminations,
        )

        return response, next_earnings


# ── Singleton ─────────────────────────────────────────────────────
_revenue_breakdown_service: Optional[RevenueBreakdownService] = None


def get_revenue_breakdown_service() -> RevenueBreakdownService:
    global _revenue_breakdown_service
    if _revenue_breakdown_service is None:
        _revenue_breakdown_service = RevenueBreakdownService()
    return _revenue_breakdown_service
