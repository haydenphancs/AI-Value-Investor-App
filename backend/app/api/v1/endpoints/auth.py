"""
Authentication Endpoints — Supabase Auth Proxy
Frontend: POST /auth/login, /auth/register, /auth/refresh, /auth/logout
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from supabase import Client
import logging
from datetime import datetime, timezone
from typing import Optional

from app.database import get_auth_client, get_supabase
from app.dependencies import get_current_user_id
from app.core.security import (
    create_access_token, create_refresh_token, decode_token, rate_limiter,
    trusted_client_ip, verify_supabase_token,
)
from app.api.error_response import ErrorCode, auth_error, make_error_response
from app.schemas.auth import (
    SignInRequest, SignUpRequest, RefreshTokenRequest,
    TokenResponse, AuthUserResponse,
    ForgotPasswordRequest, ResetPasswordRequest, ChangePasswordRequest,
    MessageResponse, PasswordChangedResponse, SignUpResponse, ResendConfirmationRequest,
    OAuthSignInRequest, SessionExchangeRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _auth_of(auth_client, supabase: Client) -> Client:
    """The client to run `auth.*` on.

    Normally the isolated one FastAPI injects (`get_auth_client`), which is what keeps the
    service-role singleton from being demoted — see `database.get_auth_client` for what the
    SDK does to a client after a successful sign-in.

    Falls back to `supabase` when `auth_client` was not injected, i.e. when a handler is
    invoked DIRECTLY as a Python function. That is this suite's standard idiom (there is no
    TestClient anywhere in backend/tests), so the fallback keeps ~44 existing call sites
    working without each having to hand in a second fake. In that case both names point at
    the same fake, which is exactly what those tests want to assert against.

    The isolation itself is pinned separately, at the source level, by
    tests/test_supabase_client_isolation.py — it does not depend on this fallback.
    """
    return auth_client if hasattr(auth_client, "auth") else supabase


def is_token_stale_after_password_change(
    payload: dict, user_id: Optional[str], supabase: Client
) -> bool:
    """True when this token was issued BEFORE the account's last password change.

    See migration 105 for the full reasoning. Short version: the app mints its own JWTs
    and validates them on signature + expiry only, so without this a password reset leaves
    every previously issued token valid.

    Fails OPEN (returns False) on a missing column, missing row, or DB error: a transient
    Supabase blip must not log out every user. The trade-off is deliberate — this is a
    containment measure layered on top of normal token expiry, not the primary auth gate.
    """
    issued_at = payload.get("iat")
    if not user_id or issued_at is None:
        return False
    try:
        row = (
            supabase.table("users")
            .select("password_changed_at")
            .eq("id", user_id)
            .single()
            .execute()
        )
        changed_at = (row.data or {}).get("password_changed_at")
        if not changed_at:
            return False
        changed_dt = datetime.fromisoformat(str(changed_at).replace("Z", "+00:00"))
        if changed_dt.tzinfo is None:
            changed_dt = changed_dt.replace(tzinfo=timezone.utc)
        # `iat` is a POSIX timestamp (int or float).
        issued_dt = datetime.fromtimestamp(float(issued_at), tz=timezone.utc)
        # Whole-second comparison — `iat` has no sub-second resolution, while
        # `password_changed_at` is stamped with microseconds, so a token minted in the same
        # second as the change floors below it and would be rejected despite being newer.
        # Mirrors dependencies._reject_if_password_changed_since_issue; see the note there.
        return issued_dt < changed_dt.replace(microsecond=0)
    except Exception as e:
        logger.warning(
            "password_changed_at check failed for user=%s (%s: %s) — allowing the token",
            user_id, type(e).__name__, e,
        )
        return False


def _mark_password_changed(supabase: Client, user_id: str) -> None:
    """Stamp `password_changed_at` so pre-existing tokens stop working.

    Best-effort by necessity: the password itself has already been changed by the time we
    get here, and failing the whole request would tell the user their reset didn't work
    when it did. Logged at ERROR because the security property silently degrades.
    """
    try:
        supabase.table("users").update(
            {"password_changed_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", user_id).execute()
    except Exception as e:
        logger.error(
            "Password changed for user=%s but stamping password_changed_at FAILED "
            "(%s: %s) — old sessions will remain valid until they expire",
            user_id, type(e).__name__, e,
        )


@router.post("/login", response_model=TokenResponse)
async def sign_in(
    request: SignInRequest,
    req: Request,
    supabase: Client = Depends(get_supabase),
    auth_client: Client = Depends(get_auth_client),
):
    """Sign in via Supabase Auth, return app tokens."""
    client_ip = trusted_client_ip(req)
    # TWO limiters, both must pass — mirroring the forgot/reset pair below.
    # IP alone was the whole control, and IP alone is never enough on a credential endpoint:
    # an attacker with a pool of addresses (or one spoofed header before this was keyed off a
    # trusted source) gets the full per-IP budget against a single victim account. The
    # per-EMAIL window caps guesses against one address no matter where they come from, which
    # is the property that actually protects the account.
    email_key = (request.email or "").strip().lower()
    # `protected=True` on every credential limiter: it routes these keys into a pool that
    # attacker-minted identifiers cannot reach. Sharing one table with the X-Guest-Id-keyed
    # buckets let ~20k cheap requests evict this very bucket and reset the cap below.
    _enforce_credential_limits(
        (f"login:ip:{client_ip}", 10, 60),
        (f"login:email:{email_key}", 10, 900),
        detail="Too many login attempts. Please try again later.",
        retry_after="60",
    )
    try:
        auth_response = _auth_of(auth_client, supabase).auth.sign_in_with_password({
            "email": request.email,
            "password": request.password,
        })

        user = auth_response.user
        if not user:
            raise auth_error(
                ErrorCode.AUTH_CREDENTIALS_INVALID,
                message="Supabase returned no user for a non-raising sign-in",
            )

        # Unconfirmed address → a DISTINCT error, not a generic 401. Telling the user
        # "invalid credentials" when their password was right and the address merely needs
        # confirming sends them to reset a password that is fine.
        #
        # Note Supabase usually rejects the sign-in itself when confirmation is required
        # (handled by the except below, which maps it to the same code). This branch covers
        # the case where a session is issued but the address is still unconfirmed.
        if not getattr(user, "email_confirmed_at", None):
            logger.info("Sign-in blocked for unconfirmed user=%s", user.id)
            return make_error_response(
                ErrorCode.EMAIL_NOT_CONFIRMED,
                message="Email address not confirmed",
            )

        # Fetch app-level user row (created by DB trigger on auth.users insert).
        # limit(1), NOT single(): the `if db_user.data else` fallback below only runs if this
        # read RETURNS empty — and `single()` never returns empty, it RAISES (PostgREST 406 /
        # PGRST116 → postgrest APIError) when the row is missing. So the fallback was dead
        # code, and a user whose signup trigger had not seeded public.users got a 500 on a
        # correct password instead of the intended graceful degradation to the auth id.
        db_user = supabase.table("users").select("id, email").eq(
            "id", str(user.id)
        ).limit(1).execute()

        db_rows = db_user.data or []
        if not db_rows:
            logger.warning(
                "Sign-in for auth user=%s has no public.users row — falling back to the auth id",
                user.id,
            )
        user_id = db_rows[0]["id"] if db_rows else str(user.id)

        access_token = create_access_token(data={"sub": user_id, "email": user.email})
        refresh_token = create_refresh_token(data={"sub": user_id, "email": user.email})

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=user_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        # Supabase raises "Email not confirmed" as a normal auth error. Surface it as the
        # specific code so the client can offer "resend" rather than "wrong password".
        if "not confirmed" in str(e).lower():
            logger.info("Sign-in rejected by Supabase: email not confirmed")
            return make_error_response(
                ErrorCode.EMAIL_NOT_CONFIRMED,
                message="Email address not confirmed",
            )
        if _is_rejected_credential(e):
            # A wrong password is a person being human, not the application failing.
            #
            # This was `logger.error(..., exc_info=True)`. Sentry's LoggingIntegration turns an
            # ERROR into an EVENT, so every single typo raised an `AuthApiError: Invalid login
            # credentials` issue in production — noise that buries real faults, burns quota,
            # and trains everyone to ignore the alert. INFO, and no stack trace: nothing here
            # is unexpected. The email is deliberately not logged; the per-email rate limiter
            # already bounds guessing, and this is a fintech backend.
            logger.info("Sign-in rejected: credentials did not match")
            raise auth_error(
                ErrorCode.AUTH_CREDENTIALS_INVALID,
                message="sign_in_with_password rejected the credentials",
            )

        # Anything else is OUR problem — a Supabase outage, a misconfiguration, a network
        # fault. This block used to launder all of it into "your password is wrong", which
        # sends the user off to reset a password that was never the issue and hides the
        # incident behind a 401 nobody investigates. AUTH_UNAVAILABLE is a retryable 503, and
        # the client is explicitly forbidden from clearing a credential on it.
        logger.error(
            "Sign in failed unexpectedly: %s: %s", type(e).__name__, e, exc_info=True
        )
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message=f"sign-in upstream failure: {type(e).__name__}",
        )


@router.post("/register", response_model=SignUpResponse)
async def sign_up(
    request: SignUpRequest,
    req: Request,
    supabase: Client = Depends(get_supabase),
    auth_client: Client = Depends(get_auth_client),
):
    """Register via Supabase Auth.

    EMAIL CONFIRMATION IS REQUIRED. On success this returns `confirmation_required = True`
    and NO tokens: the account exists but cannot be used until the address is confirmed.

    This endpoint previously minted app JWTs immediately, without ever looking at
    `email_confirmed_at` — so anyone could register an address they did not own and get a
    fully working account on it.

    Tokens ARE returned when the Supabase project has email confirmation switched off
    (local dev / self-hosted), because in that case a usable session genuinely exists.
    `confirmation_required` tells the client which world it is in rather than the client
    having to guess.

    Requires "Confirm email" to be enabled in Supabase → Authentication → Sign In / Providers.
    With it off, this degrades to the old immediate-session behaviour, which is why the
    response is explicit about which happened.
    """
    client_ip = trusted_client_ip(req)
    # Per-EMAIL as well as per-IP: every attempt makes Supabase send a confirmation email to
    # the address in the body, so an unbounded-per-address endpoint is a mail-bomb aimed at
    # whoever the attacker names — and it burns the project's mail quota, which is what stops
    # legitimate signups from arriving at all.
    email_key = (request.email or "").strip().lower()
    _enforce_credential_limits(
        (f"register:ip:{client_ip}", 5, 60),
        (f"register:email:{email_key}", 5, 3600),
        detail="Too many registration attempts. Please try again later.",
        retry_after="60",
    )
    try:
        auth_response = _auth_of(auth_client, supabase).auth.sign_up({
            "email": request.email,
            "password": request.password,
            "options": {
                "data": {"display_name": request.display_name}
            },
        })

        user = auth_response.user
        if not user:
            raise HTTPException(status_code=400, detail="Registration failed")

        # DB trigger auto-creates public.users row.
        # Update display_name (trigger may not copy it from metadata).
        supabase.table("users").update({
            "display_name": request.display_name,
        }).eq("id", str(user.id)).execute()

        # The confirmation gate. `email_confirmed_at` is set by Supabase only once the
        # address is verified; absent means "unconfirmed", so no tokens are issued.
        if not getattr(user, "email_confirmed_at", None):
            logger.info("Registration pending email confirmation for user=%s", user.id)
            return SignUpResponse(
                confirmation_required=True,
                message=(
                    "Check your email to confirm your address, then sign in."
                ),
            )

        # Confirmation disabled project-side: a real session exists, so behave as before.
        logger.info(
            "Registration completed WITHOUT confirmation for user=%s "
            "(email confirmation appears disabled in Supabase)", user.id,
        )
        access_token = create_access_token(data={"sub": str(user.id), "email": user.email})
        refresh_token = create_refresh_token(data={"sub": str(user.id), "email": user.email})

        return SignUpResponse(
            confirmation_required=False,
            message="Account created.",
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=str(user.id),
        )
    except HTTPException:
        raise
    except Exception as e:
        # A REJECTED PASSWORD is not a server failure and must not be laundered into one.
        # Every exception here used to become a bare 400 "Registration failed" — no reason, no
        # action. With leaked-password protection enabled that is the single most likely
        # failure on this route, and it would tell a user whose only mistake was reusing a
        # breached password that registration is broken, at the exact moment they are deciding
        # whether to bother.
        if _is_password_rejected(e):
            logger.info("Registration rejected: password refused by the provider")
            raise auth_error(
                ErrorCode.AUTH_PASSWORD_REJECTED,
                message="sign_up rejected the password",
                details=_optional_details(reasons=_password_rejection_reasons(e)),
            )
        logger.error(f"Sign up failed: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail="Registration failed")


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: RefreshTokenRequest,
    req: Request,
    supabase: Client = Depends(get_supabase),
    auth_client: Client = Depends(get_auth_client),
):
    """Refresh access token using refresh token."""
    # Generous, because a legitimate client refreshes rarely but several screens can race a
    # 401 at once. Present so a garbage-token flood cannot spin the decode + DB read path for
    # free — the other unlimited credential route was `/change-password`, now limited above.
    if not rate_limiter.is_allowed(
        f"refresh:ip:{trusted_client_ip(req)}", max_requests=60, window_seconds=60,
        protected=True,
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many refresh attempts. Please try again shortly.",
            headers={"Retry-After": "60"},
        )
    try:
        payload = decode_token(request.refresh_token)
        if payload.get("type") != "refresh":
            # The enum comment for AUTH_TOKEN_INVALID names this case explicitly.
            raise auth_error(
                ErrorCode.AUTH_TOKEN_INVALID,
                message=f"token type is {payload.get('type')!r}, expected 'refresh'",
            )

        user_id = payload.get("sub")
        email = payload.get("email")

        # The account must still exist. Without this, a token whose `public.users` row is
        # missing refreshes successfully forever while every real route 401s — see
        # `_app_user_row_exists`. AUTH_ACCOUNT_NOT_FOUND is one of the three codes iOS is
        # allowed to clear a credential on, so this ENDS the loop instead of feeding it.
        if user_id and not _app_user_row_exists(supabase, user_id):
            logger.warning("Refresh rejected: no public.users row for user=%s", user_id)
            raise auth_error(
                ErrorCode.AUTH_ACCOUNT_NOT_FOUND,
                message=f"no public.users row for {user_id}",
            )

        # A refresh token minted BEFORE the last password change must not mint new access
        # tokens — otherwise a reset doesn't evict a thief and they keep access for the
        # full 7-day refresh lifetime. This is the check that actually caps the window;
        # see migration 105.
        if is_token_stale_after_password_change(payload, user_id, supabase):
            logger.info(
                "Refresh rejected for user=%s: token predates last password change",
                user_id,
            )
            # AUTH_SESSION_EXPIRED is the code `dependencies.py` already raises for this
            # exact condition; its stock copy is the same sentence. Reusing it removes the
            # duplicate contract rather than papering over it.
            raise auth_error(
                ErrorCode.AUTH_SESSION_EXPIRED,
                message="refresh token predates users.password_changed_at",
            )

        access_token = create_access_token(data={"sub": user_id, "email": email})
        new_refresh = create_refresh_token(data={"sub": user_id, "email": email})

        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh,
            user_id=user_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        raise auth_error(
            ErrorCode.AUTH_TOKEN_INVALID,
            message=f"refresh token could not be decoded: {type(e).__name__}",
        )


@router.post("/logout")
async def logout(user_id: str = Depends(get_current_user_id)):
    """Logout (client deletes tokens)."""
    logger.info(f"User {user_id} logged out")
    return {"message": "Successfully logged out"}


@router.post("/resend-confirmation", response_model=MessageResponse)
async def resend_confirmation(
    request: ResendConfirmationRequest,
    req: Request,
    supabase: Client = Depends(get_supabase),
    auth_client: Client = Depends(get_auth_client),
):
    """Re-send the signup confirmation email.

    Needed because confirmation is now mandatory: without this, a user whose confirmation
    email was lost, expired, or filtered as spam is stuck with an unusable account and no
    way forward.

    Same enumeration and rate-limit posture as /forgot-password — the response never reveals
    whether the address is registered or already confirmed, and it is limited on both the IP
    and the address because it sends mail.
    """
    client_ip = trusted_client_ip(req)
    email_key = request.email.strip().lower()

    _enforce_credential_limits(
        (f"resend:ip:{client_ip}", 5, 3600),
        (f"resend:email:{email_key}", 3, 3600),
        detail="Too many requests. Please try again later.",
        retry_after="3600",
    )

    try:
        _auth_of(auth_client, supabase).auth.resend({"type": "signup", "email": email_key})
        logger.info("Confirmation email resend requested")
    except Exception as e:
        # Unknown address, already-confirmed address, and provider errors must all look
        # identical to the caller.
        logger.warning(
            "resend confirmation failed (%s: %s) — returning the generic response",
            type(e).__name__, e,
        )

    return MessageResponse(
        message=(
            "If that address needs confirming, we've sent a new confirmation email. "
            "Check your inbox, including spam."
        )
    )


# ── Social sign-in (Apple / Google) ───────────────────────────────────────────
#
# Both paths converge on the same outcome: a verified Supabase user, then app JWTs.
#
# `/oauth` takes a provider IDENTITY TOKEN obtained natively on-device. Supabase verifies
# it against the provider's public keys, so the backend never trusts a client-supplied
# token on its own. This is the path for Sign in with Apple.
#
# `/session-exchange` takes a Supabase-issued access token, for the web-redirect flow
# (Google via ASWebAuthenticationSession) where the round-trip ends with Supabase's own JWT
# rather than a provider id_token.
#
# Neither path is subject to the email-confirmation gate: Apple and Google supply an
# already-verified address, which is the whole point of using them.


# Supabase raises a generic `AuthApiError` for everything, so there is no distinct exception
# class to catch — the message is the only signal separating "this person typed the wrong
# password" from "our identity provider is down". Matched loosely because the SDK's wording has
# changed before; the fallback (treat it as an outage) is the safe direction to be wrong in.
_REJECTED_CREDENTIAL_MARKERS = (
    "invalid login credentials",
    "invalid credentials",
    "bad credentials",
    "invalid password",
)


def _is_rejected_credential(exc: Exception) -> bool:
    """True when the provider rejected the supplied password, as opposed to failing."""
    return any(marker in str(exc).lower() for marker in _REJECTED_CREDENTIAL_MARKERS)


# Last-resort wording match for a rejected NEW password. Only reached when neither typed
# signal below fires — see `_is_password_rejected`.
_WEAK_PASSWORD_MARKERS = (
    "weak_password",
    "password is known to be weak",
    "password should be at least",
    "pwned",
)


def _is_password_rejected(exc: Exception) -> bool:
    """True when the provider refused to SET the supplied password.

    Covers both halves of GoTrue's single `weak_password` code: fails the project's strength
    rules, and (once leaked-password protection is on) found in a breach corpus. The user's
    action is the same for both — choose a different password — so they share one code.

    THREE LAYERS, TYPED FIRST, and the order is deliberate:

      1. `AuthWeakPasswordError` — a real class in supabase_auth 2.27, carrying `.reasons`.
         Raised by the USER-facing calls (`sign_up`, `update_user`).
      2. `.code == "weak_password"` — the ADMIN call `admin.update_user_by_id`, which
         `change_password` uses, surfaces a plain `AuthApiError` whose `code` carries the same
         string rather than the typed subclass. Miss this and the change-password half is
         vacuously unhandled while the signup half looks fine.
      3. Wording match, mirroring `_REJECTED_CREDENTIAL_MARKERS`.

    Layer 3 alone would not be safe to rely on: an exception whose `str()` is empty defeats
    every marker list, which is exactly how a Gemini timeout classifier failed in this repo.

    ⚠️ `supabase_auth.errors.ErrorCode` is a `Literal` type alias, NOT an enum with members.
    Compare the string; `ErrorCode.weak_password` does not exist.
    """
    try:
        from supabase_auth.errors import AuthWeakPasswordError

        if isinstance(exc, AuthWeakPasswordError):
            return True
    except ImportError:  # pragma: no cover - SDK moved; fall through to the softer checks
        pass

    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.strip().lower() == "weak_password":
        return True

    return any(marker in str(exc).lower() for marker in _WEAK_PASSWORD_MARKERS)


def _optional_details(**kwargs) -> Optional[dict]:
    """Flat, non-empty details only — or None so the key is omitted entirely."""
    cleaned = {k: v for k, v in kwargs.items() if v}
    return cleaned or None


def _password_rejection_reasons(exc: Exception) -> Optional[str]:
    """The provider's own reasons, as a flat comma-joined string, when it supplied any.

    Flat on purpose: `details` values reach iOS through `AnyCodable`, which decodes
    String/Int/Double/Bool only and silently yields "" for a list (auth.md §3).
    """
    reasons = getattr(exc, "reasons", None)
    if isinstance(reasons, (list, tuple)) and reasons:
        return ", ".join(str(r) for r in reasons)[:200]
    return None


def _enforce_credential_limits(
    *checks: tuple[str, int, int],
    detail: str,
    retry_after: str,
) -> None:
    """Evaluate credential limiters IN ORDER, raising 429 on the FIRST denial.

    The short-circuit is a security property, not tidiness. `rate_limiter.is_allowed` INSERTS
    and LRU-touches its key *before* deciding, so the old shape —

        ip_ok    = is_allowed("login:ip:...")
        email_ok = is_allowed("login:email:...")
        if not (ip_ok and email_ok): raise 429

    — created a `login:email:<attacker-chosen>` entry even for requests the IP limiter had
    already refused. That reopened the eviction attack the protected pool was added to close,
    by a different door: ~20k cheap POSTs from ONE address, each 429'd without ever reaching
    Supabase, still inserted 20k keys, overflowed `_MAX_TRACKED_PROTECTED`, and popped the
    victim's `login:email:` bucket off the LRU front — handing the attacker a fresh 10-guess
    window, repeatable. The class comment in `core/security.py` claimed this cost ~33 hours;
    with both limiters always evaluated it cost seconds. Short-circuiting makes that claim
    true: a denied caller can no longer insert the second key at all, so filling the pool is
    bounded by the per-IP limiter it must pass first.

    Order matters. Put the limiter whose key the CALLER cannot choose (IP) first, and the one
    keyed on caller-supplied input (email) second.
    """
    for key, max_requests, window_seconds in checks:
        if not rate_limiter.is_allowed(
            key, max_requests=max_requests, window_seconds=window_seconds, protected=True
        ):
            raise HTTPException(
                status_code=429, detail=detail, headers={"Retry-After": retry_after}
            )


def _app_user_row_exists(supabase: Client, user_id: str) -> bool:
    """Does this id have a `public.users` row?

    The signup trigger (`handle_new_auth_user`) creates it, but it swallows its own failures,
    and an aborted account deletion can leave the same shape. When the row is missing, minting
    tokens produces a credential that authenticates against NOTHING: every route using
    `get_current_user` answers 401 AUTH_ACCOUNT_NOT_FOUND, while `/auth/refresh` happily keeps
    reissuing — `is_token_stale_after_password_change` fails OPEN when its read finds no row, so
    nothing else on that path notices either. iOS then loops: profile 401 → refresh succeeds →
    profile 401 → `.restoring` → backoff → forever, never clearing the credential, never
    prompting, re-hitting the backend for the life of the install.

    `limit(1)` rather than `single()`: "no row" must be an empty list, not an exception we would
    have to disambiguate from a transport failure.
    """
    try:
        result = supabase.table("users").select("id").eq("id", user_id).limit(1).execute()
        return bool(result.data)
    except Exception as e:
        # Fail OPEN on a transport error — a Supabase blip must not lock out every refresh.
        # Only a definitive "no row" is treated as a missing account.
        logger.warning(
            "users existence check unavailable for user=%s: %s: %s — allowing",
            user_id, type(e).__name__, e,
        )
        return True


def _issue_app_tokens_for(user_id: str, email: Optional[str]) -> TokenResponse:
    """Mint the app's own JWTs for an authenticated Supabase user."""
    access_token = create_access_token(data={"sub": user_id, "email": email})
    refresh_token = create_refresh_token(data={"sub": user_id, "email": email})
    return TokenResponse(
        access_token=access_token, refresh_token=refresh_token, user_id=user_id
    )


