"""
Crypto Detail Service — aggregates CoinGecko + FMP data, computes derived stats,
and generates AI-powered snapshot stories via Gemini.

Data sources:
  - CoinGecko: key statistics (supply, volume, FDV, market cap, ATH/ATL)
  - FMP: chart/intraday data, news, related crypto quotes
  - Gemini: AI-powered snapshot stories

Serves the CryptoDetailView screen on iOS.
"""

import asyncio
import copy
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.database import get_supabase
from app.services.agents.persona_config import neutral_system_instruction
from app.integrations.coingecko import get_coingecko_client, CoinGeckoClient
from app.integrations.fmp import get_fmp_client, FMPClient
from app.integrations.gemini import get_gemini_client
from app.services.benchmark_math import format_since, overlapping_cagrs
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
from app.utils.market_hours import to_utc_instant
from app.services.price_service import price_source

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
    "TRX": {
        "name": "TRON",
        "description": (
            "TRON is a blockchain platform founded by Justin Sun in 2017, focused on "
            "decentralizing the internet and building infrastructure for a decentralized web. "
            "It uses a Delegated Proof of Stake consensus mechanism and is known for extremely "
            "low transaction fees. TRON has become the dominant network for USDT stablecoin "
            "transfers, processing more stablecoin volume than any other blockchain."
        ),
        "launch_date": "September 2017",
        "consensus_mechanism": "Delegated Proof of Stake (DPoS)",
        "blockchain": "TRON",
        "website": "tron.network",
        "whitepaper": "tron.network/static/doc/white_paper_v_2_0.pdf",
        "max_supply": None,
    },
    "TON": {
        "name": "Toncoin",
        "description": (
            "Toncoin is the native cryptocurrency of The Open Network (TON), a Layer 1 "
            "blockchain originally designed by Telegram. After Telegram stepped back due to "
            "SEC issues, the TON Foundation took over development. TON is deeply integrated "
            "with Telegram's 900M+ user base through in-app wallets, mini-apps, and payments, "
            "making it one of the most accessible blockchains for mainstream users."
        ),
        "launch_date": "November 2021",
        "consensus_mechanism": "Proof of Stake (PoS)",
        "blockchain": "The Open Network (TON)",
        "website": "ton.org",
        "whitepaper": "ton.org/whitepaper.pdf",
        "max_supply": None,
    },
    "SHIB": {
        "name": "Shiba Inu",
        "description": (
            "Shiba Inu is an Ethereum-based meme token that launched in August 2020 as a "
            "community-driven alternative to Dogecoin. It has evolved beyond its meme origins "
            "with the launch of Shibarium (its own Layer 2 network), ShibaSwap DEX, and an "
            "expanding ecosystem of tokens (LEASH, BONE). SHIB has one of the largest crypto "
            "communities and has been listed on most major exchanges."
        ),
        "launch_date": "August 2020",
        "consensus_mechanism": "ERC-20 (Ethereum PoS)",
        "blockchain": "Ethereum / Shibarium L2",
        "website": "shibatoken.com",
        "whitepaper": None,
        "max_supply": None,
    },
    "SUI": {
        "name": "Sui",
        "description": (
            "Sui is a Layer 1 blockchain built by Mysten Labs, founded by former Meta (Diem) "
            "engineers. It uses the Move programming language and a novel object-centric data "
            "model that enables parallel transaction processing. Sui achieves high throughput "
            "with sub-second finality and is designed for consumer-facing applications, gaming, "
            "and DeFi with a focus on developer experience."
        ),
        "launch_date": "May 3, 2023",
        "consensus_mechanism": "Delegated Proof of Stake (Mysticeti)",
        "blockchain": "Sui",
        "website": "sui.io",
        "whitepaper": "docs.sui.io/paper/sui.pdf",
        "max_supply": 10_000_000_000,
    },
    "NEAR": {
        "name": "NEAR Protocol",
        "description": (
            "NEAR Protocol is a Layer 1 blockchain designed for usability with human-readable "
            "account names and a unique sharding architecture called Nightshade. Founded by "
            "Alex Skidanov and Illia Polosukhin (co-author of the 'Attention Is All You Need' "
            "transformer paper), NEAR focuses on being developer-friendly with its JavaScript SDK "
            "and has become a hub for AI x crypto projects."
        ),
        "launch_date": "April 22, 2020",
        "consensus_mechanism": "Proof of Stake (Nightshade Sharding)",
        "blockchain": "NEAR",
        "website": "near.org",
        "whitepaper": "near.org/papers/the-official-near-white-paper",
        "max_supply": None,
    },
    "UNI": {
        "name": "Uniswap",
        "description": (
            "Uniswap is the largest decentralized exchange (DEX) by volume, pioneering the "
            "automated market maker (AMM) model that eliminated the need for order books. "
            "Launched in 2018 by Hayden Adams, Uniswap V3 introduced concentrated liquidity "
            "positions. The UNI governance token gives holders voting power over protocol "
            "parameters and the community treasury worth billions."
        ),
        "launch_date": "November 2, 2018",
        "consensus_mechanism": "ERC-20 (Ethereum PoS)",
        "blockchain": "Ethereum / Multi-chain",
        "website": "uniswap.org",
        "whitepaper": "uniswap.org/whitepaper-v3.pdf",
        "max_supply": 1_000_000_000,
    },
    "APT": {
        "name": "Aptos",
        "description": (
            "Aptos is a Layer 1 blockchain built by former Meta (Diem/Libra) engineers using "
            "the Move programming language. It uses a novel parallel execution engine (Block-STM) "
            "that enables high throughput by processing transactions concurrently. Aptos focuses "
            "on safety, scalability, and upgradeability with a modular architecture."
        ),
        "launch_date": "October 17, 2022",
        "consensus_mechanism": "Proof of Stake (AptosBFT)",
        "blockchain": "Aptos",
        "website": "aptoslabs.com",
        "whitepaper": "aptos.dev/assets/files/Aptos-Whitepaper.pdf",
        "max_supply": None,
    },
}

# ── Related crypto mappings ──────────────────────────────────────

_RELATED_CRYPTOS: Dict[str, List[str]] = {
    # Layer 1 majors
    "BTC": ["ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"],
    "ETH": ["BTC", "SOL", "BNB", "ARB", "OP", "MATIC"],
    "SOL": ["ETH", "SUI", "AVAX", "APT", "NEAR", "ADA"],
    "BNB": ["ETH", "SOL", "AVAX", "XRP", "CRO", "OKB"],
    "XRP": ["BTC", "XLM", "ADA", "DOT", "LINK", "ALGO"],
    "ADA": ["ETH", "SOL", "DOT", "AVAX", "XRP", "ALGO"],
    "TRX": ["BNB", "SOL", "ETH", "XRP", "ADA", "EOS"],
    "AVAX": ["SOL", "ETH", "DOT", "NEAR", "SUI", "FTM"],
    "DOT": ["ETH", "ADA", "ATOM", "AVAX", "LINK", "NEAR"],
    "TON": ["ETH", "SOL", "SUI", "NEAR", "BNB", "TRX"],
    "SUI": ["SOL", "APT", "NEAR", "AVAX", "ETH", "SEI"],
    "NEAR": ["SOL", "SUI", "APT", "AVAX", "ETH", "FET"],
    "APT": ["SUI", "SOL", "NEAR", "AVAX", "ETH", "SEI"],
    "HBAR": ["XRP", "XLM", "ALGO", "VET", "ADA", "DOT"],
    # Layer 2
    "MATIC": ["ETH", "ARB", "OP", "STRK", "ZK", "IMX"],
    "POL": ["ETH", "ARB", "OP", "STRK", "ZK", "IMX"],
    "ARB": ["OP", "ETH", "MATIC", "STRK", "ZK", "IMX"],
    "OP": ["ARB", "ETH", "MATIC", "STRK", "ZK", "IMX"],
    "STRK": ["ARB", "OP", "ZK", "MATIC", "ETH", "IMX"],
    "ZK": ["STRK", "ARB", "OP", "MATIC", "ETH", "IMX"],
    "IMX": ["ARB", "OP", "MATIC", "GALA", "AXS", "SAND"],
    "MNT": ["ARB", "OP", "ETH", "MATIC", "STRK", "ZK"],
    # DeFi
    "UNI": ["AAVE", "SUSHI", "CRV", "CAKE", "1INCH", "COMP"],
    "AAVE": ["UNI", "COMP", "CRV", "LDO", "PENDLE", "SNX"],
    "LINK": ["ETH", "DOT", "GRT", "PYTH", "FET", "RENDER"],
    "LDO": ["AAVE", "ETHFI", "PENDLE", "ETH", "COMP", "CRV"],
    "CRV": ["UNI", "AAVE", "SNX", "BAL", "SUSHI", "COMP"],
    "PENDLE": ["AAVE", "LDO", "ETHFI", "COMP", "CRV", "UNI"],
    "COMP": ["AAVE", "UNI", "CRV", "SNX", "LDO", "PENDLE"],
    "SNX": ["CRV", "COMP", "AAVE", "UNI", "DYDX", "GMX"],
    "SUSHI": ["UNI", "CAKE", "1INCH", "CRV", "BAL", "AAVE"],
    "BAL": ["CRV", "UNI", "SUSHI", "AAVE", "1INCH", "COMP"],
    "CAKE": ["UNI", "SUSHI", "1INCH", "BNB", "CRV", "AAVE"],
    "1INCH": ["UNI", "SUSHI", "CAKE", "CRV", "BAL", "AAVE"],
    "DYDX": ["GMX", "SNX", "INJ", "JUP", "UNI", "AAVE"],
    "GMX": ["DYDX", "SNX", "INJ", "JUP", "ARB", "UNI"],
    "JUP": ["SOL", "BONK", "PYTH", "WIF", "RENDER", "DYDX"],
    "ETHFI": ["LDO", "PENDLE", "AAVE", "ETH", "EIGEN", "COMP"],
    "ONDO": ["AAVE", "PENDLE", "ETHFI", "ETH", "LINK", "UNI"],
    # AI / Compute
    "RENDER": ["FET", "TAO", "NEAR", "LINK", "GRT", "THETA"],
    "FET": ["RENDER", "TAO", "NEAR", "LINK", "GRT", "THETA"],
    "TAO": ["RENDER", "FET", "NEAR", "LINK", "GRT", "THETA"],
    "GRT": ["LINK", "FET", "RENDER", "THETA", "PYTH", "TAO"],
    "THETA": ["RENDER", "FET", "GRT", "TAO", "LINK", "FIL"],
    # Meme coins
    "DOGE": ["SHIB", "PEPE", "BONK", "FLOKI", "WIF", "BTC"],
    "SHIB": ["DOGE", "PEPE", "BONK", "FLOKI", "WIF", "ETH"],
    "PEPE": ["DOGE", "SHIB", "BONK", "FLOKI", "WIF", "ETH"],
    "BONK": ["SOL", "DOGE", "SHIB", "PEPE", "WIF", "FLOKI"],
    "FLOKI": ["DOGE", "SHIB", "PEPE", "BONK", "WIF", "BNB"],
    "WIF": ["SOL", "BONK", "DOGE", "PEPE", "SHIB", "FLOKI"],
    "TRUMP": ["DOGE", "PEPE", "SHIB", "BONK", "WIF", "SOL"],
    # Gaming / Metaverse
    "AXS": ["GALA", "SAND", "MANA", "IMX", "FLOW", "BEAM"],
    "SAND": ["MANA", "AXS", "GALA", "IMX", "FLOW", "BEAM"],
    "MANA": ["SAND", "AXS", "GALA", "IMX", "FLOW", "ENS"],
    "GALA": ["AXS", "SAND", "MANA", "IMX", "BEAM", "FLOW"],
    "BEAM": ["GALA", "AXS", "SAND", "IMX", "MANA", "FLOW"],
    "FLOW": ["AXS", "GALA", "SAND", "MANA", "IMX", "BEAM"],
    # Infrastructure
    "ATOM": ["DOT", "AVAX", "TIA", "INJ", "SEI", "NEAR"],
    "FIL": ["RENDER", "AR", "THETA", "GRT", "LINK", "STX"],
    "STX": ["BTC", "NEAR", "ICP", "FIL", "RENDER", "LINK"],
    "ICP": ["ETH", "NEAR", "DOT", "FIL", "ATOM", "STX"],
    "VET": ["HBAR", "XRP", "ALGO", "XDC", "LINK", "DOT"],
    "ALGO": ["HBAR", "XRP", "XLM", "ADA", "VET", "DOT"],
    "XLM": ["XRP", "ALGO", "HBAR", "ADA", "VET", "DOT"],
    "INJ": ["SEI", "DYDX", "ATOM", "GMX", "SUI", "SOL"],
    "SEI": ["SUI", "INJ", "APT", "NEAR", "SOL", "ATOM"],
    "TIA": ["ATOM", "DOT", "EIGEN", "NEAR", "AVAX", "SOL"],
    "PYTH": ["LINK", "GRT", "SOL", "JUP", "RENDER", "FET"],
    "KAS": ["BTC", "LTC", "BCH", "XMR", "DASH", "ZEC"],
    # Privacy / PoW
    "LTC": ["BTC", "BCH", "DOGE", "XMR", "DASH", "ZEC"],
    "BCH": ["BTC", "LTC", "XMR", "DASH", "ZEC", "ETC"],
    "XMR": ["ZEC", "DASH", "BTC", "LTC", "BCH", "DCR"],
    "ZEC": ["XMR", "DASH", "BTC", "LTC", "BCH", "DCR"],
    "DASH": ["LTC", "BCH", "XMR", "ZEC", "BTC", "DCR"],
    "DCR": ["XMR", "ZEC", "DASH", "LTC", "BTC", "ATOM"],
    "ETC": ["ETH", "BTC", "BCH", "LTC", "DOT", "ADA"],
    # Other
    "EOS": ["TRX", "NEO", "XTZ", "ADA", "ETH", "DOT"],
    "NEO": ["EOS", "XTZ", "ETH", "DOT", "ADA", "IOTA"],
    "XTZ": ["EOS", "NEO", "ADA", "ALGO", "DOT", "ATOM"],
    "IOTA": ["VET", "HBAR", "XLM", "DOT", "ADA", "ALGO"],
    "ENS": ["UNI", "AAVE", "ETH", "LINK", "MANA", "SAND"],
    "CHZ": ["GALA", "AXS", "FLOW", "IMX", "SAND", "ENS"],
    "QNT": ["LINK", "DOT", "ATOM", "HBAR", "VET", "XDC"],
    "ROSE": ["DOT", "NEAR", "AVAX", "ATOM", "FIL", "RENDER"],
    "ONE": ["DOT", "NEAR", "AVAX", "ATOM", "ADA", "SOL"],
    "CELO": ["NEAR", "ALGO", "XLM", "ADA", "DOT", "HBAR"],
    "CFX": ["NEAR", "SOL", "AVAX", "SUI", "APT", "ETH"],
    "ZIL": ["ONE", "NEAR", "DOT", "ADA", "AVAX", "ALGO"],
    "KAVA": ["ATOM", "AVAX", "DOT", "AAVE", "COMP", "NEAR"],
    "FTM": ["AVAX", "SOL", "NEAR", "SUI", "ETH", "DOT"],
    "LEO": ["BNB", "CRO", "OKB", "BGB", "ETH", "BTC"],
    "CRO": ["BNB", "LEO", "OKB", "BGB", "ETH", "BTC"],
    "MASK": ["ENS", "GRT", "LINK", "UNI", "ETH", "DOT"],
    "BLUR": ["ENS", "SAND", "MANA", "ETH", "UNI", "IMX"],
    "EIGEN": ["ETHFI", "LDO", "TIA", "ETH", "AAVE", "PENDLE"],
    "JASMY": ["IOTA", "VET", "FET", "RENDER", "LINK", "DOT"],
    "HYPE": ["SOL", "SUI", "DYDX", "GMX", "INJ", "JUP"],
    "PI": ["TON", "BTC", "ETH", "SOL", "DOGE", "XRP"],
    "VIRTUAL": ["RENDER", "FET", "TAO", "NEAR", "GALA", "BEAM"],
    "PENGU": ["SOL", "BONK", "WIF", "DOGE", "PEPE", "SHIB"],
    "WLD": ["FET", "RENDER", "TAO", "NEAR", "ETH", "LINK"],
    "ENA": ["AAVE", "PENDLE", "ETHFI", "LDO", "UNI", "ETH"],
    "MORPHO": ["AAVE", "COMP", "PENDLE", "LDO", "ETHFI", "CRV"],
    "SKY": ["AAVE", "UNI", "COMP", "ETH", "LDO", "CRV"],
    "FLR": ["XRP", "XLM", "ALGO", "HBAR", "DOT", "LINK"],
    "OKB": ["BNB", "CRO", "LEO", "BGB", "ETH", "BTC"],
    "BGB": ["BNB", "CRO", "OKB", "LEO", "ETH", "BTC"],
    "NEXO": ["AAVE", "COMP", "CRO", "LEO", "BNB", "ETH"],
    "XDC": ["VET", "HBAR", "XRP", "ALGO", "LINK", "QNT"],
}

