"""
Credit Service — atomic charge + refund for Generate Analysis.

The `charge_user_credits` and `refund_user_credits` Postgres functions
(migration 041) do the actual mutation in a single round-trip so two
concurrent Generate Analysis requests can't both succeed against a
shared low balance.

`remaining` on `user_credits` is a GENERATED column (total - used),
so all mutations target `used` and read `remaining` afterward.
"""

import logging
from typing import Optional

from app.config import settings
from app.database import get_supabase

logger = logging.getLogger(__name__)


class CreditServiceUnavailable(Exception):
    """The credit-charge RPC round-trip failed for a transient/system reason
    (Supabase down, network blip, PostgREST/RLS error) — as opposed to the user
    genuinely lacking credits (which the RPC signals by returning NULL, not by
    raising). Callers MUST surface a *retryable* error (SYSTEM_BUSY), never
    INSUFFICIENT_CREDITS: a DB blip must not tell a paying user they're broke.
    """


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

    def log_transaction(
        self,
        user_id: str,
        delta: int,
        reason: str,
        ref_id: Optional[str] = None,
        balance_after: Optional[int] = None,
    ) -> None:
        """Best-effort append to the `credit_transactions` audit ledger (migration 100).

        Never raises — a ledger write must not break the (already-decided) credit
        action. Failures are logged with context so the drop is diagnosable. Wired in
        during the enforcement phase alongside charge/refund and the monthly reset.
        """
        try:
            self.supabase.rpc(
                "add_credit_transaction",
                {
                    "p_user_id": user_id,
                    "p_delta": delta,
                    "p_reason": reason,
                    "p_ref_id": ref_id,
                    "p_balance_after": balance_after,
                },
            ).execute()
        except Exception as e:
            logger.warning(
                "add_credit_transaction failed for user=%s reason=%s (%s: %s) — "
                "ledger row dropped",
                user_id, reason, type(e).__name__, e,
            )

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
        """
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
            logger.error(
                f"spend_credits RPC failed for user={user_id} amount={amount}: "
                f"{type(e).__name__}: {e}"
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

    def refund_ledgered(
        self,
        user_id: str,
        amount: int,
        *,
        reason: str,
        ref_id: Optional[str] = None,
    ) -> Optional[int]:
        """Best-effort refund + ledger for a non-delivered metered action.

        Calls `refund_credits` (migration 101). Never raises — a refund failure
        must not affect the (already-failed) action's response — but logs a
        greppable "REFUND LEAK" marker so a stranded charge is diagnosable.
        NOT idempotent: callers guarantee at-most-once (chat: a once-only
        settlement flag; reports: the research_reports.is_refunded CAS).

        PRECONDITION: authenticated users only (guest is a no-op both here via
        the RPC guard and at the endpoint branch).
        """
        try:
            result = self.supabase.rpc(
                "refund_credits",
                {
                    "p_user_id": user_id,
                    "p_amount": amount,
                    "p_reason": reason,
                    "p_ref_id": ref_id,
                },
            ).execute()
        except Exception as e:
            logger.error(
                "REFUND LEAK: refund_credits RPC failed for user=%s amount=%s "
                "reason=%s (%s: %s) — manual credit correction may be needed",
                user_id, amount, reason, type(e).__name__, e,
            )
            return None
        remaining = result.data
        logger.info(
            f"Refunded {amount} credits to user={user_id} (reason={reason}), "
            f"remaining={remaining}"
        )
        return int(remaining) if remaining is not None else None
