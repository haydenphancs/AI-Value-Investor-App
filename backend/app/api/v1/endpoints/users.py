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
    user: dict = Depends(get_current_active_user)
):
    """
    Get user's usage statistics and limits.
    Section 5.5 - Business Rules

    Args:
        user: Current user data

    Returns:
        dict: Usage statistics
    """
    return {
        "tier": user["tier"],
        "deep_research": {
            "used": user["monthly_deep_research_used"],
            "limit": user["monthly_deep_research_limit"],
            "remaining": (
                user["monthly_deep_research_limit"] - user["monthly_deep_research_used"]
                if user["monthly_deep_research_limit"] != -1
                else "unlimited"
            ),
            "reset_at": user["monthly_research_reset_at"]
        }
    }


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