# Default related if symbol not in map
_DEFAULT_RELATED = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]


# ── Simple in-memory cache ───────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes for CoinGecko data (rate-limit friendly)
_DB_CACHE_TTL_HOURS = 12  # 12 hours in Supabase (budget-friendly for 10K/month)
_AI_CACHE_TTL_SECONDS = 1800  # 30 minutes for AI-generated stories
# Hard cap on live entries — see stock_overview_service for rationale. Eviction
# is least-recently-written; a miss just re-fetches (no correctness impact).
_CACHE_MAX_ENTRIES = 1024

# ── CoinGecko TTLs ───────────────────────────────────────────────
# Call volume is the binding constraint now: 100,000/month is 2.3 calls/MINUTE sustained,
# not the 300/min burst ceiling. Each tier is sized to how fast its data can actually move.
# Ranges served from an INTRADAY CoinGecko series, and the `days` each asks for.
# A map, not an `in (...)` test plus a ternary, so the range set and its window can
# never drift apart. Branch on THIS, never on `resolve_interval(...) != "daily"`:
# DEFAULT_INTERVALS maps 5Y→"weekly" and ALL→"monthly", so an interval test sweeps
# those in and silently serves 7 days of hourly bars under a 5-year label.
# The window "Avg. Volume (30D)" promises. The row is OMITTED unless the history really
# covers it — see the derivation in `get_crypto_detail`.
_AVG_VOLUME_DAYS = 30

_INTRADAY_RANGES = {"1D": 1, "1W": 7}

# Ranges that ask for more history than CoinGecko Basic can serve (2 years). They are
# clamped to the cap and logged; iOS stops offering them for crypto in favour of 2Y.
_OVER_CAP_RANGES = frozenset({"5Y", "ALL"})

_CG_DAILY_TTL = 3600        # settled daily bars; only the trailing point moves
_CG_INTRADAY_TTL = 120      # the live 1D/1W chart
_CG_OHLC_TTL = 21600        # 6h — 4-day candles, nothing changes faster
_CG_MARKETS_TTL = 120       # related coins / tracking rows, shared across users
_CG_LIVE_MD_TTL = 60        # the re-hydrated header/statistics row on a DB-cache hit

# ── NEVER PERSIST A LIVE PRICE (`price_service.py` invariant #2) ──────────────────────
#
# `crypto_fundamentals_cache` rows live `_DB_CACHE_TTL_HOURS` (12h), and `/coins/{id}`'s
# `market_data` carries the LIVE price, the 24h change, the 24h high/low, market cap and
# volume. Persisting that blob whole meant a crypto header whose 5-minute memory entry had
# expired painted a **12-hour-old** price as the current one — and the Key Statistics column
# agreed with it, so nothing on screen looked stale. That is the same defect the ETF / index
# / commodity decompositions removed by deleting `_refresh_volatile` outright.
#
# So the DB tier stores the DURABLE half only, and a DB hit re-hydrates the volatile half
# from ONE live `/coins/markets` row. A full miss needs no overlay — `/coins/{id}` is fresh
# by definition — so the call budget only moves on the DB-hit path: 0 → 1 light call per
# symbol per minute, in exchange for the heavy `/coins/{id}` the DB tier still saves.
#
# ⚠️ Every name here must exist on a `/coins/markets` row too, or stripping it makes the
# field permanently absent instead of live. `test_crypto_live_price_not_persisted.py` pins
# that correspondence both ways.
_VOLATILE_MARKET_DATA_FIELDS = (
    "current_price",
    "price_change_24h",
    "price_change_percentage_24h",
    "market_cap",
    "total_volume",
    "high_24h",
    "low_24h",
    "last_updated",
)
# Which of those are `{currency: value}` dicts on `/coins/{id}` (bare floats on
# `/coins/markets`) versus bare floats on both. Getting this backwards is the silent-0 trap
# `_usd` documents: a bare float handed to `_usd` hits its `not isinstance(sub, dict)` arm.
_VOLATILE_CURRENCY_KEYED = frozenset({
    "current_price", "market_cap", "total_volume", "high_24h", "low_24h",
})


def strip_volatile_market_data(data: Any) -> Any:
    """Pure: a deep copy of a `/coins/{id}` payload with every price-derived field removed.

    Deep-copied because the argument is also the in-memory tier's object; popping in place
    would blank the header for every caller until the next upstream fetch.
    """
    if not isinstance(data, dict):
        return data
    out = copy.deepcopy(data)
    md = out.get("market_data")
    if isinstance(md, dict):
        for field in _VOLATILE_MARKET_DATA_FIELDS:
            md.pop(field, None)
    out["_volatile_stripped"] = True
    return out



async def _empty_list() -> list:
    """An already-satisfied awaitable, so a gated-off leg can still sit in the gather
    without restructuring it (the same shape `home_service._empty_list` uses)."""
    return []


def _round_close(v: float) -> float:
    """Round a close price with precision proportional to its magnitude.

    2 dp at/above $1, 6 dp down to $0.0001, 10 dp below that — the same ladder
    `technical_analysis_service._round_price` uses, so a coin's chart and its technical
    levels agree on how much precision a price has.
    """
    mag = abs(v)
    if mag >= 1:
        return round(v, 2)
    if mag >= 0.0001:
        return round(v, 6)
    return round(v, 10)


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


# ── Background AI refresh: task ownership + per-symbol dedup ─────────
#
# Crypto has fired its snapshot generation in the background since it shipped — which is
# WHY this screen measures 1.27s cold while index (5.63s) and ETF (5.89s) awaited Gemini
# inline. Two hazards it did not cover, added here because index/ETF now copy this shape:
#
#  * `asyncio.create_task` keeps only a WEAK reference, so a fire-and-forget task can be
#    garbage-collected mid-execution — the documented CPython caveat that `main.py`'s
#    `background_tasks` list exists for. If that happened here the defaults stayed cached
#    and nothing said so.
#  * An exception nobody retrieves is silent until GC, if ever.
_background_tasks: set = set()
# One generation per SYMBOL. `_build_snapshots` caches the defaults BEFORE spawning, so a
# second viewer within the same process is already covered — but two viewers arriving in
# the same tick, or a cache expiry mid-flight, would otherwise each spawn a Gemini run.
_ai_refresh_inflight: set = set()


