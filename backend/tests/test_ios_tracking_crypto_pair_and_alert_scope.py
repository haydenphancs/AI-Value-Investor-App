"""Three iOS guards from the 2026-09-12 widened deep-check (source-scan; comments stripped,
brace-bound; each mutation-tested by hand).

1. Every add flow hands the group the STORED spelling of a search result — the pair for a
   coin. `AddAssetSheet.addAsset` still sent the bare `BTC`, which `PUT /tickers` resolved
   raw-first to the BTC ETF and then deleted the just-mirrored `BTCUSD` row.
2. The search sheet's star asks membership with the same stored spelling — the bare key
   never matched a tracked coin (empty star, every tap dead) and DID match the ETF.
3. `PriceAlertStore.alerts(for:)` applies the bare-symbol crypto match only when the CALLER
   is a crypto screen; the BTC ETF screen used to inherit Bitcoin's rules (bell, list, cap).
"""
import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _strip(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _block(path: Path, header: str) -> str:
    src = _strip(path.read_text())
    start = src.find(header)
    assert start != -1, f"{header!r} not found in {path.name}"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("unbalanced braces")


def test_the_stored_symbol_rule_lives_in_one_place():
    block = _block(_IOS / "Core/Utilities/CryptoSymbol.swift", "static func storedSymbol(for result: StockSearchResult)")
    assert '== "crypto"' in block and "pair(result.ticker)" in block and "result.ticker.uppercased()" in block


def test_add_asset_sheet_pushes_the_stored_spelling_into_the_group():
    block = _block(_IOS / "Views/Screens/TrackingView.swift", "private func addAsset(_ result: StockSearchResult)")
    assert "let symbol = CryptoSymbol.storedSymbol(for: result)" in block
    assert block.count("PortfolioStore.shared.addTicker(symbol)") == 2
    assert "addTicker(result.ticker)" not in block
    assert "onAssetAdded?(symbol)" in block


def test_the_view_model_add_path_uses_the_same_rule():
    block = _block(_IOS / "ViewModels/TrackingViewModel.swift", "func addTickerFromSearch(_ result: StockSearchResult)")
    assert "let symbol = CryptoSymbol.storedSymbol(for: result)" in block


def test_the_search_star_asks_membership_with_the_stored_spelling():
    src = _strip((_IOS / "Views/Molecules/TickerSearchSheet.swift").read_text())
    assert "isInWatchlist(CryptoSymbol.storedSymbol(for: result))" in src
    assert "isInWatchlist(result.ticker)" not in src


def test_alert_lookup_is_scoped_by_the_callers_asset_type():
    block = _block(_IOS / "Core/Services/PriceAlertStore.swift", "func alerts(for ticker: String, assetType: String? = nil)")
    assert 'let callerIsCrypto = (assetType ?? "").lowercased() == "crypto"' in block
    assert "return rowIsCrypto && CryptoSymbol.bare(symbol) == wantedBare" in block
    assert "return !rowIsCrypto && symbol == wanted" in block


@pytest.mark.parametrize("screen, sym, at", [
    ("Views/Screens/CryptoDetailView.swift", "cryptoSymbol", "crypto"),
    ("Views/Screens/ETFDetailView.swift", "etfSymbol", "etf"),
    ("Views/Screens/CommodityDetailView.swift", "commoditySymbol", "commodity"),
    ("Views/Screens/TickerDetailView.swift", "tickerSymbol", "stock"),
    ("Views/Screens/IndexDetailView.swift", "indexSymbol", "index"),
])
def test_every_detail_screen_passes_its_asset_type_to_the_bell(screen, sym, at):
    src = _strip((_IOS / screen).read_text())
    assert f'hasActiveAlerts(ticker: {sym}, assetType: "{at}")' in src


def test_the_sheet_and_the_cap_are_scoped_too():
    sheet = _strip((_IOS / "Views/Screens/PriceAlertsSheet.swift").read_text())
    assert "store.alerts(for: viewModel.ticker, assetType: viewModel.assetType)" in sheet
    vm = _strip((_IOS / "ViewModels/PriceAlertsViewModel.swift").read_text())
    assert "store.activeCount(ticker: ticker, assetType: assetType)" in vm
