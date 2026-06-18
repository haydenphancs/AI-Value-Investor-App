"""
Research-report refund safety net.

Generate Analysis charges 5 credits UPFRONT (CreditService.try_charge in
`POST /research/generate`), then runs the pipeline in a fire-and-forget
background task. Two failure modes can otherwise strand a row in
`pending`/`processing` forever — charged but never refunded:

  1. The worker is killed mid-task (Railway deploy, OOM, crash) — no
     `except` ever runs.
  2. The pipeline hangs (Gemini/FMP never returns) — handled in-process by
     the `asyncio.wait_for` ceiling in research_service, but only while the
     worker is alive.

This module guarantees the invariant **"a report that does not deliver
gets its credits refunded — exactly once"** via two layers:

  - `claim_and_mark_failed(report_id, blob)` — the shared terminal-failure
    primitive. ONE atomic compare-and-set flips `is_refunded` false→true
    (and stamps status=failed + the structured error). The refund runs
    only for the caller that WON the claim, so the worker's own `except`
    and the sweep below can never both refund the same row.
  - `sweep_once()` — a periodic reconciliation pass (registered in the
    app lifespan) that finds rows older than RECON_STUCK_THRESHOLD_SECONDS
    still un-refunded and claims+refunds them. This is the ONLY mechanism
    that covers a killed worker.

Idempotency lives at the ROW level (`research_reports.is_refunded`), NOT in
the `refund_user_credits` RPC — that RPC only clamps `used` to >= 0 and
would hand back credits twice if called twice. Never refund without first
winning the row claim.

All Supabase calls run via asyncio.to_thread to avoid blocking the event
loop. Failures are logged, never raised — a transient DB blip must not
crash the sweep loop or the worker's failure path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.api.error_response import ErrorCode, make_error_body
from app.config import settings
from app.database import get_supabase
from app.services.credit_service import CreditService

logger = logging.getLogger(__name__)


# A STARTED report (processing_started_at set) that hasn't finished this long
# after work began is hung/dead. Kept STRICTLY above
# settings.RESEARCH_PIPELINE_TIMEOUT_SECONDS (600) so the in-process wait_for
# always fails (and refunds) first with a clean error. Because we now age off
# processing_started_at (not created_at), queue-wait time no longer counts —
# a report waiting behind the agent semaphore is NOT prematurely refunded.
RECON_STUCK_THRESHOLD_SECONDS = 900  # 15 min after work STARTED

# A NEVER-STARTED row (processing_started_at NULL) is either still legitimately
# queued behind the semaphore OR orphaned (its fire-and-forget task died before
# acquiring a slot — e.g. a Railway redeploy). We can't tell which from a
# timestamp, so we only reconcile it after a long abandon window. The window is
# DERIVED from the actual caps so it always exceeds the worst-case legitimate
# queue drain — a back-of-queue report waits for ~MAX_GLOBAL_INFLIGHT_REPORTS
# ahead, draining MAX_CONCURRENT_AGENT_RUNS-wide, each possibly running to the
# full RESEARCH_PIPELINE_TIMEOUT_SECONDS ceiling — plus a margin. This guarantees
# a still-queued report is never false-refunded, even at full saturation AND
# even if the concurrency caps are retuned later (e.g. raised at Gemini Tier 2).
def _worst_case_queue_drain_seconds() -> int:
    slots = max(1, settings.MAX_CONCURRENT_AGENT_RUNS)
    backlog = max(0, settings.MAX_GLOBAL_INFLIGHT_REPORTS - slots)
    batches = math.ceil(backlog / slots)
    return batches * settings.RESEARCH_PIPELINE_TIMEOUT_SECONDS


# Floor of 1 hr; otherwise the derived worst-case drain + a 10-min margin.
RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS = max(
    3600, _worst_case_queue_drain_seconds() + 600
)

# How often the lifespan sweep runs. Worst-case time-to-refund for a killed
# worker = RECON_STUCK_THRESHOLD + RECON_SWEEP_INTERVAL (~20 min).
RECON_SWEEP_INTERVAL_SECONDS = 300  # 5 min

# Non-terminal-or-unrefunded statuses eligible for claim. 'failed' is
# INCLUDED so the worker's `except` path still refunds even though
# ResearchService.generate_report already stamped status='failed' before
# re-raising — and so a row stranded in 'failed' + is_refunded=False (worker
# died between mark-failed and refund) is still reconciled. 'completed' and
# 'deleted' are excluded: a delivered report is never refunded.
_CLAIMABLE_STATUSES = ["pending", "processing", "failed"]


async def claim_and_mark_failed(
    report_id: str,
    error_blob: Dict[str, Any],
    *,
    supabase=None,
) -> bool:
    """Atomically mark a research report failed and refund its credits — once.

    Issues a single UPDATE guarded by `is_refunded=False` (+ a claimable
    status + credits_charged>0). PostgREST folds every filter into the WHERE
    of one statement, so concurrent callers (the worker's `except` and the
    reconciliation sweep) can never both flip false→true — exactly one wins.
    The refund runs only for the winner, reading the authoritative
    `user_id`/`credits_charged` from the row the UPDATE returned.

    Returns True if THIS call claimed the row (and attempted the refund),
    False if it was already terminal/refunded (no-op).
    """
    sb = supabase or get_supabase()

    def _claim():
        return (
            sb.table("research_reports")
            .update(
                {
                    "status": "failed",
                    "is_refunded": True,
                    "progress": 0,
                    "error_message": json.dumps(error_blob),
                }
            )
            .eq("id", report_id)
            .eq("is_refunded", False)            # compare-and-set guard
            .in_("status", _CLAIMABLE_STATUSES)  # never touch completed/deleted
            .gt("credits_charged", 0)            # nothing to refund otherwise
            .execute()
        )

    try:
        result = await asyncio.to_thread(_claim)
    except Exception as e:
        logger.error(
            "claim_and_mark_failed: UPDATE failed for report %s: %s: %s",
            report_id, type(e).__name__, e,
        )
        return False

    rows = result.data or []
    if not rows:
        # Lost the claim, or the row was already terminal/refunded.
        return False

    row = rows[0]
    user_id = row.get("user_id")
    amount = row.get("credits_charged") or CreditService.DEEP_RESEARCH_COST
    if not user_id:
        logger.error(
            "claim_and_mark_failed: claimed report %s has no user_id — "
            "cannot refund %s credits", report_id, amount,
        )
        return True

    try:
        CreditService().refund(user_id, amount)
        logger.info(
            "Refunded %s credits for failed report %s (user %s)",
            amount, report_id, user_id,
        )
    except Exception as e:
        # We already won the claim (is_refunded=True), so we never retry —
        # biased to under-refund (safe) over double-refund. Log loudly with
        # everything needed for a manual credit correction.
        logger.error(
            "REFUND LEAK: report %s claimed (is_refunded=True) but refund of "
            "%s credits to user %s FAILED: %s: %s — manual correction needed",
            report_id, amount, user_id, type(e).__name__, e,
        )
    return True


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp from Supabase, or None if absent/malformed."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_orphaned(
    row: Dict[str, Any], started_cutoff: datetime, abandoned_cutoff: datetime
) -> bool:
    """Precise per-row staleness rule (the coarse SQL filter only narrows the set).

    - STARTED (processing_started_at set): stuck if it began work before
      `started_cutoff` (hung past RECON_STUCK_THRESHOLD after work started).
    - NEVER-STARTED (processing_started_at NULL): stuck ONLY if created before
      `abandoned_cutoff` — i.e. it's been queued/orphaned far longer than any
      legitimate semaphore wait, so its worker must be dead.
    """
    started = _parse_ts(row.get("processing_started_at"))
    if started is not None:
        return started < started_cutoff
    created = _parse_ts(row.get("created_at"))
    if created is None:
        return True  # malformed timestamp on a claimable+old row → reconcile
    return created < abandoned_cutoff


async def sweep_once(
    *,
    now: Optional[datetime] = None,
    supabase=None,
) -> Dict[str, int]:
    """Reconcile orphaned reports: find rows stuck past the threshold and
    refund them (idempotently, via `claim_and_mark_failed`).

    Ages a STARTED report off `processing_started_at` (real work-start) and a
    NEVER-STARTED row off `created_at` only after the long abandon window — so a
    report legitimately queued behind the agent semaphore is never false-refunded.
    Covers the killed-worker case the in-process failure path cannot. `now` and
    `supabase` are injectable for tests. Returns
    {"stuck": <candidates>, "refunded": <claims won>}.
    """
    sb = supabase or get_supabase()
    now = now or datetime.now(timezone.utc)
    started_cutoff = now - timedelta(seconds=RECON_STUCK_THRESHOLD_SECONDS)
    abandoned_cutoff = now - timedelta(seconds=RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS)
    # Coarse SQL pre-filter on created_at using the SMALLER threshold — a
    # started-but-hung row always has created_at older than its
    # processing_started_at, so this never misses a candidate; the precise rule
    # (`_is_orphaned`) is applied per row below.
    coarse_cutoff_iso = started_cutoff.isoformat()

    def _find():
        return (
            sb.table("research_reports")
            .select("id, status, created_at, processing_started_at")
            .in_("status", _CLAIMABLE_STATUSES)
            .eq("is_refunded", False)
            .gt("credits_charged", 0)
            .lt("created_at", coarse_cutoff_iso)
            .order("created_at", desc=False)
            .limit(200)
            .execute()
        )

    try:
        result = await asyncio.to_thread(_find)
    except Exception as e:
        logger.error(
            "research reconciliation sweep: lookup failed: %s: %s",
            type(e).__name__, e,
        )
        return {"stuck": 0, "refunded": 0}

    candidates = [
        row for row in (result.data or [])
        if _is_orphaned(row, started_cutoff, abandoned_cutoff)
    ]
    refunded = 0
    for row in candidates:
        started = row.get("processing_started_at")
        reason = (
            f"started {RECON_STUCK_THRESHOLD_SECONDS}s+ ago, never finished"
            if started else
            f"queued {RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS}s+ without starting"
        )
        blob = make_error_body(
            ErrorCode.REPORT_GENERATION_FAILED,
            message=(
                f"Report orphaned in {row.get('status')!r} ({reason}) — "
                f"worker died or hung"
            ),
            user_message=(
                "This analysis didn't finish in time, so your credits were "
                "refunded. Please try again."
            ),
            details={
                "report_id": row.get("id"),
                "step": "reconciliation_sweep",
                "stuck_status": row.get("status"),
            },
        )
        if await claim_and_mark_failed(row["id"], blob, supabase=sb):
            refunded += 1

    if candidates:
        logger.info(
            "research reconciliation sweep: %d stuck candidate(s), %d refunded",
            len(candidates), refunded,
        )
    return {"stuck": len(candidates), "refunded": refunded}