def _ensure_display_name(supabase: Client, user_id: str, display_name: Optional[str]) -> None:
    """Persist a display name if we have one and the row doesn't.

    Apple returns the user's name ONLY on the very first authorization, so if we don't
    capture it then it is gone for good. Never overwrites an existing name — the user may
    have since changed it deliberately.
    """
    if not display_name or not display_name.strip():
        return
    try:
        row = (
            supabase.table("users").select("display_name").eq("id", user_id).single().execute()
        )
        if (row.data or {}).get("display_name"):
            return
        supabase.table("users").update(
            {"display_name": display_name.strip()}
        ).eq("id", user_id).execute()
    except Exception as e:
        # Cosmetic — never fail a sign-in over a name.
        logger.warning(
            "Could not persist display_name for user=%s (%s: %s)",
            user_id, type(e).__name__, e,
        )


@router.post("/oauth", response_model=TokenResponse)
async def oauth_sign_in(
    request: OAuthSignInRequest,
    req: Request,
    supabase: Client = Depends(get_supabase),
    auth_client: Client = Depends(get_auth_client),
):
    """Sign in with a native provider identity token (Sign in with Apple)."""
    client_ip = trusted_client_ip(req)
    if not rate_limiter.is_allowed(
        f"oauth:{client_ip}", max_requests=20, window_seconds=60, protected=True
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts. Please try again later.",
            headers={"Retry-After": "60"},
        )

    credentials: dict = {"provider": request.provider, "token": request.id_token}
    if request.nonce:
        credentials["nonce"] = request.nonce

    try:
        result = _auth_of(auth_client, supabase).auth.sign_in_with_id_token(credentials)
    except Exception as e:
        # Includes a bad signature, a wrong audience, an expired token, and a nonce
        # mismatch. Deliberately not echoed to the client.
        logger.warning(
            "OAuth sign-in failed for provider=%s: %s: %s",
            request.provider, type(e).__name__, e,
        )
        # Copy stays deliberately vague — it must not confirm whether an account exists —
        # but the CODE is now machine-readable, and no longer collapses into the client's
        # "Email or password is incorrect" fallback for a flow with no password in it.
        raise auth_error(
            ErrorCode.AUTH_PROVIDER_FAILED,
            message=f"sign_in_with_id_token failed for provider={request.provider}",
        )

    user = getattr(result, "user", None)
    if not user or not getattr(user, "id", None):
        raise auth_error(
            ErrorCode.AUTH_PROVIDER_FAILED,
            message=f"provider={request.provider} verified but returned no user id",
        )

    user_id = str(user.id)

    # Same app-row confirmation `/session-exchange` already does below. Its absence here was
    # the asymmetry that made the missing-row case reachable at all: Apple sign-in minted
    # tokens for an id with no `public.users` row, iOS wrote them to the Keychain, and the
    # immediate profile fetch 401'd — leaving a stored credential the client could never
    # validate and never clear.
    if not _app_user_row_exists(supabase, user_id):
        logger.error(
            "OAuth: no public.users row for verified provider=%s user=%s — refusing to mint "
            "tokens (check the handle_new_auth_user trigger for this account)",
            request.provider, user_id,
        )
        raise auth_error(
            ErrorCode.AUTH_ACCOUNT_NOT_FOUND,
            message=f"no public.users row for {user_id}",
        )

    _ensure_display_name(supabase, user_id, request.display_name)
    logger.info("OAuth sign-in succeeded provider=%s user=%s", request.provider, user_id)
    return _issue_app_tokens_for(user_id, getattr(user, "email", None))


