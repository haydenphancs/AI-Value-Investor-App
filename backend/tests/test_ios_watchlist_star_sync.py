"""Guards for the watchlist-changed signal between the detail screens and the Tracking tab.

WHY THIS FILE EXISTS. TestFlight 1.0 (8), Tracking › NVDA detail › un-star › back: the NVDA row
was still in the Tracking list until a pull-to-refresh. Every one of the five detail-screen
`toggleFavorite()` bodies flipped its own star and called `POST`/`DELETE /watchlist`, and told
nobody. `TrackingViewModel` subscribed only to whale signals, its load is latched on
`hasLoadedOnce`, `.task(id: isActiveTab)` cannot re-fire on a pop (the detail is pushed inside
Tracking's own NavigationStack), and the 30 s timer reloads only the feed — so a removal healed
itself only during market hours and an add never did (the Tracking list is feed ∩ ACTIVE GROUP,
and the group membership comes from `GET /portfolios`).

The fix is one post-CONFIRM signal, `PortfolioStore.watchlistDidChangeNotification`, posted by
each star inside its request task's success branch, and a handler on `TrackingViewModel` that
patches the list locally, marks an add in `recentlyAddedTickers`, then reconciles from the
server strictly behind the write. Each rule below is a shipped or reviewed failure mode:

  * post AFTER `try await ... request(...)`, never on the optimistic flip and never in the
    `catch` — `WhaleService.followsDidChangeNotification` documents the race: a refetch keyed to
    the flip overtakes the write and reads the pre-toggle state (Views/Screens/WhaleService.swift).
  * the crypto star announces the PAIR spelling — the feed row is `BTCUSD` (migration 160); a
    bare `BTC` matches nothing and names the Grayscale ETF.
  * an add inserts its `recentlyAddedTickers` marker BEFORE the reload and clears it AFTER:
    `performLoad` purges every group ticker missing from the feed, and a feed build already in
    flight when the POST landed can re-cache the pre-add list (project_live_surfaces_hardening,
    "second purge path").
  * the reconcile awaits a running load before issuing its own — joining it would adopt the
    pre-toggle state — and never calls `refresh()` (no pull-to-refresh spinner for a change the
    user did not make on this tab).
  * a sign-out mid-debounce cancels the reconcile and drops the markers (auth.md §7).
  * Updates › Manage Assets posts too (same watchlist, same active group) but IGNORES its own
    source, or every toggle there would fetch the tabs twice.

Per `.claude/rules/testing.md` §3 and `project_source_scan_guard_vacuity`, every scan is
comment-stripped and brace-bounded; each was mutation-tested by hand (see the STATUS log).
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"

_TRACKING_VM = _IOS / "ViewModels/TrackingViewModel.swift"
_PORTFOLIO_STORE = _IOS / "Core/Services/PortfolioStore.swift"
_UPDATES_VM = _IOS / "ViewModels/UpdatesViewModel.swift"
_UPDATES_VIEW = _IOS / "Views/Screens/UpdatesView.swift"
_HOME_VIEW = _IOS / "Views/Screens/HomeDashboardView.swift"

# (file, the symbol expression announced, the wire class announced)
_DETAIL_STARS = [
    ("ViewModels/TickerDetailViewModel.swift", "tickerSymbol", "stock"),
    ("ViewModels/CryptoDetailViewModel.swift", "CryptoSymbol.pair(cryptoSymbol)", "crypto"),
    ("ViewModels/ETFDetailViewModel.swift", "etfSymbol", "etf"),
    ("ViewModels/IndexDetailViewModel.swift", "indexSymbol", "index"),
    ("ViewModels/CommodityDetailViewModel.swift", "commoditySymbol", "commodity"),
]

_ANNOUNCE = "PortfolioStore.announceWatchlistChange("


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails — the explanatory comments beside every fix
    here quote `announceWatchlistChange`, `request(` and `catch`."""
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str, *, start_at: int = 0) -> str:
    """The brace-balanced body of a declaration, comments stripped."""
    start = src.find(header, start_at)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_brace : i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


