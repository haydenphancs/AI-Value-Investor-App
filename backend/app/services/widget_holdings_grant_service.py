"""The Home Screen widget's Holdings grant (migration 216): mint, check, revoke, push columns.

WHY THIS EXISTS (owner 2026-11-27: "Own key, 30-day life (Recommended)", "Yes, add push too",
"Yes, issue it in refresh (Recommended)")
------------------------------------------------------------------------------------------------
The Holdings tile changed only while the app ran: the widget extension is a separate process with
no session, and `/widget/portfolio-mover` is session-only. Each device that shows a Movers widget
now holds its own opaque key — `cwh1_` + 43 base64url characters (256 bits) — that the extension
sends to `GET /widget/holdings-mover`.

THE RULES THIS MODULE KEEPS
---------------------------
* **Minted only inside a refresh rotation** (`POST /auth/refresh`, status `rotated`, the opt-in
  body flag). A stolen access token (≤ 60 min) therefore can never become a 30-day credential:
  minting needs the session's single-use refresh token, and a thief who rotates it makes the
  victim's next refresh REUSE — which revokes the session, and migration 216's trigger kills the
  grant in the same transaction.
* **The server stores only `sha256(key)`** (lower-case hex). The key leaves the server exactly
  once, in the refresh answer. Any local holding the raw key is named `*_token`, so the Sentry
  frame scrub (`log_redaction._SECRET_NAME_SEGMENTS`) blanks it; this module is also in
  `_CREDENTIAL_FRAME_MODULES`. Logs carry grant_id, user_id and the first 8 hex of the hash.
* **Liveness is ONE SQL predicate** (`widget_holdings_grant_is_live`, inside the RPCs). Never the
  fail-open Python helper `dependencies._reject_if_password_changed_since_issue`.
* **Fail closed.** Every RPC failure raises `GrantStoreUnavailable`; a missing migration raises
  `GrantSchemaMissing` (classified by `auth_session_service.is_schema_missing`: structured
  PGRST202/PGRST205 only). Unlike 214's sessions there is NO stateless fallback: a missing schema
  never means "allowed". The callers decide what a failure costs (the refresh mint skips the
  grant; the holdings route answers 503; the push-token route answers `stored=false`).

Sync functions (the Supabase SDK is sync): call them through `asyncio.to_thread`.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.services import auth_session_service as auth_sessions

logger = logging.getLogger(__name__)

#: Life of a grant. The owner fixed it ("Own key, 30-day life"); the migration CHECKs ≤ 31 days.
GRANT_DAYS = 30
#: Live, non-superseded grants per account (one per device that shows a Movers widget).
MAX_LIVE = 5

KEY_PREFIX = "cwh1_"
#: `secrets.token_urlsafe(32)` is exactly 43 base64url characters (256 bits).
KEY_BODY_LENGTH = 43
KEY_LENGTH = len(KEY_PREFIX) + KEY_BODY_LENGTH
#: Anything longer than this is refused before the regex runs.
KEY_MAX_LENGTH = 64
_KEY_RE = re.compile(r"^cwh1_[A-Za-z0-9_-]{43}$")

#: Revoke reasons a device may send (`POST /widget/holdings-grant/revoke`).
DEVICE_REVOKE_REASONS = frozenset({"app_revoked", "widget_removed"})

#: `check_widget_holdings_grant`'s statuses (migration 216 header).
CHECK_STATUSES = frozenset({"live", "unknown", "revoked", "expired", "account_missing", "evicted"})
#: `mint_widget_holdings_grant`'s statuses.
MINT_STATUSES = frozenset({
    "minted", "account_missing", "evicted", "session_unknown", "session_revoked",
})

PUSH_ENVIRONMENTS = frozenset({"sandbox", "production"})
# `widget_push_targets` / `widget_push_settle` have no reader here on purpose: the push sweeper
# (`widget_push_service`) is their ONE caller, paging and batching them itself. A second copy here
# drifted (it dropped malformed rows before the caller could compare a page with its size, so a
# full page with one bad row would have ended the paging early) and was removed (review 2026-11-27).
_PUSH_TOKEN_RE = re.compile(r"^[0-9a-f]+$")

#: The ERROR for a missing migration fires at most this often per process; WARNING otherwise
#: (as `auth_session_service._SCHEMA_MISSING_LOG_EVERY_SECONDS`).
_SCHEMA_MISSING_LOG_EVERY_SECONDS = 600.0
_last_schema_missing_error = 0.0


class GrantStoreUnavailable(Exception):
    """A grant read or write failed. Never "allowed", never "refused": the caller degrades."""


class GrantSchemaMissing(GrantStoreUnavailable):
    """Migration 216 is not applied (or a function signature drifted)."""


@dataclass(frozen=True)
class MintedGrant:
    """A freshly minted grant. `grant_token` is the raw key — shown once, never logged."""
    grant_token: str = field(repr=False)
    grant_id: str
    expires_at: datetime
    session_id: str


@dataclass(frozen=True)
class MintOutcome:
    status: str
    grant: Optional[MintedGrant] = None


@dataclass(frozen=True)
class GrantCheck:
    """`check`'s answer. `user_id` / `grant_id` are set only when `status == "live"`."""
    status: str
    grant_id: Optional[str] = None
    user_id: Optional[str] = None
    superseded: bool = False


