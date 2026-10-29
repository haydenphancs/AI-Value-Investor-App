"""Background loops for the Trillion-Dollar Club jobs (spawned from ``main.lifespan``, Railway only).

* DAILY at 07:00 ET, every calendar day — membership from dated market-cap closes, then the
  13F builds for the owner-opted-in filers (``jobs.run_daily``). 07:00 is before the open,
  so the newest dated close is the previous session's, final. A deploy or failure later in
  the day catches up on the next wake.
* WEEKLY on Monday at 08:00 ET — 13F re-hash for late amendments, the new-filer probe and
  the discovery screen (``jobs.run_weekly``). A Monday missed entirely (every attempt failed,
  or no instance up) is not caught up later in the week: the day-keyed claim cannot tell
  "ran this week" from "ran today", and the next Monday does the same work — nothing it does
  is time-critical.
* STAKE FINDER, every day at 18:30 ET — the stakes a member newly disclosed in SEC filings
  (``stake_finder.run_stakes``, migration 205). 18:30 ET picks up the filings accepted during
  the day (EDGAR full-text indexing was seen within minutes, but is not guaranteed; the
  finder's lookback window and its findings ledger make a late index harmless). On Mondays
  the same run adds the re-check of the published machine rows — the finder decides that
  from ``now``, so the schedule has one slot. ``TRILLION_CLUB_STAKE_FINDER_ENABLED`` (default
  False) is read before anything else: while it is off a wake does no I/O and never imports
  the finder. A failed run — no delivered Withdraw note means nothing was published — retries
  hourly within the same attempt cap, and a run still going after
  ``_STAKES_RUN_BACKSTOP_SECONDS`` (past the finder's own 40-minute deadline) is cancelled, so
  the loop can never wedge on one hung call and the claim is released before another
  instance could take it over.

Exactly-once and bounded, three layers:
  1. the ``notification_job_state`` day claim (migration 147 RPCs; rows seeded by migrations
     175 and 205) in the ET day — one instance at a time, and a success ends the day;
  2. ``enabled`` on that row is the no-deploy kill switch. It is read FIRST, so a disabled
     job sleeps until its next slot instead of asking hourly. ``TRILLION_CLUB_JOBS_ENABLED``
     (default False) gates all three loops entirely and is re-checked on every wake;
  3. a per-ET-day ATTEMPT counter in this module, one per job. The ledger cannot count
     failures — ``claim_scheduled_job`` resets ``runs_today`` whenever ``run_day`` differs from
     today, and ``run_day`` moves only on success — so without it a job that fails every time
     would re-run hourly all day. At most ``MAX_ATTEMPTS_PER_DAY`` claimed runs per job, per
     ET day, per instance.

``run.success`` is the summary's ``ok``: True only when EVERY stage of the run succeeded. A
partial run leaves ``run_day`` unset (with the failures as ``last_error``), and the next wake
retries within the attempt cap.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Set

from app.config import settings
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

JOB_TRILLION_CLUB_DAILY = "trillion_club_daily"
JOB_TRILLION_CLUB_WEEKLY = "trillion_club_weekly"
JOB_TRILLION_CLUB_STAKE_FINDER = "trillion_club_stake_finder"   # the row migration 205 seeds
TIMEZONE_NAME = "America/New_York"

DAILY_TIME_ET = dtime(7, 0)
WEEKLY_TIME_ET = dtime(8, 0)
WEEKLY_WEEKDAY = 0                      # Monday
STAKES_TIME_ET = dtime(18, 30)          # every day; Mondays the run adds the re-check
RETRY_SECONDS = 3600
MAX_ATTEMPTS_PER_DAY = 3
MAX_IDLE_SLEEP_SECONDS = 6 * 3600       # re-read the enable flag at least this often
DAILY_BOOT_DELAY_SECONDS = 300          # after the theme loops (180 / 240 s)
WEEKLY_BOOT_DELAY_SECONDS = 360
STAKES_BOOT_DELAY_SECONDS = 480         # after Learn's 420 s; no other loop wakes at 480 s
# A run is a few dozen FMP calls (minutes); the stale window only has to outlast it so a
# deploy overlap cannot steal a run still in progress.
_DAILY_STALE_SECONDS = 1800
_WEEKLY_STALE_SECONDS = 3600
# The stake finder ends its own run at a 40-minute deadline. The scheduler's backstop cancels
# a run still going 10 minutes after that (one hung await), and the stale window outlasts the
# backstop by another 10, so the claim is always released before a deploy overlap could take
# over a run that is still in progress: deadline < backstop < stale window.
_STAKES_STALE_SECONDS = 3600
_STAKES_RUN_BACKSTOP_SECONDS = 3000


# ── Schedule math (pure; tested) ──────────────────────────────────────────────────────


def daily_due(now: datetime) -> bool:
    return now.astimezone(ET).time() >= DAILY_TIME_ET


def _next_every_day(now: datetime, at: dtime) -> datetime:
    """The next ``at`` (New York wall clock) strictly after ``now``, in UTC. Both slots (07:00,
    18:30) lie outside the 01:00-03:00 DST gap and fold, so every day has exactly one."""
    d = now.astimezone(ET).date()
    for _ in range(3):
        candidate = datetime.combine(d, at, tzinfo=ET).astimezone(timezone.utc)
        if candidate > now:
            return candidate
        d += timedelta(days=1)
    raise RuntimeError(f"no {at:%H:%M} ET after {now.isoformat()}")  # unreachable


def next_daily_run(now: datetime) -> datetime:
    """The next 07:00 ET strictly after ``now`` (UTC)."""
    return _next_every_day(now, DAILY_TIME_ET)


def weekly_due(now: datetime) -> bool:
    local = now.astimezone(ET)
    return local.weekday() == WEEKLY_WEEKDAY and local.time() >= WEEKLY_TIME_ET


def next_weekly_run(now: datetime) -> datetime:
    """The next Monday 08:00 ET strictly after ``now`` (UTC)."""
    d = now.astimezone(ET).date()
    for _ in range(9):
        if d.weekday() == WEEKLY_WEEKDAY:
            candidate = datetime.combine(d, WEEKLY_TIME_ET, tzinfo=ET).astimezone(timezone.utc)
            if candidate > now:
                return candidate
        d += timedelta(days=1)
    raise RuntimeError(f"no Monday 08:00 ET after {now.isoformat()}")  # unreachable


def stakes_due(now: datetime) -> bool:
    return now.astimezone(ET).time() >= STAKES_TIME_ET


def next_stakes_run(now: datetime) -> datetime:
    """The next 18:30 ET strictly after ``now`` (UTC)."""
    return _next_every_day(now, STAKES_TIME_ET)


def _sleep_until(target: datetime, now: datetime) -> float:
    return max(60.0, min(MAX_IDLE_SLEEP_SECONDS, (target - now).total_seconds()))


# ── Attempt counters ({ET date: claimed runs}) — see layer 3 in the module docstring ──

_daily_attempts: Dict[date, int] = {}
_weekly_attempts: Dict[date, int] = {}
_stakes_attempts: Dict[date, int] = {}

# Lines about a switch logged once per process, not on every wake.
_logged_once: Set[str] = set()


def _log_once(key: str, level: int, msg: str, *args: Any) -> None:
    if key in _logged_once:
        return
    _logged_once.add(key)
    logger.log(level, msg, *args)


def _attempts_left(counter: Dict[date, int], today: date) -> int:
    for stale in [d for d in counter if d != today]:
        counter.pop(stale, None)
    return MAX_ATTEMPTS_PER_DAY - counter.get(today, 0)


def _ran_today(state: Mapping[str, Any], today: date) -> bool:
    return str(state.get("run_day") or "")[:10] == today.isoformat()


def _items_written(summary: Any, label: str) -> int:
    """The summary's ``items`` for the ledger: a non-negative int, else 0 (with a WARNING).

    A malformed count (a bool, a negative or fractional number, a string) must never raise
    inside the claim: that would record a run that finished — its writes done — as failed,
    and repeat it."""
    if not isinstance(summary, dict) or summary.get("items") is None:
        return 0
    raw = summary["items"]
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    logger.warning("trillion club %s: the run's items count %s is not a non-negative integer "
                   "— recorded as 0", label, repr(raw)[:80])
    return 0


async def _bounded_run(
    run: Callable[[datetime], Awaitable[Any]],
    now: datetime,
    *,
    timeout: Optional[float],
    label: str,
) -> Any:
    """``await run(now)``, cancelled if it is still going after ``timeout`` seconds (None = no
    backstop, the daily and weekly runs).

    A run the backstop cancelled comes back as a failed summary — an attempt used and a clear
    ``last_error`` — never as an exception, and the log line carries the traceback of the
    await it hung in. A ``TimeoutError`` the run raises ITSELF (the deadline did not fire)
    passes through like any other error, and a shutdown's cancel still propagates as
    ``CancelledError`` (``asyncio.timeout`` tells its own cancel from an outside one)."""
    deadline = asyncio.timeout(timeout)
    try:
        async with deadline:
            return await run(now)
    except TimeoutError:
        if not deadline.expired():
            raise
        logger.error(
            "trillion club %s: no result after %.0f s — cancelled by the scheduler's backstop "
            "(the attempt is counted; the next wake retries within the cap)", label, timeout,
            exc_info=True,
        )
        return {"ok": False, "items": 0,
                "failures": [f"{label}: cancelled by the scheduler after {timeout:.0f} s "
                             f"without a result"]}


# ── One wake ──────────────────────────────────────────────────────────────────────────


async def _tick(
    now: datetime,
    *,
    job: str,
    label: str,
    due: Callable[[datetime], bool],
    counter: Dict[date, int],
    stale_seconds: int,
    run: Callable[[datetime], Awaitable[Dict[str, Any]]],
    run_timeout: Optional[float] = None,
) -> Optional[float]:
    """One wake of any of the three loops. Returns seconds to sleep, or None = until the next
    slot. ``run_timeout`` is the backstop of ``_bounded_run`` (None = none)."""
    if not settings.TRILLION_CLUB_JOBS_ENABLED or not due(now):
        return None
    today = now.astimezone(ET).date()
    left = _attempts_left(counter, today)
    if left <= 0:
        return None

    from app.services.notification_jobs import claimed_scheduled_job, scheduled_job_state

    state = await asyncio.to_thread(scheduled_job_state, job)
    if state is None:
        logger.warning("trillion club %s: job state unreadable — skipping this wake (fail closed)",
                       label)
        return RETRY_SECONDS
    if not state.get("enabled", True):
        logger.info("trillion club %s: disabled by the operator kill switch "
                    "(notification_job_state.enabled) — skipping", label)
        return None
    if _ran_today(state, today):
        return None

    summary: Optional[Dict[str, Any]] = None
    async with claimed_scheduled_job(job, timezone_name=TIMEZONE_NAME,
                                     stale_seconds=stale_seconds) as claimed:
        if claimed is None:
            return RETRY_SECONDS          # another instance holds it (or it just finished)
        counter[today] = counter.get(today, 0) + 1
        attempt = counter[today]
        logger.info("trillion club %s: attempt %d/%d for %s", label, attempt,
                    MAX_ATTEMPTS_PER_DAY, today.isoformat())
        from app.services.trillion_club.jobs import failure_text

        summary = await _bounded_run(run, now, timeout=run_timeout, label=label)
        ok = bool(isinstance(summary, dict) and summary.get("ok") is True)
        claimed.items = _items_written(summary, label)
        claimed.success = ok
        if not ok:
            claimed.error = failure_text(summary) if isinstance(summary, dict) else "no summary"
    if claimed.success:
        return None
    if attempt >= MAX_ATTEMPTS_PER_DAY:
        logger.error(
            "trillion club %s: all %d attempts for %s failed — giving up until the next slot. "
            "Last failures: %s", label, MAX_ATTEMPTS_PER_DAY, today.isoformat(), claimed.error,
        )
        return None
    return RETRY_SECONDS


async def _daily_tick(now: datetime) -> Optional[float]:
    from app.services.trillion_club.jobs import run_daily

    return await _tick(now, job=JOB_TRILLION_CLUB_DAILY, label="daily", due=daily_due,
                       counter=_daily_attempts, stale_seconds=_DAILY_STALE_SECONDS,
                       run=run_daily)


async def _weekly_tick(now: datetime) -> Optional[float]:
    from app.services.trillion_club.jobs import run_weekly

    return await _tick(now, job=JOB_TRILLION_CLUB_WEEKLY, label="weekly", due=weekly_due,
                       counter=_weekly_attempts, stale_seconds=_WEEKLY_STALE_SECONDS,
                       run=run_weekly)


async def _stakes_tick(
    now: datetime,
    *,
    run: Optional[Callable[[datetime], Awaitable[Dict[str, Any]]]] = None,
) -> Optional[float]:
    """One wake of the stake-finder loop. Returns seconds to sleep, or None = the next slot.

    ``TRILLION_CLUB_STAKE_FINDER_ENABLED`` is read FIRST: while it is off the wake returns at
    once — no ledger read, no Supabase, no SEC — and ``stake_finder`` is never imported. On,
    the finder is imported lazily on any wake (a module that cannot load shows up loudly at the
    first wake after a deploy, not at 18:30), and the shared ``_tick`` applies the jobs flag,
    the 18:30 ET slot, the attempt cap, the kill switch, the day claim and the run backstop.
    ``run`` stands in for ``stake_finder.run_stakes`` in tests."""
    if not settings.TRILLION_CLUB_STAKE_FINDER_ENABLED:
        _log_once("stakes_off", logging.INFO,
                  "[tc-stakes-scheduler] TRILLION_CLUB_STAKE_FINDER_ENABLED is off — the stake "
                  "finder idles (migration 205 must be applied before it is switched on)")
        return None
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"stake finder tick: now must be timezone-aware, got {now!r}")
    if not settings.TRILLION_CLUB_JOBS_ENABLED:
        # `_tick` would return quietly; say once why a switched-on finder never runs.
        _log_once("stakes_jobs_off", logging.WARNING,
                  "[tc-stakes-scheduler] TRILLION_CLUB_STAKE_FINDER_ENABLED is on but "
                  "TRILLION_CLUB_JOBS_ENABLED is off — the stake finder will not run")
        return None
    if run is None:
        from app.services.trillion_club.stake_finder import run_stakes

        run = run_stakes
    return await _tick(now, job=JOB_TRILLION_CLUB_STAKE_FINDER, label="stake finder",
                       due=stakes_due, counter=_stakes_attempts,
                       stale_seconds=_STAKES_STALE_SECONDS, run=run,
                       run_timeout=_STAKES_RUN_BACKSTOP_SECONDS)


# ── Loops ─────────────────────────────────────────────────────────────────────────────


async def _loop(
    boot_delay: float,
    tick: Callable[[datetime], Awaitable[Optional[float]]],
    next_slot: Callable[[datetime], datetime],
    label: str,
) -> None:
    await asyncio.sleep(boot_delay)  # let the app finish booting
    while True:
        wait: Optional[float] = None
        try:
            wait = await tick(datetime.now(timezone.utc))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("trillion club %s loop: tick failed (%s: %s)", label,
                         type(e).__name__, e, exc_info=True)
            wait = RETRY_SECONDS
        now = datetime.now(timezone.utc)
        await asyncio.sleep(wait if wait is not None else _sleep_until(next_slot(now), now))


async def run_trillion_club_daily_loop() -> None:
    await _loop(DAILY_BOOT_DELAY_SECONDS, _daily_tick, next_daily_run, "daily")


async def run_trillion_club_weekly_loop() -> None:
    await _loop(WEEKLY_BOOT_DELAY_SECONDS, _weekly_tick, next_weekly_run, "weekly")


async def run_trillion_club_stake_finder_loop() -> None:
    await _loop(STAKES_BOOT_DELAY_SECONDS, _stakes_tick, next_stakes_run, "stake finder")


__all__ = [
    "JOB_TRILLION_CLUB_DAILY", "JOB_TRILLION_CLUB_WEEKLY", "JOB_TRILLION_CLUB_STAKE_FINDER",
    "TIMEZONE_NAME", "DAILY_TIME_ET", "WEEKLY_TIME_ET", "WEEKLY_WEEKDAY", "STAKES_TIME_ET",
    "RETRY_SECONDS", "MAX_ATTEMPTS_PER_DAY", "MAX_IDLE_SLEEP_SECONDS",
    "STAKES_BOOT_DELAY_SECONDS", "daily_due", "next_daily_run", "weekly_due", "next_weekly_run",
    "stakes_due", "next_stakes_run", "run_trillion_club_daily_loop",
    "run_trillion_club_weekly_loop", "run_trillion_club_stake_finder_loop",
]
