"""
Authentication Endpoints
Handles user authentication, registration, and token management.
Note: Primary auth is handled by Supabase Auth on iOS side.
This provides additional server-side token validation and management.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client
from pydantic import BaseModel, EmailStr
import logging

from app.database import get_supabase
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.dependencies import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response Models
# =======================

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


# Endpoints
# =========

@router.post("/token", response_model=TokenResponse)
async def create_token(
    supabase_token: str,
    supabase: Client = Depends(get_supabase)
) -> TokenResponse:
    """
    Exchange Supabase Auth token for application tokens.
    iOS app authenticates with Supabase, then calls this to get app tokens.

    Args:
        supabase_token: JWT token from Supabase Auth
        supabase: Supabase client

    Returns:
        TokenResponse: Access and refresh tokens
    """
    from app.core.security import verify_supabase_token

    # Verify Supabase token
    payload = verify_supabase_token(supabase_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Supabase token"
        )

    user_id = payload.get("sub")

    # Verify user exists in our database
    result = supabase.table("users").select("*").eq("auth_user_id", user_id).single().execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in database"
        )

    user_data = result.data

    # Create application tokens
    access_token = create_access_token(
        data={"sub": user_data["id"], "email": user_data["email"]}
    )
    refresh_token = create_refresh_token(
        data={"sub": user_data["id"], "email": user_data["email"]}
    )

    # Update last login
    supabase.table("users").update({
        "last_login_at": "now()"
    }).eq("id", user_data["id"]).execute()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user_data["id"]
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: TokenRefreshRequest,
    supabase: Client = Depends(get_supabase)
) -> TokenResponse:
    """
    Refresh access token using refresh token.

    Args:
        request: Refresh token request
        supabase: Supabase client

    Returns:
        TokenResponse: New access and refresh tokens
    """
    try:
        payload = decode_token(request.refresh_token)

        # Verify it's a refresh token
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )

        user_id = payload.get("sub")
        email = payload.get("email")

        # Create new tokens
        access_token = create_access_token(
            data={"sub": user_id, "email": email}
        )
        new_refresh_token = create_refresh_token(
            data={"sub": user_id, "email": email}
        )

        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            user_id=user_id
        )

    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )


@router.post("/logout")
async def logout(
    user_id: str = Depends(get_current_user_id)
):
    """
    Logout user (client should delete tokens).

    Args:
        user_id: Current user ID

    Returns:
        dict: Success message
    """
    # In a production app, you might want to:
    # 1. Blacklist the token in Redis
    # 2. Log the logout event
    # 3. Clear any user sessions

    logger.info(f"User {user_id} logged out")

    return {"message": "Successfully logged out"}


@router.get("/me")
async def get_current_user_info(
    user_id: str = Depends(get_current_user_id),
    supabase: Client = Depends(get_supabase)
):
    """
    Get current authenticated user information.

    Args:
        user_id: Current user ID
        supabase: Supabase client

    Returns:
        dict: User information
    """
    result = supabase.table("users").select("*").eq("id", user_id).single().execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    return result.data


@router.post("/verify")
async def verify_token(
    user_id: str = Depends(get_current_user_id)
):
    """
    Verify if token is valid.

    Args:
        user_id: Current user ID

    Returns:
        dict: Verification result
    """
    return {
        "valid": True,
        "user_id": user_id
    }
