"""
Authentication Endpoints — Supabase Auth Proxy
Frontend: POST /auth/login, /auth/register, /auth/confirm-email, /auth/refresh, /auth/logout
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError
from jose.exceptions import ExpiredSignatureError
from supabase import Client
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from app.database import (
    get_admin_client, get_auth_client, get_supabase, resolve_admin_client,
    run_gotrue,
)
from app.dependencies import forward_client_ip_to_gotrue, get_current_user_id
from app.core.security import (
    create_access_token, create_device_proof, create_refresh_token, decode_token,
    rate_limiter, trusted_client_ip, verify_device_proof, verify_supabase_token,
)
from app.api.error_response import ErrorCode, auth_error, make_error_response
from app.services.auth_methods_service import auth_methods_service
from app.services import apple_sign_in_service
from app.schemas.auth import (
    SignInRequest, SignUpRequest, RefreshTokenRequest,
    TokenResponse, AuthUserResponse,
    ForgotPasswordRequest, ResetPasswordRequest, ChangePasswordRequest,
    SetPasswordRequest, ConfirmEmailRequest,
    MessageResponse, PasswordChangedResponse, SignUpResponse, ResendConfirmationRequest,
    OAuthSignInRequest, SessionExchangeRequest,
    PASSWORD_TOO_LONG_MESSAGE, _validate_password_strength,
)
from app.utils.supabase_async import sb_exec
import asyncio

logger = logging.getLogger(__name__)

# Limits on the three routes that make Supabase SEND an email (owner decision 2026-11-26,
# "15-minute window": an hour was long enough that a person retrying a few times gave up). The
# 429's `Retry-After` is the window of the bucket that refused, and the app turns it into words.
#
# SHORT buckets (15 minutes) are the ones a real person meets:
#   /register             5 per address, 10 per IP
#   /resend-confirmation  3 per address, 10 per IP
#   /forgot-password      3 per address, 10 per IP
# Each IP bucket stays LOOSER than its address bucket, so one person's own retries are refused
# by the 15-minute address window, never by something longer.
#
# HOURLY ceilings sit behind them, sized above anything a person does, so the short windows do
# not multiply abuse by four (review 2026-11-26: 11 → 44 emails an hour into one inbox):
#   per address: 16 confirmation emails (sign-up + resend together) and 6 reset emails an hour;
#   per IP:      30 emails an hour across all three routes (the project's mail budget is 100).
# One inbox can therefore get at most ~22 Caydex emails an hour (it was 11 with hourly windows),
# and one IP at most 30. Supabase's "Minimum interval per user" (60 s) is kept PER EMAIL TYPE,
# so it does not lower these. The /register IP limits also bound how fast one address can test
# emails, now that a sign-up says plainly when one is in use (AUTH_EMAIL_ALREADY_REGISTERED):
# 30 an hour (it was 5 a MINUTE). The code-ENTRY routes (/confirm-email, /reset-password) keep
# their hour: those limit guesses at a code, not mail.
REGISTER_EMAIL_WINDOW_SECONDS = 900
MAIL_SEND_WINDOW_SECONDS = 900
MAIL_HOUR_SECONDS = 3600
CONFIRM_MAIL_PER_ADDRESS_PER_HOUR = 16
RESET_MAIL_PER_ADDRESS_PER_HOUR = 6
MAIL_PER_IP_PER_HOUR = 30

# The sign-up answer while the address still needs confirming: a NEW address, or one whose
# earlier sign-up was never confirmed (GoTrue re-sends that one's email). An address that
# already has a CONFIRMED account no longer gets this answer: it is told so plainly with
# AUTH_EMAIL_ALREADY_REGISTERED (owner decision 2026-11-26). Every build shows this sentence:
# 1.0-1.03 on their "Confirm your email" screen, the new build under the address on its code
# screen. Kept neutral about what the email holds (a code AND a link).
SIGN_UP_PENDING_MESSAGE = "Check your email to confirm your address."

# Every route here may call Supabase Auth through `run_gotrue`. Router-level, so a route added
# tomorrow forwards the caller's trusted address too (SEC-7): GoTrue then rate-limits per USER
# instead of on the one Railway egress address every user shares. Never raises.
router = APIRouter(dependencies=[Depends(forward_client_ip_to_gotrue)])


class _IdentityStoreUnavailable(Exception):
    """A `public.users` read failed, so a security check could not be answered.

    Raised (never swallowed) by the checks that guard a token-MINTING path — `/auth/refresh`
    and `/auth/session-exchange` — which turn it into 503 AUTH_UNAVAILABLE. That status is
    retryable and iOS keeps its credential on it (APIClient's 5xx arm), so "a Supabase blip
    must not log everyone out" still holds; what changed is that a blip no longer MINTS.
    Internal to this module: it never reaches `classify_exception`.
    """


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


def _issued_before_password_change(issued_at: Any, changed_at: Any) -> bool:
    """Pure comparison: was a token with this `iat` minted before `password_changed_at`?

    Shared by `/auth/refresh` (our own tokens) and `/auth/session-exchange` (a Supabase JWT),
    so the two can never disagree about the boundary. Raises ValueError/TypeError on a value
    that cannot be read; callers on a minting path treat that as "cannot prove fresh".
    """
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


def is_token_stale_after_password_change(
    payload: dict, user_id: Optional[str], supabase: Client
) -> bool:
    """True when this token was issued BEFORE the account's last password change.

    See migration 105 for the full reasoning. Short version: the app mints its own JWTs
    and validates them on signature + expiry only, so without this a password reset leaves
    every previously issued token valid.

    FAILS CLOSED (SEC-4): a read or parse error raises `_IdentityStoreUnavailable`, which the
    refresh path answers with 503 AUTH_UNAVAILABLE. It used to return False ("allowing the
    token"), and on `/auth/refresh` that was not a harmless blip: the refresh then MINTED a new
    pair whose `iat` post-dates the stamp, so one transient read failure laundered a pre-reset
    refresh token into a permanent post-reset session — and refresh allows 60 tries a minute.
    A 503 is retryable and iOS keeps its credential on it, so a blip still logs nobody out.

    A row with no stamp, or NO ROW, is "not stale": existence is `_app_user_row_exists`'s
    question, asked first. `limit(1)` rather than `single()` for exactly that reason —
    `single()` raises on zero rows, which would read as an outage.
    """
    issued_at = payload.get("iat")
    if not user_id or issued_at is None:
        return False
    try:
        result = (
            supabase.table("users")
            .select("password_changed_at")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if isinstance(rows, dict):  # a client that answered with the bare object
            rows = [rows]
        changed_at = rows[0].get("password_changed_at") if rows else None
        return _issued_before_password_change(issued_at, changed_at)
    except Exception as e:
        logger.error(
            "password_changed_at check unavailable for user=%s (%s: %s) — refusing to mint "
            "(fail closed)", user_id, type(e).__name__, e,
        )
        raise _IdentityStoreUnavailable(f"{type(e).__name__}: {e}") from e


#: The stamp is retried once (inside the worker thread, so the sleep never blocks the loop).
_STAMP_RETRY_DELAY_SECONDS = 0.25


def _mark_password_changed(supabase: Client, user_id: str) -> None:
    """Stamp `password_changed_at` so pre-existing tokens stop working.

    Best-effort by necessity: the password itself has already been changed by the time we
    get here, and failing the whole request would tell the user their reset didn't work
    when it did. So it is RETRIED once (a transient PostgREST blip is the common failure), and
    a second failure is logged at ERROR because the security property silently degrades: old
    sessions stay valid until they expire.

    Runs via `asyncio.to_thread` at every call site, so the short sleep is off the event loop.
    """
    last: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            supabase.table("users").update(
                {"password_changed_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", user_id).execute()
            if attempt > 1:
                logger.warning(
                    "password_changed_at stamp for user=%s succeeded on retry", user_id
                )
            return
        except Exception as e:
            last = e
            if attempt == 1:
                logger.warning(
                    "password_changed_at stamp for user=%s failed (%s: %s) — retrying once",
                    user_id, type(e).__name__, e,
                )
                time.sleep(_STAMP_RETRY_DELAY_SECONDS)
    logger.error(
        "Password changed for user=%s but stamping password_changed_at FAILED twice "
        "(%s: %s) — old sessions will remain valid until they expire",
        user_id, type(last).__name__, last,
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
    # The per-email bucket is what made a known address trivially LOCKABLE (ten wrong
    # passwords every fifteen minutes, inside the attacker's own per-IP budget); a device
    # that has signed into this account before carries a proof and is judged on its own
    # bucket instead — see `_per_email_check`.
    _enforce_credential_limits(
        (f"login:ip:{client_ip}", 10, 60),
        _per_email_check("login", email_key, 10, 900, req),
        detail="Too many login attempts. Please try again later.",
    )
    try:
        auth_response = await run_gotrue(
            _auth_of(auth_client, supabase).auth.sign_in_with_password,
            {"email": request.email, "password": request.password},
        )

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
        db_user = (await sb_exec(
                      supabase.table("users").select("id, email").eq(
                      "id", str(user.id)
                      ).limit(1)
                  ))

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
            # A verified credential earns this install its lockout exemption for the address.
            device_token=create_device_proof(email_key),
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
    # 5 per address per 15 minutes (owner, 2026-11-26: "15-minute window"; it was 5 an hour,
    # and the owner's own retries locked the address for an hour). The wait the app shows is
    # this window (`Retry-After`), so it now says "about 15 minutes".
    _enforce_credential_limits(
        (f"register:ip:{client_ip}", 10, REGISTER_EMAIL_WINDOW_SECONDS),
        (f"mail:ip:h:{client_ip}", MAIL_PER_IP_PER_HOUR, MAIL_HOUR_SECONDS),
        (f"register:email:{email_key}", 5, REGISTER_EMAIL_WINDOW_SECONDS),
        (f"confirm_mail:email:{email_key}", CONFIRM_MAIL_PER_ADDRESS_PER_HOUR, MAIL_HOUR_SECONDS),
        detail="Too many registration attempts. Please try again later.",
    )
    try:
        auth_response = await run_gotrue(
            _auth_of(auth_client, supabase).auth.sign_up,
            {
                "email": request.email,
                "password": request.password,
                "options": {
                    "data": {"display_name": request.display_name}
                },
            },
        )

        user = auth_response.user
        if not user:
            logger.error("Sign up returned no user object and raised nothing")
            raise HTTPException(status_code=400, detail="Registration failed")

        # NO post-signup `users.display_name` UPDATE any more (AUTH-BE-04). The signup trigger
        # (`handle_new_auth_user`) already copies `raw_user_meta_data->>'display_name'` into the
        # row it creates, so for a genuinely new account the UPDATE was redundant — and for an
        # address that already sat UNCONFIRMED, GoTrue returns that EXISTING user unchanged, so
        # the UPDATE let any unauthenticated caller overwrite the pending owner's profile name.

        # The confirmation gate. `email_confirmed_at` is set by Supabase only once the
        # address is verified; absent means "unconfirmed", so no tokens are issued.
        #
        # An address that already has a CONFIRMED account is told so plainly (owner decision
        # 2026-11-26: "if users use the same email, we should let them know right away").
        # GoTrue answers that sign-up with an OBFUSCATED user — a made-up id, no confirmation
        # time, an EMPTY identities list — and sends no email, so without this the person waited
        # for a code that never came. Only an empty LIST counts: a missing `identities` is
        # unknown, and falls through to the ordinary pending answer. A pending (never confirmed)
        # address is not "taken": GoTrue re-sends its email and the code screen opens.
        if not getattr(user, "email_confirmed_at", None):
            identities = getattr(user, "identities", None)
            # GoTrue also empties `identities` for an INVITED user it has just emailed; that
            # person has no password yet and needs the code screen, not "already in use".
            invited = getattr(user, "invited_at", None)
            if isinstance(identities, list) and not identities and not invited:
                # The made-up id is never logged as if it were a user.
                logger.info(
                    "Registration refused: the address already has a confirmed account "
                    "(Supabase sent no email)"
                )
                raise auth_error(
                    ErrorCode.AUTH_EMAIL_ALREADY_REGISTERED,
                    message="sign_up: the address already has a confirmed account",
                )
            logger.info("Registration pending email confirmation for user=%s", user.id)
            return SignUpResponse(
                confirmation_required=True,
                message=SIGN_UP_PENDING_MESSAGE,
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
        #
        # `_is_password_refused`, not only `_is_password_rejected`: GoTrue refuses a password
        # over 72 bytes with `validation_failed`, not `weak_password`, which used to fall to the
        # bare "Registration failed" below. Our own validator refuses it first now; this is the
        # backstop for a provider rule that drifts from ours.
        if _is_password_refused(e):
            logger.info(
                "Registration rejected: password refused by the provider (code=%s)", _gotrue_code(e),
            )
            raise auth_error(
                ErrorCode.AUTH_PASSWORD_REJECTED,
                message=f"sign_up rejected the password (code={_gotrue_code(e)})",
                user_message=_password_refused_copy(e),
                details=_optional_details(reasons=_password_rejection_reasons(e)),
            )

        # CLASSIFY the rest by GoTrue's typed `.code` / `.status` first (AUTH-BE-08, SB-6).
        # Every one of these used to become a bare 400 "Registration failed" logged at ERROR:
        # no reason for the user, no retry hint, and a Sentry event per user-caused failure.
        gt_code, gt_status = _gotrue_code(e), _gotrue_status(e)
        if gt_code == "email_address_invalid":
            # GoTrue refused the ADDRESS itself (a domain it will not mail). The user's to fix.
            logger.info("Registration rejected: the provider refused the email address")
            raise auth_error(
                ErrorCode.INVALID_INPUT,
                message="sign_up: email_address_invalid",
                user_message="That email address can't be used. Please check it and try again.",
                status_code=400,
            )
        if gt_code == "over_email_send_rate_limit" and _is_per_user_send_interval(e):
            # This address was mailed under a minute ago (GoTrue's per-user interval): a second
            # tap on Create Account, or a retry. That email's code is still good, so the TRUE
            # answer is the ordinary pending one — it used to be "couldn't send your
            # confirmation email", which sent people waiting for a mail that had already left
            # (seen 2026-11-26 in production).
            logger.info("Registration: the address was mailed under a minute ago; no new email")
            return SignUpResponse(confirmation_required=True, message=SIGN_UP_PENDING_MESSAGE)
        if gt_code == "over_email_send_rate_limit":
            # The confirmation email could not be SENT: the project's email budget is spent. Keyed on the CODE, never on a bare 429:
            # a per-IP `over_request_rate_limit` (shared Railway egress) must not be told
            # anything about an email. WARNING with the code so a spent budget is greppable;
            # never the address.
            logger.warning(
                "GOTRUE_EMAIL_RATE_LIMITED route=register status=%s code=%s",
                gt_status, gt_code,
            )
            raise auth_error(
                ErrorCode.AUTH_UNAVAILABLE,
                message="sign_up: over_email_send_rate_limit",
                user_message=_CONFIRMATION_SEND_FAILED,
            )
        kind = _classify_gotrue_failure(e)
        if kind == "rate_limited":
            # GoTrue's per-IP request limit. Every call leaves from the Railway egress, so a
            # trip here degrades sign-up for EVERYONE (SEC-7): ERROR, with the marker.
            logger.error(
                "GOTRUE_RATE_LIMITED route=register status=%s code=%s (%s)",
                gt_status, gt_code, type(e).__name__,
            )
            raise auth_error(
                ErrorCode.AUTH_UNAVAILABLE,
                message="sign_up: provider rate limit",
                user_message="We couldn't create your account right now. Please try again in a few minutes.",
            )
        if _is_gotrue_outage(e):
            # A GoTrue 5xx (SMTP "Error sending confirmation email" is a 500), a 502-504 or a
            # transport failure. GoTrue rolls the new user back when the send fails, so "try
            # again" is the true instruction. Untyped exceptions and 4xx configuration codes
            # (signup_disabled, …) fall through to the generic fallback below WITH a stack
            # trace: those need a person, not a retry.
            logger.warning(
                "Registration upstream failure (confirmation email not sent): %s status=%s code=%s",
                type(e).__name__, gt_status, gt_code,
            )
            raise auth_error(
                ErrorCode.AUTH_UNAVAILABLE,
                message=f"sign_up upstream failure: {type(e).__name__}",
                user_message=_CONFIRMATION_SEND_FAILED,
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
    # Decode FIRST and classify the decode failure on its own (AUTH-BE-14). Every lapsed
    # 7-day refresh used to land in the generic handler below as `logger.error` — a Sentry
    # event per user per week — and was answered AUTH_TOKEN_INVALID ("invalid") when the
    # truth is "expired". `decode_token` re-raises JWTError, and ExpiredSignatureError is a
    # JWTError subclass, so the order of these two clauses is load-bearing.
    try:
        payload = decode_token(request.refresh_token)
    except ExpiredSignatureError:
        logger.info("Refresh rejected: the refresh token has expired")
        raise auth_error(
            ErrorCode.AUTH_SESSION_EXPIRED,
            message="refresh token expired",
            # The stock AUTH_SESSION_EXPIRED copy talks about a PASSWORD change; that is not
            # what happened here.
            user_message="Your session has ended. Please sign in again.",
        )
    except JWTError as e:
        logger.warning("Refresh rejected: token could not be verified (%s)", type(e).__name__)
        raise auth_error(
            ErrorCode.AUTH_TOKEN_INVALID,
            message=f"refresh token could not be decoded: {type(e).__name__}",
        )
    except Exception as e:
        logger.error(
            "Refresh token decode failed unexpectedly: %s: %s", type(e).__name__, e,
            exc_info=True,
        )
        raise auth_error(
            ErrorCode.AUTH_TOKEN_INVALID,
            message=f"refresh token could not be decoded: {type(e).__name__}",
        )
    if not isinstance(payload, dict):
        logger.warning("Refresh rejected: decoder returned no payload")
        raise auth_error(
            ErrorCode.AUTH_TOKEN_INVALID, message="refresh token decoded to no payload",
        )

    try:
        if payload.get("type") != "refresh":
            # The enum comment for AUTH_TOKEN_INVALID names this case explicitly.
            raise auth_error(
                ErrorCode.AUTH_TOKEN_INVALID,
                message=f"token type is {payload.get('type')!r}, expected 'refresh'",
            )

        user_id = payload.get("sub")
        email = payload.get("email")
        if not user_id:
            raise auth_error(
                ErrorCode.AUTH_TOKEN_INVALID, message="refresh token carries no subject",
            )

        # The account must still exist. Without this, a token whose `public.users` row is
        # missing refreshes successfully forever while every real route 401s — see
        # `_app_user_row_exists`. AUTH_ACCOUNT_NOT_FOUND is one of the three codes iOS is
        # allowed to clear a credential on, so this ENDS the loop instead of feeding it.
        # FAIL CLOSED on a read error (SEC-4): a refresh MINTS, so "could not check" must
        # never mean "allowed" here — it is a retryable 503 instead.
        if not (await asyncio.to_thread(
            _app_user_row_exists, supabase, user_id, fail_closed=True,
        )):
            logger.warning("Refresh rejected: no public.users row for user=%s", user_id)
            raise auth_error(
                ErrorCode.AUTH_ACCOUNT_NOT_FOUND,
                message=f"no public.users row for {user_id}",
            )

        # A refresh token minted BEFORE the last password change must not mint new access
        # tokens — otherwise a reset doesn't evict a thief and they keep access for the
        # full 7-day refresh lifetime. This is the check that actually caps the window;
        # see migration 105. Fails closed too (raises `_IdentityStoreUnavailable`).
        if (await asyncio.to_thread(is_token_stale_after_password_change, payload, user_id, supabase)):
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
    except _IdentityStoreUnavailable:
        # Already logged at ERROR where it happened, with the user id.
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message="refresh: users read failed; refusing to mint (fail closed)",
        )
    except Exception as e:
        # Past the decode, so this is OURS (a token mint, a thread failure) — never the
        # caller's token. 503 keeps the client's credential; the stack goes to Sentry.
        logger.error(
            "Token refresh failed unexpectedly: %s: %s", type(e).__name__, e, exc_info=True,
        )
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message=f"refresh failed unexpectedly: {type(e).__name__}",
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
    and the address because it sends mail. (/register now says plainly when an address has a
    confirmed account — owner decision 2026-11-26 — so staying neutral HERE avoids a second,
    cheaper and unthrottled-by-409 way to ask the same question.)
    """
    client_ip = trusted_client_ip(req)
    email_key = request.email.strip().lower()

    # 3 per address and 10 per IP, per 15 minutes (owner, 2026-11-26; was 3 and 5 an hour).
    # The IP bucket stays LOOSER than the address bucket so one person's own retries are
    # refused by the address window, never by a longer IP one.
    _enforce_credential_limits(
        (f"resend:ip:{client_ip}", 10, MAIL_SEND_WINDOW_SECONDS),
        (f"mail:ip:h:{client_ip}", MAIL_PER_IP_PER_HOUR, MAIL_HOUR_SECONDS),
        (f"resend:email:{email_key}", 3, MAIL_SEND_WINDOW_SECONDS),
        (f"confirm_mail:email:{email_key}", CONFIRM_MAIL_PER_ADDRESS_PER_HOUR, MAIL_HOUR_SECONDS),
        detail="Too many requests. Please try again later.",
    )

    try:
        await run_gotrue(
            _auth_of(auth_client, supabase).auth.resend,
            {"type": "signup", "email": email_key},
        )
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


# Said once the signup code is SPENT (the address IS confirmed) but we could not finish. Every
# one travels as AUTH_CONFIRMED_SIGN_IN_FAILED (action sign_in) and opens "Your email is
# confirmed": "try again" would re-submit a dead code and read "invalid".
#: The sign-in after the confirm failed (or resolved another account): sign in.
_CONFIRMED_SIGN_IN_FAILED = (
    "Your email is confirmed, but we couldn't sign you in just now. Please sign in."
)
#: The stored password DIFFERED and the replacement was refused (our rule or GoTrue's).
_CONFIRMED_PASSWORD_REFUSED = (
    "Your email is confirmed, but that password can't be used. "
    "Use Forgot password to choose a new one."
)
#: The password could not be written (or, after an upstream sign-in failure, it is not known
#: whether the typed password is the stored one): sign in, or recover it.
_CONFIRMED_FINISH_FAILED = (
    "Your email is confirmed, but we couldn't finish signing you in. "
    "Please sign in, or use Forgot password if your password doesn't work."
)
#: Confirmed, but the app row the trigger should have made is missing (see `_app_user_row_exists`).
_CONFIRMED_ACCOUNT_NOT_READY = (
    "Your email is confirmed, but your account isn't ready yet. Please sign in again in a moment."
)


def _confirmed_but_failed(
    user_message: str, *, message: str, status_code: int = 503,
    details: Optional[dict] = None,
) -> HTTPException:
    """The one shape of every `/auth/confirm-email` failure AFTER `verify_otp` succeeded."""
    return auth_error(
        ErrorCode.AUTH_CONFIRMED_SIGN_IN_FAILED,
        message=message, user_message=user_message, status_code=status_code, details=details,
    )


@router.post("/confirm-email", response_model=TokenResponse)
async def confirm_email(
    request: ConfirmEmailRequest,
    req: Request,
    supabase: Client = Depends(get_supabase),
    auth_client: Client = Depends(get_auth_client),
    admin_client: Client = Depends(get_admin_client),
):
    """Confirm a password signup with the emailed code, then sign the caller in.

    THE MISSING HALF OF SIGNUP (AUTH-BE-01/02, TG-1). The Confirm-signup email carries an
    8-digit `{{ .Token }}`, and until this route nothing could redeem it: the only
    `verify_otp` calls were `type="recovery"`, so every new email/password account was stuck
    at EMAIL_NOT_CONFIRMED.

    Steps, in an order that each matters:
      1. Per-IP, then per-email limiter — the plain email key, deliberately NO device-proof
         relaxation (`_per_email_check`): the per-email bucket is what bounds guessing a code
         for one address, and a proof proves a past PASSWORD, not this mailbox.
      2. `verify_otp(type="signup")` — NEVER "email": GoTrue's "email" type also accepts a
         RECOVERY token, which would turn any reset code into a passwordless sign-in. Never
         "recovery" either. On the isolated auth client through `run_gotrue`.
      3. `sign_in_with_password` with the password the user typed. Confirming the mailbox
         alone must NOT sign anyone in (SEC-6): GoTrue keeps the FIRST registrant's password
         on an unconfirmed address, so "verify and mint" would hand a squatter's pre-registered
         account to the mailbox owner with the squatter's password still on it.
      4. If the stored password differs — the user re-registered with a new one (CC-03), or a
         squatter registered first (SEC-6) — OR the sign-in in step 3 failed UPSTREAM (a GoTrue
         429 / outage, BE-AUTH-03), whoever just proved the mailbox SETS it: the password is
         checked against our rule, written on the ADMIN client, and `password_changed_at` is
         stamped. That is the capability `/reset-password` already grants a mailbox owner;
         nothing new is exposed. The squatter's password stops working. Never after a policy
         answer ("Email not confirmed", a typed 4xx such as `email_provider_disabled`).
      5. Mint app tokens — after `_app_user_row_exists`, and WITHOUT a device proof: an emailed
         code is not a verified credential in auth.md §3a's sense (the proof stays minted only
         by password sign-in, provider sign-in and session exchange).

    Once step 2 succeeds the code is SPENT and the address IS confirmed, so every failure after
    it is AUTH_CONFIRMED_SIGN_IN_FAILED (action `sign_in`, copy opening "Your email is
    confirmed"): 400 when a differing password was refused, else 503. Never AUTH_UNAVAILABLE —
    that one means "your code is still good, try again", and iOS must be able to tell the two
    apart (BE-AUTH-04). The three verify ANOMALIES (no user, another address, still
    unconfirmed) stay AUTH_CODE_INVALID: nothing proves the address is confirmed, so the copy
    could not truthfully say it is.
    """
    client_ip = trusted_client_ip(req)
    email_key = request.email.strip().lower()

    _enforce_credential_limits(
        (f"confirm:ip:{client_ip}", 20, 3600),
        (f"confirm:email:{email_key}", 10, 3600),
        detail="Too many attempts. Please wait a while, then try again.",
    )

    # 2. Verify the signup code — this is what confirms the address.
    try:
        verified = await run_gotrue(
            _auth_of(auth_client, supabase).auth.verify_otp,
            {"email": email_key, "token": request.code, "type": "signup"},
        )
    except Exception as e:
        raise _otp_failure(e, route="confirm-email")

    user = getattr(verified, "user", None)
    user_id = str(getattr(user, "id", "") or "")
    if not user_id:
        logger.warning("confirm-email: verify_otp returned no user — treating the code as refused")
        raise auth_error(
            ErrorCode.AUTH_CODE_INVALID, message="confirm-email: verify_otp returned no user",
        )
    # Belt and braces: the code was verified FOR this address, so the user GoTrue returns must
    # be this address, now confirmed. Either one false is an upstream anomaly, and the cost of
    # trusting it is a session on the wrong account — refuse, loudly.
    verified_email = str(getattr(user, "email", "") or "").strip().lower()
    if verified_email != email_key:
        logger.error(
            "confirm-email: verified user=%s does not carry the requested address — refusing",
            user_id,
        )
        raise auth_error(
            ErrorCode.AUTH_CODE_INVALID,
            message="confirm-email: verified user's email does not match the request",
        )
    if not getattr(user, "email_confirmed_at", None):
        logger.error(
            "confirm-email: verify_otp succeeded but user=%s is still unconfirmed — refusing",
            user_id,
        )
        raise auth_error(
            ErrorCode.AUTH_CODE_INVALID,
            message="confirm-email: user still unconfirmed after a successful verify",
        )
    logger.info("confirm-email: address confirmed for user=%s", user_id)

    # 3. Prove the password. `run_gotrue` re-asserts service_role on the auth client inside the
    #    lock, so the SIGNED_IN that verify_otp just emitted cannot leak into this call.
    #
    #    Three outcomes lead on to step 4 (the mailbox owner SETS the typed password):
    #      * the stored password DIFFERS (a refused credential) — CC-03 / SEC-6;
    #      * GoTrue was RATE-LIMITED or DOWN (BE-AUTH-03). The code is already spent, so
    #        refusing here used to leave a squatter's or stale password live on a now-confirmed
    #        account. The caller proved the mailbox, so writing their password is the
    #        capability `/reset-password` grants anyway, and harmless when it already matched.
    #      Everything else — "Email not confirmed", a typed 4xx policy answer
    #      (`email_provider_disabled`, a banned user …), a sign-in resolving ANOTHER account —
    #      is an answer about this account, and we never mint past one: sign in instead.
    password_matches = False
    probe_failed_upstream = False
    try:
        signed_in = await run_gotrue(
            _auth_of(auth_client, supabase).auth.sign_in_with_password,
            {"email": email_key, "password": request.password},
        )
        signed_in_id = str(getattr(getattr(signed_in, "user", None), "id", "") or "")
        if signed_in_id != user_id:
            logger.error(
                "confirm-email: sign-in resolved user=%s but the code confirmed user=%s",
                signed_in_id or "<none>", user_id,
            )
            raise _confirmed_but_failed(
                _CONFIRMED_SIGN_IN_FAILED,
                message="confirm-email: sign-in resolved a different user",
            )
        password_matches = True
    except HTTPException:
        raise
    except Exception as e:
        if _is_rejected_credential(e):
            logger.info(
                "confirm-email: stored password differs for user=%s — the mailbox owner sets it",
                user_id,
            )
        elif not _is_email_not_confirmed(e) and _is_upstream_failure(e):
            probe_failed_upstream = True
            if _classify_gotrue_failure(e) == "rate_limited":
                logger.error(
                    "GOTRUE_RATE_LIMITED route=confirm-email verb=sign_in_with_password "
                    "user=%s status=%s code=%s — setting the typed password instead",
                    user_id, _gotrue_status(e), _gotrue_code(e),
                )
            else:
                logger.error(
                    "confirm-email: sign-in after a successful confirm failed upstream for "
                    "user=%s: %s: %s (status=%s code=%s) — setting the typed password instead",
                    user_id, type(e).__name__, e, _gotrue_status(e), _gotrue_code(e),
                )
        else:
            logger.error(
                "confirm-email: sign-in after a successful confirm was refused for user=%s: "
                "%s: %s (status=%s code=%s)",
                user_id, type(e).__name__, e, _gotrue_status(e), _gotrue_code(e),
            )
            raise _confirmed_but_failed(
                _CONFIRMED_SIGN_IN_FAILED,
                message=f"confirm-email: sign-in refused after confirm: {type(e).__name__} "
                        f"(code={_gotrue_code(e)})",
            )

    # 4. The caller who just proved the mailbox sets the password they typed.
    if not password_matches:
        # After a DIFFERING password a refusal is the password's fault (400, "that password
        # can't be used"). After an upstream sign-in failure we never learned whether the typed
        # password is the stored one — it may be a perfectly good legacy password the rule
        # cannot WRITE — so every failure says "sign in, or use Forgot password" (503: the root
        # cause is ours).
        if probe_failed_upstream:
            refused_copy, refused_status = _CONFIRMED_FINISH_FAILED, 503
        else:
            refused_copy, refused_status = _CONFIRMED_PASSWORD_REFUSED, 400
        try:
            _validate_password_strength(request.password)
        except ValueError as ve:
            logger.info(
                "confirm-email: the typed password fails the strength rule for user=%s "
                "(after an upstream sign-in failure: %s)", user_id, probe_failed_upstream,
            )
            raise _confirmed_but_failed(
                refused_copy, status_code=refused_status,
                message=f"confirm-email: replacement password refused: {ve}",
            )
        # ADMIN client, mandatorily: verify_otp and the sign-in attempt above both ran on the
        # auth client, and `auth.admin.*` must never run where a sign-in can rewrite the header.
        try:
            await run_gotrue(
                resolve_admin_client(admin_client, auth_client, supabase).auth.admin.update_user_by_id,
                user_id, {"password": request.password},
            )
        except Exception as e:
            if _is_password_refused(e):
                logger.info(
                    "confirm-email: replacement password refused by the provider for user=%s "
                    "(code=%s)", user_id, _gotrue_code(e),
                )
                raise _confirmed_but_failed(
                    refused_copy, status_code=refused_status,
                    message=f"confirm-email: admin.update_user_by_id rejected the password "
                            f"(code={_gotrue_code(e)})",
                    details=_optional_details(reasons=_password_rejection_reasons(e)),
                )
            logger.error(
                "confirm-email: setting the password failed for user=%s: %s: %s "
                "(status=%s code=%s)",
                user_id, type(e).__name__, e, _gotrue_status(e), _gotrue_code(e),
            )
            raise _confirmed_but_failed(
                _CONFIRMED_FINISH_FAILED,
                message=f"confirm-email: admin.update_user_by_id failed: {type(e).__name__}",
            )
        # Evict anything minted under the old password, BEFORE minting ours (iat >= stamp).
        (await asyncio.to_thread(_mark_password_changed, supabase, user_id))
        auth_methods_service.invalidate(user_id)
        logger.info(
            "confirm-email: password set by the mailbox owner for user=%s (after an upstream "
            "sign-in failure: %s)", user_id, probe_failed_upstream,
        )

    # 5. Mint — only for an id with an app row (see `_app_user_row_exists`).
    if not (await asyncio.to_thread(_app_user_row_exists, supabase, user_id)):
        logger.error(
            "confirm-email: no public.users row for confirmed user=%s — refusing to mint "
            "(check the handle_new_auth_user trigger for this account)", user_id,
        )
        raise _confirmed_but_failed(
            _CONFIRMED_ACCOUNT_NOT_READY,
            message=f"confirm-email: no public.users row for {user_id}",
        )

    logger.info("confirm-email: signed in user=%s", user_id)
    return _issue_app_tokens_for(
        user_id, getattr(user, "email", None) or email_key, device_proof=False,
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
    """True when the provider rejected the supplied password, as opposed to failing.

    gotrue's typed `.code == "invalid_credentials"` first (API version 2024-01-01 sends it),
    the wording markers as the fallback for an untyped or older error shape."""
    if _gotrue_code(exc) == "invalid_credentials":
        return True
    return any(marker in str(exc).lower() for marker in _REJECTED_CREDENTIAL_MARKERS)


# Last-resort wording match for a rejected NEW password. Only reached when neither typed
# signal below fires — see `_is_password_rejected`.
_WEAK_PASSWORD_MARKERS = (
    "weak_password",
    "password is known to be weak",
    "password should be at least",
    "pwned",
)


def _weak_password_error_types() -> tuple:
    """`AuthWeakPasswordError` from the SDK production actually runs, then any successor.

    supabase 2.16 builds its auth client from **gotrue** (2.12), not `supabase_auth` — the
    latter is a stray local install that no requirement pins, so the old
    `from supabase_auth.errors import ...` checked a class production never raises and would
    simply be absent on Railway (AUTH-BE-13). gotrue first; supabase_auth kept only so a future
    SDK move keeps working. Each import is independent: one missing never hides the other.
    """
    found = []
    for module in ("gotrue.errors", "supabase_auth.errors"):
        try:
            mod = __import__(module, fromlist=["AuthWeakPasswordError"])
            cls = getattr(mod, "AuthWeakPasswordError", None)
            if isinstance(cls, type):
                found.append(cls)
        except Exception as e:  # ImportError, or an SDK that fails on import
            logger.debug("weak-password class not importable from %s (%s)", module, type(e).__name__)
    return tuple(found)


_WEAK_PASSWORD_ERROR_TYPES = _weak_password_error_types()


def _is_password_rejected(exc: Exception) -> bool:
    """True when the provider refused to SET the supplied password.

    Covers both halves of GoTrue's single `weak_password` code: fails the project's strength
    rules, and (once leaked-password protection is on) found in a breach corpus. The user's
    action is the same for both — choose a different password — so they share one code.

    THREE LAYERS, TYPED FIRST, and the order is deliberate:

      1. `AuthWeakPasswordError` — gotrue's class (supabase_auth's as a fallback), carrying
         `.reasons`. gotrue's shared `helpers.handle_exception` builds it for ANY response whose
         code is `weak_password` — the user calls (`sign_up`) AND the admin call
         (`admin.update_user_by_id`) alike.
      2. `.code == "weak_password"` — any other AuthError shape carrying the same string (an
         SDK that stops building the subclass). Without it, a future SDK change would leave the
         change/reset/set-password halves vacuously unhandled while signup looked fine.
      3. Wording match, mirroring `_REJECTED_CREDENTIAL_MARKERS`.

    Layer 3 alone would not be safe to rely on: an exception whose `str()` is empty defeats
    every marker list, which is exactly how a Gemini timeout classifier failed in this repo.

    ⚠️ gotrue's `ErrorCode` is a `Literal` type alias, NOT an enum with members.
    Compare the string; `ErrorCode.weak_password` does not exist.
    """
    if _WEAK_PASSWORD_ERROR_TYPES and isinstance(exc, _WEAK_PASSWORD_ERROR_TYPES):
        return True

    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.strip().lower() == "weak_password":
        return True

    return any(marker in str(exc).lower() for marker in _WEAK_PASSWORD_MARKERS)


def _is_password_validation_failure(exc: BaseException) -> bool:
    """GoTrue refused the PASSWORD's shape with `validation_failed` rather than `weak_password`.

    The one known case: over 72 bytes → 400 `validation_failed`, "Password cannot be longer than
    72 characters" (verified against the live stack). `_validate_password_strength` refuses that
    first now, so this is the backstop for a provider rule that drifts from ours. Typed code AND
    the word "password" — `validation_failed` also covers unrelated fields (an email format), and
    `_GOTRUE_CODE_REJECTED_CODES` uses it for a refused one-time code."""
    return _gotrue_code(exc) == "validation_failed" and "password" in str(exc).lower()


def _is_password_refused(exc: BaseException) -> bool:
    """Either way GoTrue can refuse a NEW password: weak/pwned, or a password-shaped
    `validation_failed` (too long). The user's action is the same — choose a different one."""
    return _is_password_rejected(exc) or _is_password_validation_failure(exc)


def _password_refused_copy(exc: BaseException) -> Optional[str]:
    """The too-long sentence when GoTrue's refusal was the 72-byte cap; else None, which keeps
    AUTH_PASSWORD_REJECTED's stock copy (the provider never says WHICH rule a weak one broke)."""
    if _is_password_validation_failure(exc) and "longer than" in str(exc).lower():
        return PASSWORD_TOO_LONG_MESSAGE
    return None


def _is_email_not_confirmed(exc: BaseException) -> bool:
    """GoTrue's "Email not confirmed" sign-in refusal: the typed code, or `/login`'s wording
    match for an untyped shape."""
    return _gotrue_code(exc) == "email_not_confirmed" or "not confirmed" in str(exc).lower()


def _is_upstream_failure(exc: BaseException) -> bool:
    """GoTrue was rate-limited or DOWN — it said nothing about this account.

    True for: a 429 / rate-limit code; `AuthRetryableError` / `AuthUnknownError` (transport,
    502-504, an unparseable edge page); any 5xx; an UNTYPED exception the classifier calls
    unavailable (no status, no code — e.g. a transport error the SDK did not wrap).

    False for every typed 4xx, INCLUDING the ones `_classify_gotrue_failure` files as
    "unavailable" because they are our configuration (`email_provider_disabled`,
    `signup_disabled`, `bad_jwt`, …): those are a policy ANSWER, and `/auth/confirm-email`
    must never mint past one (BE-AUTH-03 caveat). False for a refused credential and for
    "Email not confirmed" too — callers test those first."""
    kind = _classify_gotrue_failure(exc)
    if kind == "rate_limited":
        return True
    if kind != "unavailable":
        return False
    names = _mro_names(exc)
    if "AuthRetryableError" in names or "AuthUnknownError" in names:
        return True
    status_code = _gotrue_status(exc)
    if status_code is not None:
        return status_code >= 500
    return _gotrue_code(exc) is None


# ── GoTrue failure classification ─────────────────────────────────────────────
#
# gotrue's `handle_exception` turns EVERY failure of a verb into a typed AuthError:
#   * transport errors and HTTP 502/503/504 → `AuthRetryableError` (status 0 or the 5xx);
#   * an error body it cannot parse (e.g. an edge 520 HTML page) → `AuthUnknownError`;
#   * anything else → `AuthApiError` / a subclass with `.status` and `.code`.
# That signal was there and ignored: reset- and set-password laundered all of it into
# "That code is invalid or has expired" (AUTH-BE-05), and sign-up into "Registration failed".
# Matched by class NAME across the MRO so gotrue and a future supabase_auth both count.

#: GoTrue's rate-limit codes. Matched with status 429 — either is enough.
_GOTRUE_RATE_LIMIT_CODES = frozenset({
    "over_request_rate_limit", "over_email_send_rate_limit", "over_sms_send_rate_limit",
})
#: A one-time code the PROVIDER refused as wrong / expired / used — the user's half.
_GOTRUE_CODE_REJECTED_CODES = frozenset({
    "otp_expired", "validation_failed", "user_not_found",
})
#: 4xx codes that mean OUR configuration or credentials are broken, not the user's input.
_GOTRUE_OURS_4XX_CODES = frozenset({
    "bad_jwt", "not_admin", "no_authorization", "otp_disabled", "email_provider_disabled",
    "request_timeout", "hook_timeout", "hook_timeout_after_retry", "unexpected_failure",
    "signup_disabled", "email_address_not_authorized",
})
#: Wording fallback for an UNTYPED exception only (a test fake, a future SDK shape).
_CODE_REJECTED_MARKERS = ("expired", "invalid")


def _mro_names(exc: BaseException) -> set:
    return {c.__name__ for c in type(exc).__mro__}


def _is_gotrue_outage(exc: BaseException) -> bool:
    """A typed GoTrue failure that is the provider being DOWN: transport / 502-504
    (`AuthRetryableError`), an unparseable error page (`AuthUnknownError`), or any 5xx."""
    names = _mro_names(exc)
    if "AuthRetryableError" in names or "AuthUnknownError" in names:
        return True
    status_code = _gotrue_status(exc)
    return "AuthError" in names and status_code is not None and status_code >= 500


def _gotrue_code(exc: BaseException) -> Optional[str]:
    code = getattr(exc, "code", None)
    return code.strip().lower() if isinstance(code, str) and code.strip() else None


def _gotrue_status(exc: BaseException) -> Optional[int]:
    status_code = getattr(exc, "status", None)
    return status_code if isinstance(status_code, int) and not isinstance(status_code, bool) else None


def _is_per_user_send_interval(exc: Exception) -> bool:
    """GoTrue's PER-ADDRESS send interval ("For security purposes, you can only request this
    after N seconds"), as opposed to the PROJECT's spent email budget ("email rate limit
    exceeded"). Both arrive as 429 `over_email_send_rate_limit`; only the message differs."""
    text = f"{getattr(exc, 'message', '')} {exc}".lower()
    return "only request this after" in text


def _classify_gotrue_failure(exc: BaseException) -> str:
    """'rate_limited' | 'unavailable' | 'rejected' for an exception a GoTrue verb raised.

    Typed signals first, in this order; wording only for an exception with no type signal:
      * AuthRetryableError / AuthUnknownError            → unavailable
      * status 429 or a rate-limit code                  → rate_limited
      * status ≥ 500                                     → unavailable
      * a code naming a refused one-time code            → rejected
      * a code naming OUR misconfiguration (bad_jwt, …)  → unavailable
      * any other 4xx                                    → rejected (about the submitted value)
      * untyped: "expired"/"invalid" in the message      → rejected, else unavailable
    """
    names = _mro_names(exc)
    if "AuthRetryableError" in names or "AuthUnknownError" in names:
        return "unavailable"
    code, status_code = _gotrue_code(exc), _gotrue_status(exc)
    if status_code == 429 or code in _GOTRUE_RATE_LIMIT_CODES:
        return "rate_limited"
    if status_code is not None and status_code >= 500:
        return "unavailable"
    if code in _GOTRUE_CODE_REJECTED_CODES:
        return "rejected"
    if code in _GOTRUE_OURS_4XX_CODES:
        return "unavailable"
    if status_code is not None and 400 <= status_code < 500:
        return "rejected"
    if any(marker in str(exc).lower() for marker in _CODE_REJECTED_MARKERS):
        return "rejected"
    return "unavailable"


def _otp_failure(
    exc: BaseException, *, route: str, user_id: Optional[str] = None,
    unavailable_message: Optional[str] = None,
) -> HTTPException:
    """The HTTPException for a failed `verify_otp`, classified — and logged — once, here.

    The user's half (a wrong, expired or used code) is 400 AUTH_CODE_INVALID at INFO: a typo
    is not an incident, and an ERROR would be a Sentry event per typo. Ours (outage, 5xx,
    429) is 503 AUTH_UNAVAILABLE at ERROR — the code is usually still good, so the user is told
    to try again rather than to burn the resend budget on a new one. A 429 carries the
    greppable GOTRUE_RATE_LIMITED marker (every call leaves from the one Railway egress, so it
    is everyone's problem). Never the email, never the code.
    """
    kind = _classify_gotrue_failure(exc)
    code, status_code = _gotrue_code(exc), _gotrue_status(exc)
    who = f" user={user_id}" if user_id else ""
    if kind == "rejected":
        level = logging.INFO if (code in _GOTRUE_CODE_REJECTED_CODES or code is None) else logging.WARNING
        logger.log(
            level, "%s: one-time code refused%s (%s status=%s code=%s)",
            route, who, type(exc).__name__, status_code, code,
        )
        return auth_error(
            ErrorCode.AUTH_CODE_INVALID,
            message=f"{route}: verify_otp refused the code (code={code})",
        )
    if kind == "rate_limited":
        logger.error(
            "GOTRUE_RATE_LIMITED route=%s verb=verify_otp%s status=%s code=%s",
            route, who, status_code, code,
        )
    else:
        logger.error(
            "%s: verify_otp failed upstream%s: %s: %s (status=%s code=%s)",
            route, who, type(exc).__name__, exc, status_code, code,
        )
    return auth_error(
        ErrorCode.AUTH_UNAVAILABLE,
        message=f"{route}: verify_otp upstream failure ({kind}): {type(exc).__name__}",
        user_message=unavailable_message
        or "We couldn't check your code just now. Please try again in a moment.",
    )


_CONFIRMATION_SEND_FAILED = (
    "We couldn't send your confirmation email right now. Please try again in a few minutes."
)


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
) -> None:
    """Evaluate credential limiters IN ORDER, raising 429 on the FIRST denial.

    `Retry-After` is the WINDOW OF THE CHECK THAT DENIED (IOS-AUTH-04), not one number per
    route. Login used to send "60" for both its 60-second per-IP bucket and its 15-minute
    per-email bucket, so a user locked out per-email waited the minute they were told to and
    was refused again; register sent "60" for a one-hour bucket. The limiter is a sliding
    window, so the window is an honest upper bound on the wait.

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
                status_code=429, detail=detail,
                headers={"Retry-After": str(int(window_seconds))},
            )


def _per_email_check(
    bucket: str, email_key: str, max_requests: int, window_seconds: int, req: Request,
) -> tuple[str, int, int]:
    """The per-EMAIL limiter for a credential route, or its per-PROOF stand-in.

    A request that carries `X-Device-Token` proving a PRIOR verified sign-in for exactly
    this email (`core.security.verify_device_proof`) skips the per-email bucket — that is
    the bucket an attacker fills with wrong passwords to lock a known address out — and is
    bounded by a bucket keyed on the proof instead, at the same rate. Everyone still passes
    the per-IP bucket first. A missing, foreign or forged proof changes nothing: the
    ordinary per-email bucket applies, so the proof can only relax a limit, never fail a
    request. Only a hash of the proof becomes a key, never the proof itself.
    """
    proof = req.headers.get("x-device-token") if req is not None and getattr(req, "headers", None) else None
    if proof and verify_device_proof(proof, email_key):
        import hashlib
        digest = hashlib.sha256(proof.encode("utf-8")).hexdigest()[:24]
        return (f"{bucket}:device:{digest}", max_requests, window_seconds)
    return (f"{bucket}:email:{email_key}", max_requests, window_seconds)


def _app_user_row_exists(supabase: Client, user_id: str, *, fail_closed: bool = False) -> bool:
    """Does this id have a `public.users` row?

    `fail_closed=True` (the `/auth/refresh` path, SEC-4) raises `_IdentityStoreUnavailable` on a
    read error instead of answering True: a refresh MINTS, so "could not check" must not mean
    "exists". The sign-in paths keep failing open — there a blip would fail a user who just
    proved a credential, and the check only guards the missing-row loop, not an eviction.

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
        if fail_closed:
            logger.error(
                "users existence check unavailable for user=%s: %s: %s — refusing to mint "
                "(fail closed)", user_id, type(e).__name__, e,
            )
            raise _IdentityStoreUnavailable(f"{type(e).__name__}: {e}") from e
        # Fail OPEN on a transport error on the sign-in paths. Only a definitive "no row" is
        # treated as a missing account there.
        logger.warning(
            "users existence check unavailable for user=%s: %s: %s — allowing",
            user_id, type(e).__name__, e,
        )
        return True


def _issue_app_tokens_for(
    user_id: str, email: Optional[str], *, device_proof: bool = True,
) -> TokenResponse:
    """Mint the app's own JWTs for an authenticated Supabase user.

    A provider sign-in, a session exchange and a password change all VERIFIED a credential, so
    they earn the device proof (auth.md §3a). `/auth/confirm-email` passes
    `device_proof=False`: an emailed code proves the mailbox, not a password, and §3a mints the
    lockout exemption only after a credential was verified."""
    access_token = create_access_token(data={"sub": user_id, "email": email})
    refresh_token = create_refresh_token(data={"sub": user_id, "email": email})
    return TokenResponse(
        access_token=access_token, refresh_token=refresh_token, user_id=user_id,
        device_token=create_device_proof(email) if (email and device_proof) else None,
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
            supabase.table("users").select("display_name, email")
            .eq("id", user_id).single().execute()
        )
        data = row.data or {}
        current = (data.get("display_name") or "").strip()
        # The signup trigger never leaves this NULL: it pre-fills the email's local part
        # (`split_part(NEW.email, '@', 1)`), so on an Apple sign-in with Hide My Email the
        # row already read "xk3n8d2q" and the name Apple sent ONCE was thrown away —
        # Profile, Settings and every greeting showed the relay local part forever. A
        # placeholder equal to the row's own email local part is "unset", not "chosen".
        local_part = (data.get("email") or "").split("@")[0].strip()
        if current and current.casefold() != local_part.casefold():
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


# ── SOC-08: a provider sign-in that confirmed an account somebody registered earlier ──────────
#
# Verified in GoTrue's source (supabase/auth master, 2026-11-26): an id_token sign-in whose
# VERIFIED email matches an existing email/password user is LINKED to that user
# (`DetermineAccountLinking` → LinkAccount, regardless of confirmation). When that user was never
# confirmed, `createAccountFromExternalIdentity` then calls `RemoveUnconfirmedIdentities` — which
# sets `encrypted_password` to NULL, deletes the email identity and OVERWRITES user_metadata with
# the provider's identity data — and `Confirm()`s the address. That is GoTrue's pre-account-
# takeover guard and it is right: whoever registered that address never proved the mailbox, and
# might not be its owner. Two consequences reach us:
#
#   (a) the earlier password is gone. A person who registered, never confirmed, and then used
#       Google/Apple finds email sign-in refused later. Not reversible and not ours to undo; the
#       account shows "Set a Password" (no password), and Forgot Password works. Logged below.
#   (b) `public.users.display_name` is NOT touched by GoTrue — our signup trigger copied it from
#       the unverified registration at INSERT, and no trigger syncs later changes. So a squatter
#       who registered the victim's address first chose the name the victim's account then wore.
#       Fixed here: the name is reset to the provider's (or the placeholder).
#
# GoTrue's response gives no "just linked" flag, so the case is recognised by three signals that
# must ALL hold, each ruling out a legitimate shape:
#   * `email_confirmed_at` is from THIS request (within the window below of our clock) — rules
#     out every account confirmed earlier, by email or by an earlier provider sign-in;
#   * the account is older than that confirmation by more than a few seconds — rules out a
#     brand-new provider account (created and confirmed in one transaction);
#   * `user_metadata` carries no `display_name` — our register always writes it there, and GoTrue
#     MERGES metadata when it links to a confirmed user but REPLACES it when it links to an
#     unconfirmed one. This rules out an account confirmed by its emailed code a minute ago and
#     then linked to Google, whose own chosen name must survive.
# Any signal unreadable → no reset (the name is cosmetic; a wrong reset is the worse error).

#: How far from our clock `email_confirmed_at` may be and still be THIS request's confirmation.
_LINK_CONFIRMED_WITHIN_SECONDS = 120
#: Clock skew tolerated the other way (Supabase's clock ahead of Railway's).
_LINK_CONFIRMED_SKEW_SECONDS = 30
#: An account must predate that confirmation by more than this to be an EARLIER registration.
_LINK_ACCOUNT_OLDER_BY_SECONDS = 10


def _as_utc(value: Any) -> Optional[datetime]:
    """A GoTrue timestamp (datetime or ISO-8601 string, possibly with `Z`) as aware UTC, or None."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _confirmed_an_earlier_unconfirmed_account(user: Any, *, now: Optional[datetime] = None) -> bool:
    """True when this provider sign-in is the one that LINKED to and CONFIRMED an account someone
    registered earlier without confirming it (see the block comment above)."""
    confirmed = _as_utc(getattr(user, "email_confirmed_at", None))
    created = _as_utc(getattr(user, "created_at", None))
    metadata = getattr(user, "user_metadata", None)
    if confirmed is None or created is None or not isinstance(metadata, dict):
        return False
    age = ((now or datetime.now(timezone.utc)) - confirmed).total_seconds()
    if not (-_LINK_CONFIRMED_SKEW_SECONDS <= age <= _LINK_CONFIRMED_WITHIN_SECONDS):
        return False
    if (confirmed - created).total_seconds() <= _LINK_ACCOUNT_OLDER_BY_SECONDS:
        return False
    return "display_name" not in metadata


def _provider_name_for_reset(display_name: Optional[str], metadata: Any) -> Optional[str]:
    """The name to give an account whose earlier name came from an unverified registration:
    the one the app sent, else the provider's own (GoTrue just wrote the identity data into
    user_metadata), capped like every other writer of the column."""
    from app.schemas.user import DISPLAY_NAME_MAX_LENGTH

    candidates = [display_name]
    if isinstance(metadata, dict):
        candidates += [metadata.get("full_name"), metadata.get("name")]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()[:DISPLAY_NAME_MAX_LENGTH]
    return None


def _reset_name_from_unverified_registration(
    supabase: Client, user_id: str, name: Optional[str], email: Optional[str],
) -> bool:
    """Replace the display name an unverified registration chose. Never raises (cosmetic).

    Falls back to the email's local part — exactly the signup trigger's placeholder, which
    `_ensure_display_name` treats as "unset", so a later Apple first-time name can still fill it."""
    value = name or ((email or "").split("@")[0].strip() or None)
    if not value:
        return False
    try:
        supabase.table("users").update({"display_name": value}).eq("id", user_id).execute()
        return True
    except Exception as e:
        logger.warning(
            "OAuth: could not reset the display name an unverified registration chose for user=%s "
            "(%s: %s)", user_id, type(e).__name__, e,
        )
        return False


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
        result = await run_gotrue(
            _auth_of(auth_client, supabase).auth.sign_in_with_id_token, credentials,
        )
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
    if not (await asyncio.to_thread(_app_user_row_exists, supabase, user_id)):
        logger.error(
            "OAuth: no public.users row for verified provider=%s user=%s — refusing to mint "
            "tokens (check the handle_new_auth_user trigger for this account)",
            request.provider, user_id,
        )
        # 503 AUTH_UNAVAILABLE with session-exchange's copy, not 401 AUTH_ACCOUNT_NOT_FOUND
        # (SOC-12): that told someone who was IN THE MIDDLE of signing in to "sign in again",
        # and it is one of the codes iOS may clear a credential on. No token was issued here,
        # so there is nothing to clear; the condition is ours (a trigger failure), not theirs.
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message=f"oauth: no public.users row for verified user {user_id}",
            user_message="Your account isn't ready yet. Please try again.",
        )

    # SOC-08: this sign-in confirmed an account someone registered earlier and never confirmed.
    # GoTrue already removed that registration's password and email identity; the name it chose
    # is ours to drop (see the block comment above `_confirmed_an_earlier_unconfirmed_account`).
    if _confirmed_an_earlier_unconfirmed_account(user):
        reset = await asyncio.to_thread(
            _reset_name_from_unverified_registration, supabase, user_id,
            _provider_name_for_reset(request.display_name, getattr(user, "user_metadata", None)),
            getattr(user, "email", None),
        )
        logger.warning(
            "OAuth: provider=%s sign-in confirmed user=%s, an account registered earlier and never "
            "confirmed — GoTrue removed its unconfirmed password and email identity (pre-account-"
            "takeover guard); the display name its registration chose was %s",
            request.provider, user_id, "reset" if reset else "NOT reset (write failed)",
        )

    (await asyncio.to_thread(_ensure_display_name, supabase, user_id, request.display_name))

    # Sign in with Apple: keep a token account deletion can REVOKE (SOC-04). The code is
    # single-use and lives ~5 minutes, so it is exchanged here or never. Best-effort and
    # bounded — the service never raises, and a failure only means this grant cannot be revoked
    # later. AFTER the row check, so only a real account's grant is stored. Google's grant is
    # revoked on-device (GIDSignIn `disconnect`); a code sent with provider "google" is ignored.
    if request.provider == "apple" and request.authorization_code:
        try:
            apple_grant = await apple_sign_in_service.store_grant_from_authorization_code(
                supabase, user_id=user_id, authorization_code=request.authorization_code,
                identity_token=request.id_token,
            )
            logger.info("OAuth: Apple grant for user=%s: %s", user_id, apple_grant)
        except Exception as e:  # noqa: BLE001 — the service never raises; never fail a sign-in on it
            logger.error(
                "OAuth: Apple grant step raised for user=%s (%s) — signing in without it",
                user_id, type(e).__name__, exc_info=True,
            )

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

    # Defence in depth around the verifier (SOC-02): a token whose header names an algorithm
    # its JWK cannot use made python-jose raise JWKError — a JOSEError, NOT a JWTError — which
    # escaped as an off-contract 500 and a Sentry event per forged POST. Whatever the verifier
    # raises, it is a token we could not verify.
    try:
        payload = await verify_supabase_token(request.supabase_access_token)
    except Exception as e:
        logger.warning(
            "session-exchange rejected: verifier raised %s — treating as unverifiable",
            type(e).__name__,
        )
        payload = None
    if not isinstance(payload, dict) or not payload.get("sub"):
        logger.warning("session-exchange rejected: Supabase token failed verification")
        raise auth_error(
            ErrorCode.AUTH_PROVIDER_FAILED,
            message="supabase access token failed signature verification",
        )

    # An ANONYMOUS Supabase session (if anonymous sign-ins are ever enabled) is aud
    # "authenticated" like any other, so it verifies. It proves no identity at all and must
    # never become a Caydex session (SEC-2).
    if payload.get("is_anonymous") is True:
        logger.warning("session-exchange rejected: anonymous Supabase session")
        raise auth_error(
            ErrorCode.AUTH_PROVIDER_FAILED,
            message="supabase access token belongs to an anonymous session",
        )

    user_id = str(payload["sub"])
    email = payload.get("email")

    # The DB trigger creates public.users on auth.users insert. Confirm it landed rather
    # than minting tokens for an id with no app-level row — and, in the SAME read, fetch the
    # password stamp. `limit(1)` so a missing row is an empty list, not an exception.
    try:
        row = (await sb_exec(
            supabase.table("users").select("id, password_changed_at").eq("id", user_id).limit(1)
        ))
        rows = row.data or []
        if isinstance(rows, dict):
            rows = [rows]
        if not rows or not (rows[0] or {}).get("id"):
            raise ValueError("no public.users row")
        changed_at = rows[0].get("password_changed_at")
    except Exception as e:
        logger.error(
            "session-exchange: users read failed or no app user row for verified Supabase "
            "user=%s (%s: %s) — refusing (fail closed)",
            user_id, type(e).__name__, e,
        )
        # 503 AUTH_UNAVAILABLE, not a bare-string 500. iOS's `case 500...599` arm tries
        # `APIErrorResponse` first and otherwise falls back to `.serverError`, whose copy is
        # hardcoded and generic — so a string detail here means the specific sentence below
        # is written, serialised, and then THROWN AWAY, and the failure arrives with no
        # machine-readable code. `AUTH_UNAVAILABLE` is already handled by the shipped app
        # (`.authUnavailable`, action `retry_later`) and, critically, does NOT clear the
        # stored credential — the token is fine; the identity-store operation is not.
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message="session-exchange found no app user row for a verified Supabase user",
            user_message="Your account isn't ready yet. Please try again.",
        )

    # A Supabase JWT minted BEFORE the account's last password change must not become a fresh
    # Caydex session (SEC-2). Otherwise a thief holding a still-unexpired Supabase access token
    # (they last up to an hour) POSTs it here after the victim's reset and gets app tokens with
    # `iat = now` — which pass every later eviction check, so the reset did nothing. FAIL
    # CLOSED: an `iat` we cannot read, or a stamp we cannot parse, is not "fresh".
    if changed_at:
        try:
            stale = _issued_before_password_change(payload.get("iat"), changed_at)
        except (TypeError, ValueError, OverflowError, OSError) as e:
            logger.error(
                "session-exchange: cannot compare iat with password_changed_at for user=%s "
                "(%s) — refusing (fail closed)", user_id, type(e).__name__,
            )
            raise auth_error(
                ErrorCode.AUTH_PROVIDER_FAILED,
                message="supabase token iat unreadable against password_changed_at",
            )
        if stale:
            logger.warning(
                "session-exchange rejected for user=%s: Supabase token predates the last "
                "password change", user_id,
            )
            raise auth_error(
                ErrorCode.AUTH_PROVIDER_FAILED,
                message="supabase access token predates users.password_changed_at",
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
# Associated Domains entitlement, neither of which exists yet. A one-time code (8 digits as
# the project is configured; the schema accepts 6-12) that the user types into the app needs
# no hosting and no deep-link plumbing.
#
# REQUIRES A ONE-TIME SUPABASE DASHBOARD CHANGE: the "Reset Password" email template must
# include `{{ .Token }}`. The default template only has `{{ .ConfirmationURL }}`, and with
# that template users receive a link and no code. See the launch checklist.

_GENERIC_RESET_MESSAGE = (
    "If an account exists for that email, we've sent password reset instructions. "
    "Check your inbox, including spam."
)

#: AUTH_PASSWORD_REJECTED after the single-use code was already spent (reset, set-password).
#: "Send a new code" — the words on the button both screens show.
_PASSWORD_REJECTED_NEEDS_NEW_CODE = (
    "That password can't be used. Send a new code, then choose a different password."
)


def _code_used(message: str) -> HTTPException:
    """Reset / set-password: the recovery code VERIFIED (so it is spent) and the write after it
    failed for a reason that is not the password itself (PW-4). AUTH_CODE_USED carries the
    stock copy — "…that code has now been used. Send a new code and try again." — so iOS clears
    the dead code instead of resubmitting it (which could only read AUTH_CODE_INVALID)."""
    return auth_error(ErrorCode.AUTH_CODE_USED, message=message)


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

    # 10 per IP and 3 per address, per 15 minutes (owner, 2026-11-26; was 5 and 3 an hour).
    # Deliberately tight — this endpoint sends mail. Still a generic response shape, but 429
    # so a legitimate client can back off. The IP bucket stays looser than the address one.
    _enforce_credential_limits(
        (f"forgot:ip:{client_ip}", 10, MAIL_SEND_WINDOW_SECONDS),
        (f"mail:ip:h:{client_ip}", MAIL_PER_IP_PER_HOUR, MAIL_HOUR_SECONDS),
        # Same exemption as sign-in: three posts from anyone used to block the victim's OWN
        # reset email for the whole window. The hourly ceiling takes the same exemption, so
        # nobody else's sends can hold a device that proved this address before.
        _per_email_check("forgot", email_key, 3, MAIL_SEND_WINDOW_SECONDS, req),
        _per_email_check("forgot_hour", email_key, RESET_MAIL_PER_ADDRESS_PER_HOUR, MAIL_HOUR_SECONDS, req),
        detail="Too many reset requests. Please try again later.",
    )

    try:
        await run_gotrue(
            _auth_of(auth_client, supabase).auth.reset_password_for_email, email_key,
        )
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
    admin_client: Client = Depends(get_admin_client),
):
    """Complete a reset with the emailed code, then set the new password.

    The code is short (8 digits as configured: ~100M possibilities; the schema accepts 6-12),
    so this is rate-limited per email AND per IP independently of Supabase's own limits.
    Without it, a short code is brute-forceable.

    On success `password_changed_at` is stamped, which invalidates tokens an attacker may
    already hold (migration 105) — the reason someone resets a password is often that
    somebody else has their session.
    """
    client_ip = trusted_client_ip(req)
    email_key = request.email.strip().lower()

    _enforce_credential_limits(
        (f"reset:ip:{client_ip}", 10, 3600),
        _per_email_check("reset", email_key, 10, 3600, req),
        detail="Too many attempts. Please wait a while, then send a new code and try again.",
    )

    # 1. Verify the OTP. This is what proves control of the mailbox. Classified (AUTH-BE-05):
    #    a refused code is 400 AUTH_CODE_INVALID, an outage/429 a retryable 503 — never the
    #    old blanket "That code is invalid or has expired" for a GoTrue outage.
    try:
        verified = await run_gotrue(
            _auth_of(auth_client, supabase).auth.verify_otp,
            {"email": email_key, "token": request.code, "type": "recovery"},
        )
    except Exception as e:
        raise _otp_failure(e, route="reset-password")

    user = getattr(verified, "user", None)
    if not user or not getattr(user, "id", None):
        # verify_otp did not raise, so GoTrue accepted — and consumed — the code; only the
        # answer is unusable. Ours, and the code is gone: AUTH_CODE_USED, at ERROR.
        logger.error("reset-password: verify_otp succeeded but returned no user")
        raise _code_used("reset-password: verify_otp returned no user")
    user_id = str(user.id)

    # 2. Set the new password with the admin API — on the ADMIN client, which nothing ever
    #    signs in on. The `verify_otp` above returns a session, so it emits SIGNED_IN, so the
    #    SDK rewrote `Authorization` on the auth client to THIS user's JWT (auth, admin and
    #    postgrest all share one headers dict — see database._reset_to_service_role). Running
    #    admin there meant GoTrue saw a user JWT on /admin/* and refused with "User not
    #    allowed", so every reset 500'd. Admin is still right over the verify_otp session: it
    #    keeps this independent of SDK session state.
    try:
        await run_gotrue(
            resolve_admin_client(admin_client, auth_client, supabase).auth.admin.update_user_by_id,
            user_id, {"password": request.new_password},
        )
    except Exception as e:
        # THEIRS first (AUTH-BE-03, SB-5). GoTrue refuses a password our validator accepts —
        # a breached one once leaked-password protection is on (the validator now mirrors
        # GoTrue's character sets and 72-byte cap exactly, so "pwned" is the expected case).
        # The code above is SINGLE-USE and already spent, so the copy must say a NEW code is
        # needed, because the form still holds the dead one.
        if _is_password_rejected(e):
            logger.info("reset-password: new password refused by the provider for user=%s", user_id)
            raise auth_error(
                ErrorCode.AUTH_PASSWORD_REJECTED,
                message="reset-password: admin.update_user_by_id rejected the new password",
                user_message=_PASSWORD_REJECTED_NEEDS_NEW_CODE,
                details=_optional_details(reasons=_password_rejection_reasons(e)),
            )
        # OURS — an outage, a 429, a `validation_failed` (e.g. a provider rule that drifted from
        # ours) — but the code is spent all the same, so NOT the retryable AUTH_UNAVAILABLE this
        # used to be ("Please try again" could only ever be answered "That code is invalid").
        # Typed, never a bare-string 5xx: iOS's 5xx arm decodes `APIErrorResponse` first.
        logger.error(
            "Password update failed after a VERIFIED reset code for user=%s: %s: %s "
            "(status=%s code=%s)",
            user_id, type(e).__name__, e, _gotrue_status(e), _gotrue_code(e),
        )
        raise _code_used(
            f"reset-password: admin.update_user_by_id failed after a verified code: "
            f"{type(e).__name__} (code={_gotrue_code(e)})"
        )

    # 3. Evict sessions issued before this moment.
    (await asyncio.to_thread(_mark_password_changed, supabase, user_id))
    logger.info("Password reset completed for user=%s", user_id)
    # `has_password` just flipped to true. Drop the cached probe or `GET /users/me` keeps
    # reporting the old answer for up to its TTL, and the settings row keeps offering
    # "Set a Password" to someone who now has one.
    auth_methods_service.invalidate(user_id)

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
    admin_client: Client = Depends(get_admin_client),
):
    """Change the password of a signed-in user.

    Requires the CURRENT password even though the caller is authenticated: otherwise a
    stolen access token is enough to take permanent ownership of the account by changing
    its password. Verified by attempting a real sign-in with it.
    """
    # Read while the Request is to hand; the limiter that consumes it runs further down, after
    # the has-a-password probe.
    client_ip = trusted_client_ip(req)

    # Resolve the account's email — the request doesn't carry it, and it must come from
    # the token's subject rather than user input.
    try:
        # `limit(1)`, not `single()`. `single()` RAISES on zero rows (PostgREST 406/PGRST116),
        # so a missing `public.users` row — the exact state `_app_user_row_exists` exists to
        # handle — became an indistinguishable 500 instead of the 404 below. That 500 is a
        # bare-string body iOS cannot decode, so it surfaced as `.serverError` and used to be
        # auto-retried twice, burning three of five attempts per tap.
        result = (await sb_exec(supabase.table("users").select("email").eq("id", user_id).limit(1)))
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

    # Does this account even HAVE a password? An Apple/Google account is provisioned by
    # `sign_in_with_id_token` and never has one written, so `auth.users.encrypted_password` is
    # NULL — and the sign-in below then fails as `invalid_credentials`, which this route used to
    # report as "Your current password is incorrect." about a password that has never existed.
    # A TestFlight tester found it by asking the obvious question. Answer honestly instead, and
    # point at `/auth/set-password`.
    #
    # Fails OPEN: an unknown result (RPC error, or migration 156 not applied yet) falls through
    # to the legacy path, which is wrong only in the same way it was already wrong. The mirror
    # decision in `/auth/set-password` fails CLOSED, and the asymmetry is deliberate — see there.
    methods = await auth_methods_service.get(supabase, user_id)
    if methods is not None and methods.get("has_password") is False:
        raise auth_error(
            ErrorCode.AUTH_PASSWORD_NOT_SET,
            message=f"change-password on a password-less account (providers={methods.get('providers')})",
        )

    # RATE LIMITED, and it must be. This route performs a real `sign_in_with_password` below,
    # so it is a password oracle for anyone holding a stolen access token — which is exactly the
    # threat the current-password requirement exists to stop. It was the ONLY credential route
    # with no limiter; every other one keys off `trusted_client_ip`. Supabase's own limiting is
    # not a backstop here: every call leaves from the Railway container's address, so tripping it
    # degrades sign-in for ALL users.
    #
    # Keyed per USER as well as per IP — the per-IP half alone would let an attacker with a pool
    # of addresses keep guessing against one account.
    #
    # Placed AFTER the probe on purpose. The probe is authenticated, self-scoped (the id comes
    # from the verified JWT subject, never the body) and cached, so it needs no anti-brute-force
    # budget of its own — and a stale client on a password-less account no longer spends all
    # five attempts discovering that this flow can never work for it.
    _enforce_credential_limits(
        (f"changepw:ip:{client_ip}", 10, 3600),
        (f"changepw:user:{user_id}", 5, 900),
        detail="Too many password change attempts. Please try again later.",
    )

    if request.new_password == request.current_password:
        raise HTTPException(
            status_code=400,
            detail="Your new password must be different from your current one.",
        )

    # Verify the current password.
    try:
        await run_gotrue(
            _auth_of(auth_client, supabase).auth.sign_in_with_password,
            {"email": email, "password": request.current_password},
        )
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

    # ADMIN client. The `sign_in_with_password` above — three statements up, same request —
    # emits SIGNED_IN, which rewrites `Authorization` on the auth client to this user's JWT.
    # Running admin there made every single change-password call fail with GoTrue's "User not
    # allowed", which no classifier below matches, so it surfaced as a bare 500.
    try:
        await run_gotrue(
            resolve_admin_client(admin_client, auth_client, supabase).auth.admin.update_user_by_id,
            user_id, {"password": request.new_password},
        )
    except Exception as e:
        # Same split as the re-authentication block above: THEIRS vs OURS.
        #
        # A refused new password is the user's to fix and is not an outage. As a bare-string
        # 500 it was doubly broken: `APIClient.validateResponse`'s 5xx arm has no `detail`
        # fallback (only the 4xx arm does), so the wording never reached iOS at all — the user
        # saw a generic transient server error and was invited to retry the one password that
        # can never be accepted.
        # `_is_password_refused`: a too-long password is GoTrue's `validation_failed`, not
        # `weak_password` (the validator refuses it first; this is the backstop).
        if _is_password_refused(e):
            logger.info(
                "change-password: new password refused by the provider for user=%s (code=%s)",
                user_id, _gotrue_code(e),
            )
            raise auth_error(
                ErrorCode.AUTH_PASSWORD_REJECTED,
                message=f"admin.update_user_by_id rejected the new password (code={_gotrue_code(e)})",
                user_message=_password_refused_copy(e),
                details=_optional_details(reasons=_password_rejection_reasons(e)),
            )
        logger.error(
            "change-password: update failed for user=%s: %s: %s",
            user_id, type(e).__name__, e, exc_info=True,
        )
        # 503 AUTH_UNAVAILABLE, not a bare-string 500. iOS's `case 500...599` arm tries
        # `APIErrorResponse` first and otherwise falls back to `.serverError`, whose copy is
        # hardcoded and generic — so a string detail here means the specific sentence below
        # is written, serialised, and then THROWN AWAY, and the failure arrives with no
        # machine-readable code. `AUTH_UNAVAILABLE` is already handled by the shipped app
        # (`.authUnavailable`, action `retry_later`) and, critically, does NOT clear the
        # stored credential — the token is fine; the identity-store operation is not.
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message="change-password: admin.update_user_by_id failed",
            user_message="We couldn't change your password. Please try again.",
        )

    (await asyncio.to_thread(_mark_password_changed, supabase, user_id))
    logger.info("Password changed for user=%s", user_id)
    # `has_password` just flipped to true. Drop the cached probe or `GET /users/me` keeps
    # reporting the old answer for up to its TTL, and the settings row keeps offering
    # "Set a Password" to someone who now has one.
    auth_methods_service.invalidate(user_id)

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


@router.post("/set-password", response_model=PasswordChangedResponse)
async def set_password(
    request: SetPasswordRequest,
    req: Request,
    user_id: str = Depends(get_current_user_id),
    supabase: Client = Depends(get_supabase),
    auth_client: Client = Depends(get_auth_client),
    admin_client: Client = Depends(get_admin_client),
):
    """Create a FIRST password for a signed-in account that has none.

    An Apple/Google account is provisioned by `sign_in_with_id_token` and never has a password
    written for it, so until now those users had no password, no way to make one, and a Change
    Password screen that told them their nonexistent current password was incorrect. This is the
    way in — and it means losing access to an Apple ID or Google account no longer means losing
    the Caydex account with it.

    **The emailed recovery code is the proof, not the bearer token.** Accepting the session alone
    would make a stolen access token sufficient to take permanent ownership of the account, which
    is precisely what `/auth/change-password`'s current-password requirement exists to prevent —
    and this route has no current password to fall back on. The code is obtained from the
    unchanged `POST /auth/forgot-password`, so no new delivery path exists to get wrong.

    Distinct from `/auth/reset-password`, which does the same write for a SIGNED-OUT caller and
    answers with a bare `MessageResponse`. A signed-in caller going through that route would
    stamp `password_changed_at` and evict their own session on the very next request. This one
    re-mints the caller's tokens after the stamp, exactly as change-password does.
    """
    client_ip = trusted_client_ip(req)
    _enforce_credential_limits(
        (f"setpw:ip:{client_ip}", 10, 3600),
        (f"setpw:user:{user_id}", 5, 900),
        detail="Too many attempts. Please try again later.",
    )

    # From the token's subject, never the body — otherwise the OTP check below could be aimed at
    # somebody else's mailbox. `limit(1)` not `single()`, which raises on zero rows (see the
    # identical note in change-password).
    try:
        result = (await sb_exec(supabase.table("users").select("email").eq("id", user_id).limit(1)))
        rows = result.data or []
        email = rows[0].get("email") if rows else None
    except Exception as e:
        logger.error(
            "set-password: user lookup failed for user=%s: %s: %s",
            user_id, type(e).__name__, e,
        )
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message=f"users lookup failed: {type(e).__name__}",
        )

    if not email:
        raise auth_error(
            ErrorCode.AUTH_ACCOUNT_NOT_FOUND,
            message=f"no public.users row for {user_id}",
        )

    # FAILS CLOSED, and the asymmetry with change-password is the point. There it is safe to
    # fall through on an unknown probe, because the current password is still demanded. Here
    # there is nothing else standing between the caller and the write, so an unknown answer must
    # refuse: proceeding would let this route overwrite an EXISTING password without proving the
    # current one, turning it into the very bypass change-password guards against.
    methods = await auth_methods_service.get(supabase, user_id)
    if methods is None:
        logger.warning(
            "set-password: could not determine password state for user=%s — refusing", user_id
        )
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message="account_auth_methods probe returned no answer",
        )
    if methods.get("has_password") is True:
        raise auth_error(
            ErrorCode.AUTH_PASSWORD_ALREADY_SET,
            message="set-password on an account that already has one",
        )

    # Verify the OTP. This is what proves control of the mailbox.
    try:
        verified = await run_gotrue(
            _auth_of(auth_client, supabase).auth.verify_otp,
            {"email": email, "token": request.code, "type": "recovery"},
        )
    except Exception as e:
        # Classified exactly like reset-password (AUTH-BE-05).
        raise _otp_failure(e, route="set-password", user_id=user_id)

    verified_user = getattr(verified, "user", None)
    verified_id = str(getattr(verified_user, "id", "") or "")
    if not verified_id:
        # verify_otp did not raise, so the code was accepted — and consumed. Same as reset.
        logger.error("set-password: verify_otp succeeded but returned no user for user=%s", user_id)
        raise _code_used("set-password: verify_otp returned no user")
    # Belt and braces. The email came from this user's own row, so a code that verifies against
    # it should always resolve to this user — but the consequence of being wrong is writing a
    # password into somebody else's account, so it is checked rather than assumed. Kept
    # AUTH_FORBIDDEN (not AUTH_CODE_USED): "send a new code and try again" would only repeat an
    # anomaly nobody can explain, and it must stand out in the logs as a security refusal.
    if verified_id != str(user_id):
        logger.error(
            "set-password: OTP resolved to a DIFFERENT user (token subject=%s, otp=%s)",
            user_id, verified_id or "<none>",
        )
        raise auth_error(
            ErrorCode.AUTH_FORBIDDEN,
            message="recovery OTP resolved to a different account",
        )

    # ADMIN client, mandatorily. `verify_otp` above emits SIGNED_IN, which rewrites the headers
    # dict shared by `auth` and `auth.admin` (they are the same object — see database.py) and
    # re-authenticates admin calls as this user. GoTrue then refuses `/admin/*` with
    # "User not allowed", which matches no classifier and surfaces as a bare 500. That is the
    # exact defect that made change-password and reset-password fail 100% of the time.
    try:
        await run_gotrue(
            resolve_admin_client(admin_client, auth_client, supabase).auth.admin.update_user_by_id,
            user_id, {"password": request.new_password},
        )
    except Exception as e:
        if _is_password_rejected(e):
            logger.info("set-password: new password refused by the provider for user=%s", user_id)
            # The recovery code was spent by verify_otp above — same copy as reset-password.
            raise auth_error(
                ErrorCode.AUTH_PASSWORD_REJECTED,
                message="admin.update_user_by_id rejected the new password",
                user_message=_PASSWORD_REJECTED_NEEDS_NEW_CODE,
                details=_optional_details(reasons=_password_rejection_reasons(e)),
            )
        logger.error(
            "set-password: admin update failed after a VERIFIED code for user=%s: %s: %s "
            "(status=%s code=%s)",
            user_id, type(e).__name__, e, _gotrue_status(e), _gotrue_code(e), exc_info=True,
        )
        # AUTH_CODE_USED (503, fix_input), not AUTH_UNAVAILABLE: the code is spent, so "Please
        # try again" could only be answered "That code is invalid" (PW-4). It does NOT clear the
        # stored credential (503, not 401) — the caller's session is fine.
        raise _code_used(
            f"set-password: admin.update_user_by_id failed after a verified code: "
            f"{type(e).__name__} (code={_gotrue_code(e)})"
        )

    (await asyncio.to_thread(_mark_password_changed, supabase, user_id))
    logger.info("Password set for user=%s (providers=%s)", user_id, methods.get("providers"))
    # The probe's whole answer just changed. Without this, `GET /users/me` keeps reporting
    # `has_password: false` for up to the TTL and the settings row keeps saying "Set a Password".
    auth_methods_service.invalidate(user_id)

    # Minted AFTER the stamp so `iat >= password_changed_at`: this device stays signed in while
    # every other one is evicted. Same contract as change-password — dropping these tokens signs
    # the caller out seconds after telling them it worked.
    tokens = _issue_app_tokens_for(user_id, email)
    return PasswordChangedResponse(
        message="Your password is set. You can now sign in with your email and password.",
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        user_id=user_id,
    )
