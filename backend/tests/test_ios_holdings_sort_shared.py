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

2026-10-31 (owner report: "manage tickers feature doesn't work"): Custom sort + the auto-switch
on a reorder, unknown values last, the direction shown as state, the remove-fully rule, the
per-portfolio write chain and the announcement moved into `syncTickers`. The comparator's
BEHAVIOUR is executed in test_ios_holdings_sort_behaviour.py; this file pins the wiring.
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
    "tracking_models": _IOS / "Models" / "TrackingModels.swift",
    "pstore": _IOS / "Core" / "Services" / "PortfolioStore.swift",
    "header": _IOS / "Views" / "Molecules" / "PortfolioHeaderBar.swift",
    "manage": _IOS / "Views" / "Organisms" / "ManageTickersSheet.swift",
    "edit": _IOS / "Views" / "Organisms" / "EditPortfolioSheet.swift",
    "tracking_view": _IOS / "Views" / "Screens" / "TrackingView.swift",
    "home_view": _IOS / "Views" / "Screens" / "HomeDashboardView.swift",
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


# ── the comparator (its behaviour is EXECUTED in test_ios_holdings_sort_behaviour.py) ──

def test_the_comparator_puts_unknown_values_last_and_ties_by_ticker(src):
    """Owner decision 2026-10-31: an unknown value sorts LAST in both directions. It used to sort
    as 0 (price / change) or first-when-descending (market cap)."""
    body = _flat(_block(src["sort"], "func precedes(_ lhs: Key, _ rhs: Key) -> Bool"))
    for case in (".custom", ".name", ".price", ".change", ".marketCap", ".dateAdded"):
        assert f"case {case}:" in body, f"precedes no longer handles {case}"
    # Custom is the user's order, top to bottom: the stored direction never applies.
    assert "decided = Self.knownFirst(lhs.position, rhs.position, ascending: true)" in body
    assert "decided = Self.knownFirst(lhs.addedAt, rhs.addedAt, ascending: ascending)" in body
    # Equal / both-unknown values fall to ONE deterministic tie-break, the same on both screens.
    assert body.rstrip(" }").endswith("return decided ?? (lhs.ticker < rhs.ticker)")
    known = _flat(_block(src["sort"], "private static func knownFirst<V: Comparable>(_ lhs: V?, _ rhs: V?, ascending: Bool) -> Bool?"))
    assert "case (.some, .none): return true" in known and "case (.none, .some): return false" in known, (
        "a known value no longer precedes an unknown one in EVERY direction")
    assert "case (nil, _?): return !ascending" not in _flat(src["sort"]), "the descending-first nil rule is back"


def test_the_key_treats_non_finite_numbers_as_unknown(src):
    """A NaN breaks the sort's ordering contract; the cleaning lives in ONE place."""
    key = _flat(_block(src["sort"], "struct Key"))
    assert "self.ticker = ticker.uppercased()" in key
    for field in ("price", "changePercent", "marketCap"):
        assert f"self.{field} = Self.finite({field})" in key
    finite = _flat(_block(src["sort"], "private static func finite(_ value: Double?) -> Double?"))
    assert "guard let value, value.isFinite else { return nil }" in finite


def test_the_option_lives_beside_the_comparator(src):
    """Foundation-only, so the executed test can compile it; one declaration, no second copy."""
    assert "enum AssetSortOption: String, CaseIterable" in src["sort"]
    assert "enum AssetSortOption" not in src["tracking_models"], "AssetSortOption is declared twice"
    assert "import SwiftUI" not in src["sort"], "HoldingsSort.swift can no longer run under `xcrun swift -`"


# ── Tracking uses it ──────────────────────────────────────────────────────────

def test_holdings_rows_are_ordered_by_the_shared_comparator(src):
    rows = _block(src["tracking"], "var filteredAssets: [TrackedAsset]")
    flat = _flat(rows)
    assert "let sort = HoldingsSort(option: sortOption, ascending: sortAscending)" in flat
    assert "return sort.ordered(assets)" in flat
    assert "position: positions[asset.ticker.uppercased()]" in flat
    # A "—" row's placeholder is UNKNOWN (nil), never its 0.0 wire value.
    assert "price: asset.priceKnown ? asset.price : nil" in flat
    assert "changePercent: asset.changeKnown ? asset.changePercent : nil" in flat
    assert "addedAt: asset.addedAt" in flat
    assert not _PRIVATE_SORT.search(rows), "filteredAssets sorts on its own again — the chips will drift"


