"""
Credit Service — atomic charge + refund for Generate Analysis.

The `charge_user_credits` and `refund_user_credits` Postgres functions
(migration 041) do the actual mutation in a single round-trip so two
concurrent Generate Analysis requests can't both succeed against a
shared low balance.

`remaining` on `user_credits` is a GENERATED column (total - used),
so all mutations target `used` and read `remaining` afterward.

Charge refs (2026-10-12). Every metered debit is named by a `ChargeRef` minted by one of
the three builders below, and a ChargeRef names EXACTLY ONE debit. That one property is
what makes a refund replay-safe: `refund_credits` pairs a refund with the NEWEST
UN-REVERSED debit of (user, ref_id, amount) and answers `already_refunded` when that debit
is already reversed, so a replayed refund — a retry after a lost reply, a compensation that
races the sweep — converges instead of paying twice. Report charges used to be named by
the bare ticker (deep door) or `TICKER:persona` (direct door); a replay on such a shared
ref pairs with a SIBLING report's debit and mints credits.
"""

import asyncio
import logging
import threading
import uuid
import weakref
from typing import Any, Callable, Dict, Optional

from app.config import settings
from app.database import get_supabase
from app.utils.supabase_errors import is_transient_supabase_error, retry_idempotent_sync

logger = logging.getLogger(__name__)


class ChargeRef(str):
    """A `credit_transactions.ref_id` that names EXACTLY ONE debit.

    Minted only by `chat_turn_ref`, `report_charge_ref` and `direct_report_charge_ref`, so a
    charge site and its refund sites cannot drift apart. `refund_ledgered` retries a
    transient transport fault ONLY for a ChargeRef: with one debit per ref a replay of a
    refund that already committed answers `already_refunded` and moves nothing.

    Fails SAFE by construction: any string operation (`.upper()`, slicing, an f-string,
    `str(ref)`) returns a PLAIN `str`, which is never retried — so a legacy bare-ticker ref,
    or one rebuilt by hand, can never be replayed against a sibling's debit.
    """

    __slots__ = ()


def chat_turn_ref(session_id: Optional[str]) -> ChargeRef:
    """One chat turn's debit: `{session_id}:{uuid4hex}`. The session prefix keeps a ledger
    row greppable back to its conversation; the uuid is what makes it one debit."""
    return ChargeRef(f"{session_id}:{uuid.uuid4().hex}")


def report_charge_ref(ticker: str, report_id: Any) -> ChargeRef:
    """A deep-door (`POST /research/generate`) report's debit: `{TICKER}:{report_id}`.

    A pure function of two columns every `research_reports` row already carries, so every
    refund site (the worker's claim, the sweep, both delete paths, the insert-failure path)
    rebuilds the SAME ref from the row without a new column. `credit_history_service` reads
    the ticker as the head before the first ':', so the statement keeps its subtitle.
    """
    symbol = (ticker or "").strip().upper()
    rid = str(report_id or "").strip().lower()
    if not symbol or not rid:
        raise ValueError(
            f"report_charge_ref needs a ticker and a report id (got {ticker!r}, {report_id!r})"
        )
    return ChargeRef(f"{symbol}:{rid}")


def direct_report_charge_ref(ticker: str, persona: str) -> ChargeRef:
    """A direct-door (`GET /stocks/{ticker}/report`) report's debit:
    `{TICKER}:{persona}:{uuid4hex}`. That door has no row id, and its charge and refund
    happen in the same request, so the uuid is held in ONE local the refund reuses."""
    symbol = (ticker or "").strip().upper()
    if not symbol:
        raise ValueError(f"direct_report_charge_ref needs a ticker (got {ticker!r})")
    return ChargeRef(f"{symbol}:{persona}:{uuid.uuid4().hex}")


