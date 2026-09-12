"""
Short Interest — multi-source fetcher with caching.

Sources (in priority order):
  1. FINRA Consolidated Short Interest API — OAuth, covers ALL exchanges
  2. Nasdaq public API — free, no auth, covers NASDAQ-listed stocks only

Two-tier cache-aside pattern:
  Tier 1: In-memory dict (3-day TTL)
  Tier 2: Supabase short_interest_cache table (3-day TTL)
  Miss:   Try FINRA first, then Nasdaq

FINRA publishes short interest twice monthly (~15th and end of month) with an
~8-business-day reporting lag. The 3-day TTL sits UNDER that ~14-day publish
cadence, so a freshly published print surfaces within ~3 days instead of being
masked for a full cycle — while still collapsing repeated views into a single
call (FINRA limit ~1,200/min, so the extra calls are immaterial).
"""

import asyncio
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

# Honest identifying UA. This previously spoofed Chrome on macOS, which was part of the
# removed Yahoo path (proxy rotation + harvested crumb/cookie auth). Nasdaq's public JSON
# endpoint does not require a browser UA, and misrepresenting the client is exactly the
# behaviour Caydex's own Terms forbid users from doing to Caydex.
_HEADERS = {
    "User-Agent": "Caydex/1.0 (+https://caydexinvest.com)",
    "Accept": "application/json",
}

# Cooldown after a 429 from any upstream (FINRA, Nasdaq).
_RATE_LIMIT_COOLDOWN = 300  # 5 minutes

# ── In-memory cache (Tier 1) ────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 3 * 86400  # 3 days — under FINRA's ~14-day publish cadence so new prints surface promptly

_SUPABASE_TTL_DAYS = 3  # matches in-memory; under the ~14-day publish cadence (was 18)

# Entries written before the 12-month `history` series feature lack the
# `history` key. This date floor invalidates those rows ONCE (re-fetch a fresh
# FINRA series), without permanently re-fetching legit snapshot-only
# Nasdaq tickers — mirrors the ticker_report_cache schema-floor pattern.
_SI_SCHEMA_FLOOR = datetime(2026, 6, 7, 0, 0, 0, tzinfo=timezone.utc)

#: Sentinel: the primary source ANSWERED and has no rows for this symbol, as opposed to
#: `None`, which means the attempt failed. They were the same value, so an authoritative
#: "this index has no short interest" was retried against a fallback that covers strictly
#: less — and logged as a failure every cycle.
_NO_DATA: Dict[str, Any] = {}

# ── Nasdaq safeguards ───────────────────────────────────────────

_nasdaq_kill_switch: bool = False
#: TWO counters, because the two failure families have OPPOSITE remedies and sharing one
#: routes a run into the wrong arm.
#:
#: With a single counter, nine transient ReadTimeouts followed by ONE 403 reached the
#: threshold inside the 403 branch and latched the kill switch for the life of the process
#: — logging the false line "Nasdaq 403 x10" — which is exactly the outcome the
#: time-bounded window was added to prevent, and strictly easier to hit than before (the
#: exception arm used to increment nothing, so ten real 403s were required). The mirror
#: case was just as wrong: nine 403s plus one timeout fired the transient arm, which zeroes
#: the counter, so a genuinely persistent credential failure could never accumulate to the
#: permanent latch as long as one timeout interleaved. `_build_shorts` fans out under
#: `asyncio.Semaphore(10)`, so ten of either in one moment is one blip, not ten events.
_nasdaq_auth_failures: int = 0          # 403 only → PERMANENT latch (credential-shaped)
_nasdaq_transient_failures: int = 0     # timeouts/connect errors → time-bounded window
_MAX_CONSECUTIVE_FAILURES = 10
_nasdaq_rate_limited_until: float = 0

#: A TRANSIENT run of failures disables Nasdaq for a WINDOW, not for the process.
#:
#: `_nasdaq_kill_switch` is a permanent latch with no reset anywhere in this module, which
#: is right for 402/403 — credential-shaped, persistent conditions — and wrong for the
#: timeouts and connect errors the `except Exception` arm also counts. `_build_shorts` fans
#: the short universe out under `asyncio.Semaphore(10)`, so ONE ten-second network blip
#: raises ten times concurrently, trips the counter, and costs the fallback until the next
#: deploy. The counter is also only zeroed on an HTTP 200, which by this module's own
#: premise ("Nasdaq black-holes this User-Agent") essentially never happens — so
#: "consecutive" was really "cumulative for the life of the process".
_NASDAQ_TRANSIENT_DISABLE_SECONDS = 900
_nasdaq_disabled_until: float = 0

