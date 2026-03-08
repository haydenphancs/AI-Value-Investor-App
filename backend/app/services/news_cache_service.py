"""
News Cache Service — Fetch 50, Enrich Lazily

Architecture:
  1. get_ticker_news: fetch up to 50 raw articles from FMP, cache ALL in Supabase
     (no Gemini). Return everything so the iOS client can paginate locally.
  2. enrich_articles: given specific article IDs, run Gemini only on those,
     update the cache rows, and return enriched data.
  3. Background pre-warmer keeps popular watchlist tickers warm (raw cache only).
"""

import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.integrations.gemini import get_gemini_client

logger = logging.getLogger(__name__)

# Cache TTL in hours
CACHE_TTL_HOURS = 6

# Gemini model for news enrichment (fast + cheap)
NEWS_AI_MODEL = "gemini-2.5-flash"


class NewsCacheService:
    """Service for fetching and caching news per ticker with lazy AI enrichment."""

    def __init__(self):
        self.supabase = get_supabase()
        self.gemini = get_gemini_client()
        self.fmp = get_fmp_client()

    # ── Public: Get raw/cached news ───────────────────────────────────

    async def get_ticker_news(
        self, ticker: str, limit: int = 50
    ) -> Dict[str, Any]:
        """
        Get news for a ticker. Cache-first, NO automatic AI enrichment.

        Returns:
            dict with keys: articles, ticker, cached, cache_age_seconds
        """
        ticker = ticker.upper()

        # ── 1. Check cache ──
        cached_articles = self._get_cached(ticker, limit)
        if cached_articles:
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

        # ── 2. Cache miss → fetch from FMP, store raw (no Gemini) ──
        logger.info(f"News cache MISS for {ticker}: fetching from FMP")
        try:
            articles = await self._fetch_and_cache_raw(ticker, limit)
            return {
                "articles": articles,
                "ticker": ticker,
                "cached": False,
                "cache_age_seconds": 0,
            }
        except Exception as e:
            logger.error(f"News fetch failed for {ticker}: {e}", exc_info=True)
            return await self._fallback_raw_news(ticker, limit)

    # ── Public: Enrich specific articles on demand ────────────────────

    async def enrich_articles(
        self, ticker: str, article_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """
        AI-enrich specific articles by ID. 'First User Pays' per batch.
        Only processes articles that haven't been enriched yet.

        Returns list of enriched article dicts.
        """
        ticker = ticker.upper()
        if not article_ids:
            return []

        # 1. Fetch the rows from cache by IDs
        try:
            result = (
                self.supabase.table("ticker_news_cache")
                .select("*")
                .eq("ticker", ticker)
                .in_("id", article_ids)
                .execute()
            )
            rows = result.data or []
        except Exception as e:
            logger.error(f"Failed to fetch articles for enrichment: {e}")
            return []

        if not rows:
            logger.info(f"No articles found for enrichment: {article_ids}")
            return []

        # 2. Split into already-enriched and needs-enrichment
        already_enriched = [r for r in rows if r.get("ai_processed")]
        needs_enrichment = [r for r in rows if not r.get("ai_processed")]

        enriched_response = self._format_response(already_enriched)

        if not needs_enrichment:
            logger.info(f"All {len(rows)} articles already enriched for {ticker}")
            return enriched_response

        # 3. Build article dicts for Gemini (use headline + summary as content)
        articles_for_gemini = []
        for row in needs_enrichment:
            articles_for_gemini.append({
                "title": row.get("headline", ""),
                "text": row.get("summary", ""),
            })

        # 4. Batch enrich with Gemini
        enrichments = await self._batch_enrich_articles(articles_for_gemini)

        # 5. Update cache rows with enrichment data
        newly_enriched = []
        for i, row in enumerate(needs_enrichment):
            enrichment = enrichments.get(i, {})
            if not enrichment:
                newly_enriched.append(self._format_single_row(row))
                continue

            update_data = {
                "summary_bullets": json.dumps(enrichment.get("bullets", [])),
                "sentiment": enrichment.get("sentiment"),
                "sentiment_confidence": enrichment.get("confidence", 0),
                "ai_processed": True,
                "ai_model": NEWS_AI_MODEL,
            }

            try:
                self.supabase.table("ticker_news_cache").update(
                    update_data
                ).eq("id", row["id"]).execute()
            except Exception as e:
                logger.error(f"Failed to update enrichment for {row['id']}: {e}")

            # Merge enrichment into row for response
            row.update(update_data)
            newly_enriched.append(self._format_single_row(row))

        logger.info(
            f"Enriched {len(needs_enrichment)} articles for {ticker}"
        )
        return enriched_response + newly_enriched

    # ── Private: Fetch from FMP and cache raw ─────────────────────────

    async def _fetch_and_cache_raw(
        self, ticker: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Fetch from FMP, cache raw in Supabase (no AI enrichment)."""
        raw_articles = await self.fmp.get_stock_news(ticker, limit=limit)
        if not raw_articles:
            logger.info(f"No FMP news found for {ticker}")
            return []

        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=CACHE_TTL_HOURS)

        cache_rows = []
        response_articles = []

        for i, raw in enumerate(raw_articles[:limit]):
            external_id = raw.get("url") or raw.get("title", f"unknown_{i}")

            row = {
                "ticker": ticker,
                "external_id": external_id[:500],
                "headline": raw.get("title", ""),
                "summary": raw.get("text", ""),
                "summary_bullets": json.dumps([]),
                "sentiment": None,
                "sentiment_confidence": 0,
                "source_name": raw.get("publisher") or raw.get("site", ""),
                "source_logo_url": None,
                "published_at": raw.get("publishedDate"),
                "thumbnail_url": raw.get("image"),
                "article_url": raw.get("url"),
                "related_tickers": json.dumps(
                    raw.get("symbol", ticker).split(",")
                    if isinstance(raw.get("symbol"), str)
                    else [ticker]
                ),
                "ai_processed": False,
                "ai_model": None,
                "cached_at": now.isoformat(),
                "expires_at": expires.isoformat(),
            }
            cache_rows.append(row)

            response_articles.append({
                "id": "",
                "headline": row["headline"],
                "summary": row["summary"],
                "summary_bullets": [],
                "sentiment": None,
                "sentiment_confidence": 0,
                "source_name": row["source_name"],
                "source_logo_url": None,
                "published_at": row["published_at"],
                "thumbnail_url": row["thumbnail_url"],
                "article_url": row["article_url"],
                "related_tickers": (
                    raw.get("symbol", ticker).split(",")
                    if isinstance(raw.get("symbol"), str)
                    else [ticker]
                ),
                "ai_processed": False,
            })

        # Upsert into cache
        try:
            result = (
                self.supabase.table("ticker_news_cache")
                .upsert(cache_rows, on_conflict="ticker,external_id")
                .execute()
            )
            if result.data:
                for i, inserted in enumerate(result.data):
                    if i < len(response_articles):
                        response_articles[i]["id"] = inserted.get("id", "")
            logger.info(f"Cached {len(cache_rows)} raw articles for {ticker}")
        except Exception as e:
            logger.error(f"Cache insert failed for {ticker}: {e}")
            for i, art in enumerate(response_articles):
                if not art["id"]:
                    art["id"] = f"temp_{i}"

        return response_articles

    # ── Private: Gemini batch enrichment ──────────────────────────────

    async def _batch_enrich_articles(
        self, articles: List[Dict[str, Any]]
    ) -> Dict[int, Dict[str, Any]]:
        """
        Enrich all articles in a single Gemini API call.
        Returns a dict mapping article index → enrichment data.
        """
        if not articles:
            return {}

        articles_text = []
        for i, art in enumerate(articles):
            title = art.get("title", "")
            text = art.get("text", "")
            if len(text) > 500:
                text = text[:500] + "..."
            articles_text.append(
                f"Article {i}:\nTitle: {title}\nContent: {text}"
            )

        batch_prompt = f"""Analyze the following {len(articles)} financial news articles.

For EACH article, provide:
1. Summary bullet points following these rules:
   - Minimum 2, maximum 5 bullet points
   - Each bullet must be under 25 words — short and punchy
   - The FINAL bullet must always be a "So What?" — explain why an everyday investor should care, in plain English
   - No introductory phrases like "This article discusses..." or "The key points are..."
2. Sentiment classification using these exact rules:
   - "Positive": Strong upward catalyst (e.g., crushing earnings, massive contract win, breakthrough product)
   - "Negative": Strong downward catalyst (e.g., missed earnings, lawsuit, major regulatory action)
   - "None": General commentary, educational content, mixed signals, or no clear directional impact on the stock
3. Confidence score: 0-100 (how confident you are in the sentiment call)

Return a JSON array with one object per article in order. Each object must have:
- "index": the article number (0-based)
- "bullets": array of 2-5 strings (last one is the "So What?")
- "sentiment": "Positive" | "Negative" | "None"
- "confidence": integer 0-100

{chr(10).join(articles_text)}

Return ONLY the JSON array, no markdown fencing or commentary."""

        try:
            response = await self.gemini.generate_json(
                prompt=batch_prompt,
                system_instruction=(
                    "You are an expert financial translator. Your job is to read dense "
                    "financial news and summarize it for everyday investors. Keep the tone "
                    "friendly, accessible and reliable. Must use correct numbers or data "
                    "if needed. Do not use introductory phrases."
                ),
                model_name=NEWS_AI_MODEL,
            )

            text = response.get("text", "")
            parsed = json.loads(text)

            result = {}
            if isinstance(parsed, list):
                for item in parsed:
                    idx = item.get("index", 0)
                    raw_sentiment = (item.get("sentiment") or "").strip().lower()

                    if raw_sentiment == "positive":
                        sentiment = "Positive"
                    elif raw_sentiment == "negative":
                        sentiment = "Negative"
                    else:
                        sentiment = None

                    result[idx] = {
                        "bullets": item.get("bullets", [])[:5],
                        "sentiment": sentiment,
                        "confidence": item.get("confidence", 0),
                    }

            logger.info(
                f"Gemini batch enrichment: {len(result)}/{len(articles)} articles processed"
            )
            return result

        except Exception as e:
            logger.error(f"Gemini batch enrichment failed: {e}", exc_info=True)
            return {}

    # ── Private: Cache lookup ─────────────────────────────────────────

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

    # ── Private: Fallback ─────────────────────────────────────────────

    async def _fallback_raw_news(
        self, ticker: str, limit: int
    ) -> Dict[str, Any]:
        """Fallback: return raw FMP news without caching."""
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
                    "source_name": raw.get("publisher") or raw.get("site", ""),
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

    # ── Private: Format helpers ───────────────────────────────────────

    def _format_single_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Format a single cached DB row into the API response shape."""
        bullets = row.get("summary_bullets", [])
        if isinstance(bullets, str):
            try:
                bullets = json.loads(bullets)
            except Exception:
                bullets = []

        related = row.get("related_tickers", [])
        if isinstance(related, str):
            try:
                related = json.loads(related)
            except Exception:
                related = []

        raw_sentiment = row.get("sentiment")
        sentiment = raw_sentiment if raw_sentiment not in (None, "None", "none", "neutral") else None

        return {
            "id": row.get("id", ""),
            "headline": row.get("headline", ""),
            "summary": row.get("summary"),
            "summary_bullets": bullets,
            "sentiment": sentiment,
            "sentiment_confidence": row.get("sentiment_confidence", 0),
            "source_name": row.get("source_name"),
            "source_logo_url": row.get("source_logo_url"),
            "published_at": row.get("published_at"),
            "thumbnail_url": row.get("thumbnail_url"),
            "article_url": row.get("article_url"),
            "related_tickers": related,
            "ai_processed": row.get("ai_processed", False),
        }

    def _format_response(
        self, cached_rows: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Format cached DB rows into the API response shape."""
        return [self._format_single_row(row) for row in cached_rows]

    # ── Background Pre-warmer ─────────────────────────────────────────

    async def pre_warm_popular_tickers(self, top_n: int = 20):
        """
        Pre-warm news cache for the most popular watchlist tickers.
        Fetches raw articles only (no Gemini) to keep cache warm.
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

        batch_size = 5
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            tasks = [self.get_ticker_news(t, limit=50) for t in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for t, r in zip(batch, results):
                if isinstance(r, Exception):
                    logger.error(f"Pre-warm failed for {t}: {r}")
                else:
                    count = len(r.get("articles", []))
                    logger.info(f"Pre-warmed {t}: {count} articles")

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
