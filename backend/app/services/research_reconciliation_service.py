"""
Research-report refund safety net.

Generate Analysis charges credits UPFRONT (CreditService.precharge in
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

At-most-once lives at the ROW level (`research_reports.is_refunded`). The
refund RPC is ALSO replay-safe since every deep-door charge carries its own
`report_charge_ref(ticker, report_id)` (2026-10-12): a second refund of the same
report answers `already_refunded`. That is what lets a claim whose reply was lost
be settled from a re-read (below) — but never refund without a claim, or without
proof that one committed.

`is_refunded` is the CLAIM, set before the refund RPC runs — so it cannot say whether
the refund LANDED. `research_reports.refund_outcome` (migration 195) does: every refund
site stamps it AFTER its refund, best-effort, in the same thread body
(`record_refund_outcome`), never inside the CAS. The list serves it as
`refund_confirmed` (iOS "Refund pending"), and `retry_unconfirmed_refunds_once` — the
second step of the same 5-minute loop — re-checks, only on the unique per-report ref, a
refund whose RPC failed (ONE retry) or whose stamp never landed (until an answer other than
a transport fault labels it). First refunds stay governed by the `is_refunded` CAS; the
retry is safe only because the per-report ref makes a replay converge.

All Supabase calls run via asyncio.to_thread to avoid blocking the event
loop. Failures are logged, never raised — a transient DB blip must not
crash the sweep loop or the worker's failure path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.api.error_response import ErrorCode, make_error_body
from app.config import settings
from app.database import get_supabase
from app.utils.supabase_errors import (
    is_transient_supabase_error,
    is_unknown_column_error,
    retry_idempotent_async,
    retry_idempotent_sync,
)
from app.services.credit_service import (
    REFUND_SETTLED_OUTCOMES,
    ChargeRef,
    CreditService,
    refund_did_not_happen,
    report_charge_ref,
)

logger = logging.getLogger(__name__)


# ── refund_outcome (migration 195): did the refund behind `is_refunded` LAND? ──────────
#
# `is_refunded` is the one-shot CAS, flipped BEFORE `refund_credits` runs, so on its own it
# cannot tell a refund that landed from one whose RPC failed — and iOS used to show
# "Refunded 20 credits" off it either way. This column records what the refund actually did.
# It is written ONLY by `record_refund_outcome`, after the refund, in the refund's own thread
# body; NEVER inside an `is_refunded` compare-and-set (a missing column would then break the
# money guard itself — the delete path turns any CAS exception into an endless 409).
REFUND_OUTCOME_COLUMN = "refund_outcome"

#: The round trip failed on the unique per-report ref (`ChargeRef`) — or the call raised
#: before it was sent. The ONLY label (with a missing stamp) the retry pass may retry.
LABEL_RPC_FAILED = "rpc_failed"
#: The round trip failed on a ref a retry cannot safely replay: the legacy bare ticker (a
#: pre-2026-10-12 charge, refunded through the CAS winner's fallback — that refund may have
#: committed with its reply lost) or no ref at all. NEVER retried; the owner checks the ledger.
LABEL_LEGACY_RPC_FAILED = "legacy_rpc_failed"
#: The retry pass's own attempt failed too. Never retried again: at most ONE automatic retry.
LABEL_RETRY_RPC_FAILED = "retry_rpc_failed"
#: A dict answer with no usable `outcome` — owed, exactly as `refund_did_not_happen` reads it.
LABEL_UNKNOWN = "unknown"
#: The owner corrected the balance by hand (OWNER_TASKS runbook). Written by SQL, never here.
LABEL_MANUAL = "manual"
#: A refund on a row CHARGED under the old bare-ticker ref, which no replay can re-check (the
#: per-report ref finds no debit whether or not its bare-ticker refund landed) — today's chip
#: kept. Written by migration 195's backfill and by the retry pass's legacy inference.
LABEL_LEGACY = "legacy"
#: The RPC refunded LESS than the charge (a month-boundary cap): still owed. Its reversal row
#: exists, so a later replay answers `already_refunded` — which must never paper over it.
LABEL_PARTIAL = "partial"
_LABEL_ALREADY_REFUNDED = "already_refunded"

#: Labels that mean "the credits are back". Mirrors `refund_did_not_happen` exactly (a test
#: walks every outcome shape through both), plus the two non-RPC labels that also settle.
REFUND_CONFIRMED_LABELS = frozenset(REFUND_SETTLED_OUTCOMES) | {LABEL_MANUAL, LABEL_LEGACY}

_REFUND_OUTCOME_RECHECK_SECONDS = 600.0
_LABEL_MAX_CHARS = 40

# Process-local latch: when the column was last found MISSING (code deployed ahead of 195).
# While set, no stamp is attempted and the list does not select the column; it re-checks
# after `_REFUND_OUTCOME_RECHECK_SECONDS`, so applying 195 needs no restart. Written from
# worker threads, hence the lock.
_refund_outcome_lock = threading.Lock()
_refund_outcome_missing_at: Optional[float] = None
_refund_outcome_warned = False

_ANY = object()  # `record_refund_outcome(only_if=...)` default: no CAS on the current label


def refund_outcome_label(outcome: Any, *, replayable: bool = True) -> str:
    """The `refund_outcome` label for a `refund_ledgered` result.

    `None` (a transport fault) is `rpc_failed` when the call carried the unique per-report
    ref (`replayable`), else `legacy_rpc_failed`. A dict is its `outcome` (cut to 40 chars),
    or `unknown` when that is absent, blank or not a string. Anything else is the pre-142 bare
    integer, which `refund_did_not_happen` reads as "it happened" — so `refunded`. Kept in
    lock-step with that predicate: `refund_confirmed_from_label` of this == not
    `refund_did_not_happen` (pinned by tests/test_research_refund_outcome.py).
    """
    if outcome is None:
        return LABEL_RPC_FAILED if replayable else LABEL_LEGACY_RPC_FAILED
    if isinstance(outcome, dict):
        name = outcome.get("outcome")
        if not isinstance(name, str) or not name.strip():
            return LABEL_UNKNOWN
        # Never stripped or re-cased: " refunded" is OWED to the predicate, so it must not
        # become the confirmed "refunded" here.
        return name[:_LABEL_MAX_CHARS]
    return "refunded"


def refund_confirmed_from_label(
    label: Optional[str], *, is_refunded: bool = True, column_known: bool = True,
) -> Optional[bool]:
    """What the Reports list serves as `refund_confirmed`.

    * a label → True when it is in REFUND_CONFIRMED_LABELS, False for any other (owed);
    * no label on a CLAIMED row (`is_refunded`) → False while the column is known to exist
      ("Refund pending": the stamp never landed — a lost reply, a SIGKILL between the CAS and
      the RPC — and the retry pass is the only thing that will find out); None only while the
      column is latched missing, which keeps an old database's chip unchanged;
    * no label on an UNCLAIMED row → None: no refund was claimed, there is nothing to confirm.
    """
    if label is not None:
        return str(label) in REFUND_CONFIRMED_LABELS
    if not is_refunded:
        return None
    return False if column_known else None


def refund_outcome_column_available() -> bool:
    """False only within `_REFUND_OUTCOME_RECHECK_SECONDS` of the column being found missing."""
    missing_at = _refund_outcome_missing_at
    if missing_at is None:
        return True
    return time.monotonic() - missing_at >= _REFUND_OUTCOME_RECHECK_SECONDS


def note_refund_outcome_column_missing(where: str) -> None:
    """Latch "the column is missing" (one WARNING per process, INFO on a later re-latch)."""
    global _refund_outcome_missing_at, _refund_outcome_warned
    with _refund_outcome_lock:
        first = not _refund_outcome_warned
        _refund_outcome_missing_at = time.monotonic()
        _refund_outcome_warned = True
    (logger.warning if first else logger.info)(
        "research_reports.%s missing (%s) — apply migration 195; refund outcomes are not "
        "recorded and the Reports list serves refund_confirmed=null until it lands "
        "(re-checked every %d min)",
        REFUND_OUTCOME_COLUMN, where, int(_REFUND_OUTCOME_RECHECK_SECONDS // 60),
    )


def note_refund_outcome_column_present() -> None:
    global _refund_outcome_missing_at
    if _refund_outcome_missing_at is None:
        return
    with _refund_outcome_lock:
        if _refund_outcome_missing_at is not None:
            _refund_outcome_missing_at = None
            logger.info("research_reports.%s is present again — recording refund outcomes",
                        REFUND_OUTCOME_COLUMN)


def _reset_refund_outcome_latch() -> None:
    """Tests only (backend/conftest.py): the latch is module-global state."""
    global _refund_outcome_missing_at, _refund_outcome_warned
    with _refund_outcome_lock:
        _refund_outcome_missing_at = None
        _refund_outcome_warned = False


def record_refund_outcome(
    sb: Any,
    report_id: Any,
    outcome: Any = None,
    *,
    label: Optional[str] = None,
    user_id: Optional[str] = None,
    only_if: Any = _ANY,
    replayable: bool = True,
) -> Optional[str]:
    """Best-effort stamp of `research_reports.refund_outcome`. SYNC — call it in the SAME
    worker thread as the refund it records. NEVER raises, NEVER part of an `is_refunded` CAS.

    `label` wins over `outcome` (else `refund_outcome_label(outcome, replayable=...)`).
    `user_id` scopes the UPDATE to its owner. `only_if` makes it a CAS on the CURRENT label
    (None = still unstamped) — the retry pass's guard against a concurrent writer.

    An UNCONFIRMED label never overwrites a CONFIRMED one: once the credits are proven back,
    a later replay that cannot prove it (a re-read path answering `no_matching_debit` on a
    legacy row whose CAS winner refunded through the bare ticker, a transport fault) must not
    turn the card back into "Refund pending" — nor undo the owner's `manual`.

    `already_refunded` never overwrites `partial` either (nor a confirmed label — it would say
    nothing new): a refund cut short at a month boundary still INSERTs its reversal row, so
    any later replay of that row (a raised claim's re-read, a raised delete CAS's re-read)
    answers `already_refunded` while the rest of the charge is still owed.

    The UPDATE goes through `retry_idempotent_sync` (transient errors only): one gateway
    blip must not decide a permanent chip. A replay is idempotent in effect — the same value,
    or a filtered no-op that writes nothing (then the return reads None although the first
    attempt's write landed; only a log line depends on it).

    Returns the label written, or None when nothing was written (no matching row, the column
    is latched missing, or the UPDATE failed — each logged, never raised).
    """
    rid = str(report_id or "").strip()
    if not rid:
        logger.warning("refund_outcome stamp skipped: no report id (label=%s)", label)
        return None
    lbl = label or refund_outcome_label(outcome, replayable=replayable)
    if not refund_outcome_column_available():
        return None
    col = REFUND_OUTCOME_COLUMN
    # The current labels this one may NOT replace (None = it may replace any).
    if lbl == _LABEL_ALREADY_REFUNDED:
        protected: Optional[frozenset] = REFUND_CONFIRMED_LABELS | {LABEL_PARTIAL}
    elif lbl not in REFUND_CONFIRMED_LABELS:
        protected = REFUND_CONFIRMED_LABELS
    else:
        protected = None

    def _stamp():
        q = sb.table("research_reports").update({col: lbl}).eq("id", rid)
        if user_id:
            q = q.eq("user_id", user_id)
        if only_if is None:
            q = q.is_(col, "null")
        elif only_if is not _ANY:
            q = q.eq(col, only_if)
        if protected is not None:
            q = q.or_(f"{col}.is.null,{col}.not.in.({','.join(sorted(protected))})")
        return q.execute()

    try:
        result = retry_idempotent_sync(
            _stamp, what=f"refund_outcome stamp for report {rid}", logger=logger,
        )
    except Exception as e:
        if is_unknown_column_error(e):
            note_refund_outcome_column_missing("stamp")
            return None
        logger.warning(
            "refund_outcome stamp failed for report %s (label=%s, %s: %s) — the card keeps "
            "reading the claim; the retry pass re-checks an unstamped claimed row",
            rid, lbl, type(e).__name__, e,
        )
        return None
    note_refund_outcome_column_present()
    return lbl if getattr(result, "data", None) else None


def refund_report_charge(
    refund: Callable[..., Optional[dict]],
    user_id: str,
    amount: int,
    *,
    ticker: Optional[str],
    report_id: Any,
    reason: str,
    allow_legacy_ref: bool,
) -> Tuple[Optional[dict], Optional[str]]:
    """Refund one deep-door report's charge. SYNC — call it off the event loop.

    Returns `(outcome, ref_used)`: `outcome` is `refund`'s answer (`refund_ledgered`'s
    contract — None strictly for a transport fault) and `ref_used` the ref the FINAL call
    carried (None when the row had no ticker), so a caller can record which debit it reversed.

    `refund` is the CALLER's bound `CreditService().refund_ledgered` — never built here, so
    each caller's own (patched, in tests) binding is the one that runs.

    The ref is `report_charge_ref(ticker, report_id)`, the one the charge used. Rows charged
    before 2026-10-12 carry the bare ticker instead, so `allow_legacy_ref=True` — passed ONLY
    by a caller holding the row's `is_refunded` compare-and-set (the worker's claim, the
    sweep, the delete CAS winner) — sends the per-report refund quietly and falls back to the
    bare ticker when, and only when, it answers `no_matching_debit`. Why that is safe:

      * the deep door inserts a row only after its precharge succeeded, so a row whose
        per-report ref matches NO debit at all — not even a reversed one, which would answer
        `already_refunded` — was charged under the legacy ticker ref;
      * `already_refunded`, a transport fault (None), `partial` and `capped_to_zero` never
        fall back: each proves (or may prove) the per-report debit exists, and falling back
        there IS the double refund;
      * the bare-ticker refund is a plain `str`, so it is single-shot (never retried), and
        it can never match a per-report debit — the legacy namespace only shrinks;
      * at-most-once per row comes from the CAS the caller holds, so even a fallback that
        pairs with a different legacy debit of the same ticker pays one charge's worth.

    Every other site passes `allow_legacy_ref=False`: a re-read after a raised claim (it does
    not own the CAS) — the insert-failure path (charged in the same request, never legacy)
    and every compensation refund on their own ChargeRef directly. The cost is a loud REFUND
    LEAK in a rare legacy case instead of a possible mint — the codebase's stated bias toward
    under-refunding. `allow_legacy_ref` therefore MEANS "this caller holds the row's
    `is_refunded` claim"; the unpaired no-ref refund below is gated on it too.
    """
    symbol = (ticker or "").strip().upper()
    rid = str(report_id or "").strip()
    if not symbol or not rid:
        # No ref to rebuild (`research_reports.ticker` is NOT NULL, so this is a defensive
        # arm). The RPC's no-ref_id path refunds granted-first, UNPAIRED — so it is not
        # replay-safe, and only a caller holding the row's CAS (`allow_legacy_ref=True`) may
        # take it: that is today's behaviour for a ticker-less claimed row, which the caller
        # logs. A caller WITHOUT the claim could pay a second time here, so it refuses and
        # pages instead (None reads as "did not happen" at every call site).
        if not allow_legacy_ref:
            logger.error(
                "POSSIBLE REFUND LEAK: report=%s user=%s has no ticker/id to rebuild its "
                "charge ref, and this caller does not hold the refund claim — refusing an "
                "unpaired refund of %s credits that could pay twice; manual check needed",
                report_id, user_id, amount,
            )
            return None, None
        return refund(user_id, amount, reason=reason, ref_id=None), None
    ref = report_charge_ref(symbol, rid)
    if not allow_legacy_ref:
        return refund(user_id, amount, reason=reason, ref_id=ref), ref
    outcome = refund(user_id, amount, reason=reason, ref_id=ref, quiet_no_match=True)
    if isinstance(outcome, dict) and outcome.get("outcome") == "no_matching_debit":
        logger.warning(
            "report refund: no debit under %s for report=%s — it was charged before "
            "per-report refs; refunding against the legacy ref %s (CAS winner)",
            ref, rid, symbol,
        )
        return refund(user_id, amount, reason=reason, ref_id=symbol), symbol
    return outcome, ref


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

# Non-terminal-or-unrefunded statuses eligible for claim. Since 2026-10-12
# `generate_report` no longer pre-stamps status='failed' (that made 'failed' visible
# before the refund; the claim below now writes 'failed' and is_refunded=True in ONE
# UPDATE), but 'failed' stays INCLUDED for legacy rows and for any row stranded in
# 'failed' + is_refunded=False (a pre-deploy worker that died between its stamp and
# the refund). 'completed' and 'deleted' are excluded: a delivered report is never
# refunded, and a deleted one was settled by its DELETE.
_CLAIMABLE_STATUSES = ["pending", "processing", "failed"]

# The three outcomes of the claim's thread body. `claim_and_mark_failed` still answers a
# bool (its callers branch only on "did this call settle the row"), and settles the RAISED
# case itself, inside the same shielded body, so no caller has to.
CLAIM_WON = "claimed"
CLAIM_LOST = "lost"
CLAIM_RAISED = "raised"

# A re-read after a raised claim refunds only a row the claim could have produced (or that
# a concurrent claimer already settled — a replay on the per-report ref converges).
_SETTLED_AFTER_RAISE_STATUSES = ("failed",)


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
    `user_id`/`credits_charged` from the row the UPDATE returned. 'failed' and
    is_refunded=True land in that ONE write, so no reader sees 'failed' without the
    refund having been claimed.

    The thread body has THREE outcomes (CLAIM_WON / CLAIM_LOST / CLAIM_RAISED). A
    RAISED claim proves nothing — a 520 or a read timeout after the commit lands there
    with the row already `failed` + is_refunded=True, which `sweep_once` (it selects
    is_refunded=False) would then never look at again: a permanent, silent leak. So a
    raised claim re-reads the row in the same thread body and, when it reads claimed,
    refunds with the per-report ref and NO legacy fallback (this call cannot prove it
    owns the CAS). With per-report refs a replay of a refund someone else already made
    answers `already_refunded`, so this converges rather than paying twice.

    Returns True if THIS call settled the row (won the claim, or settled a claim whose
    reply was lost), False if it was already terminal/refunded, or the claim did not land.
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

    def _settle(row: Dict[str, Any], *, allow_legacy_ref: bool = True) -> Dict[str, Any]:
        """Refund a row THIS call just claimed, and log the outcome. SYNC, in the worker.

        `refund_ledgered` is synchronous, so this used to run on the event loop after the
        `to_thread(_claim)` returned. Moving it into the same thread as the CAS is what
        makes the shield below sufficient (see there) and takes a PostgREST RPC off the
        loop as a side effect.

        `allow_legacy_ref` is True only for a WON claim — the CAS winner is the one caller
        allowed the bare-ticker fallback (`refund_report_charge`).
        """
        user_id = row.get("user_id")
        amount = row.get("credits_charged") or CreditService.DEEP_RESEARCH_COST
        if not user_id:
            logger.error(
                "claim_and_mark_failed: claimed report %s has no user_id — "
                "cannot refund %s credits", report_id, amount,
            )
            return {"user_id": None, "amount": amount, "ticker": None, "failed": True,
                    "ref": None}

        # ⚠️ The ref MUST match the one the CHARGE used. Since 2026-10-12 that is
        # `report_charge_ref(ticker, report_id)` — one debit per report — rebuilt here from
        # the row by `refund_report_charge`, which falls back to the bare upper-cased ticker
        # (the pre-2026-10-12 charge ref) only for a CAS winner whose per-report refund
        # matched nothing. This once passed `report_id` alone, which matches no charge; since
        # migration 118 that is not cosmetic: `refund_credits` looks the original spend up BY
        # ref_id to learn which pool it drained, and with no match it takes the granted-first
        # fallback — a report paid for out of PURCHASED credits was refunded into the
        # GRANTED pool, which `ensure_credit_period` wipes at the month boundary (App Store
        # Guideline 3.1.1, on the primary failure path of the 20-credit action).
        ticker = (row.get("ticker") or "").upper() or None
        if not ticker:
            logger.warning(
                "claim_and_mark_failed: report %s has no ticker — refunding %s credits "
                "without a split lookup (granted-first fallback)", report_id, amount,
            )
        # Inspected, not wrapped in a bare try/except. `refund_ledgered` NEVER raises — it
        # catches and returns None — and this is the ONLY log line carrying `report_id` for a
        # manual correction.
        #
        # We already won the claim (is_refunded=True), so nothing re-claims it — biased to
        # under-refund (safe) over double-refund. A transient transport fault IS retried
        # (bounded) inside `refund_ledgered`, because the per-report ref makes a replay
        # converge; what is left after that is loud, never silent.
        ref_used: Optional[str] = None
        raised = False
        try:
            outcome, ref_used = refund_report_charge(
                CreditService().refund_ledgered, user_id, amount,
                ticker=ticker, report_id=report_id,
                reason="report_refund_reconciled", allow_legacy_ref=allow_legacy_ref,
            )
        except Exception as e:
            # `refund_ledgered` does not raise, but CreditService() construction could, and
            # this runs inside a background sweep where an escape kills the whole pass.
            # Normalised to the same "did not happen" shape so there is ONE reporting path.
            logger.warning(
                "claim_and_mark_failed: refund call raised for report %s (%s: %s)",
                report_id, type(e).__name__, e,
            )
            outcome = None
            raised = True
        # None = transport fault; the business no-ops carry their own outcome. Both mean the
        # user keeps neither the report nor the credits, and the CAS is spent.
        failed = refund_did_not_happen(outcome)
        if failed:
            logger.error(
                "REFUND LEAK: report %s claimed (is_refunded=True) but the refund of %s "
                "credits to user %s did not happen (outcome=%s, ref_id=%s) — the claim is "
                "spent, so nothing re-claims it; only a transport fault on the per-report "
                "ref is retried (once, by the reconciliation loop's refund retry). Manual "
                "correction needed otherwise. The card reads 'Refund pending' (app >= "
                "1.02, migration 195); older builds still show '[Refunded]'.",
                report_id, amount, user_id,
                "rpc_failed" if outcome is None else outcome.get("outcome"),
                ref_used or ticker,
            )
        else:
            logger.info(
                "Refunded %s credits for failed report %s (user %s, outcome=%s, ref_id=%s)",
                outcome.get("refunded", amount) if isinstance(outcome, dict) else amount,
                report_id, user_id,
                outcome.get("outcome") if isinstance(outcome, dict) else "legacy_int",
                ref_used,
            )
        # What the refund DID, recorded in this same thread body (so the shield above covers
        # it too) and never inside the CAS. A transport fault is retryable only when it was
        # on the per-report ChargeRef — or the call raised before anything was sent; on the
        # legacy bare ticker the refund may have committed with its reply lost, so that one
        # is the owner's (`legacy_rpc_failed`). Best-effort: never raises.
        record_refund_outcome(
            sb, report_id, outcome, user_id=user_id,
            replayable=raised or isinstance(ref_used, ChargeRef),
        )
        return {"user_id": user_id, "amount": amount, "ticker": ticker, "failed": failed,
                "ref": ref_used}

    def _settle_raised_claim(exc: Exception) -> Dict[str, Any]:
        """The claim RAISED: one re-read decides whether it (or a concurrent claimer)
        committed. SYNC, in the same worker thread as the claim."""
        error = f"{type(exc).__name__}: {exc}"
        _log = logger.warning if is_transient_supabase_error(exc) else logger.error
        _log(
            "claim_and_mark_failed: claim UPDATE for report %s raised (%s) — re-reading the "
            "row: a claim that committed before its reply was lost still owes the refund",
            report_id, error,
        )

        def _reread():
            return (
                sb.table("research_reports")
                .select("id, status, is_refunded, credits_charged, user_id, ticker")
                .eq("id", report_id)
                .limit(1)
                .execute()
            )

        try:
            # A pure read, so a gateway blip is retried (bounded; we are off the loop).
            again = retry_idempotent_sync(
                _reread, what=f"research report {report_id} re-read after a raised claim",
                logger=logger,
            )
            rows = list(getattr(again, "data", None) or [])
        except Exception as e:
            logger.error(
                "POSSIBLE REFUND LEAK: the claim for report %s raised (%s) and the re-read "
                "failed too (%s: %s) — if the claim committed, is_refunded=True is set with "
                "no refund and no sweep will retry it; check the row and the ledger by ref",
                report_id, error, type(e).__name__, e,
            )
            return {"state": CLAIM_RAISED, "error": error, "settlement": None}
        row = dict(rows[0] or {}) if rows else {}
        claimed = (
            row.get("is_refunded") is True
            and row.get("status") in _SETTLED_AFTER_RAISE_STATUSES
            and int(row.get("credits_charged") or 0) > 0
        )
        if not claimed:
            # Nothing was claimed: the row is untouched (the sweep sees it again), or gone,
            # or terminal for another reason (completed/deleted) and not ours to refund.
            logger.warning(
                "claim_and_mark_failed: report %s reads status=%s is_refunded=%s after the "
                "raised claim — nothing was claimed here; left for the reconciliation sweep",
                report_id, row.get("status"), row.get("is_refunded"),
            )
            return {"state": CLAIM_RAISED, "error": error, "settlement": None}
        # Claimed — by this call (reply lost) or a concurrent claimer. Refund on the
        # per-report ref ONLY: a replay of someone else's refund answers already_refunded.
        logger.warning(
            "claim_and_mark_failed: report %s reads failed+refunded after a raised claim — "
            "settling it on the per-report ref (a replay answers already_refunded)", report_id,
        )
        return {"state": CLAIM_RAISED, "error": error,
                "settlement": _settle(row, allow_legacy_ref=False)}

    def _claim_and_settle() -> Dict[str, Any]:
        """ONE worker-thread body: the CAS and, if it was won, the refund + its logging —
        or, if the CAS raised, the re-read and the settlement it decides."""
        try:
            result = _claim()
        except Exception as e:
            return _settle_raised_claim(e)
        rows = result.data or []
        if not rows:
            # Lost the claim, or the row was already terminal/refunded.
            return {"state": CLAIM_LOST, "error": None, "settlement": None}
        return {"state": CLAIM_WON, "error": None, "settlement": _settle(rows[0])}

    # SHIELDED, and the refund lives INSIDE the thread body — both halves are load-bearing.
    #
    # The sweep runs in a lifespan task the shutdown block cancels. A cancel that lands
    # while the UPDATE is on the wire (~100-300 ms) cancels only the awaiting coroutine;
    # the executor thread cannot be interrupted, so PostgREST still commits
    # `is_refunded=True` + error_message "your credits were refunded" — and with a plain
    # `await asyncio.to_thread(_claim)` the result was discarded: `CancelledError` is a
    # BaseException, so neither `refund_ledgered` nor the REFUND LEAK line below ever ran,
    # and the one-shot claim was spent with no refund and no log. A Railway redeploy timed
    # against a sweep tick is a silent 20-credit leak per orphaned report.
    #
    # `asyncio.shield` keeps the inner task alive through the cancel. It is only enough
    # because the refund is in the SAME thread body: at interpreter exit `asyncio.run`'s
    # `_cancel_all_tasks` cancels the shielded inner task before any post-await code would
    # run, and only the executor thread is guaranteed to finish
    # (`shutdown_default_executor`). A queued-but-unstarted item CAN still be discarded by
    # that final cancel — but then the UPDATE never ran, nothing was claimed, and the next
    # process's sweep sees the row again. SIGKILL/OOM mid-UPDATE remains outside what
    # in-process code can close.
    inner = asyncio.ensure_future(asyncio.to_thread(_claim_and_settle))
    try:
        outcome = await asyncio.shield(inner)
    except asyncio.CancelledError:
        logger.warning(
            "claim_and_mark_failed: cancelled while the claim for report %s was in "
            "flight — the CAS and its refund continue in their worker thread", report_id,
        )

        def _after_cancel(done: "asyncio.Future") -> None:
            # The thread body logs a won claim itself (Refunded / REFUND LEAK). This
            # covers the outcomes it cannot report to an awaiting caller: an UPDATE that
            # raised after the cancel (otherwise only a line deep in the thread) and a lost
            # claim.
            if done.cancelled():
                logger.warning(
                    "claim_and_mark_failed: claim for report %s discarded before its "
                    "UPDATE ran (shutdown) — nothing claimed, the next sweep retries",
                    report_id,
                )
                return
            exc = done.exception()
            if exc is not None:
                logger.error(
                    "claim_and_mark_failed: UPDATE for report %s failed after the "
                    "cancel: %s: %s", report_id, type(exc).__name__, exc,
                )
                return
            result = done.result() or {}
            if result.get("state") == CLAIM_RAISED:
                logger.error(
                    "claim_and_mark_failed: UPDATE for report %s failed after the "
                    "cancel: %s (re-read %s)", report_id, result.get("error"),
                    "settled it" if result.get("settlement") else "found nothing claimed",
                )
            elif result.get("state") == CLAIM_LOST:
                logger.info(
                    "claim_and_mark_failed: report %s was already settled by another "
                    "caller (post-cancel)", report_id,
                )

        inner.add_done_callback(_after_cancel)
        raise
    except Exception as e:
        logger.error(
            "claim_and_mark_failed: claim for report %s failed: %s: %s",
            report_id, type(e).__name__, e, exc_info=True,
        )
        return False

    settled = (outcome or {}).get("settlement")
    if settled is None:
        return False
    user_id = settled["user_id"]
    if not user_id:
        return True
    amount = settled["amount"]
    ticker = settled["ticker"]
    failed = settled["failed"]

    # A settlement after a RAISED claim notifies too: the claim it settles may be this
    # call's own (its reply lost), and if a concurrent claimer already sent the push the
    # once-ever `reportfail:{report_id}` dedup key drops this one.
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

    ⚠️ A SECOND CALLER WAS ADDED AND REMOVED. It fired from `GET /stocks/{ticker}/report`
    on what was believed to be a client-disconnect cancellation. On the pinned stack
    (starlette 0.41.3) a disconnect does NOT cancel the handler — verified empirically —
    so that caller was unreachable, and the situation it described does not arise: the
    handler completes, the report is cached, and the user gets it on their next open.
    See the note in `ticker_report.py`'s `finally` before re-adding anything like it.

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


# ── Phase 2: retry a claimed refund that did not provably land ──────────────────────────
#
# `sweep_once` never looks at an `is_refunded=True` row — deliberately: the CAS is the
# at-most-once guarantee for FIRST refunds. This pass is the one exception, and it exists
# only because unique per-report refs (2026-10-12) make a refund REPLAY converge: the
# per-report ref names exactly one debit, so a second refund of it answers
# `already_refunded` and moves nothing. It therefore:
#   * refunds with `report_charge_ref(ticker, id)` — the SAME builder the precharge used —
#     and NEVER the legacy bare-ticker fallback (`refund_report_charge(...,
#     allow_legacy_ref=True)`): it does not hold the row's CAS, and two containers can run
#     this loop during a deploy overlap; a bare-ticker replay could pair a sibling report's
#     debit and mint 20 credits;
#   * retries ONLY `rpc_failed` (a transport fault on the per-report ref) and claimed rows
#     whose stamp never landed (NULL, created more than REFUND_RETRY_GRACE_SECONDS ago) —
#     never `legacy_rpc_failed`, never a business no-op (`no_matching_debit`,
#     `capped_to_zero`, `partial`: a replay cannot fix those);
#   * bounds itself: an `rpc_failed` row's second transport fault becomes `retry_rpc_failed`
#     (never re-selected) — ONE automatic retry of a known failure; an unstamped row is
#     re-checked each pass until any answer but a transport fault labels it, and since every
#     re-check is on the one-debit ref, repeating it can never pay twice.
#
# There is no cutover instant. An unstamped row whose per-report refund answers
# `no_matching_debit` was CHARGED under the legacy bare ticker — a research_reports row is
# inserted only after its precharge committed, and every precharge since 2026-10-12 is named
# by the per-report ref, so a current row always has that debit (a reversed one answers
# `already_refunded`). Such a row's CAS winner held the legacy fallback, so it reads 'legacy'
# exactly like migration 195's backfill (whose bound is likewise read from the ledger).

#: An unstamped claimed row CREATED less than this long ago may still be mid-settlement (CAS
#: committed, refund or stamp in flight) and is left alone. ⚠️ Measured from `created_at`, NOT
#: from the claim: a report deleted 40 minutes after it was queued, or failed by a run that
#: started late, is claimed long after creation, so a pass CAN overlap its claimer's in-flight
#: settlement. That is safe, not just rare: the replay is on the per-report ref (one debit, so
#: the two refunds converge on `already_refunded`), an unstamped row's transport fault is a
#: WARNING that leaves the row for the next pass (never `retry_rpc_failed`, never a page), and
#: the legacy inference below only lands on a row that is still unstamped (the one residue is
#: in `_retry_one_refund`'s docstring). No claim timestamp exists to measure from, and none
#: may be added to the `is_refunded` CAS (module rule above).
REFUND_RETRY_GRACE_SECONDS = 1800

REFUND_RETRY_BATCH = 50

# A retry's ledger reason follows the row's CURRENT status: 'deleted' → report_refund_deleted,
# otherwise report_refund_reconciled. That is the original settlement's reason on most paths,
# but not two: (1) a FAILED report whose refund leaked (rpc_failed) and that the user then
# deleted — Retry sends DELETE intent=retry, whose CAS misses (is_refunded is already true) and
# whose plain soft-delete flips status to 'deleted' — is retried as 'report_refund_deleted'
# ("report deleted" instead of "report didn't finish"); (2) the insert-failure orphan claim
# (research.py) marks its row 'failed' and refunds as 'report_refund', so its retry reads
# 'report_refund_reconciled' ("report didn't finish" instead of "research report"). Cosmetic
# both: the amount and the ref are the original charge's, and nothing records the first
# settlement's reason to recover it from.
_REFUND_RETRY_STATUSES = ["failed", "deleted"]


def _retry_one_refund(sb: Any, row: Dict[str, Any], expected: Optional[str]) -> bool:
    """Retry one claimed refund on its per-report ref and stamp the result. SYNC (worker
    thread). Never raises. Returns True when the credits are now provably back.

    `expected` is the label the lookup read: `rpc_failed`, or None for an unstamped row.

    * A CONFIRMED answer is stamped with no CAS on `expected`: credits proven back beat any
      unconfirmed label a concurrent writer put there (e.g. another container's
      `retry_rpc_failed` during a deploy overlap), and `record_refund_outcome` already
      refuses to put an unconfirmed label over a confirmed one — or `already_refunded` over
      `partial`.
    * An UNCONFIRMED answer keeps the CAS on `expected`: an owner's `manual` (or any label)
      written meanwhile stands.
    * Unstamped row + transport fault (None, or a call that raised): WARNING, no stamp — the
      next pass decides. Its refund may be on the wire from its own claimer (the grace is
      measured from created_at), so this is not a page.
    * Unstamped row + `no_matching_debit`: charged under the legacy bare ticker (see the block
      comment above) — WARNING, stamp `legacy` ONLY while still unstamped, and NEVER the
      legacy fallback. Accepted edge, the same as migration 195's backfill and as the chip
      before 195: a legacy row whose claimer never sent its refund (SIGKILL between the CAS
      and the RPC), or whose bare-ticker fallback failed AND whose `legacy_rpc_failed` stamp
      failed too (or landed only after this inference, which it may not overwrite), reads
      confirmed. The claimer's REFUND LEAK line was the page for the fallback case; legacy
      rows only ever shrink.
    * `rpc_failed` row + `no_matching_debit`: a true page (ERROR). That label is written only
      after a per-report attempt whose transport fault stopped it BEFORE any fallback, so the
      credits really are owed.
    """
    report_id = row.get("id")
    user_id = row.get("user_id")
    amount = int(row.get("credits_charged") or 0)
    if not report_id or amount <= 0:
        return False
    try:
        ref = report_charge_ref(row.get("ticker"), report_id)
    except ValueError as e:
        # No unique ref can be rebuilt, so no safe replay exists: hand it to the owner.
        logger.error(
            "REFUND LEAK: report %s (user %s) is claimed but its refund is unconfirmed and no "
            "per-report charge ref can be rebuilt (%s) — not retried; manual check needed",
            report_id, user_id, e,
        )
        record_refund_outcome(sb, report_id, label=LABEL_LEGACY_RPC_FAILED,
                              user_id=user_id, only_if=expected)
        return False
    if not user_id:
        logger.error(
            "REFUND LEAK: report %s is claimed with an unconfirmed refund but has no user_id — "
            "not retried; manual check needed (ref_id=%s)", report_id, ref,
        )
        record_refund_outcome(sb, report_id, label=LABEL_LEGACY_RPC_FAILED, only_if=expected)
        return False
    # The CURRENT status picks the reason (see `_REFUND_RETRY_STATUSES` for the two paths
    # where that differs from the first settlement's).
    reason = (
        "report_refund_deleted" if row.get("status") == "deleted"
        else "report_refund_reconciled"
    )
    # An UNSTAMPED row is a re-check, not a known failure: its claimer usually sent a refund
    # already (or the row was charged under the legacy bare ticker). So `refund_ledgered` must
    # not page for it on `no_matching_debit` (the legacy inference below) or on a TRANSIENT
    # fault (the next pass re-checks); a non-transient failure and a business no-op
    # (capped_to_zero, partial, no_credits_row) still page. An `rpc_failed` row keeps every
    # page: there the credits are known to be owed.
    unstamped = expected is None
    try:
        # Directly on the ChargeRef — NEVER through `refund_report_charge`'s legacy fallback.
        outcome = CreditService().refund_ledgered(
            user_id, amount, reason=reason, ref_id=ref,
            quiet_no_match=unstamped, quiet_transport=unstamped,
        )
    except Exception as e:  # construction only; refund_ledgered itself never raises
        logger.warning(
            "refund retry: refund call raised for report %s (%s: %s)",
            report_id, type(e).__name__, e,
        )
        outcome = None
    if outcome is None and unstamped:
        logger.warning(
            "refund retry: re-checking the unstamped refund of report %s (user %s, ref_id=%s) "
            "hit a transport fault — left unstamped; the next pass re-checks it on the same "
            "ref (its claimer may still be settling it: the grace runs from created_at)",
            report_id, user_id, ref,
        )
        return False
    if (unstamped and isinstance(outcome, dict)
            and outcome.get("outcome") == "no_matching_debit"):
        stamped = record_refund_outcome(sb, report_id, label=LABEL_LEGACY, user_id=user_id,
                                        only_if=None)
        logger.warning(
            "refund retry: no debit under %s for unstamped report %s (user %s) — it was charged "
            "under the legacy bare ticker, whose refund only its CAS winner may send; "
            "recorded as '%s' (never refunded here)%s",
            ref, report_id, user_id, LABEL_LEGACY,
            "" if stamped else " — stamp not written: the row was labelled meanwhile, or the "
                               "write failed",
        )
        return False
    label = refund_outcome_label(outcome)
    if label == LABEL_RPC_FAILED:
        label = LABEL_RETRY_RPC_FAILED   # one automatic retry, never a loop
    confirmed = label in REFUND_CONFIRMED_LABELS
    stamped = record_refund_outcome(sb, report_id, label=label, user_id=user_id,
                                    only_if=_ANY if confirmed else expected)
    settled = not refund_did_not_happen(outcome)
    (logger.info if settled else logger.error)(
        "%s: retried the refund of report %s (user %s, ref_id=%s, was=%s) → %s%s",
        "refund retry" if settled else "REFUND LEAK: refund retry",
        report_id, user_id, ref, expected or "unstamped", label,
        "" if stamped else " (stamp not written: the label changed meanwhile, or the write "
                           "failed — the next pass re-reads it)",
    )
    return settled


async def retry_unconfirmed_refunds_once(
    *,
    now: Optional[datetime] = None,
    supabase=None,
) -> Dict[str, int]:
    """One pass over claimed research-report refunds that did not provably land.

    Selects `rpc_failed` rows and unstamped (NULL) claimed rows created more than
    REFUND_RETRY_GRACE_SECONDS ago — no cutover instant: an unstamped row charged under the
    legacy bare ticker is recognised by its answer (`_retry_one_refund`). Runs after
    `sweep_once` in the 5-minute reconciliation loop. Needs migration 195 (until then:
    latched, a no-op). Returns {"candidates": n, "settled": m}; `settled` counts refunds now
    proven back (a `legacy` inference is not counted).
    """
    sb = supabase or get_supabase()
    now = now or datetime.now(timezone.utc)
    if not refund_outcome_column_available():
        return {"candidates": 0, "settled": 0}
    col = REFUND_OUTCOME_COLUMN
    grace_cutoff_iso = (now - timedelta(seconds=REFUND_RETRY_GRACE_SECONDS)).isoformat()

    def _base():
        return (
            sb.table("research_reports")
            .select("id, user_id, ticker, status, credits_charged, created_at")
            .eq("is_refunded", True)
            .in_("status", _REFUND_RETRY_STATUSES)
            .gt("credits_charged", 0)
        )

    def _find_rpc_failed():
        return (
            _base().eq(col, LABEL_RPC_FAILED)
            .order("created_at", desc=False).limit(REFUND_RETRY_BATCH).execute()
        )

    def _find_unstamped():
        return (
            _base().is_(col, "null")
            .lt("created_at", grace_cutoff_iso)
            .order("created_at", desc=False).limit(REFUND_RETRY_BATCH).execute()
        )

    work: List[Tuple[Dict[str, Any], Optional[str]]] = []
    seen: set = set()
    for finder, expected, what in (
        (_find_rpc_failed, LABEL_RPC_FAILED, "rpc_failed"),
        (_find_unstamped, None, "unstamped"),
    ):
        try:
            result = await retry_idempotent_async(
                finder, what=f"research refund retry lookup ({what})", logger=logger,
            )
        except Exception as e:
            if is_unknown_column_error(e):
                note_refund_outcome_column_missing("refund retry")
                return {"candidates": 0, "settled": 0}
            _log = logger.warning if is_transient_supabase_error(e) else logger.error
            _log(
                "research refund retry: %s lookup failed: %s: %s — the next pass retries",
                what, type(e).__name__, e, exc_info=not is_transient_supabase_error(e),
            )
            continue
        note_refund_outcome_column_present()
        for row in getattr(result, "data", None) or []:
            if not isinstance(row, dict) or row.get("id") in seen:
                continue
            seen.add(row.get("id"))
            work.append((row, expected))

    settled = 0
    for row, expected in work:
        try:
            if await asyncio.to_thread(_retry_one_refund, sb, row, expected):
                settled += 1
        except Exception as e:  # _retry_one_refund never raises; belt and braces for the loop
            logger.error(
                "research refund retry: report %s failed unexpectedly: %s: %s",
                row.get("id"), type(e).__name__, e, exc_info=True,
            )
    if work:
        logger.info(
            "research refund retry: %d unconfirmed refund(s), %d now settled",
            len(work), settled,
        )
    return {"candidates": len(work), "settled": settled}