@pytest.mark.parametrize("header", [
    "func setSortDirection(ascending: Bool)",
    "func selectSortOption(_ option: AssetSortOption)",
    "private func useCustomOrder()",
])
def test_a_sort_change_is_saved_through_the_shared_preference(src, header):
    """`save()` is what posts the notification the Updates tab listens for."""
    body = _flat(_block(src["tracking"], header))
    assert ").save()" in body
    assert "UserDefaults" not in body


def test_picking_an_option_starts_in_its_natural_direction(src):
    body = _flat(_block(src["tracking"], "func selectSortOption(_ option: AssetSortOption)"))
    assert body.startswith("{ guard option != sortOption else { return }"), "re-tapping the checked option resets it"
    assert "sortAscending = option.naturalAscending" in body
    assert "func toggleSort()" not in src["tracking"], "the action-labelled direction toggle is back"


def test_the_menu_shows_the_direction_as_state(src):
    """The single row named the ACTION ("Descending ↓" while ascending), which read as state."""
    panel = _block(src["header"], "private var optionsPanel: some View")
    flat = _flat(panel)
    assert "ForEach(AssetSortOption.allCases" in flat, "the menu no longer lists every option"
    assert "if let labels = viewModel.sortOption.directionLabels" in flat
    assert "viewModel.setSortDirection(ascending: true)" in flat
    assert "viewModel.setSortDirection(ascending: false)" in flat
    assert "isChecked: viewModel.sortAscending" in flat and "isChecked: !viewModel.sortAscending" in flat
    assert '"Descending"' not in panel and '"Ascending"' not in panel


def test_a_reorder_on_the_active_group_switches_to_custom(src):
    """The reported bug: a Manage Tickers drag never moved a card because the list was sorted
    by Name. The drag now switches Sort By to Custom, at drag time, for the active group only."""
    move = _block(src["tracking"], "func moveTickers(in portfolioId: String, to newOrder: [String])")
    flat = _flat(move)
    assert flat.startswith("{ guard canEditPortfolio else { reportPortfolioStillLoading("), (
        "moveTickers no longer OPENS with the live-data guard")
    assert "guard let write = portfolioStore.startSetTickers(newOrder, in: portfolioId) else { return }" in flat
    assert "let isActiveGroup = portfolioId == portfolioStore.activePortfolioId" in flat
    assert re.search(r"if isActiveGroup \{ useCustomOrder\(\) \}", flat), (
        "a reorder of the active group no longer switches Sort By to Custom")
    assert flat.index("startSetTickers(") < flat.index("useCustomOrder()"), "the switch runs for a no-op drag"
    catch = _flat(_block_at(move, move.index("} catch {") + 2))
    assert 'AppActions.shared.reportMutationFailure(error, action: "save the new order")' in catch
    assert "print(" not in move


def test_both_ticker_sheets_edit_through_the_shared_paths(src):
    """Manage Tickers and Edit Portfolio share the Custom switch, the remove-fully rule and the
    failure reporting; their own catches used to `print` (auth.md §6) or keep a sticky banner."""
    manage = src["manage"]
    assert "viewModel.moveTickers(in: portfolioId, to: reordered)" in manage
    assert "viewModel.removeTickers(toRemove, from: portfolioId)" in manage
    edit_move = _flat(_block(src["edit"], "private func handleMoveTicker(from source: IndexSet, to destination: Int)"))
    assert "viewModel.moveTickers(in: portfolio.id, to: updated)" in edit_move
    edit_delete = _flat(_block(src["edit"], "private func handleDeleteTicker(at offsets: IndexSet)"))
    assert "viewModel.removeTickers(toRemove, from: portfolio.id)" in edit_delete
    for name in ("manage",):
        assert "print(" not in src[name], f"{name} reports a failure with print again"
        assert "portfolioStore.setTickers(" not in src[name] and "portfolioStore.removeTicker(" not in src[name]
    assert "errorMessage" not in manage, "the sticky local banner is back"


