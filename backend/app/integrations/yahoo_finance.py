"""
Yahoo Finance helper — lightweight client for data not available via FMP.

Currently used for:
  - Short interest data (shortPercentOfFloat, shortRatio, sharesShort)

Two-tier cache-aside pattern:
  Tier 1: In-memory dict (24-hour TTL)
  Tier 2: Supabase short_interest_cache table (2-week TTL)
  Miss:   Queued for background Yahoo Finance fetch → cached on success

Short interest updates bi-monthly (FINRA), so aggressive caching is safe.
Yahoo rate-limits aggressively, so we never block the request on Yahoo calls.
Instead, cache misses are queued and fetched in a background worker.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Set, Tuple

import httpx

logger = logging.getLogger(__name__)

# ── In-memory cache (Tier 1) ─────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 86400  # 24 hours in-memory

# Yahoo auth tokens (shared across requests, refreshed as needed)
_crumb: Optional[str] = None
_cookies: Optional[httpx.Cookies] = None
_auth_ts: float = 0
_AUTH_TTL = 1800  # refresh auth every 30 min

_SUPABASE_TTL_DAYS = 14  # 2 weeks — FINRA updates bi-monthly

# Persistent HTTP client
_http_client: Optional[httpx.AsyncClient] = None

# Global cooldown after Yahoo returns 429
_rate_limited_until: float = 0
_RATE_LIMIT_COOLDOWN = 300  # 5 minutes

# Background fetch queue
_fetch_queue: asyncio.Queue = None  # initialized lazily
_pending_tickers: Set[str] = set()  # avoid duplicate queue entries
_worker_started: bool = False
_FETCH_DELAY = 3.0  # seconds between Yahoo calls to avoid rate limiting


def _mem_cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


def _mem_cache_set(key: str, value: Any):
    _cache[key] = (time.time(), value)


# ── Supabase cache (Tier 2) ──────────────────────────────────────

def _supabase_cache_get(ticker: str) -> Optional[Dict[str, Any]]:
    """Check Supabase short_interest_cache. Returns data if fresh."""
    try:
        from app.database import get_supabase
        sb = get_supabase()
        row = (
            sb.table("short_interest_cache")
            .select("response_json, cached_at")
            .eq("ticker", ticker)
            .limit(1)
            .execute()
        )
        if not row.data:
            return None

        entry = row.data[0]
        cached_at_str = entry.get("cached_at")
        if not cached_at_str:
            return None

        cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - cached_at
        if age > timedelta(days=_SUPABASE_TTL_DAYS):
            logger.info(f"Short interest Supabase cache STALE (age={age}) for {ticker}")
            return None

        data = entry.get("response_json")
        if data and isinstance(data, dict):
            logger.info(f"Short interest Supabase cache HIT for {ticker} (age={age})")
            return data
        return None
    except Exception as e:
        logger.warning(f"Short interest Supabase cache check failed for {ticker}: {e}")
        return None


def _supabase_cache_get_stale(ticker: str) -> Optional[Dict[str, Any]]:
    """Get stale Supabase data (any age) as fallback."""
    try:
        from app.database import get_supabase
        sb = get_supabase()
        row = (
            sb.table("short_interest_cache")
            .select("response_json")
            .eq("ticker", ticker)
            .limit(1)
            .execute()
        )
        if row.data:
            data = row.data[0].get("response_json")
            if data and isinstance(data, dict):
                logger.info(f"Short interest using STALE Supabase data for {ticker}")
                return data
        return None
    except Exception:
        return None


def _supabase_cache_set(ticker: str, data: Dict[str, Any]):
    """Upsert short interest data into Supabase cache."""
    try:
        from app.database import get_supabase
        sb = get_supabase()
        sb.table("short_interest_cache").upsert(
            {
                "ticker": ticker,
                "response_json": data,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="ticker",
        ).execute()
    except Exception as e:
        logger.warning(f"Short interest Supabase upsert failed for {ticker}: {e}")


# ── Yahoo Finance API ────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


async def _get_client() -> httpx.AsyncClient:
    """Get or create persistent AsyncClient for Yahoo Finance."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=10.0,
        )
    return _http_client


async def _ensure_auth(client: httpx.AsyncClient):
    """Get/refresh Yahoo Finance crumb + cookies."""
    global _crumb, _cookies, _auth_ts, _rate_limited_until

    if _crumb and _cookies and (time.time() - _auth_ts < _AUTH_TTL):
        return True

    try:
        r1 = await client.get("https://fc.yahoo.com", headers=_HEADERS)
        _cookies = r1.cookies

        r2 = await client.get(
            "https://query2.finance.yahoo.com/v1/test/getcrumb",
            headers=_HEADERS,
            cookies=_cookies,
        )
        if r2.status_code == 429:
            logger.warning("Yahoo crumb endpoint rate-limited (429)")
            _rate_limited_until = time.time() + _RATE_LIMIT_COOLDOWN
            return False
        if r2.status_code == 200 and r2.text.strip():
            _crumb = r2.text.strip()
            _auth_ts = time.time()
            return True
        else:
            logger.warning(f"Yahoo crumb fetch failed: {r2.status_code}")
            return False
    except Exception as e:
        logger.warning(f"Yahoo auth failed: {e}")
        return False


