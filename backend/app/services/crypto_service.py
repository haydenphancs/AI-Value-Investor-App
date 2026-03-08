"""
Crypto Detail Service — aggregates FMP data, computes derived stats,
and generates AI-powered snapshot stories via Gemini.

Serves the CryptoDetailView screen on iOS.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client, FMPClient
from app.integrations.gemini import get_gemini_client
from app.schemas.crypto import (
    BenchmarkSummaryResponse,
    CryptoDetailResponse,
    CryptoNewsArticleResponse,
    CryptoProfileResponse,
    CryptoSnapshotResponse,
    KeyStatisticItem,
    KeyStatisticsGroupResponse,
    PerformancePeriodResponse,
    RelatedCryptoResponse,
)

logger = logging.getLogger(__name__)


# ── Static crypto profile metadata ────────────────────────────────

_CRYPTO_PROFILES: Dict[str, Dict[str, Any]] = {
    "BTC": {
        "name": "Bitcoin",
        "description": (
            "Bitcoin is the first decentralized cryptocurrency, created in 2009 by "
            "an anonymous entity known as Satoshi Nakamoto. It introduced blockchain "
            "technology as a peer-to-peer electronic cash system, enabling trustless "
            "transactions without intermediaries. Bitcoin uses a Proof-of-Work consensus "
            "mechanism and has a fixed supply cap of 21 million coins, making it a "
            "deflationary digital asset often referred to as 'digital gold.'"
        ),
        "launch_date": "January 3, 2009",
        "consensus_mechanism": "Proof of Work (PoW)",
        "blockchain": "Bitcoin",
        "website": "bitcoin.org",
        "whitepaper": "bitcoin.org/bitcoin.pdf",
        "max_supply": 21_000_000,
    },
    "ETH": {
        "name": "Ethereum",
        "description": (
            "Ethereum is the world's largest programmable blockchain and the birthplace "
            "of smart contracts, DeFi, and NFTs. Launched in 2015 by Vitalik Buterin, "
            "Ethereum allows developers to build decentralized applications. After its "
            "shift to Proof of Stake in September 2022 ('The Merge'), Ethereum cut its "
            "energy consumption by over 99% and introduced a deflationary supply mechanism "
            "that burns ETH with every transaction."
        ),
        "launch_date": "July 30, 2015",
        "consensus_mechanism": "Proof of Stake (PoS)",
        "blockchain": "Ethereum",
        "website": "ethereum.org",
        "whitepaper": "ethereum.org/en/whitepaper",
        "max_supply": None,
    },
    "SOL": {
        "name": "Solana",
        "description": (
            "Solana is a high-performance Layer 1 blockchain designed for speed and low "
            "cost. It uses a unique Proof of History consensus combined with Proof of Stake "
            "to achieve throughput of thousands of transactions per second at sub-cent fees. "
            "Founded by Anatoly Yakovenko in 2020, Solana has become a leading platform for "
            "DeFi, NFTs, and consumer-facing crypto applications."
        ),
        "launch_date": "March 16, 2020",
        "consensus_mechanism": "Proof of History + PoS",
        "blockchain": "Solana",
        "website": "solana.com",
        "whitepaper": "solana.com/solana-whitepaper.pdf",
        "max_supply": None,
    },
    "BNB": {
        "name": "BNB",
        "description": (
            "BNB is the native cryptocurrency of the BNB Chain ecosystem (formerly Binance "
            "Smart Chain). Originally launched as an ERC-20 token on Ethereum in 2017, it "
            "migrated to its own blockchain. BNB powers the Binance ecosystem including "
            "trading fee discounts, DeFi applications, and the BNB Chain which supports "
            "smart contracts with low transaction fees."
        ),
        "launch_date": "July 25, 2017",
        "consensus_mechanism": "Proof of Staked Authority (PoSA)",
        "blockchain": "BNB Chain",
        "website": "bnbchain.org",
        "whitepaper": None,
        "max_supply": 200_000_000,
    },
    "XRP": {
        "name": "XRP",
        "description": (
            "XRP is the native digital asset of the XRP Ledger, an open-source blockchain "
            "designed for fast, low-cost cross-border payments. Created by Ripple Labs, XRP "
            "settles transactions in 3-5 seconds. The XRP Ledger uses a unique consensus "
            "protocol that does not require mining, making it energy-efficient."
        ),
        "launch_date": "June 2, 2012",
        "consensus_mechanism": "XRP Ledger Consensus Protocol",
        "blockchain": "XRP Ledger",
        "website": "xrpl.org",
        "whitepaper": None,
        "max_supply": 100_000_000_000,
    },
    "ADA": {
        "name": "Cardano",
        "description": (
            "Cardano is a third-generation blockchain platform founded by Charles Hoskinson, "
            "co-founder of Ethereum. Built on peer-reviewed academic research, Cardano uses "
            "the Ouroboros Proof of Stake protocol. It emphasizes security, scalability, and "
            "sustainability with a methodical, evidence-based development approach."
        ),
        "launch_date": "September 29, 2017",
        "consensus_mechanism": "Ouroboros Proof of Stake",
        "blockchain": "Cardano",
        "website": "cardano.org",
        "whitepaper": "cardano.org/research",
        "max_supply": 45_000_000_000,
    },
    "DOGE": {
        "name": "Dogecoin",
        "description": (
            "Dogecoin started as a joke cryptocurrency in 2013 based on the Shiba Inu meme "
            "but has grown into one of the largest cryptocurrencies by market cap. It uses "
            "a Proof of Work consensus mechanism (Scrypt algorithm) and has no supply cap, "
            "with approximately 5 billion new DOGE mined per year."
        ),
        "launch_date": "December 6, 2013",
        "consensus_mechanism": "Proof of Work (Scrypt)",
        "blockchain": "Dogecoin",
        "website": "dogecoin.com",
        "whitepaper": None,
        "max_supply": None,
    },
    "AVAX": {
        "name": "Avalanche",
        "description": (
            "Avalanche is a Layer 1 blockchain that uses a novel consensus protocol to "
            "achieve high throughput and near-instant finality. Founded by Emin Gun Sirer, "
            "it supports the creation of custom subnets and is compatible with the Ethereum "
            "Virtual Machine, making it easy for developers to port Ethereum dApps."
        ),
        "launch_date": "September 21, 2020",
        "consensus_mechanism": "Avalanche Consensus (PoS)",
        "blockchain": "Avalanche",
        "website": "avax.network",
        "whitepaper": "avax.network/whitepapers",
        "max_supply": 720_000_000,
    },
    "DOT": {
        "name": "Polkadot",
        "description": (
            "Polkadot is a multi-chain protocol founded by Gavin Wood, co-founder of "
            "Ethereum and creator of the Solidity programming language. It enables "
            "different blockchains to transfer messages and value in a trust-free fashion, "
            "sharing security through its relay chain and parachain architecture."
        ),
        "launch_date": "May 26, 2020",
        "consensus_mechanism": "Nominated Proof of Stake (NPoS)",
        "blockchain": "Polkadot",
        "website": "polkadot.network",
        "whitepaper": "polkadot.network/whitepaper",
        "max_supply": None,
    },
    "LINK": {
        "name": "Chainlink",
        "description": (
            "Chainlink is a decentralized oracle network that provides real-world data to "
            "smart contracts on the blockchain. It is the industry standard for connecting "
            "blockchains to external data sources, APIs, and payment systems. LINK is used "
            "to pay node operators for retrieving and delivering data."
        ),
        "launch_date": "September 19, 2017",
        "consensus_mechanism": "Decentralized Oracle Network",
        "blockchain": "Ethereum (ERC-20)",
        "website": "chain.link",
        "whitepaper": "chain.link/whitepaper",
        "max_supply": 1_000_000_000,
    },
    "MATIC": {
        "name": "Polygon",
        "description": (
            "Polygon (formerly Matic Network) is an Ethereum Layer 2 scaling solution that "
            "provides faster and cheaper transactions. It uses a Proof of Stake sidechain "
            "and is one of the most widely adopted scaling solutions in crypto, supporting "
            "thousands of dApps across DeFi, gaming, and NFTs."
        ),
        "launch_date": "April 26, 2019",
        "consensus_mechanism": "Proof of Stake (PoS)",
        "blockchain": "Polygon / Ethereum L2",
        "website": "polygon.technology",
        "whitepaper": "polygon.technology/papers",
        "max_supply": 10_000_000_000,
    },
    "ARB": {
        "name": "Arbitrum",
        "description": (
            "Arbitrum is an Ethereum Layer 2 scaling solution using Optimistic Rollup "
            "technology. Built by Offchain Labs, it inherits Ethereum's security while "
            "providing significantly lower transaction costs and higher throughput. It has "
            "become the largest L2 by total value locked."
        ),
        "launch_date": "August 31, 2021",
        "consensus_mechanism": "Optimistic Rollup (Ethereum L2)",
        "blockchain": "Arbitrum / Ethereum L2",
        "website": "arbitrum.io",
        "whitepaper": None,
        "max_supply": 10_000_000_000,
    },
    "OP": {
        "name": "Optimism",
        "description": (
            "Optimism is an Ethereum Layer 2 scaling solution using Optimistic Rollup "
            "technology. It is governed by the Optimism Collective and powers the OP Stack, "
            "a modular framework for building L2 chains (the Superchain vision). Coinbase's "
            "Base chain is built on the OP Stack."
        ),
        "launch_date": "December 16, 2021",
        "consensus_mechanism": "Optimistic Rollup (Ethereum L2)",
        "blockchain": "Optimism / Ethereum L2",
        "website": "optimism.io",
        "whitepaper": None,
        "max_supply": None,
    },
}

# ── Related crypto mappings ──────────────────────────────────────

_RELATED_CRYPTOS: Dict[str, List[str]] = {
    "BTC": ["ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"],
    "ETH": ["BTC", "SOL", "BNB", "MATIC", "ARB", "OP"],
    "SOL": ["ETH", "AVAX", "ADA", "DOT", "ARB", "OP"],
    "BNB": ["ETH", "SOL", "AVAX", "XRP", "ADA", "DOGE"],
    "XRP": ["BTC", "ADA", "DOT", "LINK", "BNB", "SOL"],
    "ADA": ["ETH", "SOL", "DOT", "AVAX", "XRP", "LINK"],
    "DOGE": ["BTC", "BNB", "XRP", "SOL", "ETH", "ADA"],
    "AVAX": ["SOL", "ETH", "DOT", "ADA", "ARB", "OP"],
    "DOT": ["ETH", "ADA", "AVAX", "LINK", "SOL", "ARB"],
    "LINK": ["ETH", "DOT", "ARB", "OP", "SOL", "AVAX"],
    "MATIC": ["ETH", "ARB", "OP", "SOL", "AVAX", "BNB"],
    "ARB": ["OP", "ETH", "MATIC", "SOL", "AVAX", "BNB"],
    "OP": ["ARB", "ETH", "MATIC", "SOL", "AVAX", "BNB"],
}

# Default related if symbol not in map
_DEFAULT_RELATED = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA"]


# ── Simple in-memory cache ───────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 120  # 2 minutes for crypto (24/7 market)
_AI_CACHE_TTL_SECONDS = 1800  # 30 minutes for AI-generated stories


def _cache_get(key: str, ttl: Optional[float] = None) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    max_age = ttl or _CACHE_TTL_SECONDS
    if time.time() - ts > max_age:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any):
    _cache[key] = (time.time(), value)


# ── Formatting helpers ───────────────────────────────────────────


def _fmt(value: Optional[float], decimals: int = 2) -> str:
    """Format a number with commas and N decimal places."""
    if value is None:
        return "—"
    if abs(value) >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs(value) >= 1:
        return f"${value:,.{decimals}f}"
    return f"${value:.6f}"


def _fmt_supply(value: Optional[float], symbol: str = "") -> str:
    """Format supply numbers."""
    if value is None:
        return "—"
    suffix = f" {symbol}" if symbol else ""
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B{suffix}"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M{suffix}"
    return f"{value:,.0f}{suffix}"


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _compute_return(prices: List[Dict], days_back: int) -> Optional[float]:
    """Compute % return over the last N trading days."""
    if not prices or len(prices) < 2:
        return None
    if len(prices) <= days_back:
        start = prices[0].get("close") or prices[0].get("adjClose")
        end = prices[-1].get("close") or prices[-1].get("adjClose")
    else:
        start = prices[-(days_back + 1)].get("close") or prices[-(days_back + 1)].get("adjClose")
        end = prices[-1].get("close") or prices[-1].get("adjClose")
    if not start or not end or start == 0:
        return None
    return ((end - start) / start) * 100


def _compute_ytd_return(prices: List[Dict]) -> Optional[float]:
    if not prices or len(prices) < 2:
        return None
    current_year = datetime.now(tz=timezone.utc).year
    for p in prices:
        date_str = p.get("date", "")
        if date_str.startswith(str(current_year)):
            start_price = p.get("close") or p.get("adjClose")
            end_price = prices[-1].get("close") or prices[-1].get("adjClose")
            if start_price and end_price and start_price > 0:
                return ((end_price - start_price) / start_price) * 100
            break
    return None


# ── Main service ─────────────────────────────────────────────────


class CryptoService:
    """Aggregates FMP data + Gemini AI for the Crypto Detail screen."""

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()

    async def get_crypto_detail(
        self, symbol: str, chart_range: str = "3M", interval: str = None
    ) -> CryptoDetailResponse:
        """
        Fetch and assemble complete crypto detail data.

        Steps:
          1. Fetch FMP quote, historical prices, news, related quotes in parallel
          2. Compute key statistics and performance periods
          3. Build AI-enhanced snapshots via Gemini
          4. Assemble and return
        """
        symbol = symbol.upper()
        fmp_symbol = f"{symbol}USD"

        # Resolve profile metadata
        profile_meta = _CRYPTO_PROFILES.get(symbol, {})
        crypto_name = profile_meta.get("name", symbol)

        # ── Step 1: Parallel FMP fetches ──────────────────────────
        today = datetime.now(tz=timezone.utc).date()
        from_date = (today - timedelta(days=365 * 2)).isoformat()
        to_date = today.isoformat()

        # Related crypto symbols
        related_symbols = _RELATED_CRYPTOS.get(symbol, _DEFAULT_RELATED)
        related_symbols = [s for s in related_symbols if s != symbol][:6]
        related_fmp_symbols = [f"{s}USD" for s in related_symbols]

        quote_task = self.fmp.get_stock_price_quote(fmp_symbol)
        hist_task = self.fmp.get_historical_prices(fmp_symbol, from_date, to_date)
        news_task = self.fmp.get_stock_news(fmp_symbol, limit=10)
        related_task = self.fmp.get_batch_quotes(related_fmp_symbols)

        quote, hist_raw, news_raw, related_raw = await asyncio.gather(
            quote_task, hist_task, news_task, related_task,
            return_exceptions=True,
        )

        # Handle exceptions gracefully
        if isinstance(quote, Exception):
            logger.error(f"Crypto quote fetch failed for {fmp_symbol}: {quote}")
            quote = {}
        if isinstance(hist_raw, Exception):
            logger.error(f"Crypto historical fetch failed for {fmp_symbol}: {hist_raw}")
            hist_raw = {}
        if isinstance(news_raw, Exception):
            logger.error(f"Crypto news fetch failed for {fmp_symbol}: {news_raw}")
            news_raw = []
        if isinstance(related_raw, Exception):
            logger.error(f"Related crypto fetch failed: {related_raw}")
            related_raw = []

        # Parse historical prices (sorted oldest-first)
        historical = []
        if isinstance(hist_raw, dict):
            historical = hist_raw.get("historical", [])
        elif isinstance(hist_raw, list):
            historical = hist_raw
        historical.sort(key=lambda p: p.get("date", ""))

        # ── Step 2: Extract quote data ────────────────────────────
        price = quote.get("price") or 0
        change = quote.get("change") or 0
        change_pct = quote.get("changesPercentage") or 0
        day_high = quote.get("dayHigh") or 0
        day_low = quote.get("dayLow") or 0
        year_high = quote.get("yearHigh") or 0
        year_low = quote.get("yearLow") or 0
        volume = quote.get("volume") or 0
        avg_volume = quote.get("avgVolume") or 0
        market_cap = quote.get("marketCap") or 0
        open_price = quote.get("open") or 0
        prev_close = quote.get("previousClose") or 0
        shares_outstanding = quote.get("sharesOutstanding") or 0

        # ── Step 3: Compute derived stats ─────────────────────────
        ytd_return = _compute_ytd_return(historical)
        seven_day_return = _compute_return(historical, 7)
        one_month_return = _compute_return(historical, 30)
        one_year_return = _compute_return(historical, 365)
        three_year_return = (
            _compute_return(historical, 365 * 3)
            if len(historical) > 365 * 3
            else None
        )

        # ── Step 4: Build chart data ──────────────────────────────
        from app.services.chart_helper import fetch_chart_data, resolve_interval
        resolved = resolve_interval(chart_range, interval)
        if resolved != "daily" or chart_range == "ALL":
            chart_data = await fetch_chart_data(self.fmp, fmp_symbol, chart_range, interval)
        else:
            chart_data = self._extract_chart_data(historical, chart_range)

        # ── Step 5: Build key statistics ──────────────────────────
        max_supply = profile_meta.get("max_supply")
        key_stats = self._build_key_statistics(
            price=price,
            market_cap=market_cap,
            volume=volume,
            avg_volume=avg_volume,
            day_high=day_high,
            day_low=day_low,
            year_high=year_high,
            year_low=year_low,
            shares_outstanding=shares_outstanding,
            max_supply=max_supply,
            symbol=symbol,
        )

        # ── Step 6: Build performance periods ─────────────────────
        perf_periods = self._build_performance_periods(
            seven_day=seven_day_return,
            one_month=one_month_return,
            ytd=ytd_return,
            one_year=one_year_return,
            three_year=three_year_return,
        )

        # ── Step 7: Build snapshots (AI-enhanced) ─────────────────
        snapshots = await self._build_snapshots(
            symbol=symbol,
            crypto_name=crypto_name,
            price=price,
            market_cap=market_cap,
            volume=volume,
            change_pct=change_pct,
            profile_meta=profile_meta,
        )

        # ── Step 8: Build profile ─────────────────────────────────
        crypto_profile = CryptoProfileResponse(
            description=profile_meta.get("description", f"{crypto_name} is a cryptocurrency."),
            symbol=symbol,
            launch_date=profile_meta.get("launch_date", "Unknown"),
            consensus_mechanism=profile_meta.get("consensus_mechanism", "Unknown"),
            blockchain=profile_meta.get("blockchain", symbol),
            website=profile_meta.get("website", ""),
            whitepaper=profile_meta.get("whitepaper"),
        )

        # ── Step 9: Build related cryptos ─────────────────────────
        related_cryptos = self._build_related_cryptos(
            related_raw if isinstance(related_raw, list) else [],
            related_symbols,
        )

        # ── Step 10: Build news ───────────────────────────────────
        news_articles = self._build_news(
            news_raw if isinstance(news_raw, list) else []
        )

        # ── Step 11: Build benchmark summary ──────────────────────
        benchmark = BenchmarkSummaryResponse(
            avg_annual_return=round(one_year_return, 1) if one_year_return else 0,
            sp_benchmark=0,  # Will compare vs BTC
            benchmark_name="Bitcoin (BTC)" if symbol != "BTC" else "S&P 500",
            since_date=profile_meta.get("launch_date", "")[:8] if profile_meta.get("launch_date") else None,
        )

        return CryptoDetailResponse(
            symbol=symbol,
            name=crypto_name,
            current_price=price,
            price_change=change,
            price_change_percent=change_pct,
            market_status="24/7 Trading",
            chart_data=chart_data,
            key_statistics_groups=key_stats,
            performance_periods=perf_periods,
            snapshots=snapshots,
            crypto_profile=crypto_profile,
            related_cryptos=related_cryptos,
            benchmark_summary=benchmark,
            news_articles=news_articles,
        )

    # ── Chart helpers ────────────────────────────────────────────

    def _extract_chart_data(
        self, historical: List[Dict], chart_range: str
    ) -> List[Dict]:
        if not historical:
            return []

        today = datetime.now(tz=timezone.utc).date()
        range_days = {
            "1D": 2, "1W": 7, "3M": 90, "6M": 180,
            "1Y": 365, "5Y": 365 * 5, "ALL": 99999,
        }
        days = range_days.get(chart_range, 90)
        cutoff = (today - timedelta(days=days)).isoformat()

        result = []
        for p in historical:
            if p.get("date", "") >= cutoff:
                close = p.get("close") or p.get("adjClose")
                if close and close > 0:
                    result.append({
                        "date": p.get("date"),
                        "open": p.get("open"),
                        "high": p.get("high"),
                        "low": p.get("low"),
                        "close": round(float(close), 2),
                        "volume": p.get("volume"),
                    })
        return result

    # ── Key statistics builder ───────────────────────────────────

    def _build_key_statistics(
        self, *, price, market_cap, volume, avg_volume,
        day_high, day_low, year_high, year_low,
        shares_outstanding, max_supply, symbol,
    ) -> List[KeyStatisticsGroupResponse]:
        circulating = shares_outstanding
        fdv = price * max_supply if max_supply and price else market_cap

        # Dominance approximation — we don't have total crypto market cap
        # from FMP, so we leave it as computed if market_cap > 0
        vol_mkt_ratio = (volume / market_cap * 100) if market_cap > 0 else 0

        return [
            # Column 1: Price & Volume
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="Market Cap", value=_fmt(market_cap)),
                KeyStatisticItem(label="24h Volume", value=_fmt(volume)),
                KeyStatisticItem(label="Volume/Mkt Cap", value=f"{vol_mkt_ratio:.2f}%"),
                KeyStatisticItem(label="24h High", value=_fmt(day_high)),
                KeyStatisticItem(label="24h Low", value=_fmt(day_low)),
            ]),
            # Column 2: Supply
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(
                    label="Circulating Supply",
                    value=_fmt_supply(circulating, symbol),
                ),
                KeyStatisticItem(
                    label="Total Supply",
                    value=_fmt_supply(circulating, symbol),
                ),
                KeyStatisticItem(
                    label="Max Supply",
                    value=_fmt_supply(max_supply, symbol) if max_supply else "No Cap",
                ),
                KeyStatisticItem(
                    label="Fully Diluted Val.",
                    value=_fmt(fdv),
                ),
                KeyStatisticItem(label="Avg. Volume (30D)", value=_fmt(avg_volume)),
            ]),
            # Column 3: Historical (52-week from FMP)
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="52-Week High", value=_fmt(year_high)),
                KeyStatisticItem(
                    label="From 52W High",
                    value=_pct(((price - year_high) / year_high * 100) if year_high > 0 else None),
                ),
                KeyStatisticItem(label="52-Week Low", value=_fmt(year_low)),
                KeyStatisticItem(
                    label="From 52W Low",
                    value=_pct(((price - year_low) / year_low * 100) if year_low > 0 else None),
                    is_highlighted=True,
                ),
                KeyStatisticItem(label="52-Week Range", value=f"{_fmt(year_low)} - {_fmt(year_high)}"),
            ]),
        ]

    # ── Performance periods builder ──────────────────────────────

    def _build_performance_periods(
        self, *, seven_day, one_month, ytd, one_year, three_year,
    ) -> List[PerformancePeriodResponse]:
        periods = []
        for label, val in [
            ("7 Days", seven_day),
            ("1 Month", one_month),
            ("YTD", ytd),
            ("1 Year", one_year),
            ("3 Years", three_year),
        ]:
            periods.append(PerformancePeriodResponse(
                label=label,
                change_percent=round(val, 2) if val is not None else 0,
                vs_market_percent=round(val, 2) if val is not None else 0,
                benchmark_label="BTC",
            ))
        return periods

    # ── Snapshots builder (with Gemini AI) ───────────────────────

    async def _build_snapshots(
        self, *, symbol, crypto_name, price, market_cap, volume,
        change_pct, profile_meta,
    ) -> List[CryptoSnapshotResponse]:
        """Generate AI-powered snapshots for the 4 categories."""

        cache_key = f"crypto_snapshots_{symbol}"
        cached = _cache_get(cache_key, _AI_CACHE_TTL_SECONDS)
        if cached:
            return cached

        # Default template snapshots
        defaults = self._default_snapshots(symbol, crypto_name, profile_meta)

        try:
            gemini = get_gemini_client()

            prompt = f"""You are a cryptocurrency analyst writing educational content about {crypto_name} ({symbol}).