@router.post("/session-exchange", response_model=TokenResponse)
async def session_exchange(
    request: SessionExchangeRequest,
    req: Request,
    supabase: Client = Depends(get_supabase),
    auth_client: Client = Depends(get_auth_client),
):
    """Exchange a Supabase access token for app tokens (web OAuth flow, e.g. Google).

    The Supabase JWT is verified by signature via `verify_supabase_token` — the `sub` claim
    is only trusted after that check passes.
    """
    client_ip = trusted_client_ip(req)
    if not rate_limiter.is_allowed(
        f"exchange:{client_ip}", max_requests=20, window_seconds=60, protected=True
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts. Please try again later.",
            headers={"Retry-After": "60"},
        )

    payload = await verify_supabase_token(request.supabase_access_token)
    if not payload or not payload.get("sub"):
        logger.warning("session-exchange rejected: Supabase token failed verification")
        raise auth_error(
            ErrorCode.AUTH_PROVIDER_FAILED,
            message="supabase access token failed signature verification",
        )

    user_id = str(payload["sub"])
    email = payload.get("email")

    # The DB trigger creates public.users on auth.users insert. Confirm it landed rather
    # than minting tokens for an id with no app-level row.
    try:
        row = supabase.table("users").select("id").eq("id", user_id).single().execute()
        if not (row.data or {}).get("id"):
            raise ValueError("no public.users row")
    except Exception as e:
        logger.error(
            "session-exchange: no app user row for verified Supabase user=%s (%s: %s)",
            user_id, type(e).__name__, e,
        )
        raise HTTPException(
            status_code=500, detail="Your account isn't ready yet. Please try again."
        )

    logger.info("Session exchange succeeded for user=%s", user_id)
    return _issue_app_tokens_for(user_id, email)


