"""
FastAPI Dependencies
Auth, rate limiting, and utility dependencies.
"""

from typing import Optional
from datetime import datetime, timezone
import uuid
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import Client
import logging

from app.database import get_supabase
from app.core.security import decode_token, verify_supabase_token, rate_limiter
from app.config import settings

logger = logging.getLogger(__name__)

security = HTTPBearer()


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """Extract and validate user ID from JWT (custom or Supabase Auth)."""
    token = credentials.credentials

    # Try custom JWT first
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if user_id:
            return user_id
    except Exception:
        pass

    # Try Supabase Auth token
    try:
        payload = verify_supabase_token(token)
        if payload:
            user_id = payload.get("sub")
            if user_id:
                return user_id
    except Exception:
        pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


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
        payload = decode_token(token)
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

    if issued_dt < changed_dt:
        logger.info(
            "Rejecting token for user=%s: issued %s, password changed %s",
            user_row.get("id"), issued_dt.isoformat(), changed_dt.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your password was changed. Please sign in again.",
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    user_id: str = Depends(get_current_user_id),
    supabase: Client = Depends(get_supabase)
) -> dict:
    """Get current user record from DB.

    Also enforces password-change token invalidation: a token minted before the account's
    last password change is rejected here. Free to check — the `select("*")` below already
    returns `password_changed_at` (migration 105), so there is no extra round-trip.
    """
    try:
        result = supabase.table("users").select("*").eq("id", user_id).single().execute()
        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        _reject_if_password_changed_since_issue(credentials.credentials, result.data)
        return result.data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user: {e}")
        raise HTTPException(status_code=500, detail="Error fetching user data")


async def get_optional_user_id(
    authorization: Optional[str] = Header(None)
) -> Optional[str]:
    """Optional auth - returns user_id if token present, None otherwise."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.replace("Bearer ", "")
    try:
        payload = decode_token(token)
        return payload.get("sub")
    except Exception:
        try:
            payload = verify_supabase_token(token)
            return payload.get("sub") if payload else None
        except Exception:
            return None


# TEMP: shared guest user used by research/credits endpoints while the
# iOS login UI is not yet built. Backed by a real auth.users row +
# public.users row + user_credits row (50 credits seeded). Switch back
# to strict get_current_user once SignInView is wired into RootView.
GUEST_USER_ID = "00000000-0000-4000-8000-00000000dead"


async def get_current_user_or_guest(
    authorization: Optional[str] = Header(None),
    supabase: Client = Depends(get_supabase),
) -> dict:
    """Return authenticated user if token present, otherwise a guest user dict."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")
        user_id = None
        try:
            payload = decode_token(token)
            user_id = payload.get("sub")
        except Exception:
            try:
                payload = verify_supabase_token(token)
                user_id = payload.get("sub") if payload else None
            except Exception:
                pass

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
                    "users read failed for authenticated user=%s: %s: %s",
                    user_id, type(e).__name__, e,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Temporarily unable to load your account. Please try again.",
                )
            rows = result.data or []
            if rows:
                return rows[0]
            # Valid token but no public.users row (rare — the signup trigger seeds it).
            # Fall through to guest as before rather than 500 a first-launch edge.

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


async def get_learn_identity(
    authorization: Optional[str] = Header(None),
    x_guest_id: Optional[str] = Header(None, alias="X-Guest-Id"),
    supabase: Client = Depends(get_supabase),
) -> dict:
    """Identity for the LEARN routes: a real account, else a PER-INSTALL guest.

    Deliberately scoped to Learn rather than replacing
    :func:`get_current_user_or_guest` everywhere: research / credits / portfolios
    hang off a seeded ``GUEST_USER_ID`` row (with real credits), and pointing
    those at a synthetic id would break them. Learn progress has no such
    dependency — ``user_learn_progress.user_id`` is a bare uuid column with no
    foreign key — so it can be partitioned safely.
    """
    user = await get_current_user_or_guest(authorization, supabase)
    if user.get("id") != GUEST_USER_ID:
        return user  # a real signed-in account always wins
    return {
        "id": guest_user_id_for(x_guest_id),
        "email": "guest@local",
        "tier": "free",
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
    ``chat_sessions.user_id`` (that column is FK-bound to ``public.users`` and stays
    ``GUEST_USER_ID`` for guests). Chat-history isolation between guests is a separate
    concern that resolves when real login ships.
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
AnalyticsRateLimit = Depends(IdentityRateLimitChecker("analytics", 30, 60))
