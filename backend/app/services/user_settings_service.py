"""User-settings + device-token service.

Per-user preference sync (one JSONB blob) + APNs device-token registration.
Both tables are created in migration 102. `get_supabase()` runs as the service
role, so writes here bypass RLS (RLS still guards any client-direct access).
"""

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.database import get_supabase

logger = logging.getLogger(__name__)

# Guard against a signed-in client PUTting an unbounded blob (denial-of-storage).
MAX_PREFERENCES_BYTES = 16 * 1024


class PreferencesTooLarge(ValueError):
    """The submitted preferences blob exceeds MAX_PREFERENCES_BYTES."""


class PreferencesUnreadable(RuntimeError):
    """The stored blob could not be READ. Distinct from "no row yet", which is {}.

    These two must never look alike to the client. iOS gates its full-replace push on
    "the server state is known" — so an unreadable row answered as an empty one opens
    that gate on a session whose real blob was never seen, and the next push replaces
    ~20 synced keys with whatever this device happens to hold locally. On a fresh
    install that is almost nothing, and the loss propagates to every other device.
    A failed hydrate is already handled correctly on the client (it leaves
    `hasHydrated` false); it just has to be told that it failed.
    """


class PreferencesEmptyAfterSanitize(ValueError):
    """Every submitted value was dropped, so honouring the write would clear the row.

    A full-replace endpoint cannot distinguish "the user cleared their settings" from
    "the client sent 20 nulls", and the second is indistinguishable from success today.
    Refusing is the safe reading: an intentional clear can send an explicitly empty
    object, which still passes.
    """


def preferences_too_large(preferences: Dict[str, Any]) -> bool:
    """Pure: True when the JSON-serialized blob exceeds MAX_PREFERENCES_BYTES."""
    return len(json.dumps(preferences)) > MAX_PREFERENCES_BYTES


# ── Key-name policy ──────────────────────────────────────────────────────────
#
# `sanitize_preferences` filtered by VALUE type and total size only. Key names were
# never checked, so any name at all was persistable — including two the app treats as
# device-local first-run state and that `clearLocalForEndedSession()` never removes:
#
#     has_acknowledged_disclaimers   has_completed_onboarding
#
# A blob carrying `"has_acknowledged_disclaimers": true` describes an account that
# SKIPS the first-run legal disclaimer on every device it signs into, permanently. iOS
# blocks the write today (`SettingsSyncManager.apply()` allowlists the keys it owns), but
# that is one client's guard on one code path, and it is the wrong layer for a legal gate.
#
# NOT a bare list of the 18 keys the current app version syncs, which is what a first
# reading suggests: the client's `lastServerBlob` exists precisely so a key introduced by
# a NEWER app version survives a push from an older one, and a hard list here would delete
# every such key until the backend redeployed — coupling two independently deployed
# artifacts in the one direction that silently loses data. So: an explicit set for what
# ships today, a documented forward-compatible PREFIX for the category that actually
# grows (notification toggles), and a hard denial for the reserved names.
_RESERVED_KEYS = frozenset({
    "has_acknowledged_disclaimers",   # legal gate — must be earned on THIS device
    "has_completed_onboarding",       # first-run gate, same reasoning
    "app_lock_enabled",               # device-local security, deliberately unsynced
})

_KNOWN_KEYS = frozenset({
    "default_persona", "appearance_mode", "playback_speed",
    "haptic_feedback", "autoplay_next",
})

# New notification toggles ship regularly and are harmless by construction (they can only
# reduce or restore what the user is sent). Anything else new gets dropped and LOGGED, so
# a genuinely new key shape is visible in the logs rather than silently discarded.
_KNOWN_KEY_PREFIXES = ("notify_",)

# A blob is a fixed set of app preferences, not a KV store. The byte cap alone permits
# thousands of tiny keys.
MAX_PREFERENCE_KEYS = 64
MAX_PREFERENCE_KEY_LENGTH = 64


def key_is_syncable(key: str) -> bool:
    """Pure: may this key name be persisted in a user's preference blob?"""
    if not isinstance(key, str) or not key or len(key) > MAX_PREFERENCE_KEY_LENGTH:
        return False
    if key in _RESERVED_KEYS:
        return False
    return key in _KNOWN_KEYS or key.startswith(_KNOWN_KEY_PREFIXES)


def _is_scalar(value: Any) -> bool:
    """Scalar the iOS `PreferenceValue` can decode — and that JSON can represent.

    `NaN` / `±Infinity` are floats, so a plain isinstance check keeps them, and then
    NOTHING downstream can serialize them: httpx encodes with `allow_nan=False` and
    raises, and Starlette's JSONResponse does the same on the way back out. Either
    way an *invalid input* surfaces as a 500 whose body is `{"detail": ...}` — not the
    `{error_code, message, user_message}` shape iOS decodes (invariant #3). A client
    only has to send `1e999`, which `json.loads` happily parses as `inf`.
    """
    if isinstance(value, bool) or isinstance(value, int) or isinstance(value, str):
        return True
    return isinstance(value, float) and math.isfinite(value)


