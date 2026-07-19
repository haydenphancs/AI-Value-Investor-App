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
    DEEP_RESEARCH_COST = 5

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
