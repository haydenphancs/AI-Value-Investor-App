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
  - `read_tokens` / `add_tokens_strict` (2026-11-27) RAISE `ChatBudgetUnavailable` instead: they
    back a fail-CLOSED money cap (the Updates jump search's fallback lookup keeps its month's
    spend, in micro-dollars, in `token_count` of its own uuid5 bucket keyed on the LAST day of the
    ET month, which the 7-day sweep below never reaches mid-month).

`try_claim_turn`, `refund_turn` and `record_tokens` take an optional keyword-only `day` (an ISO
date; default today in ET, so every existing call is unchanged) — the RPCs already take `p_day`.

The bucket key is the chat abuse/cost identity (a real user id, or a per-INSTALL
guest id) — NOT `chat_sessions.user_id`. See `dependencies.chat_identity_key`.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional
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


def _day_key(day: Optional[str]) -> str:
    """`day` as a budget-day key: None → today (ET); an ISO date string as is. Anything else is a
    caller's bug and raises ValueError before any RPC."""
    if day is None:
        return _budget_day()
    if not isinstance(day, str):
        raise ValueError(f"budget day must be an ISO date string, not {type(day).__name__}")
    try:
        parsed = date.fromisoformat(day)
    except ValueError:
        raise ValueError(f"budget day {day[:20]!r} is not an ISO date") from None
    if parsed.isoformat() != day:
        raise ValueError(f"budget day {day[:20]!r} is not in YYYY-MM-DD form")
    return day


#: `add_chat_tokens` takes an INTEGER (int4) amount.
_MAX_TOKENS_PER_ADD = 2_147_483_647


