"""A bare crypto SYMBOL is often a real listed SECURITY. Never route it to CoinGecko.

THE BUG THIS PINS (found in production, 2026-09-09):
`price_service` routed to CoinGecko on `detect_asset_class(s) == "crypto"` alone. But
`asset_class._BARE_CRYPTO_SYMBOLS` deliberately classifies a bare BTC/ETH/LTC as crypto
so the CHART gets a 24/7 session window — and on FMP those very tickers are securities we
are fully licensed for. Measured against FMP:

    ticker   served (coin)   real (security)   error     what it actually is
    BTC      $78,984.00      $34.68            2,277x    Grayscale Bitcoin Mini Trust ETF
    ETH      $2,490.97       $23.68              105x    Grayscale Ethereum Mini Trust ETF
    BCH      $257.17         $42.37              6.1x    Banco de Chile (NYSE)
    LTC      $53.93          $42.01              1.3x    LTC Properties, Inc. (a REIT)
    XRP      $1.42           $15.92              0.1x    Bitwise XRP ETF
    ATOM     $2.00           $4.14               0.5x    Atomera Incorporated (NASDAQ)

A REIT rendered as "Litecoin" at Litecoin's price. This is the Phase 4 labelling rule
again — the number shown must belong to the instrument named — and it is worse here,
because it is a holding a user can own.

THE FIX: route on the CONJUNCTION. `uses_coingecko_price` = crypto-classified AND
`is_blocked_symbol` (FMP genuinely cannot serve it) AND not an FX pair. A symbol FMP can
serve keeps going to FMP, so the PAIR form (BTCUSD) uses CoinGecko while the BARE form
(BTC) stays on FMP — exactly the distinction the two namespaces already carry.
"""

from __future__ import annotations

import pytest

from app.services.asset_class import (
    _BARE_CRYPTO_SYMBOLS,
    detect_asset_class,
    uses_coingecko_price,
)
from app.integrations.fmp_entitlements import is_blocked_symbol

# Verified live against FMP /stable/profile on 2026-09-09: real, actively-traded.
REAL_SECURITIES_SHADOWED_BY_A_COIN_TICKER = {
    "BTC": "Grayscale Bitcoin Mini Trust ETF",
    "ETH": "Grayscale Ethereum Mini Trust ETF",
    "XRP": "Bitwise XRP ETF",
    "ATOM": "Atomera Incorporated",
    "BCH": "Banco de Chile",
    "LTC": "LTC Properties, Inc.",
}


@pytest.mark.parametrize("ticker", sorted(REAL_SECURITIES_SHADOWED_BY_A_COIN_TICKER))
def test_a_bare_ticker_that_is_a_real_security_never_uses_coingecko(ticker):
    """The exact six that shipped a wrong price. FMP serves these; it must keep serving."""
    assert uses_coingecko_price(ticker) is False, (
        f"{ticker} is {REAL_SECURITIES_SHADOWED_BY_A_COIN_TICKER[ticker]} on FMP — "
        "routing it to CoinGecko serves the COIN's price for a listed security"
    )


@pytest.mark.parametrize("pair", ["BTCUSD", "ETHUSD", "SOLUSD", "SHIBUSD", "DOGEUSD",
                                  "PIUSD", "QTUMUSD"])
def test_the_pair_form_still_routes_to_coingecko(pair):
    """Anti-vacuity: the fix must not disable crypto routing wholesale.

    Without this, `return False` passes every other test in this file.
    """
    assert uses_coingecko_price(pair) is True, f"{pair} lost its CoinGecko routing"


def test_no_bare_crypto_symbol_is_routed_to_coingecko():
    """Assert over the WHOLE set, so adding a coin cannot silently reopen this.

    None of the 16 bare symbols is blocked on FMP, so none may take the CoinGecko path.
    """
    leaked = sorted(s for s in _BARE_CRYPTO_SYMBOLS if uses_coingecko_price(s))
    assert leaked == [], (
        f"bare symbols routed to CoinGecko: {leaked} — each is a bare ticker FMP may "
        "serve as a real security"
    )


def test_the_predicate_is_strictly_narrower_than_the_classifier():
    """Pins the SHAPE of the fix: conjunction, not classification.

    If someone 'simplifies' `uses_coingecko_price` back to `detect_asset_class == crypto`,
    these bare symbols come back and the wrong prices ship again.
    """
    classified = {s for s in _BARE_CRYPTO_SYMBOLS if detect_asset_class(s) == "crypto"}
    routed = {s for s in _BARE_CRYPTO_SYMBOLS if uses_coingecko_price(s)}
    assert classified, "guard is stale — bare symbols are no longer classified crypto"
    assert routed < classified, (
        "uses_coingecko_price must be STRICTLY narrower than the crypto classifier"
    )


@pytest.mark.parametrize("fx", ["EURUSD", "GBPUSD", "USDCAD", "USDJPY", "AUDUSD"])
def test_an_fx_pair_never_borrows_a_stablecoin_price(fx):
    """`detect_asset_class` calls any long USD-suffixed symbol crypto, so FX lands here.

    CoinGecko answers for it: EURUSD resolved to a euro STABLECOIN quoting $1.18 — close
    enough to the real rate to pass a glance, and wrong the moment that coin depegs. FX
    has no licensed substitute, so it must stay ABSENT.
    """
    assert uses_coingecko_price(fx) is False


@pytest.mark.parametrize("sym", ["AAPL", "MSFT", "SPY", "GLD", "SHOP.TO", "BRK-B"])
def test_ordinary_equities_are_untouched(sym):
    assert uses_coingecko_price(sym) is False


@pytest.mark.parametrize("sym", ["^GSPC", "^IXIC", "GCUSD", "CLUSD"])
def test_index_and_commodity_are_not_crypto_and_stay_absent(sym):
    """Blocked, but NOT crypto — they must not acquire a CoinGecko source either."""
    assert uses_coingecko_price(sym) is False
    assert is_blocked_symbol(sym) is True


@pytest.mark.parametrize("weird", [None, "", "   ", "usd", "USD", "USDT"])
def test_outlier_inputs_do_not_route(weird):
    assert uses_coingecko_price(weird) is False
