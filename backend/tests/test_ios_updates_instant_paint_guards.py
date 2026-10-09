"""Updates (Live News) — near-instant first paint: the ViewModel, the view and the header.

Owner ask (2026-10-08): the Updates tab should appear almost instantly. Three changes do it:
  * the first open fetches the Market feed BESIDE /updates/tabs, in one main-actor turn (the
    Market scope needs nothing from /tabs — p50 ~1.08 s → ~0.34 s of server time to the first
    row), and AI enrichment runs detached, so a load ends when the rows paint;
  * the first load belongs to the ViewModel (`initialLoadTask`), so a tab-away mid-load no
    longer cancels it, and it latches only on a live, non-error answer;
  * a returning account paints its on-device snapshot (`UpdatesFeedSnapshot` on the generic
    `AccountSnapshotStore`) labelled "News · Updated <time>", and the live answer replaces it.

The store and its AppState wiring are pinned by `test_ios_account_snapshot_guards.py`. THIS file
pins the Updates half: the parallel start and its fences, the loadTabs fences and the selection
kept across a same-account heal, the snapshot kept on screen during the live load (and dropped on
a refusal, kept on a failure), the detached enrichment, the owned first load, the render and
shimmer gates for the hidden tab, "Ask Cay AI" withheld from a snapshot card, the honest header,
the identity clear above the active-tab gate — and the account-snapshot contract's ViewModel
rows for Updates: the seed precondition as the first statement (assignment-only bans), exactly
one `snapshotStore.save(` that is never in a catch with the epoch captured before the request,
exactly one `UpdatesFeedSnapshotStore.shared` and it inside `init`, the `.task` prepare order and
the identity reseed above the gate.

Fix pass (adversarial review, same day) — each with its own rows below:
  * the snapshot → live swap is height-stable: a snapshot Insights card keeps the Ask Cay pill's
    slot (a hidden, inert copy of the same pill, spaced like the card's own row), the card is
    keyed `.id(summary.id)`, and the news-tone chart may draw over a snapshot that has a card;
  * the selected chip is stashed across an identity change only for the SAME account (the store
    epoch at the selection's feed load), and the stash wins over a Market fallback;
  * a load that finishes behind another tab starts no paid enrichment and no Insights re-poll
    until the tab is shown, and rows built while hidden start no enrichment either;
  * the snapshot-failure notice says less at accessibility text sizes (it never scrolls away);
  * pins for the seed's Market-scope term, the forced-stale snapshot card, and loadTabs'
    cancellation return.

Source scans (there is no XCTest target): comments stripped and every check scoped to its
brace-bounded declaration (.claude/rules/testing.md §3). `MUTATIONS` breaks each property once
and asserts its guard fails, several naming the assertion they must fail WITH.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"

_FILES = {
    "vm": _IOS / "ViewModels/UpdatesViewModel.swift",
    "view": _IOS / "Views/Screens/UpdatesView.swift",
    "header": _IOS / "Views/Molecules/LiveNewsHeader.swift",
    # Read-only here: the reserved pill slot must match the card's own pill row.
    "card": _IOS / "Views/Organisms/InsightsSummaryCard.swift",
}

_CLASS = "final class UpdatesViewModel: ObservableObject"
_INIT = "init(apiClient: APIClient = .shared)"
_LOAD_INITIAL = "private func loadInitialData() async"
_LOAD_IF_NEEDED = "func loadIfNeeded() async"
_LOAD_TABS = "private func loadTabs() async"
_LOAD_FEED = "private func loadFeed(for tab: NewsFilterTab, force: Bool) async"
_IDENTITY = "func handleIdentityChange(isActiveTab: Bool) async"
_SEED = "private func seedFromSnapshot()"
_PREPARE = "func prepareSnapshot() async"
_DROP = "private func dropSnapshot()"
_DROP_EPOCH = "private func dropSeedIfEpochMoved()"
_EXPIRE = "func expireSnapshotIfStale(now: Date = Date())"
_START_ENRICH = "private func startEnrichment(scope: String, token: UUID)"
_START_POST = "private func startPostPaintWork(scope: String, token: UUID, pollInsight: Bool)"
_SET_ACTIVE = "func setTabActive(_ active: Bool)"
_APPEAR = "func articleDidAppear(_ article: NewsArticle)"
_SNAPSHOT_CARD = "private static func snapshotInsight(_ dto: AIInsightCardDTO?) -> NewsInsightSummary?"
_RESERVE = "private var askCayPillReserve: some View"
_NOTICE_TEXT = "private var snapshotRefreshFailureMessage: String"
_TREND = "private var visibleTrend: SentimentTrend?"
_ENRICH_WINDOW = "private func enrichVisibleWindow(around index: Int, scope: String, token: UUID) async"
_MERGE = "private func mergeEnrichment("
_REFRESH = "func refresh() async"
_GROUP_RELOAD = "func reloadForActiveGroupChange() async"
_VIEW_STRUCT = "struct UpdatesView: View"
_HEADER_STRUCT = "struct LiveNewsHeader: View"
_GATE = "guard isActiveTab else { return }"
_REQUEST = "try await apiClient.requestReturningBody("


# ── Scanning helpers (copied from the account-snapshot guard file; no shared conftest) ──

def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails, keeping line structure.

    `(?<![:/])` keeps the `//` of a `https://` literal. The fix's own comments name
    `seedFromSnapshot`, `snapshotStore.save(`, `initialLoadTask` and every fence, so an
    un-stripped scan would pass on prose.
    """
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return "\n".join(re.sub(r"(?<![:/])//.*$", "", line) for line in src.splitlines())


def _sources() -> dict[str, str]:
    out = {}
    for key, path in _FILES.items():
        if not path.exists():
            pytest.fail(f"expected file is missing: {path}")
        out[key] = _strip(path.read_text(encoding="utf-8"))
    return out


def _block_at(src: str, idx: int) -> str:
    """The brace-balanced block opened by the first `{` at or after `idx`."""
    open_brace = src.find("{", idx)
    assert open_brace != -1, f"no block opens after offset {idx}"
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace : i + 1]
    raise AssertionError(f"unbalanced braces after offset {idx}")


def _block(src: str, header: str) -> str:
    assert src.count(header) == 1, f"expected exactly one {header!r}, found {src.count(header)}"
    start = src.find(header)
    return _block_at(src, start + len(header))


