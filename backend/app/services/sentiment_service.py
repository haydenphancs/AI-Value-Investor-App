"""
Sentiment Analysis Service — aggregates FMP social sentiment and
Supabase news sentiment to compute a 0-100 mood score.

Pattern follows analyst_service.py: parallel data fetches,
in-memory caching, helper functions for derived computations.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import FMPClient, get_fmp_client
from app.schemas.sentiment import (
    MarketMoodLevel,
    SentimentAnalysisResponse,
)

logger = logging.getLogger(__name__)

# ── In-memory cache (same pattern as analyst_service) ─────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 900  # 15 minutes


def _cache_get(key: str, ttl: float = _CACHE_TTL) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > ttl:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any):
    _cache[key] = (time.time(), value)


# ── Service ───────────────────────────────────────────────────────

class SentimentService:

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()
        self.supabase = get_supabase()

    async def get_sentiment(self, ticker: str) -> SentimentAnalysisResponse:
        ticker = ticker.upper()

        cached = _cache_get(f"sentiment:{ticker}")
        if cached is not None:
            return cached

        # Parallel fetch: FMP social + FMP news
        results = await asyncio.gather(
            self._fetch_social(ticker),
            self._fetch_news(ticker),
            return_exceptions=True,
        )

        social_raw = results[0] if not isinstance(results[0], Exception) else []
        news_rows = results[1] if not isinstance(results[1], Exception) else []

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(f"Sentiment data fetch {i} failed for {ticker}: {r}")

        logger.info(
            f"Sentiment data for {ticker}: "
            f"social_records={len(social_raw)}, news_rows={len(news_rows)}"
        )

        # ── 24-hour window ────────────────────────────────────────
        news_score_24h, news_cur_24h, news_prev_24h = self._compute_news_score(
            news_rows, hours=24
        )
        social_score_24h, social_cur_24h, social_prev_24h = self._compute_social_score(
            social_raw, days=1
        )
        has_news_24h = news_cur_24h > 0
        has_social_24h = social_cur_24h > 0
        combined_24h = self._combine_scores(
            news_score_24h, social_score_24h, has_social_24h, has_news_24h
        )

        # ── 7-day window ──────────────────────────────────────────
        news_score_7d, news_cur_7d, news_prev_7d = self._compute_news_score(
            news_rows, hours=168  # 7 * 24
        )
        social_score_7d, social_cur_7d, social_prev_7d = self._compute_social_score(
            social_raw, days=7
        )
        has_news_7d = news_cur_7d > 0
        has_social_7d = social_cur_7d > 0
        combined_7d = self._combine_scores(
            news_score_7d, social_score_7d, has_social_7d, has_news_7d
        )

        response = SentimentAnalysisResponse(
            symbol=ticker,
            # 24h
            mood_score=combined_24h,
            last_24h_mood=self._score_to_mood(combined_24h),
            social_mentions=social_cur_24h,
            social_mentions_change=self._pct_change(social_cur_24h, social_prev_24h),
            news_articles=news_cur_24h,
            news_articles_change=self._pct_change(float(news_cur_24h), float(news_prev_24h)),
            # 7d
            mood_score_7d=combined_7d,
            last_7d_mood=self._score_to_mood(combined_7d),
            social_mentions_7d=social_cur_7d,
            social_mentions_change_7d=self._pct_change(social_cur_7d, social_prev_7d),
            news_articles_7d=news_cur_7d,
            news_articles_change_7d=self._pct_change(float(news_cur_7d), float(news_prev_7d)),
        )

        _cache_set(f"sentiment:{ticker}", response)
        return response

    # ── Data fetching ─────────────────────────────────────────────

    async def _fetch_social(self, ticker: str) -> List[Dict[str, Any]]:
        try:
            return await self.fmp.get_social_sentiment(ticker)
        except Exception as e:
            logger.warning(f"Social sentiment fetch failed for {ticker}: {e}")
            return []

    async def _fetch_news(self, ticker: str) -> List[Dict[str, Any]]:
        """
        Fetch news articles directly from FMP for the last 14 days.

        Uses date-bounded queries with limit=1000 to capture ALL articles
        for accurate counting. Falls back to Supabase cache on failure.
        """
        try:
            now = datetime.now(timezone.utc)
            from_date = (now - timedelta(days=14)).strftime("%Y-%m-%d")
            to_date = now.strftime("%Y-%m-%d")

            articles = await self.fmp.get_stock_news(
                ticker=ticker,
                limit=1000,
                from_date=from_date,
                to_date=to_date,
            )
            return articles if articles else []
        except Exception as e:
            logger.warning(
                f"FMP news fetch failed for {ticker}, falling back to cache: {e}"
            )
            return await self._fetch_news_from_cache(ticker)

    async def _fetch_news_from_cache(self, ticker: str) -> List[Dict[str, Any]]:
        """Fallback: fetch from Supabase ticker_news_cache."""
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
            result = (
                self.supabase.table("ticker_news_cache")
                .select("published_at, sentiment, sentiment_confidence, ai_processed")
                .eq("ticker", ticker)
                .gte("published_at", cutoff)
                .order("published_at", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.warning(f"News cache fallback also failed for {ticker}: {e}")
            return []

    # ── News scoring ──────────────────────────────────────────────

    def _compute_news_score(
        self, articles: List[Dict], hours: int
    ) -> Tuple[int, int, int]:
        """
        Returns (score_0_100, current_article_count, previous_article_count).

        Handles both FMP direct format (publishedDate) and Supabase cache
        format (published_at) for backward compatibility.
        """
        now = datetime.now(timezone.utc)
        current_cutoff = (now - timedelta(hours=hours)).isoformat()
        previous_cutoff = (now - timedelta(hours=hours * 2)).isoformat()

        def _pub_date(a: Dict) -> str:
            return a.get("publishedDate") or a.get("published_at") or ""

        def _sentiment(a: Dict) -> str:
            return (a.get("sentiment") or "").lower()

        current = [a for a in articles if _pub_date(a) >= current_cutoff]
        previous = [
            a for a in articles
            if previous_cutoff <= _pub_date(a) < current_cutoff
        ]

        # Score articles with a sentiment value (FMP Premium or AI-enriched)
        enriched = [a for a in current if _sentiment(a)]
        logger.info(
            f"News scoring (hours={hours}): "
            f"total_fetched={len(articles)}, current={len(current)}, "
            f"enriched={len(enriched)}, previous={len(previous)}"
        )
        if not enriched:
            return 50, len(current), len(previous)

        pos = sum(
            1 for a in enriched if _sentiment(a) in ("positive", "bullish")
        )
        neg = sum(
            1 for a in enriched if _sentiment(a) in ("negative", "bearish")
        )
        neu = len(enriched) - pos - neg

        total = len(enriched)
        raw = (pos * 100 + neu * 50 + neg * 0) / total
        return max(0, min(100, round(raw))), len(current), len(previous)

    # ── Social scoring ────────────────────────────────────────────

    def _compute_social_score(
        self, social_data: List[Dict], days: int
    ) -> Tuple[int, float, float]:
        """
        Returns (score_0_100, current_mentions, previous_mentions).
        """
        if not social_data:
            return 50, 0.0, 0.0

        today = datetime.now(timezone.utc).date()
        current_cutoff = (today - timedelta(days=days)).isoformat()
        previous_cutoff = (today - timedelta(days=days * 2)).isoformat()

        current_records = [
            r for r in social_data
            if r.get("date") and r["date"][:10] >= current_cutoff
        ]
        previous_records = [
            r for r in social_data
            if r.get("date")
            and previous_cutoff <= r["date"][:10] < current_cutoff
        ]

        prev_mentions = sum(
            (r.get("stocktwitsPosts", 0) or 0)
            + (r.get("twitterPosts", 0) or 0)
            + (r.get("stocktwitsComments", 0) or 0)
            + (r.get("twitterComments", 0) or 0)
            for r in previous_records
        )

        if not current_records:
            return 50, 0.0, prev_mentions

        sentiments: List[float] = []
        total_mentions = 0.0

        for r in current_records:
            sw_sent = r.get("stocktwitsSentiment", 0) or 0
            tw_sent = r.get("twitterSentiment", 0) or 0
            sw_posts = r.get("stocktwitsPosts", 0) or 0
            tw_posts = r.get("twitterPosts", 0) or 0
            sw_comments = r.get("stocktwitsComments", 0) or 0
            tw_comments = r.get("twitterComments", 0) or 0

            posts = sw_posts + tw_posts
            volume = posts + sw_comments + tw_comments
            total_mentions += volume

            if posts > 0:
                weighted = (sw_sent * sw_posts + tw_sent * tw_posts) / posts
                sentiments.append(weighted)

        if not sentiments:
            return 50, total_mentions, prev_mentions

        avg = sum(sentiments) / len(sentiments)  # -1 to 1
        score = max(0, min(100, round((avg + 1) * 50)))
        return score, total_mentions, prev_mentions

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _combine_scores(
        news_score: int,
        social_score: int,
        has_social: bool,
        has_news: bool,
    ) -> int:
        if has_news and has_social:
            return max(0, min(100, round(news_score * 0.6 + social_score * 0.4)))
        if has_news:
            return news_score
        if has_social:
            return social_score
        return 50

    @staticmethod
    def _score_to_mood(score: int) -> MarketMoodLevel:
        if score <= 30:
            return MarketMoodLevel.BEARISH
        if score <= 70:
            return MarketMoodLevel.NEUTRAL
        return MarketMoodLevel.BULLISH

    @staticmethod
    def _pct_change(current: float, previous: float) -> float:
        if previous == 0:
            return 100.0 if current > 0 else 0.0
        return round(((current - previous) / previous) * 100, 1)


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[SentimentService] = None


def get_sentiment_service() -> SentimentService:
    global _service
    if _service is None:
        _service = SentimentService()
    return _service
