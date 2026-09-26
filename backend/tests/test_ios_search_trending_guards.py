"""
Source-scan guards for the search-screen chips (there is no XCTest target).

What must never regress, and why:
  • every search screen shows the chips, with the right scope — the company picker and the
    Updates "Add Ticker" sheet act on equities only;
  • a search pick is recorded ONLY for a tap on a search RESULT row. A chip, a recent-search
    row or the watchlist star recording one would feed "Trending searches" its own output;
  • the remembered picks are cleared when a session ends (auth.md §7);
  • the wording stays neutral — no counts, no "hot"/"top"/"buy";
  • the privacy manifest declares what the app now collects.

Every scan strips comments first (the comments beside these lines name every token asserted
on) and is bounded to one declaration (a token in a sibling type must not satisfy it).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", src)


def _read(rel: str) -> str:
    return _strip((_IOS / rel).read_text())


def _block(src: str, header: str) -> str:
    """The brace-bounded body that follows `header`."""
    start = src.index(header)
    open_brace = src.index("{", start + len(header) - 1 if header.endswith("{") else start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace:i + 1]
    raise AssertionError(f"unbalanced braces after {header!r}")


def _chip_closures(block: str) -> list:
    """The `onItemTapped:` closure of every chips section in `block`."""
    out = []
    for m in re.finditer(r"SearchTrendingChipsSection\(", block):
        call = block[m.start():]
        tap = call.index("onItemTapped:")
        after = call[tap:]
        if after.lstrip("onItemTapped:").lstrip().startswith("{"):
            out.append(_block(after, "onItemTapped:"))
        else:
            out.append(after[:after.index(")")])
    return out


SEARCH_VIEW = _read("Views/Screens/SearchView.swift")
SEARCH_VM = _read("ViewModels/SearchViewModel.swift")
LIVE_SHEET = _block(_read("Views/Molecules/TickerSearchSheet.swift"), "struct TickerLiveSearchSheet")
ADD_ASSET = _block(_read("Views/Screens/TrackingView.swift"), "struct AddAssetSheet")
TARGET = _block(_read("Views/Screens/TargetSearchSheet.swift"), "struct TargetSearchSheet")
UPDATES_SHEET = _block(_read("Views/Screens/UpdatesView.swift"), "struct TickerSearchSheet")
STORE = _read("Services/SearchTrendingStore.swift")
ORGANISM = _read("Views/Organisms/SearchTrendingChipsSection.swift")
MODELS = _read("Models/SearchTrendingModels.swift")


# ── 1. Every surface shows the chips, with the right scope ────────────────────

@pytest.mark.parametrize("name,block,scope", [
    ("TickerLiveSearchSheet", LIVE_SHEET, ".all"),
    ("AddAssetSheet", ADD_ASSET, ".all"),
    ("TargetSearchSheet", TARGET, ".stocksOnly"),
    ("Updates TickerSearchSheet", UPDATES_SHEET, ".stocksOnly"),
])
def test_each_sheet_renders_the_chips_with_its_scope(name, block, scope):
    assert "SearchTrendingChipsSection(" in block, f"{name} lost its chips"
    assert f"SearchTrendingStore.shared.sections(for: {scope})" in block, (
        f"{name} must show the {scope} variant"
    )
    assert "SearchTrendingStore.shared.prefetch()" in block, f"{name} never fetches the lists"


def test_home_search_renders_the_chips_from_its_viewmodel():
    body = _block(SEARCH_VIEW, "var body: some View")
    assert "SearchTrendingChipsSection(" in body
    assert "viewModel.trendingSections" in body and "viewModel.openTrendingItem" in body
    assert "RecentSearchesSection(" in body, "the history must still render (its own guard too)"
    assert "viewModel.prefetchTrending()" in SEARCH_VIEW
    assert "trending.sections(for: .all)" in _block(SEARCH_VM, "var trendingSections")


# ── 2. Picks are recorded ONLY for a search RESULT row ────────────────────────

@pytest.mark.parametrize("name,block", [
    ("TickerLiveSearchSheet", LIVE_SHEET),
    ("AddAssetSheet", ADD_ASSET),
    ("TargetSearchSheet", TARGET),
    ("Updates TickerSearchSheet", UPDATES_SHEET),
])
def test_a_result_row_records_a_pick_and_a_chip_never_does(name, block):
    assert "SearchTrendingStore.shared.recordPick(" in block, f"{name} never records a pick"
    closures = _chip_closures(block)
    assert closures, f"{name}: no chip closure found — the scan is vacuous"
    for closure in closures:
        assert "recordPick" not in closure, f"{name}: a chip tap records a pick (feedback loop)"


def test_home_records_only_from_a_result_selection():
    assert "trending.recordPick(" in _block(SEARCH_VM, "func selectSearchResult")
    for fn in ("func openTrendingItem", "func openHistoryEntry"):
        assert "recordPick" not in _block(SEARCH_VM, fn), f"{fn} must not record a pick"
    assert "recordPick" not in _block(SEARCH_VIEW, "private func handleHistoryTapped")


def test_the_tracking_star_never_records_a_pick():
    star = _block(LIVE_SHEET, "if let isInWatchlist, let onAddToWatchlist")
    assert "onAddToWatchlist(result)" in star, "anti-vacuity: this is the star block"
    assert "recordPick" not in star


def test_the_add_path_itself_never_records_a_pick():
    """Chips call `addAsset` too — the pick must be recorded at the ROW, not inside it."""
    assert "recordPick" not in _block(ADD_ASSET, "private func addAsset")


def test_a_chip_built_search_row_keeps_its_type():
    assert "func openTrendingItem" in SEARCH_VM
    item = _block(MODELS, "struct SearchTrendingItem: Identifiable")
    assert 'var id: String { "\\(symbol)_\\(type)" }' in item, "byte-equal to SearchSelection.id"
    assert "type: type" in _block(item, "var stockSearchResult: StockSearchResult")


# ── 3. The store: session end, de-dup window, observation ─────────────────────

def test_the_session_end_funnel_clears_the_store():
    appstate = _read("Core/State/AppState.swift")
    funnel = _block(appstate, "func discardDataForEndedSession")
    assert "SearchTrendingStore.shared.clearForEndedSession()" in funnel


def test_the_store_is_observable_and_forgets_on_session_end():
    assert "@Observable" in STORE and "final class SearchTrendingStore" in STORE
    clear = _block(STORE, "func clearForEndedSession")
    assert "defaults.removeObject(forKey: Self.countedPicksKey)" in clear
    assert "epoch += 1" in clear


def test_a_pick_is_sent_at_most_once_per_window_and_remembered_after_success():
    record = _block(STORE, "func recordPick")
    assert "today - last < Self.pickWindowDays" in record
    assert "pendingPicks.insert(key).inserted" in record
    success = record[record.index("try await self.apiClient.request"):]
    assert success.index("guard self.epoch == started") < success.index("saveCountedPicks")
    assert "try?" not in record, "no silent failure (auth.md §6)"
    assert "ordinality(of: .day" in STORE, "ET calendar days, not 168 hours"


# ── 4. The organism: eager, neutral, honest ───────────────────────────────────

def test_the_organism_is_eager_and_says_it_is_not_a_recommendation():
    body = _block(ORGANISM, "var body: some View")
    assert "Lazy" not in body
    assert "Not a recommendation." in ORGANISM
    assert "Based on activity in Caydex." in ORGANISM
    assert "sections.contains(where: \\.isLive)" in ORGANISM


def _string_literals(src: str) -> list:
    return re.findall(r'"((?:[^"\\]|\\.)*)"', src)


def test_the_wording_is_neutral():
    title = _block(MODELS, "var title: String")
    literals = _string_literals(ORGANISM) + _string_literals(title)
    assert literals, "anti-vacuity"
    banned = re.compile(r"\b(hot|buy|sell|top|best|winners?|must|🔥)\b|🔥", re.I)
    offenders = [s for s in literals if banned.search(s)]
    assert not offenders, offenders
    assert "\\(windowDays) days" in title, "the window is stated, never hidden"
    assert not re.search(r"\\\((count|picks|adders|rank)", title)


# ── 5. The Updates sheet's hardcoded list is gone ─────────────────────────────

def test_the_hardcoded_popular_list_and_its_broken_symbol_are_gone():
    assert "popularTickers" not in UPDATES_SHEET
    assert '"BRK.B"' not in _read("Views/Screens/UpdatesView.swift")
    assert '"BRK.B"' not in MODELS


# ── 6. Privacy manifest ───────────────────────────────────────────────────────

def test_the_privacy_manifest_declares_unlinked_search_history():
    manifest = (_IOS / "PrivacyInfo.xcprivacy").read_text()
    endpoint = _read("Core/Services/APIEndpoint.swift")
    sends_picks = "case recordSearchPick" in endpoint
    m = re.search(
        r"<string>NSPrivacyCollectedDataTypeSearchHistory</string>\s*"
        r"<key>NSPrivacyCollectedDataTypeLinked</key>\s*<(true|false)/>\s*"
        r"<key>NSPrivacyCollectedDataTypeTracking</key>\s*<(true|false)/>",
        manifest,
    )
    assert bool(m) == sends_picks, "the manifest and the pick endpoint must agree"
    if m:
        assert m.group(1) == "false" and m.group(2) == "false", "search picks are NOT linked"


# ── 7. Review fixes (2026-09-26) ──────────────────────────────────────────────

def test_the_device_key_is_the_security_class_like_the_server():
    """The SQL sums a symbol's stock/etf/fund picks, so the de-dup key must collapse them
    too — or one device re-sends AAPL under each type (the server now refuses; the device
    should not try)."""
    record = _block(STORE, "func recordPick")
    assert 'let key = "\\(sym)_\\(kind == "crypto" ? "crypto" : "security")"' in record


@pytest.mark.parametrize("name,block", [
    ("TickerLiveSearchSheet", LIVE_SHEET),
    ("AddAssetSheet", ADD_ASSET),
    ("TargetSearchSheet", TARGET),
])
def test_the_chips_get_the_free_height_not_half_of_it(name, block):
    """These stacks end in a flexible Spacer; without a priority the chips' ScrollView got
    half the space under the field and the second section was cut off above a blank band."""
    chips = block[block.index("SearchTrendingChipsSection("):]
    window = chips[:chips.index(".layoutPriority(1)") + 20] if ".layoutPriority(1)" in chips else ""
    assert window and ".scrollDismissesKeyboard(.interactively)" in window, (
        f"{name}: the chips ScrollView lost `.layoutPriority(1)`"
    )


def test_a_symbol_under_two_types_gets_distinct_chip_titles():
    """BTC the coin and BTC the ETF must not be two chips both reading "BTC"."""
    assert "chipTitle($0, ambiguous: ambiguous)" in _block(ORGANISM, "var body: some View")
    title = _block(ORGANISM, "private func chipTitle")
    assert "guard ambiguous.contains(item.symbol) else { return item.symbol }" in title
    assert '"\\(item.symbol) · \\(label)"' in title
    assert "typesBySymbol.filter { $0.value.count > 1 }" in ORGANISM
