"""
Shared symbol → asset-class detection (services/asset_class.py).

This module answers ONE load-bearing question for the mini-chart paths: does this
asset trade outside the US equity regular session? Get it wrong and
`chart_helper._filter_regular_hours` silently strips ~70% of a 24/7 series, so
the Holdings card and the Home Market Pulse tile draw an equity-session slice
beside a live 24/7 price — contradicting the DETAIL chart one tap away, which
passes extended_hours=True.

Regressions guarded:
  * `watchlist_items.asset_type` defaults to 'Stock' and `POST /api/v1/watchlist`
    (the only path the iOS add-ticker flow uses) never writes it, so the old
    `asset_type == "crypto"` test was ALWAYS False — the documented crypto fix
    never fired in production. A stored value must be a HINT, not the authority.
  * the generic `endswith("USD")` crypto heuristic swallows FMP's commodity codes
    (GCUSD/CLUSD/...) unless commodities are matched first — gold as a coin.

Run: cd backend && ./venv/bin/pytest tests/test_asset_class.py -x
"""

from __future__ import annotations

import pytest

from app.services.asset_class import (
    detect_asset_class,
    resolve_asset_class,
    symbol_trades_extended_hours,
    trades_extended_hours,
)


@pytest.mark.parametrize(
    "symbol,expected",
    [
        # Indices — track the equity session.
        ("^GSPC", "index"), ("^IXIC", "index"), ("^DJI", "index"),
        # Commodities — matched BEFORE the crypto USD-suffix rule.
        ("GCUSD", "commodity"), ("CLUSD", "commodity"), ("SIUSD", "commodity"),
        ("GOLD", "commodity"), ("NATGAS", "commodity"),
        # Crypto.
        ("BTCUSD", "crypto"), ("ETHUSD", "crypto"), ("SOLUSDT", "crypto"),
        ("BTC", "crypto"), ("DOGE", "crypto"),
        # Equities.
        ("AAPL", "stock"), ("ORCL", "stock"), ("CRM", "stock"), ("PLUG", "stock"),
        ("BRK-B", "stock"), ("BRK.B", "stock"),
    ],
)
def test_detect_asset_class(symbol, expected):
    assert detect_asset_class(symbol) == expected


def test_bare_usd_ticker_is_not_a_coin():
    """"USD" is a real listed ETF (ProShares Ultra Semiconductors). The generic
    endswith("USD") rule needs a base symbol in front of it, or that ETF gets
    fetched with extended hours and disagrees with its own detail chart."""
    assert detect_asset_class("USD") == "stock"
    assert symbol_trades_extended_hours("USD", None) is False
    # …while a real pair, which has a base symbol, still resolves as crypto.
    assert detect_asset_class("ETHUSD") == "crypto"
    assert detect_asset_class("USDT") == "crypto"   # 4 chars, a real stablecoin


def test_detect_is_case_and_whitespace_insensitive():
    assert detect_asset_class("  btcusd ") == "crypto"
    assert detect_asset_class("gcusd") == "commodity"


def test_detect_defaults_to_stock_for_missing_or_blank():
    # Conservative: an unknown symbol keeps the regular-hours filter ON rather
    # than silently widening a series.
    assert detect_asset_class(None) == "stock"
    assert detect_asset_class("") == "stock"
    assert detect_asset_class("   ") == "stock"


def test_stored_asset_type_is_a_hint_not_the_authority():
    # The column default. If this were trusted, Bitcoin would be an equity — the
    # exact bug that made the crypto sparkline fix inert.
    assert resolve_asset_class("BTCUSD", "Stock") == "crypto"
    assert resolve_asset_class("BTCUSD", None) == "crypto"
    assert resolve_asset_class("BTCUSD", "") == "crypto"
    assert resolve_asset_class("GCUSD", "Stock") == "commodity"
    # A SPECIFIC stored class is honoured — it can know things a symbol can't.
    assert resolve_asset_class("SPY", "etf") == "etf"
    assert resolve_asset_class("WEIRD", "commodity") == "commodity"
    # Case-insensitively.
    assert resolve_asset_class("SPY", "ETF") == "etf"


def test_only_crypto_and_commodity_trade_around_the_clock():
    assert trades_extended_hours("crypto") is True
    assert trades_extended_hours("commodity") is True
    assert trades_extended_hours("stock") is False
    assert trades_extended_hours("index") is False   # tracks the equity session
    assert trades_extended_hours("etf") is False
    assert trades_extended_hours("") is False
    assert trades_extended_hours("CRYPTO") is True   # case-insensitive


def test_symbol_trades_extended_hours_end_to_end():
    # The realistic call: symbol + the useless stored default.
    assert symbol_trades_extended_hours("BTCUSD", "Stock") is True
    assert symbol_trades_extended_hours("GCUSD", "Stock") is True
    assert symbol_trades_extended_hours("CLUSD", None) is True
    assert symbol_trades_extended_hours("AAPL", "Stock") is False
    assert symbol_trades_extended_hours("^GSPC", "Stock") is False


def test_chat_asset_type_delegates_to_the_same_detector():
    """chat_service kept its own copy of these symbol sets. Two copies of a
    classification that decides whether a chart is clipped is a drift hazard —
    the same ticker would render two different charts."""
    from app.services.chat_service import ChatService

    assert ChatService._detect_asset_type("") == "NORMAL"
    assert ChatService._detect_asset_type("GCUSD") == "COMMODITY"
    assert ChatService._detect_asset_type("BTCUSD") == "CRYPTO"
    assert ChatService._detect_asset_type("^GSPC") == "INDEX"
    assert ChatService._detect_asset_type("AAPL") == "STOCK"
