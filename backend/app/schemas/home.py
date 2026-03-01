"""
Home feed schemas — aggregated response for the HomeView screen.

All field names use snake_case. The Swift frontend decodes via
explicit CodingKeys (snake_case raw values), so no aliases needed.
"""

from pydantic import BaseModel
from typing import Optional, List


class MarketTickerResponse(BaseModel):
    name: str
    symbol: str
    type: str  # "index", "stock", "crypto", "commodity", "etf"
    price: float
    change_percent: float
    sparkline_data: List[float]


class MarketInsightResponse(BaseModel):
    headline: str
    bullet_points: List[str]
    sentiment: str  # "Bullish", "Bearish", "Neutral"
    updated_at: str  # ISO 8601 (YYYY-MM-DDTHH:MM:SSZ)


class DailyBriefingItemResponse(BaseModel):
    type: str  # "whales_alert", "earnings_alert", "whales_following", "wiser_trending"
    title: str
    subtitle: str
    date: Optional[str] = None  # ISO 8601 or null
    badge_text: Optional[str] = None  # e.g. "24\nFEB"


class RecentResearchResponse(BaseModel):
    id: str
    stock_ticker: str
    stock_name: str
    company_logo_name: str
    persona: str  # Display name: "Warren Buffett", "Peter Lynch", etc.
    headline: str
    summary: str
    rating: float  # 0-100 scale
    fair_value: float
    created_at: str  # ISO 8601
    gradient_colors: List[str]  # e.g. ["C74634", "F80000"]


class HomeFeedResponse(BaseModel):
    market_tickers: List[MarketTickerResponse]
    market_insight: Optional[MarketInsightResponse] = None
    daily_briefings: List[DailyBriefingItemResponse]
    recent_research: List[RecentResearchResponse]
