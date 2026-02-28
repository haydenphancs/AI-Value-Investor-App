"""
Authentication Endpoints — Supabase Auth Proxy
Frontend: POST /auth/login, /auth/register, /auth/refresh, /auth/logout
"""

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client
import logging

from app.database import get_supabase
from app.dependencies import get_current_user_id
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.schemas.auth import (
    SignInRequest, SignUpRequest, RefreshTokenRequest,
    TokenResponse, AuthUserResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def sign_in(
    request: SignInRequest,
    supabase: Client = Depends(get_supabase),
):
    """Sign in via Supabase Auth, return app tokens."""
    try:
        auth_response = supabase.auth.sign_in_with_password({
            "email": request.email,
            "password": request.password,
        })

        user = auth_response.user
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Fetch app-level user row (created by DB trigger on auth.users insert)
        db_user = supabase.table("users").select("id, email").eq(
            "id", str(user.id)
        ).single().execute()

        user_id = db_user.data["id"] if db_user.data else str(user.id)

        access_token = create_access_token(data={"sub": user_id, "email": user.email})
        refresh_token = create_refresh_token(data={"sub": user_id, "email": user.email})

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=user_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sign in failed: {e}", exc_info=True)
        raise HTTPException(status_code=401, detail="Invalid credentials")


@router.post("/register", response_model=TokenResponse)
async def sign_up(
    request: SignUpRequest,
    supabase: Client = Depends(get_supabase),
):
    """Register via Supabase Auth, return app tokens."""
    try:
        auth_response = supabase.auth.sign_up({
            "email": request.email,
            "password": request.password,
            "options": {
                "data": {"display_name": request.display_name}
            },
        })

        user = auth_response.user
        if not user:
            raise HTTPException(status_code=400, detail="Registration failed")

        # DB trigger auto-creates public.users row.
        # Update display_name (trigger may not copy it from metadata).
        supabase.table("users").update({
            "display_name": request.display_name,
        }).eq("id", str(user.id)).execute()

        access_token = create_access_token(data={"sub": str(user.id), "email": user.email})
        refresh_token = create_refresh_token(data={"sub": str(user.id), "email": user.email})

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=str(user.id),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sign up failed: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail="Registration failed")


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshTokenRequest):
    """Refresh access token using refresh token."""
    try:
        payload = decode_token(request.refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")

        user_id = payload.get("sub")
        email = payload.get("email")

        access_token = create_access_token(data={"sub": user_id, "email": email})
        new_refresh = create_refresh_token(data={"sub": user_id, "email": email})

        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh,
            user_id=user_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid refresh token")


@router.post("/logout")
async def logout(user_id: str = Depends(get_current_user_id)):
    """Logout (client deletes tokens)."""
    logger.info(f"User {user_id} logged out")
    return {"message": "Successfully logged out"}
