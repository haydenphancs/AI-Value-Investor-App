"""
CoinGecko API Integration — Crypto Market Data

Provides accurate crypto fundamentals (supply, volume, FDV, market cap)
that FMP premium doesn't support well.

Three-tier symbol resolution:
  1. Hardcoded top 100 map (instant, zero API cost)
  2. Supabase crypto_coin_id_cache (permanent, for dynamic coins)
  3. CoinGecko /search endpoint (1 API call, first time only)

Free Demo Plan: 30 calls/min, 10,000 calls/month.
Rate limiter enforces 25 calls/min with safety margin.
"""

import asyncio
import time
from collections import deque
from typing import Any, Dict, List, Optional
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


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
    Client for CoinGecko API (Demo plan).

    Uses a persistent httpx.AsyncClient with connection pooling and
    a sliding-window rate limiter (25 req/min).
    """

    _MAX_CALLS_PER_MINUTE = 25  # safety margin below 30
    _WINDOW_SECONDS = 60

    def __init__(self):
        self.base_url = settings.COINGECKO_BASE_URL
        self.api_key = settings.COINGECKO_API_KEY
        self.timeout = settings.HTTP_TIMEOUT_SECONDS
        self._client: Optional[httpx.AsyncClient] = None
        self._rate_window: deque = deque(maxlen=self._MAX_CALLS_PER_MINUTE)
        self._rate_lock = asyncio.Lock()
        # In-memory cache for dynamically resolved IDs (symbol → coingecko_id)
        self._dynamic_id_cache: Dict[str, str] = {}

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

    async def _rate_limit(self):
        """Sliding-window rate limiter: max 25 calls per 60 seconds."""
        async with self._rate_lock:
            now = time.monotonic()
            if len(self._rate_window) >= self._MAX_CALLS_PER_MINUTE:
                oldest = self._rate_window[0]
                elapsed = now - oldest
                if elapsed < self._WINDOW_SECONDS:
                    wait = self._WINDOW_SECONDS - elapsed + 0.1
                    logger.info(f"CoinGecko rate limit: sleeping {wait:.1f}s")
                    await asyncio.sleep(wait)
            self._rate_window.append(time.monotonic())

    async def _make_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        await self._rate_limit()

        url = f"{self.base_url}/{endpoint}"
        headers = {}
        if self.api_key:
            headers["x-cg-demo-api-key"] = self.api_key

        try:
            client = await self._get_client()
            response = await client.get(url, params=params or {}, headers=headers)

            if response.status_code == 429:
                logger.warning("CoinGecko 429 rate limited — returning None")
                return None

            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(f"CoinGecko HTTP error {e.response.status_code}: {endpoint}")
            return None
        except httpx.HTTPError as e:
            logger.error(f"CoinGecko request failed: {endpoint} — {e}")
            return None

    # ── Symbol resolution ────────────────────────────────────────

    async def resolve_coin_id(self, symbol: str) -> Optional[str]:
        """
        Resolve a crypto symbol to a CoinGecko coin ID.

        Three-tier lookup:
          1. Hardcoded top 100 map (instant)
          2. In-memory + Supabase permanent cache (for dynamic coins)
          3. CoinGecko /search API (1 call, cached permanently after)
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

        # Tier 2b: Supabase permanent cache
        coin_id = await asyncio.to_thread(self._check_coin_id_db, symbol)
        if coin_id:
            self._dynamic_id_cache[symbol] = coin_id
            return coin_id

        # Tier 3: CoinGecko /search
        logger.info(f"Resolving unknown symbol via CoinGecko /search: {symbol}")
        search_result = await self._make_request(
            "search", params={"query": symbol}
        )
        if not search_result:
            return None

        coins = search_result.get("coins", [])
        if not coins:
            logger.warning(f"CoinGecko /search returned no results for: {symbol}")
            return None

        # Pick best match: exact symbol match with highest market cap rank
        best = None
        for c in coins:
            if c.get("symbol", "").upper() == symbol:
                if best is None or (c.get("market_cap_rank") or 9999) < (best.get("market_cap_rank") or 9999):
                    best = c
        if not best:
            best = coins[0]  # fallback to first result

        coin_id = best.get("id")
        coin_name = best.get("name", symbol)
        if coin_id:
            self._dynamic_id_cache[symbol] = coin_id
            asyncio.get_event_loop().run_in_executor(
                None, self._upsert_coin_id_db, symbol, coin_id, coin_name
            )
            logger.info(f"Resolved {symbol} → {coin_id} ({coin_name})")

        return coin_id

    def _check_coin_id_db(self, symbol: str) -> Optional[str]:
        """Check Supabase crypto_coin_id_cache for a permanently cached ID."""
        try:
            from app.database import get_supabase
            sb = get_supabase()
            row = (
                sb.table("crypto_coin_id_cache")
                .select("coingecko_id")
                .eq("symbol", symbol)
                .limit(1)
                .execute()
            )
            if row.data and len(row.data) > 0:
                return row.data[0].get("coingecko_id")
        except Exception as e:
            logger.debug(f"Coin ID cache read failed for {symbol}: {e}")
        return None

    def _upsert_coin_id_db(self, symbol: str, coin_id: str, name: str) -> None:
        """Permanently cache a resolved symbol → CoinGecko ID mapping."""
        try:
            from app.database import get_supabase
            sb = get_supabase()
            sb.table("crypto_coin_id_cache").upsert(
                {"symbol": symbol, "coingecko_id": coin_id, "name": name},
                on_conflict="symbol",
            ).execute()
        except Exception as e:
            logger.debug(f"Coin ID cache write failed for {symbol}: {e}")

    # ── Public methods ──────────────────────────────────────────

    async def get_coin_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch comprehensive coin data from /coins/{id}.

        Returns market_data with: current_price, market_cap, total_volume,
        high_24h, low_24h, circulating_supply, total_supply, max_supply,
        fully_diluted_valuation, ath, atl, price_change_percentage_*.

        Also includes description, links, categories for auto-profile building.
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
