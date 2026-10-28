"""The Updates chip strip and Tracking's Holdings list order their tickers with ONE comparator.

Owner request (2026-10-27): the Updates ticker chips must be in exactly the Tracking tab's
order, and changing "Sort By" on Tracking must reorder the chips too. Before this, the sort
lived on `TrackingViewModel` alone (its own UserDefaults keys, its own `switch` of `.sort`
calls) and the chips stayed in the group's stored order whatever the user picked.

The fix is structural, so the guard is too: both lists must go through
`HoldingsSort.ordered`, the sort must be SAVED through `HoldingsSort.save()` (which is what
tells the mounted Updates tab), and Updates must listen. A second, private comparator on
either screen — the easiest regression — would let the two drift with every test green.

There is no XCTest target, so this reads the Swift source: comments stripped (the fix's own
comments name every token asserted here) and each check brace-bound to its declaration
(testing.md §3). Mutation-tested by hand on 2026-10-27: a `.sort {` restored in
`filteredAssets`, a direct `UserDefaults` write in `toggleSort`, a deleted `.onReceive`, a
`return ordered + market`, a dropped `isFinite` guard, a missing post in `setTickers` and a
dropped `market_cap` CodingKey each turn this file red.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_FILES = {
    "sort": _IOS / "Core" / "Utilities" / "HoldingsSort.swift",
    "tracking": _IOS / "ViewModels" / "TrackingViewModel.swift",
    "updates_vm": _IOS / "ViewModels" / "UpdatesViewModel.swift",
    "updates_view": _IOS / "Views" / "Screens" / "UpdatesView.swift",
    "models": _IOS / "Models" / "UpdatesModels.swift",
    "pstore": _IOS / "Core" / "Services" / "PortfolioStore.swift",
}

_LEGACY_KEYS = ('"TrackingView.sortOption"', '"TrackingView.sortAscending"')
# Any in-place or copying sort call — the shape a private comparator would take.
_PRIVATE_SORT = re.compile(r"\.sort\s*[({]|\.sorted\s*[({]")


def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails (keeping a `https://` literal)."""
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return "\n".join(re.sub(r"(?<![:/])//.*$", "", line) for line in src.splitlines())


@pytest.fixture(scope="module")
def src() -> dict[str, str]:
    out = {}
    for key, path in _FILES.items():
        if not path.exists():
            pytest.fail(f"expected file is missing: {path}")
        out[key] = _strip(path.read_text(encoding="utf-8"))
    return out


def _block_at(text: str, idx: int) -> str:
    open_brace = text.find("{", idx)
    assert open_brace != -1, f"no block opens after offset {idx}"
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace : i + 1]
    raise AssertionError(f"unbalanced braces after offset {idx}")


def _block(text: str, header: str) -> str:
    assert text.count(header) == 1, f"expected exactly one {header!r}, found {text.count(header)}"
    return _block_at(text, text.find(header) + len(header))


def _flat(text: str) -> str:
    return " ".join(text.split())


# ── the shared preference ─────────────────────────────────────────────────────

def test_the_preference_keeps_the_keys_tracking_always_wrote(src):
    """A renamed key silently resets every user's chosen sort to Name ↑."""
    sort = src["sort"]
    for key in _LEGACY_KEYS:
        assert key in sort, f"HoldingsSort no longer stores {key}"
    # One owner of those keys: a second writer is a second, unsynchronised preference.
    for name in ("tracking", "updates_vm", "updates_view"):
        for key in _LEGACY_KEYS:
            assert key not in src[name], f"{name} writes {key} itself, bypassing HoldingsSort"


def test_save_stores_both_halves_and_announces_the_change(src):
    save = _flat(_block(src["sort"], "func save(to defaults: UserDefaults = .standard)"))
    assert "defaults.set(option.rawValue, forKey: Self.optionKey)" in save
    assert "defaults.set(ascending, forKey: Self.ascendingKey)" in save
    assert "NotificationCenter.default.post(name: Self.didChangeNotification" in save


def test_an_unset_or_unknown_choice_reads_as_trackings_old_default(src):
    sort = _flat(src["sort"])
    assert "static let defaultValue = HoldingsSort(option: .name, ascending: true)" in sort
    load = _flat(_block(src["sort"], "static func load(from defaults: UserDefaults = .standard) -> HoldingsSort"))
    assert load.startswith("{ var sort = defaultValue")
    assert "AssetSortOption(rawValue: raw)" in load
    assert "defaults.object(forKey: ascendingKey) != nil" in load


# ── the comparator: Tracking's rules, moved verbatim ─────────────────────────────

def test_the_comparator_keeps_trackings_missing_value_rules(src):
    body = _flat(_block(src["sort"], "func precedes(_ lhs: Key, _ rhs: Key) -> Bool"))
    # Market cap: missing sorts last ascending, first descending (the Watchlist rule).
    assert "case (nil, _?): return !ascending" in body
    assert "case (_?, nil): return ascending" in body
    # Date Added: an unknown position after every known one.
    assert "let l: Int = lhs.position ?? Int.max" in body
    assert "let r: Int = rhs.position ?? Int.max" in body
    # Every option handled, each one only deciding when its values DIFFER…
    for case in (".name", ".price", ".change", ".marketCap", ".dateAdded"):
        assert f"case {case}:" in body, f"precedes no longer handles {case}"
    # …so equal values fall to ONE deterministic tie-break, the same on both screens.
    assert body.rstrip(" }").endswith("return lhs.ticker < rhs.ticker")


