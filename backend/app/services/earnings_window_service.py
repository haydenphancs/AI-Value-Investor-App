"""Earnings calendar context for the Insights sweeper: cap boost + per-ticker status.

WHY THIS EXISTS — two TestFlight reports on ORCL:

* 2026-09-11 (two days after its report): the card read "Updated 2 hours ago ·
  checking for updates" over eight fresh sources. The sweeper had checked ORCL every
  five minutes; what it lacked was ALLOWANCE. A ticker whose earnings date falls
  within D-1..D+2 therefore gets the MARKET-sized cap
  (`updates_materiality.daily_cap_for(..., earnings_window=True)`) — the boost set.
* 2026-09-10 (report day): the card said "Oracle is set to report Q1 earnings" hours
  after the 16:10 ET release. The prompt had no idea what day it was or whether the
  report had happened. So the sweeper now also gets a per-ticker STATUS
  (`reported` / `due_today` / `upcoming`) for the prompt's EARNINGS line, the
  report-day allowance, and the one-shot "results just landed" trigger.

DATA. FMP `/stable/earnings-calendar` rows carry exactly `date, epsActual,
epsEstimated, lastUpdated, revenueActual, revenueEstimated, symbol` — no time of day
(before-open / after-close). A finite `epsActual` or `revenueActual` is the only
reliable "has reported" signal (0.0 is a real breakeven report; NaN is absent).

⚠️ ONE DAY PER CALL. The calendar silently truncates at 4,000 rows and keeps the
NEWEST dates: measured 2026-09-27, a 2026-11-02..05 request returned only the 4th
(partly) and the 5th. The old single D-2..D+1 request therefore dropped D-2 and D-1 in
peak season — exactly the post-report days the boost exists for. Every day in the
window is now its own request, rows are kept only under their own date, and a day
that still comes back at the cap is logged as an ERROR.

COST, per process: the CONTEXT window D-4..D+3 is 8 single-day calls once per ET day,
plus a HOT refresh of {previous trading day, today} (2 calls) at most every 20 minutes
— and only while an equity ticker in the sweep universe has a row on those dates whose
results are not in yet. Most days that is zero hot calls. The calendar was once ~99% of
this app's FMP bandwidth (`earnings_service.py`), which is why the hot refresh is
narrow and conditional.

FAILURE is fail-SAFE, never fail-open: the full round is all-or-nothing. A failure
keeps serving the previous complete snapshot (re-derived for today) or, with none, an
EMPTY one — nobody boosted, no earnings line — plus a warning and a short negative TTL
so a transient failure is retried within the same day. A status is only ever stated
when the calendar supports it; there is deliberately no "late" status (a passed date
with no actuals), because FMP lag and reschedule duplicates make it misleading.

⚠️ `fmp` is keyword-only and has NO default on purpose. The sweeper passes its own
client (`self.fmp`), which in the test-suite stubs is `None` or a stub without
`get_earnings_calendar`. Reaching for `get_fmp_client()` here instead would make every
existing `run_sweep` test attempt a real HTTP call, and the hermetic guard in
`conftest.py` fails the WHOLE session on the first one.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import (
    Any,
    Awaitable,
    Callable,
    Collection,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
)

from app.services.updates_materiality import finite
from app.utils.market_hours import ET, previous_trading_day

logger = logging.getLogger(__name__)

# A report on day D boosts the ticker from D-1 (previews, positioning) through D+2
# (the reaction session and the follow-up analysis). ORCL's screenshot was D+2.
# Expressed as how far the window reaches BACK and AHEAD of today, so the membership
# test and its bounds share one definition.
EARNINGS_WINDOW_DAYS_BACK = 2
EARNINGS_WINDOW_DAYS_AHEAD = 1

# The wider CONTEXT window the statuses are derived from. Back 4 days covers the
# longest corpus window the card can summarise (96h across a long weekend), so a
# preview article that old can still be recognised as outdated. Ahead only 3: FMP's
# future dates are often estimates, and stating one as fact is its own error.
EARNINGS_CONTEXT_DAYS_BACK = 4
EARNINGS_CONTEXT_DAYS_AHEAD = 3

# After a failed fetch, how long to serve the stale (or empty) snapshot before trying
# again. Keyed on the injected `now` (not a monotonic clock) so it is testable.
_FAILURE_RETRY_SECONDS = 15 * 60

# How often the HOT days (previous trading day + today) are re-fetched while results
# are still pending. FMP can take until the next morning to fill `epsActual` for an
# after-close report, so the previous day is refreshed too.
_HOT_REFRESH_SECONDS = 20 * 60

_FETCH_CONCURRENCY = 4
# Overall deadline for one round, INSIDE the leader (never a wait_for around the
# leader itself — cancelling it would hand every joiner an empty answer). The client
# retries each call internally, so 8 slow calls could otherwise stall the sweep for
# minutes before a single scope is evaluated.
_FETCH_DEADLINE_SECONDS = 20.0
# FMP's silent row cap. A single-day response this large is probably truncated too.
_TRUNCATION_ROWS = 4000

EARNINGS_REPORTED = "reported"
EARNINGS_DUE_TODAY = "due_today"
EARNINGS_UPCOMING = "upcoming"


@dataclass(frozen=True)
class EarningsStatus:
    """One ticker's earnings status on an ET day. Dates only — never figures."""

    status: str
    date: date
    # When THIS process first saw the results land (a row it had seen without
    # actuals later carried them). None when it never saw the transition — a
    # restart, or results already in at the first fetch of the day.
    reported_seen_at: Optional[datetime] = None


