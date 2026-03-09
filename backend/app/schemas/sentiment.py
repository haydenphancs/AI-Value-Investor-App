"""
Sentiment Analysis schemas — response models for GET /stocks/{ticker}/sentiment.
"""

from enum import Enum

from pydantic import BaseModel


class MarketMoodLevel(str, Enum):
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    BULLISH = "bullish"


class SentimentAnalysisResponse(BaseModel):
    """Top-level response for GET /stocks/{ticker}/sentiment."""

    symbol: str
    # 24h data
    mood_score: int                    # 0-100 mood score for 24h window
    last_24h_mood: MarketMoodLevel
    social_mentions: float             # social posts in 24h
    social_mentions_change: float      # % change vs previous 24h
    news_articles: int                 # news articles in 24h
    news_articles_change: float        # % change vs previous 24h
    # 7d data
    mood_score_7d: int                 # 0-100 mood score for 7d window
    last_7d_mood: MarketMoodLevel
    social_mentions_7d: float          # social posts in 7d
    social_mentions_change_7d: float   # % change vs previous 7d
    news_articles_7d: int              # news articles in 7d
    news_articles_change_7d: float     # % change vs previous 7d
