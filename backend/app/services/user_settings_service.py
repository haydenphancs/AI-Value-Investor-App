"""User-settings + device-token service.

Per-user preference sync (one JSONB blob) + APNs device-token registration.
Both tables are created in migration 102. `get_supabase()` runs as the service
role, so writes here bypass RLS (RLS still guards any client-direct access).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.database import get_supabase

logger = logging.getLogger(__name__)

# Guard against a signed-in client PUTting an unbounded blob (denial-of-storage).
MAX_PREFERENCES_BYTES = 16 * 1024


class PreferencesTooLarge(ValueError):
    """The submitted preferences blob exceeds MAX_PREFERENCES_BYTES."""


def preferences_too_large(preferences: Dict[str, Any]) -> bool:
    """Pure: True when the JSON-serialized blob exceeds MAX_PREFERENCES_BYTES."""
    return len(json.dumps(preferences)) > MAX_PREFERENCES_BYTES


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserSettingsService:
    def __init__(self):
        self.supabase = get_supabase()

    def get_settings(self, user_id: str) -> Dict[str, Any]:
        """Return the user's preference blob, or {} when no row / on error."""
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
            return {}

    def upsert_settings(self, user_id: str, preferences: Dict[str, Any]) -> Dict[str, Any]:
        """Full-blob replace of the user's preferences. Raises PreferencesTooLarge
        on an oversized blob (→ endpoint maps to INVALID_INPUT); re-raises on a
        genuine DB failure (→ 500) so a lost sync is loud, not silent."""
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
