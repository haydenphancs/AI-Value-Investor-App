"""
News Pydantic Schemas
Request and response models for news-related operations.
"""

from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List, Dict, Any
from datetime import datetime

from app.schemas.common import SentimentType, BaseResponse, TimestampMixin, SourceMetadata, AIMetadata


# News Models
# ===========

class NewsBase(BaseModel):
    """Base news model."""
    title: str = Field(..., min_length=1, max_length=1000)
    summary: Optional[str] = Field(None, max_length=5000)


class NewsArticleCreate(NewsBase):
    """Create news article (for internal use)."""
    source_name: str
    source_url: str
    external_id: Optional[str] = None
    content: Optional[str] = None
    image_url: Optional[str] = None
    author: Optional[str] = None
    published_at: datetime


class NewsArticle(BaseResponse, TimestampMixin):
    """News article response."""
    id: str

    # Source information
    source_name: str
    source_url: str
    external_id: Optional[str]

    # Content
    title: str
    summary: Optional[str]
    content: Optional[str]
    image_url: Optional[str]

    # Classification
    sentiment: Optional[SentimentType]
    relevance_score: Optional[float] = Field(None, ge=0.0, le=1.0)

    # Metadata
    author: Optional[str]
    published_at: datetime
    scraped_at: datetime

    # AI Processing
    ai_summary: Optional[str] = Field(None, description="AI-generated plain English summary")
    ai_summary_bullets: Optional[List[str]] = Field(None, max_items=3, description="3 bullet point summary")
    ai_processed: bool = False
    ai_processed_at: Optional[datetime]
    ai_model_version: Optional[str]

    # Extra computed fields
    reading_time_minutes: Optional[int] = Field(None, description="Estimated reading time")
    sentiment_emoji: Optional[str] = Field(None, description="Emoji for sentiment")
    is_breaking: Optional[bool] = Field(False, description="Flagged as breaking news")
    age_hours: Optional[int] = Field(None, description="Hours since publication")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "title": "Apple Announces Record Q4 Earnings",
                "summary": "Apple Inc. reported record earnings...",
                "sentiment": "bullish",
                "ai_summary_bullets": [
                    "Apple beat analyst expectations with $120B revenue",
                    "iPhone sales grew 15% year-over-year",
                    "Services segment continues strong growth"
                ],
                "published_at": "2025-12-17T10:00:00Z",
                "source_name": "Bloomberg"
            }
        }


class NewsWithStocks(NewsArticle):
    """News article with related stocks."""
    related_stocks: List[Dict[str, Any]] = Field(description="Stocks mentioned in article")


class NewsFeed(BaseModel):
    """News feed response with pagination."""
    articles: List[NewsArticle]
    total: int
    page: int = 1
    page_size: int = 20
    has_next: bool
    filters_applied: Optional[Dict[str, Any]] = None


class NewsFeedFilters(BaseModel):
    """Filters for news feed."""
    sentiment: Optional[SentimentType] = None
    stock_ids: Optional[List[str]] = None
    source_names: Optional[List[str]] = None
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    min_relevance_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    only_ai_processed: bool = False


# Breaking News
# =============

class BreakingNewsCreate(BaseModel):
    """Create breaking news entry."""
    news_id: str
    stock_id: str
    impact_score: float = Field(..., ge=0.0, le=1.0)
    is_price_moving: bool = False
    price_change_percent: Optional[float] = None


class BreakingNews(BaseResponse):
    """Breaking news item."""
    id: str
    news_id: str
    stock_id: str

    # Embedded data
    news: NewsArticle
    stock: Dict[str, Any]  # Stock basic info

    # Impact assessment
    impact_score: float = Field(ge=0.0, le=1.0)
    is_price_moving: bool
    price_change_percent: Optional[float]

    # Display control
    shown_in_feed: bool = False
    expires_at: datetime
    created_at: datetime

    # Extra fields
    is_active: bool = Field(description="Not expired and not shown")
    urgency_level: str = Field(description="high/medium/low based on impact")
    alert_sent: Optional[bool] = Field(False, description="Whether alert was sent to users")


# News Summarization
# ==================

class NewsSummarizationRequest(BaseModel):
    """Request to summarize news content."""
    content: str = Field(..., min_length=100)
    max_bullets: int = Field(3, ge=1, le=5)
    style: str = Field("plain_english", description="plain_english or technical")
    include_sentiment: bool = True


class NewsSummarizationResponse(BaseModel):
    """Summarized news response."""
    summary: str
    bullets: List[str]
    sentiment: Optional[SentimentType]
    sentiment_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    key_points: Optional[List[str]] = None
    mentioned_companies: Optional[List[str]] = None
    ai_metadata: AIMetadata


# News-Stock Relationships
# ========================

class NewsStockRelation(BaseModel):
    """Relationship between news and stock."""
    id: str
    news_id: str
    stock_id: str
    relevance_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    mentioned_count: int = Field(default=1, description="Times stock mentioned in article")
    sentiment_override: Optional[SentimentType] = None


# News Analytics
# ==============

class NewsAnalytics(BaseModel):
    """Analytics for news coverage."""
    stock_id: str
    date_range: Dict[str, datetime]

    # Counts
    total_articles: int
    articles_by_sentiment: Dict[str, int]
    articles_by_source: Dict[str, int]

    # Trends
    sentiment_trend: str = Field(description="improving/declining/stable")
    coverage_trend: str = Field(description="increasing/decreasing/stable")

    # Top stories
    top_stories: List[NewsArticle] = Field(max_items=5)
    breaking_news_count: int

    # Extra insights
    average_sentiment_score: Optional[float] = None
    most_active_sources: Optional[List[str]] = None
