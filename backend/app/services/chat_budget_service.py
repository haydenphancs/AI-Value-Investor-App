"""Chat daily-budget service — durable per-user denial-of-wallet ceiling.

Mirrors `credit_service.py`: the `claim_chat_turn` / `add_chat_tokens` Postgres
functions (migration 096) do the mutation atomically in one round-trip so concurrent
turns can't both slip past a nearly-exhausted daily cap.

Contract, mirroring CreditService's NULL-vs-raise split:
  - `try_claim_turn` returns the new turn count on success, or -1 when the daily cap
    is reached (the RPC returns -1, no mutation). It RAISES `ChatBudgetUnavailable`
    only when the RPC round-trip itself fails (Supabase/network/PostgREST). The
    endpoint FAILS OPEN on that — a DB blip must never wall a user out of chat.
  - `record_tokens` is best-effort spend accounting; it swallows+logs failures.

The bucket key is the chat abuse/cost identity (a real user id, or a per-INSTALL
guest id) — NOT `chat_sessions.user_id`. See `dependencies.chat_identity_key`.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import get_supabase

logger = logging.getLogger(__name__)

# Budget day boundary. ET matches the app's trading-day convention (migration 089)
# so a user's "daily" allowance rolls at a predictable, market-aligned midnight.
_BUDGET_TZ = ZoneInfo("America/New_York")


class ChatBudgetUnavailable(Exception):
    """The budget RPC round-trip failed for a transient/system reason (Supabase down,
    network blip, PostgREST/RLS error) — as opposed to the user genuinely hitting the
    cap (signalled by the RPC returning -1, not by raising). Callers MUST fail OPEN on
    this: a DB blip must never tell a user they're out of chat for the day."""


def _budget_day() -> str:
    """Today's budget-day key (ET) as an ISO date string."""
    return datetime.now(_BUDGET_TZ).date().isoformat()


class ChatBudgetService:
    def __init__(self):
        self.supabase = get_supabase()

    def try_claim_turn(self, bucket_key: str) -> int:
        """Atomically claim one chat turn for `bucket_key` today.

        Returns the new turn count on success, or -1 when the daily cap is reached
        (no mutation). Raises `ChatBudgetUnavailable` if the RPC round-trip fails.
        """
        limit = settings.CHAT_DAILY_TURN_LIMIT
        try:
            result = self.supabase.rpc(
                "claim_chat_turn",
                {
                    "p_user_id": bucket_key,
                    "p_day": _budget_day(),
                    "p_turn_limit": limit,
                },
            ).execute()
        except Exception as e:
            logger.error(
                "claim_chat_turn RPC failed for bucket=%s: %s: %s",
                bucket_key, type(e).__name__, e,
            )
            raise ChatBudgetUnavailable(
                f"claim_chat_turn RPC failed for bucket={bucket_key}"
            ) from e

        count = result.data
        if count is None:
            # A well-formed RPC always returns an int; None means an unexpected
            # transport shape — treat as unavailable (fail open), not cap-reached.
            logger.error(
                "claim_chat_turn returned no data for bucket=%s — treating as unavailable",
                bucket_key,
            )
            raise ChatBudgetUnavailable(
                f"claim_chat_turn returned no data for bucket={bucket_key}"
            )

        count = int(count)
        if count == -1:
            logger.info(
                "Chat daily cap reached for bucket=%s (limit=%s)", bucket_key, limit
            )
        return count

    def refund_turn(self, bucket_key: str) -> None:
        """Best-effort: release a previously-claimed turn (migration 097) when generation FAILED,
        so a Gemini outage doesn't drain a user's daily cap. Never raises — a refund failure must
        not affect the (already-failed) turn's error response. The RPC floors at 0, so a stray
        double-release can't grant extra turns."""
        try:
            self.supabase.rpc(
                "release_chat_turn",
                {"p_user_id": bucket_key, "p_day": _budget_day()},
            ).execute()
        except Exception as e:
            logger.warning(
                "release_chat_turn failed for bucket=%s (%s: %s) — turn not refunded",
                bucket_key, type(e).__name__, e,
            )

    def record_tokens(self, bucket_key: str, tokens: int) -> None:
        """Best-effort: accumulate approximate token spend for today. Never raises —
        a failure here must not affect the answered turn."""
        if not tokens or tokens <= 0:
            return
        try:
            self.supabase.rpc(
                "add_chat_tokens",
                {
                    "p_user_id": bucket_key,
                    "p_day": _budget_day(),
                    "p_tokens": int(tokens),
                },
            ).execute()
        except Exception as e:
            logger.warning(
                "add_chat_tokens failed for bucket=%s (%s: %s) — spend not recorded",
                bucket_key, type(e).__name__, e,
            )

    # Rows older than this are deleted by the sweep below. Only TODAY's row is ever
    # read (`_budget_day()`), so anything past the window is pure accumulation. Kept
    # slightly wider than a day so a timezone edge or a clock skew can't delete a row
    # that is still live.
    RETENTION_DAYS = 7

    def cleanup_old_budget_rows(self) -> int:
        """Delete `chat_usage_budget` rows older than `RETENTION_DAYS`.

        Migration 096 documented this sweep — "a per-day cleanup sweep deletes by
        budget_day" — and added `idx_chat_usage_budget_day` to support it, but the sweep
        itself was never written. The table therefore grew one row per user per active
        day, forever, keyed by user id: an unbounded store of per-user activity dates
        with no retention bound and no purpose once the day has passed.

        Returns the number of rows deleted (0 on failure — best-effort, never raises,
        because a failed sweep must not take down the caller's background loop).
        """
        cutoff = (
            datetime.now(_BUDGET_TZ).date() - timedelta(days=self.RETENTION_DAYS)
        ).isoformat()
        try:
            result = (
                self.supabase.table("chat_usage_budget")
                .delete()
                .lt("budget_day", cutoff)
                .execute()
            )
            deleted = len(result.data or [])
            if deleted:
                logger.info(
                    "chat_usage_budget sweep: deleted %d row(s) older than %s",
                    deleted, cutoff,
                )
            return deleted
        except Exception as e:
            logger.warning(
                "chat_usage_budget sweep failed (cutoff=%s): %s: %s",
                cutoff, type(e).__name__, e,
            )
            return 0


_chat_budget_service: "ChatBudgetService | None" = None


def get_chat_budget_service() -> ChatBudgetService:
    """Process-wide singleton (matches the get_supabase() singleton lifecycle)."""
    global _chat_budget_service
    if _chat_budget_service is None:
        _chat_budget_service = ChatBudgetService()
    return _chat_budget_service
