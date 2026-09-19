"""
CoinGecko API Integration — Crypto Market Data

Provides accurate crypto fundamentals (supply, volume, FDV, market cap)
that FMP premium doesn't support well.

Three-tier symbol resolution:
  1. Hardcoded top 100 map (instant, zero API cost)
  2. Supabase crypto_coin_id_cache (permanent, for dynamic coins)
  3. CoinGecko /search endpoint (1 API call, first time only)

Auth is DERIVED FROM THE BASE URL, never configured separately — see `_auth_header`.
CoinGecko rejects a paid key sent to api.coingecko.com with error 10010, and a demo key
sent to pro-api.coingecko.com with 10002, so the two must move together.

Rate limit: `settings.COINGECKO_MAX_CALLS_PER_MINUTE` (default 25, a safety margin under the
free Demo plan's 30/min). A paid plan allows far more — raise the setting when the plan
changes, or the client throttles itself to demo speed on a paid key.
"""

import asyncio
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ── Typed failures ───────────────────────────────────────────────
#
# Every call used to return `None` on any failure — 429, timeout, 4xx, 5xx alike. That
# left the service layer unable to tell "this coin has no data" from "we were rate
# limited", and `crypto_service._usd(default=0)` turned both into **$0.00 on screen**.
# The endpoint layer never saw an exception, so `error_response_from_exception` could not
# classify anything and the user got a confident wrong number instead of a retry prompt.
# Mirrors the FMP integration's hierarchy (`integrations/fmp.py`), per
# `.claude/rules/integrations.md`: HTTP in, parsed dict out, typed exception on failure.


class CoinGeckoException(Exception):
    """Base for every CoinGecko failure."""