def _on_receive_closure(struct_body: str, name_token: str) -> str:
    """The trailing closure `{ … }` of the `.onReceive(` whose publisher names
    ``name_token`` — so an assertion about what the subscription DOES cannot be satisfied
    by the sibling `activeGroupDidChangeNotification` receiver in the same struct."""
    at = struct_body.index(name_token)
    close_paren = struct_body.index(")", at)
    # skip to the `{` that opens the trailing closure
    open_brace = struct_body.index("{", close_paren)
    depth = 0
    for i in range(open_brace, len(struct_body)):
        if struct_body[i] == "{":
            depth += 1
        elif struct_body[i] == "}":
            depth -= 1
            if depth == 0:
                return struct_body[open_brace : i + 1]
    pytest.fail("unbalanced .onReceive closure")


# ── 0. Anti-vacuity ────────────────────────────────────────────────────────────


def test_the_comment_stripper_actually_strips():
    """A phrase that exists ONLY in a comment must be gone after stripping, or every absence
    assertion below passes on prose."""
    raw = _read(_TRACKING_VM)
    phrase = "A star tapped on a detail screen"
    assert phrase in raw, "scan drifted — the handler's explanatory comment moved"
    assert phrase not in _strip_comments(raw)


# ── 1. The five stars post after the server confirms ──────────────────────────


@pytest.mark.parametrize("rel, symbol_expr, wire_class", _DETAIL_STARS)
def test_every_detail_star_announces_after_the_server_confirms(rel, symbol_expr, wire_class):
    body = _decl_block(_read(_IOS / rel), "func toggleFavorite()")

    # Anti-vacuity: this is the real toggle (the slow-GET guard pins the same line).
    assert "favoriteToggleGeneration &+= 1" in body, f"{rel}: scan drifted"
    assert "request(" in body, f"{rel}: scan drifted — no request in toggleFavorite"

    assert _ANNOUNCE in body, f"{rel}: toggleFavorite no longer announces the change"
    announce_at = body.index(_ANNOUNCE)

    # Inside the request task, not after it (a post after the Task fires before the request).
    task = _decl_block(body, "Task { @MainActor in")
    assert _ANNOUNCE in task, f"{rel}: the announce must sit inside the request Task"

    # After BOTH request arms, so the server already holds the change.
    assert body.rindex("request(") < announce_at, (
        f"{rel}: announce posted before the request — a refetch keyed to the optimistic flip "
        "overtakes the write and reads the pre-toggle state"
    )

    # Never in the catch: a reverted toggle has no server-side change to read.
    assert announce_at < body.index("} catch {"), f"{rel}: announce placed after the catch"
    catch_block = _decl_block(body, "} catch {")
    assert _ANNOUNCE not in catch_block, f"{rel}: announce inside the catch"

    call = body[announce_at : body.index(")", body.index("source:", announce_at))]
    assert f"ticker: {symbol_expr}" in call, f"{rel}: announces the wrong symbol expression"
    assert f'assetType: "{wire_class}"' in call, f"{rel}: announces the wrong wire class"
    assert "added: !wasInWatchlist" in call, f"{rel}: `added` must be the post-toggle state"
    assert "source: .detailStar" in call


def test_the_crypto_star_announces_the_stored_pair_spelling():
    body = _decl_block(_read(_IOS / "ViewModels/CryptoDetailViewModel.swift"), "func toggleFavorite()")
    call = body[body.index(_ANNOUNCE) :]
    assert "ticker: CryptoSymbol.pair(cryptoSymbol)" in call, (
        "the crypto star must announce the PAIR — the feed row is BTCUSD, and a bare BTC is the "
        "Grayscale ETF"
    )


# ── 2. The signal itself ───────────────────────────────────────────────────────


def test_the_signal_lives_on_portfolio_store_and_uppercases():
    src = _strip_comments(_read(_PORTFOLIO_STORE))
    assert "static let watchlistDidChangeNotification = Notification.Name(" in src
    announce = _decl_block(src, "static func announceWatchlistChange(")
    assert "name: watchlistDidChangeNotification" in announce
    assert "WatchlistChange(" in announce
    assert "ticker.uppercased()" in announce, "the payload must carry the uppercased stored spelling"

    # File-scope payload, Sendable — not nested in the @MainActor class.
    assert re.search(r"^struct WatchlistChange: Sendable", src, re.M), (
        "WatchlistChange must be a file-scope Sendable struct"
    )
    payload = _decl_block(src, "struct WatchlistChange: Sendable")
    assert "init?(_ notification: Notification)" in payload
    assert "case detailStar" in payload and "case updates" in payload


# ── 3. TrackingViewModel: subscribe, patch, reconcile behind the write ────────


