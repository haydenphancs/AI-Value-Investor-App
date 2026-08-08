"""Read side of `notification_events` — the in-app notification inbox.

Separate from `push_dispatch_service`, which owns the WRITE side (claim, deliver, stamp).
Splitting them keeps the dispatcher's surface about decisions and this one about
queries, and it means an endpoint can never accidentally reach a send primitive.

Why an inbox exists at all: a push that arrives while the phone is face-down is gone.
Before this table the app had no record of what fired, so "I got an alert about NVDA but
I was driving" ended with the user hunting through the Updates tab. It also happens to
make every sender verifiable without a device, which is why the dispatcher writes a row
for suppressed-but-claimed notifications too.

NO CACHE LAYER HERE, deliberately. The two-tier cache-aside pattern (CLAUDE.md #4) is
for expensive UPSTREAM calls; this is a single indexed Supabase read of a user's own
rows, and caching per-user mutable state keyed by user id would be all of the complexity
of that pattern with none of the benefit — plus a stale unread badge after a mark-read.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from app.database import get_supabase
from app.schemas.notifications import (
    NotificationEventResponse,
    NotificationListResponse,
    _iso,
)

logger = logging.getLogger(__name__)

TABLE = "notification_events"

# Hard ceiling on a page. Bounds both the query and the JSON we hand iOS.
MAX_PAGE = 100
DEFAULT_PAGE = 30

# Ceiling on the unread probe. The badge only has to be right up to "lots"; counting
# 10,000 rows to render "99+" is wasted work. Rows above this are reported as this
# number, and the retention sweep keeps the real total far below it anyway.
UNREAD_PROBE_CAP = 200

# `push_state` values that mean the buzz genuinely went out.
_DELIVERED = {"sent"}


class NotificationInboxUnavailable(Exception):
    """The inbox could not be read.

    Raised rather than returning an empty list, for the same reason
    `user_settings_service.get_settings` raises `PreferencesUnreadable`: an empty inbox
    and a broken inbox look identical to a user, and silently rendering "No
    notifications yet" over a database error is the kind of degradation nobody reports
    because it looks like the intended empty state.
    """


class NotificationInboxService:
    def __init__(self) -> None:
        self.supabase = get_supabase()

    # ── read ─────────────────────────────────────────────────────────

    @staticmethod
    def _flatten_route(route: Any) -> Dict[str, Any]:
        """Keep only flat scalars.

        iOS `AnyCodable` decodes String/Int/Double/Bool and silently yields "" for
        anything else, so a nested value arrives as garbage rather than as a decode
        error. Dropping it here makes the contract explicit and keeps a bad row from
        producing a mystery blank field in the UI (.claude/rules/auth.md §3).
        """
        if not isinstance(route, dict):
            return {}
        flat: Dict[str, Any] = {}
        for key, value in route.items():
            if isinstance(value, (str, int, float, bool)) and not isinstance(value, bytes):
                flat[str(key)] = value
            elif value is not None:
                logger.debug(
                    "notification inbox: dropping non-scalar route key %r (%s)",
                    key, type(value).__name__,
                )
        return flat

    def _to_response(self, row: Dict[str, Any]) -> Optional[NotificationEventResponse]:
        """One row → one DTO, or None if the row is unusable.

        Skips rather than raises: one malformed row must not blank the whole inbox.
        """
        try:
            return NotificationEventResponse(
                id=str(row["id"]),
                kind=row.get("kind") or "unknown",
                category=row.get("category") or "unknown",
                title=row.get("title") or "",
                body=row.get("body") or "",
                route=self._flatten_route(row.get("route")),
                created_at=_iso(row.get("claimed_at")) or "",
                read_at=_iso(row.get("read_at")),
                delivery_state=row.get("push_state") or "sent",
            )
        except Exception as e:
            logger.warning(
                "notification inbox: skipping unusable row id=%r (%s: %s)",
                row.get("id"), type(e).__name__, e,
            )
            return None

    def list_for_user(
        self,
        user_id: str,
        *,
        limit: int = DEFAULT_PAGE,
        before: Optional[str] = None,
    ) -> NotificationListResponse:
        """Newest-first page of a user's notifications.

        KEYSET pagination on `claimed_at`, not offset. Rows arrive continuously at the
        head, so an offset-based page 2 would repeat or skip rows whenever a
        notification landed between requests — the same reasoning the Updates feed
        already applies.
        """
        size = max(1, min(int(limit or DEFAULT_PAGE), MAX_PAGE))
        try:
            query = (
                self.supabase.table(TABLE)
                .select("id, kind, category, title, body, route, claimed_at, read_at, push_state")
                .eq("user_id", user_id)
                .order("claimed_at", desc=True)
                # `claimed_at` is not unique (a fan-out writes many rows in the same
                # millisecond), so a tiebreaker is required or a page boundary landing
                # inside a tie drops or repeats rows.
                .order("id", desc=True)
                .limit(size + 1)          # +1 probes for a next page without a count
            )
            if before:
                query = query.lt("claimed_at", before)
            rows = query.execute().data or []
        except Exception as e:
            logger.error(
                "notification inbox: list failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e, exc_info=True,
            )
            raise NotificationInboxUnavailable(str(e)) from e

        has_more = len(rows) > size
        rows = rows[:size]
        items = [dto for dto in (self._to_response(r) for r in rows) if dto is not None]

        return NotificationListResponse(
            items=items,
            unread_count=self.unread_count(user_id),
            # Derived from the raw row, not from `items` — a skipped malformed row would
            # otherwise stall the cursor and make the client re-request forever.
            next_cursor=(_iso(rows[-1].get("claimed_at")) if has_more and rows else None),
        )

    def unread_count(self, user_id: str) -> int:
        """Unread badge count, probe-capped.

        Fails to 0 rather than raising: the badge is decoration on top of the list, and
        a failure that blanks the badge is far better than one that blanks the inbox.
        """
        try:
            rows = (
                self.supabase.table(TABLE)
                .select("id")
                .eq("user_id", user_id)
                .is_("read_at", "null")
                .limit(UNREAD_PROBE_CAP)
                .execute()
                .data
                or []
            )
            return len(rows)
        except Exception as e:
            logger.warning(
                "notification inbox: unread count failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e,
            )
            return 0

    # ── write ────────────────────────────────────────────────────────

    def mark_read(
        self,
        user_id: str,
        *,
        ids: Optional[Sequence[str]] = None,
        mark_all: bool = False,
    ) -> int:
        """Mark rows read. Returns how many changed.

        ⚠️ EVERY query is scoped `.eq("user_id", user_id)` in ADDITION to the id filter.
        Filtering on `id` alone is a textbook IDOR — one user marking another's
        notifications read. The service-role key bypasses RLS, so this in-code filter is
        the effective wall (SYSTEM_DESIGN_GUIDELINES §9: RLS is defence in depth).
        """
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).isoformat()
        try:
            query = (
                self.supabase.table(TABLE)
                .update({"read_at": stamp})
                .eq("user_id", user_id)
                .is_("read_at", "null")   # idempotent: never re-stamp an older read
            )
            if not mark_all:
                wanted = [str(i) for i in (ids or []) if i]
                if not wanted:
                    return 0
                query = query.in_("id", wanted)
            return len(query.execute().data or [])
        except Exception as e:
            logger.error(
                "notification inbox: mark_read failed for user=%s (all=%s, ids=%d) "
                "(%s: %s)",
                user_id, mark_all, len(ids or []), type(e).__name__, e, exc_info=True,
            )
            raise NotificationInboxUnavailable(str(e)) from e


_service: Optional[NotificationInboxService] = None


def get_notification_inbox_service() -> NotificationInboxService:
    global _service
    if _service is None:
        _service = NotificationInboxService()
    return _service