def _warn_if_on_event_loop(op: str, user_id: Any, ref_id: Any) -> None:
    """Tripwire for CLAUDE.md invariant #6. `precharge` and `refund_ledgered` are SYNC
    Supabase round trips (a ChargeRef refund can also sleep through up to two retry
    backoffs), so on the single uvicorn worker an on-loop call stalls every in-flight
    request. A worker thread has no running loop, so this is silent there."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    logger.warning(
        "%s RPC ON THE EVENT LOOP for user=%s ref_id=%s — call it via asyncio.to_thread",
        op, user_id, ref_id,
    )


# ── Detached settlements ─────────────────────────────────────────────────────────────
# A frame that is being CANCELLED cannot await: the next await raises CancelledError again
# at once (uvicorn cancels in-flight requests at `--timeout-graceful-shutdown`, and anyio's
# cancel scopes re-deliver it). A refund owed by such a frame is handed to a strongly-held
# task here, which runs the SYNC settlement in a worker thread; the lifespan shutdown waits
# for these (`drain_detached_settlements`) so a deploy does not discard a queued refund.
# Same shape as `chat_web_search_service._release_detached`, for a sync callable.
_DETACHED_SETTLEMENTS: "set[asyncio.Task]" = set()


class _SettlementRun:
    """What a settlement's worker thread did, shared with the loop under `lock`.

    A task cancelled AFTER its thread started is not a lost refund — an executor job cannot
    be stopped once running, so the thread finishes the settlement anyway (and
    `asyncio.run` joins it at exit) — but its outcome then has no reader. So the done-callback
    marks such a run `orphaned`, and the thread logs how it ended itself; a run that had
    already finished is reported by the callback. Without this a cancelled-after-start task
    paged "REFUND LEAK?" for a refund that landed, and a raise in it was never logged."""

    __slots__ = ("lock", "started", "finished", "exc", "orphaned")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started = False
        self.finished = False
        self.exc: Optional[BaseException] = None
        self.orphaned = False


_SETTLEMENT_RUNS: "weakref.WeakKeyDictionary[asyncio.Task, _SettlementRun]" = (
    weakref.WeakKeyDictionary()
)


def _spawn_settlement(fn: Callable[[], Any], what: str, *, report: bool) -> "asyncio.Task":
    """Start `fn` in a worker thread as a strongly-held task the shutdown drain waits for.
    `report=True` logs how it ended (nobody awaits it); otherwise the awaiting caller sees
    the result and only the bookkeeping callback is attached."""
    run = _SettlementRun()

    def _body() -> Any:
        with run.lock:
            run.started = True
        try:
            result = fn()
        except BaseException as e:
            with run.lock:
                run.finished, run.exc, orphaned = True, e, run.orphaned
            if orphaned:
                logger.error(
                    "detached settlement %s raised after its task was cancelled: %s: %s — "
                    "verify the ledger by ref", what, type(e).__name__, e, exc_info=True,
                )
            raise
        with run.lock:
            run.finished, orphaned = True, run.orphaned
        if orphaned:
            logger.info("detached settlement %s finished after its task was cancelled", what)
        return result

    loop = asyncio.get_running_loop()
    task = loop.create_task(asyncio.to_thread(_body), name=f"settle:{what}")
    _SETTLEMENT_RUNS[task] = run
    _DETACHED_SETTLEMENTS.add(task)
    task.add_done_callback(_DETACHED_SETTLEMENTS.discard)
    if report:
        task.add_done_callback(lambda t: _on_detached_done(what, t))
    return task


async def settle_off_loop(fn: Callable[[], Any], *, what: str) -> Any:
    """Run the SYNC settlement `fn` in a worker thread and return its result — and finish it
    even if THIS frame is cancelled while waiting.

    Shielded and strongly held: a cancel (a deploy's graceful-shutdown deadline) stops only
    the wait, the settlement runs on and the lifespan drain waits for it, and how it ended is
    then logged by name. A plain `await asyncio.to_thread(fn)` would instead cancel an
    executor job that had not started yet, silently dropping the refund. `fn` must CONTAIN
    its literal call (a lambda or local def) so the reason scanners keep reading it.
    Exceptions from `fn` propagate to the caller, exactly as an inline call's would.
    """
    task = _spawn_settlement(fn, what, report=False)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done():
            task.add_done_callback(lambda t: _on_detached_done(what, t))
        raise


async def charge_off_loop(
    charge: Callable[[], Any],
    *,
    on_abandoned: Callable[[Any, Optional[BaseException]], Any],
    what: str,
) -> Any:
    """Run a SYNC precharge (or a claim that wraps one) in a worker thread, and never lose a
    debit to THIS frame being cancelled while it waits.

    Off the loop for invariant #6 — but a plain `await asyncio.to_thread(charge)` opened a
    window the inline call never had: a cancel (a deploy's graceful-shutdown deadline) landing
    during the round trip raised at the await, the caller never learned the charge had
    landed, and nothing refunded it. Here the charge runs on (shielded, strongly held,
    drained at shutdown), and once it ends `on_abandoned(result, exc)` runs DETACHED in a
    worker thread to hand back whatever landed — the caller decides what that means (a
    `None` balance is "insufficient": nothing was debited). Not called when the wait was not
    cancelled: the result or the exception then reaches the caller as an inline call's would.
    """
    task = _spawn_settlement(charge, what, report=False)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        def _after(t: "asyncio.Task") -> None:
            if t.cancelled():
                # Only at interpreter exit, after the drain: the thread may still have run.
                logger.error(
                    "POSSIBLE LOST CHARGE: %s was cancelled with its waiter and may have "
                    "committed — verify the ledger by ref", what,
                )
                return
            exc = t.exception()
            result = None if exc is not None else t.result()
            settle_detached(lambda: on_abandoned(result, exc), what=f"abandoned {what}")

        if task.done():
            _after(task)
        else:
            task.add_done_callback(_after)
        raise


def _on_detached_done(what: str, task: "asyncio.Task") -> None:
    _DETACHED_SETTLEMENTS.discard(task)
    if task.cancelled():
        run = _SETTLEMENT_RUNS.get(task)
        started = finished = False
        exc: Optional[BaseException] = None
        if run is not None:
            with run.lock:
                started, finished, exc = run.started, run.finished, run.exc
                # Not finished (started or not): the thread, if it ever runs, logs its end.
                run.orphaned = not finished
        if started and finished:
            if exc is not None:
                logger.error(
                    "detached settlement %s raised: %s: %s", what, type(exc).__name__, exc,
                    exc_info=exc,
                )
            return
        if started:
            logger.warning(
                "detached settlement %s: its task was cancelled after the worker thread "
                "started — the thread runs to completion and logs how it ended", what,
            )
            return
        logger.error(
            "REFUND LEAK? detached settlement %s was cancelled before it finished — its "
            "worker thread may never have run; verify the ledger by ref and correct manually",
            what,
        )
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "detached settlement %s raised: %s: %s", what, type(exc).__name__, exc,
            exc_info=exc,
        )


def settle_detached(fn: Callable[[], Any], *, what: str) -> None:
    """Run the SYNC settlement `fn` off the loop without awaiting it. Never raises.

    For a frame being cancelled only — anywhere else, `await settle_off_loop(fn, ...)` keeps
    the outcome observable. `fn` must CONTAIN its literal call (a lambda or local def), so the
    reason scanners keep reading it. With no running loop it simply runs inline.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            fn()
        except Exception as e:
            logger.error(
                "settlement %s raised (inline, no running loop): %s: %s",
                what, type(e).__name__, e, exc_info=True,
            )
        return
    try:
        _spawn_settlement(fn, what, report=True)
    except Exception as e:  # a closed loop — nothing to schedule onto
        logger.error(
            "REFUND LEAK? could not schedule detached settlement %s (%s: %s) — verify the "
            "ledger by ref and correct manually", what, type(e).__name__, e,
        )


#: How long the drain must see NO settlement running (and no task still unwinding a cancel)
#: before it concludes, and how often it re-scans meanwhile. See `drain_detached_settlements`.
_DRAIN_QUIET_SECONDS = 0.5
_DRAIN_POLL_SECONDS = 0.05
#: The most the drain waits for cancelled tasks to unwind (inside its own `timeout`), so a
#: task that swallowed its cancel cannot hold every shutdown for the whole bound.
_DRAIN_UNWIND_SECONDS = 3.0


def _unwinding_tasks() -> "list[asyncio.Task]":
    """Tasks a cancel was delivered to that have not finished yet — at shutdown, the request
    frames uvicorn cancelled, still on their way to the `finally` that spawns their refund."""
    me = asyncio.current_task()
    out = []
    for t in asyncio.all_tasks():
        if t is me or t.done():
            continue
        try:
            if t.cancelling():
                out.append(t)
        except AttributeError:  # Task.cancelling() is 3.11+; the quiet window still applies
            return []
    return out


async def drain_detached_settlements(timeout: float = 10.0) -> int:
    """Wait (bounded) for every detached settlement still running — including any a finishing
    one spawns (an abandoned charge's compensation) and any spawned AFTER the drain began.
    Returns how many did NOT finish within `timeout`; each is logged at ERROR by name (it
    carries the ref).

    An empty first scan is not the end (review F1, 2026-10-12). At the graceful-shutdown
    deadline uvicorn cancels the request tasks and IMMEDIATELY sends `lifespan.shutdown`
    without waiting for them; a handler sits three BaseHTTPMiddleware task-group hops down,
    so its cancel reaches the `finally` that hands its refund to `settle_detached` several
    loop iterations AFTER this drain first looks. Returning on that empty scan left the
    refund to `asyncio.run`'s teardown, which cancels it — lost, or a false "REFUND LEAK?"
    page on every deploy that cut off a charged request. So the drain also waits (within
    `_DRAIN_UNWIND_SECONDS`) for tasks still unwinding a cancel, and returns only after it
    has seen nothing running for `_DRAIN_QUIET_SECONDS`, re-scanning every
    `_DRAIN_POLL_SECONDS` — all inside `timeout`.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    deadline = start + max(0.0, float(timeout))
    unwind_deadline = min(deadline, start + _DRAIN_UNWIND_SECONDS)
    announced = False
    quiet_since: Optional[float] = None
    while True:
        pending = {t for t in _DETACHED_SETTLEMENTS if not t.done()}
        now = loop.time()
        if pending:
            quiet_since = None
            if not announced:
                logger.info("draining %d detached credit settlement(s)", len(pending))
                announced = True
            left = deadline - now
            if left > 0:
                # Poll-sized waits, so a settlement spawned meanwhile is seen promptly.
                await asyncio.wait(pending, timeout=min(left, _DRAIN_POLL_SECONDS))
                # Let done-callbacks run: a finished charge may have just spawned its refund.
                await asyncio.sleep(0)
                continue
            for t in pending:
                logger.error(
                    "REFUND LEAK? detached settlement %s still running after %.1fs at "
                    "shutdown — verify the ledger by ref", t.get_name(), timeout,
                )
            return len(pending)
        if now >= deadline:
            return 0
        unwinding = _unwinding_tasks() if now < unwind_deadline else []
        if unwinding:
            quiet_since = None
            await asyncio.wait(unwinding, timeout=min(unwind_deadline - now, _DRAIN_POLL_SECONDS))
            await asyncio.sleep(0)
            continue
        if quiet_since is None:
            quiet_since = now
        quiet_left = _DRAIN_QUIET_SECONDS - (now - quiet_since)
        if quiet_left <= 0:
            return 0
        await asyncio.sleep(min(quiet_left, deadline - now, _DRAIN_POLL_SECONDS))


class PackGrantConflict(Exception):
    """The submitted consumable transaction is already recorded against a DIFFERENT
    account. Terminal — ownership of an Apple transaction never moves — so the caller
    must answer 409 PURCHASE_ALREADY_LINKED rather than a retryable status. A 503 here
    tells StoreKit to keep the transaction unfinished and redeliver it on every launch,
    forever, for something that can never succeed.
    """


class CreditServiceUnavailable(Exception):
    """The credit-charge RPC round-trip failed for a transient/system reason
    (Supabase down, network blip, PostgREST/RLS error) — as opposed to the user
    genuinely lacking credits (which the RPC signals by returning NULL, not by
    raising). Callers MUST surface a *retryable* error (SYSTEM_BUSY), never
    INSUFFICIENT_CREDITS: a DB blip must not tell a paying user they're broke.
    """


# Outcomes of `refund_credits` (migration 142) that mean the user is STILL OWED credits.
# `refunded` with a zero amount is NOT here: that is a success whose caps resolved to zero,
# the same way `revoke_purchased_credits` reports `reclaimed: 0`.
#
# Since 2026-09-10 this tuple is DOCUMENTATION and log classification only. The predicate
# below no longer reads it — see REFUND_SETTLED_OUTCOMES for why. This tuple is the RPC's OWN
# owed vocabulary (pinned by test_refund_correctness_helpers); `partial` — minted by
# `refund_ledgered` itself when `0 < refunded < amount` — is owed too, and reads as owed
# through the allow-list below without being an RPC outcome.
REFUND_FAILURE_OUTCOMES = ("no_matching_debit", "no_credits_row", "capped_to_zero")

# Outcomes after which the user is NOT owed anything: the refund moved (whatever the caps let
# it move), it had already moved, or there was never a charge to reverse. THIS is the set the
# predicate consults, as an ALLOW-list.
#
# ⚠️ It used to be a DENY-list over REFUND_FAILURE_OUTCOMES, and that fell OPEN on every shape
# it had not been told about: a dict with no `outcome` key, `outcome: None`, or a name the RPC
# learns to emit tomorrow all read as "it happened". `refund_ledgered` returns exactly that
# empty dict when the RPC answers with a non-dict body, and by the time any of the three
# report call sites ask, the one-shot `is_refunded` CAS is already burned — so a malformed
# reply meant the user silently lost 20 credits AND the REFUND LEAK alert never fired. A
# false alert costs a human one look; a missed one is permanent. Fail toward alerting.
REFUND_SETTLED_OUTCOMES = ("refunded", "already_refunded", "guest", "invalid")


def refund_did_not_happen(outcome) -> bool:
    """True when a `refund_ledgered` result does NOT prove the credits were returned.

    One predicate for all three report call sites, because each of them burns the one-shot
    `research_reports.is_refunded` CAS BEFORE refunding — so "did it actually happen" is the
    only question that decides whether a human has to intervene, and it must be asked the same
    way everywhere.

    Handles three shapes on purpose:
      * `None`   -> transport fault (the round trip failed). Owed.
      * `dict`   -> the migration-142 outcome envelope. Settled ONLY when `outcome` is one of
        REFUND_SETTLED_OUTCOMES; a missing key, `None`, or an unrecognised name is owed.
      * anything else -> the PRE-142 bare integer, treated as "it happened" because that is
        the assumption the old contract forced. Unreachable through `refund_ledgered`, which
        normalises that integer into a `refunded` envelope before returning; kept so a caller
        handing the raw RPC answer straight through gets the documented legacy reading rather
        than a false leak.
    """
    if outcome is None:
        return True
    if isinstance(outcome, dict):
        return outcome.get("outcome") not in REFUND_SETTLED_OUTCOMES
    return False


class CreditService:
    # Report cost in credits on the unified pricing scale (1 chat = 1 credit; a
    # report is ~20x a chat turn by token/$ weight). Sourced from settings so the
    # scale has a single home shared with the chat cost. See migration 100.
    DEEP_RESEARCH_COST = settings.REPORT_CREDIT_COST

    def __init__(self):
        self.supabase = get_supabase()

    def try_charge(
        self, user_id: str, amount: int = DEEP_RESEARCH_COST
    ) -> Optional[int]:
        """Atomically debit `amount` credits from `user_id`.

        Returns the user's new `remaining` balance on success, or None
        if the user has fewer than `amount` credits available (callers
        should treat None as INSUFFICIENT_CREDITS — the RPC returns NULL
        and no row is mutated).

        Raises `CreditServiceUnavailable` if the RPC round-trip itself
        fails (Supabase/network/PostgREST/RLS error). That is a transient
        system condition — distinct from a genuine insufficient balance —
        and the caller must surface it as a *retryable* error, never as
        INSUFFICIENT_CREDITS.
        """
        try:
            result = self.supabase.rpc(
                "charge_user_credits",
                {"p_user_id": user_id, "p_amount": amount},
            ).execute()
        except Exception as e:
            logger.error(
                f"charge_user_credits RPC failed for user={user_id}: "
                f"{type(e).__name__}: {e}"
            )
            raise CreditServiceUnavailable(
                f"charge_user_credits RPC failed for user={user_id}"
            ) from e

        new_remaining = result.data
        if new_remaining is None:
            logger.info(
                f"Credit charge rejected for user={user_id} "
                f"(insufficient balance for {amount})"
            )
            return None

        logger.info(
            f"Charged {amount} credits to user={user_id}, "
            f"new remaining={new_remaining}"
        )
        return int(new_remaining)

    def refund(
        self, user_id: str, amount: int = DEEP_RESEARCH_COST
    ) -> Optional[int]:
        """Increment-back `amount` credits for `user_id`.

        Returns the new `remaining` balance, or None on RPC failure.

        NOT idempotent. The RPC's GREATEST(0, used - amount) only stops
        `used` going negative — calling refund twice when used >= amount
        hands the credits back TWICE. Callers MUST prevent double-refund at
        the row level (see
        research_reconciliation_service.claim_and_mark_failed, which flips
        research_reports.is_refunded atomically and refunds only the winner).
        """
        try:
            result = self.supabase.rpc(
                "refund_user_credits",
                {"p_user_id": user_id, "p_amount": amount},
            ).execute()
        except Exception as e:
            logger.error(
                f"refund_user_credits RPC failed for user={user_id}: "
                f"{type(e).__name__}: {e}"
            )
            return None

        new_remaining = result.data
        logger.info(
            f"Refunded {amount} credits to user={user_id}, "
            f"new remaining={new_remaining}"
        )
        return int(new_remaining) if new_remaining is not None else None

    def ensure_period(self, user_id: str) -> Optional[int]:
        """Lazily roll `user_id` into the current month's tier allocation.

        Calls the `ensure_credit_period` RPC (migration 100): the first call in a
        new calendar month HARD-RESETS `user_credits` to the tier's monthly
        allocation (from `plan_credits`) — use-it-or-lose-it, unused credits do not
        roll over; within a month it is a no-op and just returns the live
        `remaining`. The shared guest sentinel is skipped by the RPC, so its fixed
        balance is untouched.

        NOT wired into any endpoint yet — the enforcement phase calls this right
        before `try_charge` so a paying user always sees a fresh monthly balance.
        Returns the current `remaining`, or raises `CreditServiceUnavailable` on a
        transient RPC failure (mirrors `try_charge`: a DB blip must surface as a
        retryable error, never as an empty/zero balance).
        """
        try:
            result = self.supabase.rpc(
                "ensure_credit_period",
                {"p_user_id": user_id},
            ).execute()
        except Exception as e:
            logger.error(
                f"ensure_credit_period RPC failed for user={user_id}: "
                f"{type(e).__name__}: {e}"
            )
            raise CreditServiceUnavailable(
                f"ensure_credit_period RPC failed for user={user_id}"
            ) from e
        remaining = result.data
        return int(remaining) if remaining is not None else None

    # ── Unified credit gate (enforcement) ──────────────────────────────────
    # precharge / refund_ledgered are the single gate every metered AI action
    # goes through (chat + reports). They wrap the migration-101 combined RPCs
    # (spend_credits / refund_credits) so a charge or refund is ONE round-trip
    # with the ledger row written in the SAME transaction. Costs come from
    # settings (CHAT_CREDIT_COST / REPORT_CREDIT_COST) — configurable per action.

    def precharge(
        self,
        user_id: str,
        amount: int,
        *,
        reason: str,
        ref_id: Optional[str] = None,
    ) -> Optional[int]:
        """Atomic pre-flight charge for a metered AI action (the unified gate).

        Calls the `spend_credits` RPC (migration 101): reset-if-due + atomic
        check-and-debit + ledger, one transaction. Returns the new `remaining`
        on success, or None when the balance is insufficient — callers map None
        to HTTP 402 and must NOT start the Gemini/report work. Raises
        `CreditServiceUnavailable` on a transient RPC failure (callers → 409
        SYSTEM_BUSY): a DB blip must never masquerade as INSUFFICIENT_CREDITS.

        PRECONDITION: call only for AUTHENTICATED users. Guests are not metered
        (the shared GUEST_USER_ID pool would deplete for everyone) — endpoints
        branch on the guest sentinel before reaching here; `spend_credits` also
        guest-guards as defense-in-depth.

        SINGLE-SHOT, never retried: `spend_credits` is not idempotent. SYNC — call it via
        `asyncio.to_thread` from async code.
        """
        _warn_if_on_event_loop("spend_credits", user_id, ref_id)
        try:
            result = self.supabase.rpc(
                "spend_credits",
                {
                    "p_user_id": user_id,
                    "p_amount": amount,
                    "p_reason": reason,
                    "p_ref_id": ref_id,
                },
            ).execute()
        except Exception as e:
            # We cannot distinguish "never committed" from "committed then the HTTP response was
            # lost" — spend_credits is not idempotent, so it is never retried here. All three
            # doors (chat, both report doors) answer this with a COMPENSATING
            # `refund_ledgered(..., ref_id=<the same ChargeRef>, quiet_no_match=True)`: it
            # reverses the debit if it landed and answers a quiet `no_matching_debit` if it
            # never did. A debit still queued at the edge that commits AFTER its compensation
            # is the residual case this line exists for — greppable by the ref, manually
            # correctable via the credit_transactions ledger. The caller answers a retryable
            # 409 (never 402).
            logger.error(
                "POSSIBLE LOST CHARGE: spend_credits RPC failed for user=%s amount=%s "
                "reason=%s ref_id=%s (%s: %s) — the caller compensates with the same ref; if "
                "the debit shows in credit_transactions unreversed with no delivered action, "
                "reconcile manually",
                user_id, amount, reason, ref_id, type(e).__name__, e,
            )
            raise CreditServiceUnavailable(
                f"spend_credits RPC failed for user={user_id}"
            ) from e
        remaining = result.data
        if remaining is None:
            logger.info(
                f"Credit precharge rejected for user={user_id} "
                f"(insufficient for {amount}, reason={reason})"
            )
            return None
        logger.info(
            f"Precharged {amount} credits to user={user_id} (reason={reason}), "
            f"remaining={remaining}"
        )
        return int(remaining)

    # ── Purchased pool (consumable credit packs) ───────────────────────────
    # A SECOND, never-expiring balance. App Store Guideline 3.1.1 forbids purchased
    # credits expiring, so these never touch `total`/`used` — the three tier RPCs
    # (ensure_credit_period / grant_tier_upgrade / revoke_tier_credits) would each
    # destroy or mishandle them. See migration 117.

    def grant_purchased(
        self,
        *,
        user_id: str,
        transaction_id: str,
        product_id: str,
        credits: int,
        environment: str = "Production",
        original_transaction_id: Optional[str] = None,
        price_cents: Optional[int] = None,
        app_account_token: Optional[str] = None,
        purchased_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Grant a verified consumable purchase, exactly once.

        Wraps the `add_purchased_credits` RPC (migration 117), whose idempotency is an
        `INSERT ... ON CONFLICT DO NOTHING` on `credit_purchases (environment,
        transaction_id)` in the SAME transaction as the balance update. Safe to call as
        often as StoreKit redelivers, which is every app launch until the client calls
        `Transaction.finish()`.

        Returns the RPC's outcome dict: `{"outcome": "granted"|"replay", "credits": N,
        "spendable": N}`. Both are SUCCESS — `replay` is the common case and is what tells
        the client it may finish the transaction.

        Raises:
          * `PackGrantConflict` — the transaction belongs to another account (→ 409).
          * `CreditServiceUnavailable` — transient RPC failure, OR an `invalid` outcome.
            Both must surface as retryable: the user has ALREADY BEEN CHARGED by Apple, so
            the client must leave the transaction unfinished and let it redeliver. Never
            convert either into a success.
        """
        try:
            result = self.supabase.rpc(
                "add_purchased_credits",
                {
                    "p_transaction_id": transaction_id,
                    "p_user_id": user_id,
                    "p_product_id": product_id,
                    "p_credits": credits,
                    "p_environment": environment,
                    "p_original_transaction_id": original_transaction_id,
                    "p_price_cents": price_cents,
                    "p_app_account_token": app_account_token,
                    "p_purchased_at": purchased_at,
                },
            ).execute()
        except Exception as e:
            # UNLIKE precharge, this is safe to retry blindly: the RPC is idempotent by
            # transaction id, so a committed-but-lost response is repaired by the next
            # delivery rather than double-granting.
            logger.error(
                "add_purchased_credits RPC failed for user=%s txn=%s product=%s "
                "credits=%s (%s: %s) — user WAS charged by Apple; client must leave the "
                "transaction unfinished so StoreKit redelivers. If this is "
                "PGRST202/undefined function, migration 117 has not been applied.",
                user_id, transaction_id, product_id, credits, type(e).__name__, e,
            )
            raise CreditServiceUnavailable(
                f"add_purchased_credits RPC failed for user={user_id}"
            ) from e

        payload = result.data if isinstance(result.data, dict) else {}
        outcome = payload.get("outcome")

        if outcome == "conflict":
            logger.warning(
                "Credit pack txn=%s submitted by user=%s but owned by user=%s — refusing",
                transaction_id, user_id, payload.get("owner_user_id"),
            )
            raise PackGrantConflict(
                f"transaction {transaction_id} is bound to another account"
            )

        if outcome not in ("granted", "replay"):
            # `invalid` (guest sentinel, missing id, non-positive credits) or an
            # unrecognised shape. The user paid, so this must NOT read as success.
            logger.error(
                "add_purchased_credits returned %r for user=%s txn=%s product=%s "
                "credits=%s — purchase NOT applied",
                payload or result.data, user_id, transaction_id, product_id, credits,
            )
            raise CreditServiceUnavailable(
                f"add_purchased_credits rejected txn={transaction_id}: {outcome!r}"
            )

        logger.info(
            "Credit pack %s for user=%s txn=%s product=%s (+%s credits, spendable=%s)",
            outcome, user_id, transaction_id, product_id,
            payload.get("credits"), payload.get("spendable"),
        )
        return payload

    def revoke_purchased(
        self, *, transaction_id: str, environment: str = "Production"
    ) -> Optional[Dict[str, Any]]:
        """Claw back a REFUNDED credit pack. Best-effort; never raises.

        Wraps `revoke_purchased_credits` (migration 117). Idempotent, so Apple's up-to-5
        REFUND redeliveries are free. Floors at `purchased_used`: credits already spent are
        a business loss, not something to model as a negative balance.

        Never raises because the caller is the App Store webhook — raising there returns a
        non-2xx, which makes Apple redeliver a notification we may already have applied.
        """
        try:
            result = self.supabase.rpc(
                "revoke_purchased_credits",
                {
                    "p_transaction_id": transaction_id,
                    "p_environment": environment,
                },
            ).execute()
        except Exception as e:
            logger.error(
                "REVOKE LEAK: revoke_purchased_credits RPC failed for txn=%s (%s: %s) — "
                "the refunded user keeps their unspent purchased credits; manual "
                "correction may be needed. If PGRST202, migration 117 is not applied.",
                transaction_id, type(e).__name__, e,
            )
            return None

        payload = result.data if isinstance(result.data, dict) else {}
        reclaimed = payload.get("reclaimed")
        if payload.get("outcome") == "revoked" and reclaimed == 0:
            # Bought, spent in full, then refunded: arithmetically correct (we floor at
            # `purchased_used`) but a real, unpriced business exposure. Apple's mechanism
            # for contesting this is CONSUMPTION_REQUEST, which needs an App Store Server
            # API client this repo does not have. Logged distinctly so the exposure is
            # measurable before deciding to build one.
            logger.warning(
                "PACK REFUNDED AFTER FULL CONSUMPTION: txn=%s reclaimed 0 credits — "
                "the pack was entirely spent before the refund landed",
                transaction_id,
            )
        else:
            logger.info(
                "Credit pack revoke txn=%s outcome=%s reclaimed=%s spendable=%s",
                transaction_id, payload.get("outcome"), reclaimed,
                payload.get("spendable"),
            )
        return payload

    def refund_ledgered(
        self,
        user_id: str,
        amount: int,
        *,
        reason: str,
        ref_id: Optional[str] = None,
        quiet_no_match: bool = False,
        quiet_transport: bool = False,
    ) -> Optional[dict]:
        """Best-effort refund + ledger for a non-delivered metered action.

        Calls `refund_credits`. Never raises — a refund failure must not affect the
        (already-failed) action's response — but logs a greppable "REFUND LEAK" marker so a
        stranded charge is diagnosable. Callers still guarantee at-most-once (chat: a
        once-only settlement flag; reports: the research_reports.is_refunded CAS).

        Transient transport faults are RETRIED (bounded, `retry_idempotent_sync`) only when
        `ref_id` is a `ChargeRef`: it names exactly one debit, so a replay of a refund that
        committed but lost its reply finds that debit already reversed and answers
        `already_refunded`, and two concurrent refunds serialize on the `user_credits` row
        lock the RPC takes, so the second answers the same. A plain-`str` ref (a legacy bare
        ticker, `None`) is single-shot: replayed, it would pair with a SIBLING debit. SYNC,
        and it may sleep through the retry backoff — call it via `asyncio.to_thread`.

        Returns the RPC's outcome payload, or **None strictly for a transport fault** — the
        same contract as `revoke_purchased`, so `None` can never be confused with a business
        outcome. `outcome` is one of: refunded | already_refunded | no_matching_debit | guest |
        invalid | no_credits_row, with `refunded` carrying the amount ACTUALLY moved.

        ⚠️ Why this reports outcomes at all (migration 142). Before it, every branch returned
        `spendable`, so a refund that moved ZERO was byte-identical to one that worked, and
        this method logged "Refunded 20 credits ..." either way. That mattered because all
        three report sites burn the `is_refunded` CAS BEFORE calling: there is no retry, the
        user silently loses 20 credits, and iOS then shows a "[Refunded]" chip off that flag.
        The only signal was a Postgres RAISE WARNING that PostgREST never surfaces.

        The split between `already_refunded` (benign replay) and `no_matching_debit` (the user
        is owed) is deliberate: escalating both would trade a silent-money bug for alert
        fatigue, which is how the loud one ends up ignored.

        PRECONDITION: authenticated users only (guest is a no-op both here via
        the RPC guard and at the endpoint branch).
        """
        _warn_if_on_event_loop("refund_credits", user_id, ref_id)
        replay_safe = isinstance(ref_id, ChargeRef)

        def _call():
            return self.supabase.rpc(
                "refund_credits",
                {
                    "p_user_id": user_id,
                    "p_amount": amount,
                    "p_reason": reason,
                    "p_ref_id": ref_id,
                },
            ).execute()

        try:
            if replay_safe:
                result = retry_idempotent_sync(
                    _call, what=f"refund_credits ref_id={ref_id}", logger=logger,
                )
            else:
                result = _call()
        except Exception as e:
            transient = is_transient_supabase_error(e)
            retried = " after transient retries" if (replay_safe and transient) else ""
            if quiet_transport and transient:
                # The caller is RE-CHECKING a refund (the reconciliation retry over an
                # unstamped row) and will re-check on its next pass, so a TRANSIENT fault here
                # is not a page: it proves nothing either way. A non-transient failure still
                # pages below — repeated every pass, it would otherwise hide a refund that is
                # really owed (e.g. a claimer killed between its claim and its refund).
                logger.warning(
                    "refund_credits RPC failed transiently on a re-check for user=%s amount=%s "
                    "reason=%s ref_id=%s (%s: %s)%s — the caller re-checks later",
                    user_id, amount, reason, ref_id, type(e).__name__, e, retried,
                )
                return None
            logger.error(
                "REFUND LEAK: refund_credits RPC failed for user=%s amount=%s "
                "reason=%s ref_id=%s (%s: %s)%s — manual credit correction may be needed",
                user_id, amount, reason, ref_id, type(e).__name__, e, retried,
            )
            return None
        data = result.data

        # TRANSITIONAL — remove once migration 142 is applied everywhere.
        # Pre-142 the RPC returned a bare INTEGER (`spendable`). Deploying this method before
        # the migration is the REQUIRED order, so it must not choke on the old shape: `int` is
        # normalised into the new envelope with `refunded=None` ("moved an unknown amount"),
        # which suppresses the amount-comparison logging below rather than guessing.
        if isinstance(data, (int, float)) and not isinstance(data, bool):
            logger.info(
                "refund_credits answered with the pre-142 INTEGER contract "
                "(user=%s reason=%s) — apply migration 142", user_id, reason,
            )
            return {"outcome": "refunded", "refunded": None, "spendable": int(data)}

        payload = data if isinstance(data, dict) else {}
        outcome = payload.get("outcome")
        refunded = payload.get("refunded")

        if outcome == "capped_to_zero":
            # A debit WAS matched but the pools absorbed none of it. The reachable path is the
            # month boundary: `ensure_credit_period` resets `used` to 0, so a report charged in
            # month M and refunded in M+1 — which the reconciliation sweep does on its own
            # schedule — finds nothing to give back. Same consequence as a missing debit: the
            # user is owed and the one-shot guard is spent.
            logger.error(
                "REFUND LEAK: refund_credits matched the debit for user=%s ref_id=%s but the "
                "credit pools absorbed none of the %s credits (reason=%s) — the user is OWED "
                "and the one-shot refund guard is already burned; manual correction needed. "
                "Most likely the charge and the refund straddled a monthly reset.",
                user_id, ref_id, amount, reason,
            )
        elif outcome == "no_matching_debit" and quiet_no_match:
            # EXPECTED by the caller: a COMPENSATING refund fired after a precharge whose
            # transport failed — "no debit" is the good outcome (the charge never committed) —
            # or the reconciliation retry re-checking an unstamped report, where "no debit
            # under the per-report ref" means it was charged under the legacy bare ticker.
            logger.info(
                "refund_credits: no debit under ref_id=%s for user=%s (reason=%s) — expected "
                "by the caller (a precharge that never committed, or a legacy-ref report)",
                ref_id, user_id, reason,
            )
        elif outcome == "no_matching_debit":
            # THE one that must page. `logger.error` is the alert: Sentry's LoggingIntegration
            # captures at event_level=ERROR and its rule pings Discord. Every id a manual
            # correction needs is on this line, and no PII (send_default_pii=False).
            logger.error(
                "REFUND LEAK: refund_credits found no matching debit for user=%s ref_id=%s "
                "amount=%s reason=%s — the user is OWED %s credits and the one-shot refund "
                "guard is already burned; manual credit correction needed",
                user_id, ref_id, amount, reason, amount,
            )
        elif outcome == "no_credits_row":
            logger.error(
                "REFUND LEAK: refund_credits found no user_credits row for user=%s "
                "amount=%s reason=%s — nothing was refunded",
                user_id, amount, reason,
            )
        elif outcome == "already_refunded":
            # Benign: the charge was given back already. Deliberately INFO, not ERROR — this
            # is the expected shape of a replayed settlement and must not create an issue.
            logger.info(
                "refund_credits: charge for user=%s ref_id=%s amount=%s was already "
                "refunded — no double refund",
                user_id, ref_id, amount,
            )
        elif outcome in ("guest", "invalid"):
            logger.info(
                "refund_credits no-op (outcome=%s) for user=%s amount=%s reason=%s",
                outcome, user_id, amount, reason,
            )
        elif outcome != "refunded":
            # Every outcome the RPC is known to emit is handled above, so reaching here means
            # the body was not the 142 envelope at all — a non-dict `.data` became `{}` on the
            # `payload =` line, or the RPC now emits a name this code has never seen. Either
            # way nothing is PROVEN to have moved, and this used to fall through to the
            # "Refunded %s credits" INFO line below with `refunded=None` — a success message
            # for a refund that may not exist, on a path where the CAS is already spent.
            # Same marker as the other leaks so the existing Sentry rule pages on it.
            logger.error(
                "REFUND LEAK: refund_credits answered with an unrecognised outcome=%r "
                "(payload keys=%s) for user=%s ref_id=%s amount=%s reason=%s — cannot confirm "
                "the refund moved; the one-shot guard is already burned; manual check needed",
                outcome, sorted(payload.keys()), user_id, ref_id, amount, reason,
            )
        elif isinstance(refunded, int) and 0 < refunded < amount:
            # The LEAST() caps can move less than requested — the reachable case is a charge
            # and a refund that straddle a monthly reset with a SMALL new-period `used`
            # (charged 20 at 23:50, one chat turn after midnight, the sweep refunds 1). The
            # user is OWED the remainder and the one-shot guard is already burned, so this is
            # a leak, not a footnote: it used to log a WARNING and answer `outcome: refunded`,
            # which every report site read as settled — the phone was told "your 20 credits
            # have been returned" and nothing paged. Re-labelled `partial` so the allow-list
            # predicate flips all three sites to their leak branch with no further edits.
            logger.error(
                "REFUND LEAK: PARTIAL REFUND — refund_credits moved %s of %s credits for "
                "user=%s ref_id=%s reason=%s; the user is OWED %s credits and the one-shot "
                "refund guard is already burned; manual credit correction needed. Most likely "
                "the charge and the refund straddled a monthly reset.",
                refunded, amount, user_id, ref_id, reason, amount - refunded,
            )
            return {**payload, "outcome": "partial", "requested": amount}
        elif isinstance(refunded, int) and refunded == 0 and amount > 0:
            # `outcome: refunded` with nothing moved is the RPC's deliberate no-`ref_id`
            # shape (`NOT v_searched`): with no debit to match, "owed" is unprovable and
            # escalating it would page on every legacy caller. WARNING, not ERROR.
            logger.warning(
                "PARTIAL REFUND: refund_credits moved 0 of %s credits for user=%s "
                "ref_id=%s reason=%s — the pools could not absorb any of it",
                amount, user_id, ref_id, reason,
            )
        else:
            logger.info(
                "Refunded %s credits to user=%s (reason=%s), spendable=%s",
                refunded, user_id, reason, payload.get("spendable"),
            )

        return payload
