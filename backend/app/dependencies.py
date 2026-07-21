"""
FastAPI Dependencies
Auth, rate limiting, and utility dependencies.
"""

from typing import Optional
import uuid
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import Client
import logging

from app.database import get_supabase
from app.core.security import decode_token, verify_supabase_token, rate_limiter

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


async def get_current_user(
    user_id: str = Depends(get_current_user_id),
    supabase: Client = Depends(get_supabase)
) -> dict:
    """Get current user record from DB."""
    try:
        result = supabase.table("users").select("*").eq("id", user_id).single().execute()
        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
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
            try:
                result = supabase.table("users").select("*").eq("id", user_id).single().execute()
                if result.data:
                    return result.data
            except Exception:
                pass

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
    ):
        # TEMP: while research/credits endpoints accept the GUEST_USER_ID,
        # the rate limit must not 401 on missing auth. Bucket all
        # unauthenticated callers together under "guest" so a single
        # abusive guest can't drown out everyone else.
        key = user_id or f"guest:{GUEST_USER_ID}"
        if not rate_limiter.is_allowed(key, self.max_requests, self.window_seconds):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please try again later.",
                headers={"Retry-After": str(self.window_seconds)}
            )


StandardRateLimit = Depends(RateLimitChecker(60, 60))