# ── Password recovery ─────────────────────────────────────────────────────────
#
# There was no recovery path at all: a user who forgot their password was permanently
# locked out with no way back in.
#
# Flow chosen: Supabase's recovery OTP, verified in-app. The alternative — Supabase's
# emailed magic link — needs either a hosted redirect page or Universal Links with an
# Associated Domains entitlement, neither of which exists yet. A 6-digit code the user
# types into the app needs no hosting and no deep-link plumbing.
#
# REQUIRES A ONE-TIME SUPABASE DASHBOARD CHANGE: the "Reset Password" email template must
# include `{{ .Token }}`. The default template only has `{{ .ConfirmationURL }}`, and with
# that template users receive a link and no code. See the launch checklist.

_GENERIC_RESET_MESSAGE = (
    "If an account exists for that email, we've sent password reset instructions. "
    "Check your inbox, including spam."
)


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    request: ForgotPasswordRequest,
    req: Request,
    supabase: Client = Depends(get_supabase),
    auth_client: Client = Depends(get_auth_client),
):
    """Send a password-reset code.

    ALWAYS returns the same 200 message, whether or not the account exists. Returning 404
    for an unknown email turns this endpoint into an account-enumeration oracle — an
    attacker could test which emails are registered on a finance app, which is exactly the
    list a credential-stuffer wants.

    Rate-limited on BOTH the client IP and the target email: per-IP alone lets a
    distributed caller email-bomb one victim, and per-email alone lets one host enumerate
    many addresses.
    """
    client_ip = trusted_client_ip(req)
    email_key = request.email.strip().lower()

    # 5/hour per IP, 3/hour per address. Deliberately tight — this endpoint sends mail.
    # Still a generic response shape, but 429 so a legitimate client can back off.
    _enforce_credential_limits(
        (f"forgot:ip:{client_ip}", 5, 3600),
        (f"forgot:email:{email_key}", 3, 3600),
        detail="Too many reset requests. Please try again later.",
        retry_after="3600",
    )

    try:
        _auth_of(auth_client, supabase).auth.reset_password_for_email(email_key)
        logger.info("Password reset requested for a registered-or-not address")
    except Exception as e:
        # Never surface this. A provider error and an unknown address must be
        # indistinguishable to the caller.
        logger.warning(
            "reset_password_for_email failed (%s: %s) — returning the generic response",
            type(e).__name__, e,
        )

    return MessageResponse(message=_GENERIC_RESET_MESSAGE)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    request: ResetPasswordRequest,
    req: Request,
    supabase: Client = Depends(get_supabase),
    auth_client: Client = Depends(get_auth_client),
):
    """Complete a reset with the emailed code, then set the new password.

    A 6-digit code is only ~1M possibilities, so this is rate-limited per email AND per IP
    independently of Supabase's own limits. Without it, the code is brute-forceable.

    On success `password_changed_at` is stamped, which invalidates tokens an attacker may
    already hold (migration 105) — the reason someone resets a password is often that
    somebody else has their session.
    """
    client_ip = trusted_client_ip(req)
    email_key = request.email.strip().lower()

    _enforce_credential_limits(
        (f"reset:ip:{client_ip}", 10, 3600),
        (f"reset:email:{email_key}", 10, 3600),
        detail="Too many attempts. Please request a new code and try again later.",
        retry_after="3600",
    )

    # 1. Verify the OTP. This is what proves control of the mailbox.
    try:
        verified = _auth_of(auth_client, supabase).auth.verify_otp({
            "email": email_key,
            "token": request.code,
            "type": "recovery",
        })
    except Exception as e:
        logger.info("Password reset OTP verification failed (%s)", type(e).__name__)
        raise HTTPException(
            status_code=400,
            detail="That code is invalid or has expired. Request a new one.",
        )

    user = getattr(verified, "user", None)
    if not user or not getattr(user, "id", None):
        raise HTTPException(
            status_code=400,
            detail="That code is invalid or has expired. Request a new one.",
        )
    user_id = str(user.id)

    # 2. Set the new password with the admin API. Using admin rather than the session from
    #    verify_otp keeps this independent of SDK session state on a shared client.
    try:
        _auth_of(auth_client, supabase).auth.admin.update_user_by_id(user_id, {"password": request.new_password})
    except Exception as e:
        logger.error(
            "Password update failed after a VERIFIED reset code for user=%s: %s: %s",
            user_id, type(e).__name__, e,
        )
        raise HTTPException(
            status_code=500,
            detail="We couldn't set your new password. Please try again.",
        )

    # 3. Evict sessions issued before this moment.
    _mark_password_changed(supabase, user_id)
    logger.info("Password reset completed for user=%s", user_id)

    return MessageResponse(
        message="Your password has been reset. You can now sign in with your new password."
    )