#: Read timeout for the Nasdaq FALLBACK. It shared the 15 s client timeout with the FINRA
#: path, and Nasdaq's public endpoint now black-holes this User-Agent — so every miss hung
#: the request for the full 15 s and returned an exception whose `str()` is EMPTY
#: ("Nasdaq API error for BTCUSD: " is the literal production log line). A fallback that
#: only ever runs after the primary already answered must not cost more than the primary.
_NASDAQ_TIMEOUT_SECONDS = 4.0

#: How long a completely unanswerable symbol is remembered as "no data". `get_short_interest`
#: returned `{}` WITHOUT caching it, so `/stocks/{t}/overview` re-ran the whole FINRA+Nasdaq
#: attempt — up to 15 s of it — on every 120 s cache miss, forever, for any ticker neither
#: source covers. Short enough that a genuine outage recovers on its own; long enough that a
#: permanently-uncovered ticker stops costing a request-path round trip every two minutes.
#:
#: ⚠️ This applies ONLY when a source actually ANSWERED "no rows" — see `_FAILURE_TTL_SECONDS`.
_EMPTY_TTL_SECONDS = 900

#: How long an UNANSWERED symbol is remembered, i.e. when nothing was reached at all: a
#: FINRA 5xx, the 429 cooldown, a kill switch, a Nasdaq timeout.
#:
#: This has to be its own, much shorter number. `_NO_DATA` exists precisely so that "FINRA
#: answered and has no rows" is distinguishable from "the attempt failed" — and writing the
#: same 900-second `{}` memo for both threw that distinction away three lines after making
#: it, pinning a transient as a measured answer. A FINRA 429 sets a 300 s cooldown; the memo
#: outlived it by ten minutes, so `/stocks/{t}/overview` and Home's Skeptical Money scan
#: kept reporting "no short interest" for ~10 minutes after the upstream was healthy. Short
#: enough to ride out every backoff this module has, long enough to stop a per-request 4 s
#: Nasdaq timeout storm. Same rule `fred._mark_failed` and `commodity_service._get_quote`
#: follow: a failure and a real empty answer must never become the same cache entry.
_FAILURE_TTL_SECONDS = 60


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


