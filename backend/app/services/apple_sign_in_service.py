"""Sign in with Apple grants: keep a revocable token at sign-in, revoke it at account deletion.

SOC-04 (2026-11-26). Apple's account-deletion guidance asks an app that offers Sign in with
Apple to revoke the user's tokens through its REST API when they delete their account. Before
this, `DELETE /users/me` removed every row and the auth user but left Caydex authorised on the
person's Apple ID — so a later "Continue with Apple" counted as a RETURNING authorization, Apple
sent no name, and the new account was named after the relay address.

Two halves, both best-effort — neither ever fails the request it runs in:

* `store_grant_from_authorization_code` — called by `POST /auth/oauth` for provider "apple"
  when the app sent `authorization_code` (shipped builds 1.0–1.03 never do). Exchanges the code
  at Apple's `/auth/token` and upserts the refresh token into `apple_sign_in_grants`
  (migration 213, service-role-only), keyed by the Caydex user. Bounded by a timeout, because it
  runs inside the sign-in request: the code lives ~5 minutes and is single-use, so it cannot be
  deferred.
* `revoke_for_account` — called by `DELETE /users/me` BEFORE `admin.delete_user` (the row's FK
  cascades from `public.users`, so after the delete there is nothing left to revoke with).

The feature is OFF until the owner creates a Sign in with Apple key and sets
APPLE_SIGN_IN_KEY_ID / APPLE_SIGN_IN_PRIVATE_KEY on Railway (OWNER_TASKS.md): sign-in stores
nothing (one WARNING per process) and deletion logs a WARNING per account.

Never logged, anywhere: the authorization code, the refresh token, the client secret, the key.
The Apple subject is an opaque per-team id and IS logged on a mismatch (it is what makes the
mismatch diagnosable). `log_redaction` drops this module's frame locals from Sentry.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.integrations import apple_id
from app.utils.supabase_async import sb_exec

logger = logging.getLogger(__name__)

TABLE = "apple_sign_in_grants"

#: The exchange runs inside the sign-in request; Apple usually answers in well under a second.
EXCHANGE_TIMEOUT_SECONDS = 6.0
#: The revoke runs inside the deletion request, before the auth row goes.
REVOKE_TIMEOUT_SECONDS = 8.0
#: Column bounds, mirrored from migration 213's CHECKs so an oversized value is refused here
#: (with a clear log line) rather than as an opaque constraint violation.
MAX_TOKEN_LENGTH = 4096
MAX_ID_LENGTH = 255

# Outcomes, returned for the caller's log line and for the tests. Never shown to a user.
STORED = "stored"
NOT_CONFIGURED = "not_configured"
NO_CODE = "no_code"
EXCHANGE_REJECTED = "exchange_rejected"
EXCHANGE_UNAVAILABLE = "exchange_unavailable"
NO_REFRESH_TOKEN = "no_refresh_token"
SUBJECT_UNVERIFIED = "subject_unverified"
SUBJECT_MISMATCH = "subject_mismatch"
STORE_FAILED = "store_failed"

REVOKED = "revoked"
NO_GRANT = "no_grant"
READ_FAILED = "read_failed"
REVOKE_REJECTED = "revoke_rejected"
REVOKE_UNAVAILABLE = "revoke_unavailable"

_warned_not_configured = False


def _unverified_subject(token: Any) -> Optional[str]:
    """The `sub` claim of a JWT WITHOUT verifying it — only ever called on a token that was
    verified elsewhere: the identity token Supabase just accepted, or the id_token Apple's own
    token endpoint returned to us over TLS (OpenID Connect Core §3.1.3.7: a token received
    directly from the token endpoint need not have its signature checked)."""
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    try:
        from jose import jwt

        claims = jwt.get_unverified_claims(token)
    except Exception:
        return None
    sub = claims.get("sub") if isinstance(claims, dict) else None
    if isinstance(sub, str) and 0 < len(sub) <= MAX_ID_LENGTH:
        return sub
    return None


def _warn_not_configured_once() -> None:
    global _warned_not_configured
    if _warned_not_configured:
        logger.debug("apple grant: Sign in with Apple REST API not configured — code not exchanged")
        return
    _warned_not_configured = True
    logger.warning(
        "apple grant: Sign in with Apple REST API is NOT configured (APPLE_SIGN_IN_KEY_ID / "
        "APPLE_SIGN_IN_PRIVATE_KEY unset) — Apple sign-ins are not storing a revocable token, so "
        "account deletion cannot revoke their Apple grant. Logged once per process."
    )


async def store_grant_from_authorization_code(
    supabase: Any, *, user_id: str, authorization_code: Optional[str], identity_token: str,
) -> str:
    """Exchange the sign-in's authorization code and keep the refresh token. Never raises.

    `identity_token` is the token Supabase just VERIFIED for `user_id`; its subject must equal
    the subject of the id_token Apple returns for the code, or nothing is stored. Without that
    check a caller could pair their own identity token with someone else's intercepted code and
    have their own account deletion revoke that person's Apple grant."""
    if not authorization_code or not authorization_code.strip():
        return NO_CODE
    if not apple_id.is_configured():
        _warn_not_configured_once()
        return NOT_CONFIGURED

    expected_sub = _unverified_subject(identity_token)
    if expected_sub is None:
        logger.warning(
            "apple grant: user=%s — the verified identity token has no readable subject; "
            "code not exchanged", user_id,
        )
        return SUBJECT_UNVERIFIED

    client_id = apple_id.default_client_id()
    try:
        body = await asyncio.wait_for(
            apple_id.exchange_authorization_code(authorization_code, client_id=client_id),
            timeout=EXCHANGE_TIMEOUT_SECONDS,
        )
    except apple_id.AppleIDRejectedException as e:
        # invalid_grant: already used (a retried /auth/oauth), expired, or another client's.
        # invalid_client: the key id / team id / key is wrong — an owner fix, hence ERROR.
        log = logger.error if e.error == "invalid_client" else logger.warning
        log("apple grant: user=%s — Apple refused the code exchange (%s, HTTP %s)",
            user_id, e.error, e.status)
        return EXCHANGE_REJECTED
    except apple_id.AppleIDNotConfiguredException as e:
        logger.error("apple grant: user=%s — %s", user_id, e)
        return NOT_CONFIGURED
    except (apple_id.AppleIDUnavailableException, asyncio.TimeoutError) as e:
        logger.warning(
            "apple grant: user=%s — code exchange failed (%s: %s); no revocable token kept for "
            "this sign-in", user_id, type(e).__name__, e,
        )
        return EXCHANGE_UNAVAILABLE
    except Exception as e:  # noqa: BLE001 — best-effort by contract; never fail a sign-in
        logger.error(
            "apple grant: user=%s — unexpected code-exchange failure (%s)",
            user_id, type(e).__name__, exc_info=True,
        )
        return EXCHANGE_UNAVAILABLE

    refresh_token = body.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip() \
            or len(refresh_token) > MAX_TOKEN_LENGTH:
        logger.warning(
            "apple grant: user=%s — Apple's token answer carried no usable refresh_token "
            "(keys: %s)", user_id, ",".join(sorted(k for k in body if isinstance(k, str))[:10]),
        )
        return NO_REFRESH_TOKEN

    returned_sub = _unverified_subject(body.get("id_token"))
    if returned_sub is None:
        logger.warning(
            "apple grant: user=%s — Apple's token answer has no readable id_token subject; "
            "refresh token discarded", user_id,
        )
        return SUBJECT_UNVERIFIED
    if returned_sub != expected_sub:
        logger.warning(
            "apple grant: user=%s — the code belongs to Apple subject %s, the verified identity "
            "token to %s; refresh token discarded (a code that is not this sign-in's)",
            user_id, returned_sub, expected_sub,
        )
        return SUBJECT_MISMATCH

    row = {
        "user_id": user_id,
        "apple_sub": expected_sub,
        "client_id": client_id,
        "refresh_token": refresh_token.strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await sb_exec(supabase.table(TABLE).upsert(row, on_conflict="user_id"))
    except Exception as e:  # noqa: BLE001 — best-effort; logged with the cause, never the token
        logger.error(
            "apple grant: user=%s — could not store the Apple refresh token (%s: %s); account "
            "deletion will not be able to revoke this grant (apply migration 213?)",
            user_id, type(e).__name__, e,
        )
        return STORE_FAILED

    logger.info("apple grant: stored a revocable Sign in with Apple token for user=%s", user_id)
    return STORED


async def revoke_for_account(supabase: Any, user_id: str) -> str:
    """Revoke the account's stored Sign in with Apple grant, if any. Never raises.

    Called by `DELETE /users/me` BEFORE `admin.delete_user`. A failure is logged and the
    deletion goes ahead: an account the user cannot delete is a Guideline 5.1.1(v) break, and a
    grant left authorised is the smaller harm (the user can still remove it in Settings → Apple
    Account → Sign in with Apple)."""
    if not apple_id.is_configured():
        logger.warning(
            "Account deletion: Sign in with Apple revocation is OFF (APPLE_SIGN_IN_KEY_ID / "
            "APPLE_SIGN_IN_PRIVATE_KEY unset) — user=%s's Apple grant, if any, stays authorised",
            user_id,
        )
        return NOT_CONFIGURED

    try:
        result = await sb_exec(
            supabase.table(TABLE).select("refresh_token, client_id").eq("user_id", user_id).limit(1)
        )
        rows = result.data or []
        if isinstance(rows, dict):
            rows = [rows]
    except Exception as e:  # noqa: BLE001
        logger.error(
            "Account deletion: could not read the Sign in with Apple grant for user=%s (%s: %s) "
            "— not revoked", user_id, type(e).__name__, e,
        )
        return READ_FAILED

    row = rows[0] if rows and isinstance(rows[0], dict) else None
    token = row.get("refresh_token") if row else None
    if not isinstance(token, str) or not token.strip():
        logger.info("Account deletion: no stored Sign in with Apple grant for user=%s", user_id)
        return NO_GRANT
    client_id = row.get("client_id") if isinstance(row.get("client_id"), str) else None

    try:
        await asyncio.wait_for(
            apple_id.revoke_token(
                token, token_type_hint="refresh_token",
                client_id=client_id or apple_id.default_client_id(),
            ),
            timeout=REVOKE_TIMEOUT_SECONDS,
        )
    except apple_id.AppleIDRejectedException as e:
        log = logger.error if e.error == "invalid_client" else logger.warning
        log("Account deletion: Apple refused to revoke user=%s's grant (%s, HTTP %s) — "
            "deleting anyway", user_id, e.error, e.status)
        return REVOKE_REJECTED
    except apple_id.AppleIDNotConfiguredException as e:
        logger.error("Account deletion: user=%s's Apple grant not revoked — %s", user_id, e)
        return NOT_CONFIGURED
    except (apple_id.AppleIDUnavailableException, asyncio.TimeoutError) as e:
        logger.error(
            "Account deletion: could not reach Apple to revoke user=%s's grant (%s: %s) — "
            "deleting anyway", user_id, type(e).__name__, e,
        )
        return REVOKE_UNAVAILABLE
    except Exception as e:  # noqa: BLE001 — never block a deletion
        logger.error(
            "Account deletion: unexpected failure revoking user=%s's Apple grant (%s) — "
            "deleting anyway", user_id, type(e).__name__, exc_info=True,
        )
        return REVOKE_UNAVAILABLE

    logger.info("Account deletion: revoked user=%s's Sign in with Apple grant", user_id)
    return REVOKED
