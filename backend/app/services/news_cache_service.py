"""
News Cache Service — Hybrid "First User Pays" + Watchlist Pre-computation

Provides AI-enriched news for any ticker:
  1. Check ticker_news_cache for fresh (non-expired) rows → return instantly
  2. Cache miss → fetch from FMP, enrich with Gemini (batch), cache in Supabase
  3. Background pre-warmer keeps popular watchlist tickers warm
"""

import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.integrations.gemini import get_gemini_client

logger = logging.getLogger(__name__)

# Cache TTL in hours
CACHE_TTL_HOURS = 6

# Gemini model for news enrichment (fast + cheap)
NEWS_AI_MODEL = "gemini-2.0-flash"


class NewsCacheService:
    """Service for fetching and caching AI-enriched news per ticker."""

    def __init__(self):
        self.supabase = get_supabase()
        self.gemini = get_gemini_client()
        self.fmp = get_fmp_client()

    async def get_ticker_news(
        self, ticker: str, limit: int = 10
    ) -> Dict[str, Any]:
        """
        Get AI-enriched news for a ticker. Cache-first architecture.

        Returns:
            dict with keys: articles, ticker, cached, cache_age_seconds
        """
        ticker = ticker.upper()

        # ── 1. Check cache ──
        cached_articles = self._get_cached(ticker, limit)
        if cached_articles:
            # Compute cache age from the oldest cached_at
            oldest = min(
                (a.get("cached_at") or datetime.now(timezone.utc).isoformat())
                for a in cached_articles
            )
            try:
                cached_time = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
                age = int((datetime.now(timezone.utc) - cached_time).total_seconds())
            except Exception:
                age = 0

            logger.info(f"News cache HIT for {ticker}: {len(cached_articles)} articles")
            return {
                "articles": self._format_response(cached_articles),
                "ticker": ticker,
                "cached": True,
                "cache_age_seconds": age,
            }

        # ── 2. Cache miss → fetch + enrich + cache ──
        logger.info(f"News cache MISS for {ticker}: fetching from FMP + Gemini")
        try:
            articles = await self._fetch_and_enrich(ticker, limit)
            return {
                "articles": articles,
                "ticker": ticker,
                "cached": False,
                "cache_age_seconds": 0,
            }
        except Exception as e:
            logger.error(f"News fetch+enrich failed for {ticker}: {e}", exc_info=True)
            # Fallback: try FMP raw without AI enrichment
            return await self._fallback_raw_news(ticker, limit)

    def _get_cached(self, ticker: str, limit: int) -> List[Dict[str, Any]]:
        """Query ticker_news_cache for fresh (non-expired) rows."""
        try:
            result = (
                self.supabase.table("ticker_news_cache")
                .select("*")
                .eq("ticker", ticker)
                .gte("expires_at", datetime.now(timezone.utc).isoformat())
                .order("published_at", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.warning(f"Cache lookup failed for {ticker}: {e}")
            return []

    async def _fetch_and_enrich(
        self, ticker: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Fetch from FMP, enrich with Gemini, cache in Supabase."""
        # ── Fetch raw news from FMP ──
        raw_articles = await self.fmp.get_stock_news(ticker, limit=limit)
        if not raw_articles:
            logger.info(f"No FMP news found for {ticker}")
            return []

        # ── Batch enrich with Gemini (single API call for all articles) ──
        enrichments = await self._batch_enrich_articles(raw_articles)

        # ── Build cache rows and insert ──
        cache_rows = []
        response_articles = []

        for i, raw in enumerate(raw_articles[:limit]):
            enrichment = enrichments.get(i, {})

            # Build a stable external_id from the article URL or title hash
            external_id = raw.get("url") or raw.get("title", f"unknown_{i}")

            row = {
                "ticker": ticker,
                "external_id": external_id[:500],  # Truncate to avoid overly long keys
                "headline": raw.get("title", ""),
                "summary": raw.get("text", ""),
                "summary_bullets": json.dumps(enrichment.get("bullets", [])),
                "sentiment": enrichment.get("sentiment", "neutral"),
                "sentiment_confidence": enrichment.get("confidence", 0),
                "source_name": raw.get("site", ""),
                "source_logo_url": None,
                "published_at": raw.get("publishedDate"),
                "thumbnail_url": raw.get("image"),
                "article_url": raw.get("url"),
                "related_tickers": json.dumps(
                    raw.get("symbol", ticker).split(",")
                    if isinstance(raw.get("symbol"), str)
                    else [ticker]
                ),
                "ai_processed": bool(enrichment),
                "ai_model": NEWS_AI_MODEL if enrichment else None,
            }
            cache_rows.append(row)

            # Build response article
            response_articles.append({
                "id": "",  # Will be set after insert
                "headline": row["headline"],
                "summary": row["summary"],
                "summary_bullets": enrichment.get("bullets", []),
                "sentiment": row["sentiment"],
                "sentiment_confidence": row["sentiment_confidence"],
                "source_name": row["source_name"],
                "source_logo_url": row["source_logo_url"],
                "published_at": row["published_at"],
                "thumbnail_url": row["thumbnail_url"],
                "article_url": row["article_url"],
                "related_tickers": (
                    raw.get("symbol", ticker).split(",")
                    if isinstance(raw.get("symbol"), str)
                    else [ticker]
                ),
                "ai_processed": row["ai_processed"],
            })

        # ── Upsert into cache (ignore conflicts on duplicate external_id) ──
        try:
            result = (
                self.supabase.table("ticker_news_cache")
                .upsert(cache_rows, on_conflict="ticker,external_id")
                .execute()
            )
            # Update response article IDs from the inserted rows
            if result.data:
                for i, inserted in enumerate(result.data):
                    if i < len(response_articles):
                        response_articles[i]["id"] = inserted.get("id", "")
            logger.info(f"Cached {len(cache_rows)} articles for {ticker}")
        except Exception as e:
            logger.error(f"Cache insert failed for {ticker}: {e}")
            # Still return the enriched articles even if caching failed
            for i, art in enumerate(response_articles):
                if not art["id"]:
                    art["id"] = f"temp_{i}"

        return response_articles

    async def _batch_enrich_articles(
        self, articles: List[Dict[str, Any]]
    ) -> Dict[int, Dict[str, Any]]:
        """
        Enrich all articles in a single Gemini API call.
        Returns a dict mapping article index → enrichment data.
        """
        if not articles:
            return {}

        # Build a batch prompt with all articles
        articles_text = []
        for i, art in enumerate(articles):
            title = art.get("title", "")
            text = art.get("text", "")
            # Truncate long articles to save tokens
            if len(text) > 500:
                text = text[:500] + "..."
            articles_text.append(
                f"Article {i}:\nTitle: {title}\nContent: {text}"
            )

        batch_prompt = f"""Analyze the following {len(articles)} financial news articles.
For EACH article, provide:
1. Exactly 3 concise bullet points summarizing the key insights (plain English, no jargon)
2. Sentiment classification: "bullish", "bearish", or "neutral"
3. Confidence score: 0-100

Return a JSON array with one object per article in order. Each object must have:
- "index": the article number (0-based)
- "bullets": array of exactly 3 strings
- "sentiment": "bullish" | "bearish" | "neutral"
- "confidence": integer 0-100

{chr(10).join(articles_text)}

Return ONLY the JSON array, no markdown fencing or commentary."""

        try:
            response = await self.gemini.generate_json(
                prompt=batch_prompt,
                system_instruction=(
                    "You are a financial news analyst for a consumer investment app. "
                    "Summarize news in plain English that non-technical investors can understand. "
                    "Be concise and focus on what matters for investors."
                ),
                model_name=NEWS_AI_MODEL,
            )

            # Parse the JSON response
            text = response.get("text", "")
            parsed = json.loads(text)

            result = {}
            if isinstance(parsed, list):
                for item in parsed:
                    idx = item.get("index", 0)
                    result[idx] = {
                        "bullets": item.get("bullets", [])[:3],
                        "sentiment": item.get("sentiment", "neutral"),
                        "confidence": item.get("confidence", 0),
                    }

            logger.info(
                f"Gemini batch enrichment: {len(result)}/{len(articles)} articles processed"
            )
            return result

        except Exception as e:
            logger.error(f"Gemini batch enrichment failed: {e}", exc_info=True)
            return {}

    async def _fallback_raw_news(
        self, ticker: str, limit: int
    ) -> Dict[str, Any]:
        """Fallback: return raw FMP news without AI enrichment."""
        try:
            raw_articles = await self.fmp.get_stock_news(ticker, limit=limit)
            articles = []
            for i, raw in enumerate(raw_articles[:limit]):
                articles.append({
                    "id": f"raw_{i}",
                    "headline": raw.get("title", ""),
                    "summary": raw.get("text", ""),
                    "summary_bullets": [],
                    "sentiment": None,
                    "sentiment_confidence": 0,
                    "source_name": raw.get("site", ""),
                    "source_logo_url": None,
                    "published_at": raw.get("publishedDate"),
                    "thumbnail_url": raw.get("image"),
                    "article_url": raw.get("url"),
                    "related_tickers": (
                        raw.get("symbol", ticker).split(",")
                        if isinstance(raw.get("symbol"), str)
                        else [ticker]
                    ),
                    "ai_processed": False,
                })
            return {
                "articles": articles,
                "ticker": ticker,
                "cached": False,
                "cache_age_seconds": None,
            }
        except Exception as e:
            logger.error(f"Fallback raw news also failed for {ticker}: {e}")
            return {
                "articles": [],
                "ticker": ticker,
                "cached": False,
                "cache_age_seconds": None,
            }

    def _format_response(
        self, cached_rows: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Format cached DB rows into the API response shape."""
        articles = []
        for row in cached_rows:
            # Parse summary_bullets from JSONB
            bullets = row.get("summary_bullets", [])
            if isinstance(bullets, str):
                try:
                    bullets = json.loads(bullets)
                except Exception:
                    bullets = []

            # Parse related_tickers from JSONB
            related = row.get("related_tickers", [])
            if isinstance(related, str):
                try:
                    related = json.loads(related)
                except Exception:
                    related = []

            articles.append({
                "id": row.get("id", ""),
                "headline": row.get("headline", ""),
                "summary": row.get("summary"),
                "summary_bullets": bullets,
                "sentiment": row.get("sentiment"),
                "sentiment_confidence": row.get("sentiment_confidence", 0),
                "source_name": row.get("source_name"),
                "source_logo_url": row.get("source_logo_url"),
                "published_at": row.get("published_at"),
                "thumbnail_url": row.get("thumbnail_url"),
                "article_url": row.get("article_url"),
                "related_tickers": related,
                "ai_processed": row.get("ai_processed", False),
            })
        return articles

    # ── Background Pre-warmer ─────────────────────────────────────────

    async def pre_warm_popular_tickers(self, top_n: int = 20):
        """
        Pre-warm news cache for the most popular watchlist tickers.
        Called by the background worker on startup and periodically.
        """
        try:
            result = self.supabase.rpc(
                "get_top_watchlist_tickers", {"n": top_n}
            ).execute()
            tickers = [row["ticker"] for row in (result.data or [])]
        except Exception as e:
            logger.error(f"Failed to get top watchlist tickers: {e}")
            return

        if not tickers:
            logger.info("No watchlist tickers found for pre-warming")
            return

        logger.info(f"Pre-warming news cache for {len(tickers)} tickers: {tickers}")

        # Process in batches of 5 to avoid overwhelming FMP/Gemini
        batch_size = 5
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            tasks = [self.get_ticker_news(t, limit=10) for t in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for t, r in zip(batch, results):
                if isinstance(r, Exception):
                    logger.error(f"Pre-warm failed for {t}: {r}")
                else:
                    count = len(r.get("articles", []))
                    logger.info(f"Pre-warmed {t}: {count} articles")

            # Small delay between batches
            if i + batch_size < len(tickers):
                await asyncio.sleep(2)

        logger.info("News pre-warming complete")

    async def cleanup_expired_cache(self):
        """Delete expired cache entries. Called periodically."""
        try:
            self.supabase.table("ticker_news_cache").delete().lt(
                "expires_at", datetime.now(timezone.utc).isoformat()
            ).execute()
            logger.info("Cleaned up expired news cache entries")
        except Exception as e:
            logger.error(f"Cache cleanup failed: {e}")


# ── Singleton ─────────────────────────────────────────────────────────

_news_cache_service: Optional[NewsCacheService] = None


def get_news_cache_service() -> NewsCacheService:
    global _news_cache_service
    if _news_cache_service is None:
        _news_cache_service = NewsCacheService()
    return _news_cache_service
