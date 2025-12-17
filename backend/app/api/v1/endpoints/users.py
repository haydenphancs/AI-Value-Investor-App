"""
User Management Endpoints
Handles user profile, preferences, and account management.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client
from pydantic import BaseModel
from typing import Optional
import logging

from app.database import get_supabase
from app.dependencies import get_current_user, get_current_active_user
from app.services.user_service import UserService

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response Models
# =======================

class UserProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    preferred_timezone: Optional[str] = None
    notification_preferences: Optional[dict] = None


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str]
    tier: str
    monthly_deep_research_used: int
    monthly_deep_research_limit: int
    created_at: str


# Endpoints
# =========

@router.get("/me", response_model=UserResponse)
async def get_my_profile(
    user: dict = Depends(get_current_active_user)
) -> UserResponse:
    """
    Get current user's profile.

    Args:
        user: Current user data

    Returns:
        UserResponse: User profile data
    """
    return UserResponse(**user)


@router.patch("/me")
async def update_my_profile(
    updates: UserProfileUpdate,
    user: dict = Depends(get_current_active_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Update current user's profile.

    Args:
        updates: Profile updates
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Updated user data
    """
    update_data = updates.model_dump(exclude_unset=True)

    if not update_data:
        return user

    result = supabase.table("users").update(update_data).eq("id", user["id"]).execute()

    return result.data[0] if result.data else user


@router.get("/me/usage")
async def get_my_usage(
    user: dict = Depends(get_current_active_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get user's usage statistics and limits.
    Section 5.5 - Business Rules

    Args:
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Usage statistics
    """
    user_service = UserService(supabase)

    # Check if user has credits
    has_credits = await user_service.check_user_credits(user["id"])

    return {
        "tier": user["tier"],
        "has_credits": has_credits,
        "deep_research": {
            "used": user.get("monthly_deep_research_used", 0),
            "limit": user.get("monthly_deep_research_limit", 1),
            "remaining": (
                user.get("monthly_deep_research_limit", 1) - user.get("monthly_deep_research_used", 0)
                if user.get("monthly_deep_research_limit", 1) != -1
                else "unlimited"
            ),
            "reset_at": user.get("last_credit_reset_at")
        }
    }


@router.get("/me/stats")
async def get_my_stats(
    user: dict = Depends(get_current_active_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get comprehensive user statistics.
    Uses UserService for detailed stats including activity tracking.

    Args:
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Comprehensive user statistics
    """
    user_service = UserService(supabase)

    stats = await user_service.get_user_stats(user["id"])

    if not stats:
        raise HTTPException(
            status_code=404,
            detail="User statistics not found"
        )

    return stats


@router.delete("/me")
async def delete_my_account(
    user: dict = Depends(get_current_active_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Soft delete user account.

    Args:
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Deletion confirmation
    """
    # Soft delete by setting deleted_at timestamp
    supabase.table("users").update({
        "deleted_at": "now()"
    }).eq("id", user["id"]).execute()

    return {"message": "Account deleted successfully"}
