"""
FastAPI Dependencies
Auth, rate limiting, and utility dependencies.
"""

from typing import Optional
from datetime import datetime, timezone
import uuid
from fastapi import Depends, HTTPException, status, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import Client
import logging

from app.database import get_supabase
from jose import JWTError

from app.core.security import decode_token, verify_supabase_token, rate_limiter
from app.api.error_response import ErrorCode, auth_error
from app.config import settings

logger = logging.getLogger(__name__)

# `auto_error=False` is load-bearing, not a style choice.
#
# With the default `auto_error=True`, FastAPI answers a MISSING or malformed Authorization
# header with **403 "Not authenticated"** (fastapi/security/http.py raises HTTP_403_FORBIDDEN
# for both `not credentials` and a non-Bearer scheme). Two things broke because of that:
#
#   1. iOS runs its refresh-and-retry interceptor on 401 ONLY, so a 403 never triggered a
#      recovery attempt — the client just surfaced "Access Denied", or at a silent call site
#      nothing at all. That is the reported bug: tapping Follow while signed out reverted the
#      button with no message.
#   2. The body was FastAPI's `{"detail": ...}`, not the `{error_code, message, user_message,
#      action, details}` contract iOS decodes (CLAUDE.md invariant #3), so even a client that
#      wanted to react had nothing machine-readable to react to.
#
# Turning auto_error off makes every dependency below responsible for its own rejection, which
# is what lets each one pick the RIGHT code — AUTH_REQUIRED (no credential) is a different
# situation from AUTH_TOKEN_INVALID (a credential that failed), and only the latter should ever
# cost the user their stored token.
security = HTTPBearer(auto_error=False)


def _route_of(request: Optional[Request]) -> str:
    """Request path for a log line, tolerating a missing `Request`.

    Every auth dependency takes `request: Request = None`. FastAPI keys injection off the
    ANNOTATION, so it still injects the real object in production; the default exists purely so
    the test suite can keep calling these as plain functions (its standard idiom — there is no
    TestClient anywhere in backend/tests), and so a logging concern can never be the thing that
    breaks an auth check.
    """
    try:
        return request.url.path if request is not None else "<no-request>"
    except Exception:  # noqa: BLE001 — a log label must never raise
        return "<no-request>"


def _bearer_from_header(authorization: Optional[str]) -> Optional[str]:
    """The token out of an `Authorization` header, or None when there isn't a usable one.

    Tolerates the scheme's case (`bearer` is legal per RFC 7235) and rejects a present-but-empty
    credential — `"Bearer "` must read as "no credential", not as a token of "", which would
    otherwise be handed to the decoders and reported as an INVALID token. That distinction
    decides whether the client keeps its Keychain entry.
    """
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


async def _user_id_from_token(token: str) -> Optional[str]:
    """Resolve a bearer token to a user id, trying app-minted then Supabase-issued.

    Returns None when neither verifier accepts it. Callers decide what that means — the strict
    dependencies 401, the identity-only one degrades to guest.

    `async` because `verify_supabase_token` may have to fetch Supabase's JWKS to verify an
    ES256 token. Doing that synchronously would block the event loop on an outbound HTTP call
    inside an auth dependency — i.e. on essentially every request.
    """
    try:
        payload = _decode_access_token(token)
        user_id = payload.get("sub")
        if user_id:
            return user_id
    except Exception:
        pass

    try:
        payload = await verify_supabase_token(token)
        if payload:
            user_id = payload.get("sub")
            if user_id:
                return user_id
    except Exception:
        pass

    return None


def _reject_unverifiable_token(token: str, *, route: str) -> None:
    """Raise the right 401 for a bearer token that no verifier accepted.

    Splits out one case that is NOT the caller's fault: `verify_supabase_token` returns None
    when `SUPABASE_JWT_SECRET` is unset (core/security.py logs "not configured"). Under the old
    silent-guest behaviour that misconfiguration was invisible; now it would 401 every
    Supabase-issued token, and reporting a config error as a bad credential would send users to
    re-sign-in over and over while the real fault sat in the environment. So it answers
    AUTH_UNAVAILABLE (503, retryable, credential preserved) instead.
    """
    if not settings.SUPABASE_JWT_SECRET:
        logger.error(
            "auth: token rejected on %s and SUPABASE_JWT_SECRET is NOT CONFIGURED — "
            "reporting AUTH_UNAVAILABLE, not a bad credential; check the environment",
            route,
        )
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message=f"Identity verification unavailable on {route}",
        )

    logger.warning("auth: unverifiable bearer token on %s", route)
    raise auth_error(
        ErrorCode.AUTH_TOKEN_INVALID,
        message=f"Bearer token failed verification on {route}",
    )