def test_the_key_cleans_non_finite_numbers(src):
    """A NaN breaks the sort's ordering contract; the cleaning lives in ONE place."""
    init = _flat(_block(src["sort"], "init(ticker: String, price: Double, changePercent: Double, marketCap: Double?, position: Int?)"))
    assert "self.ticker = ticker.uppercased()" in init
    assert "self.price = price.isFinite ? price : 0" in init
    assert "self.changePercent = changePercent.isFinite ? changePercent : 0" in init
    assert "if let cap = marketCap, cap.isFinite { self.marketCap = cap } else { self.marketCap = nil }" in init


# ── Tracking uses it ──────────────────────────────────────────────────────────

def test_holdings_rows_are_ordered_by_the_shared_comparator(src):
    rows = _block(src["tracking"], "var filteredAssets: [TrackedAsset]")
    flat = _flat(rows)
    assert "let sort = HoldingsSort(option: sortOption, ascending: sortAscending)" in flat
    assert "return sort.ordered(assets)" in flat
    assert "position: positions[asset.ticker.uppercased()]" in flat
    assert not _PRIVATE_SORT.search(rows), "filteredAssets sorts on its own again — the chips will drift"


@pytest.mark.parametrize("header", ["func toggleSort()", "func selectSortOption(_ option: AssetSortOption)"])
def test_a_sort_change_is_saved_through_the_shared_preference(src, header):
    """`save()` is what posts the notification the Updates tab listens for."""
    body = _flat(_block(src["tracking"], header))
    assert ").save()" in body
    assert "UserDefaults" not in body


def test_tracking_reads_the_shared_preference_and_follows_other_saves(src):
    init = _block(src["tracking"], "init(apiClient: APIClient = .shared, portfolioStore: PortfolioStore? = nil)")
    flat = _flat(init)
    assert "let storedSort = HoldingsSort.load()" in flat
    assert "self.sortOption = storedSort.option" in flat
    assert "self.sortAscending = storedSort.ascending" in flat
    assert re.search(
        r"NotificationCenter\.default\.publisher\(for: HoldingsSort\.didChangeNotification\)"
        r"[^}]*\.sink \{ \[weak self\] _ in self\?\.applyStoredSort\(\) \}",
        flat,
    ), "the second Tracking instance no longer follows a sort saved by the first"


# ── Updates uses it ───────────────────────────────────────────────────────────

def test_the_chip_strip_is_market_first_then_the_holdings_order(src):
    tabs = _block(src["updates_vm"], "var openableTabs: [NewsFilterTab]")
    flat = _flat(tabs)
    assert "filterTabs.filter { !$0.isLocked }" in flat, "locked chips reach the strip"
    assert "holdingsSort.ordered(tickers)" in flat
    # Unknown price / change sort as 0 — exactly how Tracking's placeholder rows sort.
    assert "price: tab.price ?? 0" in flat
    assert "changePercent: tab.changePercent ?? 0" in flat
    assert "marketCap: tab.marketCap" in flat
    assert "position: positions[tab.scope.uppercased()]" in flat
    assert flat.rstrip(" }").endswith("return market + ordered"), "the Market chip is no longer first"
    assert not _PRIVATE_SORT.search(tabs), "openableTabs sorts on its own — it will drift from Tracking"


def test_updates_holds_the_shared_preference(src):
    vm = _flat(src["updates_vm"])
    assert "@Published private(set) var holdingsSort: HoldingsSort = HoldingsSort.load()" in vm
    reload = _flat(_block(src["updates_vm"], "func reloadHoldingsSort()"))
    assert "let stored = HoldingsSort.load()" in reload
    assert "holdingsSort = stored" in reload


def test_the_updates_screen_follows_a_sort_change_and_a_reorder(src):
    view = _flat(src["updates_view"])
    assert re.search(
        r"\.onReceive\( NotificationCenter\.default\.publisher\(for: HoldingsSort\.didChangeNotification\) \)"
        r" \{ _ in viewModel\.reloadHoldingsSort\(\) \}",
        view,
    ), "Updates no longer re-orders when Sort By changes on Tracking"
    assert re.search(
        r"\.onReceive\( NotificationCenter\.default\.publisher\( for: PortfolioStore\.activeGroupTickersDidChangeNotification \) \)"
        r" \{ _ in Task \{ await viewModel\.reloadForWatchlistChange\(\) \} \}",
        view,
    ), "Updates no longer re-reads the group after a reorder — Date Added goes stale"


def test_a_confirmed_active_group_edit_is_announced(src):
    body = _block(src["pstore"], "func setTickers(_ tickers: [String], in portfolioId: String) async throws")
    flat = _flat(body)
    after = flat[flat.index("try await syncTickers(for: portfolioId)"):]
    # After the server confirmed (the catch rethrows), and only for the ACTIVE group.
    assert re.search(
        r"\} if portfolioId == activePortfolioId \{ NotificationCenter\.default\.post\( "
        r"name: Self\.activeGroupTickersDidChangeNotification, object: nil \) \}",
        after,
    ), "setTickers no longer announces a confirmed active-group edit"


# ── the wire ──────────────────────────────────────────────────────────────────

def test_the_tab_dto_decodes_the_sort_keys_and_cleans_them(src):
    dto = _flat(_block(src["models"], "struct UpdatesTabDTO: Codable, Sendable"))
    assert "let price: Double?" in dto and "let marketCap: Double?" in dto
    keys = _flat(_block(src["models"], "struct UpdatesTabDTO: Codable, Sendable"))
    assert "case price" in keys and 'case marketCap = "market_cap"' in keys
    init = _flat(_block(src["models"], "init(dto: UpdatesTabDTO)"))
    assert "self.price = (dto.price?.isFinite ?? false) ? dto.price : nil" in init
    assert "self.marketCap = (dto.marketCap?.isFinite ?? false) ? dto.marketCap : nil" in init