def test_both_ticker_sheets_can_show_a_failure(src):
    """A sheet draws ABOVE the root's error toast, so every failure reported from inside one was
    invisible (ErrorPresentationHost.swift). Both presentation sites carry the host and re-inject
    the app state (the key's default is a throwaway AppState)."""
    view = _flat(src["tracking_view"])
    for sheet in ("EditPortfolioSheet(viewModel: viewModel)", "ManageTickersSheet(viewModel: viewModel)"):
        sites = [m.start() for m in re.finditer(re.escape(sheet), view)]
        assert len(sites) == 2, f"expected two presentation sites of {sheet}"
        for at in sites:
            tail = view[at: at + 240]
            assert ".errorPresentationHost()" in tail, f"{sheet} has no error host — its failures draw behind it"
            assert ".environment(\\.appState, appState)" in tail, f"{sheet} relies on implicit appState inheritance"


def test_remove_fully_is_server_checked_and_declares_the_class(src):
    """Owner decision 2026-10-31: a ticker no other group holds leaves the watchlist too. The
    delete strips the ticker from EVERY group server-side, so it is sent only after this group's
    own write was confirmed, with `onlyIfUnreferenced` (the server re-checks every group) and a
    declared asset class (an undeclared bare ticker can fall through to the coin pair)."""
    finish = _flat(_block(src["tracking"], "private func finishRemovals("))
    guard = re.search(r"guard let confirmed, !confirmed\.contains\(symbol\), portfolioStore\.hasLiveData, "
                      r"!isHeldByAnyGroup\(symbol\) else \{ continue \}", finish)
    assert guard, "remove-fully no longer waits for the confirmed write / the live store / the other-group check"
    assert finish.index(guard.group(0)) < finish.index("await removeFromWatchlistIfUnreferenced(symbol)")
    delete = _flat(_block(src["tracking"], "private func removeFromWatchlistIfUnreferenced(_ symbol: String) async"))
    assert "endpoint: .removeFromWatchlist(stockId: symbol, assetType: assetType, onlyIfUnreferenced: true)" in delete
    assert "guard response.removed != false else { return }" in delete
    assert delete.index("guard response.removed != false") < delete.index("PortfolioStore.announceWatchlistChange(")
    assert 'AppActions.shared.reportMutationFailure(error, action: "remove \\(symbol) from your watchlist")' in delete
    remove = _flat(_block(src["tracking"], "func removeTickers(_ tickers: [String], from portfolioId: String)"))
    assert remove.startswith("{ guard canEditPortfolio else { reportPortfolioStillLoading(")
    assert "portfolioStore.startRemoveTicker(symbol, from: portfolioId)" in remove


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
    # Unknown price / change are passed as nil (sorted last) — exactly as Tracking passes its
    # placeholder rows. `?? 0` made them sort as a $0 stock / a flat day.
    assert "price: tab.price," in flat and "?? 0" not in flat
    # 2 dp for the key only, matching Tracking's 2-dp feed values (the server leaves the
    # pill's change unrounded so its 1-dp label is not double-rounded).
    assert "changePercent: tab.changePercent.map { ($0 * 100).rounded() / 100 }," in flat
    assert "marketCap: tab.marketCap" in flat
    assert "addedAt: tab.addedAt" in flat
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
    ), "Updates no longer re-reads the group after a reorder — Custom goes stale"


_SYNC = "private func syncTickers(for portfolioId: String, tickers: [String], ticket: Int, epoch: Int) async throws -> MembershipLinkResult"


def _sync_block(src) -> str:
    flat_src = _flat(src["pstore"])
    assert flat_src.count(_SYNC) == 1, "syncTickers' signature moved — this scan has drifted"
    return _block_at(flat_src, flat_src.index(_SYNC) + len(_SYNC))


