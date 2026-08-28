"""
Research-report refund safety net.

Generate Analysis charges credits UPFRONT (CreditService.try_charge in
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
from app.utils.supabase_errors import (
    is_transient_supabase_error,
    retry_idempotent_async,
)
from app.services.credit_service import CreditService, refund_did_not_happen

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

    # ⚠️ `ref_id` MUST match the one the CHARGE used — `research.py` passes the upper-cased
    # ticker (`precharge(..., ref_id=request.stock_id.upper())`). This used to pass
    # `report_id`, which is never equal to a ticker, and since migration 118 that mismatch is
    # no longer cosmetic: `refund_credits` looks the original spend up BY ref_id to learn which
    # pool it drained. With no match it takes the granted-first fallback, so a user whose
    # report was paid for out of PURCHASED credits got the refund into their GRANTED pool —
    # which `ensure_credit_period` then wipes at the month boundary. That silently destroys
    # credits bought with real money, i.e. exactly the App Store Guideline 3.1.1 violation the
    # two-pool design exists to prevent, on the primary failure path of the 20-credit action.
    ticker = (row.get("ticker") or "").upper() or None
    if not ticker:
        logger.warning(
            "claim_and_mark_failed: report %s has no ticker — refunding %s credits without a "
            "split lookup (granted-first fallback)", report_id, amount,
        )
    # Inspected, not wrapped in try/except. `refund_ledgered` NEVER raises — it catches and
    # returns None — so the `except Exception` that used to guard this call was dead code, and
    # this is the ONLY log line carrying `report_id` for a manual correction. It could not fire.
    #
    # We already won the claim (is_refunded=True), so we never retry — biased to under-refund
    # (safe) over double-refund. That bias is deliberate; what was wrong is that it was silent.
    try:
        outcome = CreditService().refund_ledgered(
            user_id, amount, reason="report_refund_reconciled", ref_id=ticker,
        )
    except Exception as e:
        # `refund_ledgered` does not raise, but CreditService() construction could, and this
        # runs inside a background sweep where an escape kills the whole pass. Normalised to
        # the same "did not happen" shape so there is ONE reporting path below.
        logger.warning(
            "claim_and_mark_failed: refund call raised for report %s (%s: %s)",
            report_id, type(e).__name__, e,
        )
        outcome = None
    # None = transport fault; the business no-ops carry their own outcome. Both mean the user
    # keeps neither the report nor the credits, and the CAS is spent.
    failed = refund_did_not_happen(outcome)
    if failed:
        logger.error(
            "REFUND LEAK: report %s claimed (is_refunded=True) but the refund of %s credits "
            "to user %s did not happen (outcome=%s, ref_id=%s) — the claim is spent so nothing "
            "will retry; manual correction needed. The client will still show '[Refunded]'.",
            report_id, amount, user_id,
            "rpc_failed" if outcome is None else outcome.get("outcome"), ticker,
        )
    else:
        logger.info(
            "Refunded %s credits for failed report %s (user %s, outcome=%s)",
            outcome.get("refunded", amount) if isinstance(outcome, dict) else amount,
            report_id, user_id,
            outcome.get("outcome") if isinstance(outcome, dict) else "legacy_int",
        )

    await _notify_report_failed(
        report_id=report_id,
        user_id=user_id,
        ticker=ticker,
        refunded=not failed,
        amount=amount,
    )
    return True


async def _notify_report_failed(
    *,
    report_id: str,
    user_id: str,
    ticker: Optional[str],
    refunded: bool,
    amount: int,
) -> None:
    """Tell the user the report they paid for did not finish.

    ⚠️ CALLED FROM EXACTLY ONE PLACE, and that placement IS the correctness argument —
    the same argument `research_service._notify_report_ready` makes in mirror image.

    This sits after the compare-and-set has been WON. `claim_and_mark_failed` is the one
    atomic claim: PostgREST folds the `is_refunded=False` guard into a single UPDATE, so
    of the worker's own `except` and the reconciliation sweep, exactly one caller ever
    reaches this line for a given report. Adding a second send from
    `research_service`'s `except` would double-notify on the ordinary failure path,
    because BOTH paths run — only one of them wins the claim.

    The BODY states the refund. That is the whole reason this notification earns its
    place: "it failed" without "you have your credits back" leaves the user checking
    their balance, which is the anxiety the alert was supposed to remove. When the
    refund did NOT happen (the REFUND LEAK branch above) we say nothing about credits
    rather than claiming a refund that did not land — a false reassurance here is worse
    than silence, and the leak is already logged for manual correction.

    Informational, never directive — this copy is a surface a regulator reads, the same
    standard the report-ready body is written to.

    Never raises. A push failure must not escape into the sweep and kill the rest of the
    pass, and it must never turn a completed refund into an exception.
    """
    if not user_id:
        return
    try:
        from app.services.notification_kinds import KIND_RESEARCH_FAILED, ticker_route
        from app.services.push_dispatch_service import get_push_dispatch_service

        symbol = (ticker or "").upper()
        if refunded:
            body = (
                f"We couldn't finish this analysis. Your {amount} credits have been "
                "returned — you can try again."
            )
        else:
            body = "We couldn't finish this analysis. Please try again."

        await get_push_dispatch_service().notify_users(
            [user_id],
            kind=KIND_RESEARCH_FAILED,
            title=f"{symbol} analysis didn't finish" if symbol else "Analysis didn't finish",
            body=body,
            # A report id is unique, so this is once-EVER with no date component —
            # mirroring `report:{report_id}` on the success side. A regenerated report is
            # a different row and legitimately notifies again.
            dedup_key=f"reportfail:{report_id}",
            # The ONE route builder. A hand-written `{"route": "ticker"}` dict in a sender
            # is what shipped `ticker_move` without an `asset_type` and sent every crypto
            # alert to the equity screen; a test forbids it now.
            route=ticker_route(KIND_RESEARCH_FAILED, symbol) if symbol else None,
        )
    except Exception as e:
        logger.warning(
            "report-failed push failed for report=%s user=%s (%s: %s) — the refund itself "
            "completed normally",
            report_id, user_id, type(e).__name__, e,
        )


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
        # Idempotent (a pure read), so a Supabase gateway blip is retried instead of
        # silently costing this sweep pass. That matters here more than most places:
        # this sweep is the ONLY mechanism that refunds credits for reports killed
        # mid-flight, so skipping a pass leaves paid-for orphans un-refunded.
        # retry_idempotent_async supplies the to_thread hop `_find` needs.
        result = await retry_idempotent_async(
            _find, what="research reconciliation lookup", logger=logger
        )
    except Exception as e:
        # Transient after retries → WARNING: the next sweep tick picks the orphans
        # up, nothing is lost. A genuine failure keeps ERROR + stack.
        _log = logger.warning if is_transient_supabase_error(e) else logger.error
        _log(
            "research reconciliation sweep: lookup failed: %s: %s",
            type(e).__name__, e,
            exc_info=not is_transient_supabase_error(e),
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
