"""
Short Interest — multi-source fetcher with caching.

Sources (in priority order):
  1. FINRA Consolidated Short Interest API — OAuth, covers ALL exchanges
  2. Nasdaq public API — free, no auth, covers NASDAQ-listed stocks only
  3. Yahoo Finance via proxy — covers all stocks (NYSE, AMEX, etc.)

Two-tier cache-aside pattern:
  Tier 1: In-memory dict (16-day TTL — covers one FINRA reporting period)
  Tier 2: Supabase short_interest_cache table (18-day TTL)
  Miss:   Try FINRA first, then Nasdaq, then Yahoo proxy fallback

FINRA publishes short interest twice monthly (~15th and end of month).
Data updates are slow, so aggressive caching is safe and appropriate.
"""

import base64
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

_NASDAQ_SHORT_INTEREST_URL = (
    "https://api.nasdaq.com/api/quote/{ticker}/short-interest?assetClass=stocks"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# ── In-memory cache (Tier 1) ────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 16 * 86400  # 16 days — one FINRA reporting period + buffer

_SUPABASE_TTL_DAYS = 18  # slightly longer than in-memory for stale fallback

# ── Nasdaq safeguards ───────────────────────────────────────────

_nasdaq_kill_switch: bool = False
_nasdaq_consecutive_failures: int = 0
_MAX_CONSECUTIVE_FAILURES = 10
_nasdaq_rate_limited_until: float = 0

# ── Yahoo proxy safeguards ──────────────────────────────────────

_yahoo_kill_switch: bool = False
_yahoo_rate_limited_until: float = 0
_RATE_LIMIT_COOLDOWN = 300  # 5 minutes

# Yahoo auth tokens (crumb + cookies)
_yahoo_crumb: Optional[str] = None
_yahoo_cookies: Optional[httpx.Cookies] = None
_yahoo_auth_ts: float = 0
_YAHOO_AUTH_TTL = 1800  # refresh auth every 30 min

# ── FINRA API safeguards ───────────────────────────────────────

_FINRA_TOKEN_URL = (
    "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token"
    "?grant_type=client_credentials"
)
_FINRA_SHORT_INTEREST_URL = (
    "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
)

_finra_kill_switch: bool = False
_finra_consecutive_failures: int = 0
_finra_rate_limited_until: float = 0

# OAuth token cache
_finra_access_token: Optional[str] = None
_finra_token_expiry: float = 0
_FINRA_TOKEN_TTL = 1500  # 25 min (tokens typically valid ~30 min)

# ── HTTP clients ────────────────────────────────────────────────

_http_client: Optional[httpx.AsyncClient] = None
_yahoo_http_client: Optional[httpx.AsyncClient] = None
_finra_http_client: Optional[httpx.AsyncClient] = None


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


# ── Supabase cache (Tier 2) ─────────────────────────────────────


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


# ── Helpers ─────────────────────────────────────────────────────


def _parse_int(val: str) -> Optional[int]:
    """Parse a comma-formatted integer string like '124,192,030'."""
    if not val:
        return None
    try:
        return int(val.replace(",", ""))
    except (ValueError, TypeError):
        return None


def _parse_float(val) -> Optional[float]:
    """Parse a float value that may be string or number."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ── FINRA API fetch (primary source) ──────────────────────────


async def _get_finra_client() -> httpx.AsyncClient:
    global _finra_http_client
    if _finra_http_client is None or _finra_http_client.is_closed:
        _finra_http_client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)
    return _finra_http_client


async def _fetch_finra_token() -> Optional[str]:
    """Get or refresh FINRA OAuth access token using client credentials."""
    global _finra_access_token, _finra_token_expiry

    if _finra_access_token and time.time() < _finra_token_expiry:
        return _finra_access_token

    client_id = os.getenv("FINRA_CLIENT_ID")
    client_secret = os.getenv("FINRA_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    try:
        client = await _get_finra_client()
        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        resp = await client.post(
            _FINRA_TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        if resp.status_code != 200:
            logger.warning(f"FINRA token request failed: {resp.status_code}")
            return None

        data = resp.json()
        _finra_access_token = data.get("access_token")
        _finra_token_expiry = time.time() + _FINRA_TOKEN_TTL
        logger.info("FINRA OAuth token refreshed")
        return _finra_access_token

    except Exception as e:
        logger.warning(f"FINRA token error: {e}")
        return None


async def _fetch_from_finra(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Call FINRA Consolidated Short Interest API.
    Covers ALL exchange-listed and OTC stocks (NYSE, NASDAQ, AMEX, etc.).
    """
    global _finra_kill_switch, _finra_consecutive_failures, _finra_rate_limited_until

    if _finra_kill_switch:
        return None

    if time.time() < _finra_rate_limited_until:
        return None

    token = await _fetch_finra_token()
    if not token:
        return None

    try:
        client = await _get_finra_client()
        # Fetch ~6 months of data so we can compute 3-month change.
        # Results come oldest-first; we take the last row for latest.
        now = datetime.now(timezone.utc)
        six_months_ago = now - timedelta(days=180)
        start_date = six_months_ago.strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")

        resp = await client.post(
            _FINRA_SHORT_INTEREST_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "compareFilters": [
                    {
                        "fieldName": "symbolCode",
                        "fieldValue": ticker.upper(),
                        "compareType": "EQUAL",
                    }
                ],
                "dateRangeFilters": [
                    {
                        "fieldName": "settlementDate",
                        "startDate": start_date,
                        "endDate": end_date,
                    }
                ],
                "limit": 50,
            },
        )

        if resp.status_code == 429:
            _finra_rate_limited_until = time.time() + _RATE_LIMIT_COOLDOWN
            logger.warning(f"FINRA rate-limited (429) for {ticker}")
            return None

        if resp.status_code in (401, 403):
            _finra_consecutive_failures += 1
            # Force token refresh on next attempt
            global _finra_access_token, _finra_token_expiry
            _finra_access_token = None
            _finra_token_expiry = 0
            if _finra_consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                _finra_kill_switch = True
                logger.critical(f"FINRA auth failures x{_MAX_CONSECUTIVE_FAILURES} — FINRA DISABLED")
            else:
                logger.warning(f"FINRA auth failed for {ticker}: {resp.status_code}")
            return None

        if resp.status_code != 200:
            logger.warning(f"FINRA API failed for {ticker}: {resp.status_code}")
            return None

        _finra_consecutive_failures = 0
        rows = resp.json()

        if not rows or not isinstance(rows, list):
            logger.info(f"FINRA: no data for {ticker}")
            return None

        # Results are oldest-first; take the last row for the most recent data
        latest = rows[-1]
        shares_short = latest.get("currentShortPositionQuantity")
        if shares_short is None:
            return None

        shares_short = int(shares_short)
        result: Dict[str, Any] = {"shares_short": shares_short}

        prev = latest.get("previousShortPositionQuantity")
        if prev is not None:
            result["shares_short_prior_month"] = int(prev)

        dtc = latest.get("daysToCoverQuantity")
        if dtc is not None:
            result["short_ratio"] = round(float(dtc), 2)

        sd = latest.get("settlementDate")
        if sd:
            result["settlement_date"] = sd

        # 3-month change: find the row closest to ~90 days before latest
        if len(rows) >= 2 and sd:
            try:
                latest_date = datetime.strptime(sd, "%Y-%m-%d")
                target_date = latest_date - timedelta(days=90)
                best_row = None
                best_diff = float("inf")
                for row in rows[:-1]:  # exclude latest
                    row_date_str = row.get("settlementDate")
                    if not row_date_str:
                        continue
                    row_date = datetime.strptime(row_date_str, "%Y-%m-%d")
                    diff = abs((row_date - target_date).days)
                    if diff < best_diff:
                        best_diff = diff
                        best_row = row
                if best_row and best_diff <= 45:  # within ~1.5 months tolerance
                    old_short = best_row.get("currentShortPositionQuantity")
                    if old_short and int(old_short) > 0:
                        change_pct = round(
                            (shares_short - int(old_short)) / int(old_short) * 100, 2
                        )
                        result["short_change_3m"] = change_pct
            except (ValueError, TypeError):
                pass

        # Keep the full settlement-date series for the report's short-interest
        # trend chart — we ALREADY fetched ~6 months of rows above; the
        # integration previously discarded all but the latest. Last 12 points,
        # oldest→newest (FINRA publishes twice monthly → ~6 months).
        history: List[Dict[str, Any]] = []
        for row in rows[-12:]:
            ss_h = row.get("currentShortPositionQuantity")
            if ss_h is None:
                continue
            point: Dict[str, Any] = {
                "settlement_date": row.get("settlementDate"),
                "shares_short": int(ss_h),
            }
            dtc_h = row.get("daysToCoverQuantity")
            if dtc_h is not None:
                try:
                    point["days_to_cover"] = round(float(dtc_h), 2)
                except (TypeError, ValueError):
                    pass
            history.append(point)
        if len(history) >= 2:
            result["history"] = history

        logger.info(f"FINRA short interest for {ticker}: shares_short={shares_short}")
        return result

    except Exception as e:
        logger.warning(f"FINRA API error for {ticker}: {e}")
        return None


