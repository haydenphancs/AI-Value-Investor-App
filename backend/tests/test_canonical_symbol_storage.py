"""A bare coin ticker is AMBIGUOUS, so it must never be what we persist.

`GET /stocks/search` deliberately returns BOTH rows for "BTC" — "Bitcoin" from the local
crypto map and "Grayscale Bitcoin Mini Trust ETF" from FMP — because seven of the sixteen
bare coin tickers are also real, actively-traded US listings:

    BTC/ETH -> Grayscale mini-trust ETFs, XRP -> Bitwise ETF   (AMEX)
    LTC -> LTC Properties (a REIT), BCH -> Banco de Chile      (NYSE)
    ATOM -> Atomera, SOL -> Emeren Group                       (NASDAQ/NYSE)

Both choices used to be stored as the identical string, after which nothing downstream
could tell them apart and the price shown was whichever source the routing picked. That
is how a REIT rendered as "Litecoin" and Bitcoin priced at $34.

Resolution: COINS are persisted in the PAIR form ("BTCUSD"), which
`uses_coingecko_price` already routes to CoinGecko, leaving the bare form to mean the
listed security. Canonicalisation happens at the WRITE boundary so it also fixes the
currently-shipped client, which does not send an asset type.
"""

from __future__ import annotations

import inspect
import pathlib
import re

import pytest

from app.integrations.coingecko import SYMBOL_TO_COINGECKO_ID
from app.services.asset_class import (
    _BARE_CRYPTO_SYMBOLS,
    canonical_stored_symbol,
    uses_coingecko_price,
)

COLLIDING = ["BTC", "ETH", "XRP", "LTC", "BCH", "ATOM"]   # verified live against FMP


# ── the client's declaration wins ───────────────────────────────────────────

@pytest.mark.parametrize("sym", sorted(_BARE_CRYPTO_SYMBOLS))
def test_a_declared_crypto_is_stored_as_the_pair(sym):
    if sym not in SYMBOL_TO_COINGECKO_ID:
        pytest.skip(f"{sym} is not in the coin map")
    assert canonical_stored_symbol(sym, "crypto") == f"{sym}USD"


@pytest.mark.parametrize("sym", COLLIDING)
@pytest.mark.parametrize("declared", ["stock", "etf", "Stock", "ETF"])
def test_a_declared_security_keeps_the_bare_form(sym, declared):
    """The whole point: a user who picked the ETF must keep getting the ETF."""
    assert canonical_stored_symbol(sym, declared) == sym


def test_an_undeclared_bare_coin_resolves_toward_the_coin():
    """The older shipped client sends no asset type.

    Resolving toward the coin matches every other convention in the app: search lists the
    coin FIRST for an exact match, the crypto screen's star writes the bare form, and
    `_BARE_CRYPTO_SYMBOLS` classifies it as crypto.
    """
    for sym in sorted(_BARE_CRYPTO_SYMBOLS):
        if sym in SYMBOL_TO_COINGECKO_ID:
            assert canonical_stored_symbol(sym, None) == f"{sym}USD"


# ── everything else is left alone ───────────────────────────────────────────

@pytest.mark.parametrize("sym", ["AAPL", "MSFT", "GLD", "SPY", "SHOP.TO", "BRK-B", "^GSPC", "GCUSD"])
@pytest.mark.parametrize("declared", [None, "stock", "crypto", "etf"])
def test_a_non_coin_symbol_is_never_rewritten(sym, declared):
    assert canonical_stored_symbol(sym, declared) == sym


@pytest.mark.parametrize("sym", ["BTCUSD", "ETHUSD", "SHIBUSD", "ARUSD"])
@pytest.mark.parametrize("declared", [None, "crypto"])
def test_an_already_canonical_pair_is_idempotent(sym, declared):
    assert canonical_stored_symbol(sym, declared) == sym