def et_date(now: datetime) -> date:
    """The ET calendar date of ``now``. A naive ``now`` is read as UTC."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET).date()


def earnings_window_bounds(today: date) -> Tuple[date, date]:
    """``(from, to)`` — the inclusive span tested for the cap boost on ``today``."""
    return (
        today - timedelta(days=EARNINGS_WINDOW_DAYS_BACK),
        today + timedelta(days=EARNINGS_WINDOW_DAYS_AHEAD),
    )


def earnings_context_bounds(today: date) -> Tuple[date, date]:
    """``(from, to)`` — the inclusive span fetched and used for statuses."""
    return (
        today - timedelta(days=EARNINGS_CONTEXT_DAYS_BACK),
        today + timedelta(days=EARNINGS_CONTEXT_DAYS_AHEAD),
    )


def context_days(today: date) -> List[date]:
    """Every day in the context window, oldest first — one calendar call each."""
    start, end = earnings_context_bounds(today)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _row_date(row: Any) -> Optional[date]:
    """The row's ``date`` as a `date`, or None when it cannot be trusted.

    FMP occasionally returns a full timestamp (``"2026-09-21 16:00:00"``) or an
    empty string. Slicing to 10 characters normalises the first; anything that
    is not then a strict ``YYYY-MM-DD`` is rejected rather than guessed.
    """
    if not isinstance(row, dict):
        return None
    raw = str(row.get("date") or "")[:10]
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _row_symbol(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("symbol") or "").strip().upper()


def row_reported(row: Any) -> bool:
    """Does this calendar row carry actual results?

    ``finite`` rejects None, "", NaN, ±inf and bools, so ``True`` is not a report
    and ``0.0`` (a breakeven quarter) is. Truthiness would get both wrong.
    """
    if not isinstance(row, dict):
        return False
    return (
        finite(row.get("epsActual")) is not None
        or finite(row.get("revenueActual")) is not None
    )


def parse_earnings_window(rows: Any, today: date) -> FrozenSet[str]:
    """PURE. The upper-cased symbols whose earnings date lies inside today's window.

    Skips anything that is not a dict, has no symbol, or has a date that does not
    parse. A row OUTSIDE the window is dropped even though the fetch covers more —
    the bounds are re-applied here so the wider context fetch (or a lenient
    upstream) can never widen the boost.
    """
    if not isinstance(rows, list):
        return frozenset()
    from_d, to_d = earnings_window_bounds(today)
    out = set()
    for row in rows:
        symbol = _row_symbol(row)
        if not symbol:
            continue
        d = _row_date(row)
        if d is None or d < from_d or d > to_d:
            continue
        out.add(symbol)
    return frozenset(out)


def earnings_status(
    rows: Iterable[Any],
    today: date,
    *,
    known_reported: Collection[date] = (),
) -> Optional[EarningsStatus]:
    """PURE. One symbol's status on ``today`` from its calendar rows, or None.

    Precedence: ``reported`` (actuals, dated D-4..D — the latest) > ``due_today``
    > ``upcoming`` (the nearest pending date in D+1..D+3) > None. A pending row
    dated in the PAST yields nothing — FMP lag or a stale estimate after a
    reschedule, and "the report is late" would be an invented fact.

    A row dated in the FUTURE that already carries actuals is garbage, and makes
    the whole symbol unknown (None): falling through to "upcoming" would tell the
    model a report that happened is still ahead — the ORCL error itself.

    ``known_reported`` lists dates this process has already seen reported, so a
    later response that drops the actuals cannot flip a symbol back to pending.
    """
    lo, hi = earnings_context_bounds(today)
    reported: List[date] = []
    pending: List[date] = []
    for row in rows:
        d = _row_date(row)
        if d is None or d < lo or d > hi:
            continue
        if row_reported(row) or d in known_reported:
            if d > today:
                return None
            reported.append(d)
        else:
            pending.append(d)
    if reported:
        return EarningsStatus(EARNINGS_REPORTED, max(reported))
    if today in pending:
        return EarningsStatus(EARNINGS_DUE_TODAY, today)
    future = [d for d in pending if d > today]
    if future:
        return EarningsStatus(EARNINGS_UPCOMING, min(future))
    return None


def statuses_by_symbol(
    rows: Iterable[Any],
    today: date,
    *,
    reported_keys: Collection[Tuple[str, date]] = (),
    reported_seen_at: Mapping[Tuple[str, date], datetime] = {},
) -> Dict[str, EarningsStatus]:
    """PURE. ``{SYMBOL: EarningsStatus}`` for every symbol with a status today."""
    grouped: Dict[str, List[Any]] = {}
    for row in rows:
        symbol = _row_symbol(row)
        if symbol and _row_date(row) is not None:
            grouped.setdefault(symbol, []).append(row)
    known: Dict[str, Set[date]] = {}
    for symbol, d in reported_keys:
        known.setdefault(symbol, set()).add(d)
    out: Dict[str, EarningsStatus] = {}
    for symbol, sym_rows in grouped.items():
        status = earnings_status(
            sym_rows, today, known_reported=known.get(symbol, ()),
        )
        if status is None:
            continue
        if status.status == EARNINGS_REPORTED:
            seen = reported_seen_at.get((symbol, status.date))
            if seen is not None:
                status = EarningsStatus(status.status, status.date, seen)
        out[symbol] = status
    return out


def earnings_gate_inputs(
    status: Optional[EarningsStatus], today: date
) -> Tuple[bool, bool, Optional[datetime]]:
    """``(report_day, pending_today, reported_at)`` for `updates_materiality.decide`.

    ``report_day`` stays True after the flip to ``reported`` — the extra allowance is
    for exactly the hours after the results land.
    """
    if status is None:
        return False, False, None
    report_day = status.date == today and status.status in (
        EARNINGS_REPORTED, EARNINGS_DUE_TODAY,
    )
    pending = status.status == EARNINGS_DUE_TODAY and status.date == today
    reported_at = (
        status.reported_seen_at if status.status == EARNINGS_REPORTED else None
    )
    return report_day, pending, reported_at


def earnings_is_hot(status: Optional[EarningsStatus], today: date) -> bool:
    """Admission priority: it reports today, or reported on the previous trading day."""
    if status is None:
        return False
    if status.date == today and status.status in (EARNINGS_REPORTED, EARNINGS_DUE_TODAY):
        return True
    return (
        status.status == EARNINGS_REPORTED
        and status.date >= previous_trading_day(today)
    )


class EarningsWindowService:
    """One context fetch per ET day (+ conditional hot refreshes), shared in-process."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Forget everything (tests)."""
        # `_day` = the ET day the last COMPLETE context round was fetched for.
        self._day: Optional[date] = None
        self._rows_by_day: Dict[date, List[Dict[str, Any]]] = {}
        self._symbols: FrozenSet[str] = frozenset()
        self._statuses: Dict[str, EarningsStatus] = {}
        self._built_for: Optional[date] = None
        self._failed_until: Optional[datetime] = None
        self._hot_fetched_at: Optional[datetime] = None
        # Sticky per (symbol, date): once reported, always reported this process.
        self._reported_keys: Set[Tuple[str, date]] = set()
        self._pending_keys: Set[Tuple[str, date]] = set()
        self._reported_seen_at: Dict[Tuple[str, date], datetime] = {}
        # Keyed by (round kind, ET date) — the same shape as every other `_inflight`
        # map in this codebase, so the cancellation-safety guards in
        # tests/test_inflight_cancellation_safety.py recognise the join below.
        self._inflight: Dict[Tuple[str, date], asyncio.Future] = {}

    # ── public ────────────────────────────────────────────────────────

    async def symbols_in_window(self, now: datetime, *, fmp: Any) -> FrozenSet[str]:
        """The tickers in their earnings window (boost) on ``now``'s ET date. Never raises."""
        today = await self._ensure(now, fmp=fmp, symbols=())
        self._rebuild_if_needed(today)
        return self._symbols

    async def statuses(
        self, now: datetime, *, fmp: Any, symbols: Iterable[str]
    ) -> Dict[str, EarningsStatus]:
        """``{SYMBOL: status}`` for the given equity symbols. Never raises."""
        wanted = [str(s).strip().upper() for s in symbols if str(s).strip()]
        today = await self._ensure(now, fmp=fmp, symbols=wanted)
        self._rebuild_if_needed(today)
        return {s: self._statuses[s] for s in wanted if s in self._statuses}

    # ── refresh orchestration ─────────────────────────────────────────

    async def _ensure(self, now: datetime, *, fmp: Any, symbols: Collection[str]) -> date:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        today = et_date(now)
        if self._day != today:
            if self._failed_until is not None and now < self._failed_until:
                return today
            await self._run_once(("full", today), lambda: self._full_round(now, today, fmp))
        elif self._hot_due(now, today, symbols):
            await self._run_once(("hot", today), lambda: self._hot_round(now, today, fmp))
        return today

    async def _run_once(
        self, key: Tuple[str, date], factory: Callable[[], Awaitable[None]]
    ) -> None:
        """Leader/joiner dedup: concurrent callers share one round."""
        inflight = self._inflight.get(key)
        if inflight is not None:
            # Shielded, as in news_cache_service._deduped: a cancelled joiner must
            # not cancel the round the leader is about to publish.
            try:
                await asyncio.shield(inflight)
            except asyncio.CancelledError:
                if inflight.cancelled():
                    # The LEADER was cancelled (shutdown mid-fetch). Degrade like
                    # any other failure; the joiner itself was not cancelled.
                    return
                raise
            return

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[key] = fut
        try:
            await factory()
            if not fut.done():
                fut.set_result(None)
        except Exception as e:
            # The rounds already degrade every expected failure; this is the
            # defensive net for the unexpected — same policy, logged. Joiners get
            # the same (stale or empty) answer rather than an exception.
            logger.warning(
                "Earnings calendar round %s raised (%s: %s) — no ticker is boosted "
                "and no earnings line is added until the next attempt",
                key[0], type(e).__name__, e,
            )
            if not fut.done():
                fut.set_result(None)
        except BaseException:
            # Cancellation must still resolve the future or every joiner hangs.
            if not fut.done():
                fut.cancel()
            raise
        finally:
            self._inflight.pop(key, None)

    def _hot_due(self, now: datetime, today: date, symbols: Collection[str]) -> bool:
        if not symbols:
            return False
        last = self._hot_fetched_at
        # A `now` EARLIER than the last refresh (clock skew, or a test mixing the
        # real clock with a fixed instant) counts as due rather than as fresh.
        if last is not None and now >= last and (now - last).total_seconds() < _HOT_REFRESH_SECONDS:
            return False
        hot_days = {previous_trading_day(today), today}
        wanted = set(symbols)
        for d in hot_days:
            for row in self._rows_by_day.get(d, ()):
                symbol = _row_symbol(row)
                if (
                    symbol in wanted
                    and not row_reported(row)
                    and (symbol, d) not in self._reported_keys
                ):
                    return True
        return False

    # ── rounds ────────────────────────────────────────────────────────

    async def _full_round(self, now: datetime, today: date, fmp: Any) -> None:
        getter = getattr(fmp, "get_earnings_calendar", None)
        if getter is None:
            self._note_failure(now, "no FMP client with get_earnings_calendar")
            return
        days = context_days(today)
        try:
            fetched = await asyncio.wait_for(
                _fetch_days(getter, days), timeout=_FETCH_DEADLINE_SECONDS,
            )
        except asyncio.TimeoutError:
            self._note_failure(
                now, f"context round exceeded {_FETCH_DEADLINE_SECONDS:.0f}s"
            )
            return
        except Exception as e:
            self._note_failure(now, f"{type(e).__name__}: {e}")
            return
        # `[]` days are real answers (a holiday, a quiet stretch) and are cached for
        # the day like any other — refetching an empty calendar every sweep would be
        # pure waste.
        self._prune(today)
        self._rows_by_day = fetched
        self._absorb(now, fetched, today)
        self._day = today
        self._failed_until = None
        self._hot_fetched_at = now
        self._rebuild(today)
        lo, hi = earnings_context_bounds(today)
        logger.info(
            "Earnings calendar %s..%s (%d day calls): %d row(s), %d ticker(s) "
            "boosted, %d with a status for %s",
            lo.isoformat(), hi.isoformat(), len(days),
            sum(len(v) for v in fetched.values()), len(self._symbols),
            len(self._statuses), today.isoformat(),
        )

    async def _hot_round(self, now: datetime, today: date, fmp: Any) -> None:
        getter = getattr(fmp, "get_earnings_calendar", None)
        # Stamped FIRST: a failed hot refresh waits the same 20 minutes, rather than
        # retrying on every 5-minute sweep.
        self._hot_fetched_at = now
        if getter is None:
            return
        days = sorted({previous_trading_day(today), today})
        try:
            fetched = await asyncio.wait_for(
                _fetch_days(getter, days), timeout=_FETCH_DEADLINE_SECONDS,
            )
        except Exception as e:
            logger.warning(
                "Earnings hot refresh failed (%s: %s) — keeping the previous "
                "snapshot; next try in %d min",
                type(e).__name__, e, _HOT_REFRESH_SECONDS // 60,
            )
            return
        kept: Dict[date, List[Dict[str, Any]]] = {}
        for d, rows in fetched.items():
            if not rows and self._rows_by_day.get(d):
                # An empty answer for a day that had rows is almost always a
                # transient blip, never "every report was withdrawn".
                logger.warning(
                    "Earnings hot refresh returned no rows for %s (had %d) — kept "
                    "the previous rows", d.isoformat(), len(self._rows_by_day[d]),
                )
                continue
            kept[d] = rows
        self._rows_by_day.update(kept)
        newly = self._absorb(now, kept, today)
        self._rebuild(today)
        if newly:
            logger.info(
                "Earnings results landed for %s", ", ".join(sorted(newly)),
            )

    # ── state ─────────────────────────────────────────────────────────

    def _absorb(
        self, now: datetime, fetched: Mapping[date, List[Dict[str, Any]]], today: date,
    ) -> List[str]:
        """Record reported / pending keys; returns symbols whose results just landed.

        Two rules, each a defect found in review (2026-09-27):

        * Only a row dated TODAY OR EARLIER can become a sticky "reported" key. A
          future-dated row with actuals is garbage (``earnings_status`` refuses it),
          and making it sticky let a later, corrected pending row read "reported"
          on its date — before the release, the ORCL error in reverse — while also
          suppressing the hot refresh and the landing trigger.
        * A landing is a key that was pending BEFORE this response. One response
          carrying a pending AND a reported row for the same (symbol, date) — FMP's
          duplicate shape — is simply "reported", not a landing; otherwise every
          restart on D..D+4 fired the one-shot regeneration again.
        """
        pending_before = set(self._pending_keys)
        batch_reported: Set[Tuple[str, date]] = set()
        batch_pending: Set[Tuple[str, date]] = set()
        for d, rows in fetched.items():
            for row in rows:
                symbol = _row_symbol(row)
                if not symbol:
                    continue
                if row_reported(row):
                    if d <= today:
                        batch_reported.add((symbol, d))
                else:
                    batch_pending.add((symbol, d))
        newly: List[str] = []
        for key in sorted(batch_reported):
            if key in self._reported_keys:
                continue
            self._reported_keys.add(key)
            if key in pending_before:
                self._reported_seen_at[key] = now
                newly.append(key[0])
        self._pending_keys |= {
            key for key in batch_pending
            if key not in self._reported_keys and key not in batch_reported
        }
        return newly

    def _prune(self, today: date) -> None:
        lo, _ = earnings_context_bounds(today)
        self._reported_keys = {k for k in self._reported_keys if k[1] >= lo}
        self._pending_keys = {k for k in self._pending_keys if k[1] >= lo}
        self._reported_seen_at = {
            k: v for k, v in self._reported_seen_at.items() if k[1] >= lo
        }

    def _rebuild(self, today: date) -> None:
        rows = [r for day_rows in self._rows_by_day.values() for r in day_rows]
        self._symbols = parse_earnings_window(rows, today)
        self._statuses = statuses_by_symbol(
            rows, today,
            reported_keys=self._reported_keys,
            reported_seen_at=self._reported_seen_at,
        )
        self._built_for = today

    def _rebuild_if_needed(self, today: date) -> None:
        # A stale snapshot served across an ET-day roll (the new day's fetch failed)
        # is re-derived for TODAY: the rows are still true, the dates move.
        if self._built_for != today and self._rows_by_day:
            self._rebuild(today)

    def _note_failure(self, now: datetime, why: str) -> None:
        self._failed_until = now + timedelta(seconds=_FAILURE_RETRY_SECONDS)
        stale = " (serving the previous snapshot)" if self._rows_by_day else ""
        logger.warning(
            "Earnings window unavailable (%s) — no ticker is boosted beyond the "
            "last good snapshot%s for the next %d min",
            why, stale, _FAILURE_RETRY_SECONDS // 60,
        )