def _paren_at(src: str, idx: int) -> str:
    """The parenthesised argument list opened by the first `(` at or after `idx`."""
    open_paren = src.find("(", idx)
    assert open_paren != -1, f"no argument list opens after offset {idx}"
    depth = 0
    for i in range(open_paren, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[open_paren : i + 1]
    raise AssertionError(f"unbalanced parentheses after offset {idx}")


def _idx(src: str, token: str) -> int:
    at = src.find(token)
    assert at != -1, f"{token!r} not found"
    return at


def _norm(src: str) -> str:
    """Whitespace-collapsed, so an exact-expression check survives re-indentation only."""
    return " ".join(src.split())


def _in_order(src: str, *tokens: str) -> None:
    positions = [_idx(src, t) for t in tokens]
    assert positions == sorted(positions), f"out of order: {list(zip(tokens, positions))}"


def _vm(s: dict[str, str]) -> str:
    return _block(s["vm"], _CLASS)


def _fn(s: dict[str, str], header: str) -> str:
    return _block(_vm(s), header)


def _view(s: dict[str, str]) -> str:
    return _block(s["view"], _VIEW_STRUCT)


def _body(s: dict[str, str]) -> str:
    return _block(_view(s), "var body: some View")


def _assigns(block: str, name: str) -> bool:
    """`name = …` (not `==`, not `foo.name =`, not `_name =`)."""
    return re.search(rf"(?<![\w.]){re.escape(name)}\s*=(?!=)", block) is not None


def _catch_blocks(src: str) -> list[str]:
    return [_block_at(src, m.start()) for m in re.finditer(r"\bcatch\b[^{]*\{", src)]


def _success_region(feed: str) -> str:
    """loadFeed from its request to its first `catch` arm."""
    start = _idx(feed, _REQUEST)
    return feed[start : _idx(feed, "} catch is CancellationError {")]


def _generic_catch(feed: str) -> str:
    """The generic `} catch {` arm that follows the CancellationError arm."""
    after = feed[_idx(feed, "} catch is CancellationError {"):]
    m = re.search(r"\}\s*catch\s*\{", after[1:])
    assert m, "loadFeed's generic catch moved — this scan has drifted"
    return _block_at(after, m.start() + 1)


# ── 1. The parallel first open ───────────────────────────────────────────────────────

_PARALLEL = (
    "let market = selectedTab ?? filterTabs.first(where: { $0.isMarketTab }) ?? Self.marketTabFallback",
    "selectedTab = market",
    "async let tabs: Void = loadTabs()",
    "await loadFeed(for: market, force: false)",
    "await tabs",
)


def _check_first_open_parallel(s):
    fn = _fn(s, _LOAD_INITIAL)
    assert "let effectiveScope = selectedTab?.scope ?? pendingScope ?? UpdatesScope.market" in fn, (
        "the first open no longer decides its scope from the selection AND the chip stashed by "
        "an identity change — a same-account heal on a ticker chip would fetch Market"
    )
    branch_at = _idx(fn, "if effectiveScope == UpdatesScope.market {")
    parallel = _block_at(fn, branch_at)
    _in_order(parallel, *_PARALLEL)
    claim = parallel[_idx(parallel, "selectedTab = market"): _idx(parallel, "await loadFeed(for: market")]
    assert "await" not in claim, (
        "an await sits between selecting Market and the feed load — `.onChange(of: selectedTab)` "
        "can then start a second, duplicate Market request before loadFeed claims inFlightScope"
    )
    rest = fn[branch_at + len(parallel):]
    serial = _block_at(rest, _idx(rest, "else"))
    assert "async let" not in serial, "a ticker scope is fetched beside /tabs — before /tabs confirms the plan opens it"
    _in_order(serial, "await loadTabs()", "await loadFeed(for: tab, force: false)")


def _check_serial_paths_stay_serial(s):
    for header in (_REFRESH, _GROUP_RELOAD):
        fn = _fn(s, header)
        assert "async let" not in fn, f"`{header}` started its feed beside /tabs — it must stay serial"
        _in_order(fn, "await loadTabs()", "await loadFeed(")


def _check_trailing_isloading_fenced(s):
    fn = _fn(s, _LOAD_INITIAL)
    _in_order(fn, "let generation = identityGeneration", "isLoading = true")
    trailing = "if generation == identityGeneration, inFlightScope == nil {"
    assert trailing in fn, (
        "loadInitialData's trailing `isLoading = false` is no longer fenced by the identity "
        "generation and the in-flight load — a cancelled first load flashes 'No recent stories'"
    )
    assert "isLoading = false" in _block_at(fn, _idx(fn, trailing))
    assert fn.count("isLoading = false") == 1, (
        "loadInitialData clears isLoading outside its fenced trailing line"
    )


# ── 2. loadTabs: identity fence, selection read at apply, Market fallback ─────────────

def _check_loadtabs_fences(s):
    tabs = _fn(s, _LOAD_TABS)
    request = _idx(tabs, "try await apiClient.request(")
    assert _idx(tabs, "let generation = identityGeneration") < request, (
        "loadTabs captures the identity generation after its request"
    )
    success = tabs[request: _idx(tabs, "} catch {")]
    _in_order(success, "guard generation == identityGeneration else", "let previousScope = ")
    assert "let previousScope = selectedTab?.scope ?? pendingScope" in success, (
        "loadTabs reads the selection when the request LEFT, or ignores the stashed chip — a "
        "chip tapped while /tabs was in flight is yanked back, a healed session loses its chip"
    )
    _in_order(success, "let previousScope = selectedTab?.scope ?? pendingScope", "pendingScope = nil")
    _in_order(success, "filterTabs = tabs", "tabsAreLive = true")
    catch = _block_at(tabs, _idx(tabs, "} catch {") + 1)
    _in_order(catch, "guard generation == identityGeneration else { return }",
              "if selectedTab == nil || !tabsAreLive {")
    _in_order(catch, "let appError = AppError.from(error)", "guard !appError.isCancellation else { return }",
              "if selectedTab == nil || !tabsAreLive {")
    fallback = _block_at(catch, _idx(catch, "if selectedTab == nil || !tabsAreLive {"))
    assert "if !tabsAreLive { filterTabs = [Self.marketTabFallback] }" in fallback
    assert "selectedTab = filterTabs.first(where: { $0.isMarketTab }) ?? Self.marketTabFallback" in fallback, (
        "a failed /tabs no longer selects Market — with only the seeded Market chip on screen "
        "no feed load would ever start"
    )
    handler = _fn(s, _IDENTITY)
    assert "identityGeneration &+= 1" in handler[: _idx(handler, _GATE)], (
        "the identity handler no longer bumps the generation above the gate — a late /tabs of "
        "the previous account lands"
    )


_STASH = "pendingScope = selectionEpoch == snapshotStore.epoch ? (pendingScope ?? selectedTab?.scope) : nil"


def _check_pending_scope_survives_heal(s):
    handler = _fn(s, _IDENTITY)
    stash = re.search(r"(?<![\w.])pendingScope\s*=(?!=)[^\n]*", handler)
    assert stash, "the identity handler no longer stashes the selected chip"
    assert "selectionEpoch == snapshotStore.epoch ?" in stash.group(0) and stash.group(0).rstrip().endswith(": nil"), (
        "the chip is stashed across ANY identity change — the ended session's chip picks the next "
        "account's first feed (auth.md §7)"
    )
    assert "(pendingScope ?? selectedTab?.scope)" in stash.group(0), (
        "the stash prefers the selection — the Market fallback a refused /tabs picked during the "
        "restoring window overwrites the user's chip"
    )
    assert stash.group(0).strip() == _STASH
    _in_order(handler, _STASH, "selectedTab = nil", _GATE)
    assert "private var selectionEpoch: Int?" in _vm(s)
    feed = _fn(s, _LOAD_FEED)
    assert "selectionEpoch = snapshotStore.epoch" in feed, (
        "loadFeed no longer records which account made the selection — the stash cannot tell a "
        "same-account heal from a new account"
    )
    assert len(re.findall(r"(?<![\w.])selectionEpoch\s*=(?!=)", _vm(s))) == 1, (
        "selectionEpoch is written outside loadFeed — only a feed load says which account selected"
    )
    _in_order(feed, "inFlightScope = scope", "selectionEpoch = snapshotStore.epoch",
              "if !force, let cached = feedCache[scope] {")


# ── 3. loadFeed with a Market snapshot on screen ─────────────────────────────────────

_CONDITIONAL_CLEAR = "if snapshotSavedAt == nil || scope != UpdatesScope.market {"


def _check_market_seed_kept_while_live_loads(s):
    feed = _fn(s, _LOAD_FEED)
    clear = _block_at(feed, _idx(feed, _CONDITIONAL_CLEAR))
    for token in ("snapshotSavedAt = nil", "allNewsArticles = []", "groupedNews = []", "insightSummary = nil"):
        assert token in clear, f"the pre-request clear lost `{token}`"
    for token in ("isLoading = true", "error = nil"):
        assert token not in clear, f"`{token}` is now conditional — it must run on every load"
    after = feed[_idx(feed, _CONDITIONAL_CLEAR) + len(clear):]
    _in_order(after, "isLoading = true", "error = nil", _REQUEST)
    assert feed.count("allNewsArticles = []") == 1, (
        "loadFeed clears the rows outside the conditional clear — the Market snapshot flashes "
        "to a skeleton (and offline to an error screen) before the live answer"
    )


def _check_live_answer_replaces_snapshot(s):
    feed = _fn(s, _LOAD_FEED)
    success = _success_region(feed)
    _in_order(success, "guard loadToken == token else {", "allNewsArticles = articles",
              "snapshotSavedAt = nil", "hasShownFeed = true", "applyFiltersAndGroup()")
    cached = _block_at(feed, _idx(feed, "if !force, let cached = feedCache[scope] {"))
    for token in ("snapshotSavedAt = nil", "hasShownFeed = true"):
        assert token in cached, f"the cached path lost `{token}` — a live feed would sit under 'Updated <time>'"


def _check_refusal_drops_failure_keeps(s):
    feed = _fn(s, _LOAD_FEED)
    catch = _generic_catch(feed)
    refusal = _block_at(catch, _idx(catch, "if case .signInRequired = appError"))
    assert "dropSnapshot()" in refusal, (
        "a refusal keeps the snapshot — stored stories stay on screen under the account gate"
    )
    rest = catch[_idx(catch, "if case .signInRequired = appError") + len(refusal):]
    failure = _block_at(rest, _idx(rest, "else"))
    assert "self.error = appError.message" in failure
    assert "expireSnapshotIfStale()" in failure, "a failure no longer bounds the kept snapshot by its window"
    for token in ("dropSnapshot()", "allNewsArticles = []"):
        assert token not in failure, f"a failed refresh runs `{token}` — the snapshot vanishes offline"
    drop = _fn(s, _DROP)
    _in_order(drop, "guard snapshotSavedAt != nil else { return }", "snapshotSavedAt = nil",
              "allNewsArticles = []", "insightSummary = nil", "applyFiltersAndGroup()")
    notice = _fn(s, "var showsSnapshotRefreshFailure: Bool")
    for token in ("snapshotSavedAt != nil", "error != nil", "!requiresSignIn", "!isReconnecting",
                  "!groupedNews.isEmpty"):
        assert token in notice, f"showsSnapshotRefreshFailure lost `{token}`"


def _check_generic_catch_fenced(s):
    feed = _fn(s, _LOAD_FEED)
    cancel = _block_at(feed, _idx(feed, "} catch is CancellationError {") + 1)
    assert "guard loadToken == token else { return }" not in cancel, (
        "the CancellationError arm spells its fence as the generic catch does — "
        "test_ios_account_gate_state's refusal-order guard then anchors on IT and goes vacuous"
    )
    _in_order(cancel, "if loadToken != token { return }", "isLoading = false")
    catch = _generic_catch(feed)
    _in_order(catch, "guard loadToken == token else { return }", "let appError = AppError.from(error)",
              "if appError.isCancellation {", "if case .signInRequired = appError")
    cancelled = _block_at(catch, _idx(catch, "if appError.isCancellation {"))
    assert re.search(r"\breturn\b", cancelled) and "error =" not in cancelled, (
        "a cancellation is reported as an error ('cancelled')"
    )
    assert "(error as? URLError)?.code == .cancelled" not in feed, (
        "loadFeed classifies cancellation by hand again — a nested URLError.cancelled reads as "
        "'Couldn't load the news / cancelled'"
    )


# ── 4. Detached enrichment ───────────────────────────────────────────────────────────

def _check_enrichment_detached(s):
    feed = _fn(s, _LOAD_FEED)
    assert "await enrichVisibleWindow(" not in feed, (
        "loadFeed awaits the AI enrichment again — the load and pull-to-refresh wait on the model"
    )
    assert feed.count("startPostPaintWork(scope: scope, token: token, pollInsight: ") == 2, (
        "the cached and the live path must each start the detached enrichment"
    )
    cached = _block_at(feed, _idx(feed, "if !force, let cached = feedCache[scope] {"))
    assert "startPostPaintWork(scope: scope, token: token, pollInsight: false)" in cached
    assert "startPostPaintWork(scope: scope, token: token, pollInsight: true)" in _success_region(feed)
    assert _idx(feed, "enrichTask?.cancel()") < _idx(feed, "if !force, let cached = feedCache[scope] {"), (
        "a scope change no longer cancels the outgoing feed's enrichment"
    )
    start = _fn(s, _START_ENRICH)
    _in_order(start, "enrichTask?.cancel()", "enrichTask = Task")
    handler = _fn(s, _IDENTITY)
    assert _idx(handler, "enrichTask?.cancel()") < _idx(handler, _GATE), (
        "the previous identity's enrichment survives an identity change"
    )
    assert "enrichTask?.cancel()" in _fn(s, "deinit")
    assert "enrichVisibleWindow" not in _fn(s, _REFRESH)


def _check_post_paint_waits_for_the_tab(s):
    feed = _fn(s, _LOAD_FEED)
    for token in ("startEnrichment(", "scheduleInsightPollIfNeeded("):
        assert token not in feed, (
            f"loadFeed calls `{token}` directly — a load that finished behind another tab spends a "
            "paid enrichment (and two /feed re-polls) on rows nobody sees"
        )
    post = _fn(s, _START_POST)
    hidden = _block_at(post, _idx(post, "guard isTabActive else {"))
    assert "deferredPostPaint = (scope: scope, token: token, pollInsight: pollInsight)" in hidden
    assert re.search(r"\breturn\b", hidden)
    _in_order(post, "guard isTabActive else {", "startEnrichment(scope: scope, token: token)", "if pollInsight {")
    assert "scheduleInsightPollIfNeeded(scope: scope, token: token)" in _block_at(post, _idx(post, "if pollInsight {"))
    active = _fn(s, _SET_ACTIVE)
    resume = _block_at(active, _idx(active, "if active, let pending = deferredPostPaint {"))
    assert "deferredPostPaint = nil" in resume, "a deferred post-paint is started twice"
    current = _block_at(resume, _idx(resume, "if pending.token == loadToken, pending.scope == selectedTab?.scope {"))
    assert "startPostPaintWork(scope: pending.scope, token: pending.token, pollInsight: pending.pollInsight)" in current, (
        "a load that finished while hidden is never enriched — or a stale one is, on another feed"
    )
    appear = _fn(s, _APPEAR)
    _in_order(appear, "lastAppearedIndex = index", "guard isTabActive else { return }", "appearWorkTask = Task")
    handler = _fn(s, _IDENTITY)
    assert _idx(handler, "deferredPostPaint = nil") < _idx(handler, _GATE), (
        "the previous identity's deferred post-paint work survives an identity change"
    )


def _check_no_enrichment_of_a_snapshot(s):
    window = _fn(s, _ENRICH_WINDOW)
    _in_order(window, "guard snapshotSavedAt == nil else { return }", "isEnriching = true")
    merge = _fn(s, _MERGE)
    guarded = _block_at(merge, _idx(merge, "if snapshotSavedAt == nil {"))
    assert "feedCache[scope] =" in guarded and merge.count("feedCache[scope] =") == 1, (
        "a merged enrichment writes snapshot rows into feedCache — its cached branch would "
        "serve them later as this session's live feed"
    )


# ── 5. The owned first load ──────────────────────────────────────────────────────────

_OWNED = (
    "expireSnapshotIfStale()",
    "guard !hasLoadedOnce else { return }",
    "if let running = initialLoadTask {",
    "await running.value",
    "let task = Task {",
    "await self.loadInitialData()",
    "if !Task.isCancelled, self.error == nil, self.snapshotSavedAt == nil {",
    "self.hasLoadedOnce = true",
    "if self.initialLoadID == id {",
    "initialLoadTask = task",
    "await task.value",
)


def _check_first_load_owned(s):
    fn = _fn(s, _LOAD_IF_NEEDED)
    _in_order(fn, *_OWNED)
    outside = fn[: _idx(fn, "let task = Task {")] + fn[_idx(fn, "initialLoadTask = task"):]
    assert "loadInitialData()" not in outside, (
        "the first load runs in the view's structured task again — a tab-away cancels it"
    )
    assert "isLoadingInitial" not in _vm(s), "the old re-entrancy flag is back beside the owned task"


def _check_first_frame_not_empty(s):
    assert "@Published var isLoading: Bool = true" in _vm(s), (
        "isLoading starts false — the first frame after a tap is 'No recent stories'"
    )


_INIT_BANNED = ("loadIfNeeded(", "loadInitialData(", "loadFeed(", "loadTabs(", "Task {", "Task.detached",
                "apiClient.", "seedFromSnapshot(", "prepareSnapshot(", ".prepare(")


def _check_no_network_in_init(s):
    init = _fn(s, _INIT)
    for token in _INIT_BANNED:
        assert token not in init, (
            f"init runs `{token}` — this tab is built at launch for a screen the user may never "
            "open, and the store has not read its file yet"
        )


# ── 6. The view: render gate, shimmer gate, Ask Cay, notice, .task, expiry, trend ────

def _check_render_gate(s):
    sections = _block(_view(s), "private func newsSections()")
    gate = _block_at(sections, _idx(sections, "if isActiveTab || viewModel.snapshotSavedAt == nil {"))
    assert "ForEach(viewModel.groupedNews)" in gate, (
        "snapshot rows render in the hidden tab — their AsyncImage thumbnails load at launch for "
        "a tab nobody opened"
    )
    assert sections.count("ForEach(viewModel.groupedNews)") == 1
    body = _norm(_body(s))
    assert "if let summary = viewModel.insightSummary, isActiveTab || viewModel.snapshotSavedAt == nil {" in body, (
        "the snapshot Insights card renders in the hidden tab"
    )
    # The tab bar switches tabs inside `withAnimation`; without this fence the gate's swap is
    # cross-faded, so the first frame after a tap is half-blank instead of the snapshot.
    assert "} .transaction(value: isActiveTab) { $0.animation = nil }" in body, (
        "the render gate's activation swap is animated — the first frame after a tap is a cross-fade"
    )


def _check_shimmer_gate(s):
    view = _view(s)
    assert "@ViewBuilder private var loadingSkeleton: some View" in _norm(view)
    skeleton = _block(view, "private var loadingSkeleton: some View")
    active = _block_at(skeleton, _idx(skeleton, "if isActiveTab {"))
    assert "TickerNewsShimmerCard()" in active and skeleton.count("TickerNewsShimmerCard()") == 1, (
        "the shimmer runs in the hidden tab — five repeatForever animations behind Home"
    )
    rest = skeleton[_idx(skeleton, "if isActiveTab {") + len(active):]
    assert "else" in rest and "Color.clear.frame(height: 1)" in _block_at(rest, _idx(rest, "else"))
    body = _body(s)
    assert "} else if viewModel.isLoading && viewModel.groupedNews.isEmpty {" in body, (
        "the pinned loading branch of the account-gate chain moved"
    )


def _check_ask_cay_nil_on_snapshot(s):
    body = _body(s)
    assert "onAskCay: viewModel.snapshotSavedAt == nil ? { openUpdatesChat(focus: .card) } : nil" in body, (
        "a snapshot Insights card offers 'Ask Cay AI' — the chat grounds on another card and a "
        "send costs a credit"
    )
    sheet = _block_at(body, _idx(body, ".sheet(item: $insightSources, onDismiss: presentPendingChat)"))
    snap = _block_at(sheet, _idx(sheet, "if viewModel.isSnapshotInsight(summary) {"))
    assert "InsightsDetailView(summary: summary)" in snap and "onAskCay" not in snap, (
        "the snapshot card's detail sheet offers 'Ask Cay AI'"
    )
    probe = _fn(s, "func isSnapshotInsight(")
    _in_order(probe, "guard let seeded = snapshotInsightID else { return false }", "summary.id == seeded")
    assert "snapshotInsightID = card?.id" in _fn(s, _SEED), "the seed no longer records which card is the snapshot's"


def _check_retry_notice_placement(s):
    body = _body(s)
    _in_order(body, "LiveNewsHeader(", "if viewModel.showsSnapshotRefreshFailure {", "InlineRetryNotice(",
              "ScrollView(showsIndicators: false)")
    notice = _block_at(body, _idx(body, "if viewModel.showsSnapshotRefreshFailure {"))
    assert "viewModel.refresh()" in notice, "the snapshot's failure notice offers no retry"
    assert "message: snapshotRefreshFailureMessage" in notice
    stack = _block(body, "LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders])")
    assert "InlineRetryNotice(" not in stack, "the notice moved into the LazyVStack (in-place resize)"
    # It never scrolls away, so at accessibility sizes it must not become a band of the screen.
    # `.dynamicTypeSize(...)` would be inert (AppTypography scales via UIFontMetrics), so the
    # wording itself changes.
    view = _view(s)
    assert r"@Environment(\.dynamicTypeSize) private var dynamicTypeSize" in view
    text = _norm(_block(view, _NOTICE_TEXT))
    assert ('dynamicTypeSize.isAccessibilitySize ? "Couldn\'t refresh the news." '
            ': "Couldn\'t refresh the news. These stories are from your last visit."') in text, (
        "the failure notice says the full sentence at accessibility text sizes — a fixed band of "
        "300+ pt above the stories on a small phone"
    )


def _check_task_prepare_order(s):
    task = _block(_body(s), ".task(id: isActiveTab)")
    _in_order(task, "viewModel.setTabActive(isActiveTab)", "await viewModel.prepareSnapshot()", _GATE,
              "guard !Task.isCancelled else { return }", "await viewModel.loadIfNeeded()")


def _check_expiry(s):
    expire = _fn(s, _EXPIRE)
    _in_order(expire, "guard let savedAt = snapshotSavedAt,",
              "!AccountSnapshotPolicy.isDisplayable(savedAt: savedAt, now: now) else { return }", "dropSnapshot()")
    body = _body(s)
    at = _idx(body, "UIApplication.didBecomeActiveNotification")
    assert "viewModel.expireSnapshotIfStale()" in _block_at(body, at), (
        "a snapshot that aged past 96 h in the background is not dropped on foreground"
    )


_TREND_GATE = "viewModel.hasShownFeed || (viewModel.snapshotSavedAt != nil && viewModel.insightSummary != nil),"


def _check_trend_waits_for_first_feed(s):
    trend = _block(_view(s), _TREND)
    assert "viewModel.hasShownFeed" in trend, "the news-tone chart paints over the skeleton or the snapshot"
    assert _TREND_GATE in _norm(trend), (
        "the chart waits for the row swap over a snapshot that has a card — it is inserted in the "
        "same transaction that replaces every row, or (without the card term) it is pushed down "
        "when the live card lands above it"
    )
    assert "@Published private(set) var hasShownFeed" in _vm(s)


def _check_insights_slot_height_stable(s):
    body = _body(s)
    slot = _block_at(body, _idx(body, "if let summary = viewModel.insightSummary,"))
    norm = _norm(slot)
    assert norm.startswith("{ VStack(alignment: .leading, spacing: AppSpacing.md) {"), (
        "the Insights slot is no longer the card plus its reserved pill row"
    )
    assert "onAskCay: viewModel.snapshotSavedAt == nil ? { openUpdatesChat(focus: .card) } : nil" in norm
    reserve_at = _idx(slot, "if viewModel.snapshotSavedAt != nil {")
    assert _idx(slot, "InsightsSummaryCard(") < reserve_at
    assert "askCayPillReserve" in _block_at(slot, reserve_at), (
        "a snapshot Insights card is shorter than the live one by the Ask Cay pill row — every "
        "story drops ~50 pt the moment the live answer replaces the snapshot"
    )
    assert slot.count("askCayPillReserve") == 1
    assert ".id(summary.id)" in norm, (
        "a new Insights card resizes IN PLACE as a LazyVStack child (the 100%-CPU layout hang) "
        "instead of being removed and inserted"
    )
    reserve = _norm(_block(_view(s), _RESERVE))
    assert 'AskCayAIPill(title: "Ask Cay AI about this", action: {})' in reserve
    for token in (".hidden()", ".disabled(true)", ".allowsHitTesting(false)", ".accessibilityHidden(true)"):
        assert token in reserve, f"the reserved pill slot lost `{token}` — it must be an invisible, inert spacer"
    # The reserve copies the card's own pill row: same pill, same title, same row spacing.
    card = _block(_block(s["card"], "struct InsightsSummaryCard: View"), "var body: some View")
    assert _norm(card).startswith("{ VStack(alignment: .leading, spacing: AppSpacing.md) {"), (
        "InsightsSummaryCard's row spacing changed — the reserved slot no longer matches it"
    )
    assert 'AskCayAIPill(title: "Ask Cay AI about this", action: onAskCay)' in card, (
        "InsightsSummaryCard's pill changed — the reserved slot no longer matches it"
    )


# ── 7. The header ────────────────────────────────────────────────────────────────────

def _check_header_is_honest(s):
    label = _fn(s, "var snapshotUpdatedLabel: String?")
    assert "snapshotSavedAt.map" in label and "AccountSnapshotPolicy.updatedLabel(savedAt:" in label, (
        "the 'Updated <time>' label has a second wording source"
    )
    assert "HomeDashboardViewModel" not in _vm(s), "the ViewModel names Home's formatter directly"
    body = _body(s)
    call = _paren_at(body, _idx(body, "LiveNewsHeader("))
    assert "snapshotStatusText: viewModel.snapshotUpdatedLabel" in call, (
        "the header is not told the rows are a snapshot — it says 'Live News' over stored stories"
    )
    header = _block(s["header"], _HEADER_STRUCT)
    assert "var snapshotStatusText: String? = nil" in header
    arm = _block_at(header, _idx(header, "if let snapshotStatusText {"))
    for token in ('Text("News")', "Text(snapshotStatusText)", ".lineLimit(1)", ".minimumScaleFactor(0.75)",
                  ".accessibilityElement(children: .combine)"):
        assert token in arm, f"the snapshot header lost `{token}`"
    for token in ("LiveIndicator(", '"Live News"'):
        assert token not in arm, f"the snapshot header still shows `{token}` — it claims to be live"
    assert ".frame(height: 44)" in header, "the header lost its fixed height — the swap would move the list"
    assert '#Preview("Snapshot")' in s["header"]


# ── 8. The account-snapshot contract's ViewModel rows for Updates ────────────────────

_SEED_FIRST = re.compile(
    r"^\{\s*guard\s+!hasShownFeed,\s*allNewsArticles\.isEmpty,\s*snapshotSavedAt == nil,\s*"
    r"!requiresSignIn,\s*!isReconnecting,[^{}]*?let snapshot = snapshotStore\.snapshotForDisplay\(\)\s*"
    r"else \{ return \}",
)
_SEED_BANNED_ASSIGNS = ("hasLoadedOnce", "selectedTab", "filterOptions", "loadToken", "inFlightScope",
                        "loadedOffset", "hasMorePages", "tabsAreLive", "groupName", "lockedTickerCount",
                        "tierRequiredForMoreTickers", "isLoading")
_SEED_BANNED_TOKENS = ("feedCache", "Task {", "Task.detached", "apiClient.", "loadFeed(", "loadTabs(",
                       "loadIfNeeded(", "loadInitialData(", "portfolioStore.")


def _check_seed_precondition_first(s):
    seed = _fn(s, _SEED)
    assert _SEED_FIRST.search(seed), (
        "seedFromSnapshot no longer OPENS with its precondition (no live feed yet for this "
        "identity, not gated) — a re-run `.task` would overwrite live rows, or repaint stored "
        "stories under the account gate"
    )
    precondition = seed[: _idx(seed, "let snapshot = snapshotStore.snapshotForDisplay()")]
    assert "(selectedTab?.scope ?? pendingScope ?? UpdatesScope.market) == UpdatesScope.market," in precondition, (
        "the seed no longer requires the Market scope — a same-account heal on a ticker chip "
        "paints Market stories and a Market card under that chip"
    )
    for name in _SEED_BANNED_ASSIGNS:
        assert not _assigns(seed, name), f"the seed assigns `{name}` — it must set display state only"
    for token in _SEED_BANNED_TOKENS:
        assert token not in seed, f"the seed touches `{token}` — it must set display state only"
    for token in ("snapshotSavedAt = snapshot.savedAt", "seededEpoch = snapshotStore.epoch",
                  "applyFiltersAndGroup()", "Self.snapshotInsight(", "if filterTabs.isEmpty && !tabsAreLive {"):
        assert token in seed, f"the seed lost `{token}`"
    assert len(re.findall(r"(?<!func )seedFromSnapshot\(\)", _vm(s))) == 1, (
        "seedFromSnapshot is called outside prepareSnapshot — one seeding path only (a refusal "
        "branch or init seed would repaint stored stories)"
    )
    assert "seedFromSnapshot()" in _fn(s, _PREPARE)


def _check_snapshot_card_is_stale(s):
    card = _fn(s, _SNAPSHOT_CARD)
    guard = card[: _idx(card, "else { return nil }")]
    assert "UpdatesDateParser.parse(dto.generatedAt) != nil" in guard, (
        "a snapshot card with an unparseable generated_at is shown — it reads 'Updated just now' "
        "on a days-old summary"
    )
    after = card[_idx(card, "else { return nil }"):]
    assert "card.isStale = true" in after, "a snapshot card says '· up to date' — that was the server's claim at save time"
    assert "card.isRefreshing = false" in after, (
        "a snapshot card keeps the server's 'refreshing' — it claims a new card is being made now"
    )
    _in_order(after, "card.isStale = true", "card.isRefreshing = false", "return card")
    assert "Self.snapshotInsight(feed.insight)" in _fn(s, _SEED)


_PREPARE_ORDER = ("expireSnapshotIfStale()", "await snapshotStore.prepare(apiClient: apiClient)",
                  "dropSeedIfEpochMoved()", "seedFromSnapshot()")


def _check_prepare_order(s):
    prep = _fn(s, _PREPARE)
    _in_order(prep, *_PREPARE_ORDER)
    for token in ("loadIfNeeded(", "loadFeed(", "loadTabs(", "refresh(", "apiClient.request"):
        assert token not in prep, f"prepareSnapshot runs `{token}` — it must be disk-only"
    drop = _fn(s, _DROP_EPOCH)
    assert "guard snapshotSavedAt != nil, let seeded = seededEpoch, seeded != snapshotStore.epoch else { return }" in drop, (
        "a seed whose store epoch moved (another owner, a session end, Clear Cache) is kept"
    )
    assert "dropSnapshot()" in drop


def _check_single_save_never_in_catch(s):
    vm = _vm(s)
    assert vm.count("snapshotStore.save(") == 1, "Updates must save its snapshot from exactly one place"
    for block in _catch_blocks(vm):
        assert "snapshotStore.save(" not in block, "a snapshot is saved from a catch — a failure is never a live answer"
    feed = _fn(s, _LOAD_FEED)
    assert feed.count("let snapshotEpoch = snapshotStore.epoch") == 1
    assert _idx(feed, "let snapshotEpoch = snapshotStore.epoch") < _idx(feed, _REQUEST), (
        "the snapshot epoch is captured after the request — a response that left under the "
        "previous account could be saved under the next"
    )
    success = _success_region(feed)
    assert "snapshotStore.save(" in success, "the save left loadFeed's live success path"
    _in_order(success, _REQUEST, "let fetchedAt = Date()", "guard loadToken == token else {",
              "if scope == UpdatesScope.market, !articles.isEmpty {", "snapshotStore.save(")
    guarded = _block_at(success, _idx(success, "if scope == UpdatesScope.market, !articles.isEmpty {"))
    call = _norm(_paren_at(guarded, _idx(guarded, "snapshotStore.save(")))
    assert call == ("( parts: [UpdatesFeedSnapshot.feedPart: body], payload: UpdatesFeedSnapshot(feed: response), "
                    "savedAt: fetchedAt, epoch: snapshotEpoch )"), f"the save call changed: {call}"


def _check_store_from_shared_once(s):
    src = s["vm"]
    assert src.count("UpdatesFeedSnapshotStore.shared") == 1, (
        "the ViewModel reaches the shared store more than once — it must hold the one instance "
        "init assigned"
    )
    init = _fn(s, _INIT)
    assert "self.snapshotStore = UpdatesFeedSnapshotStore.shared" in init
    assert _vm(s).count(_INIT + " {") == 1, (
        "the init header changed — test_ios_sticky_list_preferences pins it byte-for-byte"
    )
    assigns = re.findall(r"(?<![\w])snapshotStore\s*=(?!=)", _vm(s))
    assert len(assigns) == 1, "the snapshot store is assigned outside init"
    assert "private let snapshotStore: UpdatesFeedSnapshotStore" in _vm(s)


_IDENTITY_CLEARS = (
    "identityGeneration &+= 1", "initialLoadTask?.cancel()", "loadToken = UUID()", "inFlightScope = nil",
    "enrichTask?.cancel()", "refreshPollTask?.cancel()", "appearWorkTask?.cancel()", "feedCache.removeAll()",
    "allNewsArticles = []", "insightSummary = nil", "loadedOffset = 0", "hasMorePages = false",
    "filterTabs = []", "selectedTab = nil", "tabsAreLive = false", "groupName = nil", "lockedTickerCount = 0",
    "tierRequiredForMoreTickers = nil", "snapshotSavedAt = nil", "seededEpoch = nil", "snapshotInsightID = nil",
    "hasShownFeed = false", "error = nil", "isLoading = true",
)


def _check_identity_clears_before_gate(s):
    handler = _fn(s, _IDENTITY)
    gate = _idx(handler, _GATE)
    for token in _IDENTITY_CLEARS:
        assert _idx(handler, token) < gate, (
            f"`{token}` runs after the active-tab gate — a hidden tab keeps the previous "
            "identity's feed, chips or loads (auth.md §7)"
        )
    _in_order(handler, "allNewsArticles = []", "filterOptions = NewsFilterOptions.loadSaved()",
              "await prepareSnapshot()", _GATE, "await loadIfNeeded()")
    after = handler[gate:]
    for token in ("hasLoadedOnce = true", "await loadTabs()", "loadFeed("):
        assert token not in after, f"the identity reload runs `{token}` — it must go through loadIfNeeded"


GUARDS: dict[str, Callable[[dict[str, str]], None]] = {
    "first_open_parallel": _check_first_open_parallel,
    "serial_paths_stay_serial": _check_serial_paths_stay_serial,
    "trailing_isloading_fenced": _check_trailing_isloading_fenced,
    "loadtabs_fences": _check_loadtabs_fences,
    "pending_scope_survives_heal": _check_pending_scope_survives_heal,
    "market_seed_kept_while_live_loads": _check_market_seed_kept_while_live_loads,
    "live_answer_replaces_snapshot": _check_live_answer_replaces_snapshot,
    "refusal_drops_failure_keeps": _check_refusal_drops_failure_keeps,
    "generic_catch_fenced": _check_generic_catch_fenced,
    "enrichment_detached": _check_enrichment_detached,
    "post_paint_waits_for_the_tab": _check_post_paint_waits_for_the_tab,
    "no_enrichment_of_a_snapshot": _check_no_enrichment_of_a_snapshot,
    "first_load_owned": _check_first_load_owned,
    "first_frame_not_empty": _check_first_frame_not_empty,
    "no_network_in_init": _check_no_network_in_init,
    "render_gate": _check_render_gate,
    "shimmer_gate": _check_shimmer_gate,
    "ask_cay_nil_on_snapshot": _check_ask_cay_nil_on_snapshot,
    "retry_notice_placement": _check_retry_notice_placement,
    "task_prepare_order": _check_task_prepare_order,
    "expiry": _check_expiry,
    "trend_waits_for_first_feed": _check_trend_waits_for_first_feed,
    "insights_slot_height_stable": _check_insights_slot_height_stable,
    "header_is_honest": _check_header_is_honest,
    "seed_precondition_first": _check_seed_precondition_first,
    "snapshot_card_is_stale": _check_snapshot_card_is_stale,
    "prepare_order": _check_prepare_order,
    "single_save_never_in_catch": _check_single_save_never_in_catch,
    "store_from_shared_once": _check_store_from_shared_once,
    "identity_clears_before_gate": _check_identity_clears_before_gate,
}


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_guard_holds_on_the_real_source(name):
    GUARDS[name](_sources())


# ── Mutations: each guard must fail on the bug it names ──────────────────────────────

def _rm(token: str) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        assert src.count(token) >= 1, f"mutation anchor {token!r} not in source"
        return src.replace(token, "", 1)
    return mutate


def _sub(pattern: str, repl: str, flags: int = re.S) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        out, n = re.subn(pattern, repl, src, count=1, flags=flags)
        assert n == 1, f"mutation pattern {pattern!r} did not match"
        return out
    return mutate


def _within(header: str, pattern: str, repl: str, flags: int = re.S) -> Callable[[str], str]:
    """Substitute ONCE inside the brace block of `header` (which must be unique), so a token
    that also appears in another function cannot be the one mutated."""
    def mutate(src: str) -> str:
        assert src.count(header) == 1, f"mutation header {header!r} is not unique"
        start = src.find(header) + len(header)
        open_brace = src.find("{", start)
        block = _block_at(src, start)
        new_block, n = re.subn(pattern, repl, block, count=1, flags=flags)
        assert n == 1, f"mutation pattern {pattern!r} did not match inside {header!r}"
        return src[:open_brace] + new_block + src[open_brace + len(block):]
    return mutate


def _chain(*mutations: Callable[[str], str]) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        for m in mutations:
            src = m(src)
        return src
    return mutate


_E = re.escape

MUTATIONS: list[tuple] = [
    # ── parallel first open ──
    ("serial-first-open", "first_open_parallel", "vm",
     _within(_LOAD_INITIAL, _E("async let tabs: Void = loadTabs()") + r"(\s*)" + _E("await loadFeed(for: market, force: false)") + r"\s*await tabs",
             r"await loadTabs()\1await loadFeed(for: market, force: false)")),
    ("yield-before-claim", "first_open_parallel", "vm",
     _within(_LOAD_INITIAL, r"(selectedTab = market\n)", r"\1            await Task.yield()\n"), "await sits between"),
    ("claim-after-feed", "first_open_parallel", "vm",
     _within(_LOAD_INITIAL, r"selectedTab = market\n(\s*async let tabs: Void = loadTabs\(\)\n\s*await loadFeed\(for: market, force: false\)\n)",
             r"\1            selectedTab = market\n")),
    ("ticker-prefetch", "first_open_parallel", "vm",
     _within(_LOAD_INITIAL, _E("filterTabs.first(where: { $0.isMarketTab })"), "filterTabs.first")),
    ("any-selection-parallel", "first_open_parallel", "vm",
     _within(_LOAD_INITIAL, _E("if effectiveScope == UpdatesScope.market {"), "if true {")),
    ("pending-scope-ignored", "first_open_parallel", "vm",
     _within(_LOAD_INITIAL, _E(" ?? pendingScope ?? UpdatesScope.market"), " ?? UpdatesScope.market"),
     "same-account heal on a ticker chip"),
    ("ticker-beside-tabs", "first_open_parallel", "vm",
     _within(_LOAD_INITIAL, r"\} else \{\s*await loadTabs\(\)",
             "} else {\n            async let early: Void = loadTabs()\n            await early"),
     "before /tabs confirms"),
    ("refresh-parallel", "serial_paths_stay_serial", "vm",
     _within(_REFRESH, _E("await loadTabs()"), "async let t: Void = loadTabs()")),
    ("group-reload-parallel", "serial_paths_stay_serial", "vm",
     _within(_GROUP_RELOAD, _E("await loadTabs()"), "async let t: Void = loadTabs()")),
    ("unguarded-trailing", "trailing_isloading_fenced", "vm",
     _within(_LOAD_INITIAL, r"if generation == identityGeneration, inFlightScope == nil \{\s*isLoading = false\s*\}",
             "isLoading = false"), "no longer fenced"),
    ("trailing-unfenced-by-identity", "trailing_isloading_fenced", "vm",
     _within(_LOAD_INITIAL, _E("if generation == identityGeneration, inFlightScope == nil {"),
             "if inFlightScope == nil {"), "no longer fenced"),
    # ── loadTabs ──
    ("previous-scope-at-request", "loadtabs_fences", "vm",
     _within(_LOAD_TABS, r"(let generation = identityGeneration\n)(.*?)(\s*let previousScope = selectedTab\?\.scope \?\? pendingScope\n)",
             r"\1        let previousScope = selectedTab?.scope ?? pendingScope\n\2\n")),
    ("late-tabs-unfenced", "loadtabs_fences", "vm",
     _within(_LOAD_TABS, r"guard generation == identityGeneration else \{\s*print\([^\n]*\)\s*return\s*\}", "")),
    ("tabs-catch-unfenced", "loadtabs_fences", "vm",
     _within(_LOAD_TABS, r"(\} catch \{\s*)guard generation == identityGeneration else \{ return \}", r"\1")),
    ("no-generation-bump", "loadtabs_fences", "vm", _within(_IDENTITY, _E("identityGeneration &+= 1"), ""),
     "no longer bumps the generation"),
    ("fallback-only-on-empty", "loadtabs_fences", "vm",
     _within(_LOAD_TABS, _E("if selectedTab == nil || !tabsAreLive {"), "if filterTabs.isEmpty {")),
    ("fallback-no-selection", "loadtabs_fences", "vm",
     _within(_LOAD_TABS, _E("selectedTab = filterTabs.first(where: { $0.isMarketTab }) ?? Self.marketTabFallback"), ""),
     "no longer selects Market"),
    ("pending-scope-dropped", "loadtabs_fences", "vm",
     _within(_LOAD_TABS, _E("let previousScope = selectedTab?.scope ?? pendingScope"), "let previousScope = selectedTab?.scope"),
     "loses its chip"),
    ("tabs-never-live", "loadtabs_fences", "vm", _within(_LOAD_TABS, _E("tabsAreLive = true\n"), "")),
    ("tabs-cancel-falls-back", "loadtabs_fences", "vm",
     _within(_LOAD_TABS, r"guard !appError\.isCancellation else \{ return \}\n", ""), "not found"),
    ("pending-not-stashed", "pending_scope_survives_heal", "vm",
     _within(_IDENTITY, _E(_STASH), ""), "no longer stashes"),
    ("stash-after-clear", "pending_scope_survives_heal", "vm",
     _chain(_within(_IDENTITY, _E(_STASH + "\n"), ""),
            _within(_IDENTITY, r"(selectedTab = nil\n)", lambda m: m.group(1) + "        " + _STASH + "\n")),
     "out of order"),
    ("stash-crosses-accounts", "pending_scope_survives_heal", "vm",
     _within(_IDENTITY, _E(_STASH), "pendingScope = pendingScope ?? selectedTab?.scope"), "ended session's chip"),
    ("stash-prefers-fallback", "pending_scope_survives_heal", "vm",
     _within(_IDENTITY, _E("(pendingScope ?? selectedTab?.scope)"), "(selectedTab?.scope ?? pendingScope)"),
     "Market fallback"),
    ("selection-never-stamped", "pending_scope_survives_heal", "vm",
     _within(_LOAD_FEED, r"selectionEpoch = snapshotStore\.epoch\n", ""), "which account made the selection"),
    ("selection-stamped-elsewhere", "pending_scope_survives_heal", "vm",
     _within(_REFRESH, r"(\{\n)", r"\1        selectionEpoch = snapshotStore.epoch\n"), "written outside loadFeed"),
    ("stamp-after-cache", "pending_scope_survives_heal", "vm",
     _chain(_within(_LOAD_FEED, r"selectionEpoch = snapshotStore\.epoch\n", ""),
            _within(_LOAD_FEED, r"(let fetchedAt = Date\(\)\n)", r"\1            selectionEpoch = snapshotStore.epoch\n")),
     "out of order"),
    # ── loadFeed with a snapshot ──
    ("market-seed-cleared", "market_seed_kept_while_live_loads", "vm",
     _within(_LOAD_FEED, _E(_CONDITIONAL_CLEAR), "if true {")),
    ("snapshot-under-ticker", "market_seed_kept_while_live_loads", "vm",
     _within(_LOAD_FEED, _E(" || scope != UpdatesScope.market"), "")),
    ("unconditional-row-clear", "market_seed_kept_while_live_loads", "vm",
     _within(_LOAD_FEED, r"(\n\s*isLoading = true\n\s*error = nil\n)", r"\1        allNewsArticles = []\n"),
     "outside the conditional clear"),
    ("isloading-conditional", "market_seed_kept_while_live_loads", "vm",
     _within(_LOAD_FEED, r"(insightSummary = nil\n)(\s*\}\n)(\s*isLoading = true\n)", r"\1\3\2"),
     "is now conditional"),
    ("live-keeps-label", "live_answer_replaces_snapshot", "vm",
     _within(_LOAD_FEED, r"(let replacedSnapshotSeededAt = seededAt\s*)snapshotSavedAt = nil\n", r"\1")),
    ("live-not-shown", "live_answer_replaces_snapshot", "vm",
     _within(_LOAD_FEED, r"(seededAt = nil\s*)hasShownFeed = true\n", r"\1")),
    ("cache-keeps-label", "live_answer_replaces_snapshot", "vm",
     _within(_LOAD_FEED, r"(insightSummary = cached\.insight\s*)snapshotSavedAt = nil\n", r"\1"), "the cached path lost"),
    ("refusal-keeps-snapshot", "refusal_drops_failure_keeps", "vm",
     _within(_LOAD_FEED, r"(self\.error = nil\s*)dropSnapshot\(\)", r"\1"), "keeps the snapshot"),
    ("failure-drops-snapshot", "refusal_drops_failure_keeps", "vm",
     _within(_LOAD_FEED, r"(self\.error = appError\.message\n)", r"\1                dropSnapshot()\n"), "vanishes offline"),
    ("failure-no-expiry", "refusal_drops_failure_keeps", "vm",
     _within(_LOAD_FEED, r"(isReconnecting = false\s*)expireSnapshotIfStale\(\)", r"\1"), "no longer bounds"),
    ("drop-keeps-rows", "refusal_drops_failure_keeps", "vm", _within(_DROP, _E("allNewsArticles = []"), "")),
    ("notice-under-gate", "refusal_drops_failure_keeps", "vm",
     _within("var showsSnapshotRefreshFailure: Bool", _E("!requiresSignIn && "), ""), "lost `!requiresSignIn`"),
    ("generic-catch-unfenced", "generic_catch_fenced", "vm",
     _within(_LOAD_FEED, r"(\} catch \{\s*)guard loadToken == token else \{ return \}", r"\1")),
    ("cancel-arm-guard-form", "generic_catch_fenced", "vm",
     _within(_LOAD_FEED, _E("if loadToken != token { return }"), "guard loadToken == token else { return }"),
     "goes vacuous"),
    ("cancel-arm-unfenced", "generic_catch_fenced", "vm", _within(_LOAD_FEED, _E("if loadToken != token { return }"), "")),
    ("cancel-sets-error", "generic_catch_fenced", "vm",
     _within(_LOAD_FEED, r"if appError\.isCancellation \{\s*isLoading = false\s*return\s*\}", "")),
    ("cancel-reported", "generic_catch_fenced", "vm",
     _within(_LOAD_FEED, r"(if appError\.isCancellation \{\s*)isLoading = false", r'\1self.error = "cancelled"'),
     "reported as an error"),
    ("urlerror-by-hand", "generic_catch_fenced", "vm",
     _within(_LOAD_FEED, r"(let appError = AppError\.from\(error\)\n)",
             r"\1            if (error as? URLError)?.code == .cancelled { return }\n"), "by hand again"),
    # ── enrichment ──
    ("enrich-awaited", "enrichment_detached", "vm",
     _within(_LOAD_FEED, _E("startPostPaintWork(scope: scope, token: token, pollInsight: true)"),
             "await enrichVisibleWindow(around: 0, scope: scope, token: token)"), "awaits the AI enrichment"),
    ("cached-path-not-enriched", "enrichment_detached", "vm",
     _within(_LOAD_FEED, _E("startPostPaintWork(scope: scope, token: token, pollInsight: false)"), "_ = 0"),
     "each start the detached enrichment"),
    # ── post-paint work waits for the tab ──
    ("live-path-enriches-directly", "post_paint_waits_for_the_tab", "vm",
     _within(_LOAD_FEED, _E("startPostPaintWork(scope: scope, token: token, pollInsight: true)"),
             "startEnrichment(scope: scope, token: token)\n            scheduleInsightPollIfNeeded(scope: scope, token: token)"),
     "calls `startEnrichment(` directly"),
    ("post-paint-on-hidden-tab", "post_paint_waits_for_the_tab", "vm",
     _within(_START_POST, r"guard isTabActive else \{.*?return\s*\}\n", ""), "not found"),
    ("hidden-not-recorded", "post_paint_waits_for_the_tab", "vm",
     _within(_START_POST, r"deferredPostPaint = \(scope: scope, token: token, pollInsight: pollInsight\)\n", "")),
    ("deferred-never-resumed", "post_paint_waits_for_the_tab", "vm",
     _within(_SET_ACTIVE, r"if active, let pending = deferredPostPaint \{.*?\n        \}\n", ""), "not found"),
    ("resume-stale-load", "post_paint_waits_for_the_tab", "vm",
     _within(_SET_ACTIVE, _E("pending.token == loadToken, "), ""), "not found"),
    ("resume-runs-twice", "post_paint_waits_for_the_tab", "vm",
     _within(_SET_ACTIVE, r"(let pending = deferredPostPaint \{\n)\s*deferredPostPaint = nil\n", r"\1"), "started twice"),
    ("hidden-rows-enrich", "post_paint_waits_for_the_tab", "vm",
     _within(_APPEAR, r"guard isTabActive else \{ return \}\n", ""), "not found"),
    ("deferred-survives-identity", "post_paint_waits_for_the_tab", "vm",
     _within(_IDENTITY, r"deferredPostPaint = nil\n", ""), "not found"),
    ("enrich-not-cancelled-on-scope", "enrichment_detached", "vm",
     _within(_LOAD_FEED, r"enrichTask\?\.cancel\(\)\n", ""), "not found"),
    ("enrich-survives-identity", "enrichment_detached", "vm",
     _within(_IDENTITY, r"enrichTask\?\.cancel\(\)\n", ""), "not found"),
    ("enrich-not-replaced", "enrichment_detached", "vm", _within(_START_ENRICH, r"enrichTask\?\.cancel\(\)\n", "")),
    ("deinit-keeps-enrich", "enrichment_detached", "vm", _within("deinit", r"enrichTask\?\.cancel\(\); ", "")),
    ("snapshot-enriched", "no_enrichment_of_a_snapshot", "vm",
     _within(_ENRICH_WINDOW, r"guard snapshotSavedAt == nil else \{ return \}\n", "")),
    ("snapshot-merge-cached", "no_enrichment_of_a_snapshot", "vm",
     _within(_MERGE, r"if snapshotSavedAt == nil \{\s*(feedCache\[scope\] = [^\n]*)\n\s*\}", r"\1")),
    # ── owned first load ──
    ("latch-on-failure", "first_load_owned", "vm", _within(_LOAD_IF_NEEDED, _E("self.error == nil, "), "")),
    ("latch-over-snapshot", "first_load_owned", "vm", _within(_LOAD_IF_NEEDED, _E(", self.snapshotSavedAt == nil"), "")),
    ("latch-on-cancel", "first_load_owned", "vm", _within(_LOAD_IF_NEEDED, _E("!Task.isCancelled, "), "")),
    ("structured-first-load", "first_load_owned", "vm",
     _within(_LOAD_IF_NEEDED, r"let task = Task \{.*?await task\.value", "await loadInitialData()\n        hasLoadedOnce = true")),
    ("no-join", "first_load_owned", "vm",
     _within(_LOAD_IF_NEEDED, r"if let running = initialLoadTask \{\s*await running\.value\s*return\s*\}\n", "")),
    ("clears-newer-task", "first_load_owned", "vm", _within(_LOAD_IF_NEEDED, _E("if self.initialLoadID == id {"), "if true {")),
    ("no-activation-expiry", "first_load_owned", "vm", _within(_LOAD_IF_NEEDED, r"expireSnapshotIfStale\(\)\n", "")),
    ("reentrancy-flag-back", "first_load_owned", "vm",
     _sub(r"(\n    private var hasLoadedOnce = false\n)", r"\1    private var isLoadingInitial = false\n"), "old re-entrancy flag"),
    ("isloading-false", "first_frame_not_empty", "vm",
     _sub(_E("@Published var isLoading: Bool = true"), "@Published var isLoading: Bool = false")),
    ("init-loads", "no_network_in_init", "vm",
     _within(_INIT, r"(restoreTrendWindowPreference\(\)\n)", r"\1        Task { await self.loadIfNeeded() }\n")),
    ("init-seeds", "no_network_in_init", "vm",
     _within(_INIT, r"(restoreTrendWindowPreference\(\)\n)", r"\1        seedFromSnapshot()\n"), "seedFromSnapshot("),
    # ── the view ──
    ("rows-ungated", "render_gate", "view",
     _within("private func newsSections()", _E("if isActiveTab || viewModel.snapshotSavedAt == nil {"), "if true {")),
    ("gate-swap-animated", "render_gate", "view",
     _sub(r"\n[ \t]*\.transaction\(value: isActiveTab\) \{ \$0\.animation = nil \}", ""), "cross-fade"),
    ("card-ungated", "render_gate", "view",
     _sub(r"(if let summary = viewModel\.insightSummary),\s*isActiveTab \|\| viewModel\.snapshotSavedAt == nil \{", r"\1 {"),
     "hidden tab"),
    ("shimmer-hidden", "shimmer_gate", "view",
     _within("private var loadingSkeleton: some View", _E("if isActiveTab {"), "if true {")),
    ("shimmer-in-both-arms", "shimmer_gate", "view",
     _within("private var loadingSkeleton: some View", _E("Color.clear.frame(height: 1)"), "TickerNewsShimmerCard()"),
     "hidden tab"),
    ("snapshot-ask-cay", "ask_cay_nil_on_snapshot", "view",
     _sub(_E("onAskCay: viewModel.snapshotSavedAt == nil ? { openUpdatesChat(focus: .card) } : nil"),
          "onAskCay: { openUpdatesChat(focus: .card) }"), "offers 'Ask Cay AI'"),
    ("sheet-ask-cay", "ask_cay_nil_on_snapshot", "view",
     _sub(r"(if viewModel\.isSnapshotInsight\(summary\) \{\s*)InsightsDetailView\(summary: summary\)",
          r"\1InsightsDetailView(summary: summary, onAskCay: { pendingChatFocus = .card })"), "detail sheet"),
    ("snapshot-card-untracked", "ask_cay_nil_on_snapshot", "vm",
     _within(_SEED, r"snapshotInsightID = card\?\.id\n", ""), "which card"),
    ("notice-on-any-error", "retry_notice_placement", "view",
     _sub(_E("if viewModel.showsSnapshotRefreshFailure {"), "if viewModel.error != nil {")),
    ("notice-no-retry", "retry_notice_placement", "view",
     _within("if viewModel.showsSnapshotRefreshFailure", _E("await viewModel.refresh()"), "print(1)"), "no retry"),
    ("notice-long-at-ax", "retry_notice_placement", "view",
     _within(_NOTICE_TEXT, r'\? "Couldn\'t refresh the news\."',
             '? "Couldn\'t refresh the news. These stories are from your last visit."'), "300+ pt"),
    ("notice-ax-unaware", "retry_notice_placement", "view",
     _within(_NOTICE_TEXT, _E("dynamicTypeSize.isAccessibilitySize"), "false"), "300+ pt"),
    ("prepare-after-guard", "task_prepare_order", "view",
     _within(".task(id: isActiveTab)", r"await viewModel\.prepareSnapshot\(\)\n(\s*guard isActiveTab else \{ return \}\n)",
             r"\1                await viewModel.prepareSnapshot()\n"), "out of order"),
    ("no-cancel-check", "task_prepare_order", "view",
     _within(".task(id: isActiveTab)", r"guard !Task\.isCancelled else \{ return \}\n", ""), "not found"),
    ("no-prepare", "task_prepare_order", "view",
     _within(".task(id: isActiveTab)", r"await viewModel\.prepareSnapshot\(\)\n", ""), "not found"),
    ("expiry-inverted", "expiry", "vm",
     _within(_EXPIRE, _E("!AccountSnapshotPolicy.isDisplayable("), "AccountSnapshotPolicy.isDisplayable(")),
    ("no-foreground-expiry", "expiry", "view",
     _sub(r"(UIApplication\.didBecomeActiveNotification\s*\)\s*\)\s*\{ _ in\s*)viewModel\.expireSnapshotIfStale\(\)",
          r"\1_ = 0"), "on foreground"),
    ("trend-before-feed", "trend_waits_for_first_feed", "view",
     _within(_TREND, _E(_TREND_GATE) + r"\s*", ""), "paints over the skeleton"),
    ("trend-joins-the-swap", "trend_waits_for_first_feed", "view",
     _within(_TREND, _E(" || (viewModel.snapshotSavedAt != nil && viewModel.insightSummary != nil)"), ""),
     "same transaction"),
    ("trend-over-cardless-snapshot", "trend_waits_for_first_feed", "view",
     _within(_TREND, _E(" && viewModel.insightSummary != nil"), ""), "same transaction"),
    # ── the Insights slot keeps its height across the snapshot → live swap ──
    ("reserve-dropped", "insights_slot_height_stable", "view",
     _sub(r"(if viewModel\.snapshotSavedAt != nil \{\s*)askCayPillReserve", r"\1EmptyView()"), "drops ~50 pt"),
    ("reserve-on-live", "insights_slot_height_stable", "view",
     _sub(r"if viewModel\.snapshotSavedAt != nil \{(\s*askCayPillReserve)", r"if true {\1"), "not found"),
    ("reserve-outside-slot", "insights_slot_height_stable", "view",
     _chain(_sub(r"\s*if viewModel\.snapshotSavedAt != nil \{\s*askCayPillReserve\s*\}", ""),
            _sub(r"(\n\s*)(if let trend = visibleTrend \{)", r"\1if viewModel.snapshotSavedAt != nil { askCayPillReserve }\1\2")),
     "not found"),
    ("no-card-identity", "insights_slot_height_stable", "view", _sub(r"\n\s*\.id\(summary\.id\)", ""), "100%-CPU"),
    ("slot-spacing", "insights_slot_height_stable", "view",
     _sub(r"VStack\(alignment: \.leading, spacing: AppSpacing\.md\) \{(\s*InsightsSummaryCard\()",
          r"VStack(alignment: .leading, spacing: AppSpacing.sm) {\1"), "card plus its reserved pill row"),
    ("reserve-visible", "insights_slot_height_stable", "view",
     _within(_RESERVE, r"\.hidden\(\)\n\s*", ""), "invisible, inert"),
    ("reserve-tappable", "insights_slot_height_stable", "view",
     _within(_RESERVE, r"\.allowsHitTesting\(false\)\n\s*", ""), "invisible, inert"),
    ("reserve-in-voiceover", "insights_slot_height_stable", "view",
     _within(_RESERVE, r"\.accessibilityHidden\(true\)", ""), "invisible, inert"),
    ("card-pill-retitled", "insights_slot_height_stable", "card",
     _sub(_E('AskCayAIPill(title: "Ask Cay AI about this", action: onAskCay)'),
          'AskCayAIPill(title: "Ask Cay AI", action: onAskCay)'), "pill changed"),
    ("card-row-spacing", "insights_slot_height_stable", "card",
     _sub(_E("VStack(alignment: .leading, spacing: AppSpacing.md) {"),
          "VStack(alignment: .leading, spacing: AppSpacing.lg) {"), "row spacing changed"),
    # ── the header ──
    ("header-arg-dropped", "header_is_honest", "view",
     _sub(r"snapshotStatusText: viewModel\.snapshotUpdatedLabel,\s*", ""), "told the rows are a snapshot"),
    ("snapshot-says-live", "header_is_honest", "header",
     _within("if let snapshotStatusText", _E('Text("News")'), 'Text("Live News")')),
    ("snapshot-pulses", "header_is_honest", "header",
     _within("if let snapshotStatusText", r"(HStack\(spacing: AppSpacing\.sm\) \{\n)", r"\1                LiveIndicator()\n"),
     "claims to be live"),
    ("own-label-format", "header_is_honest", "vm",
     _within("var snapshotUpdatedLabel: String?", _E("AccountSnapshotPolicy.updatedLabel(savedAt: $0)"), '"Updated \\\\($0)"'),
     "second wording source"),
    ("header-two-elements", "header_is_honest", "header",
     _within("if let snapshotStatusText", r"\.accessibilityElement\(children: \.combine\)\n", ""), "lost"),
    ("label-unscaled", "header_is_honest", "header",
     _within("if let snapshotStatusText", r"\.minimumScaleFactor\(0\.75\)\n", ""), "lost"),
    ("header-unfixed", "header_is_honest", "header", _sub(r"\.frame\(height: 44\)", ".frame(minHeight: 44)"), "fixed height"),
    # ── the account-snapshot contract's ViewModel rows ──
    ("seed-overwrites-live", "seed_precondition_first", "vm", _within(_SEED, _E("!hasShownFeed, "), ""), "OPENS"),
    ("seed-ignores-gate", "seed_precondition_first", "vm",
     _within(_SEED, r"!requiresSignIn, !isReconnecting,\s*", ""), "OPENS"),
    ("seed-not-first", "seed_precondition_first", "vm",
     _within(_SEED, r"^\{\n", "{\n        snapshotInsightID = nil\n", re.S), "OPENS"),
    ("seed-fills-feedCache", "seed_precondition_first", "vm",
     _within(_SEED, r"(applyFiltersAndGroup\(\)\n)", r"\1        feedCache[UpdatesScope.market] = (articles, nil, 0, false)\n"),
     "feedCache"),
    ("seed-latches", "seed_precondition_first", "vm",
     _within(_SEED, r"(applyFiltersAndGroup\(\)\n)", r"\1        hasLoadedOnce = true\n"), "hasLoadedOnce"),
    ("seed-selects", "seed_precondition_first", "vm",
     _within(_SEED, r"(applyFiltersAndGroup\(\)\n)", r"\1        selectedTab = filterTabs.first\n"), "selectedTab"),
    ("seed-spawns", "seed_precondition_first", "vm",
     _within(_SEED, r"(applyFiltersAndGroup\(\)\n)", r"\1        Task { await self.loadIfNeeded() }\n"), "Task {"),
    ("seed-no-epoch", "seed_precondition_first", "vm", _within(_SEED, r"seededEpoch = snapshotStore\.epoch\n", ""), "lost"),
    ("seed-any-scope", "seed_precondition_first", "vm",
     _within(_SEED, r"\(selectedTab\?\.scope \?\? pendingScope \?\? UpdatesScope\.market\) == UpdatesScope\.market,\s*", ""),
     "Market stories"),
    ("card-date-unchecked", "snapshot_card_is_stale", "vm",
     _within(_SNAPSHOT_CARD, r"UpdatesDateParser\.parse\(dto\.generatedAt\) != nil,\s*", ""), "unparseable"),
    ("card-claims-current", "snapshot_card_is_stale", "vm",
     _within(_SNAPSHOT_CARD, r"card\.isStale = true\n", ""), "up to date"),
    ("card-keeps-refreshing", "snapshot_card_is_stale", "vm",
     _within(_SNAPSHOT_CARD, r"card\.isRefreshing = false\n", ""), "refreshing"),
    ("card-unused-by-seed", "snapshot_card_is_stale", "vm",
     _within(_SEED, _E("Self.snapshotInsight(feed.insight)"), "feed.insight.flatMap { NewsInsightSummary(dto: $0) }")),
    ("refusal-reseeds", "seed_precondition_first", "vm",
     _within(_LOAD_FEED, r"(self\.error = nil\s*dropSnapshot\(\)\n)", r"\1                seedFromSnapshot()\n"),
     "outside prepareSnapshot"),
    ("prepare-no-expiry", "prepare_order", "vm", _within(_PREPARE, r"expireSnapshotIfStale\(\)\n", ""), "not found"),
    ("prepare-no-epoch-check", "prepare_order", "vm", _within(_PREPARE, r"dropSeedIfEpochMoved\(\)\n", ""), "not found"),
    ("seed-before-read", "prepare_order", "vm",
     _within(_PREPARE, r"(await snapshotStore\.prepare\(apiClient: apiClient\)\n)(\s*dropSeedIfEpochMoved\(\)\n)(\s*seedFromSnapshot\(\)\n)",
             r"\3\1\2"), "out of order"),
    ("prepare-fetches", "prepare_order", "vm",
     _within(_PREPARE, r"(seedFromSnapshot\(\)\n)", r"\1        await loadIfNeeded()\n"), "disk-only"),
    ("epoch-never-drops", "prepare_order", "vm", _within(_DROP_EPOCH, _E("seeded != snapshotStore.epoch"), "false"), "is kept"),
    ("epoch-after-request", "single_save_never_in_catch", "vm",
     _chain(_within(_LOAD_FEED, r"let snapshotEpoch = snapshotStore\.epoch\n", ""),
            _within(_LOAD_FEED, r"(let fetchedAt = Date\(\)\n)", r"\1            let snapshotEpoch = snapshotStore.epoch\n")),
     "captured after the request"),
    ("ticker-feed-saved", "single_save_never_in_catch", "vm",
     _within(_LOAD_FEED, _E("if scope == UpdatesScope.market, !articles.isEmpty {"), "if !articles.isEmpty {"), "not found"),
    ("empty-feed-saved", "single_save_never_in_catch", "vm",
     _within(_LOAD_FEED, _E(", !articles.isEmpty {"), " {"), "not found"),
    ("save-in-catch", "single_save_never_in_catch", "vm",
     _chain(_within(_LOAD_FEED, r"if scope == UpdatesScope\.market, !articles\.isEmpty \{\s*snapshotStore\.save\(.*?epoch: snapshotEpoch\s*\)\s*\}\n", ""),
            _within(_LOAD_FEED, r"(self\.error = appError\.message\n)",
                    r"\1                snapshotStore.save(parts: [:], payload: UpdatesFeedSnapshot(feed: UpdatesFeedResponse(scope: \"\", articles: nil, insight: nil, cached: nil, cacheAgeSeconds: nil, offset: nil, hasMore: nil)), savedAt: Date(), epoch: snapshotEpoch)\n")),
     "saved from a catch"),
    ("second-save", "single_save_never_in_catch", "vm",
     _within(_LOAD_FEED, r"(hasShownFeed = true\n\s*loadedOffset = cached\.offset)",
             r"snapshotStore.save(parts: [:], payload: UpdatesFeedSnapshot(feed: cached), savedAt: Date(), epoch: 0)\n            \1"),
     "exactly one place"),
    ("label-now", "single_save_never_in_catch", "vm",
     _within(_LOAD_FEED, _E("savedAt: fetchedAt,"), "savedAt: Date(),"), "the save call changed"),
    ("save-before-stale-check", "single_save_never_in_catch", "vm",
     _chain(_within(_LOAD_FEED, r"(\n\s*if scope == UpdatesScope\.market, !articles\.isEmpty \{\s*snapshotStore\.save\(.*?epoch: snapshotEpoch\s*\)\s*\}\n)", "\n"),
            _within(_LOAD_FEED, r"(let fetchedAt = Date\(\)\n)",
                    r"\1            if scope == UpdatesScope.market, !articles.isEmpty {\n"
                    r"                snapshotStore.save(parts: [UpdatesFeedSnapshot.feedPart: body], payload: UpdatesFeedSnapshot(feed: response), savedAt: fetchedAt, epoch: snapshotEpoch)\n"
                    r"            }\n")),
     "out of order"),
    ("second-shared-reference", "store_from_shared_once", "vm",
     _within(_PREPARE, r"(\{\n)", r"\1        _ = UpdatesFeedSnapshotStore.shared\n"), "more than once"),
    ("init-di", "store_from_shared_once", "vm",
     _sub(_E("init(apiClient: APIClient = .shared) {\n        self.apiClient = apiClient\n        self.snapshotStore = UpdatesFeedSnapshotStore.shared"),
          "init(apiClient: APIClient = .shared, snapshotStore: UpdatesFeedSnapshotStore? = nil) {\n        self.apiClient = apiClient\n        self.snapshotStore = snapshotStore ?? UpdatesFeedSnapshotStore.shared")),
    ("store-reassigned", "store_from_shared_once", "vm",
     _within(_PREPARE, r"(\{\n)", r"\1        snapshotStore = UpdatesFeedSnapshotStore.shared\n")),
    ("keeps-feedCache", "identity_clears_before_gate", "vm",
     _within(_IDENTITY, r"feedCache\.removeAll\(\)\n", ""), "not found"),
    ("keeps-rows", "identity_clears_before_gate", "vm", _within(_IDENTITY, r"allNewsArticles = \[\]\n", ""), "not found"),
    ("keeps-chips", "identity_clears_before_gate", "vm", _within(_IDENTITY, r"filterTabs = \[\]\n", ""), "not found"),
    ("keeps-selection", "identity_clears_before_gate", "vm", _within(_IDENTITY, r"\n\s*selectedTab = nil\n", "\n"), "not found"),
    ("keeps-token", "identity_clears_before_gate", "vm", _within(_IDENTITY, r"loadToken = UUID\(\)\n", ""), "not found"),
    ("keeps-label", "identity_clears_before_gate", "vm", _within(_IDENTITY, r"\n\s*snapshotSavedAt = nil\n", "\n"), "not found"),
    ("keeps-shown-flag", "identity_clears_before_gate", "vm", _within(_IDENTITY, r"hasShownFeed = false\n", ""), "not found"),
    ("keeps-plan-state", "identity_clears_before_gate", "vm", _within(_IDENTITY, r"lockedTickerCount = 0\n", ""), "not found"),
    ("first-frame-empty", "identity_clears_before_gate", "vm", _within(_IDENTITY, r"\n\s*isLoading = true\n", "\n"), "not found"),
    ("clear-after-gate", "identity_clears_before_gate", "vm",
     _chain(_within(_IDENTITY, r"\n\s*feedCache\.removeAll\(\)\n", "\n"),
            _within(_IDENTITY, r"(guard isActiveTab else \{ return \}\n)", r"\1        feedCache.removeAll()\n")),
     "runs after the active-tab gate"),
    ("reseed-after-gate", "identity_clears_before_gate", "vm",
     _chain(_within(_IDENTITY, r"await prepareSnapshot\(\)\n", ""),
            _within(_IDENTITY, r"(guard isActiveTab else \{ return \}\n)", r"\1        await prepareSnapshot()\n")),
     "out of order"),
    ("relatch-unconditionally", "identity_clears_before_gate", "vm",
     _within(_IDENTITY, r"(await loadIfNeeded\(\)\n)", r"\1        hasLoadedOnce = true\n"), "must go through loadIfNeeded"),
    ("serial-identity-reload", "identity_clears_before_gate", "vm",
     _within(_IDENTITY, _E("await loadIfNeeded()"), "await loadTabs()"), "not found"),
]

_MUTATION_ROWS = [m if len(m) == 5 else (*m, None) for m in MUTATIONS]


@pytest.mark.parametrize("label,guard,key,mutate,message", _MUTATION_ROWS, ids=[m[0] for m in _MUTATION_ROWS])
def test_every_guard_kills_its_mutation(label, guard, key, mutate, message):
    sources = _sources()
    mutated = dict(sources)
    mutated[key] = mutate(sources[key])
    assert mutated[key] != sources[key], f"mutation {label!r} changed nothing"
    # With a message, the guard must fail on THE assertion that names this bug, not trip an
    # unrelated earlier one.
    with pytest.raises(AssertionError, match=re.escape(message) if message else None):
        GUARDS[guard](mutated)


def test_every_guard_has_a_mutation():
    """A guard with no mutation is one nobody has seen fail."""
    covered = {row[1] for row in MUTATIONS}
    assert covered == set(GUARDS), f"guards with no mutation: {sorted(set(GUARDS) - covered)}"


def test_mutation_labels_are_unique():
    labels = [row[0] for row in MUTATIONS]
    assert len(labels) == len(set(labels)), "two mutation rows share a label"


def test_the_comment_stripper_actually_strips():
    """The CONTROL: every guarded token, written only in comments, must vanish."""
    prose = (
        "// seedFromSnapshot()  snapshotStore.save(\n"
        "/// initialLoadTask = task  UpdatesFeedSnapshotStore.shared\n"
        "/* if snapshotSavedAt == nil || scope != UpdatesScope.market {\n"
        "   if isActiveTab || viewModel.snapshotSavedAt == nil { */\n"
        'let url = "https://example.com"  // await viewModel.prepareSnapshot()\n'
    )
    code = _strip(prose)
    for token in ("seedFromSnapshot", "snapshotStore.save", "initialLoadTask", "UpdatesFeedSnapshotStore",
                  "snapshotSavedAt", "isActiveTab", "prepareSnapshot"):
        assert token not in code, f"{token!r} survived the stripper"
    assert "https://example.com" in code, "the stripper ate a URL literal"
    assert len(code.splitlines()) == len(prose.splitlines()), "the stripper must keep line structure"


def test_the_block_bound_actually_bounds():
    """The CONTROL for `_block`: a token in the NEXT function must not be read as this one's."""
    src = "func a() {\n    let x = 1\n}\nfunc b() {\n    snapshotStore.save(\n}\n"
    assert "snapshotStore.save(" not in _block(src, "func a()")
    assert "snapshotStore.save(" in _block(src, "func b()")