@pytest.mark.parametrize("weird", [None, "", "   ", "usd", "USD", "USDT"])
def test_outlier_inputs_do_not_explode(weird):
    out = canonical_stored_symbol(weird, None)
    assert isinstance(out, str)


def test_canonicalisation_and_routing_agree():
    """The point of the whole exercise: what we STORE must price correctly."""
    for sym in sorted(_BARE_CRYPTO_SYMBOLS):
        if sym not in SYMBOL_TO_COINGECKO_ID:
            continue
        stored = canonical_stored_symbol(sym, "crypto")
        assert uses_coingecko_price(stored), (
            f"{sym} is stored as {stored} but that does not route to CoinGecko — the "
            "coin would have no working price source at all"
        )
        assert not uses_coingecko_price(sym), (
            f"the BARE {sym} must stay on FMP so it can mean the listed security"
        )


# ── the write boundaries actually apply it ──────────────────────────────────

def test_the_watchlist_add_canonicalises_before_anything_else():
    from app.api.v1.endpoints import watchlist

    src = inspect.getsource(watchlist.add_to_watchlist)
    stripped = "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )
    assert "canonical_stored_symbol" in stripped
    # It must run BEFORE the duplicate check, or both forms end up in the table.
    assert stripped.find("canonical_stored_symbol") < stripped.find("watchlist_items"), (
        "canonicalise before the duplicate check and the insert"
    )


def test_the_watchlist_remove_uses_the_same_normalisation():
    """Otherwise a row stored as BTCUSD can never be removed by a client sending BTC."""
    from app.api.v1.endpoints import watchlist

    src = inspect.getsource(watchlist.remove_from_watchlist)
    assert "canonical_stored_symbol" in src


def test_the_price_alert_create_canonicalises_before_seeding():
    from app.api.v1.endpoints import price_alerts

    src = inspect.getsource(price_alerts.create_price_alert)
    stripped = "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )
    i = stripped.find("canonical_stored_symbol")
    assert i != -1, "the alert ticker must be canonicalised"
    seed = stripped.find("get_quotes_list")
    assert seed == -1 or i < seed, (
        "canonicalise BEFORE the seed quote, or the baseline is fetched for the wrong asset"
    )


def test_the_add_request_accepts_an_asset_type():
    from app.schemas.watchlist import AddToWatchlistRequest

    assert "asset_type" in AddToWatchlistRequest.model_fields
    # Optional, so the currently-shipped build keeps working.
    assert AddToWatchlistRequest(stock_id="BTC").asset_type is None


# ── the migration must cover exactly the ambiguous set ──────────────────────

def test_the_migration_lists_every_bare_coin_symbol():
    mig = (pathlib.Path(__file__).resolve().parents[1] / "database" / "migrations"
           / "160_canonicalise_bare_crypto_symbols.sql").read_text(encoding="utf-8")
    for sym in sorted(_BARE_CRYPTO_SYMBOLS):
        if sym in SYMBOL_TO_COINGECKO_ID:
            assert f"'{sym}'" in mig, f"migration 160 does not cover {sym}"


def test_the_migration_dedupes_on_the_full_unique_key():
    """`price_alerts_no_dupes` is UNIQUE (user_id, ticker, kind, threshold).

    Matching on user_id+ticker alone would DELETE a rule differing in kind or threshold
    that could have been renamed safely.
    """
    mig = (pathlib.Path(__file__).resolve().parents[1] / "database" / "migrations"
           / "160_canonicalise_bare_crypto_symbols.sql").read_text(encoding="utf-8")
    # Split on the SECTION marker, not on a table name — the header comment names both
    # tables in prose, so a name-based split lands in the documentation.
    assert "── 2." in mig, "migration section markers changed"
    alerts_block = mig.split("── 2.")[0]
    assert "DELETE FROM price_alerts" in alerts_block, "split did not isolate the alerts block"
    assert "b.kind = a.kind" in alerts_block
    assert "b.threshold IS NOT DISTINCT FROM a.threshold" in alerts_block
