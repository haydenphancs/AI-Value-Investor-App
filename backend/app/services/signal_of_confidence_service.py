"""
Signal of Confidence service — fetches cash flow, income, key metrics, and
dividend history from FMP, computes per-quarter shareholder yield data
(dividends, buybacks, shares outstanding), and returns a response matching
the iOS SignalOfConfidenceSectionData struct.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``signal_of_confidence_cache`` table (24-hour TTL + earnings-aware)

Matches the iOS SignalOfConfidenceSectionData struct.
"""

import asyncio
import math
import logging
import re
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services._earnings_common import (
    CALENDAR_UNKNOWN,
    EarningsStamp,
    next_earnings_stamp,
    stamp_is_persistable,
)
from app.database import get_supabase
from app.utils.inflight import fail_shared_future
from app.integrations.fmp import get_fmp_client
from app.utils.period_labels import annual_fiscal_year, quarterly_period_label
from app.schemas.signal_of_confidence import (
    AnnualDividendSchema,
    DividendInfoSchema,
    SignalOfConfidenceDataPointSchema,
    SignalOfConfidenceResponse,
    SignalOfConfidenceSummarySchema,
)
from app.services.asset_class import profile_is_fund
from app.services.corporate_actions_service import (
    corporate_actions_source,
    window_for_range,
)
from app.services.price_service import price_source

logger = logging.getLogger(__name__)

#: Bumped whenever `SignalOfConfidenceResponse` gains a field OR the way a stored value
#: is computed changes. `_check_supabase_cache` refuses any row that does not carry the
#: current value, so a formula fix reaches users on the next read instead of 24h later.
#:
#: 1 → pre-versioning rows (no key at all; they never match, which is the intent).
#: 2 → adds `dividend_info.annual_dividends` / `dividend_per_share*`, and corrects the
#:     `status` denominator to compare T12M against the 5-year average on the SAME
#:     point-in-time basis (JNJ was reported "Low").
#: 3 → the comparison baseline EXCLUDES its own numerator (it was self-referential, so a
#:     40% dividend cut still read "Fair"), `_ABOUT_AVERAGE` sends a payer yielding its own
#:     history to Fair rather than green, and `dividend_growth_pct` drops a partial first
#:     paying year (GOOGL read +38.3% for a 0.20 -> 0.21 quarterly raise).
#: 4 → dividend bars require the cash-flow OUTFLOW sign, use the preferred-inclusive
#:     `netDividendsPaid` only for a known payer, and are ZEROED when the per-share
#:     record (`ratios.dividendPerShare` + profile `lastDividend`) says the company pays
#:     no common dividend. PLUG — never a common payer, diluting 946M → 1.39B shares —
#:     charted a 1.75% annualised yield in Q2'26 from a mis-tagged `commonDividendsPaid`
#:     of -$16.5M (TestFlight, build 1.0 (8)). The card was already gated; the bars were
#:     not, so one screen said "no dividend" and "1.75%" at once.
#: 5 → the payer verdict is CURRENT, not historical: the most recent fiscal year's
#:     `dividendPerShare` (or the profile's TTM `lastDividend`) decides, so a company that
#:     STOPPED paying is treated like one that never started. INTC paid through FY2024
#:     and suspended in Q3 2024; "any year in the window" still called it a payer and let a
#:     −14.3B Q2'26 cash-flow line FMP tags `commonDividendsPaid` chart as an 8.79%
#:     dividend with status "Very High" beside "Dividend / Share (FY2025) $0.00". The bars
#:     are now zeroed; the card keeps the history (−100% over 5y) because the suspension
#:     is the story.
#: 6 → the bar gate is PER FISCAL YEAR (`dividend_by_year`): a quarter in a year whose
#:     per-share record is positive keeps its real bar, so INTC's last FY2024 dividend
#:     still charts and the 5-year average is a number, not "—". Bumped past 5 because
#:     rows stamped 5 were written (locally, into the shared cache table) by the
#:     intermediate build that zeroed every quarter.
#: 7 → (2026-09-30 Financials deep check) per-quarter yields are TRAILING-12-MONTH, not the
#:     quarter x4 (KO's unchanged dividend charted 0.12% → 5.85%); the dividend verdict
#:     compares the newest TTM point with TTM points at least four quarters older; the
#:     per-fiscal-year dividend map is keyed by `fiscalYear` (HD-style FYs read the
#:     neighbouring year); duplicate income/cash-flow rows are collapsed; a quarter with
#:     no market cap or no cash-flow row degrades the build instead of charting 0%; and
#:     the payload gains `share_count_change_known`, `avg_yield_window` and `degraded`.
#: 8 → (2026-09-30 round 2) x4-fallback points (no four consecutive cash-flow quarters)
#:     leave the dividend verdict's baseline and a fallback NEWEST point refuses the
#:     relative verdict — an annual payer's x4 bars read "Very High" for a flat dividend;
#:     the no-current-cap summary no longer reads one x4 quarter as the T12M; a newest
#:     quarter with no cash-flow row is trimmed instead of shipped as $0.
#: 9 → (2026-09-30 round 3) the trimmed newest edge is BOUNDED: more than
#:     `_MAX_CF_LAG_QUARTERS` trimmed, or a kept series ending more than
#:     `_CF_STALE_MAX_DAYS` before the newest income quarter, is a stale feed and marks the
#:     build `cash_flow` (blocking), not the ignorable `cash_flow_row`; a statement that
#:     answered but matched no displayed quarter is `cash_flow_statement_missing`
#:     (`cash_flow` stays for a leg that raised or answered a non-list).
#: 10 → (2026-10-01) bumped for:
#:     - P20: the earnings calendar is fetched with raise_errors=True and a failed fetch is
#:       never persisted, so a NULL `next_earnings_date` now means "no pending
#:       announcement"; an older row's NULL may be a swallowed calendar 429 (no report-day
#:       bound for up to 24h).
#:     - P19: each data point carries `cash_flow_reported`; a quarter with no cash-flow row
#:       (an interior or leading-edge vendor gap) ships False with 0.0 placeholders and no
#:       longer sets `cash_flow_row` (which now names only the trimmed newest edge), so the
#:       build persists. A v9 row would decode the flag as True and show its $0 as reported.
#:     - P19 fix pass: a summary with fewer than four known quarters in its window (an
#:       interior gap, a late-starting cash-flow history, a company listed under a year) is
#:       annualised (sum x 4/N) instead of read as a year — a v9 row of a young company
#:       persisted 3.0% "High" for a 4.0% "Very High" repurchaser, and v10 refuses it.
_PAYLOAD_VERSION = 10

#: How many newest quarters the cash-flow statement may LAG the income statement by
#: before the trim stops being "the row has not landed yet" (round-2 R47) and becomes a
#: stale feed (round-3 P9): one filing. Two or more trimmed quarters rewound the series
#: and its T12M by years while reading only as the ignorable `cash_flow_row`.
_MAX_CF_LAG_QUARTERS = 1
#: …and the newest KEPT quarter may end at most this many days before the newest income
#: quarter (one ~91-day quarter of lag plus a 16-week fiscal quarter and slack). A gap in
#: the income history itself can stretch a single trimmed quarter past it.
_CF_STALE_MAX_DAYS = 200

#: A build that `degraded` names is never persisted; it lives this long in memory — long
#: enough to absorb a retry storm, short enough that the next refresh rebuilds it.
_DEGRADED_CACHE_TTL = 60

# ── Fund-shaped empty builds (2026-10-01) ─────────────────────────────────────────
# A fund (VB, SPY …) that reaches this card has no income or cash-flow statement: FMP
# answers both with a genuine 200 []. The build then has no data points, which the getter
# refuses as a possible outage (a 429 on the income leg looks the same once coerced) — so
# every view of a fund rebuilt it from FMP every 60 s. The ONE extra shape the cache admits
# is that empty build, and only when a POSITIVE fund flag on the FMP profile fetched in the
# SAME build says so (`asset_class.profile_is_fund`), both statement legs answered exactly
# `[]` (checked on the RAW answers, before `_as_list` turns an error dict into []), and no
# leg is degraded — never inferred from the empty answer itself. The row carries
# `security_kind: "fund"`; the reader admits an empty-build row only with that marker. The
# response is unchanged: iOS still sees `data_points == []`, `degraded == []`.
# No payload_version bump: no existing row carries the marker, and an older reader serves a
# marked row exactly as it serves the live build today.
# `SignalOfConfidenceResponse.degraded` ("non-empty means never written") stays true because
# this row's `degraded` is []; SYSTEM_DESIGN_GUIDELINES "A partial build is served, never
# stored" must still name this empty build (tests/test_fund_shape_caching_soc_hc.py).
_SECURITY_KIND_KEY = "security_kind"
_SECURITY_KIND_FUND = "fund"


def _is_empty_build(resp: Any) -> bool:
    """True for the shape EVERY build with no data points has: no points, and
    `_build_summary`'s empty branch, which always says the share count was not measured
    (``share_count_change_known=False``). The getter never persists that shape unless the
    build is fund-shaped, so the reader refuses it without the fund marker."""
    summary = getattr(resp, "summary", None)
    return (
        not getattr(resp, "data_points", None)
        and getattr(summary, "share_count_change_known", True) is False
    )


@dataclass
class _SocBuild:
    """`_build_signal_of_confidence_full`'s answer. ``fund_shape`` is True only for a
    fund's empty build (see the block above): the raw profile positively says fund, the
    raw quarterly income and cash-flow answers were both exactly ``[]``, the raw annual
    ratios answer was a list, no data point survived and nothing is degraded."""

    response: SignalOfConfidenceResponse
    next_earnings: EarningsStamp
    degraded: List[str]
    fund_shape: bool = False

#: Four cash-flow rows make a trailing twelve months only when they are CONSECUTIVE
#: quarters. Each step between period ends must be a real quarter: at least 8 weeks (a
#: shorter one is a fiscal-year-change stub) and at most 18 — not the nominal ~100 days,
#: because 16-week quarters exist (Costco's fiscal Q4 and Kroger's Q1 are 112 days).
_TTM_GAP_MIN_DAYS = 56
_TTM_GAP_MAX_DAYS = 125
#: …and the first and last period ends of the window at most ~10 months apart (nominal
#: 273 days, Costco 252-280), so three long steps cannot stretch it past a year.
_TTM_SPAN_MAX_DAYS = 300

#: An income period end with no cash-flow row on the SAME date may take one this close.
#: Quarters are >= 56 days apart, so a week can never pick a neighbouring quarter.
_CF_DATE_TOLERANCE_DAYS = 7

#: A period-end market cap more than this far from the median of its ±5-day neighbours is
#: a vendor glitch (a dropped digit, the AVGO class), not a price move: the median is used.
_MCAP_OUTLIER_FRACTION = 0.5
#: Neighbours needed before the median above means anything.
_MCAP_MEDIAN_MIN_POINTS = 3

#: An ex-dividend date derived from the price series within this many days counts as
#: "currently paying" when neither the per-share record nor the profile is available.
#: Wide enough to span an annual payer's gap; the derived dates are the LAST rung, so a
#: spurious one cannot override an authoritative zero record.
_EX_DIVIDEND_RECENCY_DAYS = 400

#: Half-open ratio band treated as "about its own average", and therefore Fair rather
#: than the green "High". See `_build_dividend_info` for why it is narrow.
_ABOUT_AVERAGE = (0.97, 1.03)

#: Quarters that make up the "trailing yield" the dividend verdict is about.
_TRAILING_POINTS = 4

#: Older quarters required before that trailing yield can be compared to a baseline at
#: all. Fewer than this and the ratio has too little independent history to mean anything
#: — `_build_dividend_info` falls back to the absolute yield ladder instead.
_MIN_BASELINE_POINTS = 4


#: Completed fiscal years of dividend history to request. Six spans a full cut-and-
#: recover cycle (Intel went 1.4598 -> 0.0000 across four) without bloating the card.
_ANNUAL_DIVIDEND_YEARS = 6

