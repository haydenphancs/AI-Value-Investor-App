"""A CoinGecko **id** is a historical slug, not a name — never render it as one.

`_build_related_cryptos` fell back to `cg_id.replace("-", " ").title()`, which publishes the
project's OLD name as the coin's name with nothing on screen to signal it is wrong:

    SNX   → "havven"                  → "Havven"                   (it is Synthetix)
    STX   → "blockstack"              → "Blockstack"               (it is Stacks)
    MATIC → "polygon-ecosystem-token" → "Polygon Ecosystem Token"
    BNB   → "binancecoin"             → "Binancecoin"

…plus mangled title-casing for a long tail ("Curve Dao Token", "Crypto Com Chain", "Fetch Ai").

Same class as PPLT-is-not-platinum: the label must name the thing whose number is shown.
The `/coins/markets` row already carries the CURRENT display name, and `_shape` passes it
through, so the right value was always one `.get` away.
"""
from __future__ import annotations

import pytest

from app.integrations.coingecko import SYMBOL_TO_COINGECKO_ID
from app.services import crypto_service as cs
from app.services.crypto_service import _CRYPTO_PROFILES


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(cs, "get_supabase", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_fmp_client", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_coingecko_client", lambda: None, raising=True)
    return cs.CryptoService()


def _quote(sym, name, price=1.0):
    return {"symbol": f"{sym}USD", "name": name, "price": price, "changePercentage": 1.0}


@pytest.mark.parametrize("sym,live_name,stale_slug_name", [
    ("SNX", "Synthetix Network", "Havven"),
    ("STX", "Stacks", "Blockstack"),
    # BNB and MATIC are both curated, so they were never exposed. CRO is not: it
    # rendered as "Crypto Com Chain" rather than Cronos.
    ("CRO", "Cronos", "Crypto Com Chain"),
])
def test_the_live_name_wins_over_the_slug(svc, sym, live_name, stale_slug_name):
    assert sym not in _CRYPTO_PROFILES, f"{sym} became curated — pick another uncurated coin"
    out = svc._build_related_cryptos([_quote(sym, live_name)], [sym])
    assert len(out) == 1
    assert out[0].name == live_name
    assert out[0].name != stale_slug_name


def test_a_curated_profile_still_outranks_everything(svc):
    sym = next(iter(_CRYPTO_PROFILES))
    curated = _CRYPTO_PROFILES[sym]["name"]
    out = svc._build_related_cryptos([_quote(sym, "Whatever CoinGecko Says")], [sym])
    assert out[0].name == curated


@pytest.mark.parametrize("bad_name", [None, "", "   ", 12345, {"en": "x"}])
def test_a_missing_or_malformed_name_falls_back_to_the_symbol_not_a_slug(svc, bad_name):
    """The bare ticker is honest. A stale project name is not — and it is what the old
    fallback produced for exactly these degraded rows."""
    q = _quote("SNX", "x")
    q["name"] = bad_name
    out = svc._build_related_cryptos([q], ["SNX"])
    assert out[0].name == "SNX"


def test_the_slug_is_no_longer_a_name_source_anywhere_in_the_builder():
    """Source-scan, docstring-stripped so the prose above cannot satisfy it. Brace-bounded
    to the one function — asserting over the whole module would pass on any other mention."""
    import ast
    import inspect

    src = inspect.cleandoc(inspect.getsource(cs.CryptoService._build_related_cryptos))
    fn = ast.parse(src).body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]
    body = ast.unparse(fn)
    assert "SYMBOL_TO_COINGECKO_ID" not in body
    assert ".title()" not in body


def test_the_coins_this_was_wrong_for_are_still_uncurated_and_still_slug_mismatched():
    """Keeps the parametrisation above honest: if the map or the curated table changes so
    these coins no longer demonstrate the bug, this fails and asks for new examples."""
    for sym, slug_name in [("SNX", "Havven"), ("STX", "Blockstack"),
                           ("CRO", "Crypto Com Chain")]:
        assert sym not in _CRYPTO_PROFILES
        cg_id = SYMBOL_TO_COINGECKO_ID.get(sym, "")
        assert cg_id.replace("-", " ").title() == slug_name, (sym, cg_id)


# ── an unknown 24h move must omit the row, not render a green +0.00% ─────────

def test_a_related_coin_with_no_change_is_omitted(svc):
    """`change_percent` is a non-Optional `Double` on iOS, so a null is not available — and
    0 is not neutral: the tile colours off `changePercent >= 0` and prints "+0.00%" in
    GREEN, i.e. flat-and-up. CoinGecko sends `price_change_percentage_24h: null` for a coin
    listed inside the last 24h.

    Omitting matches the decision already made for an unknown PRICE in the same loop.
    """
    q = _quote("SNX", "Synthetix Network", price=2.5)
    q["changePercentage"] = None
    q["changesPercentage"] = None
    assert svc._build_related_cryptos([q], ["SNX"]) == []


def test_a_real_zero_change_is_still_rendered(svc):
    """A coin that genuinely did not move is a MEASUREMENT — it must survive."""
    q = _quote("SNX", "Synthetix Network", price=2.5)
    q["changePercentage"] = 0.0
    q["changesPercentage"] = 0.0
    out = svc._build_related_cryptos([q], ["SNX"])
    assert len(out) == 1 and out[0].change_percent == 0.0
