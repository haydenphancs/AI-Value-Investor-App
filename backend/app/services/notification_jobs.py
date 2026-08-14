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
            "notification job %s: cursor read failed (%s: %s) — treating as no baseline",
            job, type(e).__name__, e,
        )
        return None
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
    granted = await asyncio.to_thread(claim, job)
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
            )
        )