class CoinGeckoRateLimitException(CoinGeckoException):
    """429. Carries `retry_after` seconds when the response supplied it."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class CoinGeckoUnavailableException(CoinGeckoException):
    """Timeout, connection error, 5xx, or an unparseable body — all retryable."""


class CoinGeckoRangeExceededException(CoinGeckoException):
    """The request reached past the plan's history window.

    ⚠️ CoinGecko answers this with **HTTP 401** and `error_code` 10012 — a status that
    looks exactly like an auth failure and is not one. On the Basic plan `days=730` is
    fine and `days=1095` is not; `/ohlc/range` answers 401/10005 for the same reason (a
    Pro-only endpoint). Both are permanent for the current plan, so they must NEVER be
    routed into credential-refresh or key-rotation logic — the key is fine.
    """


#: CoinGecko `error_code` values that arrive as 401 but are NOT authentication failures.
_NON_AUTH_401_CODES = {10012, 10005}


# ── Top 100 Symbol → CoinGecko ID mapping ────────────────────────
# Hardcoded for zero API cost. Excludes stablecoins/wrapped tokens.
# Rankings approximate — sorted by typical market cap.

SYMBOL_TO_COINGECKO_ID: Dict[str, str] = {
    # Top 20
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "SOL": "solana",
    "TRX": "tron",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "LINK": "chainlink",
    "AVAX": "avalanche-2",
    "SHIB": "shiba-inu",
    "SUI": "sui",
    "TON": "the-open-network",
    "XLM": "stellar",
    "DOT": "polkadot",
    "HBAR": "hedera-hashgraph",
    "BCH": "bitcoin-cash",
    "LTC": "litecoin",
    "LEO": "leo-token",
    "UNI": "uniswap",
    # 21–40
    "NEAR": "near",
    "AAVE": "aave",
    "PEPE": "pepe",
    "TAO": "bittensor",
    "ICP": "internet-computer",
    "ETC": "ethereum-classic",
    "RENDER": "render-token",
    "POL": "polygon-ecosystem-token",
    "MATIC": "polygon-ecosystem-token",
    "APT": "aptos",
    "MNT": "mantle",
    "KAS": "kaspa",
    "ATOM": "cosmos",
    "FIL": "filecoin",
    # Served as a related coin on the Filecoin/storage screens. Its ABSENCE here was a
    # live defect, not a cosmetic gap: `is_blocked_symbol` blocks a crypto pair by
    # membership in THIS map, so "ARUSD" (5 chars, below the 6-char length rule) was
    # not refused — its Technical Analysis and sentiment went to FMP and came back a
    # raw 402, and the related-coin row rendered the label "AR" instead of "Arweave".
    # `test_crypto_symbol_map_parity.py` now fails the build on the next such omission.
    "AR": "arweave",
    "ARB": "arbitrum",
    "VET": "vechain",
    "FET": "fetch-ai",
    "ONDO": "ondo-finance",
    "WLD": "worldcoin-wld",
    "ALGO": "algorand",
    # 41–60
    "OP": "optimism",
    "CRO": "crypto-com-chain",
    "JUP": "jupiter-exchange-solana",
    "BONK": "bonk",
    "STX": "blockstack",
    "INJ": "injective-protocol",
    "SEI": "sei-network",
    "IMX": "immutable-x",
    "GRT": "the-graph",
    "FLR": "flare-networks",
    "THETA": "theta-token",
    "RUNE": "thorchain",
    "LDO": "lido-dao",
    "FTM": "fantom",
    "FLOKI": "floki",
    "TIA": "celestia",
    "PYTH": "pyth-network",
    "QNT": "quant-network",
    "ENA": "ethena",
    "BEAM": "beam-2",
    # 61–80
    "SAND": "the-sandbox",
    "MANA": "decentraland",
    "AXS": "axie-infinity",
    "GALA": "gala",
    "FLOW": "flow",
    "KAVA": "kava",
    "ENS": "ethereum-name-service",
    "CHZ": "chiliz",
    "PENDLE": "pendle",
    "CAKE": "pancakeswap-token",
    "ROSE": "oasis-network",
    "EOS": "eos",
    "NEO": "neo",
    "XTZ": "tezos",
    "IOTA": "iota",
    "ZIL": "zilliqa",
    "ONE": "harmony",
    "CELO": "celo",
    "CFX": "conflux-token",
    "COMP": "compound-governance-token",
    # 81–100
    "SNX": "havven",
    "CRV": "curve-dao-token",
    "DYDX": "dydx-chain",
    "GMX": "gmx",
    "1INCH": "1inch",
    "MASK": "mask-network",
    "SUSHI": "sushi",
    "BAL": "balancer",
    "ETHFI": "ether-fi",
    "STRK": "starknet",
    "ZK": "zksync",
    "BLUR": "blur",
    "EIGEN": "eigenlayer",
    "WIF": "dogwifcoin",
    "JASMY": "jasmycoin",
    "SKY": "sky",
    "TRUMP": "official-trump",
    "PI": "pi-network",
    "HYPE": "hyperliquid",
    "VIRTUAL": "virtual-protocol",
    "PENGU": "pudgy-penguins",
    "XMR": "monero",
    "XDC": "xdce-crowd-sale",
    "DASH": "dash",
    "DCR": "decred",
    "ZEC": "zcash",
    "NEXO": "nexo",
    "OKB": "okb",
    "BGB": "bitget-token",
    "MORPHO": "morpho",
}


class CoinGeckoClient:
    """
    Client for the CoinGecko API, Demo or paid.

    Uses a persistent httpx.AsyncClient with connection pooling and a sliding-window rate
    limiter. The plan is inferred from the base URL alone (`_auth_header`), so switching
    plans is one environment variable.
    """

    _DEMO_HEADER = "x-cg-demo-api-key"
    _PRO_HEADER = "x-cg-pro-api-key"
    _PRO_HOST_MARKER = "pro-api."
    _WINDOW_SECONDS = 60

    def __init__(self):
        self.base_url = settings.COINGECKO_BASE_URL
        self.api_key = settings.COINGECKO_API_KEY
        self.timeout = settings.HTTP_TIMEOUT_SECONDS
        self._max_calls_per_minute = max(1, int(settings.COINGECKO_MAX_CALLS_PER_MINUTE))
        self._client: Optional[httpx.AsyncClient] = None
        self._rate_window: deque = deque(maxlen=self._max_calls_per_minute)
        self._rate_lock = asyncio.Lock()
        # In-memory cache for dynamically resolved IDs (symbol → coingecko_id)
        self._dynamic_id_cache: Dict[str, str] = {}
        self._unresolved_until: Dict[str, float] = {}   # Tier 2a negative memo (see resolve_coin_id)
        # Request dedup (CLAUDE.md invariant #4). Call volume is the binding constraint
        # on this integration — 100K/month — so N concurrent viewers of the same coin
        # must cost ONE upstream call, not N. Keyed on endpoint + sorted params.
        self._inflight: Dict[str, "asyncio.Future"] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                    keepalive_expiry=30,
                ),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def _auth_header(self) -> str:
        """The header name CoinGecko expects for THIS base URL.

        Deriving it is the whole point. When these were configured independently, a paid key
        kept being sent as `x-cg-demo-api-key` to `api.coingecko.com`; CoinGecko answered
        10010 on key-required endpoints but served the public ones anonymously with HTTP 200,
        so production looked healthy while running on the free tier with the paid key ignored.
        """
        return self._PRO_HEADER if self._PRO_HOST_MARKER in self.base_url else self._DEMO_HEADER

    async def _rate_limit(self):
        """Sliding-window rate limiter: max `_max_calls_per_minute` calls per 60 seconds.

        ⚠️ The sleep is OUTSIDE the lock. It used to be inside, which turned a full window
        into a ~60-second stall for EVERY CoinGecko caller in the process, not just the one
        that hit the ceiling — a single burst would blow through the 6s Market Pulse build
        timeout and the client's 30s ceiling together. At the old 25/min that was a
        nuisance; at 300/min with the crypto fan-out it is an availability bug.

        The window is still appended to under the lock, so the accounting stays exact; only
        the waiting is concurrent.
        """
        while True:
            async with self._rate_lock:
                now = time.monotonic()
                if len(self._rate_window) < self._max_calls_per_minute:
                    self._rate_window.append(now)
                    return
                elapsed = now - self._rate_window[0]
                if elapsed >= self._WINDOW_SECONDS:
                    self._rate_window.append(now)
                    return
                wait = min(self._WINDOW_SECONDS, self._WINDOW_SECONDS - elapsed + 0.1)
            logger.info("CoinGecko rate limit: sleeping %.1fs", wait)
            await asyncio.sleep(wait)

    _MAX_RETRIES = 3
    _BACKOFF_BASE_SECONDS = 1.0
    # Longest we will honour a 429 Retry-After for before giving the caller its
    # degrade path. A monthly-quota 429 can carry hours; nobody is waiting for that.
    # A Retry-After PAST this cap is terminal for the request (no capped sleep, no
    # retry — capping the sleep alone still parked the leader and every shielded joiner
    # for 2 × 60 s per cold key while the iOS request had timed out at 20 s) and latches
    # the client for the cap window so the next requests fail fast instead of queueing.
    _MAX_RETRY_AFTER_SECONDS = 60.0
    _quota_exhausted_until: float = 0.0

    @staticmethod
    def _error_code(response: "httpx.Response") -> Optional[int]:
        """CoinGecko's own `error_code`, wherever it put it this time.

        The body is `{"status": {"error_code": N}}` on some endpoints and
        `{"error": {"status": {"error_code": N}}}` on others — both shapes observed live
        on 2026-09-08. Returns None when the body is not JSON at all (Cloudflare answers
        some blocks with bare text).
        """
        try:
            body = response.json()
        except Exception:
            return None
        if not isinstance(body, dict):
            return None
        for holder in (body, body.get("error") if isinstance(body.get("error"), dict) else {}):
            status = holder.get("status") if isinstance(holder, dict) else None
            if isinstance(status, dict) and isinstance(status.get("error_code"), int):
                return status["error_code"]
        return None

    async def _request_once(
        self, endpoint: str, params: Optional[Dict[str, Any]]
    ) -> Any:
        """One attempt. Raises a typed exception rather than returning None."""
        await self._rate_limit()

        url = f"{self.base_url}/{endpoint}"
        headers = {}
        if self.api_key:
            headers[self._auth_header] = self.api_key

        try:
            client = await self._get_client()
            response = await client.get(url, params=params or {}, headers=headers)
        except httpx.HTTPError as e:
            raise CoinGeckoUnavailableException(
                f"{endpoint}: {type(e).__name__}: {e}"
            ) from e

        if response.status_code == 429:
            retry_after = None
            raw = response.headers.get("retry-after")
            if raw:
                try:
                    retry_after = float(raw)
                except (TypeError, ValueError):
                    retry_after = None
            raise CoinGeckoRateLimitException(
                f"{endpoint}: 429 rate limited", retry_after=retry_after
            )

        # ⚠️ A 401 here is usually NOT an auth failure — see
        # `CoinGeckoRangeExceededException`. Classify on the body's `error_code`, never
        # on the status alone, or an over-range chart request reads as a dead API key.
        if response.status_code == 401:
            code = self._error_code(response)
            if code in _NON_AUTH_401_CODES:
                raise CoinGeckoRangeExceededException(
                    f"{endpoint}: outside this plan's window (error_code {code})"
                )
            raise CoinGeckoException(f"{endpoint}: 401 unauthorized (error_code {code})")

        if response.status_code >= 500:
            raise CoinGeckoUnavailableException(
                f"{endpoint}: upstream {response.status_code}"
            )
        if response.status_code >= 400:
            raise CoinGeckoException(
                f"{endpoint}: HTTP {response.status_code} "
                f"(error_code {self._error_code(response)})"
            )

        try:
            return response.json()
        except Exception as e:
            raise CoinGeckoUnavailableException(
                f"{endpoint}: unparseable response body"
            ) from e

    async def _make_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Rate-limited, deduped, retried GET.

        Retries only what retrying can fix — a 429 or a transient outage. A range/plan
        refusal and a genuine 4xx are permanent for this plan and raise immediately;
        burning three attempts on them would just spend the call budget the retry exists
        to protect.
        """
        key = f"{endpoint}?{sorted((params or {}).items())}"
        inflight = self._inflight.get(key)
        if inflight is not None:
            # Shielded: a cancelled joiner must not cancel the leader's request.
            return await asyncio.shield(inflight)

        loop = asyncio.get_running_loop()
        fut: "asyncio.Future" = loop.create_future()
        self._inflight[key] = fut
        try:
            last: Optional[Exception] = None
            remaining = self._quota_exhausted_until - time.monotonic()
            if remaining > 0:
                # Latched by an earlier over-cap 429: fail fast for the cap window rather
                # than sleeping the cap again on every cold key.
                raise CoinGeckoRateLimitException(
                    f"CoinGecko quota exhausted (fail-fast for {remaining:.0f}s more)",
                    retry_after=remaining,
                )
            for attempt in range(self._MAX_RETRIES):
                try:
                    result = await self._request_once(endpoint, params)
                    if not fut.done():
                        fut.set_result(result)
                    return result
                except (CoinGeckoRateLimitException, CoinGeckoUnavailableException) as e:
                    last = e
                    if attempt == self._MAX_RETRIES - 1:
                        break
                    wait = self._BACKOFF_BASE_SECONDS * (2 ** attempt)
                    if isinstance(e, CoinGeckoRateLimitException) and e.retry_after:
                        # Honour Retry-After, but BOUNDED. CoinGecko sends 429 for a burst
                        # AND for monthly-quota exhaustion, and the header is parsed as any
                        # float; an unbounded sleep here parked the in-flight leader — and
                        # every joiner shielded on its future — for the full value, hours
                        # in the quota case, while each HTTP handler timed out client-side
                        # and the server task lived on. Past the cap the request is OVER:
                        # the caller degrades (its own except arm) now, and the client
                        # fails fast for the cap window.
                        try:
                            retry_after = float(e.retry_after)
                        except (TypeError, ValueError):
                            retry_after = 0.0
                        if retry_after > self._MAX_RETRY_AFTER_SECONDS:
                            self._quota_exhausted_until = (
                                time.monotonic() + self._MAX_RETRY_AFTER_SECONDS
                            )
                            logger.error(
                                "CoinGecko %s: Retry-After %.0fs exceeds the %.0fs cap — "
                                "treating as quota exhaustion, failing fast for %.0fs",
                                endpoint, retry_after, self._MAX_RETRY_AFTER_SECONDS,
                                self._MAX_RETRY_AFTER_SECONDS,
                            )
                            break
                        wait = max(wait, retry_after)
                    logger.warning(
                        "CoinGecko %s attempt %d/%d failed (%s) — retrying in %.1fs",
                        endpoint, attempt + 1, self._MAX_RETRIES, e, wait,
                    )
                    await asyncio.sleep(wait)
            assert last is not None
            logger.error("CoinGecko %s failed after %d attempts: %s",
                         endpoint, self._MAX_RETRIES, last)
            if not fut.done():
                fut.set_exception(last)
            raise last
        except BaseException as e:
            # Includes CancelledError and the permanent errors from `_request_once`.
            if not fut.done():
                fut.set_exception(e if isinstance(e, Exception) else
                                  CoinGeckoUnavailableException(f"{endpoint}: {e!r}"))
            raise
        finally:
            self._inflight.pop(key, None)

    # ── Symbol resolution ────────────────────────────────────────

    async def resolve_coin_id(self, symbol: str) -> Optional[str]:
        """
        Resolve a crypto symbol to a CoinGecko coin ID.

        Three-tier lookup:
          1. Hardcoded top 100 map (instant)
          2. In-memory + Supabase cache (for dynamic coins; the Supabase row is honoured
             only when stamped after the exact-symbol rule and inside a 30 d TTL —
             see `_coin_id_row_is_trusted`)
          3. CoinGecko /search API (1 call, exact symbol match only, then cached)
        """
        symbol = symbol.upper()

        # Tier 1: hardcoded map
        coin_id = SYMBOL_TO_COINGECKO_ID.get(symbol)
        if coin_id:
            return coin_id

        # Tier 2: in-memory dynamic cache
        coin_id = self._dynamic_id_cache.get(symbol)
        if coin_id:
            return coin_id

        # Tier 2a: NEGATIVE memo. A symbol `/search` has already answered "no such coin"
        # for (no coins, or no exact symbol match) is unresolvable for a while — nothing
        # about it changes minute to minute. Without this the distrusted fuzzy-era rows
        # (`_coin_id_row_is_trusted`) never converged: they are by construction the symbols
        # with no exact match, so every cache miss re-read the row, skipped it, and paid a
        # `/search` — one per Tracking-feed poll per symbol, forever (W2 B-3). Transport
        # failures are deliberately NOT memoised (a 429 is not an answer).
        # `setdefault` on the instance dict: several tests build the client with
        # `__new__` and set only the attributes they know about.
        unresolved = self.__dict__.setdefault("_unresolved_until", {})
        until = unresolved.get(symbol)
        if until is not None:
            if time.monotonic() < until:
                return None
            unresolved.pop(symbol, None)

        # Tier 2b: Supabase cache (trusted rows only — a fuzzy-era pin is skipped)
        coin_id = await asyncio.to_thread(self._check_coin_id_db, symbol)
        if coin_id:
            self._dynamic_id_cache[symbol] = coin_id
            return coin_id

        # Tier 3: CoinGecko /search
        logger.info(f"Resolving unknown symbol via CoinGecko /search: {symbol}")
        # Degrades to None rather than propagating. `_make_request` raises now, but an
        # unresolvable SYMBOL is a legitimate "no such coin" — a different thing from an
        # outage, and the callers all treat None as "hide the surface". Letting a search
        # failure raise would turn a typo into an error page.
        try:
            search_result = await self._make_request("search", params={"query": symbol})
        except CoinGeckoException as e:
            logger.warning("CoinGecko /search failed for %s: %s", symbol, e)
            return None
        if not search_result:
            return None

        coins = search_result.get("coins", [])
        if not coins:
            logger.warning(f"CoinGecko /search returned no results for: {symbol}")
            unresolved[symbol] = time.monotonic() + self._UNRESOLVED_TTL_SECONDS
            return None

        # Pick best match: exact symbol match with highest market cap rank. NO fuzzy
        # fallback: `/search` matches NAMES by substring, so `coins[0]` for a symbol
        # nobody lists was some unrelated coin — and once written to
        # `crypto_coin_id_cache` (no TTL) that coin's price, market cap and supply were
        # served under the requested symbol for every user until the row was deleted by
        # hand. An unresolved symbol hides the surface instead.
        best = None
        for c in coins:
            if (c.get("symbol") or "").upper() == symbol:
                if best is None or (c.get("market_cap_rank") or 9999) < (best.get("market_cap_rank") or 9999):
                    best = c
        if not best:
            logger.info(
                "CoinGecko /search has no exact symbol match for %s (%d fuzzy hits, "
                "first %r) — unresolved, not cached",
                symbol, len(coins), (coins[0].get("symbol") if isinstance(coins[0], dict) else None),
            )
            unresolved[symbol] = time.monotonic() + self._UNRESOLVED_TTL_SECONDS
            return None

        coin_id = best.get("id")
        coin_name = best.get("name", symbol)
        if coin_id:
            self._dynamic_id_cache[symbol] = coin_id
            asyncio.get_event_loop().run_in_executor(
                None, self._upsert_coin_id_db, symbol, coin_id, coin_name
            )
            logger.info(f"Resolved {symbol} → {coin_id} ({coin_name})")

        return coin_id

    # Rows written BEFORE the exact-symbol rule shipped (7f0b1fd3, 2026-09-13 22:26 -0600)
    # may be the fuzzy `coins[0]` pins that rule was written to stop — 5½ months of them,
    # with no TTL and no migration that purges the table. Served unconditionally, they kept
    # answering another coin's price under the requested symbol AFTER the fix. Any row with
    # a `cached_at` before this instant is ignored (and re-resolved by the exact rule); a
    # row with no / unparseable `cached_at` is of unknown provenance and treated the same.
    _COIN_ID_PIN_VALID_FROM = datetime(2026, 9, 15, tzinfo=timezone.utc)
    # And an honoured lifetime, so a future bad pin (or a coin that CoinGecko re-ids) is
    # bounded instead of permanent. One `/search` per symbol per 30 d is negligible on the
    # 100K/month budget.
    _COIN_ID_PIN_TTL = timedelta(days=30)
    # How long a "no such coin" answer from `/search` is remembered in memory (Tier 2a).
    # Long enough to collapse a feed's ~31 s polls into one search, short enough that a
    # newly listed coin appears within the hour.
    _UNRESOLVED_TTL_SECONDS = 3600.0

    @classmethod
    def _coin_id_row_is_trusted(cls, row: Dict[str, Any], now: Optional[datetime] = None) -> bool:
        """True only for a row stamped after the exact-symbol rule and inside the TTL."""
        stamp = row.get("cached_at")
        if not isinstance(stamp, str) or not stamp.strip():
            return False
        try:
            cached_at = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
        except ValueError:
            return False
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        if cached_at < cls._COIN_ID_PIN_VALID_FROM:
            return False
        if cached_at > now + timedelta(minutes=5):
            # A stamp from the future is a clock/row-shape problem, not a fresh pin.
            return False
        return now - cached_at <= cls._COIN_ID_PIN_TTL

    def _check_coin_id_db(self, symbol: str) -> Optional[str]:
        """Check Supabase crypto_coin_id_cache for a cached ID that is still trusted.

        Returns None for a row that predates the exact-symbol rule or is past the TTL —
        the caller then re-resolves via `/search` and the upsert refreshes `cached_at`.
        """
        try:
            from app.database import get_supabase
            sb = get_supabase()
            row = (
                sb.table("crypto_coin_id_cache")
                .select("coingecko_id, cached_at")
                .eq("symbol", symbol)
                .limit(1)
                .execute()
            )
            if row.data and len(row.data) > 0:
                first = row.data[0] or {}
                coin_id = first.get("coingecko_id")
                if not coin_id:
                    return None
                if not self._coin_id_row_is_trusted(first):
                    logger.info(
                        "Coin ID cache row for %s (%s, cached_at=%r) predates the exact-symbol "
                        "rule or is past its TTL — ignored, re-resolving via /search",
                        symbol, coin_id, first.get("cached_at"),
                    )
                    return None
                return coin_id
        except Exception as e:
            logger.warning("Coin ID cache read failed for %s: %s: %s", symbol, type(e).__name__, e)
        return None

    def _upsert_coin_id_db(self, symbol: str, coin_id: str, name: str) -> None:
        """Cache a resolved symbol → CoinGecko ID mapping, stamping `cached_at` NOW.

        The stamp must be explicit: the column's DEFAULT only fires on INSERT, so an
        upsert that lands on an existing (possibly fuzzy-era) row would otherwise keep
        the old `cached_at` and the fresh exact-rule pin would be ignored forever.
        """
        try:
            from app.database import get_supabase
            sb = get_supabase()
            sb.table("crypto_coin_id_cache").upsert(
                {
                    "symbol": symbol,
                    "coingecko_id": coin_id,
                    "name": name,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="symbol",
            ).execute()
        except Exception as e:
            logger.warning("Coin ID cache write failed for %s: %s: %s", symbol, type(e).__name__, e)

    # ── Public methods ──────────────────────────────────────────

    async def get_coin_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch comprehensive coin data from /coins/{id}.

        Returns market_data with: current_price, market_cap, total_volume,
        high_24h, low_24h, circulating_supply, total_supply, max_supply,
        fully_diluted_valuation, ath, atl, price_change_percentage_*.

        Also includes description, links, categories for auto-profile building.

        RAISES on an upstream failure (it used to swallow everything to None). That is
        the point: this is the only source of a crypto price now, and a caller that
        cannot tell "no data" from "rate limited" renders `$0.00` for both. Returns None
        only when the SYMBOL cannot be resolved, which is a real "no such coin".
        """
        coin_id = await self.resolve_coin_id(symbol)
        if not coin_id:
            logger.warning(f"Could not resolve CoinGecko ID for symbol: {symbol}")
            return None

        return await self._make_request(
            f"coins/{coin_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
        )


    # ── History and batch price ──────────────────────────────────────────────
    #
    # Everything below replaces an FMP path that now 402s. Granularity and limits are
    # MEASURED (2026-09-08, Basic plan), not assumed:
    #
    #   market_chart days=1   -> 5-minute   (289 pts)
    #   market_chart days=7   -> hourly     (169 pts)
    #   market_chart days=90  -> HOURLY, 2161 pts  ⚠️ unless interval="daily" (91 pts)
    #   market_chart days=180/365/730 -> daily
    #   days > ~730           -> HTTP 401 / error_code 10012
    #
    # `/ohlc` is deliberately NOT wrapped: its `days` is a fixed enum and its candles are
    # 30-min / 4-hour / 4-DAY — it can never return daily bars, and `/ohlc/range` (which
    # could) is Pro-plan only. Close+volume from `market_chart` is the honest series.

    @staticmethod
    def _history_window_days() -> int:
        return max(1, int(settings.CRYPTO_HISTORY_YEARS) * 365)

    def _guard_window(self, days: int) -> None:
        """Refuse an over-range request BEFORE spending a call.

        CoinGecko answers past-the-window with a 401 that costs a call and reads like an
        auth failure. Deciding locally keeps the budget and the diagnosis clean.
        """
        limit = self._history_window_days()
        if days > limit:
            raise CoinGeckoRangeExceededException(
                f"{days}d exceeds the {settings.CRYPTO_HISTORY_YEARS}-year plan window "
                f"({limit}d). Raise CRYPTO_HISTORY_YEARS if the plan changed."
            )

    async def get_market_chart(
        self, symbol: str, days: int, interval: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """`{prices, market_caps, total_volumes}`, each `[[epoch_ms, value], ...]`.

        ⚠️ Pass `interval="daily"` for any range of 90 days or more. Without it CoinGecko
        returns HOURLY data up to and including days=90 — 2161 points for a 3-month chart
        instead of 91, a ~24x payload for the same rendered line.
        """
        self._guard_window(days)
        coin_id = await self.resolve_coin_id(symbol)
        if not coin_id:
            return None
        params: Dict[str, Any] = {"vs_currency": "usd", "days": days}
        if interval:
            params["interval"] = interval
        return await self._make_request(f"coins/{coin_id}/market_chart", params=params)

    async def get_market_chart_range(
        self, symbol: str, frm: int, to: int
    ) -> Optional[Dict[str, Any]]:
        """Same shape as `get_market_chart`, for an explicit epoch-second window.

        Used for YTD, which is not expressible as a `days` count.
        """
        span_days = max(1, int((to - frm) / 86400))
        self._guard_window(span_days)
        coin_id = await self.resolve_coin_id(symbol)
        if not coin_id:
            return None
        return await self._make_request(
            f"coins/{coin_id}/market_chart/range",
            params={"vs_currency": "usd", "from": frm, "to": to},
        )

    async def get_markets(
        self, symbols: List[str], *, sparkline: bool = False
    ) -> List[Dict[str, Any]]:
        """One call for many coins: price, 24h change, volume, 24h high/low, market cap —
        and, with `sparkline=True`, a 7-day hourly series (168 points) in the SAME call.

        Replaces the per-symbol quote fan-out. Ids resolve from the hardcoded map for the
        110 symbols we ship, so a batch of six normally costs exactly one HTTP request.
        Unresolvable symbols are dropped rather than failing the batch.
        """
        ids: List[str] = []
        for sym in symbols:
            coin_id = await self.resolve_coin_id(sym)
            if coin_id:
                ids.append(coin_id)
        if not ids:
            return []
        rows = await self._make_request(
            "coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ",".join(ids),
                "price_change_percentage": "24h",
                "sparkline": "true" if sparkline else "false",
            },
        )
        return rows if isinstance(rows, list) else []

    async def get_simple_prices(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """`{coingecko_id: {usd, usd_24h_change, usd_24h_vol}}` — the cheapest batch read.

        The price-alert and Updates sweepers run on a timer over every watched ticker, so
        this is the single largest recurring draw on the 100K/month budget. One request
        regardless of how many ids, and the callers skip it entirely when no crypto is in
        scope.
        """
        id_by_symbol: Dict[str, str] = {}
        for sym in symbols:
            coin_id = await self.resolve_coin_id(sym)
            if coin_id:
                id_by_symbol[sym.upper()] = coin_id
        if not id_by_symbol:
            return {}
        data = await self._make_request(
            "simple/price",
            params={
                "ids": ",".join(sorted(set(id_by_symbol.values()))),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            },
        )
        if not isinstance(data, dict):
            return {}
        # Re-key by the SYMBOL the caller asked for; a caller holding "BTC" should not
        # have to know it is "bitcoin" upstream.
        return {
            sym: data[coin_id]
            for sym, coin_id in id_by_symbol.items()
            if isinstance(data.get(coin_id), dict)
        }


# ── Singleton ────────────────────────────────────────────────────

_coingecko_client: Optional[CoinGeckoClient] = None


def get_coingecko_client() -> CoinGeckoClient:
    global _coingecko_client
    if _coingecko_client is None:
        _coingecko_client = CoinGeckoClient()
    return _coingecko_client


async def close_coingecko_client():
    global _coingecko_client
    if _coingecko_client is not None:
        await _coingecko_client.close()
        _coingecko_client = None