def test_tracking_vm_subscribes_in_init():
    init = _decl_block(
        _read(_TRACKING_VM),
        "init(apiClient: APIClient = .shared, portfolioStore: PortfolioStore? = nil)",
    )
    # Anti-vacuity: the same init still carries the follow subscription.
    assert "WhaleService.followsDidChangeNotification" in init, "scan drifted"
    assert "publisher(for: PortfolioStore.watchlistDidChangeNotification)" in init
    assert "handleWatchlistChange(" in init


def test_the_handler_patches_then_reconciles_behind_the_write():
    body = _decl_block(_read(_TRACKING_VM), "func handleWatchlistChange(_ change: WatchlistChange)")

    assert "guard hasLoadedOnce || loadTask != nil else { return }" in body, (
        "a never-loaded tab has nothing stale — unless its first load is in flight and may "
        "predate the write"
    )
    assert "trackedAssets.removeAll {" in body, "a removal must drop the row on this run-loop turn"

    insert_at = body.index("recentlyAddedTickers[activeId, default: []].insert(")
    running_at = body.index("self.loadTask")
    load_at = body.index("self.loadData()")
    clear_at = body.index("self.recentlyAddedTickers[portfolioId]?.remove(")
    assert insert_at < running_at < load_at < clear_at, (
        "order must be: marker inserted → running load awaited → fresh load → marker cleared"
    )
    assert "await running.value" in body, "a load already running may predate the write"
    assert "Task.sleep(" in body, "rapid star taps must coalesce"
    assert "refresh()" not in body, "no pull-to-refresh spinner for a change made elsewhere"
    assert "watchlistMarkerQueue.removeAll()" in body, "markers are snapshotted before the load"


def test_identity_change_and_deinit_cancel_the_pending_reconcile():
    src = _read(_TRACKING_VM)
    identity = _decl_block(src, "func handleIdentityChange(isActiveTab: Bool)")
    assert "watchlistReloadTask?.cancel()" in identity
    assert "watchlistMarkerQueue.removeAll()" in identity
    assert "recentlyAddedTickers.removeAll()" in identity, (
        "stale markers kept isOnWatchlist true for the previous identity's portfolio id"
    )
    # Cleared before the activation gate, like everything else identity-scoped there.
    assert identity.index("recentlyAddedTickers.removeAll()") < identity.index("guard isActiveTab")

    deinit = _decl_block(src, "deinit")
    assert "watchlistReloadTask?.cancel()" in deinit


# ── 4. Sibling surfaces ────────────────────────────────────────────────────────


def test_home_and_updates_react_to_a_star():
    home = _decl_block(_read(_HOME_VIEW), "struct HomeDashboardView: View")
    assert "PortfolioStore.activeGroupDidChangeNotification" in home, "scan drifted"
    assert "PortfolioStore.watchlistDidChangeNotification" in home, "Home stays stale for 60 s"
    # What the subscription DOES, bounded to ITS closure (the sibling group-change receiver
    # also calls into the view model, so a struct-wide substring would be vacuous).
    home_closure = _on_receive_closure(home, "PortfolioStore.watchlistDidChangeNotification")
    assert "viewModel.reloadForWatchlistChange(isActiveTab: isActiveTab)" in home_closure, (
        "Home must reload BEHIND an in-flight load when visible and mark itself stale when hidden"
    )
    home_vm = _decl_block(_read(_IOS / "ViewModels/HomeDashboardViewModel.swift"),
                          "func reloadForWatchlistChange(isActiveTab: Bool) async")
    assert "guard isActiveTab else" in home_vm and "lastLoadedAt = nil" in home_vm
    assert home_vm.index("await running.value") < home_vm.index("await load()"), (
        "a load already running may predate the write — await it, then load fresh"
    )

    updates = _decl_block(_read(_UPDATES_VIEW), "struct UpdatesView: View")
    assert "PortfolioStore.activeGroupDidChangeNotification" in updates, "scan drifted"
    assert "PortfolioStore.watchlistDidChangeNotification" in updates, "Updates stays stale all process"
    updates_closure = _on_receive_closure(updates, "PortfolioStore.watchlistDidChangeNotification")
    # Updates ignores its OWN source — it already reloads inline after Manage Assets.
    assert "change.source != .updates" in updates_closure, "Updates must skip its own posts (double fetch)"
    assert "viewModel.reloadForWatchlistChange()" in updates_closure
    assert updates_closure.index("change.source != .updates") < updates_closure.index("reloadForWatchlistChange")
    updates_vm = _decl_block(_read(_UPDATES_VM), "func reloadForWatchlistChange() async")
    assert "guard hasLoadedOnce else { return }" in updates_vm, "a never-loaded tab fetches fresh on activation"
    assert "reloadForActiveGroupChange()" in updates_vm


