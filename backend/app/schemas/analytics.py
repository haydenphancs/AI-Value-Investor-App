"""Analytics ingest schemas.

The ingest endpoint is the one place the app accepts arbitrary client-supplied
structure, so the caps here ARE the security boundary. An events table with no
bounds is both a storage bomb and a place for user-typed text (chat messages,
search queries) to leak into a store the privacy policy never described.

Three defences, all enforced before anything reaches Postgres:
  1. `event` must be on `ALLOWED_EVENTS` — unknown names are DROPPED, not stored.
     Analytics is a fixed vocabulary; anything else is a client bug or an abuse.
  2. Batch size, prop count, key length, and value length are all capped.
  3. Prop VALUES are scalars only. No nested objects/arrays — that's how an entire
     request body ends up in a "dimension" by accident.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# One HTTP round-trip per app background, not per event. A caller sending more than
# this in one batch is a bug or an abuse, not a session's worth of activity.
MAX_EVENTS_PER_BATCH = 50

# Per-event prop bounds. Dimensions are things like ticker/persona/screen — a handful
# of short scalars. Anything larger is text that does not belong here.
MAX_PROPS_PER_EVENT = 12
MAX_PROP_KEY_CHARS = 40
MAX_PROP_VALUE_CHARS = 120
MAX_EVENT_NAME_CHARS = 60
MAX_SESSION_ID_CHARS = 64

# The fixed analytics vocabulary. Adding an event means adding it HERE and on the iOS
# side — deliberately not open-ended, so a typo silently dropping data is caught by
# the parity test rather than becoming a permanently empty column in a dashboard.
#
# Chosen to answer the launch questions that currently have no answer at all:
# activation (install → first report), conversion (paywall → purchase), engagement
# (which tabs/personas), and retention (app_open over time).
ALLOWED_EVENTS: frozenset[str] = frozenset({
    "app_open",
    "screen_view",
    "report_requested",
    "report_completed",
    "report_failed",
    "chat_sent",
    "paywall_shown",
    "paywall_purchase_started",
    "purchase_completed",
    "watchlist_added",
    "lesson_completed",
    "audio_played",
    # First-run funnel. `onboarding_completed` carries the ticker count, which is
    # THE activation number for this app: a populated watchlist is what makes
    # Updates, the personalized Home strip, and (later) push relevant at all.
    "onboarding_completed",
    "onboarding_skipped",
    # Failure telemetry. Both replace `print`/`#if DEBUG` sites that were invisible in release
    # builds, which is how a whole class of silently-reverted user actions (whale follow, the
    # star toggles, portfolio edits) survived unnoticed. `mutation_failed` is user-initiated,
    # `background_sync_failed` is best-effort sync (settings, push registration, guest claim).
    "mutation_failed",
    "background_sync_failed",
})


# Prop KEYS are allowlisted too, not just values. `/events` is public and
# unauthenticated, so without this the DB-level guarantee would be "≤12 arbitrary
# keys × ≤120 chars of caller-supplied text per event" — which is not what the
# privacy policy or the App Privacy filing describes. Every key here is a
# low-cardinality dimension chosen at an iOS call site, never user input.
ALLOWED_PROP_KEYS: frozenset[str] = frozenset({
    "tab",        # screen_view — fixed 5-case HomeTab enum
    "ticker",     # report_*, watchlist_added
    "persona",    # report_* — fixed persona key
    "tier",       # paywall_*, purchase_completed
    "reason",     # report_failed — AppError.analyticsCode, never the message
    "kind",       # lesson_completed, audio_played — fixed category
    "context",    # chat_sent — ChatContextType enum, never the message body
    "seconds",    # durations
    "count",
    "cached",
    "action",     # mutation_failed — a fixed infinitive phrase from the call site
    "op",         # background_sync_failed — fixed operation name
    "code",       # both — AppError.analyticsCode, never the message
})


class AnalyticsEvent(BaseModel):
    """One client-emitted event."""

    event: str
    props: Dict[str, Any] = Field(default_factory=dict)
    # Device clock. Recorded for intra-session ordering, never trusted for bucketing —
    # the server stamps `server_ts` and every aggregate uses that.
    client_ts: Optional[str] = None

    @field_validator("client_ts")
    @classmethod
    def _parseable_timestamp(cls, v: Optional[str]) -> Optional[str]:
        """Null anything Postgres would reject.

        This column is TIMESTAMPTZ, and a single unparseable value fails the ENTIRE
        multi-row INSERT (SQLSTATE 22007) — so one client with a broken clock format
        could silently destroy 49 other good events per batch, repeatedly.
        """
        if not v:
            return None
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        return v

    @field_validator("event")
    @classmethod
    def _trimmed(cls, v: str) -> str:
        # TRUNCATE, never `max_length`. A length constraint RAISES, which the global
        # RequestValidationError handler turns into a 422 — discarding all 50 events
        # in the batch. That is the exact failure `is_known` exists to avoid, so an
        # over-long name must degrade the same way an unknown one does: drop the one
        # event (it won't match the allowlist), keep the rest.
        return (v or "").strip()[:MAX_EVENT_NAME_CHARS]

    @property
    def is_known(self) -> bool:
        """Whether this event survives the allowlist.

        Deliberately NOT a validator that raises: a rejected field fails the whole
        request, so one unrecognised name would discard every event in the batch
        alongside it. That is a live scenario — an app update ships a new event
        before the backend allowlist learns it — and losing a whole flush of good
        data to one unknown name is far worse than dropping the one.
        """
        return self.event in ALLOWED_EVENTS

    @field_validator("props")
    @classmethod
    def _bounded_scalar_props(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Keep only short scalar dimensions. Silently drops the rest rather than
        rejecting the whole event — losing one malformed dimension is much better
        than losing the event (and analytics must never break the app)."""
        if not v:
            return {}
        cleaned: Dict[str, Any] = {}
        # Filter FIRST, cap after. Slicing first meant a payload leading with 12 junk
        # keys silently discarded the real dimension behind them.
        for key, value in v.items():
            if len(cleaned) >= MAX_PROPS_PER_EVENT:
                break
            if not isinstance(key, str) or not key or len(key) > MAX_PROP_KEY_CHARS:
                continue
            # Key ALLOWLIST. The privacy justification for this whole table is that
            # props carry dimensions, never free text — with arbitrary keys accepted
            # that was a convention the server did not enforce, on a public
            # unauthenticated endpoint. Now it does.
            if key not in ALLOWED_PROP_KEYS:
                continue
            if isinstance(value, bool) or isinstance(value, (int, float)):
                cleaned[key] = value
            elif isinstance(value, str):
                cleaned[key] = value[:MAX_PROP_VALUE_CHARS]
            # dict / list / None → dropped: a nested payload is how a whole request
            # body (or a user's typed message) ends up in a "dimension".
        return cleaned


class AnalyticsBatchRequest(BaseModel):
    """Body of ``POST /api/v1/events``."""

    events: List[AnalyticsEvent] = Field(default_factory=list)
    session_id: Optional[str] = None

    @field_validator("session_id")
    @classmethod
    def _bounded_session_id(cls, v: Optional[str]) -> Optional[str]:
        # Truncate, don't raise — same reason as `event` above.
        return v[:MAX_SESSION_ID_CHARS] if v else None

    @field_validator("events")
    @classmethod
    def _bounded_batch(cls, v: List[AnalyticsEvent]) -> List[AnalyticsEvent]:
        return v[:MAX_EVENTS_PER_BATCH]


class AnalyticsBatchResponse(BaseModel):
    """`accepted` can be lower than what was sent — unknown event names and
    over-cap events are dropped, not errors. The client is fire-and-forget and
    ignores this; it exists so the drop rate is observable from curl/tests."""

    accepted: int
    dropped: int = 0