def _decode_access_token(token: str) -> dict:
    """`decode_token`, but refuses a REFRESH token presented as an access credential.

    Both token kinds are signed with the same `SECRET_KEY` and differ only by a `"type"` claim
    (`create_access_token` stamps `"access"`, `create_refresh_token` stamps `"refresh"` —
    core/security.py). `decode_token` verifies signature and expiry and returns the payload
    either way, and nothing downstream looked at `type`. So the refresh token — which the client
    holds precisely because it is meant to be exchange-only — worked as a Bearer credential on
    every authenticated route.

    Two things that made this worse than a curiosity:
      * refresh tokens live `REFRESH_TOKEN_EXPIRE_MINUTES` (7 days) against the access token's
        24 hours, so it silently widens the window of any leaked credential, and
      * `POST /auth/refresh` is the one place that checks `password_changed_at`, so a refresh
        token used directly as an access token skipped the eviction check entirely.

    Raises `JWTError` (same as `decode_token`) so every existing call site's `except` behaves
    unchanged: the caller falls through to the Supabase-token path and then to 401/guest.
    """
    payload = decode_token(token)
    if isinstance(payload, dict) and payload.get("type") == "refresh":
        logger.warning(
            "refresh token presented as an access credential (sub=%s) — rejected",
            payload.get("sub"),
        )
        raise JWTError("refresh token is not valid as an access credential")
    return payload


async def get_current_user_id(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    request: Request = None,
) -> str:
    """Extract and validate user ID from JWT (custom or Supabase Auth).

    Distinguishes the two failures that used to look identical to the client:
      * no credential at all → **AUTH_REQUIRED**, the "you need an account for this" case. The
        client shows a sign-in prompt and must NOT touch its Keychain.
      * a credential that failed → **AUTH_TOKEN_INVALID**, the "refresh and retry" case.

    Both are 401. `security` no longer auto-errors, so a missing header arrives here as None
    rather than as FastAPI's 403 (see the `HTTPBearer` comment above).
    """
    route = _route_of(request)
    token = credentials.credentials.strip() if credentials else None

    if not token:
        logger.info("auth: no credential presented on %s", route)
        raise auth_error(
            ErrorCode.AUTH_REQUIRED,
            message=f"No authentication credential presented for {route}",
        )

    user_id = await _user_id_from_token(token)
    if user_id:
        return user_id

    _reject_unverifiable_token(token, route=route)
    raise AssertionError("unreachable — _reject_unverifiable_token always raises")