async def _fetch_from_yahoo(ticker: str) -> Optional[Dict[str, Any]]:
    """Call Yahoo Finance API. Returns parsed dict or None on failure."""
    global _rate_limited_until, _auth_ts

    # Skip if we're in a cooldown period after a 429
    if time.time() < _rate_limited_until:
        logger.info(f"Yahoo cooldown active, skipping API call for {ticker}")
        return None

    try:
        client = await _get_client()
        auth_ok = await _ensure_auth(client)
        if not auth_ok:
            return None

        url = (
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
            f"?modules=defaultKeyStatistics&formatted=false&crumb={_crumb}"
        )
        r = await client.get(url, headers=_HEADERS, cookies=_cookies)

        if r.status_code == 429:
            logger.warning(f"Yahoo rate-limited (429) for {ticker} — cooldown {_RATE_LIMIT_COOLDOWN}s")
            _rate_limited_until = time.time() + _RATE_LIMIT_COOLDOWN
            _auth_ts = 0  # force auth refresh on next attempt
            return None

        if r.status_code != 200:
            logger.warning(f"Yahoo quoteSummary failed for {ticker}: {r.status_code}")
            # Auth might be stale — force refresh next time
            if r.status_code in (401, 403):
                _auth_ts = 0
            return None

        data = r.json()
        ks = (
            data.get("quoteSummary", {})
            .get("result", [{}])[0]
            .get("defaultKeyStatistics", {})
        )

        result: Dict[str, Any] = {}

        spf = ks.get("shortPercentOfFloat")
        if spf is not None:
            result["short_percent_of_float"] = round(float(spf) * 100, 2)

        sr = ks.get("shortRatio")
        if sr is not None:
            result["short_ratio"] = round(float(sr), 2)

        ss = ks.get("sharesShort")
        if ss is not None:
            result["shares_short"] = int(ss)

        sspm = ks.get("sharesShortPriorMonth")
        if sspm is not None:
            result["shares_short_prior_month"] = int(sspm)

        return result if result else None

    except Exception as e:
        logger.warning(f"Yahoo short interest API failed for {ticker}: {e}")
        return None


# ── Background fetch worker ──────────────────────────────────────

_MAX_RETRIES = 3


async def _background_worker():
    """Process queued tickers one at a time with delays to avoid rate limiting."""
    global _fetch_queue
    logger.info("Short interest background worker started")

    while True:
        ticker = await _fetch_queue.get()
        retries = 0
        success = False

        try:
            while retries < _MAX_RETRIES and not success:
                # Wait for cooldown if active
                wait_time = _rate_limited_until - time.time()
                if wait_time > 0:
                    logger.info(f"Short interest worker waiting {wait_time:.0f}s for cooldown before {ticker}")
                    await asyncio.sleep(wait_time + 1)

                result = await _fetch_from_yahoo(ticker)
                if result:
                    mem_key = f"yahoo_short:{ticker}"
                    _mem_cache_set(mem_key, result)
                    _supabase_cache_set(ticker, result)
                    logger.info(f"Short interest background fetch SUCCESS for {ticker}")
                    success = True
                else:
                    retries += 1
                    if retries < _MAX_RETRIES:
                        logger.info(f"Short interest retry {retries}/{_MAX_RETRIES} for {ticker}")

            if not success:
                logger.warning(f"Short interest background fetch FAILED after {_MAX_RETRIES} retries for {ticker}")

            # Delay between calls to avoid rate limiting
            await asyncio.sleep(_FETCH_DELAY)

        except Exception as e:
            logger.warning(f"Short interest background fetch error for {ticker}: {e}")
        finally:
            _pending_tickers.discard(ticker)
            _fetch_queue.task_done()


def _enqueue_fetch(ticker: str):
    """Queue a ticker for background Yahoo fetch (non-blocking, deduped)."""
    global _fetch_queue, _worker_started

    if ticker in _pending_tickers:
        return

    # Lazy init queue and worker
    if _fetch_queue is None:
        _fetch_queue = asyncio.Queue()

    if not _worker_started:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_background_worker())
            _worker_started = True
        except RuntimeError:
            logger.warning("No running event loop for short interest worker")
            return

    _pending_tickers.add(ticker)
    _fetch_queue.put_nowait(ticker)
    logger.info(f"Short interest queued background fetch for {ticker}")


# ── Public API ───────────────────────────────────────────────────

async def get_short_interest(ticker: str) -> Dict[str, Any]:
    """
    Fetch short interest data with two-tier caching.
    Never blocks on Yahoo Finance — cache misses are queued for background fetch.

    Returns dict with:
      - short_percent_of_float: float (e.g. 0.88 for 0.88%)
      - short_ratio: float (days to cover)
      - shares_short: int
      - shares_short_prior_month: int
    """
    ticker = ticker.upper()
    mem_key = f"yahoo_short:{ticker}"

    # Tier 1: In-memory cache
    cached = _mem_cache_get(mem_key)
    if cached is not None:
        return cached

    # Tier 2: Supabase cache (14-day TTL)
    sb_data = _supabase_cache_get(ticker)
    if sb_data is not None:
        _mem_cache_set(mem_key, sb_data)
        return sb_data

    # Tier 3: Stale Supabase data (any age) — better than N/A
    stale = _supabase_cache_get_stale(ticker)
    if stale:
        _mem_cache_set(mem_key, stale)
        # Still queue a refresh in background
        _enqueue_fetch(ticker)
        return stale

    # No cached data at all — queue background fetch, return empty for now
    _enqueue_fetch(ticker)
    return {}
