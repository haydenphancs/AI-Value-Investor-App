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
    news_bullish: int = 0              # bullish articles in 24h
    news_bearish: int = 0              # bearish articles in 24h
    news_neutral: int = 0              # neutral articles in 24h
    # 7d data
    mood_score_7d: int                 # 0-100 mood score for 7d window
    last_7d_mood: MarketMoodLevel
    social_mentions_7d: float          # social posts in 7d
    social_mentions_change_7d: float   # % change vs previous 7d
    news_articles_7d: int              # news articles in 7d
    news_articles_change_7d: float     # % change vs previous 7d
    news_bullish_7d: int = 0           # bullish articles in 7d
    news_bearish_7d: int = 0           # bearish articles in 7d
    news_neutral_7d: int = 0           # neutral articles in 7d
    # Social data availability
    social_data_available: bool         # True if ApeWisdom has data for this ticker
    # Per-window "was this LOOKED UP" flags. `social_mentions*` are shipped non-Optional
    # floats (old builds decode a plain Double), so an unknown count still travels as 0.0
    # — these say whether that 0.0 was measured. False = the lookup failed (a DB error,
    # an ApeWisdom timeout); the client renders "—", not "0 mentions". Default True so
    # any other builder keeps the old meaning; `sentiment_service` sets both explicitly.
    social_mentions_known: bool = True
    social_mentions_7d_known: bool = True
