"""
Sector Benchmark Service — Pre-computes median financial metrics per GICS sector
from S&P 500 constituents and stores them in Supabase.

Runs as a daily background job. Any service (Growth, Profit Power, Health Check, etc.)
can then look up sector benchmarks via a fast DB query instead of fetching peer data
on every request.
"""

import asyncio
import contextlib
import contextvars
import email.utils
import logging
import math
import random
import statistics
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterator, List, Optional, Tuple

import httpx

from app.database import get_supabase
from app.integrations.fmp import (
    get_fmp_client,
    FMPAuthException,
    FMPClient,
    FMPNotEntitledException,
    FMPRateLimitException,
    FMPUnavailableException,
)
from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE
from app.utils.period_labels import (
    annual_benchmark_key,
    calendar_quarter_key,
    format_calendar_quarter,
    previous_calendar_quarter,
)
from app.utils.supabase_async import sb_exec

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

# Statement granularity the producer READS ("annual" / "quarterly", which also names the
# FMP data keys `income_quarterly` etc.) → the `period_type` its rows are STORED under.
# Quarterly rows are keyed by calendar quarter (`_quarterly_period_label`), so they get
# their own period_type and never mix with the legacy fiscal-keyed 'quarterly' rows.
STORED_PERIOD_TYPE: Dict[str, str] = {
    "annual": "annual",
    "quarterly": CALENDAR_QUARTER_PERIOD_TYPE,
}

# A calendar-quarter key is stored as "Q<n>'<yy>", and every parser of it (the producer's
# completeness gate, `period_labels.benchmark_period_end`, the readers' sort keys) reads the
# two-digit year as 20yy. A quarter that ended before 2000 ("Q4'93", from a long FMP history)
# would be filed — and served — as 2093. Such quarters are never keyed (`_by_calendar_quarter`).
MIN_CALENDAR_QUARTER_YEAR = 2000

# An ANNUAL YoY compares a company's fiscal year with the one before it, so the two period
# ends must be about a year apart: 52/53-week years are 364/371 days, calendar years 365/366.
# A fiscal-year-end change (a Sep year followed by a Dec year, 458 days) or a short transition
# period would otherwise be filed as one year's growth.
ANNUAL_YOY_MIN_GAP_DAYS = 300
ANNUAL_YOY_MAX_GAP_DAYS = 430
# The same floor names a SHORT annual row: one whose period end is fewer than this many days
# after the company's previous (distinct) annual period end — a fiscal-year-end change's
# transition stub (a 10-KT: Jun-2024 FY, then a Jul-Dec 2024 stub, then Dec-2025 FY). It
# never displaces a full year that shares its storage key, and no YoY is computed against
# or for it (`_by_annual_key`, `_compute_yoy_for_records`). FMP carries no period START, so
# the span is measured end to end; the oldest row (no previous end) and undated rows have no
# known span and are never called short.
ANNUAL_SHORT_PERIOD_DAYS = ANNUAL_YOY_MIN_GAP_DAYS

# ── FMP 429 back-off for the benchmark fetch path ────────────────────────────────────────
#
# The benchmark recompute is the app's heaviest FMP burst (~47k fiscal calls, ~11k TTM), and
# FMP answers a burst over its per-minute quota with 429. `FMPClient` retries only 5xx and
# network errors and raises `FMPRateLimitException` on the FIRST 429, and the fetch layers
# here turned that into an empty list — so a 429 storm silently dropped companies from every
# median it touched. A 429 is now retried a bounded number of times, honouring FMP's
# Retry-After when it sends one (it often does not), and what still fails is COUNTED per
# industry by the recompute (`industry_benchmark_service._FetchTally`) and reported in its
# summary. The back-off sleeps inside the caller's concurrency slot on purpose: a rate-limited
# burst should slow down, not keep the other slots firing.
#
# Two stages, then a breaker (2026-10-07, review round 3 P3-2):
#   1. BURST — RATE_LIMIT_MAX_RETRIES retries on a short exponential back-off (Retry-After
#      clamped to RATE_LIMIT_MAX_DELAY_SECONDS). A burst over the quota clears in seconds.
#   2. SHARED WINDOW — a 429 that outlived its burst retries (~14-17.5 s) means the MINUTE's
#      quota is spent, and on its own that budget is shorter than FMP's per-minute window: the
#      calls still inside the window used to fail together, and the companies they fetched
#      were dropped. Such a call now opens ONE shared window instead (or joins the one already
#      open): every caller — a new one too, before it spends a request FMP would refuse —
#      waits until one shared instant RATE_LIMIT_WINDOW_SECONDS ahead (FMP's Retry-After
#      honoured up to that), then retries. One window per spent minute, not one per caller.
#      A call waits out at most RATE_LIMIT_LOCKOUT_WINDOWS windows itself, so none loops.
#   3. BREAKER — a quota LOCKOUT is not a per-minute window. When RATE_LIMIT_LOCKOUT_WINDOWS
#      windows in a row passed with no successful call in between AND at least
#      RATE_LIMIT_BREAKER_THRESHOLD calls ran out of their burst retries (so one stuck ticker
#      cannot open it for everyone), or the run has spent its RATE_LIMIT_RUN_WAIT_BUDGET_SECONDS
#      of window waits (`rate_limit_run`, logged at the end of the run), a 429 is no longer
#      retried at all until a call succeeds: each fails at once and is counted. Otherwise
#      retrying ~47k calls would stretch a run that should fail in minutes (and be retried by
#      the scheduler) past its claim's 3 h stale window.
# State is process-wide (the quota is the account's) and touched on the event loop only. A
# window or lockout counts only while `_exhausted_in_a_row` > 0: a successful call zeroes it
# and closes both.
RATE_LIMIT_MAX_RETRIES = 3            # 4 attempts in all before the shared window
RATE_LIMIT_BASE_DELAY_SECONDS = 2.0   # 2 s, 4 s, 8 s (+ up to 25% jitter) without Retry-After
RATE_LIMIT_MAX_DELAY_SECONDS = 30.0   # a longer Retry-After (or a hostile one) is clamped
RATE_LIMIT_WINDOW_SECONDS = 60.0      # FMP's quota is per minute: one window covers it
RATE_LIMIT_LOCKOUT_WINDOWS = 3        # windows in a row with no success (and ...
RATE_LIMIT_BREAKER_THRESHOLD = 20     # ... this many calls past their retries) = a lockout
RATE_LIMIT_RUN_WAIT_BUDGET_SECONDS = 20 * 60.0   # window waits one run may spend (claim: 3 h)
# Calls since the last successful call whose 429 outlived their burst retries.
_exhausted_in_a_row = 0
# The clock the shared window is measured on (a test substitutes a fake one).
_rate_limit_clock: Callable[[], float] = time.monotonic