def _token_count(value: Any) -> Optional[int]:
    """A `token_count` (BIGINT) as PostgREST returns it: a non-negative int, or an all-digit string.
    None for anything else (a bool, a float, a negative)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def budget_day() -> str:
    """Today's budget-day key (ET, ISO date) — the day every `chat_usage_budget` row is keyed on.

    Public for in-process latches that must roll over at exactly the same midnight as the rows
    they mirror (the web search's automatic-tier exhaustion latch, `chat_web_search_service`):
    a latch on a different clock would either keep a refilled bucket closed or reopen a full one.
    """
    return _budget_day()


class ChatBudgetService:
    def __init__(self):
        self.supabase = get_supabase()

    def try_claim_turn(self, bucket_key: str, limit: Optional[int] = None, *,
                       day: Optional[str] = None) -> int:
        """Atomically claim one chat turn for `bucket_key` today (or on `day`, an ISO date).

        Returns the new turn count on success, or -1 when the daily cap is reached
        (no mutation). Raises `ChatBudgetUnavailable` if the RPC round-trip fails.

        `limit` defaults to the per-install fair-use cap. It is overridden for the
        IP-derived anti-rotation bucket, which shares this table and RPC but carries a
        much higher ceiling — see `CHAT_DAILY_TURN_LIMIT_PER_IP`.
        """
        limit = settings.CHAT_DAILY_TURN_LIMIT if limit is None else limit
        day_key = _day_key(day)
        try:
            result = self.supabase.rpc(
                "claim_chat_turn",
                {
                    "p_user_id": bucket_key,
                    "p_day": day_key,
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

    def refund_turn(self, bucket_key: str, *, day: Optional[str] = None) -> None:
        """Best-effort: release a previously-claimed turn (migration 097) when generation FAILED,
        so a Gemini outage doesn't drain a user's daily cap. Never raises — a refund failure must
        not affect the (already-failed) turn's error response. The RPC floors at 0, so a stray
        double-release can't grant extra turns. `day` (an ISO date) releases a turn claimed on that
        day; default today (ET)."""
        day_key = _day_key(day)
        try:
            self.supabase.rpc(
                "release_chat_turn",
                {"p_user_id": bucket_key, "p_day": day_key},
            ).execute()
        except Exception as e:
            logger.warning(
                "release_chat_turn failed for bucket=%s (%s: %s) — turn not refunded",
                bucket_key, type(e).__name__, e,
            )

    # ── Free follow-up (migration 154) ──────────────────────────────────────
    #
    # These are keyed on the SESSION id, not on `bucket_key`. Everything else in this
    # service is keyed on the chat abuse/cost identity; the allowance is per conversation
    # because that is the only scope in which "a follow-up" means anything.

    def claim_free_followup(self, session_id: Optional[str]) -> bool:
        """Atomically consume this session's free follow-up, if one is live.

        True  → the caller must NOT charge; the allowance is spent (cleared in the same
                statement, so two racing turns cannot both be told "free").
        False → no live allowance; charge normally.

        FAILS CLOSED. Every other budget path in this file fails OPEN because a DB blip
        must not wall a user out of chat — but the failure here is the mirror image: a
        blip would hand out a free turn we cannot record as spent, and a sustained outage
        would make chat free for everyone. Charging is also self-healing, because the
        row was never cleared: the allowance is still there for the next turn. The user
        loses nothing permanently, they just pay a beat later than promised.
        """
        if not session_id:
            return False
        try:
            result = self.supabase.rpc(
                "claim_free_followup", {"p_session_id": session_id}
            ).execute()
        except Exception as e:
            logger.warning(
                "claim_free_followup failed for session=%s (%s: %s) — charging this turn "
                "(allowance is untouched and will apply to the next one)",
                session_id, type(e).__name__, e,
            )
            return False
        # STRICTLY `is True`. `bool(result.data)` looked equivalent and is not: the SDK
        # JSON-decodes the RPC result, and anything unexpected in that slot — the string
        # "false", a wrapping list/dict from a future PostgREST shape, a row count — is
        # TRUTHY, which would silently make every chat turn free. The failure direction
        # matters more than the tidiness here: an unrecognised value must charge.
        return result.data is True

    def grant_free_followup(self, session_id: Optional[str], seconds: Optional[int] = None) -> None:
        """Open the session's free-follow-up window. Best-effort; never raises.

        CALL ONLY AFTER A TURN THAT WAS ACTUALLY CHARGED. Calling it after a free turn
        would let one credit be ridden indefinitely by always replying inside the window
        — the "only a charged turn grants one" rule is what bounds this to 2 turns per
        credit, and it is enforced here at the call site, not in SQL.

        A failure is silent to the user by design: they simply do not get a free
        follow-up, which is strictly no worse than the pricing they were promised.
        """
        if not session_id:
            return
        window = settings.CHAT_FREE_FOLLOWUP_SECONDS if seconds is None else seconds
        try:
            self.supabase.rpc(
                "grant_free_followup",
                {"p_session_id": session_id, "p_seconds": int(window or 0)},
            ).execute()
        except Exception as e:
            logger.warning(
                "grant_free_followup failed for session=%s (%s: %s) — no free follow-up "
                "for this turn",
                session_id, type(e).__name__, e,
            )

    def record_tokens(self, bucket_key: str, tokens: int, *, day: Optional[str] = None) -> None:
        """Best-effort: accumulate approximate token spend for today (or `day`, an ISO date). Never
        raises on the RPC — a failure here must not affect the answered turn."""
        if not tokens or tokens <= 0:
            return
        day_key = _day_key(day)
        try:
            self.supabase.rpc(
                "add_chat_tokens",
                {
                    "p_user_id": bucket_key,
                    "p_day": day_key,
                    "p_tokens": int(tokens),
                },
            ).execute()
        except Exception as e:
            logger.warning(
                "add_chat_tokens failed for bucket=%s (%s: %s) — spend not recorded",
                bucket_key, type(e).__name__, e,
            )

    def read_tokens(self, bucket_key: str, day: str) -> int:
        """The `token_count` of `bucket_key` on `day` (an ISO date); 0 when there is no row.

        RAISES `ChatBudgetUnavailable` when the read fails or returns a shape it cannot vouch for
        (a negative, a float, a bool, a non-digit string, a row without the column) — its caller is a
        fail-CLOSED money cap, which must never read "nothing spent" from an error."""
        day_key = _day_key(day)
        try:
            result = (
                self.supabase.table("chat_usage_budget")
                .select("token_count")
                .eq("user_id", bucket_key)
                .eq("budget_day", day_key)
                .limit(1)
                .execute()
            )
        except Exception as e:
            logger.error(
                "chat_usage_budget read failed for bucket=%s day=%s: %s: %s",
                bucket_key, day_key, type(e).__name__, e,
            )
            raise ChatBudgetUnavailable(
                f"chat_usage_budget read failed for bucket={bucket_key}"
            ) from e
        rows = result.data
        if rows is None or not isinstance(rows, list):
            logger.error(
                "chat_usage_budget read for bucket=%s day=%s returned %s, not a list — treating as "
                "unavailable", bucket_key, day_key, type(rows).__name__,
            )
            raise ChatBudgetUnavailable(f"chat_usage_budget read returned no list for bucket={bucket_key}")
        if not rows:
            return 0
        row = rows[0]
        count = _token_count(row.get("token_count")) if isinstance(row, dict) else None
        if count is None:
            logger.error(
                "chat_usage_budget read for bucket=%s day=%s returned an unreadable token_count — "
                "treating as unavailable", bucket_key, day_key,
            )
            raise ChatBudgetUnavailable(f"chat_usage_budget token_count unreadable for bucket={bucket_key}")
        return count

    def add_tokens_strict(self, bucket_key: str, tokens: int, day: str) -> int:
        """Add `tokens` (a whole number from 1 to the RPC's int4 maximum) to `bucket_key` on `day`
        (an ISO date) and return the new total.

        Unlike `record_tokens` this RAISES `ChatBudgetUnavailable` when the RPC fails or answers
        something other than a non-negative total — it pre-charges a fail-CLOSED money cap, which
        must not make a paid call after a charge it cannot confirm. A bad amount is a caller's bug
        (ValueError, no RPC): `add_chat_tokens` floors anything ≤ 0 to 0, so it can never correct
        a charge downward."""
        if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= _MAX_TOKENS_PER_ADD:
            raise ValueError(f"add_tokens_strict needs a whole number from 1 to {_MAX_TOKENS_PER_ADD}")
        day_key = _day_key(day)
        try:
            result = self.supabase.rpc(
                "add_chat_tokens",
                {"p_user_id": bucket_key, "p_day": day_key, "p_tokens": tokens},
            ).execute()
        except Exception as e:
            logger.error(
                "add_chat_tokens (strict) failed for bucket=%s day=%s: %s: %s",
                bucket_key, day_key, type(e).__name__, e,
            )
            raise ChatBudgetUnavailable(f"add_chat_tokens failed for bucket={bucket_key}") from e
        total = _token_count(result.data)
        if total is None:
            logger.error(
                "add_chat_tokens (strict) for bucket=%s day=%s returned no readable total — treating "
                "as unavailable", bucket_key, day_key,
            )
            raise ChatBudgetUnavailable(f"add_chat_tokens returned no total for bucket={bucket_key}")
        return total

    # Rows older than this are deleted by the sweep below. Only TODAY's row is ever
    # read (`_budget_day()`) — or, for the jump lookup's month bucket, a row keyed on the
    # LAST day of the current month, which is never in the past while it is read — so
    # anything past the window is pure accumulation. Kept slightly wider than a day so a
    # timezone edge or a clock skew can't delete a row that is still live.
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
