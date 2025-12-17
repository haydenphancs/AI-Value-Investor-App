"""
Widget Pydantic Schemas
Request and response models for iOS widget updates.
"""

from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List, Dict, Any
from datetime import datetime

from app.schemas.common import SentimentType, BaseResponse, TimestampMixin


# Widget Update Models
# ====================

class WidgetUpdateCreate(BaseModel):
    """Create widget update (internal)."""
    user_id: str
    headline: str = Field(..., min_length=1, max_length=200)
    sentiment: SentimentType
    emoji: str = Field(..., min_length=1, max_length=10)
    daily_trend: str = Field(..., max_length=100)

    # Optional content
    market_summary: Optional[str] = Field(None, max_length=500)
    top_movers: Optional[Dict[str, Any]] = None

    # Scheduling
    scheduled_for: Optional[datetime] = None
    published_at: Optional[datetime] = None

    # Deep linking
    deep_link_url: Optional[str] = None
    linked_report_id: Optional[str] = None


class WidgetUpdate(BaseResponse, TimestampMixin):
    """Widget update response."""
    id: str
    user_id: str

    # Main content
    headline: str
    sentiment: SentimentType
    emoji: str
    daily_trend: str

    # Extended content
    market_summary: Optional[str]
    top_movers: Optional[Dict[str, Any]]

    # Scheduling
    update_type: Optional[str]
    scheduled_for: Optional[datetime]
    published_at: Optional[datetime]

    # Deep linking
    deep_link_url: Optional[str]
    linked_report_id: Optional[str]

    # Extra computed fields
    is_published: bool = Field(description="published_at is not null")
    is_scheduled: bool = Field(description="scheduled_for is in future")
    time_until_publish: Optional[int] = Field(None, description="Seconds until publication")


class WidgetUpdateWithReport(WidgetUpdate):
    """Widget update with linked report details."""
    linked_report: Optional[Dict[str, Any]] = Field(
        None,
        description="Deep research report details if linked"
    )


# Widget Timeline Models
# ======================

class WidgetTimelineEntry(BaseModel):
    """Single entry in widget timeline for WidgetKit."""
    date: datetime
    headline: str
    sentiment: SentimentType
    emoji: str
    daily_trend: str
    deep_link_url: Optional[str] = None

    # Display priority (higher = more important)
    priority: int = Field(default=0, ge=0, le=10)


class WidgetTimeline(BaseModel):
    """Widget timeline for iOS WidgetKit."""
    entries: List[WidgetTimelineEntry]
    reload_policy: str = Field(
        default="atEnd",
        description="atEnd/after/never - WidgetKit reload policy"
    )
    next_reload_date: Optional[datetime] = None

    # Metadata
    generated_at: datetime
    valid_until: datetime
    user_id: str

    class Config:
        json_schema_extra = {
            "example": {
                "entries": [
                    {
                        "date": "2025-12-17T07:30:00Z",
                        "headline": "Markets Open Higher on Tech Earnings",
                        "sentiment": "bullish",
                        "emoji": "📈",
                        "daily_trend": "+1.2% pre-market",
                        "priority": 8
                    },
                    {
                        "date": "2025-12-17T16:00:00Z",
                        "headline": "Fed Signals Rate Hold in 2025",
                        "sentiment": "neutral",
                        "emoji": "🏛️",
                        "daily_trend": "Markets digest FOMC",
                        "priority": 9
                    }
                ],
                "reload_policy": "atEnd"
            }
        }


# Widget Configuration
# ====================

class WidgetConfig(BaseModel):
    """User widget configuration."""
    user_id: str

    # Display preferences
    show_sentiment_emoji: bool = True
    show_market_summary: bool = True
    show_top_movers: bool = True
    compact_mode: bool = False

    # Update frequency
    update_frequency: str = Field(
        default="twice_daily",
        description="twice_daily/hourly/on_demand"
    )

    # Content preferences
    focus_watchlist_only: bool = False
    exclude_sentiments: Optional[List[SentimentType]] = None

    # Notification settings
    notify_on_update: bool = True
    notify_on_breaking_news: bool = True


# Widget Analytics
# ================

class WidgetInteraction(BaseModel):
    """Track widget interaction."""
    widget_update_id: str
    user_id: str
    interaction_type: str = Field(description="view/tap/expand")
    tapped_deep_link: bool = False
    session_id: Optional[str] = None
    device_info: Optional[Dict[str, Any]] = None
    timestamp: datetime


class WidgetAnalytics(BaseModel):
    """Analytics for widget usage."""
    user_id: str
    total_updates_delivered: int
    total_views: int
    total_taps: int
    tap_through_rate: float = Field(ge=0.0, le=1.0)

    # Engagement by time
    most_engaged_time: str = Field(description="morning/afternoon/evening")
    engagement_by_sentiment: Dict[str, int]

    # Performance
    average_time_on_widget: Optional[float] = Field(None, description="Seconds")
    favorite_content_type: Optional[str] = None


# Widget Generation Request
# =========================

class WidgetGenerationRequest(BaseModel):
    """Request to generate widget update."""
    user_id: str
    force_regenerate: bool = Field(
        default=False,
        description="Force new generation even if recent update exists"
    )
    include_watchlist_only: bool = False
    custom_context: Optional[Dict[str, Any]] = Field(
        None,
        description="Custom context for generation"
    )


class WidgetGenerationResponse(BaseModel):
    """Response from widget generation."""
    widget_update: WidgetUpdate
    generation_successful: bool
    generation_time_seconds: float
    cache_hit: bool = Field(description="True if served from cache")
    next_scheduled_update: Optional[datetime]
