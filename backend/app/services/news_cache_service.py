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
from app.integrations.fmp import (
    FMPAuthException,
    FMPRateLimitException,
    get_fmp_client,
)
from app.integrations.gemini import get_gemini_client, GeminiQuotaError

logger = logging.getLogger(__name__)

# Cache TTL in hours
CACHE_TTL_HOURS = 6

# Gemini model for news enrichment (fast + cheap)
NEWS_AI_MODEL = "gemini-2.5-flash"

# Reserved cache key for the general (non-ticker-specific) market news feed.
# It lives in `ticker_news_cache` alongside real tickers so the whole existing
# spine — cache lookup, 6h TTL, enrichment, cleanup — applies unchanged. The
# double-underscore form cannot collide with a real symbol (FMP symbols are
# alphanumeric plus `.`/`-`/`^`).
MARKET_SCOPE = "__MARKET__"

# Index / broad-market proxies whose news IS market news: the S&P 500, Nasdaq
# and Dow, via both their ETF tickers and their index symbols. Blended with
# `news/general-latest` to build the Market feed.
#
# WHY A BLEND: FMP's `news/stock` with no `symbols` param does NOT return
# general news — it falls back to a single default symbol (AAPL), so the Market
# tab used to be 100% Apple. `news/general-latest` supplies the macro narrative
# but carries no symbols; the index basket supplies "S&P 500 hits resistance"
# style coverage. Deliberately EXCLUDES `news/stock-latest`: it is a firehose of
# small-cap earnings-call recaps that, sorted by recency, bury the market story.
MARKET_INDEX_SYMBOLS = "SPY,QQQ,DIA,^GSPC,^IXIC"

# How far back a sweeper REFRESH reaches. Must be >= the span a cold
# `get_stock_news(limit=50)` returns (~3-4 days), or the refresh re-stamps only
# the newest slice and the cache silently decays to today-only. See
# `refresh_scope_news`.
REFRESH_LOOKBACK_HOURS = 96


def _has_waiters(fut: asyncio.Future) -> bool:
    """Whether anything is awaiting `fut`.

    `asyncio.Future._callbacks` is private but stable across CPython 3.8-3.13 and
    is the only way to ask this. Guarded so a future CPython change degrades to
    "assume waiters" (the pre-existing behaviour) rather than raising.
    """
    try:
        return bool(getattr(fut, "_callbacks", None))
    except Exception:
        return True