def _reject_if_password_changed_since_issue(token: str, user_row: dict) -> None:
    """401 when `token` predates `user_row["password_changed_at"]`.

    The app mints its own JWTs and validates them on signature + expiry alone, so without
    this a password reset would leave a thief's tokens working (migration 105). The `iat`
    claim already present in every token is the comparison point.

    Fails OPEN on anything unexpected — a parse quirk must not lock out a legitimate user.
    """
    changed_at = user_row.get("password_changed_at")
    if not changed_at:
        return
    try:
        payload = _decode_access_token(token)
        issued_at = payload.get("iat") if payload else None
        if issued_at is None:
            return  # Supabase-issued token, or a token without iat — not our concern here.
        changed_dt = datetime.fromisoformat(str(changed_at).replace("Z", "+00:00"))
        if changed_dt.tzinfo is None:
            changed_dt = changed_dt.replace(tzinfo=timezone.utc)
        issued_dt = datetime.fromtimestamp(float(issued_at), tz=timezone.utc)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(
            "password_changed_at comparison failed (%s: %s) — allowing the token",
            type(e).__name__, e,
        )
        return

    # Compare at WHOLE-SECOND granularity, because that is all `iat` has.
    #
    # JWTs carry `iat` as an integer POSIX timestamp, while `password_changed_at` is written
    # with microsecond precision. A token minted immediately after the stamp therefore floors
    # BELOW it — e.g. stamp 12:00:00.604382, iat 12:00:00 — and a naive `<` rejects a
    # credential that is genuinely newer than the change. That defeated the replacement tokens
    # `POST /auth/change-password` hands back (they are minted in the same second as the stamp,
    # so they were rejected ~100% of the time), and it could also 401 a legitimate user who
    # signed in again within the same second as their own reset.
    #
    # Flooring the stamp costs at most a one-second window: a token issued during the same
    # wall-clock second as the change survives. Anything from any earlier second is still
    # evicted, which is the property this control exists to provide.
    if issued_dt < changed_dt.replace(microsecond=0):
        logger.info(
            "Rejecting token for user=%s: issued %s, password changed %s",
            user_row.get("id"), issued_dt.isoformat(), changed_dt.isoformat(),
        )
        raise auth_error(
            ErrorCode.AUTH_SESSION_EXPIRED,
            message=(
                f"Token issued {issued_dt.isoformat()} predates the password change at "
                f"{changed_dt.isoformat()} for user={user_row.get('id')}"
            ),
        )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    user_id: str = Depends(get_current_user_id),
    supabase: Client = Depends(get_supabase),
    request: Request = None,
) -> dict:
    """Get current user record from DB.

    Also enforces password-change token invalidation: a token minted before the account's
    last password change is rejected here. Free to check — the `select("*")` below already
    returns `password_changed_at` (migration 105), so there is no extra round-trip.
    """
    try:
        # limit(1), NOT single(). PostgREST answers `single()` on zero rows with a 406 /
        # PGRST116, which postgrest-py raises as APIError — an ordinary exception that the
        # `except Exception` below turns into a **500 "Error fetching user data"**. The
        # intended 404 was therefore unreachable, and more importantly a 500 is not an auth
        # error: iOS `AppError.isAuthError` covers only unauthorized/tokenExpired/forbidden,
        # so the client keeps a token that can never resolve and retries forever instead of
        # signing the user out. Same idiom already used by get_current_user_or_guest below.
        result = supabase.table("users").select("*").eq("id", user_id).limit(1).execute()
        rows = result.data or []
        if not rows:
            # 401, not 404: the token is valid but names an account that no longer exists
            # (deleted, or a signup whose trigger never seeded public.users). The client must
            # drop the credential, and only an auth error makes it do that.
            logger.warning(
                "auth: valid token for user=%s but no public.users row on %s — "
                "treating as unauthenticated",
                user_id, _route_of(request),
            )
            raise auth_error(
                ErrorCode.AUTH_ACCOUNT_NOT_FOUND,
                message=f"Token names user={user_id} with no public.users row",
            )

        # `credentials` cannot be None here: get_current_user_id already raised AUTH_REQUIRED
        # when no credential was presented, and this dependency only runs after it resolves.
        #
        # ⚠️ `.strip()` is load-bearing, not tidiness. `get_current_user_id` strips before it
        # verifies, so a padded bearer authenticates fine — but this call used to receive the
        # RAW value, fail to decode it, hit the fail-open `except Exception` inside, and
        # return the user anyway. Net effect: `Authorization: Bearer  <token>` (two spaces —
        # FastAPI splits on the FIRST space only, so the credential keeps a leading one)
        # authenticated AND skipped password-change eviction, so a stolen token survived the
        # victim's password reset. Both call sites must normalise identically.
        _reject_if_password_changed_since_issue(credentials.credentials.strip(), rows[0])
        return rows[0]
    except HTTPException:
        raise
    except Exception as e:
        # 503, not 500. A read failure here is transient and RETRYABLE, and the difference is
        # load-bearing on the client: iOS does not classify a 500 as an auth error, so it kept a
        # perfectly good token bound to a request that could never resolve and retried forever.
        # It also has to match `get_current_user_or_guest`, which already answered 503 for this
        # exact condition — one failure mode must not have two contracts.
        logger.error(
            "auth: users read failed for user=%s on %s: %s: %s",
            user_id, _route_of(request), type(e).__name__, e, exc_info=True,
        )
        raise auth_error(
            ErrorCode.AUTH_UNAVAILABLE,
            message=f"users read failed: {type(e).__name__}: {str(e)[:200]}",
        )


async def get_optional_user_id(
    authorization: Optional[str] = Header(None),
    request: Request = None,
) -> Optional[str]:
    """Optional auth: the caller's user id, or None when they presented no credential.

    "No credential" and "a credential that failed" are NOT the same thing, and conflating them
    is the defect this rewrite exists to remove. Previously ANY unverifiable token returned
    None, so a signed-in user whose access token had expired was silently served as an
    anonymous caller with a 200 — the client had no way to learn its session had died, so it
    never refreshed, and the user spent the rest of the run as a guest holding a good token.

    Now: no header → None (guest, unchanged). Header present but unverifiable → 401
    AUTH_TOKEN_INVALID, which is what makes the client refresh and heal.
    """
    token = _bearer_from_header(authorization)
    if token is None:
        return None

    user_id = await _user_id_from_token(token)
    if user_id:
        return user_id

    _reject_unverifiable_token(token, route=_route_of(request))
    return None  # unreachable — _reject_unverifiable_token always raises


