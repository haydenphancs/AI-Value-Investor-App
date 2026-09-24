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

Exactly-once and bounded, three layers:
  1. the ``notification_job_state`` day claim (migration 147 RPCs; rows seeded by migration
     175) in the ET day — one instance at a time, and a success ends the day;
  2. ``enabled`` on that row is the no-deploy kill switch. It is read FIRST, so a disabled
     job sleeps until its next slot instead of asking hourly. ``TRILLION_CLUB_JOBS_ENABLED``
     (default False) gates both loops entirely and is re-checked on every wake;
  3. a per-ET-day ATTEMPT counter in this module. The ledger cannot count failures —
     ``claim_scheduled_job`` resets ``runs_today`` whenever ``run_day`` differs from today,
     and ``run_day`` moves only on success — so without it a job that fails every time would
     re-run hourly all day. At most ``MAX_ATTEMPTS_PER_DAY`` claimed runs per job, per ET
     day, per instance.

``run.success`` is the summary's ``ok``: True only when EVERY stage of the run succeeded. A
partial run leaves ``run_day`` unset (with the failures as ``last_error``), and the next wake
retries within the attempt cap.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from app.config import settings
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

JOB_TRILLION_CLUB_DAILY = "trillion_club_daily"
JOB_TRILLION_CLUB_WEEKLY = "trillion_club_weekly"
TIMEZONE_NAME = "America/New_York"

DAILY_TIME_ET = dtime(7, 0)
WEEKLY_TIME_ET = dtime(8, 0)
WEEKLY_WEEKDAY = 0                      # Monday
RETRY_SECONDS = 3600
MAX_ATTEMPTS_PER_DAY = 3
MAX_IDLE_SLEEP_SECONDS = 6 * 3600       # re-read the enable flag at least this often
DAILY_BOOT_DELAY_SECONDS = 300          # after the theme loops (180 / 240 s)
WEEKLY_BOOT_DELAY_SECONDS = 360
# A run is a few dozen FMP calls (minutes); the stale window only has to outlast it so a
# deploy overlap cannot steal a run still in progress.
_DAILY_STALE_SECONDS = 1800
_WEEKLY_STALE_SECONDS = 3600


# ── Schedule math (pure; tested) ──────────────────────────────────────────────────────


def daily_due(now: datetime) -> bool:
    return now.astimezone(ET).time() >= DAILY_TIME_ET


def next_daily_run(now: datetime) -> datetime:
    """The next 07:00 ET strictly after ``now`` (UTC)."""
    d = now.astimezone(ET).date()
    for _ in range(3):
        candidate = datetime.combine(d, DAILY_TIME_ET, tzinfo=ET).astimezone(timezone.utc)
        if candidate > now:
            return candidate
        d += timedelta(days=1)
    raise RuntimeError(f"no 07:00 ET after {now.isoformat()}")  # unreachable


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


def _sleep_until(target: datetime, now: datetime) -> float:
    return max(60.0, min(MAX_IDLE_SLEEP_SECONDS, (target - now).total_seconds()))


# ── Attempt counters ({ET date: claimed runs}) — see layer 3 in the module docstring ──

_daily_attempts: Dict[date, int] = {}
_weekly_attempts: Dict[date, int] = {}


def _attempts_left(counter: Dict[date, int], today: date) -> int:
    for stale in [d for d in counter if d != today]:
        counter.pop(stale, None)
    return MAX_ATTEMPTS_PER_DAY - counter.get(today, 0)


def _ran_today(state: Mapping[str, Any], today: date) -> bool:
    return str(state.get("run_day") or "")[:10] == today.isoformat()


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
) -> Optional[float]:
    """One wake of either loop. Returns seconds to sleep, or None = until the next slot."""
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

        summary = await run(now)
        ok = bool(isinstance(summary, dict) and summary.get("ok") is True)
        claimed.items = int((summary or {}).get("items") or 0) if isinstance(summary, dict) else 0
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


__all__ = [
    "JOB_TRILLION_CLUB_DAILY", "JOB_TRILLION_CLUB_WEEKLY", "TIMEZONE_NAME", "DAILY_TIME_ET",
    "WEEKLY_TIME_ET", "WEEKLY_WEEKDAY", "RETRY_SECONDS", "MAX_ATTEMPTS_PER_DAY",
    "MAX_IDLE_SLEEP_SECONDS", "daily_due", "next_daily_run", "weekly_due", "next_weekly_run",
    "run_trillion_club_daily_loop", "run_trillion_club_weekly_loop",
]
