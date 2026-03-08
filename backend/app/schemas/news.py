"""News schemas matching DB news_articles table."""

from enum import Enum
from pydantic import BaseModel, validator
from typing import Optional, List


class SentimentValue(str, Enum):
    """Strict sentiment values — the only three the frontend accepts."""
    POSITIVE = "Positive"
    NEGATIVE = "Negative"
    NEUTRAL = "Neutral"


class NewsArticleResponse(BaseModel):
    id: str
    headline: str
    summary: Optional[str] = None
    source_name: Optional[str] = None
    source_logo_url: Optional[str] = None
    source_is_verified: bool = False
    sentiment: Optional[str] = None
    published_at: Optional[str] = None
    thumbnail_url: Optional[str] = None
    related_tickers: Optional[List[str]] = None
    category: Optional[str] = None
    is_breaking: bool = False
    article_url: Optional[str] = None
    insight_summary: Optional[str] = None
    insight_key_points: Optional[List[str]] = None
    key_takeaways: Optional[List[str]] = None
    read_time_minutes: Optional[int] = None
    created_at: Optional[str] = None


class NewsFeedResponse(BaseModel):
    articles: List[NewsArticleResponse]
    page: int
    per_page: int
    total: Optional[int] = None
    has_more: bool = False


# ── Ticker-specific AI-enriched news ──────────────────────────────────

class TickerNewsArticleResponse(BaseModel):
    id: str
    headline: str
    summary: Optional[str] = None
    summary_bullets: List[str] = []
    sentiment: Optional[SentimentValue] = None
    sentiment_confidence: int = 0
    source_name: Optional[str] = None
    source_logo_url: Optional[str] = None
    published_at: Optional[str] = None
    thumbnail_url: Optional[str] = None
    article_url: Optional[str] = None
    related_tickers: List[str] = []
    ai_processed: bool = False

    @validator("sentiment", pre=True, always=True)
    def coerce_sentiment(cls, v):
        """Coerce raw sentiment strings to SentimentValue or None."""
        if v is None:
            return None
        if isinstance(v, SentimentValue):
            return v
        s = str(v).strip().lower()
        if s in ("positive", "bullish"):
            return SentimentValue.POSITIVE
        if s in ("negative", "bearish"):
            return SentimentValue.NEGATIVE
        if s in ("neutral", "none", "mixed", ""):
            return SentimentValue.NEUTRAL
        return SentimentValue.NEUTRAL


class TickerNewsFeedResponse(BaseModel):
    articles: List[TickerNewsArticleResponse]
    ticker: str
    cached: bool = False
    cache_age_seconds: Optional[int] = None
