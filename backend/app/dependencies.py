"""
FastAPI Dependencies
Reusable dependency functions for authentication, authorization, and more.
"""

from typing import Optional
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import Client
import logging

from app.database import get_db, get_supabase
from app.core.security import decode_token, verify_supabase_token, rate_limiter
from app.config import settings

logger = logging.getLogger(__name__)

# HTTP Bearer token security scheme
security = HTTPBearer()


# Authentication Dependencies
# ===========================

async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """
    Extract and validate user ID from JWT token.
    Supports both custom JWT tokens and Supabase Auth tokens.

    Args:
        credentials: HTTP Bearer credentials from header

    Returns:
        str: User ID (UUID)

    Raises:
        HTTPException: If token is invalid or expired
    """
    token = credentials.credentials

    # Try custom JWT first
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if user_id:
            return user_id
    except Exception as e:
        logger.debug(f"Custom JWT validation failed: {e}")

    # Try Supabase Auth token
    try:
        payload = verify_supabase_token(token)
        if payload:
            user_id = payload.get("sub")
            if user_id:
                return user_id
    except Exception as e:
        logger.debug(f"Supabase token validation failed: {e}")

    # Both failed
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    user_id: str = Depends(get_current_user_id),
    supabase: Client = Depends(get_supabase)
) -> dict:
    """
    Get current user details from database.

    Args:
        user_id: User ID from token
        supabase: Supabase client

    Returns:
        dict: User data

    Raises:
        HTTPException: If user not found
    """
    try:
        result = supabase.table("users").select("*").eq("id", user_id).single().execute()
        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        return result.data
    except Exception as e:
        logger.error(f"Error fetching user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching user data"
        )


async def get_optional_user_id(
    authorization: Optional[str] = Header(None)
) -> Optional[str]:
    """
    Optional authentication - returns user ID if token present, None otherwise.
    Useful for endpoints that work both authenticated and unauthenticated.

    Args:
        authorization: Authorization header

    Returns:
        Optional[str]: User ID or None
    """
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


# Authorization Dependencies (User Tiers)
# =======================================

class UserTierChecker:
    """
    Dependency class to check user tier permissions.
    Section 5.5 - Business Rules
    """

    def __init__(self, required_tier: str):
        """
        Args:
            required_tier: Minimum tier required ('free', 'pro', 'premium')
        """
        self.required_tier = required_tier
        self.tier_hierarchy = {"free": 0, "pro": 1, "premium": 2}

    async def __call__(
        self,
        user: dict = Depends(get_current_user)
    ) -> dict:
        """
        Check if user has required tier.

        Args:
            user: Current user data

        Returns:
            dict: User data if authorized

        Raises:
            HTTPException: If user doesn't have required tier
        """
        user_tier = user.get("tier", "free")
        user_tier_level = self.tier_hierarchy.get(user_tier, 0)
        required_tier_level = self.tier_hierarchy.get(self.required_tier, 0)

        if user_tier_level < required_tier_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This feature requires {self.required_tier} tier or higher"
            )

        return user


# Rate Limiting Dependencies
# ==========================

class RateLimitChecker:
    """
    Dependency to check rate limits.
    Section 5.1 - Performance Requirements
    """

    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: int = 60
    ):
        """
        Args:
            max_requests: Maximum requests allowed
            window_seconds: Time window in seconds
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def __call__(
        self,
        user_id: str = Depends(get_current_user_id)
    ):
        """
        Check rate limit for user.

        Args:
            user_id: Current user ID

        Raises:
            HTTPException: If rate limit exceeded
        """
        if not rate_limiter.is_allowed(
            user_id,
            self.max_requests,
            self.window_seconds
        ):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please try again later.",
                headers={"Retry-After": str(self.window_seconds)}
            )


# Usage Limit Dependencies
# ========================

async def check_deep_research_limit(
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
) -> dict:
    """
    Check if user has remaining deep research quota.
    Section 5.5 - Business Rules

    Args:
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: User data if quota available

    Raises:
        HTTPException: If quota exceeded
    """
    tier = user.get("tier", "free")
    used = user.get("monthly_deep_research_used", 0)

    # Determine limit based on tier
    if tier == "free":
        limit = settings.FREE_TIER_DEEP_RESEARCH_LIMIT
    elif tier == "pro":
        limit = settings.PRO_TIER_DEEP_RESEARCH_LIMIT
    elif tier == "premium":
        limit = settings.PREMIUM_TIER_DEEP_RESEARCH_LIMIT
    else:
        limit = settings.FREE_TIER_DEEP_RESEARCH_LIMIT

    # -1 means unlimited
    if limit == -1:
        return user

    if used >= limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Monthly deep research limit reached. Upgrade to get more reports.",
            headers={
                "X-Limit": str(limit),
                "X-Used": str(used),
                "X-Tier": tier
            }
        )

    return user


# Common dependency combinations
# ==============================

async def get_current_active_user(
    user: dict = Depends(get_current_user)
) -> dict:
    """
    Get current user and ensure account is active (not soft-deleted).

    Args:
        user: Current user data

    Returns:
        dict: User data if active

    Raises:
        HTTPException: If account is deleted
    """
    if user.get("deleted_at"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account has been deactivated"
        )
    return user


# Convenience dependency combinations
RequiresFreeUser = Depends(get_current_active_user)
RequiresProUser = Depends(UserTierChecker("pro"))
RequiresPremiumUser = Depends(UserTierChecker("premium"))
RequiresDeepResearchQuota = Depends(check_deep_research_limit)
StandardRateLimit = Depends(RateLimitChecker(60, 60))  # 60 requests per minute
