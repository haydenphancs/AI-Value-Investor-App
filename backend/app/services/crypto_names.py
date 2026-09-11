"""Display names for the coins the app can price — ONE table, no slug fallback.

Every key of `SYMBOL_TO_COINGECKO_ID` has an explicit, current name here. It used to be
`cg_id.replace("-", " ").title()` for anything without an override, and a CoinGecko id is
a historical SLUG, not a name: `beam-2` rendered "Beam 2", `xdce-crowd-sale` "Xdce Crowd
Sale", `okb` "Okb", `ether-fi` "Ether Fi", `zksync` "Zksync". Twenty-five of the 111 coins
surfaced that way in search. The related-coins card was fixed for the same defect earlier
(it now reads the name from the `/coins/markets` row); search is zero-cost and has no
markets row, so it needs the table.

`tests/test_crypto_search_names.py` fails the build on the next omission.
"""
from __future__ import annotations

from typing import Dict, Optional

CRYPTO_NAMES: Dict[str, str] = {
    '1INCH': '1inch',
    'AAVE': 'Aave',
    'ADA': 'Cardano',
    'ALGO': 'Algorand',
    'APT': 'Aptos',
    'AR': 'Arweave',
    'ARB': 'Arbitrum',
    'ATOM': 'Cosmos',
    'AVAX': 'Avalanche',
    'AXS': 'Axie Infinity',
    'BAL': 'Balancer',
    'BCH': 'Bitcoin Cash',
    'BEAM': 'Beam',
    'BGB': 'Bitget Token',
    'BLUR': 'Blur',
    'BNB': 'BNB',
    'BONK': 'Bonk',
    'BTC': 'Bitcoin',
    'CAKE': 'PancakeSwap',
    'CELO': 'Celo',
    'CFX': 'Conflux',
    'CHZ': 'Chiliz',
    'COMP': 'Compound',
    'CRO': 'Cronos',
    'CRV': 'Curve DAO',
    'DASH': 'Dash',
    'DCR': 'Decred',
    'DOGE': 'Dogecoin',
    'DOT': 'Polkadot',
    'DYDX': 'dYdX',
    'EIGEN': 'EigenLayer',
    'ENA': 'Ethena',
    'ENS': 'Ethereum Name Service',
    'EOS': 'EOS',
    'ETC': 'Ethereum Classic',
    'ETH': 'Ethereum',
    'ETHFI': 'Ether.fi',
    'FET': 'Artificial Superintelligence Alliance',
    'FIL': 'Filecoin',
    'FLOKI': 'Floki',
    'FLOW': 'Flow',
    'FLR': 'Flare',
    'FTM': 'Fantom',
    'GALA': 'Gala',
    'GMX': 'GMX',
    'GRT': 'The Graph',
    'HBAR': 'Hedera',
    'HYPE': 'Hyperliquid',
    'ICP': 'Internet Computer',
    'IMX': 'Immutable X',
    'INJ': 'Injective',
    'IOTA': 'IOTA',
    'JASMY': 'JasmyCoin',
    'JUP': 'Jupiter',
    'KAS': 'Kaspa',
    'KAVA': 'Kava',
    'LDO': 'Lido DAO',
    'LEO': 'UNUS SED LEO',
    'LINK': 'Chainlink',
    'LTC': 'Litecoin',
    'MANA': 'Decentraland',
    'MASK': 'Mask Network',
    'MATIC': 'Polygon',
    'MNT': 'Mantle',
    'MORPHO': 'Morpho',
    'NEAR': 'NEAR Protocol',
    'NEO': 'Neo',
    'NEXO': 'Nexo',
    'OKB': 'OKB',
    'ONDO': 'Ondo',
    'ONE': 'Harmony',
    'OP': 'Optimism',
    'PENDLE': 'Pendle',
    'PENGU': 'Pudgy Penguins',
    'PEPE': 'Pepe',
    'PI': 'Pi Network',
    'POL': 'Polygon',
    'PYTH': 'Pyth Network',
    'QNT': 'Quant',
    'RENDER': 'Render',
    'ROSE': 'Oasis',
    'RUNE': 'THORChain',
    'SAND': 'The Sandbox',
    'SEI': 'Sei',
    'SHIB': 'Shiba Inu',
    'SKY': 'Sky',
    'SNX': 'Synthetix',
    'SOL': 'Solana',
    'STRK': 'Starknet',
    'STX': 'Stacks',
    'SUI': 'Sui',
    'SUSHI': 'SushiSwap',
    'TAO': 'Bittensor',
    'THETA': 'Theta',
    'TIA': 'Celestia',
    'TON': 'Toncoin',
    'TRUMP': 'Official Trump',
    'TRX': 'TRON',
    'UNI': 'Uniswap',
    'VET': 'VeChain',
    'VIRTUAL': 'Virtuals Protocol',
    'WIF': 'dogwifhat',
    'WLD': 'Worldcoin',
    'XDC': 'XDC Network',
    'XLM': 'Stellar',
    'XMR': 'Monero',
    'XRP': 'XRP',
    'XTZ': 'Tezos',
    'ZEC': 'Zcash',
    'ZIL': 'Zilliqa',
    'ZK': 'ZKsync',
}


def crypto_display_name(symbol: Optional[str]) -> Optional[str]:
    """The coin's name for a bare (`BTC`) or pair (`BTCUSD`) symbol; None if unknown."""
    s = (symbol or "").strip().upper()
    if not s:
        return None
    if s in CRYPTO_NAMES:
        return CRYPTO_NAMES[s]
    for suffix in ("USDT", "USD"):
        if s.endswith(suffix) and s[: -len(suffix)] in CRYPTO_NAMES:
            return CRYPTO_NAMES[s[: -len(suffix)]]
    return None


def display_name_for_row(ticker: Optional[str], stored: Optional[str]) -> str:
    """The name a watchlist / tracking row should SHOW for a stored `company_name`.

    Rows starred before the coin-name fix (2026-09-11) persisted the symbol itself as
    the name ("ETHUSD"), and the reader used to publish it verbatim. A stored name that
    is empty or merely echoes the symbol (bare or pair) is replaced by the coin's real
    name when we know one; any other stored name is trusted as-is.
    """
    sym = (ticker or "").strip().upper()
    name = (stored or "").strip()
    bare = sym[:-3] if sym.endswith("USD") and len(sym) > 3 else sym
    if name and name.upper() not in {sym, bare, f"{bare}USD"}:
        return name
    # Only a COIN row gets a coin's name. The bare form (`BTC`) is the listed security
    # after migration 160, so an echo-named bare row keeps its symbol rather than
    # being called "Bitcoin".
    from app.services.asset_class import uses_coingecko_price

    if uses_coingecko_price(sym):
        return crypto_display_name(sym) or name or sym
    return name or sym