@router.post("/change-password", response_model=PasswordChangedResponse)
async def change_password(
    request: ChangePasswordRequest,
    req: Request,
    user_id: str = Depends(get_current_user_id),
    supabase: Client = Depends(get_supabase),
    auth_client: Client = Depends(get_auth_client),
):
    """Change the password of a signed-in user.

    Requires the CURRENT password even though the caller is authenticated: otherwise a
    stolen access token is enough to take permanent ownership of the account by changing
    its password. Verified by attempting a real sign-in with it.
    """
    # RATE LIMITED, and it must be. This route performs a real `sign_in_with_password` per
    # call (step 2 below), so it is a password oracle for anyone holding a stolen access
    # token — which is exactly the threat the current-password requirement above exists to
    # stop. It was the ONLY credential route with no limiter; every other one keys off
    # `trusted_client_ip`. Supabase's own limiting is not a backstop here: every call leaves
    # from the Railway container's address, so tripping it degrades sign-in for ALL users.
    #
    # Keyed per USER as well as per IP — the per-IP half alone would let an attacker with a
    # pool of addresses keep guessing against one account.
    client_ip = trusted_client_ip(req)
    _enforce_credential_limits(
        (f"changepw:ip:{client_ip}", 10, 3600),
        (f"changepw:user:{user_id}", 5, 900),
        detail="Too many password change attempts. Please try again later.",
        retry_after="900",
    )

    # Resolve the account's email — the request doesn't carry it, and it must come from
    # the token's subject rather than user input.
    try:
        # `limit(1)`, not `single()`. `single()` RAISES on zero rows (PostgREST 406/PGRST116),
        # so a missing `public.users` row — the exact state `_app_user_row_exists` exists to
        # handle — became an indistinguishable 500 instead of the 404 below. That 500 is a
        # bare-string body iOS cannot decode, so it surfaced as `.serverError` and used to be
        # auto-retried twice, burning three of five attempts per tap.
        result = supabase.table("users").select("email").eq("id", user_id).limit(1).execute()
        rows = result.data or []
        email = rows[0].get("email") if rows else None
    except Exception as e:
        logger.error(
            "change-password: user lookup failed for user=%s: %s: %s",
            user_id, type(e).__name__, e,
        )
        # Structured and retryable, so the client can tell a transport blip from "your account
        # is gone" — and so it stops looking like a generic 500 to a decoder that then retries.
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message=f"users lookup failed: {type(e).__name__}",
        )

    if not email:
        raise auth_error(
            ErrorCode.AUTH_ACCOUNT_NOT_FOUND,
            message=f"no public.users row for {user_id}",
        )

    if request.new_password == request.current_password:
        raise HTTPException(
            status_code=400,
            detail="Your new password must be different from your current one.",
        )

    # Verify the current password.
    try:
        _auth_of(auth_client, supabase).auth.sign_in_with_password({
            "email": email,
            "password": request.current_password,
        })
    except Exception as e:
        # CLASSIFY, exactly as /login does. This block used to map EVERY exception to
        # "Your current password is incorrect." — so a GoTrue 429 (which this endpoint's own
        # docstring warns about, since every call leaves from the one Railway egress IP), a
        # Supabase 5xx, or a connect timeout all told the user a false thing about their
        # password and pushed them toward a reset they did not need.
        #
        # It also cost them the lockout budget: the per-user limiter above CONSUMES its slot at
        # check time and nothing refunds it, so five outage-induced failures locked the real
        # owner out of password change for 15 minutes.
        if _is_rejected_credential(e):
            logger.info("change-password: current password did not match for user=%s", user_id)
            # The caller IS authenticated and their session is fine — only the field is wrong.
            # As a bare-string 401 this reached iOS as `.unauthorized`, which shows "Your
            # session has expired", AND set `triggersTokenRefresh`; since `.changePassword` is
            # excluded from `isAuthEndpoint`, the client refreshed and REPLAYED the request,
            # burning two of the five per-user attempts on a single typo.
            raise auth_error(
                ErrorCode.AUTH_CREDENTIALS_INVALID,
                message="current-password re-authentication failed",
                user_message="Your current password is incorrect.",
            )

        # Ours, not theirs. Retryable, and it must reach Sentry — the old `except Exception:`
        # bound nothing and logged at INFO with no type, no message and no stack, so during a
        # GoTrue incident there was nothing greppable separating an outage from user typos.
        logger.error(
            "change-password: re-authentication failed unexpectedly for user=%s: %s: %s",
            user_id, type(e).__name__, e, exc_info=True,
        )
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message=f"current-password check upstream failure: {type(e).__name__}",
        )

    try:
        _auth_of(auth_client, supabase).auth.admin.update_user_by_id(user_id, {"password": request.new_password})
    except Exception as e:
        # Same split as the re-authentication block above: THEIRS vs OURS.
        #
        # A refused new password is the user's to fix and is not an outage. As a bare-string
        # 500 it was doubly broken: `APIClient.validateResponse`'s 5xx arm has no `detail`
        # fallback (only the 4xx arm does), so the wording never reached iOS at all — the user
        # saw a generic transient server error and was invited to retry the one password that
        # can never be accepted.
        if _is_password_rejected(e):
            logger.info("change-password: new password refused by the provider for user=%s", user_id)
            raise auth_error(
                ErrorCode.AUTH_PASSWORD_REJECTED,
                message="admin.update_user_by_id rejected the new password",
                details=_optional_details(reasons=_password_rejection_reasons(e)),
            )
        logger.error(
            "change-password: update failed for user=%s: %s: %s",
            user_id, type(e).__name__, e, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="We couldn't change your password. Please try again.",
        )

    _mark_password_changed(supabase, user_id)
    logger.info("Password changed for user=%s", user_id)

    # Re-issue THIS caller's credentials, minted after the stamp above so `iat >=
    # password_changed_at`. Without this the request that changed the password evicted its own
    # session: the next authenticated call 401'd and the app signed the user out seconds after
    # telling them the change succeeded. Every OTHER device is still evicted, which is the point.
    tokens = _issue_app_tokens_for(user_id, email)
    return PasswordChangedResponse(
        message="Your password has been changed. Other devices will need to sign in again.",
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        user_id=user_id,
    )