class _SharedWindow:
    """The one shared 429 window (process-wide; see the stages above)."""

    def __init__(self) -> None:
        self.seq = 0            # id of the newest window
        self.retry_at = 0.0     # the `_rate_limit_clock()` instant it ends
        self.in_a_row = 0       # windows opened since the last successful call
        self.lockout = False    # the breaker: a 429 fails at once until a call succeeds


_window = _SharedWindow()


class RateLimitRun:
    """One recompute run's window-wait budget and totals (`rate_limit_run`)."""

    def __init__(self, label: str, budget_seconds: Optional[float] = None) -> None:
        self.label = label
        self.budget_seconds = (
            RATE_LIMIT_RUN_WAIT_BUDGET_SECONDS if budget_seconds is None else budget_seconds
        )
        self.windows = 0          # shared windows this run opened
        self.wait_seconds = 0.0   # their total length (concurrent waiters overlap: wall time)
        self.lockouts = 0         # times the breaker opened during this run

    def summary(self) -> Dict[str, Any]:
        return {
            "rate_limit_windows": self.windows,
            "rate_limit_wait_seconds": round(self.wait_seconds, 1),
            "rate_limit_lockouts": self.lockouts,
        }


# A ContextVar, not a global: the quarterly and the weekly sweeps can overlap on a quarter-
# start Sunday, and each keeps its own budget (`asyncio.gather` tasks inherit the context).
_RATE_LIMIT_RUN: contextvars.ContextVar[Optional[RateLimitRun]] = contextvars.ContextVar(
    "benchmark_rate_limit_run", default=None,
)


@contextlib.contextmanager
def rate_limit_run(label: str) -> Iterator[RateLimitRun]:
    """Scope one recompute run: its shared-window waits are charged to (and bounded by) one
    RATE_LIMIT_RUN_WAIT_BUDGET_SECONDS budget, and the run's waits are logged when it ends.
    A call outside any run has no run budget (its own window cap and the breaker still bound
    it)."""
    run = RateLimitRun(label)
    token = _RATE_LIMIT_RUN.set(run)
    try:
        yield run
    finally:
        _RATE_LIMIT_RUN.reset(token)
        if run.windows or run.lockouts:
            logger.warning(
                "benchmark fetch [%s run]: FMP's per-minute quota ran out — %d shared 429 "
                "window(s), %.0f s of waiting (budget %.0f s), the breaker opened %d time(s)",
                run.label, run.windows, run.wait_seconds, run.budget_seconds, run.lockouts,
            )


def current_rate_limit_run() -> Optional[RateLimitRun]:
    """The `rate_limit_run` the caller is inside, or None."""
    return _RATE_LIMIT_RUN.get()


# Key of the per-company fetch-failure list inside a `_fetch_company_data` dict: the failure
# kind (`classify_fetch_failure`) of every call that raised after its retries, or answered
# something other than a list. Every reader of that dict looks up its own statement keys, so
# this extra key is invisible to the medians.
FETCH_ERRORS_KEY = "_fetch_errors"
# The same failures by statement key ({"income_annual": "unavailable", ...}), so the recompute
# can tell a company that lost its core statements from one that lost a side call
# (`industry_benchmark_service._FISCAL_LINE_CALLS`). Not a list, so no reader takes it for one.
FETCH_FAILED_CALLS_KEY = "_fetch_failed_calls"

# Every kind `classify_fetch_failure` returns, and the TRANSIENT ones: those a same-day retry
# can change. Only a transient failure counts toward the recompute's INCOMPLETE line
# (`industry_benchmark_service._FetchTally.sector_loss`; review round 5, P4-2): a refusal FMP
# repeats on every attempt (a 4xx, a 401, a 402, a 200 whose body is not a statement list)
# used to hold the run — and the sector's aggregate — unsettled for ever, three retries a day.
FETCH_FAILURE_KINDS: Tuple[str, ...] = ("rate_limited", "unavailable", "error", "refused")
TRANSIENT_FETCH_FAILURES = frozenset({"rate_limited", "unavailable"})


