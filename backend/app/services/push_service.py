"""
APNs push service — token-based auth (.p8), HTTP/2 via httpx.

Sends alert pushes to a user's registered `device_tokens` (migration 102).
NOT wired to any trigger yet — future events (research-complete, price alerts)
will call `PushService().send_to_user(...)`. Design goals:

  * Gracefully DISABLED when APNs config is absent (`enabled` is False) — the app
    runs fine without push; registration still records tokens for later.
  * httpx only (no requests/urllib), HTTP/2 required by APNs — needs the `h2`
    package (see requirements.txt).
  * ES256 provider JWT (iss=team, kid=key id) signed with the .p8, cached ~50 min
    (APNs allows a token age up to 60 min).
  * Prune tokens APNs rejects as 410 Unregistered / BadDeviceToken.

Apple prerequisites (out-of-band): APNs Auth Key (.p8) + Key ID, Team ID, the
Push Notifications capability on the App ID, and the `aps-environment` entitlement
in the app. Provide APNS_* via env/secrets — never commit the .p8.
"""

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.database import get_supabase

logger = logging.getLogger(__name__)

# APNs allows a provider token up to 60 min old; refresh a little early.
_TOKEN_TTL_SECONDS = 50 * 60

_HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}


def host_for_environment(environment: Optional[str], default: str) -> str:
    """Pure: pick the APNs host for a token's stored environment.

    A device token is bound to the environment it was minted in (sandbox in DEBUG,
    production in Release). Routing a sandbox token to the prod host (or vice-versa)
    yields BadDeviceToken, so each token must go to ITS OWN host — never a single
    global one. Falls back to `default` (the server's APNS_ENV) then sandbox.
    """
    env = (environment or default or "sandbox").lower()
    return _HOSTS.get(env, _HOSTS["sandbox"])


class PushService:
    # Class-level JWT cache shared across instances (the signing inputs are static).
    _jwt: Optional[str] = None
    _jwt_minted_at: float = 0.0

    def __init__(self):
        self.supabase = get_supabase()

    @property
    def enabled(self) -> bool:
        """True only when all signing inputs are configured."""
        return bool(
            settings.APNS_KEY_ID
            and settings.APNS_TEAM_ID
            and settings.APNS_AUTH_KEY
            and settings.APNS_BUNDLE_ID
        )

    def _provider_jwt(self) -> Optional[str]:
        """Mint (and cache) the ES256 provider token used as the APNs bearer.

        Uses python-jose (already a dependency) to sign with the .p8 EC key.
        Returns None if signing fails so callers degrade instead of raising.
        """
        now = time.time()
        if PushService._jwt and (now - PushService._jwt_minted_at) < _TOKEN_TTL_SECONDS:
            return PushService._jwt
        try:
            from jose import jwt  # local import: keeps module import cheap

            token = jwt.encode(
                {"iss": settings.APNS_TEAM_ID, "iat": int(now)},
                settings.APNS_AUTH_KEY,
                algorithm="ES256",
                headers={"kid": settings.APNS_KEY_ID},
            )
            PushService._jwt = token
            PushService._jwt_minted_at = now
            return token
        except Exception as e:
            logger.error("APNs provider JWT mint failed (%s: %s)", type(e).__name__, e)
            return None

    def _device_tokens_for(self, user_id: str) -> List[dict]:
        """Return the user's device tokens WITH their per-token environment so each
        can be routed to the correct APNs host."""
        try:
            result = (
                self.supabase.table("device_tokens")
                .select("token, environment")
                .eq("user_id", user_id)
                .execute()
            )
            return [
                {"token": r["token"], "environment": r.get("environment")}
                for r in (result.data or [])
                if r.get("token")
            ]
        except Exception as e:
            logger.error(
                "device_tokens read failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e,
            )
            return []

    def _prune_token(self, token: str) -> None:
        """Best-effort delete of a token APNs reports as gone."""
        try:
            self.supabase.table("device_tokens").delete().eq("token", token).execute()
            logger.info("Pruned dead APNs token …%s", token[-8:])
        except Exception as e:
            logger.warning("Failed to prune dead token (%s: %s)", type(e).__name__, e)

    async def send_to_user(
        self,
        user_id: str,
        *,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Send an alert push to every device the user has registered.

        Returns the number of pushes accepted by APNs. No-op (returns 0) when push
        is disabled or the user has no tokens. Never raises — a push failure must
        not break whatever action triggered it.
        """
        if not self.enabled:
            logger.info("Push disabled (APNs not configured) — skipping send to %s", user_id)
            return 0

        devices = self._device_tokens_for(user_id)
        if not devices:
            return 0

        jwt_token = self._provider_jwt()
        if not jwt_token:
            return 0

        payload: Dict[str, Any] = {
            "aps": {"alert": {"title": title, "body": body}, "sound": "default"}
        }
        if data:
            payload.update(data)

        headers = {
            "authorization": f"bearer {jwt_token}",
            "apns-topic": settings.APNS_BUNDLE_ID,
            "apns-push-type": "alert",
        }

        accepted = 0
        try:
            async with httpx.AsyncClient(http2=True, timeout=httpx.Timeout(10.0)) as client:
                for device in devices:
                    token = device["token"]
                    host = host_for_environment(device.get("environment"), settings.APNS_ENV)
                    try:
                        resp = await client.post(
                            f"{host}/3/device/{token}",
                            headers=headers,
                            json=payload,
                        )
                    except Exception as e:
                        logger.warning(
                            "APNs POST failed for token …%s (%s: %s)",
                            token[-8:], type(e).__name__, e,
                        )
                        continue
                    if resp.status_code == 200:
                        accepted += 1
                    elif resp.status_code == 410:
                        # 410 Unregistered = token is genuinely dead → prune.
                        # NOT on 400/BadDeviceToken: that can be an env/routing issue,
                        # and pruning a valid token would silently stop notifications.
                        self._prune_token(token)
                    else:
                        logger.warning(
                            "APNs rejected token …%s: %s %s",
                            token[-8:], resp.status_code, resp.text[:200],
                        )
        except Exception as e:
            logger.error(
                "Push send to user=%s failed (%s: %s)", user_id, type(e).__name__, e
            )
        return accepted
