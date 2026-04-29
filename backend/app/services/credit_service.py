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


class CreditService:
    DEEP_RESEARCH_COST = 5

    def __init__(self):
        self.supabase = get_supabase()

    def try_charge(
        self, user_id: str, amount: int = DEEP_RESEARCH_COST
    ) -> Optional[int]:
        """Atomically debit `amount` credits from `user_id`.

        Returns the user's new `remaining` balance on success, or None
        if the user has fewer than `amount` credits available. Callers
        should treat None as INSUFFICIENT_CREDITS.
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
            return None

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
        Idempotency for double-calls is guarded server-side via
        GREATEST(0, used - amount) — never drives `used` negative.
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