class NewsCacheService:
    """Service for fetching and caching news per ticker with lazy AI enrichment."""

    # Runaway guard for the paged bulk read: 20 x 1000 rows is far beyond any
    # real cache size (the table holds ~900 fresh rows today), so hitting it
    # means something is wrong rather than that the data is large.
    _MAX_BULK_PAGES = 20

    def __init__(self):
        self.supabase = get_supabase()
        self.gemini = get_gemini_client()
        self.fmp = get_fmp_client()
        # Thundering-herd guard (CLAUDE.md invariant #4). Matters most on a cold
        # market cache — weekends, and the minutes after a Railway redeploy —
        # when every concurrent /updates/feed would otherwise fire its own FMP
        # call AND its own 50-row upsert.
        self._inflight: Dict[str, asyncio.Future] = {}
        # Second thundering-herd guard, for the PAID path. `enrich_articles`
        # skips rows already marked `ai_processed`, but that check races: two
        # users opening the same un-enriched ticker both read `ai_processed=false`
        # before either writes, so both call the (expensive) `flash` model for the
        # SAME article ids. This dedups by exact batch so the second caller awaits
        # the first's Gemini call instead of paying for it again.
        self._enrich_inflight: Dict[str, asyncio.Future] = {}

    # ── Public: Get raw/cached news ───────────────────────────────────

    async def get_ticker_news(
        self, ticker: str, limit: int = 50, is_crypto: bool = False,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get news for a ticker. Cache-first, NO automatic AI enrichment.

        ``offset > 0`` pages through already-cached history and never triggers
        an FMP fetch — see :meth:`get_market_news`.

        Returns:
            dict with keys: articles, ticker, cached, cache_age_seconds
        """
        ticker = ticker.upper()

        # ── 1. Check cache ──
        # The Supabase SDK is synchronous; called directly it would block the
        # event loop for the whole round-trip on the hottest read of this tab.
        cached_articles = await asyncio.to_thread(
            self._get_cached, ticker, limit, offset
        )
        if offset > 0:
            return {
                "articles": self._format_response(cached_articles),
                "ticker": ticker,
                "cached": True,
                "cache_age_seconds": self._cache_age_seconds(cached_articles),
            }
        if cached_articles:
            logger.info(f"News cache HIT for {ticker}: {len(cached_articles)} articles")
            return {
                "articles": self._format_response(cached_articles),
                "ticker": ticker,
                "cached": True,
                "cache_age_seconds": self._cache_age_seconds(cached_articles),
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
        except (FMPRateLimitException, FMPAuthException):
            # Must NOT degrade to an empty feed. Doing so made an exhausted FMP
            # quota indistinguishable from "this ticker has no news" — the user
            # saw a blank screen, FMP_RATE_LIMITED could never reach them
            # (invariant #3), and the retry below burned a SECOND call while
            # already over quota. The endpoint maps this to a structured error.
            raise
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
            logger.info(f"Index news cache HIT for {symbol}: {len(cached_articles)} articles")
            return {
                "articles": self._format_response(cached_articles),
                "ticker": symbol,
                "cached": True,
                "cache_age_seconds": self._cache_age_seconds(cached_articles),
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
        except (FMPRateLimitException, FMPAuthException):
            raise  # see get_ticker_news — quota must not masquerade as "no news"
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
        return self._build_and_cache_rows(
            cache_key=symbol, raw_articles=raw_articles, limit=limit,
            fallback_ticker=symbol, label=f"index {symbol}",
        )

    # ── Public: Get general market news ────────────────────────────────

    async def get_market_news(
        self, limit: int = 50, offset: int = 0
    ) -> Dict[str, Any]:
        """
        Get general (non-ticker-specific) market news.

        Cached in `ticker_news_cache` under the reserved ``MARKET_SCOPE`` key so
        the existing 6h TTL, on-demand `enrich_articles`, and `cleanup_expired_cache`
        all apply with no changes. Mirrors :meth:`get_ticker_news`'s envelope.

        ``offset > 0`` is a PAGE-THROUGH of already-cached history and never
        triggers an FMP fetch: a cold miss on page 3 means the history simply
        ends there, and refetching page 1 from upstream to satisfy it would burn
        quota to return rows the client already has.
        """
        # Sync SDK — keep it off the event loop. This is the Updates screen's
        # default tab, so it is the hottest read in the feature.
        cached_articles = await asyncio.to_thread(
            self._get_cached, MARKET_SCOPE, limit, offset
        )
        if offset > 0:
            return {
                "articles": self._format_response(cached_articles),
                "ticker": MARKET_SCOPE,
                "cached": True,
                "cache_age_seconds": self._cache_age_seconds(cached_articles),
            }
        if cached_articles:
            logger.info(
                f"Market news cache HIT: {len(cached_articles)} articles"
            )
            return {
                "articles": self._format_response(cached_articles),
                "ticker": MARKET_SCOPE,
                "cached": True,
                "cache_age_seconds": self._cache_age_seconds(cached_articles),
            }

        # Dedup concurrent misses: one FMP fetch, N awaiters.
        inflight = self._inflight.get(MARKET_SCOPE)
        if inflight is not None:
            logger.info("Market news fetch already in flight — joining")
            return await inflight

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[MARKET_SCOPE] = fut
        try:
            result = await self._fetch_market_news(limit)
            if not fut.done():
                fut.set_result(result)
            return result
        except BaseException as e:
            # BaseException, not Exception: a CancelledError must still resolve
            # the future, or every joiner hangs forever waiting on a dead fetch.
            if not fut.done():
                # Only hand the exception to the future when someone is actually
                # waiting on it. With no joiner, an unretrieved future exception
                # produces a "Future exception was never retrieved" traceback on
                # GC for every single failure — pure log/Sentry noise.
                if _has_waiters(fut):
                    fut.set_exception(e)
                else:
                    fut.cancel()
            raise
        finally:
            self._inflight.pop(MARKET_SCOPE, None)

    async def _fetch_market_raw(
        self, limit: int, from_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Fetch + merge the market corpus: macro narrative plus index coverage.

        Both legs run concurrently and are merged newest-first, deduped by URL.
        A failure in either leg degrades to whatever the other returned rather
        than emptying the feed.
        """
        general, index = await asyncio.gather(
            self.fmp.get_general_news(limit=limit),
            self.fmp.get_stock_news(
                MARKET_INDEX_SYMBOLS, limit=limit, from_date=from_date
            ),
            return_exceptions=True,
        )

        merged: List[Dict[str, Any]] = []
        seen: set = set()
        for leg_name, leg in (("general", general), ("index", index)):
            if isinstance(leg, BaseException):
                # Surface a quota failure; a soft failure just loses one leg.
                if isinstance(leg, (FMPRateLimitException, FMPAuthException)):
                    raise leg
                logger.warning(
                    "Market news %s leg failed: %s: %s",
                    leg_name, type(leg).__name__, leg,
                )
                continue
            for row in leg or []:
                if not isinstance(row, dict):
                    continue
                key = row.get("url") or row.get("title")
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(row)

        # Newest first. `publishedDate` is a sortable "YYYY-MM-DD HH:MM:SS"
        # string; a missing date sorts last rather than crashing the sort.
        merged.sort(key=lambda r: str(r.get("publishedDate") or ""), reverse=True)
        logger.info(
            "Market corpus: %d unique articles (general=%s, index=%s)",
            len(merged),
            "ok" if not isinstance(general, BaseException) else "failed",
            "ok" if not isinstance(index, BaseException) else "failed",
        )
        return merged[:limit]

    async def _fetch_market_news(self, limit: int) -> Dict[str, Any]:
        logger.info("Market news cache MISS: fetching general + index news from FMP")
        try:
            raw_articles = await self._fetch_market_raw(limit)
            if not raw_articles:
                logger.warning("FMP returned no general market news")
                return {
                    "articles": [], "ticker": MARKET_SCOPE,
                    "cached": False, "cache_age_seconds": 0,
                }
            articles = await asyncio.to_thread(
                self._build_and_cache_rows,
                MARKET_SCOPE, raw_articles, limit,
                # No fallback ticker: a general market story with no FMP `symbol`
                # genuinely relates to nothing in particular. Stamping it with
                # "__MARKET__" would surface a fake ticker chip in the iOS UI.
                fallback_ticker=None, label="market",
            )
            return {
                "articles": articles, "ticker": MARKET_SCOPE,
                "cached": False, "cache_age_seconds": 0,
            }
        except (FMPRateLimitException, FMPAuthException):
            # Propagate so the endpoint maps it to a structured error instead of
            # an empty feed. `isinstance`, not a type-NAME compare: the latter
            # misses subclasses.
            raise
        except Exception as e:
            logger.error(
                f"Market news fetch failed: {type(e).__name__}: {e}", exc_info=True
            )
            return {
                "articles": [], "ticker": MARKET_SCOPE,
                "cached": False, "cache_age_seconds": None,
            }

    # ── Private: shared row build + upsert ─────────────────────────────

    def _build_and_cache_rows(
        self,
        cache_key: str,
        raw_articles: List[Dict[str, Any]],
        limit: int,
        fallback_ticker: Optional[str],
        label: str,
        ingest_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Turn raw FMP articles into cache rows + API response, and upsert them.

        Shared by the ticker, index, and market fetch paths — these were three
        byte-identical copies that had already drifted (the index copy used
        ``raw.get("title", f"unknown_{i}")``, which returns ``None`` — and then
        crashes on ``[:500]`` — when the key exists with a null value, whereas
        the ticker copy used a safe ``or`` chain).

        ``cache_key`` is the ``ticker`` column value (a real symbol, an index
        symbol, or ``MARKET_SCOPE``). ``fallback_ticker`` is what to record in
        ``related_tickers`` when FMP omits ``symbol``; pass ``None`` to record
        nothing rather than a synthetic chip.

        ``ingest_only=True`` OMITS the AI-enrichment columns from the upserted
        row. This is essential for any REFRESH of articles that may already be
        cached: PostgREST's default `merge-duplicates` resolution issues
        `DO UPDATE SET` for every column present in the payload, so including
        the enrichment columns would reset `summary_bullets`/`sentiment`/
        `ai_processed` back to empty on every refresh — silently destroying
        enrichment that users already paid Gemini for, on a cache SHARED with
        the ticker/crypto/index/commodity detail screens.
        """
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=CACHE_TTL_HOURS)

        cache_rows: List[Dict[str, Any]] = []
        response_articles: List[Dict[str, Any]] = []
        ext_ids: List[str] = []
        seen_external_ids: set = set()

        for i, raw in enumerate(raw_articles[:limit]):
            if not isinstance(raw, dict):
                logger.warning(
                    "Skipping non-dict FMP news item at index %d for %s: %r",
                    i, label, type(raw).__name__,
                )
                continue

            external_id = (raw.get("url") or raw.get("title") or f"unknown_{i}")[:500]
            # Dedup within the batch: two FMP articles sharing a url/title yield the
            # same (ticker, external_id), and a single ON CONFLICT upsert that touches
            # the same row twice raises Postgres "cannot affect row a second time" →
            # the WHOLE upsert aborts → every id degrades to temp_N → enrichment is
            # permanently disabled for this key (iOS filters out temp_ ids).
            if external_id in seen_external_ids:
                continue
            seen_external_ids.add(external_id)
            ext_ids.append(external_id)

            related = self._parse_tickers(raw, fallback_ticker)

            row = {
                "ticker": cache_key,
                "external_id": external_id,
                "headline": raw.get("title") or "",
                "summary": raw.get("text") or "",
                "source_name": raw.get("publisher") or raw.get("site") or "",
                "source_logo_url": None,
                "published_at": raw.get("publishedDate"),
                "thumbnail_url": raw.get("image"),
                "article_url": raw.get("url"),
                # `expires_at` IS re-stamped on a refresh — that is what keeps an
                # already-cached article alive instead of aging out.
                "expires_at": expires.isoformat(),
            }
            if not ingest_only:
                # Only a first-time fetch may (re-)initialise these. On a REFRESH
                # every one of them would be clobbered back to its empty value:
                #
                #  * the AI columns — wiping enrichment users already paid for;
                #  * `related_tickers` — FMP's raw `symbol` list does NOT include
                #    the extra symbols Gemini extracts during enrichment, so
                #    re-writing it strips the related-ticker chips permanently
                #    (`ai_processed` stays true, so nothing ever re-enriches);
                #  * `cached_at` — SentimentService derives its 4-hour staleness
                #    check from max(cached_at). Re-stamping it every 15 minutes
                #    means that check never trips and the 14-day sentiment corpus
                #    is never rebuilt.
                row.update({
                    "related_tickers": related,
                    "cached_at": now.isoformat(),
                    "summary_bullets": json.dumps([]),
                    "sentiment": None,
                    "sentiment_confidence": 0,
                    "ai_processed": False,
                    "ai_model": None,
                })
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
                "related_tickers": related,
                "ai_processed": False,
            })

        if not cache_rows:
            logger.info("No usable FMP news rows for %s", label)
            return []

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
            logger.info("Cached %d raw articles for %s", len(cache_rows), label)
        except Exception as e:
            logger.error(
                "Cache insert failed for %s: %s: %s", label, type(e).__name__, e
            )

        # Any article the upsert didn't yield an id for → temp fallback (still renders,
        # just not enrichable until the next cache cycle).
        for i, art in enumerate(response_articles):
            if not art["id"]:
                art["id"] = f"temp_{i}"

        return response_articles

    @staticmethod
    def _cache_age_seconds(cached_articles: List[Dict[str, Any]]) -> int:
        """Age of the OLDEST row in the cached set, in seconds (0 on any error)."""
        try:
            oldest = min(
                (a.get("cached_at") or datetime.now(timezone.utc).isoformat())
                for a in cached_articles
            )
            cached_time = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
            return max(0, int((datetime.now(timezone.utc) - cached_time).total_seconds()))
        except Exception as e:
            logger.warning(
                "Cache-age computation failed: %s: %s", type(e).__name__, e
            )
            return 0

    # ── Public: Enrich specific articles on demand ────────────────────

    async def enrich_articles(
        self, ticker: str, article_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """
        AI-enrich specific articles by ID. 'First User Pays' per batch.
        Only processes articles that haven't been enriched yet.

        Concurrent callers requesting the SAME batch are deduped onto one
        Gemini call — without this the `ai_processed` skip races and both pay
        (see `_enrich_inflight`). The key is the ticker plus the sorted ids, so
        two users viewing the same feed (which yields ids in the same order)
        collapse to one call; a different id set is a different, independent key.

        Returns list of enriched article dicts.
        """
        ticker = ticker.upper()
        if not article_ids:
            return []

        # Dedup key: exact batch. `dict.fromkeys` drops duplicate ids while
        # keeping the set stable; sorted() makes the key order-independent.
        key = f"{ticker}|" + "|".join(sorted(dict.fromkeys(article_ids)))
        inflight = self._enrich_inflight.get(key)
        if inflight is not None:
            # Someone is already enriching this exact batch — await their result
            # rather than firing a second Gemini call.
            try:
                return await inflight
            except Exception:
                # The leader failed; fall through and try once ourselves rather
                # than propagating their error to every joiner.
                pass

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._enrich_inflight[key] = fut
        try:
            result = await self._enrich_articles_uncached(ticker, article_ids)
            if not fut.done():
                fut.set_result(result)
            return result
        except Exception as e:
            if not fut.done():
                # Only hand the exception to the future when someone is waiting,
                # else an unretrieved future exception logs noisily on GC (same
                # rule as `get_market_news`).
                if _has_waiters(fut):
                    fut.set_exception(e)
                else:
                    fut.cancel()
            raise
        finally:
            self._enrich_inflight.pop(key, None)

    async def _enrich_articles_uncached(
        self, ticker: str, article_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """The actual enrichment. Always call via :meth:`enrich_articles`, which
        adds the concurrent-batch dedup."""
        ticker = ticker.upper()
        if not article_ids:
            return []

        # 1. Fetch the rows from cache by IDs
        def _select():
            return (
                self.supabase.table("ticker_news_cache")
                .select("*")
                .eq("ticker", ticker)
                .in_("id", article_ids)
                .execute()
            )

        try:
            # Off-thread: sync SDK on a request path (POST /updates/news/enrich).
            result = await asyncio.to_thread(_select)
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
        """Update a single enrichment row in Supabase.

        Off-thread: the Supabase SDK is synchronous, so with the body inline this
        coroutine had NO await point — the `asyncio.gather` fan-out above was
        purely decorative. 25-50 enrichment writes ran strictly serially AND
        parked the event loop for the whole batch, stalling every other request
        on the instance each time someone opened a news feed.
        """
        def _do() -> None:
            self.supabase.table("ticker_news_cache").update(
                update_data
            ).eq("id", row_id).execute()

        await asyncio.to_thread(_do)

    # ── Private: Ticker parsing helper ──────────────────────────────────

    @staticmethod
    def _parse_tickers(
        raw: dict, fallback_ticker: Optional[str], max_tickers: int = 8
    ) -> list:
        """Split FMP's comma-separated symbol string into a clean list.

        ``fallback_ticker`` is used only when FMP omits ``symbol``. Pass ``None``
        (the general-market case) to record no related tickers at all — a
        synthetic value would render as a real ticker chip in the iOS UI.
        """
        symbol = raw.get("symbol")
        if isinstance(symbol, str) and symbol.strip():
            tickers = [t.strip().upper() for t in symbol.split(",") if t.strip()]
        elif fallback_ticker:
            tickers = [fallback_ticker.upper()]
        else:
            tickers = []
        # De-dup while preserving order: FMP occasionally repeats a symbol.
        return list(dict.fromkeys(tickers))[:max_tickers]

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
        return self._build_and_cache_rows(
            cache_key=ticker, raw_articles=raw_articles, limit=limit,
            fallback_ticker=ticker, label=ticker,
        )

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

    def _get_cached(
        self, ticker: str, limit: int, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Query ticker_news_cache for fresh (non-expired) rows.

        ``offset`` pages deeper into the retained history. The sweeper refreshes
        on a 96h lookback, so a busy scope holds several days of rows while a
        single page shows well under one — `.range()` is what lets the client
        reach the rest without inflating every first paint.

        Ordered by ``published_at`` DESC then ``id`` DESC: `published_at` alone
        is NOT unique (FMP stamps whole batches to the same minute), and
        PostgREST gives no stable tiebreak for equal keys, so page 2 could
        repeat or skip rows that page 1 already returned.
        """
        try:
            result = (
                self.supabase.table("ticker_news_cache")
                .select("*")
                .eq("ticker", ticker)
                .gte("expires_at", datetime.now(timezone.utc).isoformat())
                .order("published_at", desc=True)
                .order("id", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.warning(f"Cache lookup failed for {ticker}: {e}")
            return []

    def get_cached_bulk(
        self, scopes: List[str], per_scope_limit: int = 25
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Fresh cached rows for MANY scopes in ONE Supabase query.

        The insight sweeper evaluates every watchlisted scope on each pass; doing
        that with one `_get_cached` call per scope would be N round-trips per
        sweep. Rows come back newest-first and are truncated per scope in Python.

        Blocking — call via ``asyncio.to_thread`` from async code.
        """
        scopes = [s for s in dict.fromkeys(scopes) if s]
        if not scopes:
            return {}

        now_iso = datetime.now(timezone.utc).isoformat()
        columns = (
            "id, ticker, external_id, headline, summary, sentiment, "
            "ai_processed, published_at, article_url"
        )
        grouped: Dict[str, List[Dict[str, Any]]] = {}

        # PAGED, not a single `.limit(per_scope_limit * len(scopes))`.
        # A global LIMIT over a global ORDER BY has no per-group semantics: the
        # newest rows of a few busy scopes consume the entire budget and a quiet
        # scope comes back with ZERO rows even though its cache is populated —
        # which the gate then reads as `no_corpus` and never generates its card.
        # PostgREST also clamps a large `.limit()` server-side (~1000 rows), a
        # trap this repo has hit before (see sector_benchmark_lookup._fetch_rows).
        page_size = 1000
        offset = 0
        # Page until a SHORT read, not until a global row budget is spent. The
        # budget (`per_scope_limit * len(scopes)`) assumed rows are spread evenly
        # across scopes; they are not. A few busy scopes can consume it entirely
        # while a sparse scope's rows sit on a later page that is never fetched —
        # and a scope that comes back empty reads as `no_corpus`, so its Insights
        # card is never generated. `_MAX_BULK_PAGES` is only a runaway guard.
        while offset < page_size * self._MAX_BULK_PAGES:
            try:
                result = (
                    self.supabase.table("ticker_news_cache")
                    .select(columns)
                    .in_("ticker", scopes)
                    .gte("expires_at", now_iso)
                    # DESC defaults to NULLS FIRST in Postgres, and published_at
                    # is nullable — an undated row would otherwise head a
                    # newest-first feed and consume the page budget.
                    .order("published_at", desc=True, nullsfirst=False)
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
            except Exception as e:
                logger.warning(
                    "Bulk cache lookup failed for %d scopes at offset %d: %s: %s",
                    len(scopes), offset, type(e).__name__, e,
                )
                break

            rows = result.data or []
            for row in rows:
                key = row.get("ticker")
                if not key:
                    continue
                bucket = grouped.setdefault(key, [])
                if len(bucket) < per_scope_limit:
                    bucket.append(row)

            if len(rows) < page_size:
                break  # short read => last page, every scope has been seen
            # Every scope already full → nothing more to learn from later pages.
            if len(grouped) == len(scopes) and all(
                len(v) >= per_scope_limit for v in grouped.values()
            ):
                break
            offset += page_size

        return grouped

    async def refresh_scope_news(
        self, scope: str, limit: int = 50, lookback_hours: int = REFRESH_LOOKBACK_HOURS,
    ) -> int:
        """Force-fetch recent news for ``scope`` from FMP and write it through.

        Deliberately BYPASSES the 6-hour cache read. The insight sweeper needs to
        notice a story that broke ten minutes ago; if it went through
        :meth:`get_ticker_news` it would just re-read the same 6-hour-old rows
        and the fingerprint would never change — the "catch breaking news"
        property would silently not exist.

        The upsert is idempotent on ``(ticker, external_id)``, so re-fetching
        overlapping articles refreshes ``expires_at`` instead of duplicating.

        THE WINDOW MUST NOT BE NARROWER THAN THE COLD FETCH. This ran at 6 hours,
        which made `from_date` today — so the refresh only ever re-stamped
        TODAY's articles while every older cached row aged out at its 6h
        `expires_at`. Because `_get_cached` filters on `expires_at` and
        `get_ticker_news` short-circuits on any non-empty result, the full
        multi-day fetch never ran again: the Ticker Detail News tab silently
        collapsed from ~50 articles across several days to the handful published
        today. A refresh must therefore cover the same span the cold fetch does.

        Returns the number of rows written (0 on any failure — non-fatal, the
        next sweep retries).
        """
        from_date = (
            datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        ).strftime("%Y-%m-%d")
        try:
            if scope == MARKET_SCOPE:
                # Same blend as the cold path — NOT `get_stock_news(None)`,
                # which returns an all-Apple feed.
                raw = await self._fetch_market_raw(limit, from_date=from_date)
                fallback = None
            else:
                raw = await self.fmp.get_stock_news(
                    scope, limit=limit, from_date=from_date
                )
                fallback = scope
        except Exception as e:
            logger.warning(
                "News refresh fetch failed for %s: %s: %s",
                scope, type(e).__name__, e,
            )
            return 0

        if not raw:
            return 0
        # ingest_only: this is a REFRESH of a scope whose rows are very likely
        # already cached and already enriched. Writing the AI columns here would
        # reset every one of them (see _build_and_cache_rows).
        # Off-thread: the Supabase SDK is synchronous, and this upserts up to 30
        # rows for each of ~200 scopes per news pass — on the event loop that
        # stalls every other in-flight request on this instance.
        written = await asyncio.to_thread(
            self._build_and_cache_rows,
            scope, raw, limit, fallback, f"{scope} (refresh)", True,
        )
        return len(written)

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
        except (FMPRateLimitException, FMPAuthException):
            raise  # a second swallow here would burn another call over quota
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
        # The general market feed backs the Updates screen's default tab, so it is
        # warmed FIRST and unconditionally — even when nobody has a watchlist yet.
        try:
            market = await self.get_market_news(limit=50)
            logger.info(
                "Pre-warmed %s: %d articles", MARKET_SCOPE, len(market.get("articles", []))
            )
        except Exception as e:
            logger.warning(
                "Pre-warm failed for %s: %s: %s", MARKET_SCOPE, type(e).__name__, e
            )

        try:
            result = await asyncio.to_thread(
                lambda: self.supabase.rpc(
                    "get_top_watchlist_tickers", {"n": top_n}
                ).execute()
            )
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
        def _delete():
            return self.supabase.table("ticker_news_cache").delete().lt(
                "expires_at", datetime.now(timezone.utc).isoformat()
            ).execute()

        try:
            # A table-wide DELETE is the slowest statement this service issues;
            # on the loop it stalls every concurrent request for its duration.
            await asyncio.to_thread(_delete)
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
