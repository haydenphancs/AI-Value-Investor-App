"""Cross-instance claim for the scheduled notification senders.

Railway may run more than one instance. Without a claim, every one of them would walk
the same 200-ticker universe at 18:00 ET and spend the same ~200 FMP calls — and while
the dedup claim in `notification_events` would stop the duplicate BUZZ, it does nothing
about the duplicate WORK. This is the first line of defence; the dedup key is the last.

Thin wrapper over the two RPCs in migration 120. The interesting parts are not the SQL
calls but the failure semantics around them:

  * A claim failure is treated as "someone else has it" — NOT as "go ahead anyway".
    Fail-closed here costs at most one skipped run of a job that wakes hourly; failing
    open costs a duplicated FMP spend and, on a bad day, a duplicated fan-out.
  * The release runs in `finally` + `asyncio.shield`. `CancelledError` is a
    `BaseException`, so a plain `except Exception` misses a deploy-time cancel entirely
    and the claim then sits parked for the full stale window — meaning a redeploy at
    18:00 could silently skip that whole day's notifications. `updates_insight_sweeper`
    documents the same trap.
  * `success=False` deliberately leaves `run_day` untouched so the next wake retries
    the same ET day. That is how a transient FMP failure becomes a retry instead of a
    silently skipped day.
  * The release is stamped with the CLAIM's time, not the finish time. The SQL derives
    `run_day` from `p_now`, so a run claimed at 23:50 that succeeded at 00:10 used to
    record TOMORROW as done and the claim then refused tomorrow's run all day (theme
    insights retry until midnight ET; a sender after a late deploy; a whale sweep
    claimed at 23:5x UTC). Side effect: `last_run_at` is the run's START.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from app.config import settings
from app.database import get_supabase

logger = logging.getLogger(__name__)


class JobStateUnreadable(RuntimeError):
    """The job ledger could not be read. Distinct from "no baseline" (`None`): a caller
    that treats a failed read as "never ran" silently re-baselines and skips rows."""


# Job names. Also the `notification_job_state.job` primary key, so renaming one orphans
# its state row (and grants one extra run on the changeover day — harmless, but say so).
JOB_EARNINGS = "earnings"
JOB_SMART_MONEY = "smart_money"
JOB_PROFILE_MATCH = "profile_match"


class NotificationJobResult:
    """Mutable handle a claimed job fills in as it works.

    Passed to the body rather than returned from it so the `finally` in `claimed_job`
    can still report partial progress when the body raises — a job that fanned out to
    300 users and then died should not record zero.
    """

    __slots__ = ("notified", "cursor", "success", "error")

    def __init__(self) -> None:
        self.notified: int = 0
        # Advanced ONLY on success (enforced in SQL too). A failed pass must never move
        # the high-water mark past rows it did not actually process.
        self.cursor: Optional[datetime] = None
        self.success: bool = False
        self.error: Optional[str] = None


def _sb():
    return get_supabase()


def claim(job: str, *, now: Optional[datetime] = None) -> bool:
    """Try to take the daily claim for `job`. True = it's yours.

    Returns False on ANY error. See the module docstring: fail-closed costs one skipped
    hourly wake, fail-open costs a duplicated FMP spend across instances.
    """
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    try:
        result = _sb().rpc(
            "claim_notification_job",
            {
                "p_job": job,
                "p_now": stamp,
                "p_stale_seconds": settings.NOTIFICATION_JOB_STALE_SECONDS,
            },
        ).execute()
        return bool(result.data)
    except Exception as e:
        logger.warning(
            "notification job %s: claim failed (%s: %s) — skipping this wake",
            job, type(e).__name__, e,
        )
        return False


def finish(
    job: str,
    *,
    success: bool,
    notified: int = 0,
    cursor: Optional[datetime] = None,
    error: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    """Release the claim and record the outcome. Best-effort, never raises.

    A failure here is non-fatal but LOUD: the claim then sits until the stale window
    expires, which delays (never duplicates) the next run.
    """
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    try:
        _sb().rpc(
            "finish_notification_job",
            {
                "p_job": job,
                "p_now": stamp,
                "p_success": success,
                "p_notified": int(notified or 0),
                "p_cursor": cursor.isoformat() if cursor else None,
                "p_error": (error or None) and str(error)[:500],
            },
        ).execute()
    except Exception as e:
        logger.warning(
            "notification job %s: finish failed (%s: %s) — the claim will free itself "
            "after NOTIFICATION_JOB_STALE_SECONDS",
            job, type(e).__name__, e,
        )


def last_cursor(job: str) -> Optional[datetime]:
    """The job's ingest high-water mark, or None if it has never succeeded.

    None is meaningful to the whale sender: it means "no baseline", and the sender
    seeds a conservative recent window rather than notifying on the entire table.

    A FAILED read raises `JobStateUnreadable` instead. It used to return None too, so one
    Supabase blip re-baselined the whale pass to "the last 24 h" and the successful run
    then overwrote the real cursor — every row between the two was never evaluated. The
    raise fails the claimed run, which the next hourly wake retries.
    """
    try:
        rows = (
            _sb().table("notification_job_state")
            .select("last_cursor")
            .eq("job", job)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception as e:
        logger.warning(
            "notification job %s: cursor read failed (%s: %s) — failing this run so the "
            "next wake retries it", job, type(e).__name__, e,
        )
        raise JobStateUnreadable(f"{job}: cursor read failed: {type(e).__name__}: {e}") from e
    if not rows or not rows[0].get("last_cursor"):
        return None
    raw = str(rows[0]["last_cursor"]).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        logger.warning(
            "notification job %s: unparseable last_cursor %r — treating as no baseline",
            job, raw,
        )
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@contextlib.asynccontextmanager
async def claimed_job(job: str) -> AsyncIterator[Optional[NotificationJobResult]]:
    """Hold the daily claim for the duration of the block.

    Yields a `NotificationJobResult` when the claim was granted, or ``None`` when it
    was not — so the caller writes::

        async with claimed_job(JOB_EARNINGS) as run:
            if run is None:
                return
            ...
            run.notified = sent
            run.success = True

    Marking `success` is an explicit act: the default is False, so a body that returns
    early or raises leaves `run_day` unset and the next hourly wake retries the same ET
    day. Silence must not be mistaken for a completed run.
    """
    # One timestamp for the claim AND the release, so `run_day` is the day the run was
    # claimed even when it finishes after midnight (module docstring).
    claimed_at = datetime.now(timezone.utc)
    granted = await asyncio.to_thread(claim, job, now=claimed_at)
    if not granted:
        yield None
        return

    result = NotificationJobResult()
    try:
        yield result
    except asyncio.CancelledError:
        # A redeploy mid-run. Record it honestly as a failure so the day is retried,
        # then let the cancellation continue.
        result.success = False
        result.error = "cancelled (shutdown)"
        raise
    except Exception as e:
        result.success = False
        result.error = f"{type(e).__name__}: {e}"
        raise
    finally:
        # SHIELDED. `CancelledError` is a BaseException and would otherwise abort this
        # release, parking the claim for the full stale window — i.e. a redeploy at the
        # job's scheduled hour would silently skip that day's notifications entirely.
        await asyncio.shield(
            asyncio.to_thread(
                finish,
                job,
                success=result.success,
                notified=result.notified,
                cursor=result.cursor,
                error=result.error,
                now=claimed_at,
            )
        )


# ─────────────────────────────────────────────────────────────────────────────────────
# Generic scheduled jobs (non-notification), added in migration 147.
#
# Same table, same claim discipline, two differences that matter:
#
#   * The day boundary is a PARAMETER, not hardcoded to America/New_York. The whale full
#     hydration is specified in UTC and runs at 02:00 UTC; judged on an ET calendar that
#     is 21:00/22:00 the PREVIOUS day, so the marker would misdate every run, and any
#     future shift of the schedule past 04:00 UTC would put two consecutive daily runs on
#     one ET day and silently suppress one of them.
#   * It records `items_written` — how many rows the run actually wrote. A sweep that ran
#     and legitimately wrote nothing is indistinguishable from one that never ran without
#     it, and both the operator and the latency measurement need to tell them apart.
#
# The notification RPCs are deliberately NOT reused or modified here — see the migration
# header for why (argument ambiguity, and a DROP would discard their GRANTs).
# ─────────────────────────────────────────────────────────────────────────────────────

JOB_WHALE_HYDRATION_FULL = "whale_hydration_full"


class ScheduledJobResult:
    """Mutable handle a claimed scheduled job fills in as it works.

    Mirrors `NotificationJobResult`, but counts rows WRITTEN rather than notifications
    sent. Passed to the body rather than returned from it so the `finally` in
    `claimed_scheduled_job` can still record partial progress when the body raises — a
    sweep that wrote 30 whales and then died must not record zero.
    """

    __slots__ = ("items", "success", "error")

    def __init__(self) -> None:
        self.items: int = 0
        self.success: bool = False
        self.error: Optional[str] = None


def claim_scheduled(
    job: str, *, timezone_name: str = "UTC", now: Optional[datetime] = None,
    stale_seconds: Optional[int] = None,
) -> bool:
    """Try to take the daily claim for `job`. True = it's yours.

    Returns False on ANY error, for the same fail-closed reason as `claim`: a skipped
    wake is cheap, a duplicated sweep is not.

    `stale_seconds` overrides `NOTIFICATION_JOB_STALE_SECONDS` (900 s) for a job whose
    single run is LONGER than that: Railway overlaps the old and new instance on a deploy,
    and a 15-minute window would let the new one steal a phase the old one is still
    running (the quarterly moat recompute is 60-90 min).
    """
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    stale = settings.NOTIFICATION_JOB_STALE_SECONDS if stale_seconds is None else int(stale_seconds)
    try:
        result = _sb().rpc(
            "claim_scheduled_job",
            {
                "p_job": job,
                "p_now": stamp,
                "p_stale_seconds": stale,
                "p_timezone": timezone_name,
            },
        ).execute()
        return bool(result.data)
    except Exception as e:
        logger.warning(
            "scheduled job %s: claim failed (%s: %s) — skipping this wake",
            job, type(e).__name__, e,
        )
        return False


def finish_scheduled(
    job: str,
    *,
    success: bool,
    items: int = 0,
    error: Optional[str] = None,
    timezone_name: str = "UTC",
    now: Optional[datetime] = None,
) -> None:
    """Release the claim and record the outcome. Best-effort, never raises.

    A failure here is non-fatal but LOUD: the claim then sits until the stale window
    expires, which delays (never duplicates) the next run.
    """
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    try:
        _sb().rpc(
            "finish_scheduled_job",
            {
                "p_job": job,
                "p_now": stamp,
                "p_success": success,
                "p_items": int(items or 0),
                "p_error": (error or None) and str(error)[:500],
                "p_timezone": timezone_name,
            },
        ).execute()
    except Exception as e:
        logger.warning(
            "scheduled job %s: finish failed (%s: %s) — the claim will free itself "
            "after NOTIFICATION_JOB_STALE_SECONDS",
            job, type(e).__name__, e,
        )


def scheduled_job_state(job: str) -> Optional[dict]:
    """The job's ledger row (`run_day`, `claim_at`, `enabled`), or None when unreadable.

    `claim_scheduled` answers one bit — yours or not — and "not" covers three cases a
    long-running chain must tell apart: it already RAN today (skip), it is HELD by another
    instance right now (wait — a deploy overlaps the old and new process), or the claim
    RPC failed (fail closed). `main._run_claimed_phase` reads this row to decide.
    """
    try:
        result = (
            _sb().table("notification_job_state")
            .select("job, run_day, claim_at, enabled")
            .eq("job", job)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.warning(
            "scheduled job %s: state read failed (%s: %s)", job, type(e).__name__, e,
        )
        return None
    rows = list(getattr(result, "data", None) or [])
    return dict(rows[0]) if rows else {"job": job, "run_day": None, "claim_at": None, "enabled": True}


@contextlib.asynccontextmanager
async def claimed_scheduled_job(
    job: str, *, timezone_name: str = "UTC", stale_seconds: Optional[int] = None,
) -> AsyncIterator[Optional[ScheduledJobResult]]:
    """Hold the daily claim for the duration of the block.

    Yields a `ScheduledJobResult` when the claim was granted, or ``None`` when it was
    not::

        async with claimed_scheduled_job(JOB_WHALE_HYDRATION_FULL) as run:
            if run is None:
                return
            stats = await hydrator.run()
            run.items = stats["processed"]
            run.success = True

    Marking `success` is an explicit act: the default is False, so a body that returns
    early or raises leaves `run_day` unset and the next wake retries the same day.
    """
    claimed_at = datetime.now(timezone.utc)  # stamps the release too — see `claimed_job`
    granted = await asyncio.to_thread(
        claim_scheduled, job, timezone_name=timezone_name, stale_seconds=stale_seconds,
        now=claimed_at,
    )
    if not granted:
        yield None
        return

    result = ScheduledJobResult()
    try:
        yield result
    except asyncio.CancelledError:
        # A redeploy mid-run. Record it honestly as a failure so the day is retried —
        # this is the exact case the old clock-inferred seed got wrong, by assuming the
        # run had completed and skipping the rest of the day.
        result.success = False
        result.error = "cancelled (shutdown)"
        raise
    except Exception as e:
        result.success = False
        result.error = f"{type(e).__name__}: {e}"
        raise
    finally:
        # SHIELDED, for the same reason as `claimed_job`: `CancelledError` is a
        # BaseException and would otherwise abort the release, parking the claim for the
        # full stale window.
        await asyncio.shield(
            asyncio.to_thread(
                finish_scheduled,
                job,
                success=result.success,
                items=result.items,
                error=result.error,
                timezone_name=timezone_name,
                now=claimed_at,
            )
        )
