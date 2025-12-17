"""
User Pydantic Schemas
Request and response models for user-related operations.
"""

from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, Dict, Any
from datetime import datetime

from app.schemas.common import UserTier, BaseResponse, TimestampMixin


# User Models
# ===========

class UserBase(BaseModel):
    """Base user model with common fields."""
    email: EmailStr
    full_name: Optional[str] = None
    tier: UserTier = UserTier.FREE


class UserCreate(UserBase):
    """User creation request."""
    password: str = Field(..., min_length=8, description="Minimum 8 characters")

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password strength."""
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not any(c.islower() for c in v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        return v


class UserUpdate(BaseModel):
    """User profile update request."""
    full_name: Optional[str] = None
    preferred_timezone: Optional[str] = Field(None, description="IANA timezone (e.g., America/New_York)")
    notification_preferences: Optional[Dict[str, bool]] = Field(
        None,
        description="Notification settings (email, push, etc.)"
    )


class UserPreferences(BaseModel):
    """User preferences model."""
    preferred_timezone: str = "America/New_York"
    notification_preferences: Dict[str, bool] = {
        "email": True,
        "push": True,
        "breaking_news": True,
        "widget_updates": True,
        "research_complete": True
    }
    onboarding_completed: bool = False


class UserUsageStats(BaseModel):
    """User usage statistics."""
    tier: UserTier
    deep_research_used: int
    deep_research_limit: int
    deep_research_remaining: int | str  # Can be "unlimited"
    reset_at: datetime

    # Additional useful stats
    watchlist_count: Optional[int] = 0
    reports_generated: Optional[int] = 0
    chat_sessions: Optional[int] = 0
    last_activity: Optional[datetime] = None


class UserResponse(BaseResponse, TimestampMixin):
    """User response model (no sensitive data)."""
    id: str
    email: EmailStr
    full_name: Optional[str]
    tier: UserTier
    tier_start_date: Optional[datetime]
    tier_expiry_date: Optional[datetime]

    # Usage stats
    monthly_deep_research_used: int
    monthly_deep_research_limit: int
    monthly_research_reset_at: datetime

    # Preferences
    preferred_timezone: str
    notification_preferences: Dict[str, Any]
    onboarding_completed: bool

    # Timestamps
    last_login_at: Optional[datetime]

    # Extra computed fields
    is_premium: bool = Field(default=False, description="Computed: tier == premium")
    days_until_reset: Optional[int] = Field(None, description="Days until usage reset")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "email": "investor@example.com",
                "full_name": "Warren B.",
                "tier": "pro",
                "monthly_deep_research_used": 3,
                "monthly_deep_research_limit": 10,
                "monthly_research_reset_at": "2025-01-01T00:00:00Z",
                "preferred_timezone": "America/New_York",
                "onboarding_completed": True,
                "created_at": "2025-01-01T00:00:00Z"
            }
        }


class UserWithUsage(UserResponse):
    """Extended user response with detailed usage stats."""
    usage_stats: UserUsageStats


# Authentication Models
# =====================

class Token(BaseModel):
    """JWT token response."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until expiration")
    user_id: str


class TokenRefresh(BaseModel):
    """Token refresh request."""
    refresh_token: str


class LoginRequest(BaseModel):
    """User login request."""
    email: EmailStr
    password: str


class SupabaseTokenExchange(BaseModel):
    """Exchange Supabase token for app token."""
    supabase_token: str


# Account Management
# ==================

class AccountDeletion(BaseModel):
    """Account deletion confirmation."""
    confirm: bool = Field(..., description="Must be true to confirm deletion")
    feedback: Optional[str] = Field(None, description="Optional feedback")


class TierUpgrade(BaseModel):
    """Tier upgrade request."""
    new_tier: UserTier
    payment_method_id: Optional[str] = None
    billing_period: str = Field("monthly", description="monthly or annual")
