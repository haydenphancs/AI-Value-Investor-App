"""Sign in with Apple REST API — authorization-code exchange and token revocation.

Used by `services/apple_sign_in_service.py` for ONE purpose (SOC-04, 2026-11-26): Apple's
account-deletion guidance asks an app that offers Sign in with Apple to revoke the user's
tokens when they delete their account. Sign-in itself never touches this module — Supabase
verifies the identity token — and GoTrue's id_token grant never receives an Apple refresh
token, so Supabase cannot revoke on our behalf. The only way to hold a revocable token is to
exchange the single-use `authorizationCode` the app gets at sign-in (valid ~5 minutes) at
`/auth/token`, keep the refresh token it returns, and hand it to `/auth/revoke` at deletion.

Both endpoints authenticate the CALLER with a `client_secret`: an ES256 JWT signed by a Sign in
with Apple `.p8` key (header `kid`; claims `iss` = team id, `sub` = client id, `aud` =
https://appleid.apple.com, `exp` ≤ 6 months — ours lives 5 minutes, minted per call).

Thin by rule (.claude/rules/integrations.md): HTTP in, dict out, typed exceptions. Storing the
refresh token, the subject check and every decision live in the service.

Secrets: the authorization code, the refresh token, the client secret and the key are NEVER in
an exception message or a log line from this module. Apple's OAuth `error` code ("invalid_grant",
"invalid_client", …) is safe and is what a failure carries. `log_redaction` also drops this
module's frame locals from Sentry (`_CREDENTIAL_FRAME_MODULES`).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

APPLE_ID_BASE_URL = "https://appleid.apple.com"
_AUDIENCE = "https://appleid.apple.com"
# Minted per call, so a short life costs nothing; Apple accepts up to 6 months.
_CLIENT_SECRET_TTL_SECONDS = 300
# Apple's OAuth error codes are short lowercase words; anything else in that field is not
# trusted into a log line or an exception message.
_ERROR_CODE_RE = re.compile(r"^[a-z_]{1,40}$")


# ── Exception hierarchy ────────────────────────────────────────────────


class AppleIDException(Exception):
    """Base for Sign in with Apple REST API errors. Never carries a token or a code."""


class AppleIDNotConfiguredException(AppleIDException):
    """APPLE_SIGN_IN_KEY_ID / APPLE_SIGN_IN_PRIVATE_KEY unset, or the key cannot sign.

    Raised BEFORE any I/O: the feature is off, not failing."""


class AppleIDRejectedException(AppleIDException):
    """Apple refused the request (a 4xx). Permanent for that request — never retried.

    `error` is Apple's OAuth error code when it sent a recognisable one ("invalid_grant": the
    code was already used, expired, or issued for another client; "invalid_client": the client
    secret, key id or team id is wrong), else "http_<status>"."""

    def __init__(self, message: str, *, status: int, error: str):
        super().__init__(message)
        self.status = status
        self.error = error


class AppleIDUnavailableException(AppleIDException):
    """Transport failure, timeout, 429 or 5xx — Apple could not be asked. Transient."""


# ── Client ─────────────────────────────────────────────────────────────

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=APPLE_ID_BASE_URL,
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
    return _client


async def close_apple_id_client() -> None:
    """Tear-down hook for the app.main lifespan. Idempotent."""
    global _client
    if _client is not None:
        client, _client = _client, None
        await client.aclose()


# ── Configuration + client secret ──────────────────────────────────────


def _private_key() -> Optional[str]:
    raw = settings.APPLE_SIGN_IN_PRIVATE_KEY
    if not raw or not raw.strip():
        return None
    key = raw.strip()
    # A `.p8` pasted into a one-line env var arrives with literal "\n" escapes (the APNs key
    # has hit the same thing — push_service names it as the usual cause of a signing failure).
    if "\\n" in key and "\n" not in key:
        key = key.replace("\\n", "\n")
    return key


def is_configured() -> bool:
    """True when every input to the client secret is present. Says nothing about validity."""
    return bool(
        (settings.APPLE_SIGN_IN_KEY_ID or "").strip()
        and _private_key()
        and (settings.APPLE_TEAM_ID or "").strip()
        and (settings.APPLE_BUNDLE_ID or "").strip()
    )


def default_client_id() -> str:
    """The client id our identity tokens are issued to: the app's bundle id."""
    return (settings.APPLE_BUNDLE_ID or "").strip()