async def _fetch_days(
    getter: Callable[..., Awaitable[Any]], days: List[date]
) -> Dict[date, List[Dict[str, Any]]]:
    """One calendar call per day, bounded concurrency, ALL-OR-NOTHING.

    A partial round could leave only a stale duplicate row for a ticker whose
    confirmed date failed to load — a wrong status, not just a missing one — so any
    failed day fails the round and the previous snapshot keeps serving.
    """
    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def _one(d: date) -> Tuple[date, List[Dict[str, Any]]]:
        async with sem:
            rows = await getter(from_date=d.isoformat(), to_date=d.isoformat())
        if not isinstance(rows, list):
            raise TypeError(f"calendar for {d.isoformat()} returned {type(rows).__name__}, not a list")
        if len(rows) >= _TRUNCATION_ROWS:
            logger.error(
                "Earnings calendar for %s returned %d rows — at FMP's silent cap, "
                "so it is probably TRUNCATED (newest dates kept)",
                d.isoformat(), len(rows),
            )
        # Rows are kept only under their own date: a lenient upstream (or a test
        # fake) that returns other days must not duplicate them.
        return d, [r for r in rows if isinstance(r, dict) and _row_date(r) == d]

    results = await asyncio.gather(*(_one(d) for d in days), return_exceptions=True)
    out: Dict[date, List[Dict[str, Any]]] = {}
    for res in results:
        if isinstance(res, BaseException):
            raise res
        d, rows = res
        out[d] = rows
    return out


_service: Optional[EarningsWindowService] = None


def get_earnings_window_service() -> EarningsWindowService:
    global _service
    if _service is None:
        _service = EarningsWindowService()
    return _service


async def symbols_in_earnings_window(now: datetime, *, fmp: Any) -> FrozenSet[str]:
    """Module-level entry point (the sweeper imports this name so tests can patch it)."""
    return await get_earnings_window_service().symbols_in_window(now, fmp=fmp)


async def earnings_statuses_for(
    now: datetime, *, fmp: Any, symbols: Iterable[str]
) -> Dict[str, EarningsStatus]:
    """Module-level entry point for per-ticker statuses. Shares the boost's snapshot."""
    return await get_earnings_window_service().statuses(now, fmp=fmp, symbols=symbols)
