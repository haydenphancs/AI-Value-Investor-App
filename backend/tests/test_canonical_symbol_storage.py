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


# ── the tracking write paths (added after migration 160) ─────────────────────
#
# `POST /tracking/holdings` and `PUT /tracking/holdings/{ticker}` both write
# `watchlist_items.ticker` and neither canonicalised. That is not a cosmetic gap: the POST
# re-creates a BARE row beside the migrated pair-form one, undoing migration 160 through
# ordinary use, and the PUT 404s for a client that still says "BTC" for the coin.

def _tracking_source():
    """`tracking.py` with comments stripped — the notes beside these fixes name every token
    a naive scan greps for, so an unstripped scan passes on prose after a revert."""
    import inspect
    from app.api.v1.endpoints import tracking

    raw = inspect.getsource(tracking)
    return "\n".join(line.split("#", 1)[0] for line in raw.splitlines())


def test_the_holdings_create_path_canonicalises():
    src = _tracking_source()
    assert "canonical_stored_symbol(request.ticker, request.asset_type)" in src, (
        "POST /tracking/holdings can re-create a bare crypto row, undoing migration 160"
    )
    assert "ticker = request.ticker.upper()\n" not in src, "the raw .upper() path is back"


def test_the_holdings_update_path_tries_raw_before_canonical():
    """RAW first is load-bearing. After migration 160 the two spellings name DIFFERENT
    assets — "BTC" is the Grayscale ETF, "BTCUSD" is Bitcoin — so canonicalising
    unconditionally would stop an ETF holder editing their own row."""
    src = _tracking_source()
    assert "raw_ticker" in src and "canonical_stored_symbol(raw_ticker" in src
    # raw must be attempted first
    assert src.index('.eq("ticker", raw_ticker)') < src.index('.eq("ticker", canonical)')


@pytest.mark.parametrize("bare,declared,expected", [
    ("BTC", "crypto", "BTCUSD"),
    ("btc", "crypto", "BTCUSD"),
    ("BTC", None, "BTCUSD"),          # bare-list membership, the migration's own guess
    ("BTC", "Stock", "BTC"),          # a declared equity stays the security
    ("BTCUSD", "crypto", "BTCUSD"),   # already canonical
    ("AAPL", None, "AAPL"),
    ("AAPL", "crypto", "AAPL"),       # not a known coin — never invent a pair
])
def test_the_canonicaliser_the_tracking_paths_now_use(bare, declared, expected):
    from app.services.asset_class import canonical_stored_symbol

    assert canonical_stored_symbol(bare, declared) == expected


# ── DELETE /watchlist: a deliberately-bare security must be removable ────────
#
# 🔴 Live bug, and migration 160's header invites the exact state that triggers it: a user
# who really tracks LTC Properties / Banco de Chile / Atomera re-adds the BARE form (correct
# — `canonical_stored_symbol('LTC','stock')` leaves it alone), and could then never remove
# it. `RemoveFromWatchlistRequest` carries no `asset_type`, so the handler canonicalised to
# 'LTCUSD', deleted ZERO rows, logged a warning, and returned
# 200 {"message": "LTCUSD removed from watchlist"}. The row stayed forever while the UI
# reported success every time.

def _watchlist_source():
    """Comment-stripped: the note beside the fix names the retired pattern."""
    import inspect
    from app.api.v1.endpoints import watchlist

    raw = inspect.getsource(watchlist.remove_from_watchlist)
    return "\n".join(line.split("#", 1)[0] for line in raw.splitlines())


def test_remove_tries_the_raw_ticker_before_the_canonical_one():
    src = _watchlist_source()
    assert "raw_ticker" in src, "remove still canonicalises unconditionally"
    assert src.index('.eq("ticker", raw_ticker)') < src.index('.eq("ticker", canonical)')


def test_remove_does_not_delete_both_spellings_at_once():
    """A user can legitimately hold the coin AND the security — 'BTCUSD' and 'BTC' are two
    different assets after migration 160. A single `.in_([raw, canonical])` would remove
    both when the user asked for one."""
    src = _watchlist_source()
    assert ".in_(" not in src, "remove deletes both spellings — that is a second bug"


def test_the_canonicaliser_leaves_a_declared_equity_bare():
    """This is what makes the bare form usable for the listed security at all."""
    from app.services.asset_class import canonical_stored_symbol

    for sym in ("LTC", "BCH", "ATOM", "BTC", "SOL", "XRP", "ETH"):
        assert canonical_stored_symbol(sym, "stock") == sym
        assert canonical_stored_symbol(sym, "Stock") == sym
        # ...but with no declaration at all, the bare-list guess still applies.
        assert canonical_stored_symbol(sym, None) == sym + "USD"
