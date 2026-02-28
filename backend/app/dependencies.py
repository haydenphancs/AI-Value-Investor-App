"""
FastAPI Dependencies
Auth, rate limiting, and utility dependencies.
"""

from typing import Optional
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


class RateLimitChecker:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def __call__(self, user_id: str = Depends(get_current_user_id)):
        if not rate_limiter.is_allowed(user_id, self.max_requests, self.window_seconds):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please try again later.",
                headers={"Retry-After": str(self.window_seconds)}
            )


StandardRateLimit = Depends(RateLimitChecker(60, 60))
