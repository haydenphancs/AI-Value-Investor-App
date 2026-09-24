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
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

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


class InvalidCursor(ValueError):
    """`before` is not a cursor this service minted.

    Distinct from `NotificationInboxUnavailable` on purpose: that one is 503 "try again",
    this one is 400 "that request cannot succeed". Raised BEFORE any database call, and
    never degraded to "serve page 1" — a first page answered as page 2 would re-append
    the rows already on screen and, on the next scroll, do it again.
    """


# ── the keyset cursor: `<claimed_at>|<id>`, URL-safe by construction ─────────────
#
# The cursor crosses the wire as a QUERY-STRING VALUE and comes back the same way, and
# that round trip broke page 2 for every TestFlight build (2026-09-11): the server minted
# `2026-08-28T14:17:21.462+00:00|<uuid>`, iOS's `URLComponents` leaves a literal `+`
# unencoded in a query value, and Starlette's form decoder turns `+` into a SPACE — so the
# endpoint received `…21.462 00:00|<uuid>`, the raw interpolation below handed Postgres
# `timestamptz '2026-08-28T14:17:21.462 00:00'`, and 22007 became a 503 toast: "Couldn't
# load more notifications". Everything older than the first page was unreachable, which
# the tester read as a two-week retention window.
#
# Two fixes, both needed. `mint_cursor` writes the stamp with a `Z` suffix — no `+`
# anywhere, so an UNPATCHED client round-trips it intact. `parse_cursor` repairs the
# space-for-plus mangling on the way in, so the cursors those clients already hold (and
# the next page they request against an old server-minted cursor) work the moment this
# deploys. The stamp is then re-emitted in the `+00:00` form for PostgREST, which
# postgrest-py percent-encodes correctly (the `whale_service` keyset does the same).

_CURSOR_SEP = "|"
# A form decoder that ate the offset's "+" leaves " HH:MM" at the very end of the stamp.
_MANGLED_OFFSET = re.compile(r" (\d{2}:\d{2})$")


def mint_cursor(claimed_at: Any, row_id: Any) -> Optional[str]:
    """`<UTC stamp with a Z>|<id>` for the last row of a page, or None if unusable."""
    if claimed_at is None or row_id is None:
        return None
    try:
        dt = _parse_stamp(str(claimed_at))
    except ValueError:
        logger.warning(
            "notification inbox: cannot mint a cursor from claimed_at=%r — no next page",
            claimed_at,
        )
        return None
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S.%f')}Z{_CURSOR_SEP}{row_id}"


def parse_cursor(before: Any) -> Tuple[str, Optional[str]]:
    """`before` → `(stamp for PostgREST, last_id or None)`. Raises `InvalidCursor`.

    Accepts the three shapes in the wild: the `Z` form this service mints, the legacy
    `+00:00` form older servers minted, and that legacy form with its `+` decoded to a
    space by the client's un-encoded query. A stamp-only cursor (pre-composite builds)
    is honoured with `last_id=None`. Anything else is refused before the database sees it.
    """
    raw = str(before or "").strip()
    stamp, _, last_id = raw.partition(_CURSOR_SEP)
    stamp = _MANGLED_OFFSET.sub(r"+\1", stamp.strip())
    try:
        dt = _parse_stamp(stamp)
    except ValueError as e:
        raise InvalidCursor(f"unreadable cursor stamp {stamp!r}") from e
    last_id = last_id.strip()
    if last_id:
        try:
            uuid.UUID(last_id)
        except ValueError as e:
            raise InvalidCursor(f"unreadable cursor id {last_id!r}") from e
    return dt.isoformat(), (last_id or None)


