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
from app.integrations.gemini import get_gemini_client, GeminiQuotaError

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
        self, ticker: str, limit: int = 50, is_crypto: bool = False,
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
            articles = await self._fetch_and_cache_raw(ticker, limit, is_crypto=is_crypto)
            return {
                "articles": articles,
                "ticker": ticker,
                "cached": False,
                "cache_age_seconds": 0,
            }
        except Exception as e:
            logger.error(f"News fetch failed for {ticker}: {e}", exc_info=True)
            return await self._fallback_raw_news(ticker, limit)

    # ── Public: Get index news (constituent-based) ─────────────────────

    async def get_index_news(
        self, symbol: str, limit: int = 50, news_tickers: str = "",
    ) -> Dict[str, Any]:
        """
        Get news for an index. Uses the index symbol as the cache key
        but fetches news for its top constituent tickers from FMP.

        Args:
            symbol: Index symbol (e.g., "^GSPC") — used as cache key
            limit: Max articles
            news_tickers: Comma-separated constituent tickers for FMP query
        """
        symbol = symbol.upper()

        # 1. Check cache (keyed by index symbol)
        cached_articles = self._get_cached(symbol, limit)
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

            logger.info(f"Index news cache HIT for {symbol}: {len(cached_articles)} articles")
            return {
                "articles": self._format_response(cached_articles),
                "ticker": symbol,
                "cached": True,
                "cache_age_seconds": age,
            }

        # 2. Cache miss → fetch from FMP using constituent tickers
        logger.info(f"Index news cache MISS for {symbol}: fetching via tickers={news_tickers}")
        try:
            articles = await self._fetch_and_cache_index_news(
                symbol, news_tickers, limit
            )
            return {
                "articles": articles,
                "ticker": symbol,
                "cached": False,
                "cache_age_seconds": 0,
            }
        except Exception as e:
            logger.error(f"Index news fetch failed for {symbol}: {e}", exc_info=True)
            return {
                "articles": [],
                "ticker": symbol,
                "cached": False,
                "cache_age_seconds": None,
            }

    async def _fetch_and_cache_index_news(
        self, symbol: str, news_tickers: str, limit: int,
    ) -> List[Dict[str, Any]]:
        """Fetch news for constituent tickers, cache under the index symbol."""
        raw_articles = await self.fmp.get_stock_news(
            news_tickers if news_tickers else None, limit=limit
        )
        if not raw_articles:
            logger.info(f"No FMP news found for index {symbol} (tickers={news_tickers})")
            return []

        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=CACHE_TTL_HOURS)

        cache_rows = []
        response_articles = []
        ext_ids: List[str] = []
        seen_external_ids: set = set()

        for i, raw in enumerate(raw_articles[:limit]):
            external_id = raw.get("url") or raw.get("title", f"unknown_{i}")
            ext_key = external_id[:500]
            if ext_key in seen_external_ids:
                continue
            seen_external_ids.add(ext_key)
            ext_ids.append(ext_key)

            row = {
                "ticker": symbol,  # Cache key = index symbol
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
                "related_tickers": self._parse_tickers(raw, symbol),
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
                "related_tickers": self._parse_tickers(raw, symbol),
                "ai_processed": False,
            })

        # Upsert into cache
        try:
            result = (
                self.supabase.table("ticker_news_cache")
                .upsert(cache_rows, on_conflict="ticker,external_id")
                .execute()
            )
            # Match ids by external_id (RETURNING order is not guaranteed to match
            # VALUES order → a positional zip could misattribute the id/enrichment).
            id_by_ext = {
                r.get("external_id"): r.get("id", "")
                for r in (result.data or [])
                if r.get("external_id")
            }
            for art, ext in zip(response_articles, ext_ids):
                art["id"] = id_by_ext.get(ext, "")
            logger.info(f"Cached {len(cache_rows)} index news articles for {symbol}")
        except Exception as e:
            logger.error(f"Index news cache insert failed for {symbol}: {e}")

        for i, art in enumerate(response_articles):
            if not art["id"]:
                art["id"] = f"temp_{i}"

        return response_articles

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
        enrichments = await self._batch_enrich_articles(articles_for_gemini, ticker=ticker)

        if not enrichments:
            logger.warning(
                f"Gemini enrichment returned empty for {ticker} "
                f"({len(needs_enrichment)} articles) — returning unenriched data"
            )
            # Return unenriched rows so iOS knows ai_processed=false
            return enriched_response + [
                self._format_single_row(r) for r in needs_enrichment
            ]

        # 5. Update cache rows with enrichment data (concurrent)
        newly_enriched = []
        update_tasks = []
        update_indices = []

        for i, row in enumerate(needs_enrichment):
            enrichment = enrichments.get(i, {})
            if not enrichment:
                newly_enriched.append(self._format_single_row(row))
                continue

            # Merge Gemini-extracted tickers with existing FMP-derived tickers
            gemini_tickers = enrichment.get("related_tickers", [])
            existing_tickers = row.get("related_tickers", [])
            if isinstance(existing_tickers, str):
                try:
                    existing_tickers = json.loads(existing_tickers)
                except Exception:
                    existing_tickers = []
            merged_tickers = list(
                dict.fromkeys(existing_tickers + gemini_tickers)
            )[:8]

            update_data = {
                "summary_bullets": json.dumps(enrichment.get("bullets", [])),
                "sentiment": self._normalize_sentiment(enrichment.get("sentiment", "")),
                "sentiment_confidence": enrichment.get("confidence", 0),
                "related_tickers": merged_tickers,
                "ai_processed": True,
                "ai_model": NEWS_AI_MODEL,
            }

            # Merge enrichment into row for response
            row.update(update_data)
            newly_enriched.append(self._format_single_row(row))

            # Queue concurrent DB update
            update_tasks.append(self._update_enrichment_row(row["id"], update_data))
            update_indices.append(i)

        # Execute all DB updates concurrently
        if update_tasks:
            results = await asyncio.gather(*update_tasks, return_exceptions=True)
            success_count = sum(1 for r in results if not isinstance(r, Exception))
            for j, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(f"Failed to update enrichment for article {update_indices[j]}: {r}")
        else:
            success_count = 0

        logger.info(
            f"Enriched {success_count}/{len(needs_enrichment)} articles for {ticker}"
        )
        return enriched_response + newly_enriched

    async def _update_enrichment_row(self, row_id: str, update_data: dict):
        """Update a single enrichment row in Supabase."""
        self.supabase.table("ticker_news_cache").update(
            update_data
        ).eq("id", row_id).execute()

    # ── Private: Ticker parsing helper ──────────────────────────────────

    @staticmethod
    def _parse_tickers(raw: dict, fallback_ticker: str, max_tickers: int = 8) -> list:
        """Split FMP's comma-separated symbol string into a clean list."""
        symbol = raw.get("symbol")
        if isinstance(symbol, str):
            tickers = [t.strip() for t in symbol.split(",") if t.strip()]
        else:
            tickers = [fallback_ticker]
        return tickers[:max_tickers]

    # ── Private: Fetch from FMP and cache raw ─────────────────────────

    async def _fetch_and_cache_raw(
        self, ticker: str, limit: int, is_crypto: bool = False,
    ) -> List[Dict[str, Any]]:
        """Fetch from FMP, cache raw in Supabase (no AI enrichment)."""
        if is_crypto:
            raw_articles = await self.fmp.get_crypto_news(ticker, limit=limit)
        else:
            raw_articles = await self.fmp.get_stock_news(ticker, limit=limit)
        if not raw_articles:
            logger.info(f"No FMP news found for {ticker}")
            return []

        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=CACHE_TTL_HOURS)

        cache_rows = []
        response_articles = []
        ext_ids: List[str] = []
        seen_external_ids: set = set()

        for i, raw in enumerate(raw_articles[:limit]):
            external_id = (raw.get("url") or raw.get("title") or f"unknown_{i}")[:500]
            # Dedup within the batch: two FMP articles sharing a url/title yield the
            # same (ticker, external_id), and a single ON CONFLICT upsert that touches
            # the same row twice raises Postgres "cannot affect row a second time" →
            # the WHOLE upsert aborts → every id degrades to temp_N → enrichment is
            # permanently disabled for this ticker (iOS filters out temp_ ids).
            # Mirrors _fetch_and_cache_index_news.
            if external_id in seen_external_ids:
                continue
            seen_external_ids.add(external_id)
            ext_ids.append(external_id)

            row = {
                "ticker": ticker,
                "external_id": external_id,
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
                "related_tickers": self._parse_tickers(raw, ticker),
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
                "related_tickers": self._parse_tickers(raw, ticker),
                "ai_processed": False,
            })

        # Upsert into cache
        try:
            result = (
                self.supabase.table("ticker_news_cache")
                .upsert(cache_rows, on_conflict="ticker,external_id")
                .execute()
            )
            # Assign the DB id by external_id match. Postgres does NOT guarantee that
            # the RETURNING rows come back in VALUES order, so a positional zip could
            # attach the wrong id — and thus the wrong enrichment — to an article.
            id_by_ext = {
                r.get("external_id"): r.get("id", "")
                for r in (result.data or [])
                if r.get("external_id")
            }
            for art, ext in zip(response_articles, ext_ids):
                art["id"] = id_by_ext.get(ext, "")
            logger.info(f"Cached {len(cache_rows)} raw articles for {ticker}")
        except Exception as e:
            logger.error(f"Cache insert failed for {ticker}: {e}")

        # Any article the upsert didn't yield an id for → temp fallback (still renders,
        # just not enrichable until the next cache cycle).
        for i, art in enumerate(response_articles):
            if not art["id"]:
                art["id"] = f"temp_{i}"

        return response_articles

    # ── Private: Gemini batch enrichment ──────────────────────────────

    # Gemini response schema for structured output enforcement
    _ENRICHMENT_SCHEMA = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "index": {"type": "INTEGER"},
                "bullets": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                },
                "sentiment": {
                    "type": "STRING",
                    "enum": ["bullish", "bearish", "neutral"],
                },
                "confidence": {"type": "INTEGER"},
                "related_tickers": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                },
            },
            "required": ["index", "bullets", "sentiment", "confidence"],
        },
    }

    @staticmethod
    def _normalize_sentiment(raw: str) -> str:
        """Normalize any sentiment string to DB-compatible bullish/bearish/neutral."""
        s = (raw or "").strip().lower()
        if s in ("positive", "bullish"):
            return "bullish"
        if s in ("negative", "bearish"):
            return "bearish"
        return "neutral"

    @staticmethod
    def _map_enrichments(parsed: Any, expected_count: int) -> Dict[int, Dict[str, Any]]:
        """Map a Gemini enrichment array to {position: enrichment} by POSITIONAL order.

        Deliberately IGNORES each item's self-reported ``index`` field: Gemini can
        emit duplicate / missing / 1-based index values, and keying on ``item["index"]``
        (default 0) then binds one article's bullets+sentiment to a DIFFERENT article
        (silent wrong-data). The structured-output array is one object per article in
        INPUT order, so position is authoritative.

        Returns ``{}`` when the array shape doesn't match the input count, so the
        caller degrades to unenriched-and-retryable instead of risking misattribution.
        """
        if not isinstance(parsed, list) or len(parsed) != expected_count:
            return {}
        result: Dict[int, Dict[str, Any]] = {}
        for pos, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            raw_tickers = item.get("related_tickers", []) or []
            cleaned_tickers = list(
                dict.fromkeys(
                    t.strip().upper()
                    for t in raw_tickers
                    if isinstance(t, str) and t.strip()
                )
            )[:8]
            result[pos] = {
                "bullets": (item.get("bullets", []) or [])[:5],
                "sentiment": NewsCacheService._normalize_sentiment(item.get("sentiment", "")),
                "confidence": item.get("confidence", 0),
                "related_tickers": cleaned_tickers,
            }
        return result

    async def _batch_enrich_articles(
        self, articles: List[Dict[str, Any]], ticker: str = ""
    ) -> Dict[int, Dict[str, Any]]:
        """
        Enrich all articles in a single Gemini API call.
        Returns a dict mapping article index → enrichment data.
        Falls back to Neutral sentiment for each article on any failure.
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
   - The FINAL bullet must always explain why an everyday investor should care, in plain English
   - Transition Rule: To sound natural and human, vary how you start this final bullet. Sometimes use a short, friendly transition like "So,", "In short,", "Ultimately,", or "The takeaway:". Other times, just state the insight directly without any introductory phrase at all. NEVER use "So What?" or "So what:" as a prefix.
   - No introductory phrases like "This article discusses..." or "The key points are..."
2. Sentiment classification — you MUST use one of these three exact values:
   - "bullish": ONLY use if the article indicates a direct upward catalyst for the stock price (e.g., earnings beat, new product launch, analyst upgrade, winning a lawsuit, major contract win, breakthrough product approval).
   - "bearish": ONLY use if the article indicates a direct downward catalyst for the stock price (e.g., missed revenue, SEC investigation, product recall, analyst downgrade, lawsuit loss, executive fraud, data breach).
   - "neutral": Use for EVERYTHING else — macroeconomic noise, educational articles, history lessons, CEO interviews without financial guidance, mixed signals, industry commentary, or any article where the directional impact on the stock is unclear.
3. Confidence score: 0-100 (how confident you are in the sentiment call)
4. Related tickers: Extract ALL US-listed stock ticker symbols (e.g., AAPL, MSFT, GOOGL) explicitly mentioned or clearly referenced in the article. Only include real ticker symbols — no crypto, indices, ETFs, or made-up symbols. Maximum 8 tickers.

{f'These articles were fetched for ticker {ticker}. Always include {ticker} in related_tickers if the article is relevant to it.' if ticker else ''}

Return a JSON array with one object per article in order. Each object must have:
- "index": the article number (0-based)
- "bullets": array of 2-5 strings (last one explains why investors should care — vary the opening naturally)
- "sentiment": exactly one of "bullish" | "bearish" | "neutral"
- "confidence": integer 0-100
- "related_tickers": array of uppercase ticker symbol strings (max 8)

{chr(10).join(articles_text)}"""

        try:
            response = await self.gemini.generate_json(
                prompt=batch_prompt,
                system_instruction=(
                    "You are an expert financial translator. Your job is to read dense "
                    "financial news and summarize it for everyday investors. Keep the tone "
                    "friendly, accessible and reliable. Must use correct numbers or data "
                    "if needed. Do not use introductory phrases. "
                    "For sentiment, you MUST return exactly one of: bullish, bearish, neutral. "
                    "No other values are accepted."
                ),
                model_name=NEWS_AI_MODEL,
                response_schema=self._ENRICHMENT_SCHEMA,
            )

            text = response.get("text", "")
            parsed = json.loads(text)

            result = self._map_enrichments(parsed, len(articles))
            if not result:
                logger.warning(
                    f"Gemini enrichment shape mismatch for {ticker} "
                    f"(expected {len(articles)}) — returning unenriched (retryable)"
                )
            logger.info(
                f"Gemini batch enrichment: {len(result)}/{len(articles)} articles processed"
            )
            return result

        except json.JSONDecodeError as e:
            # The LLM returned non-JSON / truncated output — an EXPECTED degradation,
            # not a code bug. Returning {} makes the caller retry once Gemini recovers;
            # WARNING keeps it OUT of Sentry (at ERROR it pages on every malformed
            # response, which happens routinely under load / long prompts).
            logger.warning(
                f"Gemini batch enrichment returned malformed JSON for {ticker or '<mixed>'}: {e}"
            )
            return {}
        except Exception as e:
            # Quota / 429 rate-limit is a known, transient capacity condition already
            # governed by the Gemini quota circuit breaker — an EXPECTED degradation, so
            # log at WARNING (not an ERROR-level Sentry page). `GeminiQuotaError` is the
            # typed signal; the string check also catches a quota error that arrived
            # wrapped/untyped. Anything else is unexpected → ERROR with a stack.
            emsg = str(e).lower()
            is_quota = isinstance(e, GeminiQuotaError) or any(
                s in emsg for s in ("429", "quota", "resource_exhausted", "rate limit")
            )
            if is_quota:
                logger.warning(
                    f"Gemini batch enrichment quota-limited for {ticker or '<mixed>'}: {e}"
                )
                return {}
            logger.error(f"Gemini batch enrichment failed for {ticker or '<mixed>'}: {e}", exc_info=True)
            # Return EMPTY (NOT a per-article neutral dict). A non-empty fallback made
            # the caller persist ai_processed=True with empty bullets + a forced
            # 'neutral' sentiment, poisoning the SHARED 6h cache: every user then saw
            # no AI summary and a wrong 'neutral' badge (even for an earnings beat /
            # SEC probe) with no retry. Returning {} makes enrich_articles take its
            # 'return unenriched' branch — ai_processed stays False, so the next
            # request retries once Gemini recovers.
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
                    "related_tickers": self._parse_tickers(raw, ticker),
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

        sentiment = self._normalize_sentiment(row.get("sentiment", ""))

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