def _http_status(exc: BaseException) -> Optional[int]:
    """The HTTP status an `httpx.HTTPStatusError` carries, or None when unreadable."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def classify_fetch_failure(exc: BaseException) -> str:
    """The kind of one failed benchmark fetch call:

      * 'rate_limited' — a 429 that outlived `call_with_rate_limit_retry`. Transient.
      * 'unavailable'  — 5xx / network after the client's own retries
        (`FMPUnavailableException`), or a raw `httpx.HTTPStatusError` of 500 or above: a 5xx
        the client does not retry (it retries 500/502/503/504 only, so Cloudflare's 520-527
        reach here raw). Transient.
      * 'refused'      — FMP answered and will answer the same again: an `HTTPStatusError`
        4xx, a 401 (`FMPAuthException`), a 402 / an unlicensed path
        (`FMPNotEntitledException`). The fetch layers also file a 200 whose body is not a
        statement list here (`_fetch_company_data`, `industry_benchmark_service._fetch_ttm`).
      * 'error'        — anything else: a malformed or undecodable body, a bug.

    Only the transient kinds (`TRANSIENT_FETCH_FAILURES`) count toward the INCOMPLETE line;
    every kind is counted and reported."""
    if isinstance(exc, FMPRateLimitException):
        return "rate_limited"
    if isinstance(exc, FMPUnavailableException):
        return "unavailable"
    if isinstance(exc, (FMPAuthException, FMPNotEntitledException)):
        return "refused"
    if isinstance(exc, httpx.HTTPStatusError):
        status = _http_status(exc)
        if status is not None and status >= 500:
            return "unavailable"
        if status is not None and 400 <= status < 500:
            return "refused"
    return "error"


def _retry_after_seconds(
    value: Any, ceiling: float = RATE_LIMIT_MAX_DELAY_SECONDS,
) -> Optional[float]:
    """Seconds to wait from a Retry-After header value (delta-seconds or an HTTP date),
    clamped to [0, ceiling] (a burst retry: RATE_LIMIT_MAX_DELAY_SECONDS; a shared window:
    RATE_LIMIT_WINDOW_SECONDS); None when absent or unreadable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), ceiling)


def _window_length(exc: BaseException) -> float:
    """How long a new shared window lasts: FMP's Retry-After up to RATE_LIMIT_WINDOW_SECONDS
    (never below one base delay, so a 'Retry-After: 0' cannot burn the window budget in a
    tight loop), else a whole RATE_LIMIT_WINDOW_SECONDS."""
    after = _retry_after_seconds(getattr(exc, "retry_after", None), RATE_LIMIT_WINDOW_SECONDS)
    if after is None:
        return RATE_LIMIT_WINDOW_SECONDS
    return max(after, RATE_LIMIT_BASE_DELAY_SECONDS)


def _pending_window() -> Optional[Tuple[int, float]]:
    """(id, end instant) of the shared window while it is open, else None. Never during a
    lockout (a 429 then fails at once), and only while a call is exhausted since the last
    success (a success closes the window)."""
    if _exhausted_in_a_row <= 0 or _window.lockout:
        return None
    if _rate_limit_clock() < _window.retry_at:
        return _window.seq, _window.retry_at
    return None


def _open_breaker(why: str) -> None:
    """The lockout: every 429 now fails at once (counted) until a call succeeds."""
    if _window.lockout:
        return
    _window.lockout = True
    run = current_rate_limit_run()
    if run is not None:
        run.lockouts += 1
    # WARNING for the first few per run, DEBUG after: a quota that keeps relapsing would
    # otherwise log once per recovery. The run's end line carries the count.
    level = logging.WARNING if run is None or run.lockouts <= 3 else logging.DEBUG
    logger.log(
        level,
        "benchmark fetch%s: %s — a quota lockout, not a per-minute window: 429s are no longer "
        "retried until a call succeeds (each fails at once and is counted)",
        f" [{run.label} run]" if run is not None else "", why,
    )


def _lockout_reached() -> bool:
    """Open the breaker (and say True) once RATE_LIMIT_LOCKOUT_WINDOWS windows in a row passed
    with no success and RATE_LIMIT_BREAKER_THRESHOLD calls ran out of their burst retries.
    Asked by a call that needs a new window AND by one that gave up after its own windows —
    when every call in flight gives up together, nobody asks for a new window, and the next
    caller would otherwise spend a whole burst before failing."""
    if _window.lockout:
        return True
    if (
        _window.in_a_row >= RATE_LIMIT_LOCKOUT_WINDOWS
        and _exhausted_in_a_row >= RATE_LIMIT_BREAKER_THRESHOLD
    ):
        _open_breaker(
            f"{_window.in_a_row} shared windows in a row passed with no successful call, and "
            f"{_exhausted_in_a_row} calls were rate-limited past their retries"
        )
        return True
    return False


def _join_or_open_window(
    exc: BaseException, waited: Optional[int],
) -> Optional[Tuple[int, float]]:
    """For a call whose 429 outlived its burst retries (or came back after a window): the
    shared window to wait out before retrying — the open one this call has not waited out
    yet, else a new one — or None when the breaker is open (give up)."""
    now = _rate_limit_clock()
    if _window.seq != waited and now < _window.retry_at:
        return _window.seq, _window.retry_at   # still open, not waited out yet: join it
    if _lockout_reached():
        return None
    length = _window_length(exc)
    run = current_rate_limit_run()
    if run is not None and run.wait_seconds + length > run.budget_seconds:
        _open_breaker(
            f"this run has spent its {run.budget_seconds:.0f} s window-wait budget "
            f"({run.windows} windows)"
        )
        return None
    _window.seq += 1
    _window.in_a_row += 1
    _window.retry_at = now + length
    if run is not None:
        run.windows += 1
        run.wait_seconds += length
    logger.warning(
        "benchmark fetch%s: FMP still answers 429 after %d retries — the minute's quota is "
        "spent; every call waits for one shared window of %.0f s (Retry-After %s) and then "
        "retries. Window %d in a row%s",
        f" [{run.label} run]" if run is not None else "", RATE_LIMIT_MAX_RETRIES, length,
        getattr(exc, "retry_after", None) or "not sent", _window.in_a_row,
        "" if run is None else
        f"; {run.wait_seconds:.0f} of this run's {run.budget_seconds:.0f} s wait budget used",
    )
    return _window.seq, _window.retry_at


async def _wait_out(window: Tuple[int, float]) -> int:
    """Sleep until the shared window ends; returns its id. Waking at its end closes it (a
    no-op on a real clock; it keeps a stopped test clock from parking every later caller)."""
    seq, retry_at = window
    await asyncio.sleep(max(0.0, retry_at - _rate_limit_clock()))
    if _window.seq == seq:
        _window.retry_at = min(_window.retry_at, _rate_limit_clock())
    return seq


