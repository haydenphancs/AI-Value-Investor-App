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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.api.error_response import ErrorCode, make_error_body
from app.database import get_supabase
from app.services.credit_service import CreditService

logger = logging.getLogger(__name__)


# A row is considered orphaned once it has sat in a non-terminal state this
# long. Deliberately well past the iOS client timeout (300s) and a realistic
# worst-case generation (~5-8 min) so a slow-but-alive report is NEVER
# false-refunded. Keep STRICTLY above settings.RESEARCH_PIPELINE_TIMEOUT_SECONDS
# (600) so the in-process wait_for always fails (and refunds) first with a
# clean error.
RECON_STUCK_THRESHOLD_SECONDS = 900  # 15 min

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


async def sweep_once(
    *,
    now: Optional[datetime] = None,
    supabase=None,
) -> Dict[str, int]:
    """Reconcile orphaned reports: find rows stuck past the threshold and
    refund them (idempotently, via `claim_and_mark_failed`).

    Covers the killed-worker case the in-process failure path cannot. `now`
    and `supabase` are injectable for tests. Returns
    {"stuck": <candidates>, "refunded": <claims won>}.
    """
    sb = supabase or get_supabase()
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        seconds=RECON_STUCK_THRESHOLD_SECONDS
    )
    cutoff_iso = cutoff.isoformat()

    def _find():
        return (
            sb.table("research_reports")
            .select("id, status, created_at")
            .in_("status", _CLAIMABLE_STATUSES)
            .eq("is_refunded", False)
            .gt("credits_charged", 0)
            .lt("created_at", cutoff_iso)
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

    rows = result.data or []
    refunded = 0
    for row in rows:
        blob = make_error_body(
            ErrorCode.REPORT_GENERATION_FAILED,
            message=(
                f"Report orphaned in {row.get('status')!r} for more than "
                f"{RECON_STUCK_THRESHOLD_SECONDS}s (worker died or hung)"
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

    if rows:
        logger.info(
            "research reconciliation sweep: %d stuck candidate(s), %d refunded",
            len(rows), refunded,
        )
    return {"stuck": len(rows), "refunded": refunded}
