"""Every coin the app can price has an explicit display name — never a CoinGecko slug.

A CoinGecko id is a historical slug, not a name (`beam-2`, `xdce-crowd-sale`, `okb`,
`ether-fi`, `zksync`). Search used `cg_id.replace("-", " ").title()` for anything without
an override, so 25 of 111 coins surfaced as "Beam 2", "Xdce Crowd Sale", "Okb", "Ether Fi"
or "Zksync". The related-coins card was fixed for the same class earlier; search has no
markets row to read a name from, so it needs the table in `services/crypto_names.py`.
"""
from __future__ import annotations

import pytest

from app.integrations.coingecko import SYMBOL_TO_COINGECKO_ID
from app.services.crypto_names import CRYPTO_NAMES, crypto_display_name

# Slug renderings that shipped. None of them may come back.
_SLUG_RENDERINGS = {
    "Beam 2", "Xdce Crowd Sale", "Okb", "Ether Fi", "Lido Dao", "Zksync", "Eigenlayer",
    "Conflux Token", "Leo Token", "Flare Networks", "Fetch Ai", "Havven", "Blockstack",
    "Crypto Com Chain", "Compound Governance Token",
}


def test_every_priced_coin_has_an_explicit_name():
    missing = sorted(set(SYMBOL_TO_COINGECKO_ID) - set(CRYPTO_NAMES))
    assert missing == [], f"coins without a display name (would fall to the slug): {missing}"


@pytest.mark.parametrize("sym", sorted(SYMBOL_TO_COINGECKO_ID))
def test_no_name_is_a_title_cased_slug(sym):
    name = CRYPTO_NAMES[sym]
    assert name not in _SLUG_RENDERINGS, f"{sym}: {name!r} is the slug, not the name"
    assert name.strip() and name == name.strip()


def test_the_search_table_is_the_shared_table():
    from app.api.v1.endpoints.stocks import _CRYPTO_NAMES
    assert _CRYPTO_NAMES == CRYPTO_NAMES


@pytest.mark.parametrize("raw, expected", [
    ("BTC", "Bitcoin"), ("btc", "Bitcoin"), ("BTCUSD", "Bitcoin"), ("BTCUSDT", "Bitcoin"),
    ("XDC", "XDC Network"), ("XDCUSD", "XDC Network"), ("OKB", "OKB"),
    ("", None), (None, None), ("NOTACOIN", None), ("USD", None),
])
def test_display_name_accepts_bare_and_pair_forms(raw, expected):
    assert crypto_display_name(raw) == expected


# ── rows starred BEFORE the name fix still render the coin's name ─────────────
#
# `watchlist_items.company_name` for a coin persisted before 2026-09-11 is the symbol
# itself ("ETHUSD"); the Home tile and the Tracking row published it verbatim. The
# readers now resolve an echo-name at read time — for COIN rows only, since the bare
# form is the listed security after migration 160.

from app.services.crypto_names import display_name_for_row


@pytest.mark.parametrize("ticker, stored, expected", [
    ("ETHUSD", "ETHUSD", "Ethereum"),
    ("ETHUSD", "", "Ethereum"),
    ("ETHUSD", None, "Ethereum"),
    ("ETHUSD", "ETH", "Ethereum"),
    ("ETHUSD", "Ethereum", "Ethereum"),        # a real stored name is trusted
    ("ETHUSD", "Ether (custom)", "Ether (custom)"),
    ("OKBUSD", "OKB", "OKB"),
    ("AAPL", "Apple", "Apple"),
    ("AAPL", "", "AAPL"),                       # an equity never gets a coin name
    ("BTC", "BTC", "BTC"),                      # the bare form is the SECURITY
    ("BTC", "Grayscale Bitcoin Mini Trust ETF", "Grayscale Bitcoin Mini Trust ETF"),
    ("ZZZUSD", "ZZZUSD", "ZZZUSD"),             # unknown coin keeps its symbol
    ("", "", ""),
])
def test_display_name_for_row(ticker, stored, expected):
    assert display_name_for_row(ticker, stored) == expected


def test_both_readers_resolve_the_stored_name():
    import inspect
    from app.services import tracking_service as ts, home_dashboard_service as hd
    src = inspect.getsource(ts.TrackingService)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "company_name=display_name_for_row(" in code
    src = inspect.getsource(hd.HomeDashboardService._build_watchlist)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert 'name=display_name_for_row(sym, row.get("company_name"))' in code
