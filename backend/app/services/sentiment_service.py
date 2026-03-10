"""
Sentiment Analysis Service — aggregates FMP social sentiment and
news sentiment to compute a 0-100 mood score.

Uses 4 parallel FMP data sources:
  1. social-sentiments/change (hourly social data)
  2. social-sentiments/historical (daily social data, supplement)
  3. news/stock (raw news articles, high limit)
  4. stock-news-sentiments-rss-feed (news WITH sentiment scores)

Three-tier news scoring ensures every article gets scored:
  Tier 1: sentimentScore float from RSS feed (-1 to 1)
  Tier 2: sentiment string label (Bullish/Bearish/Neutral)
  Tier 3: keyword-based classification on title + text
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

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


# ── Keyword-based sentiment classifier (fallback) ────────────────

_BULLISH_KEYWORDS: Set[str] = {
    # Price action
    "surge", "surges", "surging", "soar", "soars", "soaring",
    "rally", "rallies", "rallying", "jump", "jumps", "jumping",
    "gain", "gains", "gaining", "rise", "rises", "rising",
    "climb", "climbs", "climbing", "rebound", "rebounds",
    "breakout", "highs", "record", "unstoppable",
    # Analyst/rating
    "upgrade", "upgrades", "upgraded", "outperform", "buy",
    "overweight", "bullish", "top-pick", "conviction",
    # Fundamentals
    "beat", "beats", "beating", "exceed", "exceeds", "exceeded",
    "strong", "robust", "growth", "profit", "profitable",
    "revenue", "earnings", "dividend", "innovation",
    # Sentiment
    "optimistic", "optimism", "upside", "positive", "boom",
    "booming", "momentum", "attractive", "opportunity",
    # Institutional
    "acquires", "acquired", "raises", "increases", "adds",
    "accumulates", "accumulating", "buys", "buying",
}

_BEARISH_KEYWORDS: Set[str] = {
    # Price action
    "crash", "crashes", "crashing", "plunge", "plunges", "plunging",
    "drop", "drops", "dropping", "fall", "falls", "falling",
    "decline", "declines", "declining", "tumble", "tumbles",
    "sinking", "slump", "slumps", "slumping", "lows",
    # Analyst/rating
    "downgrade", "downgrades", "downgraded", "sell", "bearish",
    "underweight", "underperform", "reduce", "reduced",
    # Fundamentals
    "loss", "losses", "losing", "miss", "misses", "missed",
    "weak", "weakness", "disappointing", "disappoints",
    "layoff", "layoffs", "restructuring",
    # Sentiment
    "risk", "risks", "risky", "concern", "concerns",
    "fears", "fear", "worried", "warning", "warns", "crisis",
    "recession", "negative", "uncertainty", "trouble", "troubled",
    # Institutional
    "decreases", "reduces", "sells", "sold", "dumps",
    "divests", "divesting", "trims", "trimming",
    # Economic
    "cut", "cuts", "slash", "slashes", "tariff", "tariffs",
}


def _classify_headline(text: str) -> str:
    """
    Simple keyword-based sentiment classification.
    Returns 'positive', 'negative', or 'neutral'.
    """
    if not text:
        return "neutral"
    words = set(text.lower().split())
    bull_count = len(words & _BULLISH_KEYWORDS)
    bear_count = len(words & _BEARISH_KEYWORDS)
    if bull_count > bear_count:
        return "positive"
    elif bear_count > bull_count:
        return "negative"
    return "neutral"


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

        # Parallel fetch: 5 data sources
        results = await asyncio.gather(
            self._fetch_social_change(ticker),
            self._fetch_social_historical(ticker),
            self._fetch_news(ticker),
            self._fetch_news_sentiments_rss(ticker),
            self._fetch_price_data(ticker),
            return_exceptions=True,
        )

        source_names = [
            "social_change", "social_historical",
            "news_raw", "news_rss", "price",
        ]
        fetched = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(
                    f"Sentiment fetch '{source_names[i]}' failed for "
                    f"{ticker}: {r}"
                )
                fetched.append([] if i < 4 else {})
            else:
                fetched.append(r)

        social_change = fetched[0]
        social_historical = fetched[1]
        news_raw = fetched[2]
        news_rss = fetched[3]
        price_data = fetched[4]

        # Extract company name from quote for better article filtering
        company_name = ""
        if isinstance(price_data, dict):
            company_name = price_data.get("name", "")

        # Merge data sources
        social_merged = self._merge_social_data(
            social_change, social_historical
        )
        all_news = self._merge_news_data(news_raw, news_rss)

        # Filter news to articles relevant to this ticker
        news_merged = self._filter_articles_for_ticker(
            all_news, ticker, company_name
        )
        logger.info(
            f"News filtering for {ticker} ({company_name}): "
            f"{len(all_news)} fetched → {len(news_merged)} relevant"
        )

        logger.info(
            f"Sentiment data for {ticker}: "
            f"social_change={len(social_change)}, "
            f"social_hist={len(social_historical)}, "
            f"social_merged={len(social_merged)}, "
            f"news_raw={len(news_raw)}, news_rss={len(news_rss)}, "
            f"news_merged={len(news_merged)}"
        )

        # ── Price momentum score (always available) ────────────
        price_score = self._compute_price_sentiment(price_data)

        # ── 24-hour window ────────────────────────────────────────
        news_score_24h, news_cur_24h, news_prev_24h = (
            self._compute_news_score(news_merged, hours=24)
        )
        social_score_24h, social_cur_24h, social_prev_24h = (
            self._compute_social_score(social_merged, days=1)
        )

        # When FMP social endpoints are unavailable, derive
        # mention counts from news data as a buzz proxy.
        if social_cur_24h == 0 and not social_merged:
            social_cur_24h = self._count_ticker_mentions(
                news_merged, ticker, hours=24
            )
            social_prev_24h = self._count_ticker_mentions(
                news_merged, ticker, hours=24, previous=True
            )

        has_news_24h = news_cur_24h > 0
        has_social_24h = social_cur_24h > 0
        combined_24h = self._combine_scores(
            news_score_24h, social_score_24h,
            has_social_24h, has_news_24h, price_score,
        )

        # ── 7-day window ──────────────────────────────────────────
        news_score_7d, news_cur_7d, news_prev_7d = (
            self._compute_news_score(news_merged, hours=168)
        )
        social_score_7d, social_cur_7d, social_prev_7d = (
            self._compute_social_score(social_merged, days=7)
        )

        if social_cur_7d == 0 and not social_merged:
            social_cur_7d = self._count_ticker_mentions(
                news_merged, ticker, hours=168
            )
            social_prev_7d = self._count_ticker_mentions(
                news_merged, ticker, hours=168, previous=True
            )

        has_news_7d = news_cur_7d > 0
        has_social_7d = social_cur_7d > 0
        combined_7d = self._combine_scores(
            news_score_7d, social_score_7d,
            has_social_7d, has_news_7d, price_score,
        )

        logger.info(
            f"Sentiment scores for {ticker} — "
            f"price={price_score} | "
            f"24h: news={news_score_24h}({news_cur_24h} articles) "
            f"mentions={social_cur_24h} combined={combined_24h} | "
            f"7d: news={news_score_7d}({news_cur_7d} articles) "
            f"mentions={social_cur_7d} combined={combined_7d}"
        )

        response = SentimentAnalysisResponse(
            symbol=ticker,
            # 24h
            mood_score=combined_24h,
            last_24h_mood=self._score_to_mood(combined_24h),
            social_mentions=social_cur_24h,
            social_mentions_change=self._pct_change(
                social_cur_24h, social_prev_24h
            ),
            news_articles=news_cur_24h,
            news_articles_change=self._pct_change(
                float(news_cur_24h), float(news_prev_24h)
            ),
            # 7d
            mood_score_7d=combined_7d,
            last_7d_mood=self._score_to_mood(combined_7d),
            social_mentions_7d=social_cur_7d,
            social_mentions_change_7d=self._pct_change(
                social_cur_7d, social_prev_7d
            ),
            news_articles_7d=news_cur_7d,
            news_articles_change_7d=self._pct_change(
                float(news_cur_7d), float(news_prev_7d)
            ),
        )

        _cache_set(f"sentiment:{ticker}", response)
        return response

    # ── Data fetching ─────────────────────────────────────────────

    async def _fetch_social_change(
        self, ticker: str
    ) -> List[Dict[str, Any]]:
        """Fetch hourly social sentiment from social-sentiments/change."""
        try:
            return await self.fmp.get_social_sentiment(ticker)
        except Exception as e:
            logger.warning(f"Social change fetch failed for {ticker}: {e}")
            return []

    async def _fetch_social_historical(
        self, ticker: str
    ) -> List[Dict[str, Any]]:
        """Fetch daily historical social sentiment."""
        try:
            return await self.fmp.get_social_sentiment_historical(ticker)
        except Exception as e:
            logger.warning(
                f"Social historical fetch failed for {ticker}: {e}"
            )
            return []

    async def _fetch_news(self, ticker: str) -> List[Dict[str, Any]]:
        """
        Fetch news articles from FMP for the last 14 days.

        Returns raw (unfiltered) articles. Filtering by ticker
        is done after all parallel fetches complete so we can
        use the company name from the price/quote data.
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
                f"FMP news fetch failed for {ticker}, "
                f"falling back to cache: {e}"
            )
            return await self._fetch_news_from_cache(ticker)

    async def _fetch_news_sentiments_rss(
        self, ticker: str
    ) -> List[Dict[str, Any]]:
        """Fetch news with FMP-computed sentiment scores."""
        try:
            return await self.fmp.get_news_sentiments_rss(ticker)
        except Exception as e:
            logger.warning(
                f"News sentiments RSS failed for {ticker}: {e}"
            )
            return []

    async def _fetch_price_data(
        self, ticker: str
    ) -> Dict[str, Any]:
        """Fetch real-time quote for price momentum scoring."""
        try:
            return await self.fmp.get_stock_price_quote(ticker)
        except Exception as e:
            logger.warning(f"Price data fetch failed for {ticker}: {e}")
            return {}

    async def _fetch_news_from_cache(
        self, ticker: str
    ) -> List[Dict[str, Any]]:
        """Fallback: fetch from Supabase ticker_news_cache."""
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=14)
            ).isoformat()
            result = (
                self.supabase.table("ticker_news_cache")
                .select(
                    "published_at, sentiment, sentiment_confidence, "
                    "ai_processed"
                )
                .eq("ticker", ticker)
                .gte("published_at", cutoff)
                .order("published_at", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.warning(
                f"News cache fallback also failed for {ticker}: {e}"
            )
            return []

    # ── Article filtering ──────────────────────────────────────

    @staticmethod
    def _filter_articles_for_ticker(
        articles: List[Dict[str, Any]],
        ticker: str,
        company_name: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Filter articles to those relevant to the ticker.

        Checks symbol field, title, and text for the ticker symbol,
        $TICKER, or company name. Compensates for FMP's news/stock
        endpoint not filtering by ticker properly.
        """
        ticker_upper = ticker.upper()
        dollar_ticker = f"${ticker_upper}"

        # Build search terms (e.g., ["AAPL", "$AAPL", "APPLE"])
        search_terms = [ticker_upper, dollar_ticker]
        if company_name:
            # Use the first word of the company name as a search
            # term (e.g., "Apple" from "Apple Inc.")
            name_parts = company_name.split()
            if name_parts:
                first_word = name_parts[0].upper()
                # Only add if it's meaningful (>2 chars, not generic)
                if len(first_word) > 2:
                    search_terms.append(first_word)

        filtered = []
        for a in articles:
            # Direct symbol match
            sym = (a.get("symbol") or "").upper()
            if sym == ticker_upper:
                filtered.append(a)
                continue

            # Check title and text for any search term
            title = (a.get("title") or "").upper()
            text = (a.get("text") or "").upper()
            combined = f"{title} {text}"

            if any(term in combined for term in search_terms):
                filtered.append(a)

        return filtered

    # ── Data merging ─────────────────────────────────────────────

    @staticmethod
    def _merge_social_data(
        change_data: List[Dict[str, Any]],
        historical_data: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Merge hourly (change) and daily (historical) social data.

        Uses hourly data as primary if it has 100+ records.
        Otherwise supplements with historical data for dates
        not already covered by change_data.
        """
        if len(change_data) >= 100:
            return change_data

        if not change_data and not historical_data:
            return []
        if not change_data:
            return historical_data
        if not historical_data:
            return change_data

        # Get dates already covered by change_data
        change_dates: Set[str] = set()
        for r in change_data:
            d = (r.get("date") or "")[:10]
            if d:
                change_dates.add(d)

        # Add historical records for dates NOT in change_data
        merged = list(change_data)
        for r in historical_data:
            d = (r.get("date") or "")[:10]
            if d and d not in change_dates:
                merged.append(r)

        return merged

    @staticmethod
    def _merge_news_data(
        raw_articles: List[Dict[str, Any]],
        rss_articles: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Merge news/stock articles with stock-news-sentiments-rss-feed.

        RSS articles have sentimentScore and sentiment fields.
        Deduplicate by URL, preferring RSS version.
        """
        seen_urls: Set[str] = set()
        merged: List[Dict[str, Any]] = []

        # Add RSS articles first (they have sentiment data)
        for a in rss_articles:
            url = a.get("url") or ""
            if url:
                seen_urls.add(url)
            merged.append(a)

        # Add raw articles that aren't already in RSS set
        for a in raw_articles:
            url = a.get("url") or ""
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            merged.append(a)

        return merged

    # ── Ticker mention counting (social mentions proxy) ────────

    @staticmethod
    def _count_ticker_mentions(
        articles: List[Dict[str, Any]],
        ticker: str,
        hours: int,
        previous: bool = False,
    ) -> float:
        """
        Count ticker mentions across news articles as a buzz proxy.

        When FMP social endpoints are unavailable, this provides
        a non-zero 'social mentions' count by counting how many
        times the ticker symbol appears in news titles and text.
        """
        now = datetime.now(timezone.utc)
        if previous:
            start = (now - timedelta(hours=hours * 2)).isoformat()
            end = (now - timedelta(hours=hours)).isoformat()
        else:
            start = (now - timedelta(hours=hours)).isoformat()
            end = now.isoformat()

        def _pub_date(a: Dict) -> str:
            return a.get("publishedDate") or a.get("published_at") or ""

        ticker_lower = ticker.lower()
        ticker_dollar = f"${ticker_lower}"
        count = 0.0

        for a in articles:
            pd = _pub_date(a)
            if not (start <= pd <= end):
                continue
            title = (a.get("title") or "").lower()
            text = (a.get("text") or a.get("summary") or "").lower()
            combined = f"{title} {text}"
            count += combined.count(ticker_lower)
            count += combined.count(ticker_dollar)

        return count

    # ── News scoring ─────────────────────────────────────────────

    def _compute_news_score(
        self, articles: List[Dict], hours: int
    ) -> Tuple[int, int, int]:
        """
        Returns (score_0_100, current_article_count, previous_article_count).

        Three-tier sentiment resolution ensures every article gets scored:
          1. sentimentScore float (from RSS, -1 to 1) -> 0-100
          2. sentiment string label (Bullish/Bearish/Neutral) -> mapped
          3. keyword-based classification on title + text -> mapped
        """
        now = datetime.now(timezone.utc)
        current_cutoff = (now - timedelta(hours=hours)).isoformat()
        previous_cutoff = (now - timedelta(hours=hours * 2)).isoformat()

        def _pub_date(a: Dict) -> str:
            return a.get("publishedDate") or a.get("published_at") or ""

        current = [a for a in articles if _pub_date(a) >= current_cutoff]
        previous = [
            a for a in articles
            if previous_cutoff <= _pub_date(a) < current_cutoff
        ]

        logger.info(
            f"News scoring (hours={hours}): "
            f"total_fetched={len(articles)}, current={len(current)}, "
            f"previous={len(previous)}"
        )

        if not current:
            return 50, 0, len(previous)

        # Score every article using three-tier resolution
        scores: List[float] = []
        for a in current:
            scores.append(self._resolve_article_sentiment(a))

        avg_score = sum(scores) / len(scores)
        final = max(0, min(100, round(avg_score)))
        return final, len(current), len(previous)

    @staticmethod
    def _resolve_article_sentiment(article: Dict) -> float:
        """
        Resolve sentiment for a single article. Returns 0-100 float.

        Priority:
          1. sentimentScore float (from RSS feed, -1..1) -> map to 0..100
          2. sentiment string label -> fixed score
          3. keyword classification on title + text -> moderate score
        """
        # Tier 1: sentimentScore float from RSS feed (-1 to 1)
        sent_score = article.get("sentimentScore")
        if sent_score is not None and isinstance(sent_score, (int, float)):
            return max(0.0, min(100.0, (float(sent_score) + 1) * 50))

        # Tier 2: sentiment string label
        sent_label = (article.get("sentiment") or "").lower().strip()
        if sent_label in ("positive", "bullish"):
            return 85.0
        if sent_label in ("negative", "bearish"):
            return 15.0
        if sent_label == "neutral":
            return 50.0

        # Tier 3: keyword-based classification on title + text
        title = article.get("title") or article.get("headline") or ""
        text = article.get("text") or article.get("summary") or ""
        combined = f"{title} {text[:200]}"
        classification = _classify_headline(combined)

        if classification == "positive":
            return 75.0
        if classification == "negative":
            return 25.0
        return 50.0

    # ── Social scoring ───────────────────────────────────────────

    def _compute_social_score(
        self, social_data: List[Dict], days: int
    ) -> Tuple[int, float, float]:
        """Returns (score_0_100, current_mentions, previous_mentions)."""
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

        current_mentions = self._count_mentions(current_records)
        prev_mentions = self._count_mentions(previous_records)

        if not current_records:
            return 50, 0.0, prev_mentions

        sentiments: List[float] = []

        for r in current_records:
            sw_sent = r.get("stocktwitsSentiment", 0) or 0
            tw_sent = r.get("twitterSentiment", 0) or 0
            sw_posts = r.get("stocktwitsPosts", 0) or 0
            tw_posts = r.get("twitterPosts", 0) or 0

            posts = sw_posts + tw_posts
            if posts > 0:
                weighted = (
                    (sw_sent * sw_posts + tw_sent * tw_posts) / posts
                )
                sentiments.append(weighted)
            elif sw_sent != 0 or tw_sent != 0:
                # Records with sentiment values but no post counts
                vals = [v for v in (sw_sent, tw_sent) if v != 0]
                if vals:
                    sentiments.append(sum(vals) / len(vals))

        if not sentiments:
            return 50, current_mentions, prev_mentions

        avg = sum(sentiments) / len(sentiments)  # -1 to 1
        score = max(0, min(100, round((avg + 1) * 50)))
        return score, current_mentions, prev_mentions

    @staticmethod
    def _count_mentions(records: List[Dict]) -> float:
        """Sum all social mention counts (posts + comments)."""
        return sum(
            (r.get("stocktwitsPosts", 0) or 0)
            + (r.get("twitterPosts", 0) or 0)
            + (r.get("stocktwitsComments", 0) or 0)
            + (r.get("twitterComments", 0) or 0)
            for r in records
        )

    # ── Price momentum scoring ──────────────────────────────────

    @staticmethod
    def _compute_price_sentiment(price_data: Dict) -> int:
        """
        Derive a 0-100 sentiment score from price momentum.

        Uses changesPercentage (daily % change) as the primary signal.
        Maps roughly: -5% or worse → ~15, 0% → 50, +5% or better → ~85.
        Always available since FMP's quote endpoint works per-ticker.
        """
        if not price_data:
            return 50

        pct_change = (
            price_data.get("changePercentage")
            or price_data.get("changesPercentage")
            or 0
        )

        # Map % change to 0-100 using a sigmoid-like curve
        # Clamp to reasonable range and scale
        # -5% → ~15, -2% → ~35, 0% → 50, +2% → ~65, +5% → ~85
        scaled = 50 + (pct_change * 7)
        return max(0, min(100, round(scaled)))

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _combine_scores(
        news_score: int,
        social_score: int,
        has_social: bool,
        has_news: bool,
        price_score: int = 50,
    ) -> int:
        """
        Combine sentiment signals with adaptive weighting.

        When news + social are available:
          45% news + 25% social + 30% price
        When only news:
          55% news + 45% price
        When only social:
          55% social + 45% price
        When neither:
          100% price (never returns hardcoded 50)
        """
        if has_news and has_social:
            return max(0, min(100, round(
                news_score * 0.45
                + social_score * 0.25
                + price_score * 0.30
            )))
        if has_news:
            return max(0, min(100, round(
                news_score * 0.55 + price_score * 0.45
            )))
        if has_social:
            return max(0, min(100, round(
                social_score * 0.55 + price_score * 0.45
            )))
        return price_score

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