def _client_secret(client_id: str, *, now: Optional[float] = None) -> str:
    if not is_configured():
        raise AppleIDNotConfiguredException(
            "Sign in with Apple REST API is not configured "
            "(APPLE_SIGN_IN_KEY_ID / APPLE_SIGN_IN_PRIVATE_KEY unset)"
        )
    issued = int(now if now is not None else time.time())
    try:
        from jose import jwt  # local import, as push_service: keeps module import cheap

        return jwt.encode(
            {
                "iss": settings.APPLE_TEAM_ID.strip(),
                "iat": issued,
                "exp": issued + _CLIENT_SECRET_TTL_SECONDS,
                "aud": _AUDIENCE,
                "sub": client_id,
            },
            _private_key(),
            algorithm="ES256",
            headers={"kid": settings.APPLE_SIGN_IN_KEY_ID.strip()},
        )
    except Exception as e:
        # The type only: a jose/cryptography message about a malformed key can quote part of it.
        raise AppleIDNotConfiguredException(
            f"APPLE_SIGN_IN_PRIVATE_KEY could not sign an ES256 client secret ({type(e).__name__}) "
            "— check it is the .p8 file's contents with its line breaks"
        ) from None


# ── Requests ───────────────────────────────────────────────────────────


def _error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"http_{response.status_code}"
    code = body.get("error") if isinstance(body, dict) else None
    if isinstance(code, str) and _ERROR_CODE_RE.match(code):
        return code
    return f"http_{response.status_code}"


async def _post_form(path: str, form: dict[str, str], *, operation: str) -> httpx.Response:
    try:
        response = await _get_client().post(
            path, data=form, headers={"Accept": "application/json"},
        )
    except httpx.TimeoutException as e:
        raise AppleIDUnavailableException(f"{operation}: timed out ({type(e).__name__})") from None
    except httpx.HTTPError as e:
        raise AppleIDUnavailableException(f"{operation}: transport error ({type(e).__name__})") from None

    status = response.status_code
    if status == 429 or status >= 500:
        raise AppleIDUnavailableException(f"{operation}: Apple answered HTTP {status}")
    if status >= 400:
        error = _error_code(response)
        raise AppleIDRejectedException(
            f"{operation}: Apple refused the request (HTTP {status}, {error})",
            status=status, error=error,
        )
    if status != 200:
        # 2xx other than 200 (or a 3xx httpx did not follow): not the documented answer.
        raise AppleIDUnavailableException(f"{operation}: unexpected HTTP {status}")
    return response


async def exchange_authorization_code(
    authorization_code: str, *, client_id: Optional[str] = None,
) -> dict[str, Any]:
    """POST /auth/token (grant_type=authorization_code). Returns Apple's JSON body as a dict:
    `access_token`, `token_type`, `expires_in`, `refresh_token`, `id_token`.

    The code is single-use and lives about five minutes, so this must run during the sign-in
    request that carried it. A native app's code needs no `redirect_uri`."""
    if not isinstance(authorization_code, str) or not authorization_code.strip():
        raise ValueError("authorization_code must be a non-empty string")
    cid = client_id or default_client_id()
    response = await _post_form(
        "/auth/token",
        {
            "client_id": cid,
            "client_secret": _client_secret(cid),
            "code": authorization_code.strip(),
            "grant_type": "authorization_code",
        },
        operation="apple token exchange",
    )
    try:
        body = response.json()
    except ValueError:
        raise AppleIDUnavailableException("apple token exchange: 200 with a body that is not JSON") from None
    if not isinstance(body, dict):
        raise AppleIDUnavailableException("apple token exchange: 200 with a JSON body that is not an object")
    return body


async def revoke_token(
    token: str, *, token_type_hint: str = "refresh_token", client_id: Optional[str] = None,
) -> None:
    """POST /auth/revoke. Apple answers 200 with an empty body on success — including for a
    token that is already revoked — so success means "this grant is no longer active"."""
    if not isinstance(token, str) or not token.strip():
        raise ValueError("token must be a non-empty string")
    if token_type_hint not in ("refresh_token", "access_token"):
        raise ValueError("token_type_hint must be refresh_token or access_token")
    cid = client_id or default_client_id()
    await _post_form(
        "/auth/revoke",
        {
            "client_id": cid,
            "client_secret": _client_secret(cid),
            "token": token.strip(),
            "token_type_hint": token_type_hint,
        },
        operation="apple token revoke",
    )