def _is_stale_finra_snapshot(data: Any) -> bool:
    """True for a FINRA payload that has the 3-month change (`short_change_3m`,
    a FINRA-only field) but NO `history` series — a pre-feature artifact written
    before the integration built the 12-month series. Forces a re-fetch so the
    trend chart can fill. CANNOT loop: current FINRA code always builds `history`
    whenever short_change_3m exists (change_3m needs >=2 settlement rows ~90 days
    apart → >=2 history points). Nasdaq snapshots carry no short_change_3m,
    so legitimate snapshot-only tickers are never flagged."""
    return (
        isinstance(data, dict)
        and data.get("short_change_3m") is not None
        and not data.get("history")
    )


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

        # Pre-feature rows lack the `history` series — treat as a miss so the
        # 12-month trend chart can fill from a fresh FINRA fetch.
        if cached_at < _SI_SCHEMA_FLOOR:
            logger.info(f"Short interest Supabase cache PRE-FLOOR for {ticker} — forcing re-fetch")
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
        # Fetch ~12 months of data — enough for the report's short-interest
        # trend chart (FINRA publishes twice monthly → ~24 points) and the
        # 3-month change. Results come oldest-first; last row is the latest.
        now = datetime.now(timezone.utc)
        twelve_months_ago = now - timedelta(days=365)
        start_date = twelve_months_ago.strftime("%Y-%m-%d")
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

        if resp.status_code == 204:
            # 204 = FINRA answered, and has no short-interest rows for this symbol. That is
            # a REAL answer — FINRA covers every exchange-listed and OTC equity, so an index
            # (^GSPC), a crypto pair (BTCUSD) or a fund simply has none and never will. It
            # was logged as `FINRA API failed for ^GSPC: 204` at WARNING every cycle and
            # then fell through to the Nasdaq fallback, which cannot know better either.
            _finra_consecutive_failures = 0
            logger.debug("FINRA: no short-interest rows for %s (204)", ticker)
            return _NO_DATA
        if resp.status_code != 200:
            logger.warning(f"FINRA API failed for {ticker}: {resp.status_code}")
            return None

        _finra_consecutive_failures = 0
        rows = resp.json()

        if not rows or not isinstance(rows, list):
            # `_NO_DATA`, not None — FINRA ANSWERED (HTTP 200) and has nothing for this
            # symbol, which is the same fact a 204 carries. Returning None classified it as
            # a FAILURE and cost twice: the caller then paid the Nasdaq fallback the 204
            # branch exists to skip (up to `_NASDAQ_TIMEOUT_SECONDS` per attempt), and the
            # memo got `_FAILURE_TTL_SECONDS` (60 s) instead of `_EMPTY_TTL_SECONDS`
            # (900 s), so an unanswerable symbol was re-attempted 15x more often than
            # intended. 204 is the dominant shape today, which is why this was only a cost
            # bug — but any upstream change to `200 []` would have moved every uncovered
            # ticker back onto the slow path silently.
            logger.debug("FINRA: no short-interest rows for %s (200, empty body)", ticker)
            return _NO_DATA

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
        # trend chart — we ALREADY fetched ~12 months of rows above; the
        # integration previously discarded all but the latest. Last 24 points,
        # oldest→newest (FINRA publishes twice monthly → ~12 months).
        history: List[Dict[str, Any]] = []
        for row in rows[-24:]:
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
    global _nasdaq_kill_switch, _nasdaq_auth_failures, _nasdaq_transient_failures
    global _nasdaq_rate_limited_until, _nasdaq_disabled_until

    if _nasdaq_kill_switch:
        return None

    if time.time() < _nasdaq_disabled_until:
        return None

    if time.time() < _nasdaq_rate_limited_until:
        return None

    try:
        client = await _get_client()
        url = _NASDAQ_SHORT_INTEREST_URL.format(ticker=ticker.upper())
        resp = await client.get(url, headers=_HEADERS, timeout=_NASDAQ_TIMEOUT_SECONDS)

        if resp.status_code == 429:
            _nasdaq_rate_limited_until = time.time() + _RATE_LIMIT_COOLDOWN
            logger.warning(f"Nasdaq rate-limited (429) for {ticker}")
            return None

        if resp.status_code == 402:
            _nasdaq_kill_switch = True
            logger.critical("Nasdaq API returned 402 — short interest via Nasdaq DISABLED")
            return None

        if resp.status_code == 403:
            _nasdaq_auth_failures += 1
            if _nasdaq_auth_failures >= _MAX_CONSECUTIVE_FAILURES:
                _nasdaq_kill_switch = True
                logger.critical(f"Nasdaq 403 x{_MAX_CONSECUTIVE_FAILURES} — Nasdaq DISABLED")
            return None

        if resp.status_code != 200:
            logger.warning(f"Nasdaq API failed for {ticker}: {resp.status_code}")
            return None

        # A 200 clears BOTH families — the source is demonstrably reachable AND authorised.
        _nasdaq_auth_failures = 0
        _nasdaq_transient_failures = 0

        body = resp.json()
        data = body.get("data")
        if data is None:
            # Nasdaq returns data=null for non-NASDAQ-listed stocks (NYSE, AMEX)
            msg = body.get("message", "")
            if "not available" in msg.lower() or "only supported" in msg.lower():
                logger.info(f"Nasdaq: not available for {ticker} (likely NYSE)")
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
        # Count it. The kill switch only ever incremented on a 403, so the failure mode
        # Nasdaq actually exhibits — a silent black-hole that ends in a timeout whose
        # `str()` is empty — could never trip it, and every call kept paying the timeout.
        _nasdaq_transient_failures += 1
        if _nasdaq_transient_failures >= _MAX_CONSECUTIVE_FAILURES:
            # A WINDOW, not the permanent latch. These are timeouts and connect errors —
            # transient by nature — and ten of them land together from one blip because
            # `_build_shorts` fans out under a semaphore of 10. Zeroing the counter is part
            # of the fix: without it the switch re-trips on the very next failure after the
            # window and the disable is permanent by another route.
            _nasdaq_disabled_until = time.time() + _NASDAQ_TRANSIENT_DISABLE_SECONDS
            _nasdaq_transient_failures = 0
            logger.warning(
                "Nasdaq short-interest disabled for %ds after %d consecutive failures "
                "(last: %s) — FINRA remains the primary source",
                _NASDAQ_TRANSIENT_DISABLE_SECONDS, _MAX_CONSECUTIVE_FAILURES,
                type(e).__name__,
            )
        else:
            # `type(e).__name__` because a timeout's str() is empty: the production log
            # read "Nasdaq API error for BTCUSD: " with nothing after the colon.
            logger.warning(
                "Nasdaq API error for %s: %s: %s", ticker, type(e).__name__, e or "(no message)",
            )
        return None


# ── Public API ──────────────────────────────────────────────────