def sanitize_preferences(preferences: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: keep only scalar values (bool/int/float/str) the iOS client can decode.

    The iOS PreferenceValue decodes bool/int/double/string only; a nested object,
    array, or null would break the whole settings decode. Dropping non-scalars here
    (defense-in-depth with the tolerant iOS decoder) guarantees we never persist a
    blob the client can't read. Non-dict input yields {}.

    Non-finite floats are dropped for a second reason — see `_is_scalar`.

    Key NAMES are filtered too — see `key_is_syncable` and `_RESERVED_KEYS`.
    """
    if not isinstance(preferences, dict):
        return {}
    kept: Dict[str, Any] = {}
    dropped_names: list[str] = []
    for k, v in preferences.items():
        # NB: bool is a subclass of int — both are kept intentionally.
        if not isinstance(k, str) or not _is_scalar(v):
            continue
        if not key_is_syncable(k):
            dropped_names.append(k)
            continue
        kept[k] = v
        if len(kept) >= MAX_PREFERENCE_KEYS:
            logger.warning(
                "user_settings: blob hit the %d-key cap; remaining keys dropped",
                MAX_PREFERENCE_KEYS,
            )
            break
    if dropped_names:
        # Loud, because this is either a client sending something it should not or a new
        # key shape that needs adding above — and both are invisible otherwise.
        logger.warning(
            "user_settings: dropped %d non-syncable preference key(s): %s",
            len(dropped_names), sorted(dropped_names)[:10],
        )
    return kept


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserSettingsService:
    def __init__(self):
        self.supabase = get_supabase()

    def get_settings(self, user_id: str) -> Dict[str, Any]:
        """Return the user's preference blob, or {} when there is no row yet.

        Raises `PreferencesUnreadable` on a READ FAILURE rather than returning {} —
        the two are not interchangeable, see that exception's docstring.
        """
        try:
            result = (
                self.supabase.table("user_settings")
                .select("preferences")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            rows = list(result.data or [])
            prefs = rows[0].get("preferences") if rows else None
            return prefs if isinstance(prefs, dict) else {}
        except Exception as e:
            logger.error(
                "user_settings read failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e,
            )
            raise PreferencesUnreadable(str(e)) from e

    def upsert_settings(self, user_id: str, preferences: Dict[str, Any]) -> Dict[str, Any]:
        """Full-blob replace of the user's preferences. Raises PreferencesTooLarge
        on an oversized blob (→ endpoint maps to INVALID_INPUT); re-raises on a
        genuine DB failure (→ 500) so a lost sync is loud, not silent."""
        # Drop non-scalar values BEFORE the size check so a huge nested blob can't
        # both bypass sanitization and trip the limit on junk we'd discard anyway.
        submitted = preferences
        preferences = sanitize_preferences(preferences)
        # A request that sanitizes away to NOTHING would be honoured as "clear the
        # row" and answered 200 — indistinguishable from success. A client bug that
        # encodes nils, or one nested-junk body, would silently erase every synced
        # key. An intentional clear still works: it sends an explicitly empty object.
        if submitted and not preferences:
            raise PreferencesEmptyAfterSanitize(
                f"all {len(submitted)} submitted preference value(s) were unsupported"
            )
        if preferences_too_large(preferences):
            raise PreferencesTooLarge(
                f"preferences blob exceeds {MAX_PREFERENCES_BYTES} bytes"
            )
        try:
            self.supabase.table("user_settings").upsert(
                {
                    "user_id": user_id,
                    "preferences": preferences,
                    "updated_at": _now_iso(),
                },
                on_conflict="user_id",
            ).execute()
            return preferences
        except Exception as e:
            logger.error(
                "user_settings upsert failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e,
            )
            raise

    def register_device(
        self,
        user_id: str,
        token: str,
        platform: str = "ios",
        environment: Optional[str] = None,
    ) -> bool:
        """Upsert an APNs device token, keyed on the unique `token` column so a
        re-registration re-binds the token to the current user. Best-effort:
        returns False (never raises) so a token-sync blip doesn't break the app."""
        token = (token or "").strip()
        if not token:
            logger.warning("register_device called with empty token for user=%s", user_id)
            return False
        try:
            self.supabase.table("device_tokens").upsert(
                {
                    "user_id": user_id,
                    "token": token,
                    "platform": platform,
                    "environment": environment,
                    "updated_at": _now_iso(),
                },
                on_conflict="token",
            ).execute()
            return True
        except Exception as e:
            logger.error(
                "device_tokens upsert failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e,
            )
            return False

    def unregister_device(self, user_id: str, token: str) -> bool:
        """Detach an APNs token from this user.

        Sign-out used to leave the `token → user_id` row in place. `device_tokens.token` is
        UNIQUE, so the binding only ever moved when a NEW registration re-bound it — and after
        signing out the client has no session, so it never registers. The device therefore kept
        receiving the PREVIOUS account's watchlist alerts: a phone showing the signed-out guest
        UI buzzing with someone else's tickers, which also discloses what they follow.

        Scoped by user_id AND token deliberately: a stale client must never be able to unbind a
        token that has already re-bound to somebody else.
        """
        token = (token or "").strip()
        if not token:
            logger.warning("unregister_device called with an empty token for user=%s", user_id)
            return False
        try:
            self.supabase.table("device_tokens").delete() \
                .eq("token", token).eq("user_id", user_id).execute()
            return True
        except Exception as e:
            logger.error(
                "device_tokens delete failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e,
            )
            return False
