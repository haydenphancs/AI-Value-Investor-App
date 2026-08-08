"""Response shapes for the in-app notification inbox.

⚠️ EVERY field here is decoded by iOS. A rename or a nullability change that iOS does
not mirror is a decode CRASH in production, not a missing row — see
`tests/test_notification_schema_parity.py`, which is the guard rail for exactly that.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class NotificationEventResponse(BaseModel):
    """One row of the inbox."""

    id: str
    kind: str = Field(description="NotificationKind key, e.g. 'earnings_upcoming'")
    category: str = Field(description="Cap bucket: watchlist | earnings | smart_money | price_alert | app")
    title: str
    body: str

    # The tap target, exactly as it shipped in the APNs payload. FLAT SCALARS ONLY:
    # the iOS `AnyCodable` decoder handles String/Int/Double/Bool and silently yields ""
    # for anything nested, so a nested value here arrives as garbage rather than as an
    # error (.claude/rules/auth.md §3). Values are sanitized on the way out.
    route: Dict[str, Any] = {}

    created_at: str = Field(description="ISO-8601. When the notification was DECIDED.")
    read_at: Optional[str] = None

    # Why the buzz did or didn't happen. Surfaced so a support question ("I never got
    # it") is answerable from the app itself rather than from server logs:
    #   sent | deferred | no_device | dry_run | failed | pending
    delivery_state: str = "sent"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


class NotificationListResponse(BaseModel):
    items: List[NotificationEventResponse] = []
    unread_count: int = 0
    # Cursor for the next page: the `created_at` of the last item. Null = no more.
    # Keyset rather than offset because rows arrive continuously at the head, and an
    # offset page 2 would silently repeat or skip rows as new notifications land.
    next_cursor: Optional[str] = None


class MarkReadRequest(BaseModel):
    """Ids to mark read. Empty list + `all=True` marks everything."""

    ids: List[str] = []
    all: bool = False


class MarkReadResponse(BaseModel):
    updated: int = 0
    unread_count: int = 0


def _iso(value: Any) -> Optional[str]:
    """Normalise a Supabase timestamp to a plain ISO-8601 string.

    Dates cross the wire as STRINGS, never as `datetime` — FMP returns strings and the
    iOS Codable layer decodes strings (backend-python.md). Returning a `datetime` here
    would serialize with a shape iOS has never parsed.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