# ── In-memory cache ───────────────────────────────────────────────
# (written_at, value, ttl_seconds) — per-entry TTL so a DEGRADED build is held for
# `_DEGRADED_CACHE_TTL` only, not the full 5 minutes.
_cache: Dict[str, Tuple[float, Any, float]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value, ttl = entry
    if time.time() - ts > ttl:
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


def _cache_set(key: str, value: Any, ttl: Optional[float] = None) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value, _CACHE_TTL if ttl is None else ttl)
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
    """Safely extract a float value from a dict."""
    val = record.get(key)
    if val is None:
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


# Quarter display labels come from app.utils.period_labels.quarterly_period_label
# (shared app-wide): the fiscal-year apostrophe form "Q4 '26", monotonic for
# off-calendar-fiscal companies. The Institutions / 13F chart is the only section
# that intentionally counts calendar quarters instead.


def _as_list(payload: Any) -> List[Dict[str, Any]]:
    """Normalize an FMP payload to a list of record dicts (see the sibling
    services): ``_make_request`` is typed ``-> Any`` and a bare error dict
    iterates as string keys → AttributeError → 502."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if payload:
        logger.warning(
            "signal_of_confidence: expected a list from FMP, got %s — degrading to empty",
            type(payload).__name__,
        )
    return []


def _build_market_cap_lookup(records: List[Dict[str, Any]]) -> Dict[str, float]:
    """``{yyyy-MM-dd: marketCap}`` from FMP's historical-market-capitalization."""
    lookup: Dict[str, float] = {}
    for rec in records:
        d = (rec.get("date") or "")[:10]
        mc = _safe_float(rec, "marketCap")
        if d and mc is not None and mc > 0:
            lookup[d] = mc
    return lookup


def _market_cap_on(date_str: str, lookup: Dict[str, float]) -> Optional[float]:
    """Market cap on ``date_str``, scanning back then forward up to 5 days.

    A fiscal period-end often falls on a weekend/holiday, so an exact match is
    not guaranteed. Mirrors ``earnings_service._find_close_price``.
    """
    if not date_str:
        return None
    if date_str in lookup:
        return lookup[date_str]
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        return None
    for delta in range(1, 6):
        key = (dt - timedelta(days=delta)).strftime("%Y-%m-%d")
        if key in lookup:
            return lookup[key]
    for delta in range(1, 6):
        key = (dt + timedelta(days=delta)).strftime("%Y-%m-%d")
        if key in lookup:
            return lookup[key]
    return None


def _robust_market_cap_on(
    date_str: str, lookup: Dict[str, float], ticker: str = ""
) -> Optional[float]:
    """`_market_cap_on`, refusing a single-day vendor glitch.

    `_build_market_cap_lookup` takes every positive value at face value, so one dropped
    digit on a period-end day (the AVGO class) moved that quarter's yield x10. When at
    least `_MCAP_MEDIAN_MIN_POINTS` values sit within ±5 days and the nearest one is more
    than `_MCAP_OUTLIER_FRACTION` away from their median, the median is used and logged.
    The quarter is never dropped — that would silently change the bar count.
    """
    nearest = _market_cap_on(date_str, lookup)
    if nearest is None:
        return None
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        return nearest
    window = [
        lookup[k]
        for k in ((dt + timedelta(days=delta)).strftime("%Y-%m-%d") for delta in range(-5, 6))
        if k in lookup
    ]
    if len(window) < _MCAP_MEDIAN_MIN_POINTS:
        return nearest
    median = statistics.median(window)
    if median > 0 and abs(nearest - median) / median > _MCAP_OUTLIER_FRACTION:
        logger.warning(
            "[soc-mcap-outlier] %s %s: market cap %.0f is %.0f%% off the ±5-day median "
            "%.0f over %d days — using the median (a one-day vendor glitch would move this "
            "quarter's yield by the same factor)",
            ticker or "?", date_str[:10], nearest,
            abs(nearest - median) / median * 100, median, len(window),
        )
        return median
    return nearest


def _positive_finite(record: Any, *keys: str) -> Optional[float]:
    """First of ``keys`` holding a finite value > 0, else None (a 0 cap is not a cap)."""
    if not isinstance(record, dict):
        return None
    for key in keys:
        value = _safe_float(record, key)
        if value is not None and value > 0:
            return value
    return None