def test_a_confirmed_active_group_edit_is_announced(src):
    """EVERY confirmed membership change on the active group is announced — add, remove,
    reorder, purge — once per burst, on the NEWEST answer (an older answer's unordered Updates
    reload could land last), carrying whether any write in the burst changed membership.
    Only `setTickers` used to post, so a removed ticker stayed on the Updates chips."""
    sync = _sync_block(src)
    confirmed = sync[sync.index("guard epoch == identityEpoch else { return .abandoned } let serverItems"):]
    assert "unannouncedChanges[portfolioId] = (unannouncedChanges[portfolioId] ?? false) || membershipChanged" in confirmed, (
        "an older confirmed write's membership change is no longer carried to the burst's post")
    m = re.search(r"if portfolioId == activePortfolioId \{", confirmed)
    assert m, "syncTickers no longer has the active-group block after the server confirmed"
    active = _block_at(confirmed, m.start())
    assert "WidgetRefreshService.shared.contentChanged()" in active
    assert re.search(r"if isNewest \{ announceConfirmedChanges\(for: portfolioId\) \}", active), (
        "the announcement is no longer limited to the newest answer")
    failed = sync[sync.index("} catch {"): sync.index("throw error")]
    assert "announceConfirmedChanges(for: portfolioId)" in failed, (
        "a failed newest write no longer announces the older writes the server DID confirm")
    announce = _flat(_block(src["pstore"], "private func announceConfirmedChanges(for portfolioId: String)"))
    assert "guard let changed = unannouncedChanges.removeValue(forKey: portfolioId), portfolioId == activePortfolioId else { return }" in announce
    assert re.search(
        r"NotificationCenter\.default\.post\( name: Self\.activeGroupTickersDidChangeNotification, "
        r"object: nil, userInfo: \[Self\.membershipChangedKey: changed\] \)", announce,
    ), "the confirmed change is no longer posted with whether the ticker SET changed"
    set_tickers = _flat(_block(src["pstore"], "func setTickers(_ tickers: [String], in portfolioId: String) async throws"))
    assert "activeGroupTickersDidChangeNotification" not in set_tickers, "a second, earlier poster is back"


def test_only_the_newest_write_touches_the_screen(src):
    """Each gesture's PUT used to apply its own answer: a late one rolled back a newer order, and
    a failure reverted to a list captured before it, wiping newer edits."""
    sync = _sync_block(src)
    failed = sync[sync.index("} catch {"): sync.index("throw error")]
    assert "guard writeSequencer.isNewest(portfolioId, ticket: ticket) else { return .superseded(error) }" in failed, (
        "an older write's failure is final again (the newer write carries its edit)")
    assert failed.index("guard epoch == identityEpoch else { return .abandoned }") < failed.index(
        "guard writeSequencer.isNewest(portfolioId, ticket: ticket) else { return .superseded(error) }"), (
        "a revert can land in the next session")
    assert failed.index("else { return .superseded(error) }") < failed.index("portfolios[index].items = confirmed"), (
        "an older failure reverts again (it would wipe the newer edit already on screen)")
    ok = sync[sync.index("let serverItems"):]
    assert "confirmedItems[portfolioId] = serverItems" in ok
    assert re.search(r"if isNewest, let index = portfolios\.firstIndex\(where: \{ \$0\.id == portfolioId \}\) "
                     r"\{ portfolios\[index\]\.items = serverItems \}", ok), "an older answer can overwrite a newer edit"
    enqueue = _flat(_block(src["pstore"], "private func enqueueMembershipWrite("))
    assert "guard let self, epoch == self.identityEpoch else { return .abandoned }" in enqueue, (
        "a link from an ended session can still send (no epoch check in the link)")
    assert enqueue.index("await previous?.value") < enqueue.index(
        "guard let self, epoch == self.identityEpoch else { return .abandoned }"), (
        "the epoch is not re-checked after the wait")
    assert "guard self.portfolios.contains(where: { $0.id == portfolioId }) else { return .abandoned }" in enqueue, (
        "a write queued for a since-deleted group still sends (and reports a failure)")
    assert "writeTails[portfolioId] = Task" in enqueue, "writes to one portfolio no longer wait for each other"
    reset = _flat(_block(src["pstore"], "func reset()"))
    for token in ("writeSequencer.reset()", "writeTails = [:]", "confirmedItems = [:]", "unannouncedChanges = [:]"):
        assert token in reset, f"reset() no longer clears `{token}` — the next session inherits the chain"


