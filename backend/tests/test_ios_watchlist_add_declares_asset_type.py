"""Every iOS watchlist add DECLARES the asset class it means (F15-2).

`POST /watchlist` resolves an UNDECLARED bare coin symbol toward the coin by design —
`canonical_stored_symbol("LTC", None)` → "LTCUSD", stored as `asset_type='crypto'`,
`company_name="Litecoin"` (pinned by
`test_watchlist_asset_type_persistence.py::test_an_undeclared_bare_coin_resolves_to_the_coin_and_is_typed_crypto`).
Two live add paths sent `assetType: nil` for an EQUITY the user had just picked from a
stocks-only search sheet:

  * `UpdatesViewModel.addTicker`, fed by `TickerSearchSheet`, which filtered results to
    `type == "stock"` and then DROPPED the type when mapping to `TickerSearchItem`;
  * `OnboardingViewModel.addFromSearch`, fed by `TargetSearchSheet` (stocks-only too).

So "LTC Properties" was stored as Litecoin, "Banco de Chile" (BCH) as Bitcoin Cash,
"Atomera" (ATOM) as Cosmos, "Interlink" (LINK) as Chainlink and "Emeren" (SOL) as Solana:
the strip and Tracking showed the coin, and the REIT they chose was tracked nowhere.

Source-scan (no XCTest target): comments stripped, brace-bound, mutation-tested by hand
(restore `assetType: nil` in `addTicker` → red; restore → green). The backend twin at the
bottom pins the rule the clients now rely on: a declared "stock" keeps the bare form.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.services.asset_class import canonical_stored_symbol

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"


def _stripped(path: pathlib.Path) -> str:
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return "\n".join(
        "" if line.strip().startswith("//") else re.sub(r"\s//.*$", "", line)
        for line in src.splitlines()
    )


def _block(src: str, declaration: str, *, where: str) -> str:
    i = src.find(declaration)
    assert i != -1, f"guard is stale — `{declaration}` not found in {where}"
    start = src.index("{", i)
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(f"unbalanced braces after `{declaration}` in {where}")


_UPDATES_VM = _IOS / "ViewModels" / "UpdatesViewModel.swift"
_UPDATES_VIEW = _IOS / "Views" / "Screens" / "UpdatesView.swift"
_ONBOARDING_VM = _IOS / "ViewModels" / "OnboardingViewModel.swift"


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_stripper_drops_the_comment_that_names_the_old_call():
    raw = _UPDATES_VM.read_text(encoding="utf-8")
    assert 'canonical_stored_symbol("LTC", nil)' in raw, "the explanatory comment moved"
    assert 'canonical_stored_symbol("LTC", nil)' not in _stripped(_UPDATES_VM)


# ── Updates: the type travels from the search row to the request ─────────────

def test_updates_add_ticker_no_longer_sends_nil():
    body = _block(_stripped(_UPDATES_VM), "func addTicker(", where="UpdatesViewModel.swift")
    assert "assetType: nil" not in body, (
        "addTicker sends the add undeclared again — an equity whose symbol names a coin "
        "(LTC/BCH/ATOM/LINK/SOL) is stored as the coin"
    )
    assert ".addToWatchlist(stockId: ticker, assetType: assetType)" in body
    # The parameter is REQUIRED, not defaulted — a future caller must decide.
    assert re.search(r"func addTicker\(_ symbol: String, assetType: String\?\)", body), (
        "assetType must be a required parameter of addTicker"
    )


def test_ticker_search_item_carries_the_type():
    src = _stripped(_UPDATES_VIEW)
    item = _block(src, "struct TickerSearchItem", where="UpdatesView.swift")
    assert re.search(r"let type: String\?", item), "TickerSearchItem must carry `type`"


def test_the_search_sheet_maps_the_type_through_and_hands_over_the_item():
    src = _stripped(_UPDATES_VIEW)
    sheet = _block(src, "struct TickerSearchSheet", where="UpdatesView.swift")
    search = _block(sheet, "private func runSearch", where="TickerSearchSheet")
    assert 'type == "stock"' in search or '== "stock"' in search, "the equities filter is the premise"
    assert re.search(r"type:\s*\$0\.type", search), (
        "the search mapping drops `type` again — the filter proves it was there"
    )
    assert "onSelectTicker?(item)" in sheet, "the sheet must hand the whole item up, not just the symbol"
    assert "onSelectTicker?(item.ticker)" not in sheet
    assert "var onSelectTicker: ((TickerSearchItem) -> Void)?" in sheet
    # The hardcoded `popularTickers` list became the shared trending chips (2026-09-26). The
    # premise is unchanged: an item built from a chip must still DECLARE its type, and the
    # chips must be the equities-only variant this sheet requires.
    assert "popularTickers" not in sheet, "the hardcoded list (with its broken BRK.B) is gone"
    assert "sections(for: .stocksOnly)" in sheet, "the chips must be the stocks-only variant"
    assert re.search(r"TickerSearchItem\([^)]*type:\s*item\.type", sheet, re.S), (
        "a chip-built item must carry `type` — an undeclared add is how LTC became Litecoin"
    )


def test_manage_assets_forwards_the_type_and_the_screen_passes_it_on():
    src = _stripped(_UPDATES_VIEW)
    manage = _block(src, "struct ManageAssetsSheet", where="UpdatesView.swift")
    assert "var onAddTicker: ((String, String?) -> Void)?" in manage
    assert re.search(r'onAddTicker\?\(item\.ticker,\s*item\.type \?\? "stock"\)', manage), (
        "ManageAssetsSheet must forward the search item's type (defaulting to stock — the "
        "sheet is equities-only)"
    )
    # The screen's call site threads it into the view model.
    assert re.search(r"onAddTicker:\s*\{\s*ticker,\s*assetType\s+in", src)
    assert "viewModel.addTicker(ticker, assetType: assetType)" in src


# ── Onboarding: search declares; the curated chips stay undeclared ───────────

def test_onboarding_search_add_declares_stock():
    src = _stripped(_ONBOARDING_VM)
    add_from_search = _block(src, "func addFromSearch(", where="OnboardingViewModel.swift")
    assert 'assetType: String? = "stock"' in add_from_search, (
        "addFromSearch must default to stock — TargetSearchSheet is equities-only"
    )
    assert re.search(r'self\.add\(upper,\s*assetType:\s*assetType \?\? "stock"\)', add_from_search)
    assert "assetType: nil" not in add_from_search


def test_onboarding_add_passes_the_declaration_through():
    src = _stripped(_ONBOARDING_VM)
    add = _block(src, "private func add(", where="OnboardingViewModel.swift")
    assert "assetType: nil" not in add
    assert ".addToWatchlist(stockId: symbol, assetType: assetType)" in add


def test_onboarding_chips_stay_undeclared_because_of_the_bitcoin_chip():
    """The premise the fix notes flagged: `OnboardingTicker(symbol: "BTC", name: "Bitcoin")`
    relies on nil→coin to land as BTCUSD. Blanket-declaring "stock" on the chip path would
    turn that chip into the Grayscale ETF."""
    src = _stripped(_ONBOARDING_VM)
    assert re.search(r'OnboardingTicker\(symbol:\s*"BTC",\s*name:\s*"Bitcoin"\)', src), (
        "the Bitcoin chip is gone — if it was replaced by a declared crypto chip, update "
        "this guard and re-check `toggle`"
    )
    toggle = _block(src, "func toggle(", where="OnboardingViewModel.swift")
    assert "self.add(symbol, assetType: nil)" in toggle


# ── tree-wide: no `.addToWatchlist(…, assetType: nil)` anywhere ───────────────

def test_no_watchlist_add_call_site_passes_a_literal_nil_type():
    offenders = []
    for path in list((_IOS / "ViewModels").glob("*.swift")) + list((_IOS / "Views").rglob("*.swift")):
        src = _stripped(path)
        for m in re.finditer(r"\.addToWatchlist\([^)]*\)", src):
            if re.search(r"assetType:\s*nil", m.group(0)):
                offenders.append(f"{path.relative_to(_IOS)}: {m.group(0)}")
    assert not offenders, "\n".join(offenders)


def test_the_tree_scan_sees_the_real_call_sites():
    """Anti-vacuity for the scan above: it must find the two paths this file is about."""
    found = 0
    for path in (_UPDATES_VM, _ONBOARDING_VM):
        found += len(re.findall(r"\.addToWatchlist\(", _stripped(path)))
    assert found >= 2


# ── backend twin: the rule the clients now rely on ───────────────────────────

@pytest.mark.parametrize("sym", ["LTC", "BCH", "ATOM", "LINK", "SOL"])
def test_a_declared_stock_keeps_the_bare_form_and_undeclared_goes_to_the_coin(sym):
    assert canonical_stored_symbol(sym, "stock") == sym
    assert canonical_stored_symbol(sym, None) == f"{sym}USD", (
        "if nil no longer resolves to the coin, the iOS guards above are pinning a fix for "
        "a rule that changed — revisit both sides together"
    )