def _day(date_str: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _cash_known(dp: Any, missing: Set[str]) -> bool:
    """True when this point's cash was MEASURED: its label is not in the builder's
    ``missing`` set AND the point does not say ``cash_flow_reported=False`` (P19). Both,
    because a caller without the diagnostics (tests, older call sites) still holds the
    point's own flag, and a 0.0 placeholder summed as a measured zero dropped a steady
    repurchaser's T12M by a quarter."""
    return (
        getattr(dp, "period", None) not in missing
        and getattr(dp, "cash_flow_reported", True) is not False
    )


@dataclass
class _QuarterDiagnostics:
    """What `_build_quarters` could NOT measure, for the builder's degraded gate.

    The points' cash fields stay non-Optional numbers (shipped iOS decodes them as
    `Double`), so a gap travels beside them: here for the builder's gate, and on the
    point itself as `cash_flow_reported=False` (P19) for every reader of the wire.
    """

    #: Labels of quarters with no cash-flow row for their period end: the newest edge,
    #: TRIMMED from the series (round-2 R47), and interior / leading-edge gaps, which keep
    #: their point (0.0 placeholders flagged `cash_flow_reported=False`, skipped by the
    #: summary and the verdicts).
    missing_cash_flow_periods: List[str] = field(default_factory=list)
    #: True only when the newest edge was TRIMMED (the row has not landed yet). An interior
    #: or leading-edge gap never sets it (P19): it is a vendor history hole that may never
    #: close, flagged on its point, and must not keep the build out of the 24h tier.
    missing_cash_flow_recent: bool = False
    #: Labels that returned capital but had no usable market cap (yield charted 0.00%).
    unpriced_periods: List[str] = field(default_factory=list)
    #: Labels whose yield is the single quarter x4 (no consecutive four-quarter window).
    ttm_fallback_periods: List[str] = field(default_factory=list)
    #: Period end (yyyy-MM-dd) of the oldest displayed quarter, or None.
    oldest_period_end: Optional[str] = None
    #: False when NO displayed quarter has a cash-flow row: the statement is missing, not
    #: lagging, and every point is an unknown (`cash_flow_reported=False`, 0.0
    #: placeholders) with nothing measured to show. The builder flags
    #: `cash_flow_statement_missing` (a 200 with nothing usable, possibly permanent).
    cash_flow_rows_found: bool = True
    #: True when the trimmed newest edge is longer than `_MAX_CF_LAG_QUARTERS` or the kept
    #: series ends more than `_CF_STALE_MAX_DAYS` before the newest income quarter: the
    #: cash-flow feed is STALE, not lagging one filing. The builder flags `cash_flow`.
    cash_flow_stale: bool = False


# ── Service ───────────────────────────────────────────────────────

class SignalOfConfidenceService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_signal_of_confidence(self, ticker: str) -> SignalOfConfidenceResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"signal_of_confidence:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Signal of confidence in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Signal of confidence Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Signal of confidence in-flight JOIN for {ticker}")
            return await asyncio.shield(_inflight[cache_key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Signal of confidence cache MISS for {ticker} — fetching from FMP")
            build = await self._build_signal_of_confidence_full(ticker)
            result, next_earnings, degraded_slices = (
                build.response, build.next_earnings, build.degraded,
            )
            # A fund's empty build (see `_SocBuild`) is its permanent answer, not an
            # outage. Re-checked against what is actually served, so a verdict can never
            # carry a build that has points or reasons.
            fund_shape = (
                build.fund_shape and not degraded_slices and _is_empty_build(result)
            )

            # NEVER persist a degraded build. A single FMP 429 on the quarterly income
            # call is turned into `[]` by `return_exceptions=True`, `_build_data_points`
            # returns `[]`, and `_build_summary` takes its empty branch — producing a
            # structurally valid response reading total_yield 0.0 / dividend 0.0 /
            # buyback 0.0 / share change 0.0. Written to the 24-hour tier that is a
            # FABRICATED "returns nothing to shareholders" verdict for a day, for every
            # user, and it is frozen into the 20-credit report. The 5-minute in-memory
            # tier still absorbs the retry storm. Mirrors profit_power_service's gate.
            #
            # The same holds one layer down: a failed `ratios` or `profile` call leaves
            # the payer verdict without its per-share record, and a build that ran without
            # it must not be pinned for a day either (the record is what keeps PLUG's
            # mis-tagged dividend line off the chart).
            #
            # A degraded build also lives in memory for `_DEGRADED_CACHE_TTL` only, and
            # carries its reasons in `result.degraded` so the client skips its own cache.
            #
            # The one exception is a fund's empty build (`fund_shape`): no data points is
            # what a fund IS, so it is persisted (marked) and kept for the normal TTL.
            soc_degraded = (
                not getattr(result, "data_points", None) and not fund_shape
            ) or bool(degraded_slices)
            if soc_degraded:
                logger.warning(
                    "Signal of confidence NOT persisted for %s (degraded: %s) — held in "
                    "memory for %ds, then rebuilt",
                    ticker,
                    ", ".join(degraded_slices) if degraded_slices else "no data points survived the build",
                    _DEGRADED_CACHE_TTL,
                )
            elif next_earnings is CALENDAR_UNKNOWN:
                # Complete values (the calendar feeds none), but the 24h row would carry no
                # report-day bound. Not degraded: the memory tier keeps its normal TTL.
                if fund_shape:
                    logger.info(
                        "[fund-shape] ticker=%s step=%s", ticker, "signal_of_confidence_memory",
                    )
                logger.warning(
                    "Signal of confidence NOT persisted for %s (earnings calendar "
                    "unavailable — no report-day bound for the 24h row); served from memory",
                    ticker,
                )
            else:
                if fund_shape:
                    logger.info(
                        "[fund-shape] ticker=%s step=%s", ticker, "signal_of_confidence_write",
                    )
                # Persist to Supabase in background. `fund_shape` is POSITIONAL:
                # `run_in_executor` forwards no keywords.
                asyncio.get_running_loop().run_in_executor(
                    None,
                    self._upsert_supabase_cache_safe,
                    ticker,
                    result,
                    next_earnings,
                    fund_shape,
                )

            _cache_set(cache_key, result, ttl=_DEGRADED_CACHE_TTL if soc_degraded else None)
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

    def _check_supabase_cache(self, ticker: str) -> Optional[SignalOfConfidenceResponse]:
        """Return cached response if fresh (< 24h and before next earnings)."""
        try:
            row = (
                self.supabase.table("signal_of_confidence_cache")
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

            # SCHEMA DRIFT GUARD. `buyback_status` moved onto the summary so a
            # non-dividend payer could carry a buyback verdict at all. A row cached
            # BEFORE that change has no such key, and the Pydantic field's `"Low"`
            # default then silently fills it in — reproducing the exact defect (a
            # confident "Low" for the market's largest repurchasers) for a further 24h,
            # with nothing to distinguish it from a real measurement.
            #
            # A default is the wrong tool for a value that must be COMPUTED, so detect
            # the drift and recompute instead of serving a plausible-looking guess.
            # Self-limiting: it stops mattering once every cached row is rewritten.
            # A VERSION, not a key probe. The original guard tested
            # `"buyback_status" not in summary`, which detects exactly one historical
            # change and nothing since: rows written before `annual_dividends` existed
            # pass it and are served for 24h with the field defaulted to `[]` ("no
            # dividend history" for a dividend king), and — worse — rows written before
            # the `status` denominator was corrected pass it while carrying a value that
            # is simply WRONG (JNJ cached as "Low"). A key probe cannot see a changed
            # formula at all. Bump `_PAYLOAD_VERSION` whenever a field is added OR a
            # stored value's computation changes, and every stale row recomputes on its
            # next read. Self-limiting, same as before.
            version = (json_data or {}).get("payload_version")
            if version != _PAYLOAD_VERSION:
                logger.info(
                    "Supabase cache STALE for %s (payload_version=%r, want %d) — recomputing",
                    ticker, version, _PAYLOAD_VERSION,
                )
                return None
            # A degraded build is never written (see `get_signal_of_confidence`); a row
            # that nonetheless carries reasons is refused rather than served for 24h.
            if json_data.get("degraded"):
                logger.warning(
                    "Supabase cache row for %s carries degraded=%r — refusing it and "
                    "recomputing", ticker, json_data.get("degraded"),
                )
                return None
            security_kind = json_data.get(_SECURITY_KIND_KEY)
            # Not response fields; strip them so the model never sees them. (Pydantic v2
            # ignores extras by default, but relying on that would break the moment
            # someone sets `extra="forbid"`.)
            json_data = {
                k: v for k, v in json_data.items()
                if k not in ("payload_version", _SECURITY_KIND_KEY)
            }

            resp = SignalOfConfidenceResponse(**json_data)
            # The empty build (no data points) is persisted ONLY for a fund, and then
            # marked. An unmarked one was written by hand or by a bug and could be the
            # fabricated "returns nothing to shareholders" verdict — refuse it.
            if _is_empty_build(resp):
                if security_kind != _SECURITY_KIND_FUND:
                    logger.warning(
                        "Supabase cache row for %s is an EMPTY build (no data points) "
                        "without the fund marker (security_kind=%r) — refusing it and "
                        "recomputing", ticker, security_kind,
                    )
                    return None
                logger.info(
                    "[fund-shape] ticker=%s step=%s", ticker, "signal_of_confidence_read",
                )
            return resp

        except Exception as e:
            logger.warning(f"Supabase cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache_safe(
        self,
        ticker: str,
        result: SignalOfConfidenceResponse,
        next_earnings: EarningsStamp,
        fund_shape: bool = False,
    ) -> None:
        """Upsert to Supabase cache — safe wrapper that logs and swallows errors.

        ``fund_shape`` is the getter's verdict (`_SocBuild.fund_shape`); it stamps the
        `security_kind: "fund"` marker the reader needs to admit an empty build — and only
        onto a result that really is one."""
        if not stamp_is_persistable(next_earnings):
            # Belt-and-braces: the getter already refuses a CALENDAR_UNKNOWN build.
            logger.warning(
                "Signal of confidence upsert REFUSED for %s — next_earnings_date %r is not "
                "a date (an unread earnings calendar is never persisted)",
                ticker, next_earnings,
            )
            return
        try:
            response_json: Dict[str, Any] = {
                **result.model_dump(), "payload_version": _PAYLOAD_VERSION,
            }
            if fund_shape:
                if _is_empty_build(result) and not result.degraded:
                    response_json[_SECURITY_KIND_KEY] = _SECURITY_KIND_FUND
                else:
                    logger.warning(
                        "[fund-shape-refused] ticker=%s step=signal_of_confidence_write: a fund "
                        "verdict came with a build that is not the empty fund shape (%d data "
                        "point(s), degraded=%r) — written WITHOUT the fund marker",
                        ticker, len(result.data_points or []), result.degraded,
                    )
            self.supabase.table("signal_of_confidence_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": response_json,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": next_earnings,
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(f"Supabase signal_of_confidence upsert failed for {ticker}: {e}")

    # ── Builder ───────────────────────────────────────────────────

    async def _build_signal_of_confidence(
        self, ticker: str
    ) -> Tuple[SignalOfConfidenceResponse, EarningsStamp, List[str]]:
        """Fetch FMP data, compute per-quarter shareholder yield, build response.

        Returns ``(response, next_earnings_date, degraded)`` — ``degraded`` names the
        upstream slices that RAISED and were substituted with an empty default; the
        caller refuses to persist such a build (see `get_signal_of_confidence`).
        ``next_earnings_date`` is CALENDAR_UNKNOWN when the calendar fetch failed: the
        response is complete (not degraded), but the getter never persists it.

        A thin adapter over `_build_signal_of_confidence_full` (which also carries the
        fund verdict the getter needs); the 3-tuple is kept because callers unpack it.
        """
        build = await self._build_signal_of_confidence_full(ticker)
        return build.response, build.next_earnings, build.degraded

    async def _build_signal_of_confidence_full(self, ticker: str) -> _SocBuild:
        """The build behind `_build_signal_of_confidence`, plus its fund verdict
        (`_SocBuild.fund_shape`)."""
        degraded: List[str] = []

        # Phase 1: parallel FMP fetch (6 calls). historical-market-cap covers
        # ~6y so every displayed quarter can be valued at ITS OWN period end.
        today = datetime.now(timezone.utc).date()
        mcap_from = (today - timedelta(days=6 * 365)).strftime("%Y-%m-%d")
        mcap_to = today.strftime("%Y-%m-%d")

        (
            quarterly_cashflow,
            quarterly_income,
            quote_data,
            annual_ratios,
            ec_raw,
            hist_mcap_raw,
            ex_dividend_dates,
            profile_raw,
        ) = await asyncio.gather(
            self.fmp.get_cash_flow_statement(ticker, period="quarter", limit=20),
            self.fmp.get_income_statement(ticker, period="quarter", limit=20),
            price_source(self).get_quote(ticker),
            # Was `get_dividend_history`, which is outside the FMP licence and answered
            # 402 on every request — a guaranteed failure occupying a slot in this gather.
            # `ratios` (annual) is entitled and carries the dividend AMOUNTS instead.
            self.fmp.get_financial_ratios(
                ticker, period="annual", limit=_ANNUAL_DIVIDEND_YEARS
            ),
            # raise_errors=True: the default swallows a 429 / 5xx / non-list body into
            # [] ("no announcements"), and the row was persisted with no report-day bound.
            self.fmp.get_earning_calendar_full(ticker, raise_errors=True),
            self.fmp.get_historical_market_cap(
                ticker, from_date=mcap_from, to_date=mcap_to, limit=2000
            ),
            # Ex-dividend DATES, derived from entitled price series. `/dividends` is 402,
            # so this row read a permanent "N/A" on every dividend payer in the market.
            # Dates come back exact (AAPL 6/6, KO / MSFT / JNJ 6/6 each); AMOUNTS do not
            # and are taken from `annual_ratios` above instead.
            corporate_actions_source(self).get_ex_dividend_dates(
                ticker, *window_for_range(None, mcap_to)
            ),
            # `/stable/profile` carries `lastDividend` (the TTM per-share total, see
            # `etf_service`), the one live per-share signal a company that INITIATED a
            # dividend this fiscal year has before `ratios` (completed FYs only) catches
            # up. The quote row above is profile-backed but `_shape` does not forward it.
            self.fmp.get_company_profile(ticker),
            return_exceptions=True,
        )

        # Fund-shape inputs (`_SocBuild.fund_shape`), read from the RAW answers before any
        # substitution or `_as_list` coercion: a leg that raised, or answered an error dict
        # (silently coerced to [] below), is a failure — never a fund's empty answer. The
        # profile must be a dict that POSITIVELY says fund. The annual ratios still feed a
        # fund's card (`dividend_info`'s per-share history), so that leg must have answered
        # a list too (rows allowed): an error dict there is coerced to [] with no reason.
        raw_profile_is_fund = profile_is_fund(profile_raw)
        raw_income_answered_empty = isinstance(quarterly_income, list) and not quarterly_income
        raw_cashflow_answered_empty = (
            isinstance(quarterly_cashflow, list) and not quarterly_cashflow
        )
        raw_ratios_answered_list = isinstance(annual_ratios, list)

        # Handle failures gracefully
        if isinstance(quarterly_cashflow, Exception):
            logger.error(f"Quarterly cash flow fetch failed for {ticker}: {quarterly_cashflow}")
            quarterly_cashflow = []
            degraded.append("cash_flow")
        if isinstance(quarterly_income, Exception):
            logger.error(f"Quarterly income fetch failed for {ticker}: {quarterly_income}")
            quarterly_income = []
            degraded.append("income")
        if isinstance(quote_data, Exception):
            logger.warning(f"Quote fetch failed for {ticker}: {quote_data}")
            quote_data = {}
        if isinstance(annual_ratios, Exception):
            logger.warning(
                "Annual ratios fetch failed for %s (%s: %s) — the dividend amounts and "
                "growth are omitted and the payer verdict runs without its per-share "
                "record; this build is served from memory only, not persisted",
                ticker, type(annual_ratios).__name__, annual_ratios,
            )
            annual_ratios = []
            degraded.append("annual_ratios")
        # The calendar is not a slice (it feeds no served value): its only use is the
        # cache row's next-earnings stamp. A failed fetch is CALENDAR_UNKNOWN, never
        # `degraded`; the getter serves that build from memory and never persists it.
        next_earnings = next_earnings_stamp(
            ec_raw, ticker=ticker, service="signal_of_confidence"
        )
        ec_raw = []
        if isinstance(ex_dividend_dates, Exception):
            logger.warning(
                "Ex-dividend date derivation failed for %s (%s: %s) — the date row is "
                "hidden rather than guessed",
                ticker, type(ex_dividend_dates).__name__, ex_dividend_dates,
            )
            ex_dividend_dates = []
        if isinstance(hist_mcap_raw, Exception):
            logger.warning(
                f"Historical market cap fetch failed for {ticker}: {hist_mcap_raw} "
                f"— per-quarter yields fall back to the current market cap"
            )
            hist_mcap_raw = []
        if isinstance(profile_raw, Exception):
            logger.warning(
                "[soc-profile-unavailable] %s: %s: %s — the payer verdict falls back to "
                "the per-share record alone (an initiator may read as a non-payer for "
                "one refresh); this build is served from memory only, not persisted",
                ticker, type(profile_raw).__name__, profile_raw,
            )
            profile_raw = {}
            degraded.append("profile")
        if isinstance(profile_raw, list):
            profile_raw = profile_raw[0] if profile_raw and isinstance(profile_raw[0], dict) else {}
        profile = profile_raw if isinstance(profile_raw, dict) else {}

        # Normalize quote_data — FMP returns list for quote endpoint
        if isinstance(quote_data, list):
            quote_data = quote_data[0] if quote_data else {}
        if not isinstance(quote_data, dict):
            quote_data = {}

        # A cash-flow leg that answered something other than a list (an FMP error dict, a
        # null body) failed in all but name: `cash_flow`, the blocking reason, exactly like
        # a raise — NOT `cash_flow_statement_missing`, which is a real (possibly permanent)
        # 200 with no usable row and must not make the report uncacheable (round-3 P10).
        if not isinstance(quarterly_cashflow, list) and "cash_flow" not in degraded:
            logger.warning(
                "[soc-cashflow-not-a-list] ticker=%s step=cash_flow: the quarterly cash-flow "
                "leg answered %s, not a list — treated as a failed leg; this build is "
                "served from memory only, not persisted",
                ticker, type(quarterly_cashflow).__name__,
            )
            degraded.append("cash_flow")

        # Ensure all are lists
        quarterly_cashflow = _as_list(quarterly_cashflow)
        quarterly_income = _as_list(quarterly_income)
        annual_ratios = _as_list(annual_ratios)
        hist_mcap_raw = _as_list(hist_mcap_raw)

        # Phase 2: build per-quarter data points
        #
        # The current cap falls back to the profile's own `marketCap` (`mktCap` on the
        # legacy shape). `price_service.get_quote` swallows a profile failure into `{}`, so
        # a split failure — its internal profile read down, our own profile call up — left
        # this None; with the historical series also down every yield read 0.00%, both
        # verdicts read "Low" for a top repurchaser, and nothing marked the build degraded.
        current_market_cap = _positive_finite(quote_data, "marketCap") or _positive_finite(
            profile, "marketCap", "mktCap"
        )
        mcap_by_date = _build_market_cap_lookup(hist_mcap_raw)

        # ONE payer verdict, shared by the bars and the card below, so the two can never
        # disagree again (the card said "no dividend" while the bars charted 1.75%).
        pays_common_dividend = self._pays_common_dividend(
            annual_ratios, profile, ex_dividend_dates
        )

        data_points, diag = self._build_quarters(
            quarterly_cashflow,
            quarterly_income,
            current_market_cap,
            mcap_by_date,
            ticker,
            pays_common_dividend=pays_common_dividend,
            dividend_by_year=self._annual_dividend_map(annual_ratios),
        )

        # A quarter that returned capital but could not be priced charts 0.00%, and the
        # verdicts below read that as "returns nothing". Served from memory only.
        if diag.unpriced_periods:
            logger.warning(
                "[soc-market-cap-unavailable] %s: %d quarter(s) returned capital but have "
                "no usable market cap (%s; current cap %s) — their yields read 0.00%%; "
                "this build is served from memory only, not persisted",
                ticker, len(diag.unpriced_periods), ", ".join(diag.unpriced_periods),
                "unavailable" if current_market_cap is None else "present",
            )
            degraded.append("market_cap")
        # A statement that ANSWERED (a list, no raise) but matched NO displayed quarter:
        # every bar is an unknown charted as $0 and both verdicts read "returns nothing",
        # so the report must not show the section. But unlike a raised leg it may be
        # PERMANENT for this ticker (an empty or off-cycle vendor feed), so it is its own
        # reason, `cash_flow_statement_missing`: the report collector drops the section
        # WITHOUT marking the report degraded, so the report stays cacheable instead of
        # being re-run and re-billed on every open (round-3 P10). `cash_flow` stays the
        # reason for a leg that raised or answered a non-list (above).
        if data_points and not diag.cash_flow_rows_found and "cash_flow" not in degraded:
            logger.warning(
                "[soc-cashflow-statement-empty] ticker=%s step=cash_flow: %d income quarter(s) "
                "but no cash-flow row for any of them (%d row(s) returned) — every yield is "
                "unknown; marked cash_flow_statement_missing; this build is served from "
                "memory only, not persisted",
                ticker, len(data_points), len(quarterly_cashflow),
            )
            degraded.append("cash_flow_statement_missing")
        # A trimmed newest edge longer than one filing's lag is a STALE feed: the series
        # and its T12M were rewound to quarters years old while reading only as the
        # ignorable `cash_flow_row`, which the report froze (round-3 P9). It is reported
        # as `cash_flow_statement_missing`: never persisted here, and the report DROPS the
        # section — but, being a vendor gap that may never close, it does not make every
        # report for the ticker uncacheable (owner decision 2026-10-01). The trim itself
        # stays (an unmeasured point is never $0). `_build_quarters` logs the detail under
        # [soc-cashflow-stale].
        if (
            diag.cash_flow_stale
            and "cash_flow" not in degraded
            and "cash_flow_statement_missing" not in degraded
        ):
            degraded.append("cash_flow_statement_missing")
        # `cash_flow_row` names ONLY a trimmed newest edge: the newest quarter's cash-flow
        # row has not landed yet, so the build stays out of the 24h tier until it does. An
        # interior or leading-edge gap emits NO reason (P19, 2026-10-01): it is a vendor
        # history hole that may never close, so it must not keep the ticker out of the
        # cache for up to four quarters; its point ships `cash_flow_reported=False` and is
        # skipped by the summary and the verdicts. Skip this when the whole cash-flow leg
        # failed, is stale, or matched nothing — "cash_flow" /
        # "cash_flow_statement_missing" already say so.
        if diag.missing_cash_flow_recent and not (
            {"cash_flow", "cash_flow_statement_missing"} & set(degraded)
        ):
            degraded.append("cash_flow_row")

        # Phase 3: build trailing-12-month summary
        missing_cf = set(diag.missing_cash_flow_periods)
        fallback_x4 = set(diag.ttm_fallback_periods)
        summary = self._build_summary(
            data_points, current_market_cap, missing_cash_flow_periods=missing_cf,
            ttm_fallback_periods=fallback_x4, ticker=ticker,
        )

        # A spin-off inside the window makes every pre-spin historical cap too small
        # (FMP back-computes it from the spin-adjusted price series), so the older yields
        # the dividend verdict compares against are inflated by the spin factor. Asked only
        # when the RELATIVE verdict can run at all (the same inputs `_build_dividend_info`
        # uses): a trailing-twelve-month newest point and a positive baseline of enough
        # trailing-twelve-month points. Anything else runs the absolute ladder anyway, and
        # an unknown answer would degrade the build for nothing.
        spinoff_in_window = False
        newest, baseline = self._verdict_points(data_points, missing_cf, fallback_x4)
        if (
            diag.oldest_period_end
            and newest is not None
            and newest.period not in fallback_x4
            and len(baseline) >= _MIN_BASELINE_POINTS
            and round(sum(dp.dividend_yield for dp in baseline) / len(baseline), 2) > 0
        ):
            checked = await self._spinoff_in_window(ticker, diag.oldest_period_end)
            if checked is None:
                # Unknown is NOT a spin-off: the relative verdict runs as it always did,
                # and the build stays out of the 24h tier so the next refresh re-checks.
                degraded.append("spinoff_check")
            spinoff_in_window = bool(checked)

        # Phase 4: build dividend info (optional)
        dividend_info = self._build_dividend_info(
            [],  # per-payment history is unlicensed; amounts come from `annual_ratios`
            summary.dividend_yield,
            summary.buyback_yield,
            summary.share_count_change,
            data_points=data_points,
            annual_ratios=annual_ratios,
            ex_dividend_dates=ex_dividend_dates,
            pays_common_dividend=pays_common_dividend,
            missing_cash_flow_periods=missing_cf,
            spinoff_in_window=spinoff_in_window,
            ttm_fallback_periods=fallback_x4,
            ticker=ticker,
        )

        # Phase 5: `next_earnings` was stamped from the calendar slot right after the gather.

        response = SignalOfConfidenceResponse(
            symbol=ticker,
            data_points=data_points,
            summary=summary,
            dividend_info=dividend_info,
            degraded=list(degraded),
        )

        fund_shape = bool(
            raw_profile_is_fund
            and raw_income_answered_empty
            and raw_cashflow_answered_empty
            and raw_ratios_answered_list
            and not data_points
            and not degraded
        )
        if raw_profile_is_fund and not data_points and not fund_shape:
            # Diagnosable from logs alone: the profile says fund, but the empty build is
            # NOT admitted — a leg failed or answered something other than [].
            logger.info(
                "[fund-shape-refused] ticker=%s step=fund_check: the profile says fund but the "
                "empty build is not admitted (income answered []: %s, cash flow answered "
                "[]: %s, annual ratios answered a list: %s, degraded=%r) — it stays "
                "unpersisted",
                ticker, raw_income_answered_empty, raw_cashflow_answered_empty,
                raw_ratios_answered_list, degraded,
            )

        return _SocBuild(
            response=response,
            next_earnings=next_earnings,
            degraded=degraded,
            fund_shape=fund_shape,
        )

    async def _spinoff_in_window(self, ticker: str, oldest_period_end: str) -> Optional[bool]:
        """TRI-STATE: True when an adjustment the classifier could not name as a split (a
        spin-off) took effect after ``oldest_period_end``, False when the window was
        derived and holds none, **None when it could not be derived** (a price leg 429'd,
        timed out or came back short — or the call raised).

        ⚠️ Never `has_unclassified_adjustment` here. That method fails CLOSED (True) for
        the 13F magnitude backstop, and reading its "could not look" as "a spin-off
        happened" zeroed the baseline, so the absolute ladder ran on the T12M: a steady
        0.4% payer (AAPL-shaped) read red "Low" instead of "Fair", a 3% payer "High", and
        nothing marked the build degraded, so it was persisted for a day (round-2 R20).
        The caller keeps the relative verdict on None and marks the build degraded.
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            from_date, to_date = window_for_range(oldest_period_end, today_str)
            answer = await corporate_actions_source(self).unclassified_adjustment_or_none(
                ticker, from_date, to_date,
                effective_from=oldest_period_end, effective_to=today_str,
            )
        except Exception as e:
            logger.warning(
                "[soc-spinoff-check-unavailable] ticker=%s step=spinoff_check: %s: %s — "
                "the window %s..%s could not be checked; the relative dividend verdict "
                "runs and the build is served from memory only, not persisted",
                ticker, type(e).__name__, e, oldest_period_end, today_str,
            )
            return None
        if answer is None:
            logger.warning(
                "[soc-spinoff-check-unavailable] ticker=%s step=spinoff_check: the price "
                "series for %s..%s could not be derived — NOT read as a spin-off; the "
                "relative dividend verdict runs and the build is served from memory only, "
                "not persisted",
                ticker, oldest_period_end, today_str,
            )
            return None
        return bool(answer)

    # ── Per-quarter data points ───────────────────────────────────

    def _build_data_points(
        self,
        cashflow_records: List[Dict[str, Any]],
        income_records: List[Dict[str, Any]],
        current_market_cap: Optional[float],
        mcap_by_date: Optional[Dict[str, float]] = None,
        ticker: str = "",
        pays_common_dividend: Optional[bool] = None,
        dividend_by_year: Optional[Dict[str, float]] = None,
    ) -> List[SignalOfConfidenceDataPointSchema]:
        """The points of `_build_quarters`, without its diagnostics (callers that only
        chart). The builder uses `_build_quarters` so the gaps reach its degraded gate."""
        points, _diag = self._build_quarters(
            cashflow_records,
            income_records,
            current_market_cap,
            mcap_by_date,
            ticker,
            pays_common_dividend=pays_common_dividend,
            dividend_by_year=dividend_by_year,
        )
        return points

    @staticmethod
    def _gate_cash_flow_row(
        cf_rec: Dict[str, Any],
        quarter_pays: Optional[bool],
        ticker: str,
        date: str,
    ) -> Tuple[Optional[float], float, bool]:
        """``(dividend_dollars | None, buyback_dollars, payer_line_missing)`` for one
        cash-flow row, both amounts as positive dollars.

        The dividend gates, in order:
        * `commonDividendsPaid` is the /stable field; legacy `dividendsPaid` only when that
          KEY is absent (a present 0 is a real zero); `netDividendsPaid` — common PLUS
          preferred — only for a known payer, because on a non-payer it charts preferred
          coupons as a common dividend.
        * Outflow sign, mirroring the buyback gate: a positive value is not a dividend (a
          reclass, a refund, a sign error), and neither is any value at all when the
          per-share record says the company pays no common dividend in this fiscal year.
        Run over EVERY row a trailing window touches — not only the displayed quarters —
        so a trailing sum can never add back a line the displayed bar would have refused.
        """
        dividends_paid_raw = _safe_float(cf_rec, "commonDividendsPaid")
        common_absent = cf_rec.get("commonDividendsPaid") is None
        if dividends_paid_raw is None and common_absent:
            dividends_paid_raw = _safe_float(cf_rec, "dividendsPaid")
        if (
            dividends_paid_raw is None
            and quarter_pays is True
            and common_absent
            and cf_rec.get("dividendsPaid") is None
        ):
            dividends_paid_raw = _safe_float(cf_rec, "netDividendsPaid")

        if quarter_pays is False:
            dividends_paid_raw = None
        elif dividends_paid_raw is not None and dividends_paid_raw >= 0:
            if dividends_paid_raw > 0:
                logger.info(
                    "[soc-dividend-sign] %s %s: dividend line %.0f is not an outflow "
                    "— charted as 0",
                    ticker or "?", date, dividends_paid_raw,
                )
            dividends_paid_raw = None
        # A known payer whose row carries no usable line: a data gap, not a measurement.
        missing_line = dividends_paid_raw is None and quarter_pays is True

        dividend = abs(dividends_paid_raw) if dividends_paid_raw else None

        # Buyback: commonStockRepurchased is negative when buying back; positive or zero
        # is issuance or nothing.
        repurchased_raw = _safe_float(cf_rec, "commonStockRepurchased")
        buyback = abs(repurchased_raw) if (repurchased_raw is not None and repurchased_raw < 0) else 0.0
        return dividend, buyback, missing_line

    @staticmethod
    def _is_consecutive_window(dates: List[str]) -> bool:
        """True when ``dates`` (ascending period ends) are back-to-back fiscal quarters —
        see `_TTM_GAP_MIN_DAYS` / `_TTM_GAP_MAX_DAYS` / `_TTM_SPAN_MAX_DAYS`."""
        days = [_day(d) for d in dates]
        if any(d is None for d in days):
            return False
        for prev, cur in zip(days, days[1:]):
            gap = (cur - prev).days
            if gap < _TTM_GAP_MIN_DAYS or gap > _TTM_GAP_MAX_DAYS:
                return False
        return (days[-1] - days[0]).days <= _TTM_SPAN_MAX_DAYS

    def _build_quarters(
        self,
        cashflow_records: List[Dict[str, Any]],
        income_records: List[Dict[str, Any]],
        current_market_cap: Optional[float],
        mcap_by_date: Optional[Dict[str, float]] = None,
        ticker: str = "",
        pays_common_dividend: Optional[bool] = None,
        dividend_by_year: Optional[Dict[str, float]] = None,
    ) -> Tuple[List[SignalOfConfidenceDataPointSchema], _QuarterDiagnostics]:
        """Build per-quarter data points from FMP data, plus what could not be measured.

        **Yields are TRAILING TWELVE MONTHS** (owner decision, 2026-09-30): the gated cash
        of this quarter and the three before it, over the market cap at THIS quarter's
        period end (point-in-time — today's cap mis-states an old quarter by the whole
        re-rating since). They used to be the single quarter x4, but `commonDividendsPaid`
        is cash that happened to SETTLE in the quarter: KO's unchanged dividend charted
        0.12% / 2.92% / 2.92% / 5.85% against a 2.95% yield, and one accelerated buyback
        read 40% "annualised" and flattened every other bar. When the four rows are not
        consecutive quarters the quarter falls back to x4 and is logged.
        `dividend_amount` / `buyback_amount` stay the raw quarter's cash (the Capital view
        and the T12M summary add them up).

        ``pays_common_dividend`` is the shared per-share verdict from
        `_pays_common_dividend`: ``False`` zeroes every dividend bar regardless of the
        cash-flow line (the line is not the same question — PLUG's carried -$16.5M in
        Q2'26 with the outflow sign and everything, for a company that has never paid
        a common dividend), ``True`` additionally unlocks the preferred-inclusive
        `netDividendsPaid` fallback, ``None`` (record unavailable) trusts the sign-gated
        cash-flow line as before.

        ``dividend_by_year`` (``{fiscal_year: dividendPerShare}``, the same record the
        verdict is read from) makes the gate PER FISCAL YEAR: a quarter in a year that
        shows a positive per-share dividend is a payer's quarter whatever the current
        verdict, and a quarter in a year that shows zero is not. A company that STOPPED
        (INTC: paid through FY2024, FY2025 = 0) keeps its real FY2024 bars and loses only
        the fabricated ones; a quarter in a year the record does not cover (the FY in
        progress) falls back to the overall verdict.

        Duplicates are collapsed before the newest eight are taken, so the window always
        holds eight DISTINCT quarters: rows sharing a `date` keep FMP's first (newest-first
        order, so a restatement wins), and two dates sharing a display label (a fiscal
        year-end change) keep the later one. Two points with one label used to double-count
        the T12M summary and land in ONE iOS category column with the label rows drifting
        off their bars.
        """
        mcap_by_date = mcap_by_date or {}
        dividend_by_year = dividend_by_year or {}
        tag = ticker or "?"
        diag = _QuarterDiagnostics()

        # Cash-flow rows by period end — FIRST occurrence wins. This used to keep the
        # LAST, i.e. the stale original behind a restatement.
        cf_by_date: Dict[str, Dict[str, Any]] = {}
        dup_cf = 0
        for rec in cashflow_records:
            date = str(rec.get("date") or "")[:10]
            if not date:
                continue
            if date in cf_by_date:
                dup_cf += 1
                continue
            cf_by_date[date] = rec

        income_by_date: Dict[str, Dict[str, Any]] = {}
        dup_income = 0
        for rec in income_records:
            date = str(rec.get("date") or "")[:10]
            if not date:
                continue
            if date in income_by_date:
                dup_income += 1
                continue
            income_by_date[date] = rec

        # Ascending, labelled, one row per display label (the chronologically latest).
        # Fiscal-year labels so off-calendar-FY companies (e.g. Oracle) read
        # monotonically: fiscal Q1 (Aug 2025) -> "Q1 '26", not "Q1 '25".
        labelled: List[Optional[Tuple[str, str, Dict[str, Any]]]] = []
        slot_by_label: Dict[str, int] = {}
        dup_label = 0
        for date in sorted(income_by_date):
            rec = income_by_date[date]
            label = quarterly_period_label(rec, use_fiscal_year=True)
            if not label or not label.startswith("Q"):
                continue
            if label in slot_by_label:
                labelled[slot_by_label[label]] = None
                dup_label += 1
            slot_by_label[label] = len(labelled)
            labelled.append((date, label, rec))
        quarters = [q for q in labelled if q is not None]
        if dup_cf or dup_income or dup_label:
            logger.warning(
                "[soc-dup-label] %s: collapsed %d duplicate cash-flow date(s), %d duplicate "
                "income date(s) and %d duplicate quarter label(s) — one point per quarter",
                tag, dup_cf, dup_income, dup_label,
            )

        cf_dates = sorted(cf_by_date)
        cf_index = {d: i for i, d in enumerate(cf_dates)}
        cf_match: Dict[str, Optional[str]] = {}

        def cash_flow_date_for(income_date: str) -> Optional[str]:
            """The cash-flow row for an income period end: the exact date, else the
            nearest within ±`_CF_DATE_TOLERANCE_DAYS` that no income row claims exactly.
            Both statements come from one filing, so a near-miss is a vendor date wobble;
            without this a one-day mismatch would read "no cash-flow row" on every build
            and keep the ticker out of the 24h tier for good."""
            if income_date in cf_match:
                return cf_match[income_date]
            matched: Optional[str] = None
            if income_date in cf_by_date:
                matched = income_date
            else:
                target = _day(income_date)
                near = [] if target is None else [
                    (abs((_day(d) - target).days), d) for d in cf_dates
                    if d not in income_by_date and _day(d) is not None
                    and abs((_day(d) - target).days) <= _CF_DATE_TOLERANCE_DAYS
                ]
                if near:
                    matched = min(near)[1]
                    logger.info(
                        "[soc-cashflow-date-nudge] %s: income %s matched to cash-flow row %s",
                        tag, income_date, matched,
                    )
            cf_match[income_date] = matched
            return matched

        # The NEWEST edge without a cash-flow row is trimmed BEFORE the window is taken
        # (round-2 R47): the cash-flow statement lags the income statement, so the newest
        # quarter's cash is UNKNOWN — and a point kept for its share count shipped "$0" /
        # "0.00%" on the wire with no marker, which the Financials bar labels, the report's
        # "Buybacks" header (`dataPoints.last`), its mini-chart and the Stage B prompt
        # ("it is not repurchasing stock") all read as a measured zero. Trimmed here, every
        # reader agrees: the series ends at the last quarter that HAS a cash-flow row, and
        # the window still holds eight quarters when the history has them. The labels stay
        # in `missing_cash_flow_periods` (logged; `cash_flow_row` keeps the build out of
        # the 24h tier until the row lands). Interior and leading-edge gaps keep their
        # point — the share line must stay continuous — flagged `cash_flow_reported=False`
        # and skipped by the summary and the verdicts; they set no reason (P19).
        # Only while an older quarter HAS a row: with none at all there is no edge to trim
        # to (the statement is missing, not lagging) and the builder flags
        # `cash_flow_statement_missing`. A trim longer than one filing is a stale feed
        # (`diag.cash_flow_stale`, below), which the builder flags as `cash_flow`.
        unmeasured_tail: List[Tuple[str, str]] = []
        if any(cash_flow_date_for(d) is not None for d, _lbl, _r in quarters):
            while quarters and cash_flow_date_for(quarters[-1][0]) is None:
                date, label, _rec = quarters.pop()
                unmeasured_tail.append((date, label))
        if unmeasured_tail:
            unmeasured_tail.reverse()
            diag.missing_cash_flow_periods.extend(lbl for _d, lbl in unmeasured_tail)
            diag.missing_cash_flow_recent = True
            logger.warning(
                "[soc-cashflow-row-missing] %s: no cash-flow row yet for the newest %d "
                "quarter(s) (%s) — trimmed from the series (their dividends and buybacks "
                "are unknown, not $0); it ends at %s; this build is served from memory "
                "only until the row lands",
                tag, len(unmeasured_tail),
                ", ".join(f"{lbl} {d}" for d, lbl in unmeasured_tail),
                f"{quarters[-1][1]} {quarters[-1][0]}" if quarters else "nothing",
            )
            # Bounded (round-3 P9): one filing of lag is "the row has not landed yet";
            # more is a STALE feed. The trim stays either way — the gate is the reason.
            newest_income = _day(unmeasured_tail[-1][0])
            newest_kept = _day(quarters[-1][0]) if quarters else None
            gap_days = (
                (newest_income - newest_kept).days
                if newest_income is not None and newest_kept is not None
                else None
            )
            if (
                len(unmeasured_tail) > _MAX_CF_LAG_QUARTERS
                or not quarters
                or (gap_days is not None and gap_days > _CF_STALE_MAX_DAYS)
            ):
                diag.cash_flow_stale = True
                logger.warning(
                    "[soc-cashflow-stale] ticker=%s step=cash_flow: the cash-flow statement "
                    "stops %d quarter(s) before the income statement (%s; newest kept %s, "
                    "%s day(s) behind; bounds %d quarter(s) / %d days) — a stale feed, not "
                    "a one-filing lag; the build is marked cash_flow and served from memory "
                    "only, not persisted",
                    tag, len(unmeasured_tail),
                    ", ".join(lbl for _d, lbl in unmeasured_tail),
                    f"{quarters[-1][1]} {quarters[-1][0]}" if quarters else "nothing",
                    "?" if gap_days is None else gap_days,
                    _MAX_CF_LAG_QUARTERS, _CF_STALE_MAX_DAYS,
                )

        # Take the most recent 8 DISTINCT quarters.
        recent = quarters[-8:]
        if recent:
            diag.oldest_period_end = recent[0][0]
        diag.cash_flow_rows_found = any(
            cash_flow_date_for(d) is not None for d, _lbl, _r in recent
        )

        def fiscal_year(date: str) -> str:
            src = income_by_date.get(date) or cf_by_date.get(date) or {}
            return str(src.get("fiscalYear") or src.get("calendarYear") or date[:4])

        def quarter_pays_for(date: str) -> Optional[bool]:
            # The verdict for THIS quarter: its fiscal year's per-share record when the
            # record covers it, else the overall (current) verdict.
            fy = fiscal_year(date)
            if fy in dividend_by_year:
                return dividend_by_year[fy] > 0
            return pays_common_dividend

        gated_by_date: Dict[str, Tuple[Optional[float], float, bool]] = {}

        def gated(date: str) -> Tuple[Optional[float], float, bool]:
            if date not in gated_by_date:
                gated_by_date[date] = self._gate_cash_flow_row(
                    cf_by_date[date], quarter_pays_for(date), ticker, date
                )
            return gated_by_date[date]

        results: List[SignalOfConfidenceDataPointSchema] = []
        interior_missing: List[str] = []
        fell_back_to_current = 0
        missing_line_for_payer = 0
        for date, label, rec in recent:
            # Shares outstanding from income statement (weighted average)
            # 0.0 is a SENTINEL here, not a share count — no listed company has zero
            # weighted-average shares. FMP does return `weightedAverageShsOut: 0` on real
            # rows (verified live: CD's newest quarter, 2026-06-30, while every older row
            # is populated), and collapsing that to 0.0 made it indistinguishable from a
            # measured value: the newest point read "0 shares outstanding" and the
            # share-count change came out as a flat -100%, rendered as a spectacular
            # buyback. Keep it as None so the summary can SKIP the point instead.
            shares_raw = _safe_float(rec, "weightedAverageShsOut")
            shares_outstanding = (
                round(shares_raw / 1_000_000, 2)
                if (shares_raw is not None and shares_raw > 0)
                else None
            )

            cf_date = cash_flow_date_for(date)
            has_cash_flow = cf_date is not None
            if has_cash_flow:
                dividend, buyback, missing_line = gated(cf_date)
                if missing_line:
                    # Keep the point (the shares line must stay continuous) and say so,
                    # rather than dropping the quarter silently.
                    missing_line_for_payer += 1
            else:
                # An INTERIOR or LEADING-EDGE quarter with no cash-flow row (the newest
                # edge was trimmed above), or every quarter when the statement returned
                # nothing usable. The share count is real, so the point stays; its cash is
                # UNKNOWN — the point ships `cash_flow_reported=False` with 0.0
                # placeholders, and the label is recorded so the summary and the verdicts
                # skip it. It sets NO degraded reason (P19): a vendor history hole may never
                # close, and `cash_flow_row` (which kept the build out of the 24h tier) now
                # names only the trimmed newest edge.
                dividend, buyback = None, 0.0
                diag.missing_cash_flow_periods.append(label)
                interior_missing.append(f"{label} {date}")

            dividend_amount = round(dividend / 1_000_000, 2) if dividend else 0.0
            buyback_amount = round(buyback / 1_000_000, 2) if buyback else 0.0

            # Market cap AT THIS QUARTER'S PERIOD END (point-in-time). Fall back to the
            # current cap only when the historical series doesn't reach this period.
            period_mcap = _robust_market_cap_on(date, mcap_by_date, ticker)
            if period_mcap is None:
                period_mcap = current_market_cap
                fell_back_to_current += 1

            # Trailing twelve months ending here, or this quarter x4 without a
            # consecutive window.
            window: Optional[List[str]] = None
            if has_cash_flow:
                i = cf_index[cf_date]
                if i >= _TRAILING_POINTS - 1:
                    candidate = cf_dates[i - (_TRAILING_POINTS - 1): i + 1]
                    if self._is_consecutive_window(candidate):
                        window = candidate
            if window is not None:
                dividend_sum = sum(gated(d)[0] or 0.0 for d in window)
                buyback_sum = sum(gated(d)[1] for d in window)
                annualise = 1
            else:
                dividend_sum = dividend or 0.0
                buyback_sum = buyback
                annualise = 4
                if has_cash_flow:
                    diag.ttm_fallback_periods.append(label)

            if period_mcap is not None and period_mcap > 0:
                dividend_yield = (
                    round(dividend_sum / period_mcap * 100 * annualise, 2)
                    if dividend_sum > 0 else 0.0
                )
                buyback_yield = (
                    round(buyback_sum / period_mcap * 100 * annualise, 2)
                    if buyback_sum > 0 else 0.0
                )
            else:
                dividend_yield = buyback_yield = 0.0
                if dividend_sum > 0 or buyback_sum > 0:
                    diag.unpriced_periods.append(label)

            results.append(SignalOfConfidenceDataPointSchema(
                period=label,
                dividend_yield=dividend_yield,
                buyback_yield=buyback_yield,
                dividend_amount=dividend_amount,
                buyback_amount=buyback_amount,
                shares_outstanding=shares_outstanding,
                cash_flow_reported=has_cash_flow,
            ))

        if fell_back_to_current:
            logger.warning(
                "signal_of_confidence %s: %d/%d quarters had no historical market "
                "cap within +-5d of the period end — those yields use the CURRENT "
                "cap and are not point-in-time",
                tag, fell_back_to_current, len(results),
            )
        if missing_line_for_payer:
            logger.warning(
                "[soc-dividend-missing] %s: %d/%d quarters of a known payer carry no "
                "dividend outflow on the cash-flow row — charted as 0.00%%, which is a "
                "gap, not a measurement",
                tag, missing_line_for_payer, len(results),
            )
        if interior_missing:
            logger.warning(
                "[soc-cashflow-row-missing] ticker=%s step=cash_flow: no cash-flow row for "
                "%d/%d displayed quarter(s) (%s)%s — their dividends and buybacks are "
                "unknown: flagged cash_flow_reported=false (rendered '—'), left out of the "
                "T12M and the verdicts",
                tag, len(interior_missing), len(results), ", ".join(interior_missing),
                "" if cf_by_date else " (the cash-flow statement returned no rows at all)",
            )
        if diag.ttm_fallback_periods:
            logger.warning(
                "[soc-ttm-fallback] %s: %d/%d quarter(s) lack four consecutive cash-flow "
                "quarters (%s) — their yields are the single quarter x4, not trailing "
                "twelve months",
                tag, len(diag.ttm_fallback_periods), len(results),
                ", ".join(diag.ttm_fallback_periods),
            )

        return results, diag

    # ── Payer verdict ─────────────────────────────────────────────

    @staticmethod
    def _pays_common_dividend(
        annual_ratios: Any,
        profile: Optional[Dict[str, Any]] = None,
        ex_dividend_dates: Optional[List[str]] = None,
        today: Optional[datetime] = None,
    ) -> Optional[bool]:
        """Does this company pay a COMMON dividend — the one verdict both the bars and
        the card use, so they cannot disagree.

        The cash-flow ``commonDividendsPaid`` line is NOT the answer: FMP tags preferred
        coupons and one-off distributions onto it with the outflow sign intact (PLUG
        Q2'26: -$16.5M; TSLA: a single 0.01% quarter). The per-share record is:

        * ``True``  — the MOST RECENT completed fiscal year has ``dividendPerShare > 0``
          (`ratios`, period=annual), OR the profile's ``lastDividend`` (the TTM per-share
          total) is positive. The profile is what rescues a company that initiated its
          dividend THIS fiscal year, before `ratios` has a completed year to show — and a
          payer whose latest `ratios` row is a zero stub.
        * ``False`` — the most recent per-share year reads ZERO: a company that never
          paid (PLUG) or one that STOPPED. This used to be "any year in the window", which
          made INTC a payer on the strength of FY2020-24 (1.33 → 0.37/share, suspended
          Q3 2024) and let a −14.3B Q2'26 cash-flow line FMP tags ``commonDividendsPaid``
          chart as an 8.79% dividend against a FY2025 DPS of 0 and a profile
          ``lastDividend`` of 0. Older positive years are history, not a current dividend.
          Also ``False`` with no usable per-share year when the profile says zero.
        * ``None``  — no record: `ratios` failed, is empty, or the rows exist but the
          ``dividendPerShare`` key is gone (the /stable drift class — a truthiness test on
          the payload here would zero every payer in the market). As a last rung, an
          ex-dividend date derived from the price series within
          `_EX_DIVIDEND_RECENCY_DAYS` reads as ``True``; it is deliberately below the
          record so a spurious derived date cannot override an authoritative zero.
        """
        by_year = SignalOfConfidenceService._annual_dividend_map(annual_ratios)
        # Profile `lastDividend`, with the same two-key read `etf_service` uses. A present
        # 0 means "pays none" and must not fall through to `lastDiv` — `0 or x` would.
        last_div = None
        if isinstance(profile, dict):
            raw = profile.get("lastDividend")
            if raw is None:
                raw = profile.get("lastDiv")
            last_div = _safe_float({"v": raw}, "v")

        latest_dps = by_year[max(by_year)] if by_year else None   # keys are "YYYY"
        if (latest_dps is not None and latest_dps > 0) or (last_div is not None and last_div > 0):
            return True
        if latest_dps is not None and latest_dps == 0:
            return False
        if last_div is not None and last_div == 0:
            # No usable per-share year (the `ratios` call failed, is empty, or lost its
            # key) but the profile says zero. The profile is FMP's own TTM per-share
            # statement and is trusted ALONE for the True verdict above, so it is trusted
            # alone for False too — otherwise a single 429 on `ratios` sent PLUG back to
            # the cash-flow line and its 1.75% bar.
            return False

        now = today or datetime.now(timezone.utc)
        for d in ex_dividend_dates or []:
            try:
                dt = datetime.strptime(str(d)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if 0 <= (now - dt).days <= _EX_DIVIDEND_RECENCY_DAYS:
                return True
        return None

    # ── Trailing-12-month summary ─────────────────────────────────

    def _build_summary(
        self,
        data_points: List[SignalOfConfidenceDataPointSchema],
        current_market_cap: Optional[float],
        missing_cash_flow_periods: Optional[Set[str]] = None,
        ttm_fallback_periods: Optional[Set[str]] = None,
        ticker: Optional[str] = None,
    ) -> SignalOfConfidenceSummarySchema:
        """Build T12M summary from the most recent 4 quarters that HAVE a cash-flow row.

        A quarter in ``missing_cash_flow_periods`` — or one whose point says
        ``cash_flow_reported=False`` (P19) — carries $0 because its cash is unknown, not
        because nothing was returned; summing it dropped a steady repurchaser's T12M by a
        quarter (4.0% "Very High" → 3.0% "High"). It is skipped.

        FEWER than four known quarters (an interior gap inside the newest four, a
        cash-flow history that starts late, a company listed under a year) are
        ANNUALISED — their sum x 4/N over the current cap — never summed as if they were a
        year: three steady $1B quarters on a $100B cap read 3.0% "High" for a 4.0% "Very
        High" repurchaser, one read 1.0% "Moderate" (fix pass, 2026-10-01). Since P19 such
        a build persists (a vendor hole sets no reason), so the estimate is logged under
        [soc-ttm-partial]. Today's cap, not each point's own: it is the denominator of the
        four-quarter sum too, and a point-in-time yield from two years back would carry the
        whole re-rating since.

        ``ttm_fallback_periods`` (labels whose yield is the single quarter x4) matters only
        on the no-current-cap fallback below, which reads a point's own yield.
        ``ticker`` only tags the log line.
        """

        if not data_points:
            return SignalOfConfidenceSummarySchema(
                total_yield=0.0,
                dividend_yield=0.0,
                buyback_yield=0.0,
                share_count_change=0.0,
                share_count_change_known=False,
                # No data points at all: 0 yield / 0 change classifies as "Low", which
                # is the honest reading of "we measured nothing".
                buyback_status=self._classify_buyback(0.0, 0.0),
            )

        missing = missing_cash_flow_periods or set()
        cash_points = [dp for dp in data_points if _cash_known(dp, missing)]
        # Last 4 measured quarters (or fewer if not enough data)
        last_4 = cash_points[-_TRAILING_POINTS:]

        # T12M dividend yield: sum of dollar amounts / market cap * 100
        # (amounts are already in millions, market cap is in raw dollars)
        total_div_amount = sum(dp.dividend_amount for dp in last_4)
        total_bb_amount = sum(dp.buyback_amount for dp in last_4)

        if not last_4:
            t12m_div_yield = t12m_bb_yield = 0.0
        elif current_market_cap and current_market_cap > 0:
            # Fewer than four known quarters are annualised (x 4/N), never read as a year.
            # `x * 4 / N` is exact for N == 4 (a power-of-two scale), so the full-window
            # answer is bit-for-bit unchanged.
            known = len(last_4)
            if known < _TRAILING_POINTS:
                logger.warning(
                    "[soc-ttm-partial] ticker=%s step=summary known=%d: only %d quarter(s) "
                    "with a cash-flow row in the trailing window (%s of %d displayed) — the "
                    "T12M is their sum x %d/%d over the current cap, an annualised estimate, "
                    "not a reported year",
                    ticker or "?", known, known,
                    ", ".join(dp.period for dp in last_4), len(data_points),
                    _TRAILING_POINTS, known,
                )
            annual_div_amount = total_div_amount * _TRAILING_POINTS / known
            annual_bb_amount = total_bb_amount * _TRAILING_POINTS / known
            # Convert millions back to raw for division
            t12m_div_yield = round(annual_div_amount * 1_000_000 / current_market_cap * 100, 2)
            t12m_bb_yield = round(annual_bb_amount * 1_000_000 / current_market_cap * 100, 2)
        elif last_4[-1].period not in (ttm_fallback_periods or set()):
            # Fallback: the newest measured point. Its yields are ALREADY a trailing twelve
            # months (see `_build_quarters`) — averaging four of them, as the x4 points once
            # needed, would smear the answer across seven quarters.
            t12m_div_yield = round(last_4[-1].dividend_yield, 2)
            t12m_bb_yield = round(last_4[-1].buyback_yield, 2)
        else:
            # …unless the newest point is itself the x4 fallback (no four consecutive
            # cash-flow quarters behind it): one Q4-heavy quarter x4 is not a year. The mean
            # of the last four measured points is the old x4-era estimate, smoother than
            # any single one of them (round-2 R48).
            t12m_div_yield = round(sum(dp.dividend_yield for dp in last_4) / len(last_4), 2)
            t12m_bb_yield = round(sum(dp.buyback_yield for dp in last_4) / len(last_4), 2)

        total_yield = round(t12m_div_yield + t12m_bb_yield, 2)

        # Share count change: oldest → newest across all data points.
        # Pick the oldest and newest points that actually REPORT a share count.
        # Anchoring on `data_points[0]`/`[-1]` regardless meant one unreported
        # quarter at either end produced a ±100% change out of nothing. With fewer than
        # two reported counts the change is UNKNOWN: 0.0 stays on the wire (a
        # non-Optional Double on shipped iOS) and `share_count_change_known` says so.
        measured = [
            dp for dp in data_points
            if dp.shares_outstanding is not None and dp.shares_outstanding > 0
        ]
        share_count_change_known = len(measured) >= 2
        if share_count_change_known:
            oldest_shares = measured[0].shares_outstanding
            newest_shares = measured[-1].shares_outstanding
            share_count_change = round(
                (newest_shares - oldest_shares) / oldest_shares * 100, 2
            )
        else:
            share_count_change = 0.0

        return SignalOfConfidenceSummarySchema(
            total_yield=total_yield,
            dividend_yield=t12m_div_yield,
            buyback_yield=t12m_bb_yield,
            share_count_change=share_count_change,
            share_count_change_known=share_count_change_known,
            buyback_status=self._classify_buyback(t12m_bb_yield, share_count_change),
        )

    # ── Buyback status ────────────────────────────────────────────

    @staticmethod
    def _classify_buyback(
        t12m_buyback_yield: float, share_count_change: float
    ) -> str:
        """Buyback verdict from share-count change + buyback yield.

        Lifted out of `_build_dividend_info` because it never depended on dividends
        in the first place. That function returns None the moment `dividend_history`
        is empty — which is every non-payer, including AMZN, BRK-B and NFLX, three of
        the largest repurchasers on the market — so their buyback verdict was computed
        and then thrown away. The report's fallback then asserted a flat "Low".
        """
        if share_count_change > 2.0:
            return "Diluting"
        if share_count_change > 0:
            return "Diluting (Mild)"
        if t12m_buyback_yield < 1.0:
            return "Low"
        if t12m_buyback_yield < 2.0:
            return "Moderate"
        if t12m_buyback_yield < 4.0:
            return "High"
        return "Very High"

    # ── Dividend info ─────────────────────────────────────────────

    @staticmethod
    def _annual_dividend_map(rows: Any) -> Dict[str, float]:
        """``{fiscal_year: dividendPerShare}`` from `ratios` (period=annual).

        Keyed by `annual_fiscal_year` — FMP ``fiscalYear`` first, the same field
        `_build_quarters` reads a quarter's year from (else the year of ``date`` - 7 days,
        so a 52/53-week year closing 2026-01-03 is 2025). It was keyed by the year of the
        fiscal-year-END date, so for a company that names its year by the START (Home
        Depot's FY ending 2026-02-01 is fiscal 2025) every quarter looked up its
        neighbouring year: the card read "FY2026" beside "Q4 '25" bars, an initiator's
        first paying year was zeroed, and a suspender's zero year charted again. Two rows
        mapping to one year keep the later-dated row, not whichever came last.
        """
        by_year: Dict[str, float] = {}
        dated: Dict[str, str] = {}
        if not isinstance(rows, list):
            return by_year
        for row in rows:
            if not isinstance(row, dict):
                continue
            date = str(row.get("date") or "")[:10]
            year = annual_fiscal_year(row)
            if len(year) != 4 or not year.isdigit():
                continue
            value = _safe_float(row, "dividendPerShare")
            if value is None or value < 0:
                continue
            if year in by_year and date <= dated[year]:
                continue
            by_year[year] = value
            dated[year] = date
        return by_year

    @staticmethod
    def _initiation_observed(rows: Any, series: List[AnnualDividendSchema]) -> bool:
        """True when the series' first paying year is one we watched the company START.

        The discriminator for a PARTIAL first year. `dividendPerShare` is a full-calendar
        -year total, so a company that initiates in Q2 or Q4 books a fraction of its
        run-rate in that year — and `_build_annual_dividends` trims the leading zeros, so
        that stub becomes `series[0]` and any growth measured from it is inflated.

        Requires an OBSERVED zero in the immediately preceding year, not a guess from the
        shape of the numbers. A mature payer whose window merely begins mid-stream has no
        such zero and is left alone; inferring "partial" from a large year-two rise would
        discard genuine raises.
        """
        if not series:
            return False
        by_year = SignalOfConfidenceService._annual_dividend_map(rows)
        try:
            prior = str(int(series[0].year) - 1)
        except (TypeError, ValueError):
            return False
        return by_year.get(prior) == 0.0

    @staticmethod
    def _build_annual_dividends(rows: Any) -> List[AnnualDividendSchema]:
        """Dividends per share by completed fiscal year, oldest first.

        Source is `ratios` (period=annual) — entitled, and verified exact against declared
        totals (KO 2024 = 1.9399 against a declared $1.94; 2025 = 2.0402 against $2.04).

        **Leading zeros are trimmed, interior and trailing zeros are kept.** The two look
        identical in the raw feed and mean opposite things: META and GOOGL read
        `0, 0, 0, 0, 2.0016, 2.1119` because they did not pay before 2024, while Intel
        reads `1.4598, 0.7370, 0.3736, 0.0000` because it wound its dividend down and
        suspended it. Rendering META's four $0.00 years would be noise; dropping Intel's
        would delete the most important fact in the series. A company that has never paid
        trims to nothing at all, which is how a non-payer ends up with no series rather
        than a flat line at zero.
        """
        by_year = SignalOfConfidenceService._annual_dividend_map(rows)
        series = [
            AnnualDividendSchema(year=y, per_share=round(by_year[y], 4))
            for y in sorted(by_year)
        ]
        first_paid = next((i for i, p in enumerate(series) if p.per_share > 0), None)
        return [] if first_paid is None else series[first_paid:]

    @staticmethod
    def _dividend_growth(
        series: List[AnnualDividendSchema],
        first_year_partial: bool = False,
    ) -> Tuple[Optional[float], Optional[int]]:
        """Total growth across the series, or ``(None, None)`` when it is undefined.

        Undefined is not zero. The series always starts at the first paying year (see
        above), so a company that began paying inside the window has exactly ONE point and
        no rate — "+infinity%" is not a fact about GOOGL. A company that cut to nothing
        does have one, and it is -100%, which is the number a reader most needs to see.

        ⚠️ `first_year_partial` drops that first year, and it matters more than it looks.
        `dividendPerShare` is a full-CALENDAR-year total, so an initiation part-way
        through the year books a fraction of the run-rate. Measured live: GOOGL 2024 =
        0.60 (three $0.20 payments) against 2025 = 0.83 (0.20 + three 0.21) rendered
        "Dividend Growth +38.3% over 1y" in the gain colour, for a per-quarter dividend
        that went 0.20 -> 0.21. Worse for a Q4 initiation that is never raised again:
        0.25 then 1.00 five years running reads "+300.0% over 5y" for a FLAT dividend.
        Same class as the `0 -> N` case already handled by trimming; this one was missed
        because a stub year is non-zero. Dropping it can leave fewer than two full years,
        and then undefined is the honest answer — exactly as it already is above.
        """
        if first_year_partial:
            series = series[1:]
        if len(series) < 2:
            return None, None
        first, last = series[0].per_share, series[-1].per_share
        if first <= 0:
            return None, None
        years = int(series[-1].year) - int(series[0].year)
        if years <= 0:
            return None, None
        return round((last / first - 1.0) * 100.0, 1), years

    @staticmethod
    def _verdict_points(
        data_points: Optional[List[Any]],
        missing_cash_flow_periods: Optional[Set[str]] = None,
        ttm_fallback_periods: Optional[Set[str]] = None,
    ) -> Tuple[Optional[Any], List[Any]]:
        """``(newest, baseline)`` — the two sides of the relative dividend verdict.

        ``newest`` is the newest point whose cash is KNOWN (a quarter with no cash-flow row
        — in ``missing_cash_flow_periods`` or flagged ``cash_flow_reported=False`` — is
        skipped); ``baseline`` the known points at least `_TRAILING_POINTS` older than
        it, so their trailing windows never overlap the newest one — minus any x4-fallback
        point (round-2 R48): a lumpy single quarter x4 is not a trailing twelve months, and
        an annual payer's x4 Q2 (4x its yield) and Q3 (0%) dragged a flat payer's baseline
        to half its real yield ("Very High" for a dividend that never moved). Whether
        ``newest`` is itself a fallback is the CALLER's check. Shared by
        `_build_dividend_info` and the builder's spin-off gate, so the lookup is spent
        exactly when the relative verdict can run.
        """
        missing = missing_cash_flow_periods or set()
        fallback = ttm_fallback_periods or set()
        points = [dp for dp in (data_points or []) if _cash_known(dp, missing)]
        if not points:
            return None, []
        baseline = [
            dp for dp in points[:-_TRAILING_POINTS]
            if getattr(dp, "period", None) not in fallback
        ]
        return points[-1], baseline

    def _build_dividend_info(
        self,
        dividend_history: List[Dict[str, Any]],
        t12m_dividend_yield: float,
        t12m_buyback_yield: float,
        share_count_change: float = 0.0,
        data_points: Optional[List] = None,
        annual_ratios: Optional[List[Dict[str, Any]]] = None,
        ex_dividend_dates: Optional[List[str]] = None,
        pays_common_dividend: Optional[bool] = None,
        missing_cash_flow_periods: Optional[Set[str]] = None,
        spinoff_in_window: bool = False,
        ttm_fallback_periods: Optional[Set[str]] = None,
        ticker: str = "",
    ) -> Optional[DividendInfoSchema]:
        """Build DividendInfo for a company that actually pays a dividend.

        ``missing_cash_flow_periods`` (labels whose cash is unknown), and any point flagged
        ``cash_flow_reported=False`` (P19), are left out of every average below. ``ttm_fallback_periods`` (labels whose yield is the single quarter
        x4) are left out of the verdict's baseline, and a fallback NEWEST point refuses the
        relative verdict (round-2 R48). ``spinoff_in_window`` refuses it too: before a spin-off
        FMP's historical cap is back-computed from the spin-adjusted prices, so it is too
        small by the spin factor and every older yield is inflated by it — a payer whose
        real yield never moved read "Low" (measured: a factor-2 spin, 4,4,4,4,4,2,2,2).

        ⚠️ THE GATE IS NOT `dividend_history`. It used to be, and that turned the whole
        card off for EVERY ticker on 2026-09-03: FMP's `/dividends` went outside the signed
        Order Form and answers 402, so `dividend_history` is now permanently `[]` — while
        every number this card renders is still perfectly available. `five_year_avg_yield`
        and `status` come from `data_points[].dividend_yield`, which
        `_build_data_points` computes from cash-flow `dividendsPaid` over historical market
        cap, and has never touched `/dividends` at all.

        So the gate is "does this company pay a dividend", answered by the yield. The one
        genuine loss is the per-payment metadata: the ex-dividend and payment dates below
        degrade to None, and the iOS card already renders "N/A" for a nil date rather than
        inventing one.
        """
        annual = self._build_annual_dividends(annual_ratios)
        # The verdict is computed ONCE in `_build_signal_of_confidence` and shared with
        # `_build_data_points`; callers that skip the builder (tests, older call sites)
        # get the same answer derived here from the record alone.
        if pays_common_dividend is None:
            pays_common_dividend = self._pays_common_dividend(annual_ratios)
        if pays_common_dividend is True:
            pays_dividend = True
        elif pays_common_dividend is False:
            # We HAVE the authoritative per-share record and it says the company does not
            # pay NOW. Trust it over the cash-flow yield, which is not the same question:
            # it is `dividendsPaid / market cap`, and that line picks up preferred and
            # one-off distributions. Measured — TSLA, which has never paid a common
            # dividend, shows a 0.01% trailing yield from a single quarter and used to
            # render a whole dividend card of em dashes on the strength of it.
            #
            # Two different "no": a company that has NEVER paid gets no card; a company
            # that STOPPED (INTC: 1.46 → 0.74 → 0.37 → 0 across FY2022-25) keeps it, because
            # the suspension IS the story — "Dividend / Share $0.00, −100% over 5y". The
            # bars are zeroed either way (`_build_data_points`); only the card's history
            # survives. Pinned by tests/test_annual_dividends.py::test_a_suspended_payer_keeps_its_card.
            pays_dividend = any(a.per_share > 0 for a in annual)
        else:
            # No series at all (the `ratios` fetch failed). Fall back to the yield rather
            # than hiding a real payer's card because one upstream call went down.
            pays_dividend = (
                t12m_dividend_yield > 0
                or any(getattr(dp, "dividend_yield", 0) > 0 for dp in (data_points or []))
            )
        if not dividend_history and not pays_dividend:
            return None
        growth_pct, growth_years = self._dividend_growth(
            annual, self._initiation_observed(annual_ratios, annual)
        )

        # Sort descending by date to find most recent
        sorted_divs = sorted(
            dividend_history,
            key=lambda d: d.get("date") or "",
            reverse=True,
        )

        # Most recent dividend entry
        latest = sorted_divs[0] if sorted_divs else {}
        ex_date = (latest.get("date") or "")[:10] or None
        # Fall back to the DERIVED dates (newest first). Only the date is recoverable this
        # way — the step size implies an amount to ~1%, which is not good enough to print
        # as money, so `payment_date` below stays absent and the amounts come from
        # `annual_ratios`.
        if not ex_date and ex_dividend_dates:
            ex_date = str(ex_dividend_dates[0])[:10] or None
        payment_date = (latest.get("paymentDate") or latest.get("payment_date") or "")[:10] or None

        # Historical average DIVIDEND yield from the quarterly data points.
        #
        # This value is compared against the T12M DIVIDEND yield below, so it
        # must be dividend-only. It previously summed `dividend_yield +
        # buyback_yield` and was then divided into a dividend-only numerator —
        # any large repurchaser got a systematically depressed ratio and was
        # mislabelled "Low". (Verified: 0.5% dividends + 3.5% buybacks every
        # quarter -> avg 4.0 -> ratio 0.125 -> "Low", for a company yielding
        # exactly its own average.)
        #
        # NOTE the window is the available data points (<= 8 quarters, see
        # _build_quarters), NOT five years — the schema field is named
        # `five_year_avg_yield` for backward compatibility with the shipped iOS
        # DTO, but it is a trailing average over whatever history we hold, and
        # `avg_yield_window` now says how much ("8Q"), so the card stops claiming "5Y".
        missing = missing_cash_flow_periods or set()
        points = [dp for dp in (data_points or []) if _cash_known(dp, missing)]
        five_year_avg_yield = 0.0
        avg_yield_window: Optional[str] = None
        if len(points) >= 4:
            dividend_yields = [dp.dividend_yield for dp in points]
            five_year_avg_yield = round(
                sum(dividend_yields) / len(dividend_yields), 2
            )
            avg_yield_window = f"{len(points)}Q"
        else:
            # Fallback: use dividend history only
            yearly_yields: dict[str, float] = defaultdict(float)
            for d in sorted_divs:
                y = _safe_float(d, "yield")
                date_str = (d.get("date") or "")[:4]
                if y is not None and y > 0 and date_str:
                    yearly_yields[date_str] += y
            sorted_years = sorted(yearly_yields.keys(), reverse=True)
            annual_values = [yearly_yields[yr] for yr in sorted_years if yearly_yields[yr] > 0]
            annual_values = annual_values[:5]
            five_year_avg_yield = round(
                sum(annual_values) / len(annual_values), 2
            ) if annual_values else 0.0
            avg_yield_window = f"{len(annual_values)}Y" if annual_values else None
        if five_year_avg_yield <= 0:
            # No average to label (iOS renders the 0.0 as "—").
            avg_yield_window = None

        # Dividend yield status: compare the trailing yield to its own history.
        #
        # ⚠️ BOTH SIDES MUST USE THE SAME DENOMINATOR, and they did not. `t12m_dividend_yield`
        # (the summary) divides the last four quarters' dividends by the **current** market
        # cap, while `five_year_avg_yield` averages per-quarter yields each divided by that
        # quarter's **point-in-time** cap. So a stock that merely re-rated upward scored low
        # with no change whatsoever in its payout.
        #
        # Measured across 8 mega-caps, 4 changed verdict once the bases matched:
        #   JNJ  Low -> Fair   (a dividend king reported as "Low")
        #   MSFT Fair -> High     KO Fair -> High     WMT High -> Fair
        #
        # Same class as the bug the `five_year_avg_yield` comment above already fixed once
        # (a div+buyback denominator under a dividend-only numerator) — one layer deeper.
        # The verdict also reaches the 20-credit report via `capital_allocation.dividend_status`.
        #
        # `summary.dividend_yield` itself is untouched: it is a genuine current-cap yield and
        # is rendered as such elsewhere. Only the COMPARISON is put on a consistent footing.
        #
        # ⚠️ AND THE BASELINE MUST EXCLUDE THE NUMERATOR. Putting the trailing window on a
        # point-in-time basis fixed the units but made the ratio SELF-REFERENTIAL, because
        # `five_year_avg_yield` averages ALL points — the same four among them. Measured on
        # the intermediate version:
        #   4 points (all we hold for a recent initiator) -> ratio is IDENTICALLY 1.0,
        #     so a 0.05% token yield published as green "High";
        #   8 flat points -> ratio exactly 1.0, the first value of the "High" bucket, and a
        #     0.3% wiggle flipped the verdict Fair <-> High;
        #   8 points across a 40% dividend CUT -> still "Fair", because ratio = 2B/(A+B)
        #     compresses everything toward 1.0 and puts the ladder's ends out of reach.
        # So the baseline is the OLDER points only. `five_year_avg_yield` keeps its meaning
        # (the published trailing average over everything we hold) — only the comparison
        # denominator changes.
        #
        # Below `_MIN_BASELINE_POINTS` older quarters there is no independent history to
        # compare against, so the ratio is refused outright and the absolute ladder runs.
        # A fabricated verdict from a degenerate ratio is worse than an absolute one.
        #
        # The points are TRAILING-TWELVE-MONTH yields now (`_build_quarters`), so the
        # trailing side is the NEWEST point alone — averaging the last four would smear
        # seven quarters together — and the baseline is the points at least four quarters
        # older (`points[:-4]`), whose windows never overlap the newest one.
        #
        # x4-FALLBACK points (no four consecutive cash-flow quarters behind them) are not on
        # that basis: they are left out of the baseline (`_verdict_points`, then
        # `_MIN_BASELINE_POINTS` is re-checked), and a fallback NEWEST point refuses the
        # ratio outright — one Q4-heavy quarter x4 read "Very High", a Q1-light one "Low".
        fallback = ttm_fallback_periods or set()
        newest_point, baseline_points = self._verdict_points(
            data_points, missing, fallback
        )
        comparable_t12m = (
            round(newest_point.dividend_yield, 2)
            if newest_point is not None
            else t12m_dividend_yield
        )
        baseline_yield = (
            round(sum(dp.dividend_yield for dp in baseline_points) / len(baseline_points), 2)
            if len(baseline_points) >= _MIN_BASELINE_POINTS
            else 0.0
        )
        if (
            newest_point is not None
            and getattr(newest_point, "period", None) in fallback
            and baseline_yield > 0
        ):
            logger.warning(
                "[soc-ttm-fallback-verdict] ticker=%s: the newest point %s is the single "
                "quarter x4 (no four consecutive cash-flow quarters behind it) — the "
                "relative verdict (%.2f%% vs baseline %.2f%%) is refused and the absolute "
                "ladder runs on the T12M %.2f%%",
                ticker or "?", getattr(newest_point, "period", "?"), comparable_t12m,
                baseline_yield, t12m_dividend_yield,
            )
            baseline_yield = 0.0
        if spinoff_in_window and baseline_yield > 0:
            logger.warning(
                "[soc-spinoff-baseline] ticker=%s: an unclassified adjustment (spin-off) "
                "falls inside the window — the pre-spin historical caps are too small, so "
                "the relative verdict (newest %.2f%% vs baseline %.2f%%) is refused and the "
                "absolute ladder runs on the current-cap T12M %.2f%%",
                ticker or "?", comparable_t12m, baseline_yield, t12m_dividend_yield,
            )
            baseline_yield = 0.0

        if baseline_yield > 0:
            ratio = comparable_t12m / baseline_yield
            if _ABOUT_AVERAGE[0] <= ratio < _ABOUT_AVERAGE[1]:
                # "Yielding what it always has" is FAIR, not green.
                #
                # The bare `>= 1.0 -> High` cut split hairs it cannot actually measure:
                # T at 0.993 and VZ at 1.011 are 1.8% apart in trailing yield and were
                # rendered in different colours, one of them as a positive signal. The
                # basis fix above makes that boundary far more crowded than it used to
                # be — matched denominators put a stable payer very close to 1.0 by
                # construction, where the old mismatched ones scattered.
                #
                # Deliberately a NARROW band rather than a re-centred ladder. Measured
                # over 20 real payers, re-centring to 0.85/1.15 moves 10 of them and
                # drops JNJ (0.740) and CSCO (0.727) into "Low" — reintroducing exactly
                # the dividend-king-reads-Low defect this whole section was fixing. The
                # band moves one (VZ), which is the only genuine anomaly in the sample.
                status = "Fair"
            elif ratio < 0.7:
                status = "Low"
            elif ratio < 1.0:
                status = "Fair"
            elif ratio < 1.5:
                status = "High"
            else:
                status = "Very High"
        else:
            # No historical average — classify based on absolute yield
            if t12m_dividend_yield < 1.0:
                status = "Low"
            elif t12m_dividend_yield < 2.0:
                status = "Fair"
            elif t12m_dividend_yield < 4.0:
                status = "High"
            else:
                status = "Very High"

        # Same verdict the summary carries — kept on DividendInfo too so the existing
        # iOS DividendInfoCard row is unchanged for dividend payers.
        buyback_status = self._classify_buyback(t12m_buyback_yield, share_count_change)

        return DividendInfoSchema(
            ex_dividend_date=ex_date,
            payment_date=payment_date,
            five_year_avg_yield=five_year_avg_yield,
            status=status,
            buyback_status=buyback_status,
            annual_dividends=annual,
            dividend_per_share=annual[-1].per_share if annual else None,
            dividend_per_share_year=annual[-1].year if annual else None,
            dividend_growth_pct=growth_pct,
            dividend_growth_years=growth_years,
            avg_yield_window=avg_yield_window,
        )


# ── Singleton ─────────────────────────────────────────────────────

_signal_of_confidence_service: Optional[SignalOfConfidenceService] = None


def get_signal_of_confidence_service() -> SignalOfConfidenceService:
    global _signal_of_confidence_service
    if _signal_of_confidence_service is None:
        _signal_of_confidence_service = SignalOfConfidenceService()
    return _signal_of_confidence_service