# TEMP: shared guest user used by research/credits endpoints while the
# iOS login UI is not yet built. Backed by a real auth.users row +
# public.users row + user_credits row (50 credits seeded). Switch back
# to strict get_current_user once SignInView is wired into RootView.
GUEST_USER_ID = "00000000-0000-4000-8000-00000000dead"


async def get_current_user_or_guest(
    authorization: Optional[str] = Header(None),
    supabase: Client = Depends(get_supabase),
    request: Request = None,
) -> dict:
    """The authenticated user, or the shared guest when NO credential was presented.

    The middle case is the one that changed. A bearer token that fails verification (expired,
    bad signature, or a refresh token used as an access token) used to fall through to the guest
    sentinel and return **200 with the guest's data** — the single largest source of "auth is
    unreliable app-wide". This dependency (directly or via the three `*_identity` wrappers)
    backs ~100 routes, so a signed-in user whose token quietly died was served the guest
    watchlist, the guest reports, and the guest credit balance, indistinguishably from being
    signed out. Nothing on the wire told the client to refresh, so nothing ever did.

    It now raises AUTH_TOKEN_INVALID for that case. Callers who legitimately have no account —
    every real guest — send no `Authorization` header at all and are completely unaffected;
    that path still returns the sentinel. `X-Guest-Id` continues to partition them per install.
    """
    token = _bearer_from_header(authorization)
    if token is not None:
        user_id = await _user_id_from_token(token)
        if not user_id:
            _reject_unverifiable_token(token, route=_route_of(request))

        if user_id:
            # A VALID token resolved to a real user_id. A transient users-table read
            # failure here must NOT silently fall through to the shared guest — that
            # would serve the guest's balance to a signed-in user and skip their
            # monthly reset. Surface a retryable error instead. (limit(1), not
            # single(), so "no row" is an empty list, not an exception.)
            try:
                result = supabase.table("users").select("*").eq("id", user_id).limit(1).execute()
            except Exception as e:
                logger.error(
                    "auth: users read failed for authenticated user=%s on %s: %s: %s",
                    user_id, _route_of(request), type(e).__name__, e, exc_info=True,
                )
                raise auth_error(
                    ErrorCode.AUTH_UNAVAILABLE,
                    message=f"users read failed: {type(e).__name__}: {str(e)[:200]}",
                )
            rows = result.data or []
            if rows:
                # Same password-change eviction `get_current_user` applies (migration 105).
                # It was missing here, and THIS is the dependency most authenticated routes
                # actually use — directly (chat, reports, research, analytics) and indirectly
                # via get_watchlist_identity / get_learn_identity. So a stolen access token kept
                # reading and writing the victim's data for the rest of its 24-hour life after
                # the victim reset their password; only POST /auth/refresh turned the thief away.
                # Free to check: the select("*") above already returned password_changed_at.
                _reject_if_password_changed_since_issue(token, rows[0])
                return rows[0]
            # Valid token but no public.users row (rare — the signup trigger seeds it).
            # Fall through to guest as before rather than 500 a first-launch edge.
            #
            # LOG IT. This is a silent privilege DEMOTION: the caller proved a valid token and
            # is nonetheless served the shared guest sentinel — guest credits, guest watchlist,
            # someone else's data — with a 200. Undiagnosable from logs without this line, and
            # CLAUDE.md requires an intentionally non-fatal degradation to say so.
            logger.warning(
                "Valid token for user=%s resolved NO public.users row — serving the shared "
                "guest identity instead (check the signup trigger for this account)",
                user_id,
            )

    return {"id": GUEST_USER_ID, "email": "guest@local", "tier": "free"}


# Fixed namespace for deriving a per-install guest id. Random once, constant
# forever — changing it orphans every guest's Learn progress.
_GUEST_NAMESPACE = uuid.UUID("6f9d3c2a-1e57-4a2b-9c84-5b1d0e7a3f6c")