async def get_short_interest(ticker: str) -> Dict[str, Any]:
    """
    Fetch short interest data with two-tier caching.
    Tries FINRA API first, then Nasdaq.

    Returns dict with:
      - shares_short: int
      - shares_short_prior_month: int (if available)
      - short_ratio: float (days to cover)
      - short_percent_of_float: float (if the source supplies it)
      - settlement_date: str (if available)
    """
    ticker = ticker.upper()
    mem_key = f"finra_short:{ticker}"

    # Tier 1: In-memory cache (skip a stale pre-feature snapshot → re-fetch)
    cached = _mem_cache_get(mem_key)
    if cached is not None and not _is_stale_finra_snapshot(cached):
        # `{}` is a real memoised answer ("nothing anywhere, recently"), and it is falsy —
        # so this must test `is not None`, which it does.
        return cached

    # Tier 2 / Tier 3: Supabase. All three of these are SYNCHRONOUS PostgREST round trips
    # on a request path (`/stocks/{t}/overview` gathers this alongside 13 FMP coroutines,
    # and `/home/dashboard` calls it per ticker) and the app runs ONE uvicorn worker, so
    # each one used to suspend every other in-flight request. The 2026-08-27 event-loop fix
    # threaded `stock_overview_service`'s own three calls and declared the path clean; this
    # integration's three, on the same path, were missed.
    sb_data = await asyncio.to_thread(_supabase_cache_get, ticker)
    if sb_data is not None and not _is_stale_finra_snapshot(sb_data):
        _mem_cache_set(mem_key, sb_data)
        return sb_data

    # Tier 3: Stale Supabase data (any age) — better than N/A
    stale = await asyncio.to_thread(_supabase_cache_get_stale, ticker)

    # Try FINRA API first (covers ALL exchanges)
    result = await _fetch_from_finra(ticker)

    answered_empty = result is _NO_DATA
    if answered_empty:
        # FINRA answered "no rows for this symbol" (204). It covers every exchange-listed
        # and OTC equity, so Nasdaq — which covers a SUBSET — cannot know better. Skipping
        # the fallback here is what stops an index or a crypto pair from paying the Nasdaq
        # timeout on every request.
        result = None
    elif not result:
        # Fallback 1: Nasdaq (covers NASDAQ-listed stocks)
        result = await _fetch_from_nasdaq(ticker)

    if result:
        _mem_cache_set(mem_key, result)
        await asyncio.to_thread(_supabase_cache_set, ticker, result)
        return result

    # Fall back to stale cache data
    if stale:
        _mem_cache_set(mem_key, stale)
        return stale

    # NOTHING anywhere. Memoise BRIEFLY — in memory only, never in Supabase (a persisted
    # empty would outlive the outage that caused it). Without this, `/stocks/{t}/overview`
    # re-ran the full FINRA attempt plus, until the timeout fix above, a 15 s Nasdaq wait
    # on EVERY 120 s cache miss, forever, for any ticker neither source covers — the single
    # largest avoidable cost on the detail-screen path.
    #
    # ⚠️ TWO DIFFERENT TTLs, because this line is reached for two different facts.
    # `answered_empty` means FINRA returned a clean 204 — a measured "this company has no
    # reported short interest", stable for days. Everything else (a 5xx, the 429 cooldown,
    # a kill switch, a Nasdaq timeout) means nothing was reached at all, and writing the
    # 900-second memo for that pinned a transient as an answer: FINRA's own 429 cooldown is
    # 300 s, so Home's Skeptical Money scan kept reporting "no short interest" for ten
    # minutes after the upstream recovered. `_NO_DATA` exists to tell these apart; this is
    # where that distinction has to be spent.
    ttl = _EMPTY_TTL_SECONDS if answered_empty else _FAILURE_TTL_SECONDS
    _cache[mem_key] = (time.time() - (_CACHE_TTL - ttl), {})
    return {}


async def close_finra_client() -> None:
    """Tear-down hook for app.main lifespan.

    This module keeps TWO persistent clients, not one — `_http_client` for the Nasdaq path
    (`_get_client`) and `_finra_http_client` for the FINRA OAuth path (`_get_finra_client`) —
    and neither had a closer, so both leaked their connection pools at shutdown. Closing both
    here keeps the lifespan's import list to one name per module, matching
    `close_fmp_client` / `close_coingecko_client`.

    Both builders re-create on `is_closed`, so calling this is safe even if something
    subsequently reaches for a client during shutdown.
    """
    global _http_client, _finra_http_client
    _http_client = await _aclose("_http_client", _http_client)
    _finra_http_client = await _aclose("_finra_http_client", _finra_http_client)


async def _aclose(name: str, client: Optional[httpx.AsyncClient]) -> None:
    """Close `client` if it exists, swallowing (but logging) any failure. Always returns None
    so the caller can reassign the global unconditionally.

    Best-effort on purpose: shutdown must not raise. A closer that propagates would abort the
    lifespan teardown and skip every `close_*` registered after it in `main.py`, turning one
    integration's problem into four leaked pools.
    """
    if client is None:
        return None
    try:
        await client.aclose()
    except Exception as e:  # noqa: BLE001 — logged, and shutdown must continue
        logger.warning("finra %s failed to close cleanly: %s: %s", name, type(e).__name__, e)
    return None