def _on_background_done(task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(
            "Crypto background task %r failed (snapshots stay on defaults): %s: %s",
            task.get_name(), type(exc).__name__, exc,
        )


def _spawn_background(coro, *, name: str) -> None:
    """Own a fire-and-forget task: strong ref + a loud death."""
    try:
        task = asyncio.create_task(coro, name=name)
    except RuntimeError as e:
        logger.warning("Crypto background spawn skipped for %s: %s", name, e)
        coro.close()
        return
    _background_tasks.add(task)
    task.add_done_callback(_on_background_done)


def _cache_set(key: str, value: Any):
    _cache.pop(key, None)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


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

    # Sub-dollar: precision PROPORTIONAL to magnitude, on the same ladder `_round_close`
    # uses — so a coin's Key Statistics and its chart agree on how much precision a price
    # has (2 dp at/above $1, 6 dp down to $0.0001, 10 dp below).
    #
    # A flat 6 dp was wrong twice over on exactly the coins this branch exists for:
    #   • SHIB's 24h High 5.4123e-06 and 24h Low 5.3891e-06 BOTH rendered "$0.000005" —
    #     a real intraday range shown as a dead flat line, on the row whose only job is
    #     to show the range.
    #   • Anything under 1e-6 rendered "$0.000000" — a fabricated zero, the same defect
    #     as the "$0.00 Market Cap" this screen was rewritten to stop publishing.
    places = 6 if abs(value) >= 0.0001 else 10
    text = f"{value:.{places}f}"
    if "." in text:
        # Trim the padding the fixed width adds, but never below 2 dp: "$0.5" reads as a
        # truncation, and an exact 0.0 must still render "$0.00" rather than "$0.".
        whole, _, frac = text.rstrip("0").partition(".")
        text = f"{whole}.{frac.ljust(2, '0')}"
    return f"${text}"


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
    # Not enough history to cover the requested window: return None so the caller
    # OMITS this period rather than mislabeling a shorter (e.g. since-inception)
    # return under a "1Y"/"3Y"/... label (a young coin would otherwise show its
    # full-history return mislabeled as "1 Year"). Genuine since-inception rows use
    # the dedicated _compute_all_time_return, not this fallback.
    if len(prices) <= days_back:
        return None
    # Finite-guard both ends: a NaN/Inf close is truthy and slips past
    # `not start`/`start == 0`, producing a NaN change_percent that serializes to an
    # invalid-JSON `NaN` token and crashes the iOS decode of the whole detail screen
    # (change_percent is a non-optional Double on iOS). Matches index/commodity.
    from app.services.chart_helper import _finite_or_none
    start = _finite_or_none(prices[-(days_back + 1)].get("close") or prices[-(days_back + 1)].get("adjClose"))
    end = _finite_or_none(prices[-1].get("close") or prices[-1].get("adjClose"))
    if not start or not end or start == 0:
        return None
    return ((end - start) / start) * 100


def _compute_ytd_return(prices: List[Dict]) -> Optional[float]:
    if not prices or len(prices) < 2:
        return None
    current_year = datetime.now(tz=timezone.utc).year
    from app.services.chart_helper import _finite_or_none
    for p in prices:
        date_str = p.get("date") or ""
        if date_str.startswith(str(current_year)):
            # Finite-guard so a NaN/Inf close degrades to an omitted period, not a
            # NaN change_percent that breaks the (non-optional) iOS decode.
            start_price = _finite_or_none(p.get("close") or p.get("adjClose"))
            end_price = _finite_or_none(prices[-1].get("close") or prices[-1].get("adjClose"))
            if start_price and end_price and start_price > 0:
                return ((end_price - start_price) / start_price) * 100
            break
    return None


def _compute_all_time_return(prices: List[Dict]) -> Optional[float]:
    """Compute % return from first available price to latest."""
    if not prices or len(prices) < 2:
        return None
    from app.services.chart_helper import _finite_or_none
    start = _finite_or_none(prices[0].get("close") or prices[0].get("adjClose"))
    end = _finite_or_none(prices[-1].get("close") or prices[-1].get("adjClose"))
    if not start or not end or start == 0:
        return None
    return ((end - start) / start) * 100


# ── Main service ─────────────────────────────────────────────────


class CryptoService:
    """Aggregates CoinGecko + FMP data + Gemini AI for the Crypto Detail screen."""

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()
        self.coingecko: CoinGeckoClient = get_coingecko_client()
        self.supabase = get_supabase()

    # ── Source gate ──────────────────────────────────────────────
    #
    # FMP's crypto package is not on the Order Form, so every `…USD` pair 402s and the
    # price/history source is CoinGecko. The FMP branches below are KEPT and reachable:
    # set `CRYPTO_PRICE_SOURCE=fmp` and every one of them runs again, unchanged. Nothing
    # was deleted, so buying the package back is one environment variable — the same
    # posture `FMPClient`'s entitlement manifest takes ("nothing is deleted, so buying a
    # package later re-enables the feature").

    @staticmethod
    def _fmp_crypto_enabled() -> bool:
        return str(settings.CRYPTO_PRICE_SOURCE or "").lower() == "fmp"

    @staticmethod
    def _history_days_cap() -> int:
        """The furthest back any crypto surface may look, in days.

        ONE source for every long-horizon decision — chart ranges, the 3Y/5Y/10Y/All-Time
        performance rows, the benchmark CAGR window. Scattering `365 * N` literals is how
        a 15-year assumption survived into a 2-year world.
        """
        return max(1, int(settings.CRYPTO_HISTORY_YEARS) * 365)

    # ── CoinGecko-backed history ─────────────────────────────────

    async def _cg_history(
        self, symbol: str, days: int, *, intraday: bool = False
    ) -> List[Dict[str, Any]]:
        """Daily (or intraday) rows in the app's FMP-shaped row format.

        Cached per (symbol, days, granularity). ⚠️ The cache key carries the SOURCE: a
        rolling deploy with `CRYPTO_PRICE_SOURCE` mixed across pods would otherwise let
        CoinGecko-shaped rows (close+volume, no OHLC) be read by FMP-shaped code, or the
        reverse — a shape mismatch that renders as missing highs rather than an error.
        """
        from app.services.coingecko_adapter import market_chart_to_rows

        days = min(int(days), self._history_days_cap())
        key = f"cg:hist:{symbol.upper()}:{days}:{'i' if intraday else 'd'}"
        cached = _cache_get(key, _CG_INTRADAY_TTL if intraday else _CG_DAILY_TTL)
        if cached is not None:
            return cached
        payload = await self.coingecko.get_market_chart(
            symbol, days, interval=None if intraday else "daily"
        )
        rows = market_chart_to_rows(payload, intraday=intraday)
        if rows:
            _cache_set(key, rows)
        return rows

    async def _cg_52_week_band(
        self, symbol: str
    ) -> Tuple[Optional[float], Optional[float]]:
        """True 52-week high/low, from `/ohlc?days=365`.

        `market_chart` carries **no high/low at all**, so a close-only band understates
        both extremes — measured on BTC: $124,740/$58,566 from closes versus the real
        $126,080/$57,779. `/ohlc`'s candles are coarse (4-day at this range, and CoinGecko
        offers nothing finer on Basic), but each candle's high/low ARE true intraday
        extremes over its bucket, so the max/min across 365 days is a genuine 52-week band.
        One extra call, cached for hours.
        """
        from app.services.coingecko_adapter import ohlc_to_rows

        key = f"cg:ohlc365:{symbol.upper()}"
        cached = _cache_get(key, _CG_OHLC_TTL)
        if cached is None:
            coin_id = await self.coingecko.resolve_coin_id(symbol)
            if not coin_id:
                return None, None
            raw = await self.coingecko._make_request(
                f"coins/{coin_id}/ohlc", params={"vs_currency": "usd", "days": 365}
            )
            cached = ohlc_to_rows(raw)
            if cached:
                _cache_set(key, cached)
        highs = [r["high"] for r in cached if r.get("high") and r["high"] > 0]
        lows = [r["low"] for r in cached if r.get("low") and r["low"] > 0]
        return (max(highs) if highs else None), (min(lows) if lows else None)

    async def _cg_related_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """Related-coin quotes, shaped exactly like `price_service` emits them.

        ONE `/coins/markets` request for the whole set, replacing six blocked per-symbol
        FMP quotes.

        Two things this must not get wrong:

        * **Key on the coin ID, never the returned symbol.** `MATIC` and `POL` both
          resolve to `polygon-ecosystem-token`, CoinGecko answers with one canonical
          symbol, and `/coins/markets` orders by market cap rather than request order —
          so keying by symbol or zipping positionally drops a coin. MATIC appears in six
          `_RELATED_CRYPTOS` sets, i.e. six screens with a permanently missing row.
        * **Emit through `PriceService._shape`.** Callers across the app read
          `changePercentage` AND the legacy `changesPercentage`, and `_shape` emits both
          on purpose. A hand-rolled dict that carries only one spelling produces alerts
          that never fire and rows that read 0.00% — invisible in every test.
        """
        from app.services.coingecko_adapter import markets_rows_by_id
        from app.services.price_service import PriceService

        wanted = [s for s in symbols if s]
        if not wanted:
            return []
        key = "cg:markets:" + ",".join(sorted({s.upper() for s in wanted}))
        cached = _cache_get(key, _CG_MARKETS_TTL)
        if cached is not None:
            return cached

        pairs: List[Tuple[str, str]] = []
        for sym in wanted:
            coin_id = await self.coingecko.resolve_coin_id(sym)
            if coin_id:
                pairs.append((sym, coin_id))
        if not pairs:
            return []
        rows = await self.coingecko.get_markets([s for s, _ in pairs])
        by_id = markets_rows_by_id(rows)

        out: List[Dict[str, Any]] = []
        for sym, coin_id in pairs:
            row = by_id.get(coin_id)
            if not row:
                continue
            price = row.get("current_price")
            change_pct = row.get("price_change_percentage_24h")
            change_abs = row.get("price_change_24h")
            prev = None
            if isinstance(price, (int, float)) and isinstance(change_abs, (int, float)):
                prev = price - change_abs
            out.append(PriceService._shape(
                # The caller's map is keyed on the `…USD` pair spelling.
                symbol=f"{sym.upper()}USD",
                name=row.get("name"),
                price=price,
                previous_close=prev,
                change=change_abs,
                change_pct=change_pct,
                volume=row.get("total_volume"),
                avg_volume=None,
                market_cap=row.get("market_cap"),
                exchange="CRYPTO",
            ))
        if out:
            _cache_set(key, out)
        return out

    # ── Two-tier cache for CoinGecko fundamentals ────────────────

    async def _get_coin_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """
        Two-tier cache for CoinGecko coin data:
          Tier 1: in-memory (5 min TTL)
          Tier 2: Supabase crypto_fundamentals_cache (`_DB_CACHE_TTL_HOURS`),
                  DURABLE fields only — the price half is re-hydrated live
          Miss:   CoinGecko API call → cache in both tiers
        """
        mem_key = f"cg_fundamentals:{symbol}"

        # Tier 1: in-memory
        cached = _cache_get(mem_key, _CACHE_TTL_SECONDS)
        if cached is not None:
            logger.debug(f"CoinGecko mem cache hit for {symbol}")
            return cached

        # Tier 2: Supabase — DURABLE fields only. The row can be 12h old, so the
        # price-derived half is re-hydrated live before anything reads it. See
        # `_VOLATILE_MARKET_DATA_FIELDS`.
        db_data = await asyncio.to_thread(self._check_crypto_cache_db, symbol)
        if db_data is not None:
            logger.debug(f"CoinGecko DB cache hit for {symbol}")
            db_data = await self._rehydrate_volatile(symbol, db_data)
            _cache_set(mem_key, db_data)
            return db_data

        # Miss: fetch from CoinGecko
        logger.info(f"CoinGecko cache miss — fetching /coins/ for {symbol}")
        coin_data = await self.coingecko.get_coin_data(symbol)
        if coin_data:
            _cache_set(mem_key, coin_data)
            asyncio.get_event_loop().run_in_executor(
                None, self._upsert_crypto_cache_db, symbol, coin_data,
            )

        return coin_data or {}

    def _check_crypto_cache_db(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Check Supabase crypto_fundamentals_cache (`_DB_CACHE_TTL_HOURS`).

        The row is stripped of every price-derived field on the way out — see
        `_VOLATILE_MARKET_DATA_FIELDS`.
        """
        try:
            row = (
                self.supabase.table("crypto_fundamentals_cache")
                .select("response_json, cached_at")
                .eq("symbol", symbol)
                .limit(1)
                .execute()
            )
            if row.data and len(row.data) > 0:
                cached_at_str = row.data[0].get("cached_at", "")
                if cached_at_str:
                    cached_at = datetime.fromisoformat(
                        cached_at_str.replace("Z", "+00:00")
                    )
                    age_hours = (
                        datetime.now(timezone.utc) - cached_at
                    ).total_seconds() / 3600
                    if age_hours < _DB_CACHE_TTL_HOURS:
                        # Stripped on the READ as well as the write. The write-side strip
                        # only protects rows persisted AFTER this deploy; rows already in
                        # the table still hold a live price and would otherwise keep
                        # serving it for up to _DB_CACHE_TTL_HOURS. Cheap, and it makes
                        # the invariant hold without a data migration.
                        return strip_volatile_market_data(
                            row.data[0].get("response_json")
                        )
                    logger.debug(f"Crypto DB cache expired for {symbol} ({age_hours:.1f}h)")
        except Exception as e:
            logger.warning(f"Crypto DB cache read failed for {symbol}: {e}")
        return None

    async def _rehydrate_volatile(
        self, symbol: str, durable: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Overlay a live `/coins/markets` row onto a DURABLE (price-stripped) payload.

        Every volatile field on the result comes from the live row or is ABSENT — there is
        no path on which a persisted number survives. That is why the strip runs here too
        rather than only at the read: it makes the guarantee a property of this function,
        so an outage degrades to "unknown" instead of to a 12-hour-old price. Consumers
        already render an absent field as "—" (`_usd_opt`), and `get_crypto_core` raises
        rather than paint a `$0.00` header.
        """
        if not isinstance(durable, dict):
            return durable
        out = strip_volatile_market_data(durable)
        row = await self._live_markets_row(symbol)
        if not row:
            logger.info(
                "crypto_service: no live row for %s — serving durable fundamentals with "
                "no price rather than the persisted (up to %dh old) one",
                symbol, _DB_CACHE_TTL_HOURS,
            )
            return out

        md = out.get("market_data")
        if not isinstance(md, dict):
            md = {}
            out["market_data"] = md
        for field in _VOLATILE_MARKET_DATA_FIELDS:
            value = row.get(field)
            if value is None:
                # Absent stays absent. Writing a 0 here is the exact fabrication the
                # strip exists to prevent.
                continue
            md[field] = (
                {"usd": value} if field in _VOLATILE_CURRENCY_KEYED else value
            )
        out.pop("_volatile_stripped", None)
        return out

    async def _live_markets_row(self, symbol: str) -> Dict[str, Any]:
        """One live `/coins/markets` row for `symbol`, memoised for `_CG_LIVE_MD_TTL`.

        Keyed by COIN ID, not by `row["symbol"]` — MATIC and POL both resolve to
        `polygon-ecosystem-token` and CoinGecko answers with one canonical symbol, so a
        symbol key drops the other. Returns {} on any failure; never raises.
        """
        from app.services.coingecko_adapter import crypto_base_symbol, markets_rows_by_id

        base = crypto_base_symbol(symbol)
        if not base:
            return {}
        key = f"cg:live_md:{base}"
        cached = _cache_get(key, _CG_LIVE_MD_TTL)
        if cached is not None:
            return cached
        try:
            coin_id = await self.coingecko.resolve_coin_id(base)
            if not coin_id:
                return {}
            rows = await self.coingecko.get_markets([base])
            row = markets_rows_by_id(rows).get(coin_id)
            if not isinstance(row, dict):
                return {}
            _cache_set(key, row)
            return row
        except Exception as e:
            logger.warning(
                "crypto_service: live market row unavailable for %s (%s: %s)",
                symbol, type(e).__name__, e,
            )
            return {}

    def _upsert_crypto_cache_db(self, symbol: str, data: Dict[str, Any]) -> None:
        """Upsert CoinGecko response into Supabase cache — DURABLE fields only.

        `strip_volatile_market_data` is applied HERE, at the persistence boundary, rather
        than at the read: a row already written with a price would otherwise stay a live
        price for 12h after the fix deployed.
        """
        try:
            self.supabase.table("crypto_fundamentals_cache").upsert(
                {
                    "symbol": symbol,
                    "response_json": strip_volatile_market_data(data),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="symbol",
            ).execute()
        except Exception as e:
            logger.warning(f"Crypto DB cache write failed for {symbol}: {e}")

    async def get_crypto_core(
        self,
        symbol: str,
        chart_range: str = "3M",
        interval: Optional[str] = None,
    ) -> "CryptoCoreResponse":
        """FIRST PAINT: the header line, from the cached CoinGecko fundamentals alone.

        The full build gathers CoinGecko fundamentals + a 15-YEAR FMP history + news +
        six related quotes. Only the first of those is needed to paint the header, and it
        is already two-tier cached (5 min memory / `_DB_CACHE_TTL_HOURS` Supabase, the
        latter re-hydrated live), so this answers well
        inside the 1.27s the full build measured cold.

        `chart_range` / `interval` are accepted for call-site symmetry with the other
        three core endpoints and are deliberately unused: crypto has no cached chart
        section to serve from, and pulling the history is the very thing core exists to
        skip. The full response owns the chart.
        """
        from app.schemas.crypto import CryptoCoreResponse
        # Local, matching every other call site in this module: `_finite_or_none` is not
        # imported at module scope here.
        from app.services.chart_helper import _finite_or_none

        symbol = symbol.upper()
        profile_meta = _CRYPTO_PROFILES.get(symbol, {})

        coin_data = await self._get_coin_fundamentals(symbol)
        md = coin_data.get("market_data", {}) if isinstance(coin_data, dict) else {}

        def _usd(field: str, default: float = 0) -> float:
            # CoinGecko returns some fields as JSON null (not absent), so a plain
            # `md.get(f, {}).get("usd")` does None.get(...) and 500s the response.
            sub = md.get(field)
            if not isinstance(sub, dict):
                return default
            v = sub.get("usd")
            return v if v is not None else default

        price = _finite_or_none(_usd("current_price")) or 0
        if price <= 0:
            # The full build can fall back to the last finite FMP close here; core has no
            # history to fall back to, and a "$0.00" header is worse than a skeleton. The
            # client fetches core with `try?`, so this just leaves the shimmer up.
            raise ValueError(f"crypto core has no usable price for {symbol}")

        change = _finite_or_none(md.get("price_change_24h")) or 0
        change_pct = _finite_or_none(md.get("price_change_percentage_24h")) or 0

        # Prefer the curated profile name, then CoinGecko's, then the symbol.
        name = profile_meta.get("name")
        if not name and isinstance(coin_data, dict):
            cg_name = coin_data.get("name")
            if isinstance(cg_name, str) and cg_name.strip():
                name = cg_name
        if not name:
            name = symbol

        return CryptoCoreResponse(
            symbol=symbol,
            name=name,
            current_price=price,
            price_change=change,
            price_change_percent=change_pct,
            market_status="24/7 Trading",
            chart_data=[],
        )

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

        # Resolve profile metadata (may be enriched after CoinGecko fetch)
        profile_meta = _CRYPTO_PROFILES.get(symbol, {})
        crypto_name = profile_meta.get("name", symbol)
        _has_curated_profile = bool(profile_meta)

        # ── Step 1: Parallel fetches ──────────────────────────────
        today = datetime.now(tz=timezone.utc).date()
        # The window is derived from the plan cap, not a literal. It was `365 * 15` — a
        # 15-year assumption that survived into a 2-year world and silently relabelled
        # "last 2 years" as "All Time".
        history_days = self._history_days_cap()
        from_date = (today - timedelta(days=history_days)).isoformat()
        to_date = today.isoformat()

        # Related crypto symbols
        related_symbols = _RELATED_CRYPTOS.get(symbol, _DEFAULT_RELATED)
        related_symbols = [s for s in related_symbols if s != symbol][:6]
        related_fmp_symbols = [f"{s}USD" for s in related_symbols]

        coin_data_task = self._get_coin_fundamentals(symbol)

        # ── The source gate. FMP's branches are preserved verbatim ────────────────
        if self._fmp_crypto_enabled():
            hist_task = self.fmp.get_historical_prices(fmp_symbol, from_date, to_date)
            related_task = price_source(self).get_quotes_list(related_fmp_symbols)
        else:
            hist_task = self._cg_history(symbol, history_days)
            related_task = self._cg_related_quotes(related_symbols)

        # `news/stock` for a crypto pair is REDUNDANT, not broken: it is entitled and
        # returns real Bitcoin news (verified 200, same rows as `news/crypto`). But iOS's
        # `CryptoDetailResponse.toModel()` discards `newsArticles` entirely — the News tab
        # calls `GET /crypto/{symbol}/news` instead — so this call has been doing a full
        # round trip per detail request for a field nobody reads. Gated off rather than
        # deleted; the builder below still runs if it is ever turned back on.
        news_task = (
            self.fmp.get_stock_news(fmp_symbol, limit=10)
            if self._fmp_crypto_enabled()
            else _empty_list()
        )

        coin_data, hist_raw, news_raw, related_raw = await asyncio.gather(
            coin_data_task, hist_task, news_task, related_task,
            return_exceptions=True,
        )

        # Handle exceptions gracefully
        if isinstance(coin_data, Exception):
            logger.error(f"CoinGecko fetch failed for {symbol}: {coin_data}")
            coin_data = {}
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
        historical.sort(key=lambda p: p.get("date") or "")

        # ── Step 2: Extract data from CoinGecko ──────────────────
        md = coin_data.get("market_data", {}) if isinstance(coin_data, dict) else {}

        def _usd(field: str, default: float = 0) -> float:
            """Read the ``usd`` value from a CoinGecko currency-keyed field.

            CoinGecko returns some fields as JSON ``null`` (not absent) — most
            commonly ``fully_diluted_valuation`` for uncapped coins. A plain
            ``md.get(field, {}).get("usd")`` then does ``None.get(...)`` ->
            AttributeError, 500-ing the WHOLE crypto detail response. Coalescing
            the sub-object to ``{}`` keeps it safe.
            """
            sub = md.get(field)
            if not isinstance(sub, dict):
                return default
            v = sub.get("usd")
            return v if v is not None else default

        def _usd_opt(field: str) -> Optional[float]:
            """`_usd` for the fields where ABSENT must stay absent.

            ⚠️ `_usd`'s `default=0` is right for `current_price` (which has its own
            last-close recovery below) and catastrophic for the statistics: when
            `/coins/{id}` degrades, `md` is `{}` and every one of these reads 0, so the
            Key Statistics column shipped "Market Cap $0.00 / 24h Volume $0.00 / 24h High
            $0.00 / Circulating Supply 0 BTC" as FACTS — underneath a header showing a
            real price recovered from the chart, which makes them look measured rather
            than missing.

            The house rule is that an unknown number is None, never 0.0
            (`price_service.py` invariant #1), and the 52-week block a few lines below
            already honours it: `_fmt(None)` and `_pct(None)` render "—". This is the
            same degrade for the other columns.
            """
            sub = md.get(field)
            if not isinstance(sub, dict):
                return None
            v = sub.get("usd")
            return v if isinstance(v, (int, float)) else None

        price = _usd("current_price")
        change = md.get("price_change_24h", 0) or 0
        change_pct = md.get("price_change_percentage_24h", 0) or 0

        # CoinGecko outage / unresolved coin id → market_data is {} and every
        # _usd()/md.get() collapses to 0, shipping a bogus "$0.00 (+0.00%)" header
        # under a live FMP price chart (the FMP `historical` fetch succeeds
        # independently). Degrade to the real FMP price: last finite historical
        # close, deriving the 24h move from the prior close. A wrong $0.00 is worse
        # than a slightly stale-but-real price.
        if not price and historical:
            from app.services.chart_helper import _finite_or_none
            _fin_closes = [
                c for c in (
                    _finite_or_none(p.get("close") or p.get("adjClose"))
                    for p in historical
                ) if c and c > 0
            ]
            if _fin_closes:
                price = _fin_closes[-1]
                if not change and not change_pct and len(_fin_closes) >= 2:
                    _prev = _fin_closes[-2]
                    if _prev > 0:
                        change = round(price - _prev, 6)
                        change_pct = round(((price - _prev) / _prev) * 100, 4)

        # Both legs share ONE provider now, so they fail together — refuse rather than
        # ship a zero.
        #
        # ⚠️ The recovery above is dead in exactly the case it was written for. Its comment
        # says "the FMP `historical` fetch succeeds independently", which stopped being
        # true when history moved to CoinGecko: a 429 that empties `market_data` empties
        # `historical` too, so `if not price and historical` never runs and `price` stays
        # 0.0. The screen then shipped "$0.00 / +0.00%" as a measured fact for a live coin.
        #
        # `get_crypto_core` already refuses in this situation ("a $0.00 header is worse
        # than a skeleton"), and index/commodity detail do the same. Typed, so the endpoint
        # maps it to COINGECKO_UNAVAILABLE (502, retry_later) instead of a generic 500.
        if not price or price <= 0:
            if self._fmp_crypto_enabled():
                raise ValueError(f"crypto detail has no usable price for {symbol}")
            from app.integrations.coingecko import CoinGeckoUnavailableException

            raise CoinGeckoUnavailableException(
                f"no usable price for {symbol}: both the fundamentals and the history "
                f"legs returned nothing"
            )

        # Absent stays absent — see `_usd_opt`. These render "—" rather than "$0.00".
        day_high = _usd_opt("high_24h")
        day_low = _usd_opt("low_24h")
        volume = _usd_opt("total_volume")
        market_cap = _usd_opt("market_cap")
        circulating_supply = md.get("circulating_supply")
        total_supply = md.get("total_supply")
        # ⚠️ `None` here is AMBIGUOUS and the two meanings render differently:
        #   • CoinGecko answered and the coin has no cap  → "No Cap" (a fact)
        #   • `md` is {} because /coins/{id} degraded     → unknown, must render "—"
        # `md.get(...)` collapses both to None, and the old `if max_supply else "No Cap"`
        # then published the degraded case as a FACT — the same class as the fabricated
        # "$0.00 Market Cap" the `_usd_opt` rewrite removed one screen over. So carry the
        # measurement separately, exactly as `benchmark_available` / `mfi_known` do.
        max_supply_cg = md.get("max_supply")  # None = no cap OR not measured
        max_supply_known = (
            # A curated profile is authoritative in BOTH directions: the table encodes
            # `"max_supply": None` for the genuinely uncapped coins (ETH, XRP, …).
            symbol in _CRYPTO_PROFILES
            # Otherwise only an actual answer counts. `in` rather than truthiness: an
            # explicit JSON null IS the "no cap" answer.
            or (isinstance(md, dict) and "max_supply" in md)
        )
        fdv = _usd_opt("fully_diluted_valuation")

        # 52-week band, from the ONLY source that can express a 52-week window:
        # the daily history. `None` when it cannot be derived — never a stand-in.
        #
        # ⚠️ There was a fallback to CoinGecko's ALL-TIME `ath`/`atl` here, for the
        # case where `historical` came back empty. That case was rare when it was
        # written and is now the ONLY case: FMP 402s every `…USD` crypto pair since
        # enforcement went live 2026-09-03, so `historical` is always empty and the
        # all-time figures became the only ones ever shown — under a "52-Week" label.
        # Bitcoin's atl is $67.81, set in 2013, which rendered live as
        # "52-Week Low $67.81 / From 52W Low +115,804.73% / 52-Week % Range
        # 185,831.28%". An all-time extreme is not a 52-week extreme, and no TTL or
        # refresh can make it one — the WRITER has to refuse, so it does.
        #
        # An unknown number is None, never a substitute (price_service.py:37-45).
        # `_fmt(None)` and `_pct(None)` already render "—", so the five rows degrade
        # to honest em-dashes instead of disappearing (a missing row reads as a
        # layout bug; an em-dash reads as "we don't know", which is the truth).
        # ⚠️ Phase 5 note: `market_chart` carries close and volume and **no high/low at
        # all**, so the daily-history path below yields nothing on the CoinGecko source
        # and these five rows would be em-dashes forever. `/ohlc?days=365` is the answer:
        # its candles are coarse (4-day at that range, and Basic offers nothing finer)
        # but each candle's high/low ARE true intraday extremes over its bucket, so the
        # max/min across the year is a genuine 52-week band. Measured on BTC: $126,080 /
        # $57,779 from /ohlc versus $124,740 / $58,566 from closes — the close-only
        # version understates both ends, which is precisely the kind of quietly-wrong
        # number this phase exists to remove.
        year_high: Optional[float] = None
        year_low: Optional[float] = None
        if not self._fmp_crypto_enabled():
            # Guarded: this is an EXTRA CoinGecko call outside the main gather, and an
            # unguarded await made one /ohlc hiccup (a 429 after 3 retries) propagate out
            # of the whole builder — so the endpoint returned an error screen instead of a
            # complete crypto detail merely missing 5 of its 14 statistics rows. The same
            # failure INSIDE the gather degrades gracefully; this now matches it.
            try:
                year_high, year_low = await self._cg_52_week_band(symbol)
            except Exception as e:
                logger.warning(
                    "crypto 52-week band unavailable for %s (%s: %s) — the five band rows "
                    "degrade to em-dashes; the rest of the screen is unaffected",
                    symbol, type(e).__name__, e,
                )
        if (year_high is None or year_low is None) and historical:
            from app.services.chart_helper import _finite_or_none
            one_year_ago = (today - timedelta(days=365)).isoformat()
            year_prices = [
                p for p in historical
                if (p.get("date") or "") >= one_year_ago
            ]
            if year_prices:
                # Finite-guard BOTH ends. A NaN high is truthy and slips past a bare
                # `or 0`, and `max()` propagates it into the response as an invalid
                # JSON `NaN` token — the decode crash this file already guards
                # against in `_compute_return`.
                highs = [
                    h for h in (_finite_or_none(p.get("high")) for p in year_prices)
                    if h is not None and h > 0
                ]
                lows = [
                    lo for lo in (_finite_or_none(p.get("low")) for p in year_prices)
                    if lo is not None and lo > 0
                ]
                year_high = max(highs) if highs else None
                year_low = min(lows) if lows else None

        if year_high is None or year_low is None:
            logger.warning(
                "Crypto %s: no 52-week window derivable from %d historical rows — "
                "omitting the 52-week statistics rather than substituting all-time "
                "ATH/ATL, which would mislabel a multi-year extreme as 52-week",
                symbol, len(historical or []),
            )

        # Avg volume over 30 DAYS, or nothing. Neither source publishes it directly, so it
        # is derived from the daily history.
        #
        # ⚠️ Two substitutions used to hide behind this label, and both are the "label must
        # describe the data shown" rule again:
        #   * `avg_volume = volume` fell back to the TWENTY-FOUR HOUR volume whenever the
        #     history was empty (a market_chart 429, or an id that resolves for
        #     /coins/{id} but not for history) — a one-day figure rendered as "$34.20B"
        #     under "Avg. Volume (30D)".
        #   * `historical[-30:] if len >= 30 else historical` averaged whatever it had, so
        #     a coin listed eleven days ago shipped an 11-day mean under a 30-day label.
        # Neither was logged; the label was the only thing the user had, and it was wrong.
        # Imported here, not relied upon from the 52-week block above — that import sits
        # inside an `if` and is not guaranteed to have run.
        from app.services.chart_helper import _finite_or_none as _fin

        avg_volume: Optional[float] = None
        if historical and len(historical) >= _AVG_VOLUME_DAYS:
            last_30 = historical[-_AVG_VOLUME_DAYS:]
            vols = [
                v for v in (_fin(p.get("volume")) for p in last_30 if isinstance(p, dict))
                if v is not None and v > 0
            ]
            # Gaps inside the window are tolerated; an absent window is not.
            if vols:
                avg_volume = sum(vols) / len(vols)
        if avg_volume is None:
            logger.info(
                "Crypto %s: fewer than %d daily rows (%d) — omitting Avg. Volume (30D) "
                "rather than labelling a shorter mean, or the 24h volume, as 30-day",
                symbol, _AVG_VOLUME_DAYS, len(historical or []),
            )

        # ── Auto-generate profile from CoinGecko if no curated profile ──
        if not _has_curated_profile and isinstance(coin_data, dict):
            profile_meta = self._build_profile_from_coingecko(symbol, coin_data)
            crypto_name = profile_meta.get("name", symbol)

        # ── Step 3: Compute derived stats ─────────────────────────
        # Prefer CoinGecko's pre-computed percentages, fall back to historical.
        # Use an explicit None check, NOT `or`: a legitimate 0.0% is falsy and
        # would otherwise be discarded and silently recomputed.
        one_month_return = md.get("price_change_percentage_30d")
        if one_month_return is None:
            one_month_return = _compute_return(historical, 30)
        one_year_return = md.get("price_change_percentage_1y")
        if one_year_return is None:
            one_year_return = _compute_return(historical, 365)
        ytd_return = _compute_ytd_return(historical)
        three_year_return = (
            _compute_return(historical, 365 * 3)
            if len(historical) > 365 * 3
            else None
        )
        five_year_return = (
            _compute_return(historical, 365 * 5)
            if len(historical) > 365 * 5
            else None
        )
        # 🔴 "All Time" is only true if the history REACHES all time.
        #
        # `_compute_all_time_return` means "first row → last row". Against a 15-year FMP
        # pull that was approximately true. Against CoinGecko Basic's hard 730-day cap it
        # is false for every coin older than two years: Bitcoin rendered "All Time
        # +37.58%" meaning "the last 24 months", with a non-Optional Double on the wire so
        # nothing crashed — it was simply a confident wrong number under the most
        # authoritative-sounding label on the card.
        #
        # 3Y/5Y/10Y need no equivalent guard: `_compute_return` already returns None when
        # `len(historical) <= days_back`, and 1095 > 730. Only the unbounded one lies.
        _history_reaches_all_time = self._fmp_crypto_enabled()
        all_time_return = (
            _compute_all_time_return(historical) if _history_reaches_all_time else None
        )
        if not _history_reaches_all_time:
            logger.debug(
                "Crypto %s: All-Time return suppressed — the %d-day source cannot "
                "express it", symbol, self._history_days_cap(),
            )
        ten_year_return = (
            _compute_return(historical, 365 * 10)
            if len(historical) > 365 * 10
            else None
        )

        # ── Step 3b: Fetch benchmark data ────────────────────────
        # Altcoins benchmark vs BTC; BTC benchmarks vs S&P 500
        bench_1m = bench_ytd = bench_1y = bench_3y = bench_5y = bench_10y = bench_all = None
        spy_hist = []
        btc_hist = []
        if symbol == "BTC":
            benchmark_label = "S&P 500"
            # Fetch SPY historical from FMP (cached in memory)
            spy_cache_key = f"spy_hist:{from_date}:{to_date}"
            spy_hist = _cache_get(spy_cache_key, 3600)  # 1h cache
            if spy_hist is None:
                try:
                    spy_raw = await self.fmp.get_historical_prices("SPY", from_date, to_date)
                    if isinstance(spy_raw, dict):
                        spy_hist = spy_raw.get("historical", [])
                    elif isinstance(spy_raw, list):
                        spy_hist = spy_raw
                    else:
                        spy_hist = []
                    spy_hist.sort(key=lambda p: p.get("date") or "")
                    if spy_hist:
                        _cache_set(spy_cache_key, spy_hist)
                except Exception as e:
                    logger.warning(f"SPY historical fetch failed: {e}")
                    spy_hist = []
            if spy_hist:
                # SPY history is TRADING days (~252/yr). _compute_return looks back by
                # array POSITION, so 1M/1Y must use trading-day counts (21/252) — using
                # 30/365 looked back ~6 weeks / ~1.45 yr, so the "1 Month"/"1 Year"
                # benchmark (and the vs-market delta derived from it) were wrong. Matches
                # the 252*N convention already used for 3Y/5Y/10Y below.
                bench_1m = _compute_return(spy_hist, 21)
                bench_ytd = _compute_ytd_return(spy_hist)
                bench_1y = _compute_return(spy_hist, 252)
                bench_3y = _compute_return(spy_hist, 252 * 3) if len(spy_hist) > 252 * 3 else None
                bench_5y = _compute_return(spy_hist, 252 * 5) if len(spy_hist) > 252 * 5 else None
                bench_10y = _compute_return(spy_hist, 252 * 10) if len(spy_hist) > 252 * 10 else None
                bench_all = _compute_all_time_return(spy_hist)
        else:
            benchmark_label = "BTC"
            # Fetch BTC data from CoinGecko (likely already cached).
            #
            # Guarded for the same reason as the 52-week band above: this call exists only
            # to populate two "vs BTC" comparison numbers, and an unguarded await let a
            # CoinGecko failure here kill an ALTCOIN screen whose own coin data, chart,
            # 52-week band, related coins and snapshots had all succeeded.
            try:
                btc_coin_data = await self._get_coin_fundamentals("BTC")
            except Exception as e:
                logger.warning(
                    "BTC benchmark fundamentals unavailable for the %s screen (%s: %s) — "
                    "the vs-BTC rows are omitted; the rest of the screen is unaffected",
                    symbol, type(e).__name__, e,
                )
                btc_coin_data = {}
            btc_md = btc_coin_data.get("market_data", {}) if isinstance(btc_coin_data, dict) else {}
            bench_1m = btc_md.get("price_change_percentage_30d")
            bench_1y = btc_md.get("price_change_percentage_1y")
            # For YTD, 3Y, 5Y, All Time — compute from BTC historical.
            #
            # 🔴 This leg was still on FMP, which 402s every `…USD` crypto pair since
            # enforcement went live 2026-09-03. So `btc_hist` was ALWAYS `[]` and the
            # "vs BTC" YTD / 3Y / 5Y / All-Time rows were silently absent on every
            # altcoin screen — invisible, because an omitted row is also what a genuinely
            # short history produces. The coin's OWN history was moved to CoinGecko in
            # this phase; its benchmark was left behind.
            #
            # Routed through `_cg_history`, the same cached reader the chart uses, so the
            # benchmark and the chart cannot disagree about BTC's prices. The FMP path is
            # GATED, not deleted — flipping `CRYPTO_PRICE_SOURCE=fmp` restores it verbatim.
            btc_fmp_symbol = "BTCUSD"
            # ⚠️ The SOURCE is in the key. The two paths produce differently shaped rows
            # (FMP carries OHLC, CoinGecko carries close+volume only), and during a rolling
            # deploy with `CRYPTO_PRICE_SOURCE` mixed across pods one pod's rows would be
            # read by the other's code — a shape mismatch that renders as missing data
            # rather than an error. Same reasoning as `_cg_history`'s key.
            _hist_source = "fmp" if self._fmp_crypto_enabled() else "cg"
            btc_hist_cache_key = f"btc_hist:{_hist_source}:{from_date}:{to_date}"
            btc_hist = _cache_get(btc_hist_cache_key, 3600)
            if btc_hist is None:
                try:
                    if self._fmp_crypto_enabled():
                        btc_raw = await self.fmp.get_historical_prices(btc_fmp_symbol, from_date, to_date)
                        if isinstance(btc_raw, dict):
                            btc_hist = btc_raw.get("historical", [])
                        elif isinstance(btc_raw, list):
                            btc_hist = btc_raw
                        else:
                            btc_hist = []
                    else:
                        btc_hist = await self._cg_history("BTC", history_days)
                    btc_hist = list(btc_hist or [])
                    btc_hist.sort(key=lambda p: p.get("date") or "")
                    if btc_hist:
                        _cache_set(btc_hist_cache_key, btc_hist)
                except Exception as e:
                    logger.warning(
                        "BTC benchmark history unavailable for the %s screen (%s: %s) — "
                        "the vs-BTC YTD/3Y/5Y rows are omitted; the rest is unaffected",
                        symbol, type(e).__name__, e,
                    )
                    btc_hist = []
            if btc_hist:
                bench_ytd = _compute_ytd_return(btc_hist)
                bench_3y = _compute_return(btc_hist, 365 * 3) if len(btc_hist) > 365 * 3 else None
                bench_5y = _compute_return(btc_hist, 365 * 5) if len(btc_hist) > 365 * 5 else None
                bench_10y = _compute_return(btc_hist, 365 * 10) if len(btc_hist) > 365 * 10 else None
                # Same suppression as the coin's own All-Time row a few lines above, and
                # for the identical reason: `_compute_all_time_return` is "first row →
                # last row", which over a 730-day cap is "the last two years" published
                # under the most authoritative label on the card. 3Y/5Y/10Y need no guard
                # (`_compute_return` returns None once `len <= days_back`, and 1095 > 730);
                # only the unbounded one lies.
                bench_all = (
                    _compute_all_time_return(btc_hist)
                    if _history_reaches_all_time else None
                )

        # ── Step 4: Build chart data ──────────────────────────────
        from app.services.chart_helper import fetch_chart_data, resolve_interval
        resolved = resolve_interval(chart_range, interval)
        if self._fmp_crypto_enabled():
            # Preserved verbatim; reachable via `CRYPTO_PRICE_SOURCE=fmp`.
            if resolved != "daily" or chart_range == "ALL":
                chart_data = await fetch_chart_data(self.fmp, fmp_symbol, chart_range, interval, extended_hours=True)
            else:
                chart_data = self._extract_chart_data(historical, chart_range)
        elif chart_range in _INTRADAY_RANGES:
            # INTRADAY (1D / 1W). This branch used to call FMP unconditionally, which
            # now raises FMPNotEntitledException — so opening a crypto screen on 1D or
            # 1W failed the whole detail build with an exception rather than degrading.
            #
            # CoinGecko's `market_chart` gives 5-minute granularity at days=1 and hourly
            # at days=7, which is the same shape `fetch_chart_data` returned. Timestamps
            # are converted to ET wall-clock by the adapter, because `_bar_minute_of_day`
            # and the iOS `inputDateTimeFormatter` both read bar strings as
            # America/New_York — formatting them as UTC shifts every bar 4-5 hours.
            #
            # ⚠️ Branch on the RANGE, never on `resolved != "daily"`. `DEFAULT_INTERVALS`
            # maps 5Y→"weekly" and ALL→"monthly", so an interval test sends both of those
            # down here too and serves SEVEN DAYS of hourly bars under a "5Y" label —
            # plausible, confidently wrong, and worse than the exception it replaced.
            _days = _INTRADAY_RANGES[chart_range]
            chart_data = self._chart_rows_from(
                await self._cg_history(symbol, _days, intraday=True)
            )
        else:
            # DAILY and long-horizon. `historical` is the CoinGecko series, already
            # capped at CRYPTO_HISTORY_YEARS, and `_extract_chart_data` windows it.
            #
            # 5Y / ALL therefore render the cap (2 years) rather than five years or the
            # whole history. The bars are REAL — only the window is shorter than the pill
            # claims — which is why iOS stops offering those two ranges for crypto and
            # offers 2Y instead. Logged so the clamp is visible rather than assumed.
            if chart_range in _OVER_CAP_RANGES:
                logger.info(
                    "crypto chart: %s requested for %s but CoinGecko Basic caps history "
                    "at %d years — serving the full %d-year window instead",
                    chart_range, symbol, settings.CRYPTO_HISTORY_YEARS,
                    settings.CRYPTO_HISTORY_YEARS,
                )
            chart_data = self._extract_chart_data(historical, chart_range)

        # ── Step 5: Build key statistics ──────────────────────────
        key_stats = self._build_key_statistics(
            price=price,
            market_cap=market_cap,
            volume=volume,
            avg_volume=avg_volume,
            day_high=day_high,
            day_low=day_low,
            year_high=year_high,
            year_low=year_low,
            circulating_supply=circulating_supply,
            total_supply=total_supply,
            max_supply=max_supply_cg or profile_meta.get("max_supply"),
            max_supply_known=max_supply_known,
            fdv=fdv,
            symbol=symbol,
        )

        # ── Step 6: Build performance periods ─────────────────────
        perf_periods = self._build_performance_periods(
            one_month=one_month_return,
            ytd=ytd_return,
            one_year=one_year_return,
            three_year=three_year_return,
            five_year=five_year_return,
            ten_year=ten_year_return,
            all_time=all_time_return,
            bench_1m=bench_1m,
            bench_ytd=bench_ytd,
            bench_1y=bench_1y,
            bench_3y=bench_3y,
            bench_5y=bench_5y,
            bench_10y=bench_10y,
            bench_all_time=bench_all,
            benchmark_label=benchmark_label,
            symbol=symbol,
        )

        # ── Step 7: Build snapshots (AI-enhanced) ─────────────────
        snapshots = await self._build_snapshots(
            symbol=symbol,
            crypto_name=crypto_name,
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

        # ── Step 11: Build benchmark summary (CAGR) ─────────────────
        #
        # Both figures now come from `benchmark_math.overlapping_cagrs`, which measures
        # the asset and its benchmark over the window they SHARE and returns the start of
        # that window so the label can be true. It replaced three nested helpers here that
        # each had a way of being quietly wrong:
        #
        #   * `_cagr` returned a TOTAL return, not an annualised one, whenever the span
        #     was under a year — into a field named `avg_annual_return`.
        #   * on an unparseable date it fell back to `days = len(prices)`, annualising
        #     over a row count.
        #   * `_cagr_aligned` fell back to the benchmark's OWN full history when the
        #     benchmark had nothing at the asset's start, then published that number
        #     under the asset's start date. That is the same defect that made a stock
        #     card read "S&P 500 9.1% · Since Dec 31, 1981" for a 2006-start series.
        #
        # And every failure returned `0.0`, which is indistinguishable from a flat
        # market; `benchmark_available` carries that signal properly now.

        def _filter_from(prices: list, cutoff: str) -> list:
            return [p for p in prices if (p.get("date") or "")[:10] >= cutoff]

        # 🔴 The whole card is suppressed when the history cannot span it.
        #
        # A long-run CAGR needs a long run. On CoinGecko Basic the series is 730 days, and
        # the arithmetic below then lies twice over: `len(hist_5y) >= 252` is TRUE for 730
        # rows, so it takes the five-year branch; and `since_iso == alltime_since_iso`
        # (same series) makes `window_label` resolve to **"All-time"**. The card would
        # read "All-time · Since Sep 2024" — a two-year window wearing an all-time label,
        # beside a benchmark measured over the same two years.
        #
        # `benchmark_summary` is `Optional` on the wire and iOS gates the section on
        # `if let`, so omitting it hides the card cleanly. The builder below is untouched
        # and runs again the moment the source can reach far enough back.
        _benchmark_window_days = self._history_days_cap()
        _suppress_benchmark = (
            not self._fmp_crypto_enabled() and _benchmark_window_days < 365 * 5
        )
        if _suppress_benchmark:
            logger.debug(
                "Crypto %s: benchmark CAGR card suppressed — a %d-day history cannot "
                "express a 5-year or all-time annualised return",
                symbol, _benchmark_window_days,
            )

        bench_rows = (spy_hist if symbol == "BTC" else btc_hist) or []
        bench_name = "S&P 500" if symbol == "BTC" else "Bitcoin (BTC)"
        _label = f"crypto:{symbol}"

        alltime_asset, alltime_bench, alltime_since_iso = overlapping_cagrs(
            historical or [], bench_rows, label=_label,
        )

        # ── 5-year window (primary display when the asset is old enough) ──
        five_year_cutoff = (today - timedelta(days=365 * 5)).isoformat()
        hist_5y = _filter_from(historical or [], five_year_cutoff)

        if len(hist_5y) >= 252:
            asset_annual, bench_annual, since_iso = overlapping_cagrs(
                hist_5y, _filter_from(bench_rows, five_year_cutoff), label=_label,
            )
        else:
            asset_annual, bench_annual, since_iso = (
                alltime_asset, alltime_bench, alltime_since_iso,
            )

        # From the MEASURED window, not the branch — see the note in
        # `stock_overview_service._build_benchmark_summary`. A coin listed last year has
        # enough rows inside the five-year cutoff to take that branch while covering
        # nothing like five years.
        window_label = "All-time" if since_iso == alltime_since_iso else "5-year"

        if _suppress_benchmark or asset_annual is None:
            # Nothing measurable to compare — omit the whole block rather than publish a
            # 0.0% that reads as a real result. iOS gates the section on `if let`.
            benchmark = None
        else:
            # The secondary all-time row only earns its space when it covers a DIFFERENT
            # window from the primary one.
            _show_alltime = (
                alltime_since_iso is not None
                and alltime_since_iso != since_iso
                and alltime_asset is not None
            )
            benchmark = BenchmarkSummaryResponse(
                avg_annual_return=asset_annual,
                # Required float on the wire (iOS decodes a non-optional Double), so
                # "unmeasurable" travels in `benchmark_available` rather than as a null.
                sp_benchmark=bench_annual if bench_annual is not None else 0.0,
                benchmark_name=bench_name,
                since_date=format_since(since_iso),
                window_label=window_label,
                benchmark_available=bench_annual is not None,
                alltime_annual_return=alltime_asset if _show_alltime else None,
                alltime_benchmark=alltime_bench if _show_alltime else None,
                alltime_since_date=(
                    format_since(alltime_since_iso, style="day") if _show_alltime else None
                ),
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

    @staticmethod
    def _chart_rows_from(rows: List[Dict]) -> List[Dict]:
        """Sanitize already-windowed rows into chart points.

        The intraday sibling of `_extract_chart_data`: same output shape and the same
        guarantees, minus the date cutoff, because an intraday CoinGecko series is
        already exactly the requested window.

        Both the finite guard and `_round_close` are load-bearing. A NaN/Inf token in
        any field serializes to invalid JSON and 500s the whole crypto detail, and a
        plain `round(close, 2)` collapses every sub-penny coin (SHIB near $0.00000495,
        PEPE, BONK) to 0.0 — which drew a flat line on the axis.
        """
        from app.services.chart_helper import _finite_or_none

        out: List[Dict] = []
        for p in rows or []:
            # One non-dict element must not crash the whole response — the same guard
            # `_build_related_cryptos` carries, for the same reason. A malformed upstream
            # payload (a bare string, a null, a nested list) would otherwise raise
            # AttributeError from `.get` and 500 the entire crypto detail screen.
            if not isinstance(p, dict):
                continue
            close = _finite_or_none(p.get("close") or p.get("adjClose"))
            if close is None or close <= 0:
                continue
            out.append({
                "date": p.get("date"),
                "open": _finite_or_none(p.get("open")),
                "high": _finite_or_none(p.get("high")),
                "low": _finite_or_none(p.get("low")),
                "close": _round_close(close),
                "volume": _finite_or_none(p.get("volume")),
            })
        return out

    def _extract_chart_data(
        self, historical: List[Dict], chart_range: str
    ) -> List[Dict]:
        if not historical:
            return []

        from app.services.chart_helper import _finite_or_none, daily_range_days

        # The visible window PLUS the MA(200) warm-up, from the one shared definition.
        # This service used to carry its own copy of the range map with NO warm-up in it,
        # so the client's `TickerChartView.warmupCount` resolved to 0 and the moving
        # average never drew on a daily chart. index_service and stock_overview_service
        # always had the warm-up; these three never did.
        today = datetime.now(tz=timezone.utc).date()
        cutoff = (today - timedelta(days=daily_range_days(chart_range))).isoformat()

        result = []
        for p in historical:
            # `historical` is RAW upstream JSON on the FMP branch (`raw.get("historical")`),
            # so a malformed payload can carry a non-dict element. Skip it rather than
            # raising AttributeError out of `.get` and losing the whole chart.
            if not isinstance(p, dict):
                continue
            if (p.get("date") or "") >= cutoff:
                # A non-finite (Inf) close slips past a bare `close > 0`, and raw
                # open/high/low/volume can carry a NaN/Inf token — either serializes
                # to invalid JSON and 500s the whole crypto detail. Sanitize all.
                close = _finite_or_none(p.get("close") or p.get("adjClose"))
                if close is not None and close > 0:
                    result.append({
                        "date": p.get("date"),
                        "open": _finite_or_none(p.get("open")),
                        "high": _finite_or_none(p.get("high")),
                        "low": _finite_or_none(p.get("low")),
                        # Magnitude-aware, NOT `round(close, 2)`: SHIB trades near
                        # $0.00000495, so two decimals collapsed every close to 0.0 and
                        # the default 3M crypto chart drew a flat line on the axis for
                        # every sub-penny coin (SHIB / PEPE / BONK). OHLC is left
                        # unrounded — it is already finite-guarded above.
                        "close": _round_close(close),
                        "volume": _finite_or_none(p.get("volume")),
                    })
        return result

    # ── Key statistics builder ───────────────────────────────────

    def _build_supply_stats(
        self, *, circulating_supply, total_supply, max_supply,
        fdv, market_cap, avg_volume, symbol,
        max_supply_known: bool,   # required — see `_build_key_statistics`
    ) -> List[KeyStatisticItem]:
        """Build supply column, skipping redundant stats."""
        stats = []

        stats.append(KeyStatisticItem(
            label="Circulating Supply",
            value=_fmt_supply(circulating_supply, symbol),
        ))

        # Only show Total Supply if it differs from Circulating
        # Both are Optional now (an absent CoinGecko field stays absent), so the
        # subtraction needs BOTH present — `abs(x - None)` is a TypeError that would
        # 500 the whole detail response.
        if (total_supply is not None and circulating_supply is not None
                and total_supply and abs(total_supply - circulating_supply) > 1):
            stats.append(KeyStatisticItem(
                label="Total Supply",
                value=_fmt_supply(total_supply, symbol),
            ))

        # Max Supply — THREE states, not two.
        #
        # "No Cap" is a claim about the coin's monetary policy, and publishing it because
        # `/coins/{id}` degraded is a fabricated fact, not a formatting nicety. It only
        # renders when the absence was actually MEASURED (`max_supply_known`); otherwise
        # the row degrades to "—" like every other unknown on this screen.
        if max_supply:
            _max_supply_value = _fmt_supply(max_supply, symbol)
        elif max_supply_known:
            _max_supply_value = "No Cap"
        else:
            _max_supply_value = "—"
        stats.append(KeyStatisticItem(label="Max Supply", value=_max_supply_value))

        stats.append(KeyStatisticItem(
            label="Fully Diluted Val.",
            # Both are Optional now; `_fmt(None)` renders "—", so an unknown FDV with an
            # unknown market cap degrades instead of printing $0.00.
            value=_fmt(fdv) if fdv else _fmt(market_cap),
        ))

        stats.append(KeyStatisticItem(
            label="Avg. Volume (30D)",
            value=_fmt(avg_volume),
        ))

        return stats

    def _build_key_statistics(
        self, *, price, market_cap, volume, avg_volume,
        day_high, day_low, year_high, year_low,
        circulating_supply, total_supply, max_supply, fdv, symbol,
        # No default, deliberately. A default of True is fail-OPEN: dropping the argument
        # anywhere along the thread silently restores the fabricated "No Cap". Required
        # makes that a TypeError instead.
        max_supply_known: bool,
    ) -> List[KeyStatisticsGroupResponse]:
        # None-safe on BOTH operands: either being unknown makes the ratio unknown, and
        # "0.00%" is a claim (a coin with no trading) rather than an absence. `market_cap`
        # and `volume` are Optional now, so a bare `market_cap > 0` would also TypeError.
        vol_mkt_ratio = (
            (volume / market_cap * 100)
            if (volume is not None and market_cap is not None and market_cap > 0)
            else None
        )

        return [
            # Column 1: Price & Volume
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="Market Cap", value=_fmt(market_cap)),
                KeyStatisticItem(label="24h Volume", value=_fmt(volume)),
                # NOT `_pct`: that prefixes a sign ("+3.45%"), which reads as a CHANGE.
                # This is a ratio. Keep the shipped format and only handle unknown.
                KeyStatisticItem(
                    label="Volume/Mkt Cap",
                    value=f"{vol_mkt_ratio:.2f}%" if vol_mkt_ratio is not None else "—",
                ),
                KeyStatisticItem(label="24h High", value=_fmt(day_high)),
                KeyStatisticItem(label="24h Low", value=_fmt(day_low)),
            ]),
            # Column 2: Supply (CoinGecko provides accurate supply data)
            KeyStatisticsGroupResponse(statistics=self._build_supply_stats(
                circulating_supply=circulating_supply,
                total_supply=total_supply,
                max_supply=max_supply,
                max_supply_known=max_supply_known,
                fdv=fdv,
                market_cap=market_cap,
                avg_volume=avg_volume,
                symbol=symbol,
            )),
            # Column 3: Historical (52-week, from daily history only — see above)
            KeyStatisticsGroupResponse(statistics=[
                # `year_high` / `year_low` are Optional now — None means the 52-week
                # window could not be derived, and every row below renders "—".
                # `is not None and > 0` rather than a bare truthiness test so the
                # guard survives a mutation that reintroduces a 0.0 sentinel.
                KeyStatisticItem(label="52-Week High", value=_fmt(year_high)),
                KeyStatisticItem(
                    label="From 52W High",
                    value=_pct(
                        ((price - year_high) / year_high * 100)
                        if year_high is not None and year_high > 0 else None
                    ),
                ),
                KeyStatisticItem(label="52-Week Low", value=_fmt(year_low)),
                KeyStatisticItem(
                    label="From 52W Low",
                    value=_pct(
                        ((price - year_low) / year_low * 100)
                        if year_low is not None and year_low > 0 else None
                    ),
                    is_highlighted=True,
                ),
                KeyStatisticItem(
                    label="52-Week % Range",
                    value=(
                        f"{((year_high - year_low) / year_low * 100):.2f}%"
                        if year_low is not None and year_low > 0
                        and year_high is not None and year_high > 0
                        else "—"
                    ),
                ),
            ]),
        ]

    # ── Auto-profile builder from CoinGecko data ──────────────────

    def _build_profile_from_coingecko(
        self, symbol: str, coin_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Build a profile dict from CoinGecko /coins/{id} response.
        Used for coins without a curated profile in _CRYPTO_PROFILES.
        Returns same shape as _CRYPTO_PROFILES entries.
        """
        name = coin_data.get("name", symbol)

        # Description — truncate to ~500 chars at sentence boundary
        raw_desc = coin_data.get("description", {}).get("en", "")
        if raw_desc:
            # Strip HTML tags
            import re
            clean = re.sub(r"<[^>]+>", "", raw_desc).strip()
            if len(clean) > 500:
                # Cut at sentence boundary
                cut = clean[:500]
                last_period = cut.rfind(".")
                if last_period > 200:
                    clean = cut[: last_period + 1]
                else:
                    clean = cut.rstrip() + "..."
            description = clean
        else:
            description = f"{name} ({symbol}) is a cryptocurrency."

        # Launch date
        genesis = coin_data.get("genesis_date")
        launch_date = genesis if genesis else "Unknown"

        # Consensus / hashing
        hashing = coin_data.get("hashing_algorithm")
        categories = coin_data.get("categories", []) or []
        consensus = "Unknown"
        if hashing:
            consensus = f"{hashing}"
        else:
            for cat in categories:
                cat_lower = (cat or "").lower()
                if "proof of stake" in cat_lower:
                    consensus = "Proof of Stake (PoS)"
                    break
                elif "proof of work" in cat_lower:
                    consensus = "Proof of Work (PoW)"
                    break

        # Blockchain — derive from categories
        blockchain = name
        for cat in categories:
            cat_lower = (cat or "").lower()
            if "layer 2" in cat_lower or "l2" in cat_lower:
                blockchain = f"{name} (Layer 2)"
                break
            elif "layer 1" in cat_lower or "l1" in cat_lower:
                blockchain = name
                break

        # Links
        links = coin_data.get("links", {})
        homepages = links.get("homepage", [])
        website = ""
        for hp in homepages:
            if hp:
                website = hp.replace("https://", "").replace("http://", "").rstrip("/")
                break
        whitepaper = links.get("whitepaper") or None

        # Max supply
        md = coin_data.get("market_data", {})
        max_supply = md.get("max_supply")

        return {
            "name": name,
            "description": description,
            "launch_date": launch_date,
            "consensus_mechanism": consensus,
            "blockchain": blockchain,
            "website": website,
            "whitepaper": whitepaper,
            "max_supply": max_supply,
        }

    # ── Performance periods builder ──────────────────────────────

    def _build_performance_periods(
        self, *, one_month, ytd, one_year, three_year, five_year,
        ten_year=None, all_time,
        bench_1m, bench_ytd, bench_1y, bench_3y, bench_5y,
        bench_10y=None, bench_all_time,
        benchmark_label, symbol=None,
    ) -> List[PerformancePeriodResponse]:
        periods = []
        entries = [
            ("1 Month", one_month, bench_1m),
            ("YTD", ytd, bench_ytd),
            ("1 Year", one_year, bench_1y),
            ("3 Years", three_year, bench_3y),
            ("5 Years", five_year, bench_5y),
        ]
        # Prefer a real "10 Years" row; if the coin lacks 10y of daily history,
        # surface the already-computed all-time return (previously discarded — the
        # `all_time` kwarg was accepted but never read, so most altcoins showed NO
        # long-horizon row) under an "All Time" label instead of dropping it. No
        # window-aligned benchmark exists for a since-inception span, so leave
        # vs-market empty rather than comparing to the benchmark's full history.
        if ten_year is not None:
            entries.append(("10 Years", ten_year, bench_10y))
        elif all_time is not None:
            entries.append(("All Time", all_time, None))
        for label, asset_val, bench_val in entries:
            # Skip periods where the crypto doesn't have enough history
            if asset_val is None:
                continue

            asset_ret = round(asset_val, 2)
            bench_ret = round(bench_val, 2) if bench_val is not None else None
            vs_market = round(asset_ret - bench_ret, 2) if bench_ret is not None else None

            periods.append(PerformancePeriodResponse(
                label=label,
                change_percent=asset_ret,
                vs_market_percent=vs_market,
                sp_return_percent=bench_ret,
                benchmark_label=benchmark_label,
            ))
        return periods

    # ── Snapshots builder (with Gemini AI) ───────────────────────

    async def _build_snapshots(
        self, *, symbol, crypto_name, profile_meta,
    ) -> List[CryptoSnapshotResponse]:
        """
        Return snapshots immediately — never block the response.

        Lookup order:
          1. In-memory cache (fast)
          2. Supabase crypto_snapshots table (permanent)
          3. Template defaults (instant) + fire Gemini background → saves to DB
        """
        cache_key = f"crypto_snapshots_{symbol}"

        # Tier 1: in-memory
        cached = _cache_get(cache_key)
        if cached:
            return cached

        # Tier 2: Supabase (permanent)
        db_snapshots = await asyncio.to_thread(self._load_snapshots_db, symbol)
        if db_snapshots and len(db_snapshots) == 4:
            _cache_set(cache_key, db_snapshots)
            return db_snapshots

        # Tier 3: return defaults immediately, generate AI in background
        defaults = self._default_snapshots(symbol, crypto_name, profile_meta)
        _cache_set(cache_key, defaults)

        if symbol not in _ai_refresh_inflight:
            _ai_refresh_inflight.add(symbol)
            _spawn_background(
                self._generate_ai_snapshots(
                    symbol=symbol,
                    crypto_name=crypto_name,
                    profile_meta=profile_meta,
                    cache_key=cache_key,
                ),
                name=f"crypto-ai-snapshots:{symbol}",
            )

        return defaults

    def _load_snapshots_db(self, symbol: str) -> Optional[List[CryptoSnapshotResponse]]:
        """Load snapshots from Supabase (permanent storage)."""
        try:
            rows = (
                self.supabase.table("crypto_snapshots")
                .select("category, paragraphs")
                .eq("symbol", symbol)
                .execute()
            )
            if rows.data and len(rows.data) >= 4:
                # Order: Origin, Tokenomics, Next Big Moves, Risks
                category_order = {
                    "Origin and Technology": 0,
                    "Tokenomics": 1,
                    "Next Big Moves": 2,
                    "Risks": 3,
                }
                sorted_rows = sorted(
                    rows.data,
                    key=lambda r: category_order.get(r.get("category", ""), 99),
                )
                return [
                    CryptoSnapshotResponse(
                        category=r["category"],
                        paragraphs=r["paragraphs"],
                    )
                    for r in sorted_rows
                ]
        except Exception as e:
            logger.debug(f"Snapshots DB read failed for {symbol}: {e}")
        return None

    def _save_snapshots_db(self, symbol: str, snapshots: List[CryptoSnapshotResponse]) -> None:
        """Save snapshots to Supabase permanently."""
        try:
            for snap in snapshots:
                self.supabase.table("crypto_snapshots").upsert(
                    {
                        "symbol": symbol,
                        "category": snap.category,
                        "paragraphs": snap.paragraphs,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        # Provider-neutral: crypto_snapshots is anon-readable.
                        "generated_by": "Cay AI",
                    },
                    on_conflict="symbol,category",
                ).execute()
            logger.info(f"Saved {len(snapshots)} snapshots to DB for {symbol}")
        except Exception as e:
            logger.warning(f"Snapshots DB write failed for {symbol}: {e}")

    async def _generate_ai_snapshots(
        self, *, symbol, crypto_name, profile_meta, cache_key,
    ) -> None:
        """
        Background task: generate stable AI snapshots via Gemini.
        Focuses on technology, tokenomics, catalysts, risks — NOT volatile market data.
        Result saved to Supabase permanently.
        """
        try:
            gemini = get_gemini_client()

            prompt = f"""You are a cryptocurrency analyst writing educational content about {crypto_name} ({symbol}).

Background:
- Consensus mechanism: {profile_meta.get('consensus_mechanism', 'Unknown')}
- Launch date: {profile_meta.get('launch_date', 'Unknown')}
- Blockchain: {profile_meta.get('blockchain', 'Unknown')}
- Max supply: {profile_meta.get('max_supply', 'No cap')}
- Description: {profile_meta.get('description', '')[:300]}

Generate content for these 4 categories.

FORMATTING RULES:
- Each paragraph: 2-3 sentences max. Keep it tight.
- Use 2 to 4 paragraphs per category — choose the count that best fits the content. Some topics need more depth, others are better kept brief. Do NOT use the same count for every category.

Focus on STABLE knowledge: how the technology works, the economic model, upcoming developments, and structural risks. Do NOT mention current prices, market cap, volume, or any volatile market data — this content will be displayed for months.

Write in a confident, conversational tone — like a sharp analyst briefing a smart friend. Be specific: real names, dates, version numbers. No filler sentences.

Separate each category with "===CATEGORY===" followed by the category name.

===CATEGORY===Origin and Technology
[who built it and when, core tech innovation, consensus mechanism, what makes it unique]

===CATEGORY===Tokenomics
[supply model, fee/burn mechanics, staking/yield, token utility]

===CATEGORY===Next Big Moves
[upcoming upgrades, ecosystem catalysts, institutional signals]

===CATEGORY===Risks
[regulatory risks, technical/security risks, competition, adoption challenges]"""

            ai_response = await gemini.generate_text(
                prompt=prompt,
                # Wrapped: IDENTITY_RULE + ADVICE_BOUNDARY (see persona_config).
                system_instruction=neutral_system_instruction(
                    "You are a senior crypto analyst providing sharp, concise educational content. "
                    "Write factually. Keep paragraphs to 2-3 sentences max — no fluff. "
                    "Use 2 to 4 paragraphs per section — let the content dictate the count. "
                    "Mention specific names, dates, and technical details. "
                    "Do NOT include any current market prices or volatile data."
                ),
                model_name="gemini-2.5-flash",
            )

            text = ai_response.get("text", "")
            snapshots = self._parse_ai_snapshots(text)

            if len(snapshots) == 4:
                # Save to memory cache
                _cache_set(cache_key, snapshots)
                # Save to Supabase permanently
                await asyncio.to_thread(self._save_snapshots_db, symbol, snapshots)
                logger.info(f"Background: AI snapshots generated and saved for {symbol}")

        except Exception as e:
            logger.warning(
                "Crypto snapshot refresh failed for %s (%s: %s) — defaults stand",
                symbol, type(e).__name__, e,
            )
        finally:
            _ai_refresh_inflight.discard(symbol)

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
                    paragraphs=paragraphs[:4],
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
        from app.services.chart_helper import _finite_or_none

        # Create lookup by FMP symbol
        quote_map: Dict[str, Dict] = {}
        for q in raw_quotes:
            if not isinstance(q, dict):
                continue  # one non-dict element must not crash the whole response
            fmp_sym = (q.get("symbol") or "").upper()
            quote_map[fmp_sym] = q

        result = []
        for sym in expected_symbols:
            fmp_sym = f"{sym}USD"
            q = quote_map.get(fmp_sym, {})
            # Name, in order of authority. ⚠️ The CoinGecko **id** is NOT a name source.
            #
            # It used to be the fallback (`cg_id.replace("-", " ").title()`), and an id is a
            # historical, immutable SLUG — so that published the project's OLD name as the
            # coin's name, with no way to tell from the screen that it was wrong:
            #     SNX   → "havven"                  → "Havven"      (it is Synthetix)
            #     STX   → "blockstack"              → "Blockstack"  (it is Stacks)
            #     MATIC → "polygon-ecosystem-token" → "Polygon Ecosystem Token"
            #     BNB   → "binancecoin"             → "Binancecoin"
            # …and it mangled the rest ("Curve Dao Token", "Crypto Com Chain", "Fetch Ai").
            #
            # The quote row already carries CoinGecko's CURRENT display name — `_shape` is
            # given `row["name"]` from `/coins/markets` — so the correct value was one
            # `.get` away the whole time. Falling back to the bare symbol is honest; a
            # stale project name is not.
            name = _CRYPTO_PROFILES.get(sym, {}).get("name")
            if not name:
                q_name = q.get("name")
                name = q_name.strip() if isinstance(q_name, str) and q_name.strip() else sym
            # `x or 0` does NOT guard a non-finite: an FMP NaN token is truthy, so it
            # sails through and lands in these REQUIRED response floats — Starlette then
            # renders with allow_nan=False and 500s the WHOLE crypto detail screen from
            # inside the renderer, past the endpoint's try/except. commodity_service's
            # `related_commodities` was fixed for exactly this; the crypto twin was not.
            # Note the singular `changePercentage` is FMP /stable's spelling and is read
            # FIRST here; the plural is the dead /api/v3 fallback.
            rel_price = _finite_or_none(q.get("price"))
            rel_change = _finite_or_none(q.get("changePercentage"))
            if rel_change is None:
                rel_change = _finite_or_none(q.get("changesPercentage"))
            # ⚠️ OMIT the row rather than zeroing it. This used to append unconditionally
            # with `price = rel_price if ... else 0`, so when FMP started refusing crypto
            # pairs all six related coins rendered "$0.00 +0.00%" — six confident, wrong
            # prices. `RelatedCryptoDTO.price` is a non-Optional Double on iOS, so a null
            # is not available and omission from the array is the only honest signal;
            # the strip already renders a shorter list correctly.
            if rel_price is None or rel_price <= 0:
                logger.warning(
                    "Related crypto %s has no usable price — omitting the row rather "
                    "than rendering $0.00", sym,
                )
                continue
            result.append(RelatedCryptoResponse(
                symbol=sym,
                name=name,
                price=rel_price,
                change_percent=rel_change if rel_change is not None else 0,
            ))

        return result

    # ── News builder ─────────────────────────────────────────────

    def _build_news(
        self, raw_articles: List[Dict],
    ) -> List[CryptoNewsArticleResponse]:
        articles = []
        for item in raw_articles[:10]:
            # FMP's publishedDate is a naive America/New_York wall clock, but this field is
            # a timestamp the client renders, and the News tab now serves a true UTC instant
            # (news_cache_service._sanitize_published_at). Emitting the raw string here made
            # the same article read 4h apart on two screens. These detail builders bypass
            # news_cache_service entirely, so the ingest-level fix does NOT reach them.
            # Keep the `or ""` fallback: the field is REQUIRED and a None 500s the response.
            _raw_pub = item.get("publishedDate") or item.get("published_date") or ""
            _pub_dt = to_utc_instant(_raw_pub)
            published = _pub_dt.isoformat() if _pub_dt is not None else _raw_pub
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
