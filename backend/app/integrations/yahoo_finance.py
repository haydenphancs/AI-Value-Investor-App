"""
Yahoo Finance helper — lightweight client for data not available via FMP.

Currently used for:
  - Short interest data (shortPercentOfFloat, shortRatio, sharesShort)

Two-tier cache-aside pattern:
  Tier 1: In-memory dict (1-hour TTL)
  Tier 2: Supabase short_interest_cache table (24-hour TTL)
  Miss:   Yahoo Finance API call → cache in both tiers

Short interest updates bi-monthly (FINRA), so aggressive caching is safe.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# ── In-memory cache (Tier 1) ─────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 3600  # 1 hour

# Yahoo auth tokens (shared across requests, refreshed as needed)
_crumb: Optional[str] = None
_cookies: Optional[httpx.Cookies] = None
_auth_ts: float = 0
_AUTH_TTL = 1800  # refresh auth every 30 min

_SUPABASE_TTL_HOURS = 24


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
    """Check Supabase short_interest_cache. Returns data if fresh (<=24h)."""
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
        if age > timedelta(hours=_SUPABASE_TTL_HOURS):
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
    """Get stale Supabase data (any age) as fallback when Yahoo is rate-limited."""
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


async def _ensure_auth(client: httpx.AsyncClient):
    """Get/refresh Yahoo Finance crumb + cookies."""
    global _crumb, _cookies, _auth_ts

    if _crumb and _cookies and (time.time() - _auth_ts < _AUTH_TTL):
        return

    try:
        r1 = await client.get("https://fc.yahoo.com", headers=_HEADERS)
        _cookies = r1.cookies

        r2 = await client.get(
            "https://query2.finance.yahoo.com/v1/test/getcrumb",
            headers=_HEADERS,
            cookies=_cookies,
        )
        if r2.status_code == 200 and r2.text.strip():
            _crumb = r2.text.strip()
            _auth_ts = time.time()
        else:
            logger.warning(f"Yahoo crumb fetch failed: {r2.status_code}")
    except Exception as e:
        logger.warning(f"Yahoo auth failed: {e}")


async def _fetch_from_yahoo(ticker: str) -> Optional[Dict[str, Any]]:
    """Call Yahoo Finance API. Returns parsed dict or None on failure."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            await _ensure_auth(client)

            if not _crumb or not _cookies:
                logger.warning("Yahoo Finance auth not available")
                return None

            url = (
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
                f"?modules=defaultKeyStatistics&formatted=false&crumb={_crumb}"
            )
            r = await client.get(url, headers=_HEADERS, cookies=_cookies)

            if r.status_code == 429:
                logger.warning(f"Yahoo rate-limited (429) for {ticker}")
                return None

            if r.status_code != 200:
                logger.warning(f"Yahoo quoteSummary failed for {ticker}: {r.status_code}")
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


# ── Public API ───────────────────────────────────────────────────

async def get_short_interest(ticker: str) -> Dict[str, Any]:
    """
    Fetch short interest data with two-tier caching.

    Returns dict with:
      - short_percent_of_float: float (e.g. 0.88 for 0.88%)
      - short_ratio: float (days to cover)
      - shares_short: int
      - shares_short_prior_month: int
    """
    ticker = ticker.upper()
    mem_key = f"yahoo_short:{ticker}"

    # Tier 1: In-memory cache (1-hour TTL)
    cached = _mem_cache_get(mem_key)
    if cached is not None:
        return cached

    # Tier 2: Supabase cache (24-hour TTL)
    sb_data = _supabase_cache_get(ticker)
    if sb_data is not None:
        _mem_cache_set(mem_key, sb_data)
        return sb_data

    # Miss: Call Yahoo Finance API
    result = await _fetch_from_yahoo(ticker)

    if result:
        # Cache in both tiers
        _mem_cache_set(mem_key, result)
        _supabase_cache_set(ticker, result)
        return result

    # Yahoo failed (rate-limited or error) — try stale Supabase data as fallback
    stale = _supabase_cache_get_stale(ticker)
    if stale:
        _mem_cache_set(mem_key, stale)
        return stale

    return {}