def test_a_superseded_failure_is_decided_by_the_settled_list(src):
    """A write that failed while a newer one was queued is NOT a final failure: the newer write
    sends the full list, this edit included. It waits for the group's queue to settle, then
    succeeds when the confirmed list holds its edit — and only then may remove-fully run."""
    settle = _flat(_block(src["pstore"], "private func settledOutcome("))
    assert "while epoch == identityEpoch, writeSequencer.hasPending(portfolioId), let tail = writeTails[portfolioId], tail != lastAwaited" in settle, (
        "the settle loop no longer waits for the queue (or can spin on a finished tail)")
    assert "guard let confirmed = confirmedItems[portfolioId]?.map(\\.ticker), satisfied(confirmed) else { throw error }" in settle
    remove = _flat(_block(src["pstore"], "func startRemoveTicker(_ ticker: String, from portfolioId: String) -> Task<[String]?, Error>?"))
    assert "satisfied: { !$0.contains(symbol) }" in remove
    finish = _flat(_block(src["tracking"], "private func finishRemovals("))
    assert "!isHeldByAnyGroup(symbol) else { continue }" in finish, (
        "remove-fully ignores the edited group (a quick re-add refills it while the removal flies)")


def test_a_failed_drag_gives_back_the_sort_it_replaced(src):
    move = _flat(_block(src["tracking"], "func moveTickers(in portfolioId: String, to newOrder: [String])"))
    assert "let replacedSort: HoldingsSort? = (isActiveGroup && sortOption != .custom)" in move
    assert "if let replacedSort, sortOption == .custom {" in move and "replacedSort.save()" in move


def test_a_load_that_raced_a_write_keeps_the_local_membership(src):
    """A GET issued before a PUT and answering after it put the OLD order back on screen — the
    reported "my reorder did nothing" — and the next whole-list write sent it to the server."""
    load = _flat(_block(src["pstore"], "private func performLoad() async -> Bool"))
    assert load.index("let revisionsAtRequest = writeSequencer.revisionSnapshot()") < load.index("requestReturningBody(")
    assert "let loaded = keepingLocalMembership(over: fetched, revisionsAtRequest: revisionsAtRequest)" in load
    assert load.index("keepingLocalMembership(") < load.index("self.portfolios = loaded")


def test_home_follows_a_confirmed_group_edit(src):
    view = _flat(src["home_view"])
    assert re.search(
        r"\.onReceive\( NotificationCenter\.default\.publisher\( for: PortfolioStore\.activeGroupTickersDidChangeNotification \) \)"
        r" \{ _ in Task \{ await viewModel\.reloadForWatchlistChange\(isActiveTab: isActiveTab\) \} \}",
        view,
    ), "Home's strip no longer reloads after a reorder / remove on the active group"


# ── the wire ──────────────────────────────────────────────────────────────────

def test_the_tab_dto_decodes_the_sort_keys_and_cleans_them(src):
    dto = _flat(_block(src["models"], "struct UpdatesTabDTO: Codable, Sendable"))
    assert "let price: Double?" in dto and "let marketCap: Double?" in dto
    keys = _flat(_block(src["models"], "struct UpdatesTabDTO: Codable, Sendable"))
    assert "case price" in keys and 'case marketCap = "market_cap"' in keys
    assert "let addedAt: String?" in dto and 'case addedAt = "added_at"' in keys
    init = _flat(_block(src["models"], "init(dto: UpdatesTabDTO)"))
    assert "self.price = (dto.price?.isFinite ?? false) ? dto.price : nil" in init
    assert "self.marketCap = (dto.marketCap?.isFinite ?? false) ? dto.marketCap : nil" in init
    assert "self.addedAt = HoldingsSort.parseAddedAt(dto.addedAt)" in init


def test_the_feed_dto_decodes_the_add_date_leniently(src):
    dto = _flat(_block(src["tracking_models"], "struct TrackedAssetDTO: Codable, Identifiable"))
    assert "let addedAt: String?" in dto and 'case addedAt = "added_at"' in dto
    assert "addedAt = lenient(String.self, .addedAt)" in dto, "a malformed date can fail the whole row"
    assert "addedAt: HoldingsSort.parseAddedAt(addedAt)" in dto