# ── key helpers ─────────────────────────────────────────────────────────────────────────────

def new_token() -> str:
    """A fresh grant key: `cwh1_` + 43 base64url characters (256 bits of entropy)."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def is_well_formed(raw_token: Any) -> bool:
    """Exactly the shape `new_token` produces. Length-checked BEFORE the regex."""
    if not isinstance(raw_token, str) or len(raw_token) > KEY_MAX_LENGTH:
        return False
    return _KEY_RE.fullmatch(raw_token) is not None


def key_hash(raw_token: str) -> str:
    """Lower-case hex SHA-256 of the full key — what the database stores and looks up."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def hash_label(hashed: str) -> str:
    """The first 8 hex of a key hash, for log lines. Never the key, never the full hash."""
    return (hashed or "")[:8]


def is_valid_push_token(push_token: Any) -> bool:
    """Lower-case hex, even length, 32..400 characters (Apple: do not assume a size)."""
    return (
        isinstance(push_token, str)
        and 32 <= len(push_token) <= 400
        and len(push_token) % 2 == 0
        and _PUSH_TOKEN_RE.fullmatch(push_token) is not None
    )


# ── plumbing ────────────────────────────────────────────────────────────────────────────────

def _log_schema_missing(operation: str, exc: BaseException, **ids: Any) -> None:
    global _last_schema_missing_error
    now = time.monotonic()
    context = " ".join(f"{k}={v}" for k, v in ids.items())
    if now - _last_schema_missing_error >= _SCHEMA_MISSING_LOG_EVERY_SECONDS:
        _last_schema_missing_error = now
        logger.error(
            "widget grants: migration 216 is NOT applied (%s: %s: %s) %s — no Holdings grant can "
            "be minted or checked until it is; apply "
            "backend/database/migrations/216_widget_holdings_grants.sql",
            operation, type(exc).__name__, exc, context,
        )
    else:
        logger.warning("widget grants: migration 216 still not applied (%s) %s", operation, context)


def _call(supabase: Any, name: str, params: dict, **ids: Any) -> Any:
    """Run one RPC; map every failure to the typed exceptions. Returns the raw `data`."""
    try:
        return getattr(supabase.rpc(name, params).execute(), "data", None)
    except Exception as e:  # noqa: BLE001 — classified here, re-raised typed
        if auth_sessions.is_schema_missing(e):
            _log_schema_missing(name, e, **ids)
            raise GrantSchemaMissing(f"{name}: {type(e).__name__}: {e}") from e
        raise GrantStoreUnavailable(f"{name}: {type(e).__name__}: {e}") from e


def _object(data: Any, name: str) -> dict:
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise GrantStoreUnavailable(f"{name} answered an unexpected shape: {type(data).__name__}")
    return data


