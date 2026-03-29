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
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.coingecko import get_coingecko_client, CoinGeckoClient
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


def _compute_all_time_return(prices: List[Dict]) -> Optional[float]:
    """Compute % return from first available price to latest."""
    if not prices or len(prices) < 2:
        return None
    start = prices[0].get("close") or prices[0].get("adjClose")
    end = prices[-1].get("close") or prices[-1].get("adjClose")
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

    # ── Two-tier cache for CoinGecko fundamentals ────────────────

    async def _get_coin_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """
        Two-tier cache for CoinGecko coin data:
          Tier 1: in-memory (5 min TTL)
          Tier 2: Supabase crypto_fundamentals_cache (6h TTL)
          Miss:   CoinGecko API call → cache in both tiers
        """
        mem_key = f"cg_fundamentals:{symbol}"

        # Tier 1: in-memory
        cached = _cache_get(mem_key, _CACHE_TTL_SECONDS)
        if cached is not None:
            logger.debug(f"CoinGecko mem cache hit for {symbol}")
            return cached

        # Tier 2: Supabase
        db_data = await asyncio.to_thread(self._check_crypto_cache_db, symbol)
        if db_data is not None:
            logger.debug(f"CoinGecko DB cache hit for {symbol}")
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
        """Check Supabase crypto_fundamentals_cache (6h TTL)."""
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
                        return row.data[0].get("response_json")
                    logger.debug(f"Crypto DB cache expired for {symbol} ({age_hours:.1f}h)")
        except Exception as e:
            logger.warning(f"Crypto DB cache read failed for {symbol}: {e}")
        return None

    def _upsert_crypto_cache_db(self, symbol: str, data: Dict[str, Any]) -> None:
        """Upsert CoinGecko response into Supabase cache."""
        try:
            self.supabase.table("crypto_fundamentals_cache").upsert(
                {
                    "symbol": symbol,
                    "response_json": data,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="symbol",
            ).execute()
        except Exception as e:
            logger.warning(f"Crypto DB cache write failed for {symbol}: {e}")

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

        # ── Step 1: Parallel fetches (CoinGecko + FMP) ────────────
        today = datetime.now(tz=timezone.utc).date()
        from_date = (today - timedelta(days=365 * 6)).isoformat()  # 6 years for 5Y + All Time
        to_date = today.isoformat()

        # Related crypto symbols
        related_symbols = _RELATED_CRYPTOS.get(symbol, _DEFAULT_RELATED)
        related_symbols = [s for s in related_symbols if s != symbol][:6]
        related_fmp_symbols = [f"{s}USD" for s in related_symbols]

        # CoinGecko for fundamentals, FMP for chart/news/related
        coin_data_task = self._get_coin_fundamentals(symbol)
        hist_task = self.fmp.get_historical_prices(fmp_symbol, from_date, to_date)
        news_task = self.fmp.get_stock_news(fmp_symbol, limit=10)
        related_task = self.fmp.get_batch_quotes(related_fmp_symbols)

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
        historical.sort(key=lambda p: p.get("date", ""))

        # ── Step 2: Extract data from CoinGecko ──────────────────
        md = coin_data.get("market_data", {}) if isinstance(coin_data, dict) else {}
        price = md.get("current_price", {}).get("usd", 0) or 0
        change = md.get("price_change_24h", 0) or 0
        change_pct = md.get("price_change_percentage_24h", 0) or 0
        day_high = md.get("high_24h", {}).get("usd", 0) or 0
        day_low = md.get("low_24h", {}).get("usd", 0) or 0
        volume = md.get("total_volume", {}).get("usd", 0) or 0
        market_cap = md.get("market_cap", {}).get("usd", 0) or 0
        circulating_supply = md.get("circulating_supply", 0) or 0
        total_supply = md.get("total_supply", 0) or 0
        max_supply_cg = md.get("max_supply")  # None if no cap
        fdv = md.get("fully_diluted_valuation", {}).get("usd", 0) or 0

        # 52-week from historical data (more accurate than ATH/ATL)
        year_high = 0
        year_low = 0
        if historical:
            one_year_ago = (today - timedelta(days=365)).isoformat()
            year_prices = [
                p for p in historical
                if p.get("date", "") >= one_year_ago
            ]
            if year_prices:
                highs = [p.get("high", 0) or 0 for p in year_prices]
                lows = [p.get("low", 0) or 0 for p in year_prices if (p.get("low", 0) or 0) > 0]
                year_high = max(highs) if highs else 0
                year_low = min(lows) if lows else 0

        # Fallback to CoinGecko ATH/ATL if no historical data
        if year_high == 0:
            year_high = md.get("ath", {}).get("usd", 0) or 0
        if year_low == 0:
            year_low = md.get("atl", {}).get("usd", 0) or 0

        # Avg volume from FMP (CoinGecko doesn't provide 30D avg directly)
        # Compute 30-day average volume from historical data
        avg_volume = volume  # fallback
        if historical:
            last_30 = historical[-30:] if len(historical) >= 30 else historical
            vols = [p.get("volume", 0) or 0 for p in last_30 if (p.get("volume", 0) or 0) > 0]
            if vols:
                avg_volume = sum(vols) / len(vols)

        # ── Auto-generate profile from CoinGecko if no curated profile ──
        if not _has_curated_profile and isinstance(coin_data, dict):
            profile_meta = self._build_profile_from_coingecko(symbol, coin_data)
            crypto_name = profile_meta.get("name", symbol)

        # ── Step 3: Compute derived stats ─────────────────────────
        # Prefer CoinGecko's pre-computed percentages, fall back to historical
        one_month_return = md.get("price_change_percentage_30d", None) or _compute_return(historical, 30)
        one_year_return = md.get("price_change_percentage_1y", None) or _compute_return(historical, 365)
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
        all_time_return = _compute_all_time_return(historical)

        # ── Step 3b: Fetch benchmark data ────────────────────────
        # Altcoins benchmark vs BTC; BTC benchmarks vs S&P 500
        bench_1m = bench_ytd = bench_1y = bench_3y = bench_5y = bench_all = None
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
                    spy_hist.sort(key=lambda p: p.get("date", ""))
                    if spy_hist:
                        _cache_set(spy_cache_key, spy_hist)
                except Exception as e:
                    logger.warning(f"SPY historical fetch failed: {e}")
                    spy_hist = []
            if spy_hist:
                bench_1m = _compute_return(spy_hist, 30)
                bench_ytd = _compute_ytd_return(spy_hist)
                bench_1y = _compute_return(spy_hist, 365)
                bench_3y = _compute_return(spy_hist, 252 * 3) if len(spy_hist) > 252 * 3 else None
                bench_5y = _compute_return(spy_hist, 252 * 5) if len(spy_hist) > 252 * 5 else None
                bench_all = _compute_all_time_return(spy_hist)
        else:
            benchmark_label = "BTC"
            # Fetch BTC data from CoinGecko (likely already cached)
            btc_coin_data = await self._get_coin_fundamentals("BTC")
            btc_md = btc_coin_data.get("market_data", {}) if isinstance(btc_coin_data, dict) else {}
            bench_1m = btc_md.get("price_change_percentage_30d")
            bench_1y = btc_md.get("price_change_percentage_1y")
            # For YTD, 3Y, 5Y, All Time — compute from BTC historical
            btc_fmp_symbol = "BTCUSD"
            btc_hist_cache_key = f"btc_hist:{from_date}:{to_date}"
            btc_hist = _cache_get(btc_hist_cache_key, 3600)
            if btc_hist is None:
                try:
                    btc_raw = await self.fmp.get_historical_prices(btc_fmp_symbol, from_date, to_date)
                    if isinstance(btc_raw, dict):
                        btc_hist = btc_raw.get("historical", [])
                    elif isinstance(btc_raw, list):
                        btc_hist = btc_raw
                    else:
                        btc_hist = []
                    btc_hist.sort(key=lambda p: p.get("date", ""))
                    if btc_hist:
                        _cache_set(btc_hist_cache_key, btc_hist)
                except Exception as e:
                    logger.warning(f"BTC historical fetch failed: {e}")
                    btc_hist = []
            if btc_hist:
                bench_ytd = _compute_ytd_return(btc_hist)
                bench_3y = _compute_return(btc_hist, 365 * 3) if len(btc_hist) > 365 * 3 else None
                bench_5y = _compute_return(btc_hist, 365 * 5) if len(btc_hist) > 365 * 5 else None
                bench_all = _compute_all_time_return(btc_hist)

        # ── Step 4: Build chart data ──────────────────────────────
        from app.services.chart_helper import fetch_chart_data, resolve_interval
        resolved = resolve_interval(chart_range, interval)
        if resolved != "daily" or chart_range == "ALL":
            chart_data = await fetch_chart_data(self.fmp, fmp_symbol, chart_range, interval)
        else:
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
            all_time=all_time_return,
            bench_1m=bench_1m,
            bench_ytd=bench_ytd,
            bench_1y=bench_1y,
            bench_3y=bench_3y,
            bench_5y=bench_5y,
            bench_all_time=bench_all,
            benchmark_label=benchmark_label,
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
        # CAGR = ((end/start)^(1/years) - 1) * 100
        # Use 1-year return if less than 1 year of data

        def _cagr(prices: list) -> float:
            """Compute compound annual growth rate from price history."""
            if not prices or len(prices) < 2:
                return 0.0
            start = prices[0].get("close") or prices[0].get("adjClose") or 0
            end = prices[-1].get("close") or prices[-1].get("adjClose") or 0
            if start <= 0 or end <= 0:
                return 0.0
            years = len(prices) / 365.0
            if years < 1:
                # Less than 1 year — just return total return
                return round(((end - start) / start) * 100, 1)
            return round(((end / start) ** (1 / years) - 1) * 100, 1)

        asset_annual = _cagr(historical)

        # Benchmark CAGR
        bench_annual = 0.0
        if symbol == "BTC" and spy_hist:
            bench_annual = _cagr(spy_hist)
        elif symbol != "BTC" and btc_hist:
            bench_annual = _cagr(btc_hist)

        benchmark = BenchmarkSummaryResponse(
            avg_annual_return=asset_annual,
            sp_benchmark=bench_annual,
            benchmark_name="Bitcoin (BTC)" if symbol != "BTC" else "S&P 500",
            since_date=profile_meta.get("launch_date") or None,
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

    def _build_supply_stats(
        self, *, circulating_supply, total_supply, max_supply,
        fdv, market_cap, avg_volume, symbol,
    ) -> List[KeyStatisticItem]:
        """Build supply column, skipping redundant stats."""
        stats = []

        stats.append(KeyStatisticItem(
            label="Circulating Supply",
            value=_fmt_supply(circulating_supply, symbol),
        ))

        # Only show Total Supply if it differs from Circulating
        if total_supply and abs(total_supply - circulating_supply) > 1:
            stats.append(KeyStatisticItem(
                label="Total Supply",
                value=_fmt_supply(total_supply, symbol),
            ))

        # Max Supply
        stats.append(KeyStatisticItem(
            label="Max Supply",
            value=_fmt_supply(max_supply, symbol) if max_supply else "No Cap",
        ))

        stats.append(KeyStatisticItem(
            label="Fully Diluted Val.",
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
    ) -> List[KeyStatisticsGroupResponse]:
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
            # Column 2: Supply (CoinGecko provides accurate supply data)
            KeyStatisticsGroupResponse(statistics=self._build_supply_stats(
                circulating_supply=circulating_supply,
                total_supply=total_supply,
                max_supply=max_supply,
                fdv=fdv,
                market_cap=market_cap,
                avg_volume=avg_volume,
                symbol=symbol,
            )),
            # Column 3: Historical (52-week from FMP historical + CoinGecko fallback)
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
                KeyStatisticItem(
                    label="52-Week % Range",
                    value=f"{((year_high - year_low) / year_low * 100):.2f}%" if year_low > 0 and year_high > 0 else "—",
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
        self, *, one_month, ytd, one_year, three_year, five_year, all_time,
        bench_1m, bench_ytd, bench_1y, bench_3y, bench_5y, bench_all_time,
        benchmark_label,
    ) -> List[PerformancePeriodResponse]:
        periods = []
        for label, asset_val, bench_val in [
            ("1 Month", one_month, bench_1m),
            ("YTD", ytd, bench_ytd),
            ("1 Year", one_year, bench_1y),
            ("3 Years", three_year, bench_3y),
            ("5 Years", five_year, bench_5y),
            ("All Time", all_time, bench_all_time),
        ]:
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

        asyncio.create_task(self._generate_ai_snapshots(
            symbol=symbol,
            crypto_name=crypto_name,
            profile_meta=profile_meta,
            cache_key=cache_key,
        ))

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
                        "generated_by": "gemini-2.5-flash",
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
                system_instruction=(
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
            logger.warning(f"Background Gemini failed for {symbol}: {e}")

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
        # Create lookup by FMP symbol
        quote_map: Dict[str, Dict] = {}
        for q in raw_quotes:
            fmp_sym = (q.get("symbol") or "").upper()
            quote_map[fmp_sym] = q

        result = []
        for sym in expected_symbols:
            fmp_sym = f"{sym}USD"
            q = quote_map.get(fmp_sym, {})
            # Use curated name, or derive from CoinGecko ID (e.g. "bitcoin" → "Bitcoin")
            name = _CRYPTO_PROFILES.get(sym, {}).get("name")
            if not name:
                from app.integrations.coingecko import SYMBOL_TO_COINGECKO_ID
                cg_id = SYMBOL_TO_COINGECKO_ID.get(sym, "")
                name = cg_id.replace("-", " ").title() if cg_id else sym
            result.append(RelatedCryptoResponse(
                symbol=sym,
                name=name,
                price=q.get("price") or 0,
                change_percent=q.get("changesPercentage") or q.get("changePercentage") or 0,
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
