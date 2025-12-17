"""
Common Pydantic Schemas
Shared models and base classes used across the application.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Any, Dict, List
from datetime import datetime
from enum import Enum


# Enums matching database schema
# ==============================

class UserTier(str, Enum):
    """User subscription tier."""
    FREE = "free"
    PRO = "pro"
    PREMIUM = "premium"


class SentimentType(str, Enum):
    """Sentiment classification for news and analysis."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class ReportStatus(str, Enum):
    """Status of research report generation."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class InvestorPersona(str, Enum):
    """Investor personality types for research analysis."""
    BUFFETT = "buffett"
    ACKMAN = "ackman"
    MUNGER = "munger"
    LYNCH = "lynch"
    GRAHAM = "graham"


class ContentType(str, Enum):
    """Type of educational content."""
    BOOK = "book"
    ARTICLE = "article"


class SessionType(str, Enum):
    """Type of chat session."""
    EDUCATION = "education"
    STOCK_ANALYSIS = "stock_analysis"
    GENERAL = "general"


# Base Response Models
# ====================

class BaseResponse(BaseModel):
    """Base response model with common fields."""
    model_config = ConfigDict(from_attributes=True)


class SuccessResponse(BaseModel):
    """Standard success response."""
    success: bool = True
    message: str
    data: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    """Standard error response."""
    success: bool = False
    error: str
    detail: Optional[str] = None
    error_code: Optional[str] = None


class PaginatedResponse(BaseModel):
    """Paginated list response."""
    items: List[Any]
    total: int
    page: int
    page_size: int
    has_next: bool
    has_prev: bool


# Common Field Types
# ==================

class TimestampMixin(BaseModel):
    """Mixin for timestamp fields."""
    created_at: datetime
    updated_at: Optional[datetime] = None


class SoftDeleteMixin(BaseModel):
    """Mixin for soft delete functionality."""
    deleted_at: Optional[datetime] = None
    is_deleted: bool = Field(default=False, description="Computed from deleted_at")


# Metadata Models
# ===============

class APIMetadata(BaseModel):
    """Metadata for API responses."""
    request_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    version: str = "1.0"

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "req_123abc",
                "timestamp": "2025-12-17T10:30:00Z",
                "version": "1.0"
            }
        }
    )


class AIMetadata(BaseModel):
    """Metadata for AI-generated content."""
    model_name: str
    model_version: Optional[str] = None
    tokens_used: Optional[int] = None
    generation_time_seconds: Optional[float] = None
    cost_usd: Optional[float] = None
    temperature: Optional[float] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "model_name": "gemini-1.5-pro",
                "tokens_used": 1234,
                "generation_time_seconds": 2.5,
                "cost_usd": 0.0025
            }
        }
    )


class SourceMetadata(BaseModel):
    """Metadata for content sources."""
    source_name: str
    source_url: Optional[str] = None
    source_type: Optional[str] = None
    scraped_at: Optional[datetime] = None
    reliability_score: Optional[float] = Field(None, ge=0.0, le=1.0)


# Validation Helpers
# ==================

class TickerSymbol(BaseModel):
    """Validated stock ticker symbol."""
    symbol: str = Field(..., min_length=1, max_length=10, pattern=r"^[A-Z]+$")


class Percentage(BaseModel):
    """Validated percentage value."""
    value: float = Field(..., ge=-100.0, le=100.0)


# Request Helpers
# ===============

class PaginationParams(BaseModel):
    """Standard pagination parameters."""
    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    page_size: int = Field(default=20, ge=1, le=100, description="Items per page")

    @property
    def offset(self) -> int:
        """Calculate offset for database queries."""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """Alias for page_size."""
        return self.page_size


class DateRangeParams(BaseModel):
    """Date range filter parameters."""
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

    def validate_range(self) -> bool:
        """Validate that start_date is before end_date."""
        if self.start_date and self.end_date:
            return self.start_date <= self.end_date
        return True
