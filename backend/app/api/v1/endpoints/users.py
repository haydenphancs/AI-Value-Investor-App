"""
User Endpoints
Frontend: GET /users/me, GET /users/me/credits, PATCH /users/me
"""

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client
import logging

from app.database import get_supabase
from app.dependencies import (
    get_current_user,
    get_current_user_or_guest,  # TEMP: guest fallback
    GUEST_USER_ID,
)
from app.schemas.user import UserResponse, UserCreditsResponse, UpdateProfileRequest
from app.services.credit_service import CreditService, CreditServiceUnavailable

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    user: dict = Depends(get_current_user),
):
    """Get current user profile."""
    return UserResponse(
        id=user["id"],
        email=user["email"],
        display_name=user.get("display_name"),
        avatar_url=user.get("avatar_url"),
        tier=user.get("tier", "free"),
        created_at=user["created_at"],
        updated_at=user.get("updated_at"),
    )


@router.get("/me/credits", response_model=UserCreditsResponse)
async def get_user_credits(
    user: dict = Depends(get_current_user_or_guest),  # TEMP: guest fallback
    supabase: Client = Depends(get_supabase),
):
    """Get current user's credit balance from user_credits table."""
    # Roll the monthly allocation BEFORE reading so a returning user in a NEW month sees their
    # fresh balance. The lazy reset otherwise only fires on a spend (chat/report), so a user who
    # depleted last month and doesn't chat would see a stale remaining=0 here AND the report
    # Generate button would stay disabled. Authenticated non-guest only (ensure_credit_period
    # skips the guest sentinel). Best-effort: a transient RPC blip falls through to the raw read.
    if user["id"] != GUEST_USER_ID:
        try:
            CreditService().ensure_period(user["id"])
        except CreditServiceUnavailable:
            logger.warning(
                "ensure_period unavailable for user=%s — serving raw balance", user["id"]
            )
    try:
        result = supabase.table("user_credits").select(
            "total, used, remaining, resets_at"
        ).eq("user_id", user["id"]).single().execute()

        if not result.data:
            # No credit row yet → optimistic Free-tier allocation (mirrors
            # plan_credits.free = 50; migration 100). The signup trigger normally
            # seeds a row, so this is a rare safety net.
            return UserCreditsResponse(total=50, used=0, remaining=50)

        return UserCreditsResponse(**result.data)
    except Exception as e:
        logger.error(f"Failed to fetch credits: {e}")
        return UserCreditsResponse(total=50, used=0, remaining=50)


@router.patch("/me", response_model=UserResponse)
async def update_profile(
    request: UpdateProfileRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Update current user profile (display_name, avatar_url)."""
    update_data = request.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = supabase.table("users").update(update_data).eq(
        "id", user["id"]
    ).execute()

    updated = result.data[0] if result.data else user
    return UserResponse(
        id=updated["id"],
        email=updated["email"],
        display_name=updated.get("display_name"),
        avatar_url=updated.get("avatar_url"),
        tier=updated.get("tier", "free"),
        created_at=updated["created_at"],
        updated_at=updated.get("updated_at"),
    )