def _reset_window() -> None:
    """No window open, none in a row, no lockout."""
    _window.in_a_row = 0
    _window.lockout = False
    _window.retry_at = min(_window.retry_at, _rate_limit_clock())


def _rate_limit_succeeded() -> None:
    """A call answered: the quota is back — close the window and the breaker."""
    global _exhausted_in_a_row
    if _exhausted_in_a_row and (_window.in_a_row or _window.lockout):
        logger.info(
            "benchmark fetch: FMP answers again after %d shared window(s)%s — 429s are "
            "retried again", _window.in_a_row, " and a lockout" if _window.lockout else "",
        )
    _exhausted_in_a_row = 0
    _reset_window()


async def call_with_rate_limit_retry(
    fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any,
) -> Any:
    """Await `fn(*args, **kwargs)`, retrying an `FMPRateLimitException` through the stages
    above: RATE_LIMIT_MAX_RETRIES burst retries, then the shared window (at most
    RATE_LIMIT_LOCKOUT_WINDOWS of them for this call); none while the breaker is open. The
    last 429 is re-raised for the caller to count; every other exception propagates on the
    first attempt (5xx / network were already retried by `FMPClient`, and a 4xx never
    succeeds on retry)."""
    global _exhausted_in_a_row
    attempt = 0                    # burst retries used
    waited: Optional[int] = None   # id of the last shared window this call waited out
    windows_waited = 0
    counted = False                # this call is in `_exhausted_in_a_row`
    while True:
        # A window is open (another caller's 429 outlived its burst): wait it out BEFORE
        # spending a request FMP would refuse.
        pending = _pending_window()
        if pending is not None and pending[0] != waited:
            waited = await _wait_out(pending)
            windows_waited += 1
        try:
            result = await fn(*args, **kwargs)
        except FMPRateLimitException as exc:
            if _exhausted_in_a_row > 0 and _window.lockout:
                if not counted:
                    _exhausted_in_a_row += 1
                raise
            if waited is None and attempt < RATE_LIMIT_MAX_RETRIES:
                delay = _retry_after_seconds(getattr(exc, "retry_after", None))
                if delay is None:
                    base = RATE_LIMIT_BASE_DELAY_SECONDS * (2 ** attempt)
                    delay = min(
                        base + random.uniform(0.0, base * 0.25), RATE_LIMIT_MAX_DELAY_SECONDS,
                    )
                attempt += 1
                # FMPClient already logged this 429 at WARNING; one DEBUG line per retry.
                logger.debug(
                    "benchmark fetch %s%s: FMP 429 — retry %d/%d in %.1fs",
                    getattr(fn, "__name__", "call"), args[:1], attempt,
                    RATE_LIMIT_MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
                continue
            if not counted:
                counted = True
                if _exhausted_in_a_row <= 0:
                    _reset_window()   # the first since a success: no window, no lockout yet
                _exhausted_in_a_row += 1
            if windows_waited >= RATE_LIMIT_LOCKOUT_WINDOWS:
                _lockout_reached()   # opens the breaker when the quota is out for everyone
                raise    # this call has waited out its share of windows
            window = _join_or_open_window(exc, waited)
            if window is None:
                raise
            # Waited HERE, not left to the loop's top: every pass after the burst waits out
            # one window, so `windows_waited` bounds the loop by construction.
            waited = await _wait_out(window)
            windows_waited += 1
            continue
        _rate_limit_succeeded()
        return result


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

# 🔴 NOT a ticker source any more — see `_get_sector_tickers`.
#
# This table's ONLY remaining job is to define the 11 canonical sector NAMES
# (`CANONICAL_SECTORS`, below), which the whole lookup layer keys on. It must not be
# deleted, and it must never again be used to compute a median: `sp500-constituent` is a
# BLOCKED path under the current FMP entitlement, so "the endpoint is unavailable" went
# from a rare degradation to the permanent state, and this fallback went from a safety net
# to the thing that silently overwrites good data.
#
# 5 tickers x 11 sectors = 55, against `MIN_SAMPLE_SIZE = 5` — so every one of these
# sectors clears the sample gate at EXACTLY the boundary and upserts a 5-company median
# over the ~5,700-company values that `industry_benchmark_service` writes from
# `benchmark_universe.json`. Thirteen services read that table, including moat scoring,
# health check, valuation/growth snapshots and the AI report collector.
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
    # Interest coverage: positive-only. Most software / no-debt firms report 0/null,
    # collapsing a naive MEDIAN to 0.0 (the degenerate bug). Dropping ≤0 (no-debt +
    # loss-makers) leaves the indebted, profitable peers → a realistic high-teens/~30x
    # median (matches Damodaran/CSIMarket aggregates). Capped so a tiny-interest
    # outlier can't run to thousands.
    {"name": "interest_coverage",   "source": "ratios",    "field": "interestCoverageRatio",  "type": "direct", "positive_only": True, "cap": 100.0},
    {"name": "debt_to_assets",      "source": "ratios",    "field": "debtRatio",              "type": "direct"},
    # Valuation
    # Price multiples: positive-only + capped. A negative P/E·P/B·P/S is "Neg."/
    # undefined — the company-side valuation card already hides it (_positive_or_none),
    # and a negative drags the MEDIAN below the comparable profitable-peer level.
    # External providers (Damodaran/CSIMarket medians) also exclude loss-makers from
    # P/E. (P/FCF & EV/EBITDA are already positive-only via their computed >0 gate.)
    {"name": "pe_ratio",            "source": "ratios",    "field": "priceToEarningsRatio",   "type": "direct", "positive_only": True, "cap": 200.0},
    {"name": "pb_ratio",            "source": "ratios",    "field": "priceToBookRatio",       "type": "direct", "positive_only": True, "cap": 200.0},
    {"name": "ps_ratio",            "source": "ratios",    "field": "priceToSalesRatio",      "type": "direct", "positive_only": True, "cap": 200.0},
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
    """Safely extract a FINITE float value from a dict.

    🔴 `except (ValueError, TypeError)` does NOT catch a NaN: `float("NaN")` and
    `float(float("nan"))` both succeed. Without the `isfinite` check a NaN reached the
    winsorizer, and `max(-500.0, min(500.0, nan))` evaluates to **500.0** — every
    comparison against NaN is False, so `min` and `max` both return their other operand.
    A missing field therefore became the single most extreme positive growth reading in
    the sample and dragged the sector median up, silently and permanently (historical
    benchmark rows are never recomputed).

    It also defeats the YoY guard upstream: `prev_val != 0` is True for NaN, so
    `(nan - nan) / abs(nan) * 100` is admitted as a growth rate.

    `profit_power_service._safe_float` has had this guard all along; this twin did not.
    """
    val = record.get(key)
    if val is None:
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def _extract_year(record: Dict[str, Any]) -> str:
    """Extract calendar year from the record.

    Prefers FMP's ``calendarYear`` field which correctly maps fiscal quarters
    to their reporting calendar year (e.g., Apple's fiscal Q1 ending Dec 2020
    is reported as calendar year 2021).  Falls back to the date field.

    No longer the ANNUAL storage key (2026-10-07): that is `_annual_period_label`
    (`period_labels.annual_benchmark_key`), the key the readers join on.
    """
    cal_year = record.get("calendarYear")
    if cal_year:
        return str(cal_year)
    # `.get("date", "")` yields None for a present-but-null key, and `len(None)` is a
    # TypeError. Most callers here are NOT inside a try (`_compute_yoy_for_records`
    # lines ~228/233, `_index_by_period`, the quarterly recompute), so one null date
    # aborted a whole sector's benchmark recompute — and a missing sector row is what
    # makes "vs Industry Avg" quietly disappear from the detail screens.
    # `app/utils/period_labels.extract_year` is the null-safe twin that growth and
    # profit-power were migrated to; this copy is kept (its labels are STORAGE keys on
    # a calendar basis, not fiscal display strings) but now guards the same way.
    date_str = record.get("date") or ""
    if not isinstance(date_str, str):
        return ""
    if len(date_str) >= 4:
        return date_str[:4]
    return ""


def _annual_period_label(record: Dict[str, Any]) -> str:
    """Storage key of an ANNUAL row, e.g. '2024': `period_labels.annual_benchmark_key`, the
    SAME helper every reader joins on (the year of the period end minus 7 days, so a 52/53-
    week year closing on Jan 1-7 counts as the year before).

    It used to be `calendarYear`, else ``date[:4]``. /stable no longer ships calendarYear, so
    a Cadence-shaped FY2025 ending 2026-01-03 was stored as "2026" while the company's own
    card joined it to "2025": its peer line came from the wrong year, and the year it was
    pooled into counted it twice (FY2025 and FY2026 both keyed 2026)."""
    return annual_benchmark_key(record)


def _storable_quarter_key(date_value: Any) -> Optional[Tuple[int, int]]:
    """`calendar_quarter_key`, or None for a quarter that ended before
    MIN_CALENDAR_QUARTER_YEAR (its two-digit label would read as the 2090s)."""
    key = calendar_quarter_key(date_value)
    if key is None or key[0] < MIN_CALENDAR_QUARTER_YEAR:
        return None
    return key


def _quarterly_period_label(record: Dict[str, Any]) -> str:
    """Storage key of a QUARTERLY row: the CALENDAR quarter its period ends in, e.g.
    \"Q3'25\" for any quarter ending Jul-Sep 2025 (an end on day 1-7 counts as the
    previous month, for 52/53-week filers). ``""`` when the row has no usable date, or
    ended before MIN_CALENDAR_QUARTER_YEAR (never keyed — see that constant).

    It used to be FMP's FISCAL ``period`` + the calendar year of the end date, which
    pooled Microsoft's Jul-Sep quarter (fiscal Q1) with everyone else's Jan-Mar and
    Nvidia's Nov-Jan quarter (fiscal Q4) with Oct-Dec of the FOLLOWING year. Consumers
    join on the same helper (`period_labels.calendar_quarter_label`).
    """
    if not isinstance(record, dict):
        return ""
    key = _storable_quarter_key(record.get("date"))
    return format_calendar_quarter(key) if key is not None else ""


def _by_calendar_quarter(
    records: List[Dict[str, Any]],
) -> Dict[Tuple[int, int], Dict[str, Any]]:
    """One QUARTERLY record per calendar quarter, keyed ``(year, quarter)``.

    When two of a company's rows land in the same calendar quarter (a fiscal-year-end
    change leaves a short stub period; FMP occasionally repeats a row) the NEWEST period
    end wins, so a company is counted once per quarter — a duplicate used to enter the
    median twice. Non-dict rows, rows without a usable date and quarters that ended before
    MIN_CALENDAR_QUARTER_YEAR are skipped (the last quietly: a DEBUG count, since a long
    FMP history routinely reaches back that far).
    """
    out: Dict[Tuple[int, int], Dict[str, Any]] = {}
    rows = [r for r in (records or []) if isinstance(r, dict)]
    too_old = 0
    for rec in sorted(rows, key=lambda r: str(r.get("date") or "")):
        key = calendar_quarter_key(rec.get("date"))
        if key is None:
            continue
        if key[0] < MIN_CALENDAR_QUARTER_YEAR:
            too_old += 1
            continue
        out[key] = rec
    if too_old:
        logger.debug(
            "sector_benchmark: skipped %d quarterly row(s) that ended before %d "
            "(a two-digit quarter label cannot name them)", too_old, MIN_CALENDAR_QUARTER_YEAR,
        )
    return out


def _annual_spans(rows: List[Dict[str, Any]]) -> Dict[datetime, Optional[int]]:
    """Each distinct annual period END → days since the company's previous distinct end
    (None for the oldest). The span of a row is a property of its end date, so repeated
    rows share it. Undated rows have no entry."""
    ends = sorted({end for end in (_period_end(r) for r in rows) if end is not None})
    return {
        end: ((end - ends[i - 1]).days if i else None) for i, end in enumerate(ends)
    }


def _is_short_annual(rec: Dict[str, Any], spans: Dict[datetime, Optional[int]]) -> bool:
    """True for a row KNOWN to cover less than ~a year (see ANNUAL_SHORT_PERIOD_DAYS)."""
    end = _period_end(rec)
    span = spans.get(end) if end is not None else None
    return span is not None and span < ANNUAL_SHORT_PERIOD_DAYS


def _by_annual_key_with_spans(
    records: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[datetime, Optional[int]]]:
    """`_by_annual_key` plus the spans it chose by (for the YoY's own stub check)."""
    out: Dict[str, Dict[str, Any]] = {}
    rows = [r for r in (records or []) if isinstance(r, dict)]
    spans = _annual_spans(rows)
    for rec in sorted(rows, key=lambda r: _period_end(r) or datetime.min):
        label = _annual_period_label(rec)
        if not label:
            continue
        held = out.get(label)
        if (
            held is not None
            and _is_short_annual(rec, spans)
            and not _is_short_annual(held, spans)
        ):
            continue   # a transition stub never displaces a full year with the same key
        out[label] = rec
    return out, spans


def _by_annual_key(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """One ANNUAL record per storage key (`_annual_period_label`).

    The annual twin of `_by_calendar_quarter`. Two of a company's annual rows can share a
    key — a fiscal-year-end change (a Jun year and a Dec year both keyed 2024), a 52/53-week
    year closing on Jan 1-7 next to a December one, or a row FMP repeats — and each used to
    vote in the median. Non-dict rows and rows with no usable year are skipped.

    Which row wins a shared key: a row KNOWN to be short (`_is_short_annual`: a transition
    stub, its end under ANNUAL_SHORT_PERIOD_DAYS after the previous one) never displaces a
    row that is not; otherwise the newest period end wins. So a Jun → Dec switch filed as
    Jun-2024 (FY), Dec-2024 (6-month stub), Dec-2025 keeps the FULL Jun-2024 year under
    2024 — its margins and multiples vote, not a half year's (2026-10-07; newest-wins used
    to keep the stub, whose half-year P/E then voted for 2024). Every statement resolves a
    collision the same way, so the income / balance / cash-flow / key-metrics rows of a
    period still meet. A row without a usable date (keyed by `calendarYear`) sorts first, so
    a dated row with the same key replaces it unless that row is a known stub."""
    return _by_annual_key_with_spans(records)[0]


def _period_end(rec: Dict[str, Any]) -> Optional[datetime]:
    """A row's period-end date, or None when `date` is missing or not an ISO date."""
    try:
        return datetime.strptime(str(rec.get("date") or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _period_end_gap_days(rec: Dict[str, Any], prev_rec: Dict[str, Any]) -> Optional[int]:
    """Days between two rows' period ends, or None when either date is unusable."""
    cur, prev = _period_end(rec), _period_end(prev_rec)
    if cur is None or prev is None:
        return None
    return (cur - prev).days


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

    result: Dict[str, float] = {}

    if is_quarterly:
        # Same CALENDAR quarter one year earlier. Keyed by calendar quarter, not by
        # (fiscal period, end year): the old key missed the prior year whenever a
        # 52/53-week Q4 closed on Jan 1-7 (FY2025 ending 2026-01-03 looked for a
        # "Q4 2025" row that was keyed 2024), and it labelled the result with the
        # fiscal quarter number.
        by_quarter = _by_calendar_quarter(records)
        for key, rec in by_quarter.items():
            prev_rec = by_quarter.get((key[0] - 1, key[1]))
            if prev_rec is None:
                continue
            current_val = _safe_float(rec, field)
            prev_val = _safe_float(prev_rec, field)
            if current_val is not None and prev_val is not None and prev_val != 0:
                result[format_calendar_quarter(key)] = round(
                    (current_val - prev_val) / abs(prev_val) * 100, 2,
                )
    else:
        # Annual: the year keyed Y against the year keyed Y-1, one record per key
        # (`_by_annual_key`: a full year over a stub, else newest end) — the same keys the
        # rows are stored under. This used to walk consecutive records sorted by date and
        # require their `calendarYear`/`date[:4]` years to differ by exactly 1, which
        # mis-keyed a Jan 1-7 year end and let a duplicated year silently break the chain.
        by_year, spans = _by_annual_key_with_spans(records)
        for label, rec in by_year.items():
            prev_rec = by_year.get(str(int(label) - 1))
            if prev_rec is None:
                continue
            gap = _period_end_gap_days(rec, prev_rec)
            if gap is not None and not (ANNUAL_YOY_MIN_GAP_DAYS <= gap <= ANNUAL_YOY_MAX_GAP_DAYS):
                # Not a one-year comparison (a fiscal-year-end change): no growth figure.
                continue
            if _is_short_annual(rec, spans) or _is_short_annual(prev_rec, spans):
                # A transition stub on either side: a Dec → Jun switch filed as Dec-2023
                # (FY), Jun-2024 (6-month stub, alone under 2024), Jun-2025 passes the gap
                # test (365 days) but would file a full year over a half year (~+100%).
                continue
            current_val = _safe_float(rec, field)
            prev_val = _safe_float(prev_rec, field)
            if current_val is not None and prev_val is not None and prev_val != 0:
                result[label] = round((current_val - prev_val) / abs(prev_val) * 100, 2)

    return result


def _compute_qoq_for_records(
    records: List[Dict[str, Any]],
    field: str,
) -> Dict[str, float]:
    """
    Compute sequential Quarter-over-Quarter growth % for each period.
    Compares each quarter to the immediately preceding CALENDAR quarter (Q2 vs Q1,
    Q1 vs the prior Q4). Returns {calendar period_label: qoq_percent}.

    The previous quarter must actually be present: this used to compare each row with
    whatever row preceded it in date order, so a quarter missing from FMP's history
    silently produced a six-month change filed as a one-quarter one.
    """
    if not records:
        return {}

    by_quarter = _by_calendar_quarter(records)
    result: Dict[str, float] = {}

    for key, rec in by_quarter.items():
        prev_rec = by_quarter.get(previous_calendar_quarter(key))
        if prev_rec is None:
            continue
        current_val = _safe_float(rec, field)
        prev_val = _safe_float(prev_rec, field)
        if current_val is not None and prev_val is not None and prev_val != 0:
            result[format_calendar_quarter(key)] = round(
                (current_val - prev_val) / abs(prev_val) * 100, 2,
            )

    return result


def _winsorize(values: List[float], floor: float = WINSORIZE_FLOOR, ceil: float = WINSORIZE_CEIL) -> List[float]:
    """Cap extreme values to prevent outliers from distorting the median.

    Non-finite values are DROPPED, not clamped. Clamping is the trap: `min(ceil, nan)`
    returns `ceil` and `max(floor, ceil)` returns `ceil`, so a NaN silently becomes the
    ceiling — the most extreme admissible reading — rather than being excluded. Inf clamps
    to the ceiling too, which is at least directionally honest but is still a fabricated
    magnitude from a near-zero denominator.

    Kept here as well as in `_safe_float` because the `computed` metrics are reconstructed
    from raw building blocks (`_compute_ratio_values`) and can produce a non-finite ratio
    from perfectly finite inputs.
    """
    return [max(floor, min(ceil, v)) for v in values if math.isfinite(v)]


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
    """Key each record by its period label so we can join across endpoints.

    Quarterly rows key on the calendar quarter, annual rows on `_annual_period_label`;
    a collision keeps the newest period end (annual: unless it is a transition stub and the
    other row a full year — `_by_annual_key`), so the income / balance / cash-flow /
    key-metrics rows of one period still meet (each statement resolves the same tie the
    same way). Annual collisions used to keep whichever row came LAST in FMP's newest-first
    list, i.e. the OLDEST.
    """
    if period_type == "quarterly":
        return {
            format_calendar_quarter(key): rec
            for key, rec in _by_calendar_quarter(records).items()
        }
    return _by_annual_key(records)


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
                .eq("industry", "")  # only this service's sector-aggregate rows
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
        if not force and (await asyncio.to_thread(self._benchmarks_are_fresh)):
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
                existing = (await asyncio.to_thread(self._get_existing_periods, sector))

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
        """Fetch S&P 500 constituents and group by canonical sector name.

        RAISES rather than falling back. `sp500-constituent` is a blocked path, and
        `FMPClient.get_sp500_constituents` swallows the refusal into `[]` (a warning, no
        exception) — so an empty list here is the PERMANENT state, not a blip.

        Returning `_FALLBACK_SECTOR_TICKERS` at that point computed 5-company medians and
        upserted them over the broad-universe rows, and the caller could not tell: the
        write succeeded, the row count looked plausible, and `POST /admin/refresh-sector-
        benchmarks` answered `200 {"status": "started"}` because it dispatches through
        `asyncio.create_task`. One click silently degraded every sector comparison in the
        app.

        The live producer is `industry_benchmark_service.recompute_all`, which writes both
        the industry rows and the `industry = ''` sector aggregate from
        `benchmark_universe.json` (see `main.py`'s retirement note). Anything that wants
        sector benchmarks should call that.
        """
        constituents = await self.fmp.get_sp500_constituents()

        if not constituents:
            raise FMPUnavailableException(
                "sector benchmarks: `sp500-constituent` returned no rows (it is a BLOCKED "
                "path under the current entitlement). Refusing to compute medians from "
                f"the {sum(len(v) for v in _FALLBACK_SECTOR_TICKERS.values())}-ticker "
                "fallback — they would overwrite the broad-universe rows written by "
                "industry_benchmark_service.recompute_all, which is the live producer."
            )

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
            # Same refusal as the empty-constituents arm above, and for the same reason:
            # a fallback here would upsert 5-company medians over the broad-universe rows.
            # Reachable if FMP ever changes its sector vocabulary wholesale.
            raise FMPUnavailableException(
                "sector benchmarks: no constituent row mapped to a canonical sector "
                f"({len(constituents)} rows in, 0 grouped). Refusing to fall back to the "
                "hardcoded tickers — see `_get_sector_tickers`."
            )

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
                stored_type = STORED_PERIOD_TYPE[period_type]
                metric_type = metric_config["type"]
                for period_label, values in period_values.items():
                    # Filter BEFORE the sample-size gate, not after. `_winsorize` now drops
                    # non-finite values, so gating on the raw list and reporting
                    # `len(cleaned)` would publish a median whose stored `sample_size` is
                    # below MIN_SAMPLE_SIZE — a row that reads as authoritative and is not.
                    # (`_compute_ratio_values` builds `computed` metrics from raw parts and
                    # can emit a non-finite ratio from finite inputs, so this is reachable
                    # even with `_safe_float` guarded.)
                    values = [v for v in values if math.isfinite(v)]
                    if len(values) < MIN_SAMPLE_SIZE:
                        continue
                    # Skip periods already stored (historical benchmarks never change)
                    if existing_periods and (
                        metric_config["name"], stored_type, period_label
                    ) in existing_periods:
                        continue
                    # Winsorize to cap extreme outliers.
                    #   yoy / qoq → wide bounds (growth % can swing huge)
                    #   computed ratios (P/FCF, EV/EBITDA) → tight 0–200 bounds:
                    #     near-zero denominators produce 4-digit multiples that
                    #     pull the median upward; healthy ratios are <50.
                    metric_cap = metric_config.get("cap")
                    if metric_cap is not None:
                        # positive-only multiples (P/E·P/B·P/S, interest coverage):
                        # already filtered to > 0; clamp the top so a near-zero
                        # denominator can't inflate the tail.
                        cleaned = _winsorize(values, floor=0.0, ceil=metric_cap)
                    elif metric_type in ("yoy", "qoq"):
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
                    # Belt and braces: the branches above can only ever SHRINK the list
                    # (winsorize drops non-finite), so re-check rather than trust that the
                    # gate above still holds. A median over 2 companies published as a
                    # sector benchmark is worse than no row.
                    if len(cleaned) < MIN_SAMPLE_SIZE:
                        logger.warning(
                            "  %s/%s/%s: %d of %d values survived cleaning (min %d) — "
                            "skipping rather than publishing a thin median",
                            sector, metric_config["name"], period_label,
                            len(cleaned), len(values), MIN_SAMPLE_SIZE,
                        )
                        continue
                    rows_to_upsert.append({
                        "sector": sector,
                        # This service writes only SECTOR-aggregate rows → industry=''.
                        # Industry-level rows (industry=<name>) are written by
                        # industry_benchmark_service. See migration 072.
                        "industry": "",
                        "metric_name": metric_config["name"],
                        "period_type": stored_type,
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
                (await sb_exec(
                    self.supabase.table("sector_benchmarks").upsert(
                    batch,
                    on_conflict="sector,industry,metric_name,period_type,period_label",
                    )
                ))
                upserted += len(batch)
            except Exception as e:
                logger.error(f"  Upsert batch failed for {sector}: {e}")

        return upserted

    async def _fmp_call_retrying(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        """`_fmp_call` for a CALLABLE, so a 429 can be retried (a coroutine object can be
        awaited only once): holds the global semaphore and backs off inside it."""
        async with self._fmp_semaphore:
            return await call_with_rate_limit_retry(fn, *args, **kwargs)

    async def _fetch_company_data(self, ticker: str, annual_limit: int, quarterly_limit: int) -> Dict[str, List]:
        """Fetch income, cash flow, balance sheet, ratios, and key metrics for one company (annual + quarterly).

        Balance sheet was added in Phase 3A for moat-scoring metrics
        (intangibles_to_assets, deferred_revenue_to_revenue).

        A failed call still yields an empty list for its key (the medians simply lack that
        company there), but it is no longer invisible: a 429 is retried first
        (`call_with_rate_limit_retry`), and the kind of every call that still failed — or
        answered something other than a list ('refused') — is listed under FETCH_ERRORS_KEY,
        and by statement key under FETCH_FAILED_CALLS_KEY, for the recompute to count
        (`industry_benchmark_service._FetchTally`).
        """
        calls = (
            ("income_annual", self.fmp.get_income_statement, "annual", annual_limit),
            ("income_quarterly", self.fmp.get_income_statement, "quarter", quarterly_limit),
            ("cashflow_annual", self.fmp.get_cash_flow_statement, "annual", annual_limit),
            ("cashflow_quarterly", self.fmp.get_cash_flow_statement, "quarter", quarterly_limit),
            ("ratios_annual", self.fmp.get_financial_ratios, "annual", annual_limit),
            ("ratios_quarterly", self.fmp.get_financial_ratios, "quarter", quarterly_limit),
            ("key_metrics_annual", self.fmp.get_key_metrics, "annual", annual_limit),
            ("key_metrics_quarterly", self.fmp.get_key_metrics, "quarter", quarterly_limit),
            ("balance_annual", self.fmp.get_balance_sheet, "annual", annual_limit),
            ("balance_quarterly", self.fmp.get_balance_sheet, "quarter", quarterly_limit),
        )
        results = await asyncio.gather(
            *[
                self._fmp_call_retrying(fn, ticker, period=period, limit=limit)
                for _key, fn, period, limit in calls
            ],
            return_exceptions=True,
        )

        out: Dict[str, Any] = {}
        errors: List[str] = []
        failed_calls: Dict[str, str] = {}
        for (key, _fn, _period, _limit), result in zip(calls, results):
            if isinstance(result, list):
                out[key] = result
                continue
            out[key] = []
            if isinstance(result, BaseException):
                kind = classify_fetch_failure(result)
            elif result is not None:
                # FMP answered 200 with something that is not a statement list (an error
                # object): the company is missing from this key exactly as if it had failed,
                # and a retry gets the same answer — 'refused', never transient.
                kind = "refused"
            else:
                continue
            errors.append(kind)
            failed_calls[key] = kind
        out[FETCH_ERRORS_KEY] = errors
        out[FETCH_FAILED_CALLS_KEY] = failed_calls
        return out

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
                # Direct value extraction. `positive_only` metrics (the price
                # multiples P/E·P/B·P/S, interest coverage) drop non-positive values:
                # a negative multiple is "Neg."/undefined (the company side hides it)
                # and would drag the median below the comparable profitable-peer level.
                positive_only = metric_config.get("positive_only", False)
                # One row per period per company (`_by_calendar_quarter` /
                # `_by_annual_key`), so a duplicate row can't vote twice.
                labelled = (
                    [
                        (format_calendar_quarter(key), rec)
                        for key, rec in _by_calendar_quarter(records).items()
                    ]
                    if is_quarterly
                    else list(_by_annual_key(records).items())
                )
                for label, rec in labelled:
                    val = _safe_float(rec, field)
                    if val is None:
                        continue
                    if positive_only and val <= 0:
                        continue
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