def guest_user_id_for(install_id: Optional[str]) -> str:
    """Stable pseudo-user id for one app INSTALL.

    Every install currently authenticates as the same ``GUEST_USER_ID`` (the iOS
    login UI is not built yet and no request carries a Bearer token). Because the
    Learn stores union-merge the server's set into the local one, that meant one
    user's completed lessons and bookmarks were merged into EVERY other user's
    app. This derives a distinct id per install instead.

    UUID5 over a fixed namespace, so:
      * the id is deterministic — the same install always resolves to the same
        row set, across app restarts and backend deploys;
      * a client cannot impersonate a real account by sending someone's uuid,
        because the value it sends is HASHED, never used directly.

    Returns the shared ``GUEST_USER_ID`` when no install id is supplied, which is
    what already-shipped app versions do — their behaviour is unchanged.
    """
    if not install_id:
        return GUEST_USER_ID
    cleaned = install_id.strip()[:200]
    if not cleaned:
        return GUEST_USER_ID
    return str(uuid.uuid5(_GUEST_NAMESPACE, cleaned))


async def get_identity_only_user(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Caller identity from the TOKEN ALONE — no database read, cannot raise.

    For fire-and-forget instrumentation. `get_current_user_or_guest` reads `public.users` and
    deliberately raises 503 when that read fails, so a signed-in user's request is never
    silently served the guest's data. Correct for data endpoints — and wrong for analytics,
    which promises in its own module docstring that it "can never break the app": the iOS
    `Analytics` actor removes events from its buffer BEFORE the request and does not re-queue
    on failure, so a 503 here silently destroys the batch. A Supabase blip would blind the
    telemetry that exists to detect Supabase blips.

    Only `identity_key()` is needed downstream, and for an authenticated caller that is just
    the token's `sub` — which is already in hand without touching the database.

    ⚠️ DELIBERATE CARVE-OUT — do not "fix" this to match the others. Every sibling dependency
    now raises AUTH_TOKEN_INVALID for an unverifiable bearer, so that a dead session announces
    itself instead of being silently served guest data. This one must NOT, for the reason above:
    it backs analytics, whose whole contract is that instrumentation can never break the
    product, and whose client discards the batch on any error. A bad token here costs one
    mis-bucketed telemetry row; raising would cost the batch. It is logged so the degradation is
    visible, per CLAUDE.md's no-silent-degradation rule. `tests/test_auth_dependency_matrix.py`
    pins the non-raising behaviour.
    """
    token = _bearer_from_header(authorization)
    if token is not None:
        user_id = await _user_id_from_token(token)
        if user_id:
            return {"id": user_id, "email": "", "tier": "free"}
        logger.warning(
            "auth: unverifiable bearer on the identity-only path — bucketing as guest rather "
            "than raising (analytics must never break the app); telemetry for this caller is "
            "attributed to their install, not their account",
        )
    return {"id": GUEST_USER_ID, "email": "guest@local", "tier": "free"}


async def get_learn_identity(
    authorization: Optional[str] = Header(None),
    x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    supabase: Client = Depends(get_supabase),
    request: Request = None,
) -> dict:
    """Identity for the LEARN routes: a real account, else a PER-INSTALL guest.

    Deliberately scoped to Learn rather than replacing
    :func:`get_current_user_or_guest` everywhere: research / credits / portfolios
    hang off a seeded ``GUEST_USER_ID`` row (with real credits), and pointing
    those at a synthetic id would break them. Learn progress has no such
    dependency — ``user_learn_progress.user_id`` is a bare uuid column with no
    foreign key — so it can be partitioned safely.
    """
    user = await get_current_user_or_guest(
        request=request, authorization=authorization, supabase=supabase
    )
    if user.get("id") != GUEST_USER_ID:
        return {**user, "is_guest": False}   # a real signed-in account always wins
    return {
        "id": guest_user_id_for(x_guest_id),
        "email": "guest@local",
        "tier": "free",
        # Carried for the same reason the research and chat wrappers carry it, and it was
        # missing here: a per-install id NEVER equals ``GUEST_USER_ID``, so the obvious test
        # ``user["id"] == GUEST_USER_ID`` classifies every guest as a paying account. The
        # rulebook prescribes ``user.get("is_guest")``, which read falsy on this dependency
        # and on the watchlist one — a loaded gun for the next caller that follows the rule.
        "is_guest": True,
    }


async def get_profile_identity(
    authorization: Optional[str] = Header(None),
    x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    supabase: Client = Depends(get_supabase),
    request: Request = None,
) -> dict:
    """Identity for the INVESTOR-PROFILE routes: a real account, else a PER-INSTALL guest.

    Same shape and same justification as :func:`get_learn_identity`, and guest-capable
    for a specific product reason: the profile is captured during FIRST-RUN onboarding,
    before an account exists. Gating it on a token would mean the questions can only be
    asked after sign-up — which is the wrong order for a guest-first funnel and would
    leave the whole feature with no data for the users most likely to convert.

    Safe because `user_investor_profile.user_id` has NO foreign key (migration 131), so
    a synthetic per-install uuid5 is a valid owner. It costs nothing per call — no LLM,
    no upstream — so it sits on the cheap-and-claimable side of the line in
    .claude/rules/auth.md §1a rather than with the metered surfaces.

    Its own dependency rather than borrowing the Learn or watchlist one: those carry
    their own table's semantics, and a future change to either must not silently
    re-partition this one.
    """
    user = await get_current_user_or_guest(
        request=request, authorization=authorization, supabase=supabase
    )
    if user.get("id") != GUEST_USER_ID:
        return {**user, "is_guest": False}   # a real signed-in account always wins
    return {
        "id": guest_user_id_for(x_guest_id),
        "email": "guest@local",
        "tier": "free",
        # Never `user["id"] == GUEST_USER_ID` downstream — a per-install id never equals
        # the sentinel, so that test reads every guest as a paying account.
        "is_guest": True,
    }


async def get_research_identity(
    authorization: Optional[str] = Header(None),
    x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    supabase: Client = Depends(get_supabase),
    request: Request = None,
) -> dict:
    """Identity for the RESEARCH routes: a real account, else a PER-INSTALL guest.

    ⚠️ **NOT WIRED TO ANY ROUTE. Do not connect one without reading this.**

    All nine ``/research/*`` routes take ``Depends(get_current_user)``: AI generation is
    account-only, because it is the most expensive call in the product (~17 Gemini + ~20 FMP
    per run) and the guest meter it used to rely on keyed on ``X-Guest-Id`` — a header the
    CLIENT picks — so rotating it minted a fresh allowance on every request. Credits replaced
    that: FK-bound to a real ``public.users`` row, and therefore not rotatable. Re-pointing a
    research route at this dependency re-opens free, unmetered AI generation.

    Kept rather than deleted because migration 110's per-install partitioning is still live for
    anyone holding reports created before the change, and because
    ``POST /users/me/claim-guest-data`` must still be able to find them. Two source-scan tests
    fail the build if ``Depends(get_research_identity)`` reappears in ``research.py``
    (``test_research_guest_partition.py`` and ``test_auth_dependency_matrix.py``).

    Same shape and same reason as :func:`get_watchlist_identity`. Before migration 110 every
    signed-out user wrote `research_reports` under the shared ``GUEST_USER_ID``, and
    ``GET /research/reports`` filters on that column — so one guest's Reports tab listed every
    other guest's reports (ticker, executive summary, score, fair value), and either could open,
    rate, or delete the other's. The ticker someone researches discloses intent, so this is a
    cross-user data leak, not just untidy state.

    Safe only because migration 110 dropped ``research_reports_user_id_fkey`` — a synthetic
    per-install uuid has no ``public.users`` row. That FK was ON DELETE CASCADE, so account
    deletion now clears the table explicitly via ``_UNLINKED_USER_TABLES`` in the users endpoint.

    Callers must ALSO stop using ``user["id"] == GUEST_USER_ID`` as their guest test — a
    per-install id never equals the sentinel. Use ``user.get("is_guest")``.
    """
    user = await get_current_user_or_guest(
        request=request, authorization=authorization, supabase=supabase
    )
    if user.get("id") != GUEST_USER_ID:
        return {**user, "is_guest": False}   # a real signed-in account always wins
    return {
        "id": guest_user_id_for(x_guest_id),
        "email": "guest@local",
        "tier": "free",
        "is_guest": True,
    }


async def get_chat_identity(
    authorization: Optional[str] = Header(None),
    x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    supabase: Client = Depends(get_supabase),
    request: Request = None,
) -> dict:
    """Identity for the CHAT routes: a real account, else a PER-INSTALL guest.

    Same shape and same reason as :func:`get_research_identity`. Before migration 111 every
    signed-out user wrote `chat_sessions` under the shared ``GUEST_USER_ID``, and both read
    paths filter on that column — ``GET /chat/sessions`` and ``GET /chat/sessions/{id}`` each
    do ``.eq("user_id", user["id"])``. So one guest's conversations were listed, readable,
    renameable and DELETABLE by every other guest.

    This was the last table where that was still true, and the worst one: chat is where people
    paste holdings and ask personal financial questions.

    Safe only because migration 111 dropped ``chat_sessions_user_id_fkey`` — a synthetic
    per-install uuid has no ``public.users`` row. That FK was ON DELETE CASCADE, so account
    deletion now clears the table explicitly via ``_UNLINKED_USER_TABLES``; ``chat_messages``
    still cascades from ``chat_sessions``.

    Callers must ALSO stop using ``user["id"] == GUEST_USER_ID`` as their guest test — a
    per-install id never equals the sentinel, so that comparison silently classifies every
    guest as a paying account and sends them into a credit precharge against a `user_credits`
    row that does not exist (402 "insufficient credits"). Use ``user.get("is_guest")``.
    """
    user = await get_current_user_or_guest(
        request=request, authorization=authorization, supabase=supabase
    )
    if user.get("id") != GUEST_USER_ID:
        return {**user, "is_guest": False}   # a real signed-in account always wins
    return {
        "id": guest_user_id_for(x_guest_id),
        "email": "guest@local",
        "tier": "free",
        "is_guest": True,
    }


async def get_watchlist_identity(
    authorization: Optional[str] = Header(None),
    x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    supabase: Client = Depends(get_supabase),
    request: Request = None,
) -> dict:
    """Identity for the WATCHLIST routes: a real account, else a PER-INSTALL guest.

    Same shape and same reason as :func:`get_learn_identity`. Before migration 108,
    every signed-out user wrote `watchlist_items` under the shared ``GUEST_USER_ID``,
    so one guest adding a ticker put it on EVERY other guest's Tracking tab and either
    could delete the other's. That is the identical defect `guest_user_id_for` was
    introduced to fix for Learn progress; it just was never extended here.

    Safe only because migration 108 dropped ``watchlist_items_user_id_fkey`` — a
    synthetic per-install uuid has no ``public.users`` row. That FK was ON DELETE
    CASCADE, so account deletion now clears the table explicitly via
    ``_UNLINKED_USER_TABLES`` in the users endpoint.

    Still deliberately scoped rather than replacing ``get_current_user_or_guest``
    everywhere: research / credits / portfolios hang off the seeded GUEST_USER_ID row
    (which owns real credits), and pointing those at a synthetic id would break them.
    """
    user = await get_current_user_or_guest(
        request=request, authorization=authorization, supabase=supabase
    )
    if user.get("id") != GUEST_USER_ID:
        return {**user, "is_guest": False}   # a real signed-in account always wins
    return {
        "id": guest_user_id_for(x_guest_id),
        "email": "guest@local",
        "tier": "free",
        # See get_learn_identity. This wrapper backs 20 routes (portfolios, tracking,
        # watchlist, home /dashboard, updates /tabs) — the widest guest surface in the app,
        # and the one where a falsy ``is_guest`` would do the most damage.
        "is_guest": True,
    }


class RateLimitChecker:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def __call__(
        self,
        user_id: Optional[str] = Depends(get_optional_user_id),  # TEMP: guest fallback
        x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    ):
        # While research/credits endpoints accept the GUEST_USER_ID, the rate
        # limit must not 401 on missing auth. Unauthenticated callers bucket
        # PER-INSTALL off the X-Guest-Id header (same derivation chat uses), not
        # under one shared "guest" key — a single key gives zero per-attacker
        # protection AND makes real guests 429 each other. Clients that send no
        # header still land on the shared GUEST_USER_ID, so already-shipped app
        # versions are unaffected.
        #
        # The key space is now caller-influenced, which is safe: `rate_limiter`
        # self-bounds at _MAX_TRACKED with idle-drop + FIFO eviction
        # (core/security.py, pinned by tests/test_rate_limiter_bound.py).
        key = user_id or f"guest:{guest_user_id_for(x_guest_id)}"
        if not rate_limiter.is_allowed(key, self.max_requests, self.window_seconds):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please try again later.",
                headers={"Retry-After": str(self.window_seconds)}
            )


StandardRateLimit = Depends(RateLimitChecker(60, 60))


# ── Chat abuse / cost bucketing (OWASP LLM10 — denial-of-wallet) ─────────────

def identity_key(user: dict, x_guest_id: Optional[str]) -> str:
    """The rate-limit + daily-budget bucket key for a caller.

    A real signed-in account keys off its own user id. An unauthenticated caller
    (the shared ``GUEST_USER_ID``) keys off a PER-INSTALL id derived from the
    ``X-Guest-Id`` header (:func:`guest_user_id_for`), so one guest install can't
    exhaust another's rate limit or daily budget.

    IMPORTANT: this is ONLY an abuse/cost bucket — it is NEVER written as
    ``chat_sessions.user_id``. That column is now written from :func:`get_chat_identity`
    instead — migration 111 dropped its FK so each guest install owns its own chat history
    (it used to be one shared bucket that any guest could read, rename and delete).
    """
    uid = user.get("id")
    if uid and uid != GUEST_USER_ID:
        return uid
    return guest_user_id_for(x_guest_id)


# Historical name — chat was the first caller. Kept so existing imports and the
# docs that reference it keep working.
chat_identity_key = identity_key


class IdentityRateLimitChecker:
    """Per-user (per-install for guests) sliding-window rate limit.

    ``bucket`` namespaces the counter so unrelated surfaces don't share a
    window — but every endpoint using the SAME bucket does share one, which is
    deliberate: the two chat endpoints must not let a caller dodge the limit by
    alternating between them.
    """

    def __init__(self, bucket: str, max_requests: int, window_seconds: int = 60):
        self.bucket = bucket
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def __call__(
        self,
        user: dict = Depends(get_current_user_or_guest),
        x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    ) -> None:
        key = f"{self.bucket}:{identity_key(user, x_guest_id)}"
        if not rate_limiter.is_allowed(key, self.max_requests, self.window_seconds):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please slow down and try again shortly.",
                headers={"Retry-After": str(self.window_seconds)},
            )


ChatRateLimit = Depends(
    IdentityRateLimitChecker("chat", settings.CHAT_RATE_LIMIT_PER_MINUTE, 60)
)

# A report generation is ~20x the cost of a chat turn (~17 Gemini + ~20 FMP calls
# on a cache miss), so its window is far tighter than chat's. This is the ONLY
# per-caller control on GET /stocks/{ticker}/report — it was previously
# completely ungated.
ReportRateLimit = Depends(
    IdentityRateLimitChecker("report", settings.REPORT_RATE_LIMIT_PER_MINUTE, 60)
)

# Analytics gets its OWN bucket. Sharing StandardRateLimit's window would let a burst
# of telemetry flushes 429 the user's REAL requests — instrumentation degrading the
# product is precisely what the analytics module forbids. Generous, because a batch is
# one cheap insert, and lossy by design if it's ever hit.
class IdentityOnlyRateLimitChecker(IdentityRateLimitChecker):
    """`IdentityRateLimitChecker` that resolves identity WITHOUT a database read.

    The base class depends on `get_current_user_or_guest`, which raises 503 when the
    `public.users` read fails — so a limiter attached to a fire-and-forget endpoint could
    itself break the very "always 200" promise that endpoint makes, before the handler ever
    ran. Analytics uses this variant; the data endpoints keep the strict one.
    """

    async def __call__(  # type: ignore[override]
        self,
        user: dict = Depends(get_identity_only_user),
        x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    ) -> None:
        key = f"{self.bucket}:{identity_key(user, x_guest_id)}"
        if not rate_limiter.is_allowed(key, self.max_requests, self.window_seconds):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please slow down.",
                headers={"Retry-After": str(self.window_seconds)},
            )


AnalyticsRateLimit = Depends(IdentityOnlyRateLimitChecker("analytics", 30, 60))

#: The investor-profile routes. Guest-allowed and row-writing, which is the combination
#: `create_chat_session` already got a limiter for: `user_id` is a uuid5 of the
#: client-chosen `X-Guest-Id`, so rotating that header mints a fresh identity and an
#: unthrottled caller could insert unbounded rows that nothing purges (the per-install
#: bucket is unreachable from account deletion, and `claim-guest-data` can never reclaim a
#: bucket it does not know about). Identity-ONLY, matching analytics: this must not be able
#: to 503 a first-run onboarding save because a `public.users` read blipped.
#: 20/min is far above the UI's ceiling — onboarding writes once, the editor once per Save.
ProfileRateLimit = Depends(IdentityOnlyRateLimitChecker("investor_profile", 20, 60))
