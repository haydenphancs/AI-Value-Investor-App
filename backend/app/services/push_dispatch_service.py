"""Push dispatch — deciding WHO gets an alert, and making sure they get it once.

`PushService` knows how to talk to APNs. This layer sits above it and owns the three
decisions that determine whether a notification is welcome or an uninstall:

  1. **Who.** Reverse lookup from a ticker to the users watching it
     (`idx_watchlist_items_ticker`, migration 108).
  2. **Whether.** The user's own `notify_*` preference, synced from the app into
     `user_settings.preferences`. Absent → treated as ON, matching the iOS default.
  3. **Once.** A unique claim in `push_send_log` (migration 109) inserted BEFORE the
     send, so a retry, a re-trip of the same scope, or two overlapping Railway
     instances cannot produce a second buzz.

A duplicate cache write is invisible; a duplicate push is a buzz in someone's pocket,
and it cannot be taken back. That asymmetry is why the claim comes first and why
every failure path here declines to send rather than risking a repeat.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from app.database import get_supabase
from app.services.push_service import PushService

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Hard ceiling on how many users one ticker's alert fans out to in a single cycle.
# Each recipient costs a token lookup + an APNs POST, and a mega-cap moving on a busy
# day could otherwise stall the sweeper behind thousands of sequential sends. Anything
# skipped is LOGGED, never silently dropped — a truncated fan-out that looked complete
# would read as "push works" while most users got nothing.
MAX_RECIPIENTS_PER_SCOPE = 500


def trading_date_et() -> str:
    """Today's ET date — the dedup bucket, matching the app's trading-day convention."""
    return datetime.now(_ET).date().isoformat()


class PushDispatchService:
    RETENTION_DAYS = 30

    def __init__(self, push: Optional[PushService] = None):
        self.supabase = get_supabase()
        self._push = push

    @property
    def push(self) -> PushService:
        # Lazy so constructing this service never builds an APNs client it may not use.
        if self._push is None:
            self._push = PushService()
        return self._push

    # ── who ──────────────────────────────────────────────────────────

    def watchers_of(self, ticker: str) -> List[str]:
        """User ids watching `ticker`, capped and de-duplicated.

        Uses the ticker-leading index added in migration 108; before that this was a
        sequential scan of the whole watchlist table.
        """
        try:
            rows = (
                self.supabase.table("watchlist_items")
                .select("user_id")
                .eq("ticker", ticker.upper())
                .limit(MAX_RECIPIENTS_PER_SCOPE + 1)
                .execute()
                .data
                or []
            )
        except Exception as e:
            logger.warning(
                "push: watcher lookup failed for %s (%s: %s) — no alerts this cycle",
                ticker, type(e).__name__, e,
            )
            return []

        users = list(dict.fromkeys(r["user_id"] for r in rows if r.get("user_id")))
        if len(users) > MAX_RECIPIENTS_PER_SCOPE:
            logger.warning(
                "push: %s has %d+ watchers — notifying the first %d only this cycle",
                ticker, len(users), MAX_RECIPIENTS_PER_SCOPE,
            )
            users = users[:MAX_RECIPIENTS_PER_SCOPE]
        return users

    # ── whether ──────────────────────────────────────────────────────

    def preference_enabled(self, user_id: str, key: str) -> bool:
        """Whether `user_id` wants this category.

        Absent preference → True, matching the iOS default for the keys used here.
        A FAILED read also returns True: the alternative is silently withholding
        notifications a user asked for because of a transient DB blip, which is
        indistinguishable from the feature being broken.
        """
        try:
            rows = (
                self.supabase.table("user_settings")
                .select("preferences")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
                .data
                or []
            )
        except Exception as e:
            logger.warning(
                "push: settings read failed for user=%s (%s: %s) — assuming opted IN",
                user_id, type(e).__name__, e,
            )
            return True
        if not rows:
            return True
        value = (rows[0].get("preferences") or {}).get(key)
        return True if value is None else bool(value)

    # ── once ─────────────────────────────────────────────────────────

    def claim_send(self, user_id: str, dedup_key: str) -> bool:
        """Claim the right to send. True = you may send; False = already sent.

        The INSERT is the lock: a unique (user_id, dedup_key) means a concurrent or
        repeat attempt conflicts and returns False, with no coordination between
        instances. Claimed BEFORE the send, deliberately — the failure mode of
        claiming first is a rare missed alert, and of sending first is a duplicate.
        Missing one is forgivable; buzzing twice is not.

        A failed claim round-trip returns False (do NOT send) for the same reason.
        """
        try:
            self.supabase.table("push_send_log").insert(
                {"user_id": user_id, "dedup_key": dedup_key}
            ).execute()
            return True
        except Exception as e:
            # Prefer the STRUCTURED code: supabase-py raises `APIError` with
            # `.code == "23505"` (unique_violation) — verified empirically against a
            # live composite-PK insert. Matching on the message text alone would be
            # one wording change away from treating every duplicate as an unknown
            # error, which fails safe (no send) but would silently suppress alerts
            # forever with only a warning to show for it. The substring check stays as
            # a fallback for any client that surfaces the error differently.
            if getattr(e, "code", None) == "23505":
                return False   # already sent — the normal, expected path
            msg = str(e).lower()
            if "duplicate key" in msg or "23505" in msg:
                return False
            logger.warning(
                "push: dedup claim failed for user=%s key=%s (%s: %s) — NOT sending",
                user_id, dedup_key, type(e).__name__, e,
            )
            return False

    # ── send ─────────────────────────────────────────────────────────

    async def notify_watchers(
        self,
        *,
        ticker: str,
        title: str,
        body: str,
        dedup_key: str,
        preference_key: str = "notify_watchlist_changes",
        data: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Alert everyone watching `ticker`. Returns how many were actually sent.

        Never raises: a push failure must not break the sweep that triggered it.
        """
        if not self.push.enabled:
            # Expected until the APNs key is configured. Debug, not warning — this
            # would otherwise log on every material move, forever.
            logger.debug("push: APNs not configured — skipping alert for %s", ticker)
            return 0

        try:
            users = await asyncio.to_thread(self.watchers_of, ticker)
        except Exception as e:
            # `watchers_of` handles its own errors today, but this method PROMISES
            # never to raise — and it is called from inside the sweeper's generation
            # path, where an escape would mark a successfully generated card as
            # failed. Make the promise true rather than relying on the callee.
            logger.warning(
                "push: watcher lookup raised for %s (%s: %s) — no alerts this cycle",
                ticker, type(e).__name__, e,
            )
            return 0
        if not users:
            return 0

        sent = 0
        for user_id in users:
            try:
                if not await asyncio.to_thread(
                    self.preference_enabled, user_id, preference_key
                ):
                    continue
                if not await asyncio.to_thread(self.claim_send, user_id, dedup_key):
                    continue
                accepted = await self.push.send_to_user(
                    user_id, title=title, body=body, data=data
                )
                if accepted:
                    sent += 1
            except Exception as e:
                # One bad recipient must not abandon the rest of the fan-out.
                logger.warning(
                    "push: send to user=%s failed (%s: %s)",
                    user_id, type(e).__name__, e,
                )

        if sent:
            logger.info(
                "push: alerted %d/%d watcher(s) of %s (key=%s)",
                sent, len(users), ticker, dedup_key,
            )
        return sent

    # ── housekeeping ─────────────────────────────────────────────────

    def sweep_expired(self) -> int:
        """Drop dedup rows older than the retention window. Best-effort.

        The window only has to outlive the dedup horizon (one trading day today), so
        30 days is generous; without a sweep this table grows one row per push forever.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.RETENTION_DAYS)).isoformat()
        try:
            result = (
                self.supabase.table("push_send_log")
                .delete()
                .lt("sent_at", cutoff)
                .execute()
            )
            deleted = len(result.data or [])
            if deleted:
                logger.info("push_send_log sweep: deleted %d row(s) older than %s", deleted, cutoff)
            return deleted
        except Exception as e:
            logger.warning(
                "push_send_log sweep failed (cutoff=%s): %s: %s",
                cutoff, type(e).__name__, e,
            )
            return 0


_service: Optional[PushDispatchService] = None


def get_push_dispatch_service() -> PushDispatchService:
    global _service
    if _service is None:
        _service = PushDispatchService()
    return _service