Current data:
- Price: ${price:,.2f}
- Market Cap: ${market_cap:,.0f}
- 24h Volume: ${volume:,.0f}
- 24h Change: {change_pct:+.2f}%
- Consensus: {profile_meta.get('consensus_mechanism', 'Unknown')}
- Launch: {profile_meta.get('launch_date', 'Unknown')}
- Max Supply: {profile_meta.get('max_supply', 'No cap')}

Generate content for these 4 categories. For each, write exactly 3 paragraphs (2-4 sentences each). Write in a confident, conversational tone that educates without jargon. Be specific and mention real names, dates, and numbers.

Separate each category with "===CATEGORY===" followed by the category name.

===CATEGORY===Origin and Technology
[3 paragraphs about founding team, technology, consensus, key innovations]

===CATEGORY===Tokenomics
[3 paragraphs about supply mechanics, fees/burns, staking, revenue model]

===CATEGORY===Next Big Moves
[3 paragraphs about upcoming upgrades, institutional adoption, catalysts]

===CATEGORY===Risks
[3 paragraphs about regulatory, technical, competition risks]"""

            ai_response = await gemini.generate_text(
                prompt=prompt,
                system_instruction=(
                    "You are a senior crypto analyst providing educational, balanced commentary. "
                    "Write factually. Each paragraph should be 2-4 sentences. "
                    "Avoid hype. Mention specific names, dates, and data points."
                ),
                model_name="gemini-2.0-flash",
            )

            text = ai_response.get("text", "")
            snapshots = self._parse_ai_snapshots(text)

            if len(snapshots) == 4:
                _cache_set(cache_key, snapshots)
                logger.info(f"Generated AI snapshots for {symbol}")
                return snapshots

        except Exception as e:
            logger.warning(f"Gemini snapshot generation failed for {symbol}, using defaults: {e}")

        _cache_set(cache_key, defaults)
        return defaults

    def _parse_ai_snapshots(self, text: str) -> List[CryptoSnapshotResponse]:
        """Parse Gemini's response into structured snapshots."""
        categories = [
            "Origin and Technology",
            "Tokenomics",
            "Next Big Moves",
            "Risks",
        ]
        snapshots = []

        parts = text.split("===CATEGORY===")
        for part in parts:
            part = part.strip()
            if not part:
                continue

            matched_category = None
            for cat in categories:
                if part.lower().startswith(cat.lower()):
                    matched_category = cat
                    part = part[len(cat):].strip()
                    break

            if not matched_category:
                continue

            # Split into paragraphs (non-empty lines separated by blank lines)
            raw_paragraphs = [p.strip() for p in part.split("\n\n") if p.strip()]
            # Clean up — remove numbered prefixes like "1." or "**1.**"
            paragraphs = []
            for p in raw_paragraphs:
                cleaned = p.lstrip("0123456789.-) ").strip()
                cleaned = cleaned.replace("**", "").strip()
                if len(cleaned) > 20:
                    paragraphs.append(cleaned)

            if paragraphs:
                snapshots.append(CryptoSnapshotResponse(
                    category=matched_category,
                    paragraphs=paragraphs[:3],
                ))

        return snapshots

    def _default_snapshots(
        self, symbol: str, name: str, profile: Dict
    ) -> List[CryptoSnapshotResponse]:
        """Fallback template snapshots when AI is unavailable."""
        consensus = profile.get("consensus_mechanism", "its consensus mechanism")
        launch = profile.get("launch_date", "its launch")
        blockchain = profile.get("blockchain", symbol)

        return [
            CryptoSnapshotResponse(
                category="Origin and Technology",
                paragraphs=[
                    f"{name} is built on the {blockchain} blockchain using {consensus}.",
                    f"Launched on {launch}, it has grown into one of the most recognized digital assets in the cryptocurrency ecosystem.",
                    "The underlying technology continues to evolve through community-driven development and protocol upgrades.",
                ],
            ),
            CryptoSnapshotResponse(
                category="Tokenomics",
                paragraphs=[
                    f"{name} ({symbol}) operates with a defined token supply model that governs issuance and distribution.",
                    "Transaction fees contribute to the economic model, with mechanisms in place to manage supply dynamics over time.",
                    "Staking and network participation provide additional economic incentives for holders.",
                ],
            ),
            CryptoSnapshotResponse(
                category="Next Big Moves",
                paragraphs=[
                    f"The {name} ecosystem continues to expand with upcoming protocol upgrades and partnerships.",
                    "Institutional adoption and ETF discussions could serve as significant catalysts for price discovery.",
                    "Ecosystem growth in DeFi, NFTs, and real-world asset tokenization represents ongoing opportunities.",
                ],
            ),
            CryptoSnapshotResponse(
                category="Risks",
                paragraphs=[
                    "Regulatory uncertainty remains the primary risk, with evolving legislation across major jurisdictions.",
                    "Technical risks include smart contract vulnerabilities, centralization concerns, and network security.",
                    "Competition from alternative chains and protocols could impact market share and adoption over time.",
                ],
            ),
        ]

    # ── Related cryptos builder ──────────────────────────────────

    def _build_related_cryptos(
        self, raw_quotes: List[Dict], expected_symbols: List[str],
    ) -> List[RelatedCryptoResponse]:
        """Build related crypto list from batch FMP quotes."""
        # Create lookup by FMP symbol
        quote_map: Dict[str, Dict] = {}
        for q in raw_quotes:
            fmp_sym = (q.get("symbol") or "").upper()
            quote_map[fmp_sym] = q

        result = []
        for sym in expected_symbols:
            fmp_sym = f"{sym}USD"
            q = quote_map.get(fmp_sym, {})
            name = _CRYPTO_PROFILES.get(sym, {}).get("name", sym)
            result.append(RelatedCryptoResponse(
                symbol=sym,
                name=name,
                price=q.get("price") or 0,
                change_percent=q.get("changesPercentage") or 0,
            ))

        return result

    # ── News builder ─────────────────────────────────────────────

    def _build_news(
        self, raw_articles: List[Dict],
    ) -> List[CryptoNewsArticleResponse]:
        articles = []
        for item in raw_articles[:10]:
            published = item.get("publishedDate") or item.get("published_date") or ""
            articles.append(CryptoNewsArticleResponse(
                headline=item.get("title") or item.get("headline") or "",
                source_name=item.get("site") or item.get("source") or "Unknown",
                source_icon=None,
                sentiment="neutral",
                published_at=published,
                thumbnail_url=item.get("image") or item.get("thumbnail_url"),
                related_tickers=[
                    s.strip()
                    for s in (item.get("symbol") or "").split(",")
                    if s.strip()
                ],
                summary_bullets=[],
                article_url=item.get("url") or item.get("article_url"),
            ))
        return articles


# ── Singleton ────────────────────────────────────────────────────

_crypto_service: Optional[CryptoService] = None


def get_crypto_service() -> CryptoService:
    global _crypto_service
    if _crypto_service is None:
        _crypto_service = CryptoService()
    return _crypto_service
