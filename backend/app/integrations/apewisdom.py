"""
ApeWisdom API client — free Reddit stock/crypto mention tracking.

Fetches mention counts from r/wallstreetbets, r/stocks, r/investing, etc.
Rate-limit-safe: pages fetched with 2s delays, stocks and crypto staggered.

Two-tier access:
  1. In-memory cache (30-min TTL) for fast per-ticker lookups
  2. Paginated API fetch when cache expires
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

_BASE_URL = "https://apewisdom.io/api/v1.0/filter"
_PAGE_DELAY = 2.0  # seconds between page fetches
_FILTER_DELAY = 30.0  # seconds between stock and crypto fetches
_CACHE_TTL = 1800  # 30 minutes

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# ── In-memory cache ──────────────────────────────────────────────
# Keyed by ticker (uppercase), value is dict with mentions data.
# Both stocks and crypto are merged into one cache.

_cache: Dict[str, Dict[str, Any]] = {}
_cache_ts: float = 0
_fetch_lock: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    global _fetch_lock
    if _fetch_lock is None:
        _fetch_lock = asyncio.Lock()
    return _fetch_lock


def _is_cache_fresh() -> bool:
    return bool(_cache) and (time.time() - _cache_ts < _CACHE_TTL)


# ── API fetching ─────────────────────────────────────────────────

async def _fetch_filter(
    client: httpx.AsyncClient,
    filter_name: str,
) -> Dict[str, Dict[str, Any]]:
    """
    Fetch all pages for a filter (e.g., 'all-stocks').

    Returns dict keyed by ticker with mentions data.
    Fetches one page at a time with delays to avoid rate limiting.
    """
    result: Dict[str, Dict[str, Any]] = {}

    # First page to get total page count
    try:
        r = await client.get(
            f"{_BASE_URL}/{filter_name}/page/1",
            headers=_HEADERS,
            timeout=15.0,
        )
        if r.status_code != 200:
            logger.warning(
                f"ApeWisdom {filter_name} page 1 failed: {r.status_code}"
            )
            return result

        data = r.json()
        total_pages = data.get("pages", 1)
        _parse_page(data, result)

        logger.info(
            f"ApeWisdom {filter_name}: page 1/{total_pages}, "
            f"{len(data.get('results', []))} tickers"
        )

    except Exception as e:
        logger.warning(f"ApeWisdom {filter_name} page 1 error: {e}")
        return result

    # Remaining pages with delay
    for page in range(2, total_pages + 1):
        await asyncio.sleep(_PAGE_DELAY)
        try:
            r = await client.get(
                f"{_BASE_URL}/{filter_name}/page/{page}",
                headers=_HEADERS,
                timeout=15.0,
            )
            if r.status_code != 200:
                logger.warning(
                    f"ApeWisdom {filter_name} page {page} failed: "
                    f"{r.status_code}"
                )
                continue

            page_data = r.json()
            _parse_page(page_data, result)

        except Exception as e:
            logger.warning(
                f"ApeWisdom {filter_name} page {page} error: {e}"
            )
            continue

    logger.info(
        f"ApeWisdom {filter_name} complete: {len(result)} tickers"
    )
    return result


def _parse_page(
    data: Dict[str, Any],
    into: Dict[str, Dict[str, Any]],
) -> None:
    """Parse a single page response into the result dict."""
    for item in data.get("results", []):
        ticker = (item.get("ticker") or "").upper().strip()
        if not ticker:
            continue

        into[ticker] = {
            "mentions": int(item.get("mentions") or 0),
            "mentions_24h_ago": int(item.get("mentions_24h_ago") or 0),
            "upvotes": int(item.get("upvotes") or 0),
            "rank": int(item.get("rank") or 0),
        }


# ── Public API ───────────────────────────────────────────────────

# Strong reference to the in-flight background refresh. `asyncio.create_task` keeps only a
# WEAK one, so without this the task can be collected mid-flight.
_refresh_task: Optional["asyncio.Task"] = None


def _kick_background_refresh() -> None:
    """Start a refresh if the cache is stale and one is not already running.

    WHY THE READERS NO LONGER AWAIT THIS. `refresh_cache` has an unconditional
    `await asyncio.sleep(_FILTER_DELAY)` (30s) between the stock and crypto filters, so it
    cannot complete in less than half a minute. Every caller wraps its read in a SHORTER
    `asyncio.wait_for` — `news_cache_service` uses 2.0s — so the await was cancelled every
    single time, `_cache_ts` was never advanced, and the cache could never be rebuilt after
    the one-shot warm at boot. Thirty minutes after startup `_is_cache_fresh()` returned
    False permanently, and from then on every sentiment request paid a full timeout to
    abandon a live HTTP fetch and return nothing.

    Serving slightly stale social-mention counts is obviously better than serving none, so
    readers now take what is cached and let the refresh land when it lands.
    """
    global _refresh_task
    if _is_cache_fresh():
        return
    if _refresh_task is not None and not _refresh_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (sync context) — the lifespan warm task will handle it
    _refresh_task = loop.create_task(_refresh_quietly())


async def _refresh_quietly() -> None:
    """Background refresh whose failure must never surface to a request."""
    try:
        await refresh_cache()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(
            "ApeWisdom background refresh failed (%s: %s) — serving the previous cache",
            type(e).__name__, e,
        )


async def refresh_cache() -> Dict[str, Dict[str, Any]]:
    """
    Fetch all stock + crypto mentions and populate the in-memory cache.

    Rate-limit-safe: fetches pages with 2s delays, stocks then crypto
    with a 30s gap between filters.
    """
    global _cache, _cache_ts

    lock = _get_lock()
    async with lock:
        # Double-check after acquiring lock
        if _is_cache_fresh():
            return _cache

        async with httpx.AsyncClient(follow_redirects=True) as client:
            # Fetch stocks first
            stocks = await _fetch_filter(client, "all-stocks")

            # Wait before fetching crypto
            await asyncio.sleep(_FILTER_DELAY)

            # Fetch crypto
            crypto = await _fetch_filter(client, "all-crypto")

        # Merge into cache (stocks take priority on collision)
        merged = {**crypto, **stocks}
        _cache = merged
        _cache_ts = time.time()

        logger.info(
            f"ApeWisdom cache refreshed: "
            f"{len(stocks)} stocks + {len(crypto)} crypto = "
            f"{len(merged)} total"
        )
        return _cache


async def get_ticker_mentions(
    ticker: str,
) -> Optional[Dict[str, Any]]:
    """
    Get mention data for a single ticker.

    Returns dict with keys: mentions, mentions_24h_ago, upvotes, rank.
    Returns None if ticker not found on Reddit.

    NON-BLOCKING: serves the cache and refreshes in the BACKGROUND when stale.
    """
    ticker = ticker.upper().strip()
    _kick_background_refresh()
    return _cache.get(ticker)


async def get_all_mentions() -> Dict[str, Dict[str, Any]]:
    """
    Get the full mention dataset (all tickers).

    NON-BLOCKING: returns whatever is cached and refreshes in the BACKGROUND if stale.
    See `_kick_background_refresh` for why awaiting the refresh here was unworkable.
    """
    _kick_background_refresh()
    return dict(_cache)
