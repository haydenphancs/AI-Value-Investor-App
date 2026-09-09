"""Every crypto symbol the app can SURFACE must be in SYMBOL_TO_COINGECKO_ID.

This map is not just a convenience lookup — it is load-bearing for three separate things,
which is why an omission is a live defect rather than a missing nicety:

  1. **Entitlement.** `is_blocked_symbol` refuses a crypto pair by looking its BASE up in
     this map (the 6-char length rule cannot see a 2-char base like "AR"). A symbol that
     is missing is NOT refused, so its request goes out to FMP and comes back a raw
     402 Payment Required.
  2. **Price routing.** `asset_class.uses_coingecko_price` is gated on `is_blocked_symbol`,
     so a missing symbol is never routed to CoinGecko either — it has no working source
     at all.
  3. **Display name.** `_build_related_cryptos` derives the label from this map, so a
     missing symbol renders as its raw ticker.

FOUND IN PRODUCTION: "AR" (Arweave) is listed in `_RELATED_CRYPTOS` for the storage-coin
screens but was absent here. Opening it from Filecoin's related strip produced a related
row labelled "AR", and its Technical Analysis and sentiment endpoints both hit FMP and
took a 402 — a broken screen, reached in two taps from a coin the app itself offers.
"""

from __future__ import annotations

import pytest

from app.integrations.coingecko import SYMBOL_TO_COINGECKO_ID
from app.integrations.fmp_entitlements import is_blocked_symbol
from app.services.asset_class import uses_coingecko_price
from app.services.crypto_service import _CRYPTO_PROFILES, _RELATED_CRYPTOS

RELATED_SYMBOLS = sorted({s for v in _RELATED_CRYPTOS.values() for s in v})


def test_every_related_coin_is_in_the_id_map():
    """The exact omission that shipped. Assert over the whole set, not a sample."""
    missing = sorted(s for s in RELATED_SYMBOLS if s not in SYMBOL_TO_COINGECKO_ID)
    assert missing == [], (
        f"related coins missing from SYMBOL_TO_COINGECKO_ID: {missing}. Each is offered to "
        "the user, is NOT refused by is_blocked_symbol, reaches FMP and takes a 402, and "
        "renders its raw ticker instead of a name."
    )


def test_every_screen_with_a_profile_is_in_the_id_map():
    missing = sorted(s for s in _CRYPTO_PROFILES if s not in SYMBOL_TO_COINGECKO_ID)
    assert missing == [], f"profiled coins missing from the id map: {missing}"


def test_every_related_coin_key_is_in_the_id_map():
    """The screens that HAVE a related strip must themselves be resolvable."""
    missing = sorted(s for s in _RELATED_CRYPTOS if s not in SYMBOL_TO_COINGECKO_ID)
    assert missing == [], f"related-strip owners missing from the id map: {missing}"


@pytest.mark.parametrize("symbol", RELATED_SYMBOLS)
def test_every_related_coin_pair_is_blocked_and_routed(symbol):
    """The three consequences, asserted per symbol so a failure names the coin."""
    pair = f"{symbol}USD"
    assert is_blocked_symbol(pair) is True, (
        f"{pair} is not refused — it will reach FMP and take a 402"
    )
    assert uses_coingecko_price(pair) is True, (
        f"{pair} has no working price source: not on FMP (blocked) and not routed to CoinGecko"
    )


def test_arweave_specifically_resolves():
    """Pins the exact regression, by name, so a revert is unambiguous."""
    assert SYMBOL_TO_COINGECKO_ID.get("AR") == "arweave"
    assert is_blocked_symbol("ARUSD") is True
    assert uses_coingecko_price("ARUSD") is True


def test_no_id_in_the_map_is_blank_or_duplicated_by_accident():
    """A blank id resolves to nothing; a surprise duplicate silently merges two coins.

    MATIC and POL legitimately share `polygon-ecosystem-token` (the rebrand), which is why
    `markets_rows_by_id` keys on the id rather than the symbol. Any OTHER collision is a
    typo that would serve one coin's price under another's name.
    """
    blanks = sorted(k for k, v in SYMBOL_TO_COINGECKO_ID.items() if not str(v).strip())
    assert blanks == [], f"blank CoinGecko ids: {blanks}"

    seen: dict[str, list[str]] = {}
    for sym, cid in SYMBOL_TO_COINGECKO_ID.items():
        seen.setdefault(cid, []).append(sym)
    dupes = {cid: sorted(syms) for cid, syms in seen.items() if len(syms) > 1}
    # Compare as SORTED sets — dict iteration order is insertion order, not alphabetical.
    assert dupes == {"polygon-ecosystem-token": ["MATIC", "POL"]}, (
        f"unexpected duplicate CoinGecko ids: {dupes}. MATIC/POL is the one legitimate "
        "collision (the Polygon rebrand); any other means two symbols resolve to one coin, "
        "so one of them renders the other's price under its own name."
    )