# ── Nasdaq API fetch (fallback 1) ──────────────────────────────


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)
    return _http_client


async def _fetch_from_nasdaq(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Call Nasdaq's public API for short interest data.
    No authentication required. Only covers NASDAQ-listed stocks.
    """
    global _nasdaq_kill_switch, _nasdaq_consecutive_failures, _nasdaq_rate_limited_until

    if _nasdaq_kill_switch:
        return None

    if time.time() < _nasdaq_rate_limited_until:
        return None

    try:
        client = await _get_client()
        url = _NASDAQ_SHORT_INTEREST_URL.format(ticker=ticker.upper())
        resp = await client.get(url, headers=_HEADERS)

        if resp.status_code == 429:
            _nasdaq_rate_limited_until = time.time() + _RATE_LIMIT_COOLDOWN
            logger.warning(f"Nasdaq rate-limited (429) for {ticker}")
            return None

        if resp.status_code == 402:
            _nasdaq_kill_switch = True
            logger.critical("Nasdaq API returned 402 — short interest via Nasdaq DISABLED")
            return None

        if resp.status_code == 403:
            _nasdaq_consecutive_failures += 1
            if _nasdaq_consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                _nasdaq_kill_switch = True
                logger.critical(f"Nasdaq 403 x{_MAX_CONSECUTIVE_FAILURES} — Nasdaq DISABLED")
            return None

        if resp.status_code != 200:
            logger.warning(f"Nasdaq API failed for {ticker}: {resp.status_code}")
            return None

        _nasdaq_consecutive_failures = 0

        body = resp.json()
        data = body.get("data")
        if data is None:
            # Nasdaq returns data=null for non-NASDAQ-listed stocks (NYSE, AMEX)
            msg = body.get("message", "")
            if "not available" in msg.lower() or "only supported" in msg.lower():
                logger.info(f"Nasdaq: not available for {ticker} (likely NYSE) — will try Yahoo")
            return None

        table = data.get("shortInterestTable", {})
        rows = table.get("rows", [])
        if not rows:
            return None

        latest = rows[0]
        shares_short = _parse_int(latest.get("interest"))
        settlement_date = latest.get("settlementDate")
        days_to_cover = _parse_float(latest.get("daysToCover"))

        if shares_short is None:
            return None

        result: Dict[str, Any] = {"shares_short": shares_short}

        if len(rows) >= 2:
            prior_short = _parse_int(rows[1].get("interest"))
            if prior_short is not None:
                result["shares_short_prior_month"] = prior_short

        if days_to_cover is not None:
            result["short_ratio"] = round(days_to_cover, 2)

        if settlement_date:
            try:
                dt = datetime.strptime(settlement_date, "%m/%d/%Y")
                result["settlement_date"] = dt.strftime("%Y-%m-%d")
            except ValueError:
                result["settlement_date"] = settlement_date

        logger.info(f"Nasdaq short interest for {ticker}: shares_short={shares_short}")
        return result

    except Exception as e:
        logger.warning(f"Nasdaq API error for {ticker}: {e}")
        return None


# ── Yahoo Finance via proxy (fallback for NYSE stocks) ──────────


async def _get_yahoo_client() -> Optional[httpx.AsyncClient]:
    """Get or create proxy-enabled Yahoo Finance client."""
    global _yahoo_http_client

    proxy_url = os.getenv("YAHOO_PROXY_URL")
    if not proxy_url:
        return None

    if _yahoo_http_client is None or _yahoo_http_client.is_closed:
        _yahoo_http_client = httpx.AsyncClient(
            proxy=proxy_url,
            follow_redirects=True,
            timeout=15.0,
        )
    return _yahoo_http_client


async def _ensure_yahoo_auth(client: httpx.AsyncClient) -> bool:
    """Get/refresh Yahoo Finance crumb + cookies via proxy."""
    global _yahoo_crumb, _yahoo_cookies, _yahoo_auth_ts, _yahoo_rate_limited_until

    if _yahoo_crumb and _yahoo_cookies and (time.time() - _yahoo_auth_ts < _YAHOO_AUTH_TTL):
        return True

    try:
        r1 = await client.get("https://fc.yahoo.com", headers=_HEADERS)
        _yahoo_cookies = r1.cookies

        r2 = await client.get(
            "https://query2.finance.yahoo.com/v1/test/getcrumb",
            headers=_HEADERS,
            cookies=_yahoo_cookies,
        )
        if r2.status_code == 429:
            logger.warning("Yahoo crumb endpoint rate-limited (429)")
            _yahoo_rate_limited_until = time.time() + _RATE_LIMIT_COOLDOWN
            return False
        if r2.status_code == 200 and r2.text.strip():
            _yahoo_crumb = r2.text.strip()
            _yahoo_auth_ts = time.time()
            logger.info("Yahoo Finance auth refreshed via proxy")
            return True
        else:
            logger.warning(f"Yahoo crumb fetch failed: {r2.status_code}")
            return False
    except Exception as e:
        logger.warning(f"Yahoo auth failed via proxy: {e}")
        return False


async def _fetch_from_yahoo(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Call Yahoo Finance API via proxy for short interest data.
    Used as fallback for stocks not covered by Nasdaq (NYSE, AMEX).
    """
    global _yahoo_kill_switch, _yahoo_rate_limited_until, _yahoo_auth_ts

    if _yahoo_kill_switch:
        return None

    if time.time() < _yahoo_rate_limited_until:
        logger.info(f"Yahoo cooldown active, skipping {ticker}")
        return None

    client = await _get_yahoo_client()
    if client is None:
        # No proxy configured — Yahoo fallback unavailable
        return None

    try:
        auth_ok = await _ensure_yahoo_auth(client)
        if not auth_ok:
            return None

        url = (
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
            f"?modules=defaultKeyStatistics&formatted=false&crumb={_yahoo_crumb}"
        )
        r = await client.get(url, headers=_HEADERS, cookies=_yahoo_cookies)

        if r.status_code == 429:
            logger.warning(f"Yahoo rate-limited (429) for {ticker}")
            _yahoo_rate_limited_until = time.time() + _RATE_LIMIT_COOLDOWN
            _yahoo_auth_ts = 0
            return None

        if r.status_code in (401, 403):
            logger.warning(f"Yahoo auth failed for {ticker}: {r.status_code}")
            _yahoo_auth_ts = 0  # force auth refresh
            return None

        if r.status_code != 200:
            logger.warning(f"Yahoo API failed for {ticker}: {r.status_code}")
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

        if result:
            logger.info(f"Yahoo (proxy) short interest for {ticker}: shares_short={result.get('shares_short')}")
            return result
        return None

    except Exception as e:
        logger.warning(f"Yahoo API error for {ticker}: {e}")
        return None


# ── Public API ──────────────────────────────────────────────────


async def get_short_interest(ticker: str) -> Dict[str, Any]:
    """
    Fetch short interest data with two-tier caching.
    Tries FINRA API first, then Nasdaq, then Yahoo via proxy.

    Returns dict with:
      - shares_short: int
      - shares_short_prior_month: int (if available)
      - short_ratio: float (days to cover)
      - short_percent_of_float: float (from Yahoo, if available)
      - settlement_date: str (if available)
    """
    ticker = ticker.upper()
    mem_key = f"finra_short:{ticker}"

    # Tier 1: In-memory cache
    cached = _mem_cache_get(mem_key)
    if cached is not None:
        return cached

    # Tier 2: Supabase cache
    sb_data = _supabase_cache_get(ticker)
    if sb_data is not None:
        _mem_cache_set(mem_key, sb_data)
        return sb_data

    # Tier 3: Stale Supabase data (any age) — better than N/A
    stale = _supabase_cache_get_stale(ticker)

    # Try FINRA API first (covers ALL exchanges)
    result = await _fetch_from_finra(ticker)

    # Fallback 1: Nasdaq (covers NASDAQ-listed stocks)
    if not result:
        result = await _fetch_from_nasdaq(ticker)

    # Fallback 2: Yahoo Finance via proxy (covers NYSE, AMEX, etc.)
    if not result:
        result = await _fetch_from_yahoo(ticker)

    if result:
        _mem_cache_set(mem_key, result)
        _supabase_cache_set(ticker, result)
        return result

    # Fall back to stale cache data
    if stale:
        _mem_cache_set(mem_key, stale)
        return stale

    return {}
