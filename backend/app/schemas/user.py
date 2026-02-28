"""User request/response schemas matching DB users + user_credits tables."""

from pydantic import BaseModel
from typing import Optional


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    tier: str = "free"
    created_at: str
    updated_at: Optional[str] = None


class UserCreditsResponse(BaseModel):
    total: int
    used: int
    remaining: int
    resets_at: Optional[str] = None


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
