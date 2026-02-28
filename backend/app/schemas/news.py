"""News schemas matching DB news_articles table."""

from pydantic import BaseModel
from typing import Optional, List


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
