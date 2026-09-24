"""Background loops for the Emerging Frontiers jobs (spawned from `main.lifespan`, Railway only).

* MONTHLY rotation — the first US trading day of each month at 18:30 ET (after the close,
  clear of the quarterly/weekly benchmark jobs that run on Sunday mornings UTC). A restart,
  a deploy or a failure inside the next 7 days catches up hourly. `run_month` is derived in
  ET from the anchor, never from `now()` in UTC (at 20:00 ET on the 31st it is already the
  1st in UTC).
* DAILY insights — every US trading day at 18:15 ET (performance vs the benchmark ETF and
  the "why it's moving" summary; `theme_insights_service`).

Exactly-once, three layers deep:
  1. the day-keyed, cross-instance `notification_job_state` claim (migration 147), in the ET
     day — no two replicas run the same job at once, and a success ends the day;
  2. for the rotation, the MONTH-level record in `theme_rotation_runs` (migration 174) — a
     day-keyed claim alone would run the rotation again on day 2 of the catch-up window;
  3. `enabled` on the ledger row is the no-deploy kill switch; `THEME_*_ENABLED` settings
     (default False until migration 174 is applied) gate the loops entirely.

A window that closes without a published rotation logs `theme rotation MISSED` at ERROR —
the lists stay as they were, and the app still says "Reviewed monthly", so it must be loud.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

from app.config import settings
from app.utils.market_hours import ET, is_trading_day

logger = logging.getLogger(__name__)

JOB_THEME_ROTATION_MONTHLY = "theme_rotation_monthly"
JOB_THEME_INSIGHTS_DAILY = "theme_insights_daily"

ROTATION_TIME_ET = dtime(18, 30)
INSIGHTS_TIME_ET = dtime(18, 15)
CATCHUP = timedelta(days=7)
RETRY_SECONDS = 3600
INSIGHTS_RETRY_SECONDS = 1800
MAX_IDLE_SLEEP_SECONDS = 6 * 3600        # re-read the enable flags at least this often
_ROTATION_STALE_SECONDS = 3 * 3600
_INSIGHTS_STALE_SECONDS = 3600
INSIGHTS_SAME_DAY_RETRIES = 2    # re-runs of the themes that failed for a reason that can clear


# ── Schedule math (pure; tested) ──────────────────────────────────────────────────────

def first_trading_day(year: int, month: int) -> date:
    d = date(year, month, 1)
    for _ in range(15):
        if is_trading_day(d):
            return d
        d += timedelta(days=1)
    raise RuntimeError(f"no trading day in the first 15 days of {year}-{month:02d}")


def rotation_anchor(year: int, month: int) -> datetime:
    d = first_trading_day(year, month)
    return datetime.combine(d, ROTATION_TIME_ET, tzinfo=ET).astimezone(timezone.utc)


def rotation_window(now: datetime) -> Optional[Tuple[date, datetime]]:
    """(run_month, anchor) while `now` is inside this month's [anchor, anchor + 7 days)."""
    local = now.astimezone(ET)
    anchor = rotation_anchor(local.year, local.month)
    if anchor <= now < anchor + CATCHUP:
        return date(local.year, local.month, 1), anchor
    return None


def next_rotation_anchor(now: datetime) -> datetime:
    local = now.astimezone(ET)
    anchor = rotation_anchor(local.year, local.month)
    if anchor > now:
        return anchor
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    return rotation_anchor(year, month)


def insights_due(now: datetime) -> bool:
    local = now.astimezone(ET)
    return is_trading_day(local.date()) and local.time() >= INSIGHTS_TIME_ET


def next_insights_run(now: datetime) -> datetime:
    """The next trading-day 18:15 ET strictly after `now`."""
    local = now.astimezone(ET)
    d = local.date()
    for _ in range(15):
        candidate = datetime.combine(d, INSIGHTS_TIME_ET, tzinfo=ET).astimezone(timezone.utc)
        if is_trading_day(d) and candidate > now:
            return candidate
        d += timedelta(days=1)
    raise RuntimeError(f"no trading day within 15 days of {local.date()}")


def _sleep_until(target: datetime, now: datetime) -> float:
    return max(60.0, min(MAX_IDLE_SLEEP_SECONDS, (target - now).total_seconds()))


# ── Monthly rotation ──────────────────────────────────────────────────────────────────

async def run_theme_rotation_loop() -> None:
    await asyncio.sleep(180)  # let the app finish booting
    missed_logged: Set[date] = set()
    while True:
        wait: Optional[float] = None
        try:
            wait = await _rotation_tick(datetime.now(timezone.utc), missed_logged)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("theme rotation loop: tick failed (%s: %s)", type(e).__name__, e,
                         exc_info=True)
            wait = RETRY_SECONDS
        now = datetime.now(timezone.utc)
        await asyncio.sleep(wait if wait is not None else _sleep_until(next_rotation_anchor(now), now))


