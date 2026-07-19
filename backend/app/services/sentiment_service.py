"""
Sentiment Analysis Service — aggregates FMP news articles and price
momentum to compute a 0-100 mood score.

Data sources:
  1. news/stock (raw news articles from FMP)
  2. quote (price momentum for real-time signal)
  3. ticker_news_cache (Supabase — accumulated articles with sentiment)

Articles are scored using keyword-based classification on title + text,
persisted to Supabase for rolling 24h/7d window accuracy, and combined
with price momentum for the final mood score.

Social mentions are derived from news article mention counts (FMP's
social-sentiments endpoints were removed from the stable API).
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import math

from app.database import get_supabase
from app.integrations.fmp import FMPClient, get_fmp_client
from app.schemas.sentiment import (
    MarketMoodLevel,
    SentimentAnalysisResponse,
)
from app.services.social_mentions_service import get_social_mentions_service

logger = logging.getLogger(__name__)

# ── In-memory cache (same pattern as analyst_service) ─────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 900  # 15 minutes

# How often to re-fetch from FMP and refresh the DB cache
_DB_REFRESH_TTL = 14400  # 4 hours


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


# ── Keyword-based sentiment classifier ───────────────────────────

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


def _sentiment_label_to_db(classification: str) -> str:
    """Map keyword classification to DB enum: bullish/bearish/neutral."""
    if classification == "positive":
        return "bullish"
    if classification == "negative":
        return "bearish"
    return "neutral"


# ── Service ───────────────────────────────────────────────────────

class SentimentService:

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()
        self.supabase = get_supabase()

    async def get_sentiment(
        self, ticker: str, social_ticker: Optional[str] = None,
        is_crypto: bool = False,
    ) -> SentimentAnalysisResponse:
        ticker = ticker.upper()
        # For crypto: FMP uses "ETHUSD" but ApeWisdom uses "ETH"
        social_key = (social_ticker or ticker).upper()
        self._is_crypto = is_crypto

        cached = _cache_get(f"sentiment:{ticker}")
        if cached is not None:
            return cached

        social_svc = get_social_mentions_service()

        # Wrap social calls with a timeout. ApeWisdom cold cache can
        # take 60+ seconds (paginated fetch with delays), so allow enough
        # time for the initial cache build. Subsequent calls are instant.
        async def _social_24h():
            return await asyncio.wait_for(
                social_svc.get_mentions_24h(social_key), timeout=45
            )

        async def _social_7d():
            return await asyncio.wait_for(
                social_svc.get_mentions_7d(social_key), timeout=45
            )

        # Parallel fetch: news + price + historical + social (24h + 7d)
        results = await asyncio.gather(
            self._get_articles(ticker),
            self._fetch_price_data(ticker),
            self._fetch_historical_prices(ticker),
            _social_24h(),
            _social_7d(),
            return_exceptions=True,
        )

        articles = results[0] if not isinstance(results[0], Exception) else []
        price_data = results[1] if not isinstance(results[1], Exception) else {}
        hist_prices = results[2] if not isinstance(results[2], Exception) else []
        social_24h = results[3] if not isinstance(results[3], Exception) else (0, 0)
        social_7d = results[4] if not isinstance(results[4], Exception) else (0, 0)

        for i, name in enumerate(["articles", "price", "historical", "social_24h", "social_7d"]):
            if isinstance(results[i], Exception):
                logger.warning(f"Sentiment fetch '{name}' failed for {ticker}: {results[i]}")

        social_cur_24h, social_prev_24h = social_24h
        social_cur_7d, social_prev_7d = social_7d

        logger.info(
            f"Sentiment data for {ticker}: "
            f"articles={len(articles)}, "
            f"social_24h={social_cur_24h}, social_7d={social_cur_7d}"
        )

        # ── Price momentum scores ─────────────────────────────
        price_score_24h = self._compute_price_sentiment(price_data)
        price_score_7d = self._compute_price_sentiment_7d(hist_prices)

        # ── 24-hour window ────────────────────────────────────────
        (news_score_24h, news_cur_24h, news_prev_24h,
         news_bull_24h, news_bear_24h, news_neut_24h) = (
            self._compute_news_score(articles, hours=24)
        )
        social_score_24h = self._compute_social_buzz_score(
            social_cur_24h, social_prev_24h
        )

        has_news_24h = news_cur_24h > 0
        has_social_24h = social_cur_24h > 0
        combined_24h = self._combine_scores(
            news_score_24h, social_score_24h,
            has_social_24h, has_news_24h, price_score_24h,
        )

        # ── 7-day window ──────────────────────────────────────────
        (news_score_7d, news_cur_7d, news_prev_7d,
         news_bull_7d, news_bear_7d, news_neut_7d) = (
            self._compute_news_score(articles, hours=168)
        )
        social_score_7d = self._compute_social_buzz_score(
            social_cur_7d, social_prev_7d
        )

        has_news_7d = news_cur_7d > 0
        has_social_7d = social_cur_7d > 0
        combined_7d = self._combine_scores(
            news_score_7d, social_score_7d,
            has_social_7d, has_news_7d, price_score_7d,
        )

        logger.info(
            f"Sentiment scores for {ticker} — "
            f"price_24h={price_score_24h} price_7d={price_score_7d} | "
            f"24h: news={news_score_24h}({news_cur_24h} articles) "
            f"social={social_score_24h}({social_cur_24h} mentions) "
            f"combined={combined_24h} | "
            f"7d: news={news_score_7d}({news_cur_7d} articles) "
            f"social={social_score_7d}({social_cur_7d} mentions) "
            f"combined={combined_7d}"
        )

        response = SentimentAnalysisResponse(
            symbol=ticker,
            # 24h
            mood_score=combined_24h,
            last_24h_mood=self._score_to_mood(combined_24h),
            social_mentions=float(social_cur_24h),
            social_mentions_change=self._pct_change(
                float(social_cur_24h), float(social_prev_24h)
            ),
            news_articles=news_cur_24h,
            news_articles_change=self._pct_change(
                float(news_cur_24h), float(news_prev_24h)
            ),
            news_bullish=news_bull_24h,
            news_bearish=news_bear_24h,
            news_neutral=news_neut_24h,
            # 7d
            mood_score_7d=combined_7d,
            last_7d_mood=self._score_to_mood(combined_7d),
            social_mentions_7d=float(social_cur_7d),
            social_mentions_change_7d=self._pct_change(
                float(social_cur_7d), float(social_prev_7d)
            ),
            news_articles_7d=news_cur_7d,
            news_articles_change_7d=self._pct_change(
                float(news_cur_7d), float(news_prev_7d)
            ),
            news_bullish_7d=news_bull_7d,
            news_bearish_7d=news_bear_7d,
            news_neutral_7d=news_neut_7d,
            social_data_available=has_social_24h or has_social_7d,
        )

        _cache_set(f"sentiment:{ticker}", response)
        return response

    # ── Article pipeline (DB-backed) ─────────────────────────────

    async def _get_articles(
        self, ticker: str
    ) -> List[Dict[str, Any]]:
        """
        Get articles from DB cache, refreshing from FMP if stale.

        Returns articles in a unified format with publishedDate and
        sentiment fields suitable for scoring.
        """
        # Check if DB has fresh enough articles for this ticker
        db_articles = await asyncio.to_thread(
            self._load_from_db, ticker
        )

        if db_articles is not None:
            logger.info(
                f"Sentiment DB cache HIT for {ticker}: "
                f"{len(db_articles)} articles"
            )
            return db_articles

        # DB is stale or empty — fetch from FMP
        logger.info(f"Sentiment DB cache MISS for {ticker} — fetching FMP")
        fmp_articles = await self._fetch_news(ticker)

        if not fmp_articles:
            # Try loading stale DB data as fallback
            stale = await asyncio.to_thread(
                self._load_from_db, ticker, stale_ok=True
            )
            if stale:
                logger.info(
                    f"Using stale DB articles for {ticker}: {len(stale)}"
                )
                return stale
            return []

        # Persist to DB in background (fire-and-forget)
        asyncio.get_running_loop().run_in_executor(
            None, self._persist_articles, ticker, fmp_articles
        )

        return fmp_articles

    def _load_from_db(
        self, ticker: str, stale_ok: bool = False,
    ) -> Optional[List[Dict[str, Any]]]:
        """Load cached articles from Supabase ticker_news_cache."""
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=14)
            ).isoformat()

            result = (
                self.supabase.table("ticker_news_cache")
                .select(
                    "headline, summary, sentiment, "
                    "sentiment_confidence, published_at, "
                    "source_name, cached_at"
                )
                .eq("ticker", ticker)
                .gte("published_at", cutoff)
                .order("published_at", desc=True)
                .execute()
            )

            if not result.data:
                return None

            # Check freshness — is the most recent cached_at within 4h?
            if not stale_ok:
                latest_cached = max(
                    (r.get("cached_at") or "") for r in result.data
                )
                if latest_cached:
                    cached_dt = datetime.fromisoformat(
                        latest_cached.replace("Z", "+00:00")
                    )
                    age = datetime.now(timezone.utc) - cached_dt
                    if age > timedelta(seconds=_DB_REFRESH_TTL):
                        logger.info(
                            f"DB cache STALE for {ticker} "
                            f"(age={age})"
                        )
                        return None

            # Convert DB rows to article-like dicts for scoring
            articles = []
            for row in result.data:
                articles.append({
                    "title": row.get("headline") or "",
                    "text": row.get("summary") or "",
                    "publishedDate": row.get("published_at") or "",
                    "sentiment": row.get("sentiment") or "",
                    "site": row.get("source_name") or "",
                })
            return articles

        except Exception as e:
            logger.warning(
                f"DB article load failed for {ticker}: {e}"
            )
            return None

    def _persist_articles(
        self, ticker: str, articles: List[Dict[str, Any]]
    ):
        """Upsert FMP articles into ticker_news_cache with sentiment."""
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(days=14)).isoformat()
        rows = []

        for a in articles:
            url = a.get("url") or ""
            title = a.get("title") or ""
            text = a.get("text") or ""
            published = a.get("publishedDate") or ""

            if not url or not title:
                continue

            # Score sentiment using keyword classifier
            combined = f"{title} {text[:300]}"
            classification = _classify_headline(combined)
            sentiment_db = _sentiment_label_to_db(classification)
            confidence = 60 if classification != "neutral" else 40

            rows.append({
                "ticker": ticker,
                "external_id": url,  # Use URL as dedup key
                "headline": title[:500],
                "summary": (text or "")[:2000],
                "sentiment": sentiment_db,
                "sentiment_confidence": confidence,
                "source_name": a.get("site") or a.get("source") or "",
                "published_at": published,
                "thumbnail_url": a.get("image") or "",
                "article_url": url,
                "cached_at": now.isoformat(),
                "expires_at": expires,
            })

        if not rows:
            return

        try:
            # Batch upsert (Supabase handles UNIQUE constraint)
            self.supabase.table("ticker_news_cache").upsert(
                rows, on_conflict="ticker,external_id"
            ).execute()
            logger.info(
                f"Persisted {len(rows)} articles for {ticker} "
                f"to ticker_news_cache"
            )
        except Exception as e:
            logger.warning(
                f"Article persist failed for {ticker}: {e}"
            )

    # ── Data fetching ─────────────────────────────────────────────

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

            if getattr(self, '_is_crypto', False):
                articles = await self.fmp.get_crypto_news(
                    ticker=ticker, limit=1000,
                )
            else:
                articles = await self.fmp.get_stock_news(
                    ticker=ticker, limit=1000,
                    from_date=from_date, to_date=to_date,
                )
            return articles if articles else []
        except Exception as e:
            logger.warning(
                f"FMP news fetch failed for {ticker}: {e}"
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

    async def _fetch_historical_prices(
        self, ticker: str
    ) -> List[Dict[str, Any]]:
        """Fetch ~10 days of historical prices for 7-day return calc."""
        try:
            now = datetime.now(timezone.utc)
            from_date = (now - timedelta(days=12)).strftime("%Y-%m-%d")
            to_date = now.strftime("%Y-%m-%d")
            raw = await self.fmp.get_historical_prices(ticker, from_date, to_date)
            if isinstance(raw, dict):
                hist = raw.get("historical", [])
            elif isinstance(raw, list):
                hist = raw
            else:
                hist = []
            hist.sort(key=lambda p: p.get("date") or "")
            return hist
        except Exception as e:
            logger.warning(f"Historical prices fetch failed for {ticker}: {e}")
            return []

    @staticmethod
    def _compute_price_sentiment_7d(hist_prices: List[Dict]) -> int:
        """
        Derive a 0-100 sentiment score from 7-day price momentum.

        Computes the % change from ~7 days ago to latest close,
        then maps it the same way as daily: -5% → ~15, 0% → 50, +5% → ~85.
        Falls back to 50 (neutral) if insufficient data.
        """
        if not hist_prices or len(hist_prices) < 2:
            return 50

        latest_close = hist_prices[-1].get("close") or hist_prices[-1].get("adjClose")
        # Find the price closest to 7 days ago
        target_idx = max(0, len(hist_prices) - 8)  # ~7 trading days back
        start_close = hist_prices[target_idx].get("close") or hist_prices[target_idx].get("adjClose")

        if not latest_close or not start_close or start_close == 0:
            return 50

        pct_change_7d = ((latest_close - start_close) / start_close) * 100
        # Use same mapping as daily but with gentler scaling (7d moves are larger)
        # -10% → ~15, -5% → ~32, 0% → 50, +5% → ~68, +10% → ~85
        scaled = 50 + (pct_change_7d * 3.5)
        return max(0, min(100, round(scaled)))

    # ── Social buzz scoring ─────────────────────────────────────

    @staticmethod
    def _compute_social_buzz_score(
        current: int, previous: int
    ) -> int:
        """
        Map Reddit mention count to a 0-100 buzz score.

        Uses logarithmic scale so both small and large tickers
        get meaningful scores:
          0 mentions → 30 (low buzz is slightly bearish)
          1-5 → 40, 5-20 → 50, 20-100 → 60-70,
          100-500 → 70-80, 500+ → 80-90

        Adjusted by trend: rising mentions boost +5, falling -5.
        """
        if current <= 0:
            return 30

        # Log scale: log2(mentions) mapped to 30-90 range
        # log2(1)=0, log2(5)≈2.3, log2(20)≈4.3, log2(100)≈6.6,
        # log2(500)≈9, log2(1000)≈10
        log_val = math.log2(current + 1)  # +1 to avoid log(0)
        # Map 0-10 log range to 35-85 score range
        base_score = 35 + (log_val / 10.0) * 50
        base_score = max(30, min(85, base_score))

        # Trend adjustment
        if previous > 0:
            if current > previous:
                base_score += 5
            elif current < previous:
                base_score -= 5

        return max(0, min(100, round(base_score)))

    # ── News scoring ─────────────────────────────────────────────

    def _compute_news_score(
        self, articles: List[Dict], hours: int
    ) -> Tuple[int, int, int, int, int, int]:
        """
        Returns (score_0_100, current_article_count, previous_article_count,
                 bullish_count, bearish_count, neutral_count).

        Scores each article using keyword classification on title + text.
        Classifies articles: score > 60 = bullish, < 40 = bearish, else neutral.
        """
        now = datetime.now(timezone.utc)
        # FMP publishedDate is space-separated ("2024-01-15 09:30:00"); the ISO cutoff
        # carries a 'T' + tz offset. A raw string compare mis-buckets articles — ' '
        # (0x20) < 'T' (0x54), so a same-day article ALWAYS sorts before the cutoff —
        # corrupting the current/previous window counts and the news change%.
        # Canonicalize both sides to "yyyy-mm-ddThh:mm:ss" before comparing.
        current_cutoff = (now - timedelta(hours=hours)).isoformat()[:19]
        previous_cutoff = (now - timedelta(hours=hours * 2)).isoformat()[:19]

        def _pub_date(a: Dict) -> str:
            raw = a.get("publishedDate") or a.get("published_at") or ""
            return raw.replace(" ", "T")[:19]

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
            return 50, 0, len(previous), 0, 0, 0

        # Score every article and classify sentiment
        scores: List[float] = []
        bullish = 0
        bearish = 0
        neutral = 0
        for a in current:
            score = self._resolve_article_sentiment(a)
            scores.append(score)
            if score > 60:
                bullish += 1
            elif score < 40:
                bearish += 1
            else:
                neutral += 1

        avg_score = sum(scores) / len(scores)
        final = max(0, min(100, round(avg_score)))
        return final, len(current), len(previous), bullish, bearish, neutral

    @staticmethod
    def _resolve_article_sentiment(article: Dict) -> float:
        """
        Resolve sentiment for a single article. Returns 0-100 float.

        Priority:
          1. sentiment string label from DB cache -> fixed score
          2. keyword classification on title + text -> moderate score
        """
        # Tier 1: sentiment label (from DB cache or RSS feed)
        sent_label = (article.get("sentiment") or "").lower().strip()
        if sent_label in ("positive", "bullish"):
            return 80.0
        if sent_label in ("negative", "bearish"):
            return 20.0
        if sent_label == "neutral":
            return 50.0

        # Tier 2: keyword-based classification on title + text
        title = article.get("title") or article.get("headline") or ""
        text = article.get("text") or article.get("summary") or ""
        combined = f"{title} {text[:300]}"
        classification = _classify_headline(combined)

        if classification == "positive":
            return 75.0
        if classification == "negative":
            return 25.0
        return 50.0

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

        All 3 available: 40% news + 30% social + 30% price
        News + price only: 55% news + 45% price
        Social + price only: 55% social + 45% price
        Price only: 100% price
        """
        if has_news and has_social:
            return max(0, min(100, round(
                news_score * 0.40
                + social_score * 0.30
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