def _parse_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def format_expires_at(dt: datetime) -> str:
    """`YYYY-MM-DDTHH:MM:SSZ` — no fractional seconds (iOS parses with a formatter)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── operations ──────────────────────────────────────────────────────────────────────────────

def mint(supabase: Any, user_id: str, sid: str, minted_iat: int) -> MintOutcome:
    """Mint a grant under session `sid` for `user_id`. Raises on a store failure.

    `minted_iat` is the `iat` of the access token minted in the SAME refresh; eviction compares
    it, never the row's created_at. Refuses (ValueError, no I/O) an argument that is not the
    shape the route guarantees: that is a bug, never a client.
    """
    user = auth_sessions.parse_uuid(user_id)
    session = auth_sessions.parse_uuid(sid)
    if user is None or session is None:
        raise ValueError("mint: user_id and sid must be UUIDs")
    if isinstance(minted_iat, bool) or not isinstance(minted_iat, int) or minted_iat <= 0:
        raise ValueError("mint: minted_iat must be a positive int")

    grant_token = new_token()
    hashed = key_hash(grant_token)
    data = _object(
        _call(
            supabase,
            "mint_widget_holdings_grant",
            {
                "p_user_id": user,
                "p_sid": session,
                "p_minted_iat": minted_iat,
                "p_key_hash": hashed,
                "p_ttl_days": GRANT_DAYS,
                "p_max_live": MAX_LIVE,
            },
            user=user, sid=session, key=hash_label(hashed),
        ),
        "mint_widget_holdings_grant",
    )
    status = data.get("status")
    if status == "minted":
        grant_id = auth_sessions.parse_uuid(data.get("grant_id"))
        expires_at = _parse_utc(data.get("expires_at"))
        if grant_id is None or expires_at is None:
            # A row may exist with no holder now: it expires, or the next mint supersedes it.
            raise GrantStoreUnavailable(
                f"mint_widget_holdings_grant answered minted without a usable grant_id/expires_at "
                f"(user={user} sid={session} key={hash_label(hashed)})"
            )
        logger.info(
            "widget grants: minted grant=%s user=%s sid=%s key=%s expires=%s",
            grant_id, user, session, hash_label(hashed), format_expires_at(expires_at),
        )
        return MintOutcome(
            status="minted",
            grant=MintedGrant(
                grant_token=grant_token, grant_id=grant_id, expires_at=expires_at,
                session_id=session,
            ),
        )
    if status in MINT_STATUSES:
        return MintOutcome(status=status)
    raise GrantStoreUnavailable(
        f"mint_widget_holdings_grant answered an unknown status {status!r} (user={user})"[:300]
    )


def check(supabase: Any, hashed: str) -> GrantCheck:
    """Look a key hash up. Raises on a store failure or an unrecognised answer (never "live")."""
    data = _object(
        _call(supabase, "check_widget_holdings_grant", {"p_key_hash": hashed},
              key=hash_label(hashed)),
        "check_widget_holdings_grant",
    )
    status = data.get("status")
    if status == "live":
        grant_id = auth_sessions.parse_uuid(data.get("grant_id"))
        user_id = auth_sessions.parse_uuid(data.get("user_id"))
        if grant_id is None or user_id is None:
            raise GrantStoreUnavailable(
                f"check_widget_holdings_grant answered live without usable ids "
                f"(key={hash_label(hashed)})"
            )
        return GrantCheck(
            status="live", grant_id=grant_id, user_id=user_id,
            superseded=data.get("superseded") is True,
        )
    if status in CHECK_STATUSES:
        return GrantCheck(status=status)
    raise GrantStoreUnavailable(
        f"check_widget_holdings_grant answered an unknown status {status!r} "
        f"(key={hash_label(hashed)})"[:300]
    )


def revoke(supabase: Any, grant_id: str, user_id: str, reason: str) -> str:
    """Revoke one grant of `user_id` on the device's word. Returns the RPC status."""
    if reason not in DEVICE_REVOKE_REASONS:
        raise ValueError(f"revoke: unknown reason {reason!r}")
    data = _object(
        _call(
            supabase, "revoke_widget_holdings_grant",
            {"p_grant_id": grant_id, "p_user_id": user_id, "p_reason": reason},
            grant=grant_id, user=user_id,
        ),
        "revoke_widget_holdings_grant",
    )
    status = data.get("status")
    return status if isinstance(status, str) else "unknown"


def note_head(supabase: Any, grant_id: str, ticker: Optional[str]) -> str:
    """Record the ticker the device's tile leads with (push targeting). Returns the status."""
    data = _object(
        _call(
            supabase, "note_widget_holdings_head",
            {"p_grant_id": grant_id, "p_ticker": ticker},
            grant=grant_id,
        ),
        "note_widget_holdings_head",
    )
    status = data.get("status")
    return status if isinstance(status, str) else "unknown"


def register_push_token(
    supabase: Any, user_id: str, grant_id: str, push_token: Optional[str], environment: str,
) -> tuple[bool, bool]:
    """Store (or clear, `push_token=None`) the device's WidgetKit push token on its grant.

    Returns `(stored, grant_usable)`. `(False, False)` for an unknown, foreign, dead or superseded
    grant — one indistinguishable answer. Raises on a store failure.
    """
    if push_token is not None and not is_valid_push_token(push_token):
        raise ValueError("register_push_token: malformed push token")
    if environment not in PUSH_ENVIRONMENTS:
        raise ValueError("register_push_token: unknown environment")
    data = _object(
        _call(
            supabase, "widget_push_register",
            {
                "p_user_id": user_id,
                "p_grant_id": grant_id,
                "p_token": push_token,
                "p_environment": environment,
            },
            user=user_id, grant=grant_id,
        ),
        "widget_push_register",
    )
    stored, usable = data.get("stored"), data.get("grant_usable")
    if not isinstance(stored, bool) or not isinstance(usable, bool):
        raise GrantStoreUnavailable(
            f"widget_push_register answered {sorted(data)!r} (user={user_id} grant={grant_id})"
        )
    return stored, usable