async def _rotation_tick(now: datetime, missed_logged: Set[date]) -> Optional[float]:
    """One wake. Returns seconds to sleep, or None to sleep until the next anchor."""
    if not settings.THEME_ROTATION_ENABLED:
        return None
    from app.services.theme_rotation.service import get_theme_rotation_service

    service = get_theme_rotation_service()
    mode = "dry_run" if settings.THEME_ROTATION_DRY_RUN else "live"
    local = now.astimezone(ET)
    anchor = rotation_anchor(local.year, local.month)
    run_month = date(local.year, local.month, 1)

    if now >= anchor + CATCHUP:
        if run_month not in missed_logged and await service.month_done(run_month, mode) is False:
            missed_logged.add(run_month)
            # MISSED only when a run was actually ATTEMPTED and never completed. A month
            # with no run row at all means the job was switched on after this month's
            # window closed (a first deploy mid-month) — not a failure, so no ERROR.
            if await service.month_attempted(run_month, mode):
                logger.error("theme rotation MISSED %s (%s): the 7-day window closed without "
                             "a completed run — every theme keeps last month's list. See "
                             "theme_rotation_runs (status, error) for why.",
                             run_month.isoformat(), mode)
            else:
                logger.info("theme rotation: no %s run for %s (enabled after its window "
                            "closed); the next runs at %s", mode, run_month.isoformat(),
                            next_rotation_anchor(now).isoformat())
        return None
    if now < anchor:
        return None

    done = await service.month_done(run_month, mode)
    if done is None:
        logger.warning("theme rotation: run state unreadable — skipping this wake (fail closed)")
        return RETRY_SECONDS
    if done or await service.attempts_exhausted(run_month, mode):
        return None

    from app.services.notification_jobs import claimed_scheduled_job, scheduled_job_state

    state = await asyncio.to_thread(scheduled_job_state, JOB_THEME_ROTATION_MONTHLY)
    if state is not None and not state.get("enabled", True):
        logger.info("theme rotation: disabled by the operator kill switch — skipping")
        return None
    async with claimed_scheduled_job(JOB_THEME_ROTATION_MONTHLY, timezone_name="America/New_York",
                                     stale_seconds=_ROTATION_STALE_SECONDS) as run:
        if run is None:
            return RETRY_SECONDS      # another instance holds it, or it already ran today
        result = await service.run(run_month, mode)
        run.items = sum(len(p.after) for p in result.plans.values())
        run.success = result.status in ("published", "computed", "skipped")
    return RETRY_SECONDS


# ── Daily insights ────────────────────────────────────────────────────────────────────

async def run_theme_insights_loop() -> None:
    await asyncio.sleep(240)
    while True:
        wait: Optional[float] = None
        try:
            wait = await _insights_tick(datetime.now(timezone.utc))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("theme insights loop: tick failed (%s: %s)", type(e).__name__, e,
                         exc_info=True)
            wait = INSIGHTS_RETRY_SECONDS
        now = datetime.now(timezone.utc)
        await asyncio.sleep(wait if wait is not None else _sleep_until(next_insights_run(now), now))


# {ET date: (retries used, slugs still to retry)} — this instance's same-evening retry of
# themes that failed on a PARTIAL run. The day claim closes on a partial success (a whole
# re-run every 30 min would multiply the job's cost when a theme fails for good), so the
# instance that ran it retries just those themes, a bounded number of times.
_insights_retry: Dict[date, Tuple[int, List[str]]] = {}


def _retryable(summary: object) -> List[str]:
    """Failed themes whose reason can clear the same evening. A theme with no stocks
    fails every day — retrying it only spends calls."""
    failed = (summary or {}).get("themes_failed") if isinstance(summary, dict) else None
    out: List[str] = []
    for f in failed or []:
        if isinstance(f, dict) and isinstance(f.get("slug"), str) \
                and "no_constituents" not in str(f.get("error") or ""):
            out.append(f["slug"])
    return out


async def _insights_tick(now: datetime) -> Optional[float]:
    if not settings.THEME_INSIGHTS_ENABLED or not insights_due(now):
        return None
    from app.services.notification_jobs import claimed_scheduled_job
    from app.services.theme_insights_service import get_theme_insights_service

    today = now.astimezone(ET).date()
    for stale in [d for d in _insights_retry if d != today]:
        _insights_retry.pop(stale, None)
    pending = _insights_retry.get(today)
    if pending is not None:
        used, slugs = pending
        try:
            summary = await get_theme_insights_service().run_daily(now, slugs=slugs)
            still = _retryable(summary)
        except Exception as e:
            logger.warning("theme insights: retry of %s failed (%s: %s)", slugs,
                           type(e).__name__, e)
            still = slugs
        used += 1
        if still and used < INSIGHTS_SAME_DAY_RETRIES:
            _insights_retry[today] = (used, still)
            return INSIGHTS_RETRY_SECONDS
        _insights_retry.pop(today, None)
        if still:
            logger.warning("theme insights: %s still failing after %d retries — they keep "
                           "the previous session's row", still, used)
        return None

    async with claimed_scheduled_job(JOB_THEME_INSIGHTS_DAILY, timezone_name="America/New_York",
                                     stale_seconds=_INSIGHTS_STALE_SECONDS) as run:
        if run is None:
            # Done for this ET day, held by another instance (which may still fail), or
            # disabled — ask again later; the claim answers cheaply until midnight ET.
            return INSIGHTS_RETRY_SECONDS
        summary = await get_theme_insights_service().run_daily(now)
        run.items = int((summary or {}).get("themes_ok", 0) or 0)
        run.success = True
    retry = _retryable(summary)
    if retry and INSIGHTS_SAME_DAY_RETRIES > 0:
        _insights_retry[today] = (0, retry)
        return INSIGHTS_RETRY_SECONDS
    return None