def test_trackings_own_writers_post_for_home_and_updates_and_tracking_ignores_them():
    """Tracking's search star / AddAssetSheet / swipe removal reload Tracking themselves,
    but Home's watchlist section and Updates' chips are server-built from the same group
    and observed nothing — so they post with `.tracking`, and Tracking skips that source."""
    vm = _read(_TRACKING_VM)
    add = _decl_block(vm, "func addTickerFromSearch(")
    assert _ANNOUNCE in add
    assert add.index(".addToWatchlist(") < add.index(_ANNOUNCE), "post after the POST"
    assert "added: true, source: .tracking" in add
    assert "ticker: symbol," in add[add.index(_ANNOUNCE):], "the stored spelling (storedSymbol), not result.ticker"

    remove = _decl_block(vm, "func removeAssetFromAll(_ asset: TrackedAsset)")
    assert _ANNOUNCE in remove
    assert remove.index(".removeFromWatchlist(") < remove.index(_ANNOUNCE)
    assert remove.index(_ANNOUNCE) < remove.index("} catch {")
    assert "added: false, source: .tracking" in remove

    sheet = _read(_IOS / "Views/Screens/TrackingView.swift")
    sheet_add = _decl_block(sheet, "struct AddAssetSheet")
    assert _ANNOUNCE in sheet_add
    assert sheet_add.index(".addToWatchlist(") < sheet_add.index(_ANNOUNCE)
    assert "added: true, source: .tracking" in sheet_add

    handler = _decl_block(vm, "func handleWatchlistChange(_ change: WatchlistChange)")
    assert "guard change.source != .tracking else { return }" in handler, (
        "Tracking must ignore its own posts — it already reloads itself"
    )
    payload = _decl_block(_strip_comments(_read(_PORTFOLIO_STORE)), "struct WatchlistChange: Sendable")
    assert "case tracking" in payload


def test_the_remove_branch_drops_a_pending_add_marker():
    """Add-then-remove inside one debounce window: the pending add marker would otherwise
    keep `isOnWatchlist` true for a ticker that is gone."""
    body = _decl_block(_read(_TRACKING_VM), "func handleWatchlistChange(_ change: WatchlistChange)")
    else_at = body.index("} else {")
    debounce_at = body.index("watchlistReloadTask?.cancel()")
    remove_branch = body[else_at:debounce_at]
    assert "trackedAssets.removeAll {" in remove_branch
    assert "recentlyAddedTickers[portfolioId]?.remove(ticker)" in remove_branch
    assert "watchlistMarkerQueue.removeAll { $0.ticker == ticker }" in remove_branch


def test_updates_announces_the_pair_only_for_a_declared_coin():
    add = _decl_block(_read(_UPDATES_VM), "func addTicker(_ symbol: String, assetType: String?) async")
    call = add[add.index(_ANNOUNCE):]
    assert 'ticker: wireClass == "crypto" ? CryptoSymbol.pair(ticker) : ticker' in call, (
        "the pair spelling is right ONLY for a declared coin — a bare BTC declared stock is the ETF"
    )
    assert "MarketTickerType.resolve(nil, symbol: ticker).rawValue" in call, "an undeclared class is derived, never empty"


def test_updates_manage_assets_posts_after_its_own_request():
    src = _read(_UPDATES_VM)
    for header, added in (("func addTicker(_ symbol: String, assetType: String?) async", "true"),
                          ("func removeTicker(_ symbol: String) async", "false")):
        body = _decl_block(src, header)
        assert _ANNOUNCE in body, f"{header}: does not announce"
        assert body.index("request(") < body.index(_ANNOUNCE), f"{header}: announce before request"
        assert body.index(_ANNOUNCE) < body.index("} catch {"), f"{header}: announce after the catch"
        assert f"added: {added}, source: .updates" in body
    add = _decl_block(src, "func addTicker(_ symbol: String, assetType: String?) async")
    assert "CryptoSymbol.pair(ticker)" in add, "a coin added from Updates must announce the pair"