def _parse_stamp(stamp: str) -> datetime:
    """An aware UTC datetime from an ISO stamp; naive is read as UTC. Raises ValueError."""
    dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
        # Parsed OUTSIDE the try: a bad cursor is the caller's 400, not the database's 503.
        cursor = parse_cursor(before) if before else None
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
            if cursor:
                # COMPOSITE keyset, matching the composite ORDER BY above.
                #
                # ⚠️ This used to be `.lt("claimed_at", before)` alone. The sort is
                # `(claimed_at DESC, id DESC)` precisely because `claimed_at` is NOT unique —
                # a fan-out writes many rows in the same millisecond — but the cursor only
                # carried half of it. Every row sharing the boundary timestamp with the last
                # row of a page was therefore skipped on the next page: permanently
                # unreachable through the list, while still counted as unread. A page
                # boundary landing inside a fan-out is the common case, not a corner one.
                stamp, last_id = cursor
                if last_id:
                    query = query.or_(
                        f"claimed_at.lt.{stamp},"
                        f"and(claimed_at.eq.{stamp},id.lt.{last_id})"
                    )
                else:
                    # A cursor minted by an older build, mid-session. Degrade to the old
                    # behaviour rather than 500 on it.
                    query = query.lt("claimed_at", stamp)
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
            # `claimed_at|id` — both halves of the sort key. Opaque to the client, which
            # only ever echoes it back as `before`, so the format is ours to choose — and
            # it is chosen to survive an un-encoded query string (see `mint_cursor`).
            next_cursor=(
                mint_cursor(rows[-1].get("claimed_at"), rows[-1].get("id"))
                if has_more and rows and rows[-1].get("id")
                else None
            ),
        )

    def get_by_dedup_key(
        self, user_id: str, dedup_key: str
    ) -> Optional[NotificationEventResponse]:
        """One of this user's notifications, addressed by its `dedup_key`, or None.

        WHY. A push tap now opens the notification's detail screen before anything else,
        and the payload cannot carry what that screen exists to show: APNs gets the body
        cut to `BANNER_BODY_LIMIT` (180 chars + "…") while the row keeps up to
        `LEDGER_BODY_LIMIT`, and for a `ticker_move` the cut-off text IS the catalyst. The
        payload carries no `notification_events.id` either — only `dedup_key`, the other
        half of the `(user_id, dedup_key)` unique index — so that is the lookup key.

        ⚠️ Scoped by `user_id` AND `dedup_key`. Keys are not secret and not unique across
        users (`move:TER:2026-09-14` exists once per watcher), so the `user_id` filter is
        the only thing that stops one account reading another's row; the service-role
        client bypasses RLS (same wall as `mark_read`).

        None means "no such row for this user" — a push can outlive its row (90-day
        retention). A read failure raises `NotificationInboxUnavailable`, never None, so
        the endpoint answers 503 rather than a confident "not found".
        """
        try:
            rows = (
                self.supabase.table(TABLE)
                .select("id, kind, category, title, body, route, claimed_at, read_at, push_state")
                .eq("user_id", user_id)
                .eq("dedup_key", dedup_key)
                .limit(1)
                .execute()
                .data
                or []
            )
        except Exception as e:
            logger.error(
                "notification inbox: lookup failed for user=%s dedup_key=%r (%s: %s)",
                user_id, dedup_key, type(e).__name__, e, exc_info=True,
            )
            raise NotificationInboxUnavailable(str(e)) from e
        if not rows:
            logger.info(
                "notification inbox: no row for user=%s dedup_key=%r", user_id, dedup_key,
            )
            return None
        return self._to_response(rows[0])

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
                # DELIVERED only — this number goes on the app icon.
                #
                # The row is written BEFORE delivery is attempted, so this table also holds
                # `deferred` / `no_device` / `dry_run` / `failed` / `pending` rows the user
                # was never shown. Counting them badges the icon for something that exists
                # nowhere on the phone — "there is no notification but it still shows 1".
                # The LIST deliberately still returns every row: the inbox is a record of
                # what the system decided, the badge is a promise that something is there.
                .in_("push_state", sorted(_DELIVERED))
                .limit(UNREAD_PROBE_CAP)
                .execute()
                .data
                or []
            )
            # A row with no id cannot be rendered (`_to_response` skips it) and cannot be
            # marked read (both selectors key on id or dedup_key), so counting it creates a
            # badge the user has no way to clear.
            return sum(1 for r in rows if r.get("id"))
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
        dedup_keys: Optional[Sequence[str]] = None,
        mark_all: bool = False,
    ) -> int:
        """Mark rows read. Returns how many changed.

        ⚠️ EVERY query is scoped `.eq("user_id", user_id)` in ADDITION to the id filter.
        Filtering on `id` alone is a textbook IDOR — one user marking another's
        notifications read. The service-role key bypasses RLS, so this in-code filter is
        the effective wall (SYSTEM_DESIGN_GUIDELINES §9: RLS is defence in depth).

        `dedup_keys` addresses the same rows by the OTHER half of their unique key. It
        exists for the notification's own "Mark as Read" button: a push payload carries
        no `notification_events.id`, and adding one would mean returning the inserted id
        out of `claim_send`, whose boolean contract several call sites and tests depend
        on. `(user_id, dedup_key)` is UNIQUE (migration 119), so this is an indexed
        lookup addressing exactly one row — and it is scoped by `user_id` for precisely
        the same reason `ids` is. A dedup key is not a secret and not a capability: it is
        derived from a ticker and a date, so `user_id` is what does the work either way.

        `ids` wins if both are supplied — one filter per query, and the caller that has
        real ids is the in-app list, which is the more specific request.
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
                keys = [str(k) for k in (dedup_keys or []) if k]
                if wanted:
                    query = query.in_("id", wanted)
                elif keys:
                    query = query.in_("dedup_key", keys)
                else:
                    # Neither selector and not `all` — nothing was asked for. Returning 0
                    # rather than falling through matters: without this the query would
                    # be scoped to the user alone and mark their ENTIRE inbox read.
                    return 0
            return len(query.execute().data or [])
        except Exception as e:
            logger.error(
                "notification inbox: mark_read failed for user=%s (all=%s, ids=%d, "
                "dedup_keys=%d) (%s: %s)",
                user_id, mark_all, len(ids or []), len(dedup_keys or []),
                type(e).__name__, e, exc_info=True,
            )
            raise NotificationInboxUnavailable(str(e)) from e


_service: Optional[NotificationInboxService] = None


def get_notification_inbox_service() -> NotificationInboxService:
    global _service
    if _service is None:
        _service = NotificationInboxService()
    return _service
