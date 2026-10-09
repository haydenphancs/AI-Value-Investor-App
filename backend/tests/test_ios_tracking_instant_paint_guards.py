"""Tracking (Holdings) paints instantly — and never writes a snapshot back, or paints one wrongly.

Owner ask (2026-10-08): Assets → Holdings should appear almost instantly. A returning user's
cold launch now paints the account's last LIVE Holdings answer from the device
(`TrackingSnapshot.swift` on `AccountSnapshotStore`), labelled "Updated <time>", and the live
load replaces it in place. The generic store and its AppState wiring are pinned by
`test_ios_account_snapshot_guards.py`; this file pins the Tracking half:

  * PortfolioStore: `hasLiveData` (a LIVE answer published this session — `hasLoadedOnce` only
    means "attempted"), a published `loadErrorMessage`, `loadPortfolios() -> Bool`,
    `lastLiveBody`, a purge that REFUSES without live data (the whole-list PUT data-loss guard),
    and `confirmedMutationCount` bumped after every server-confirmed write.
  * TrackingViewModel, the account-snapshot contract rows: the seed precondition as the FIRST
    statement of `seedFromSnapshot` (assignment-only bans after it — the precondition must be
    able to READ the flags), `prepareSnapshot()`'s order, ONE `snapshotStore.save(` in
    `performLoad`, never in a catch, with its epoch captured before `async let feedTask`,
    exactly one `TrackingSnapshotStore.shared` and it sits in `init`, the identity re-seed ABOVE
    the active-tab gate after the clears, and no `portfolioStore` write fed from the snapshot.
  * TrackingViewModel, the Tracking rows: purge only when BOTH halves of this load are live;
    rows / group / score presented all-or-nothing; edit entry points gated; the insights
    tri-state with the account-gate term and an `.idle` start; insights started beside phase 1
    and id-fenced; `confirmedMutationCount` → purge; the latch on load completion; the failed-
    first-load retry keyed on what is missing; the identity data clears pinned EXPLICITLY (the
    old first-match regex in test_ios_tabs_reload_on_identity_change.py is now satisfied by
    `loadTask = nil`).
  * Views: the `.task` prepare order, the render gate (`isActiveTab ||`), the static never-loaded
    skeleton, the "Couldn't load your holdings" path for a failed /portfolios, the labelled
    header with its Retry (on its own line at accessibility text sizes), swipe-remove only on
    live rows.
  * Review-round fixes (2026-10-08): a confirmed edit drops the seed ON SCREEN too, not only the
    file; a removal confirmed elsewhere purges the file (the patched seed stays, re-stamped only
    when it was current); the identity clears are pinned token by token, whales included; the
    insights card carries the snapshot's same-group answer across the snapshot → live swap
    while that request is on the wire; the search star needs a LIVE group list before it fills.

Source scans (there is no XCTest target): comments stripped and every check scoped to its
brace-bounded declaration (.claude/rules/testing.md §3). `MUTATIONS` breaks each property once
and asserts its guard fails — several name the assertion they must fail WITH.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"

_FILES = {
    "vm": _IOS / "ViewModels/TrackingViewModel.swift",
    "pstore": _IOS / "Core/Services/PortfolioStore.swift",
    "view": _IOS / "Views/Screens/TrackingView.swift",
    "section": _IOS / "Views/Organisms/PortfolioInsightsSection.swift",
    "list": _IOS / "Views/Organisms/AssetsListSection.swift",
    "header": _IOS / "Views/Molecules/PortfolioHeaderBar.swift",
    "skeleton": _IOS / "Views/Molecules/TrackedAssetsSkeleton.swift",
}

_VM_INIT = "init(apiClient: APIClient = .shared, portfolioStore: PortfolioStore? = nil)"
_PERFORM = "private func performLoad() async"
_FEED = "private func loadTrackingFeed() async -> Bool"
_IDENTITY = "func handleIdentityChange(isActiveTab: Bool) async"
_SEED = "private func seedFromSnapshot()"
_PREPARE = "func prepareSnapshot() async"
_LIVE_ROOT = "struct TrackingContentViewWithBinding: View"
_ASSETS = "struct AssetsTabContent: View"
_STORE_LOAD = "private func performLoad() async -> Bool"
_PURGE = "func purgeTickers(notIn allowed: Set<String>) async -> Bool"

# Every PortfolioStore write the server confirms, and the request it confirms.
_CONFIRMED_WRITES = {
    "private func syncTickers(for portfolioId: String) async throws": "apiClient.request(",
    "func setHoldings(_ items: [HoldingUpdateItem], in portfolioId: String) async throws -> Portfolio":
        "apiClient.request(",
    "func createPortfolio(named name: String) async throws -> Portfolio": "apiClient.request(",
    "func renamePortfolio(id: String, to newName: String) async throws -> Portfolio": "apiClient.request(",
    "func deletePortfolio(id: String) async throws": "apiClient.request(",
    "func reorderPortfolios(_ newOrder: [Portfolio]) async throws": "apiClient.request(",
    "func setActivePortfolio(_ id: String) async": "apiClient.request(",
}


# ── Scanning helpers (copied from the account-snapshot guard file; no shared conftest) ──

def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails, keeping line structure.

    `(?<![:/])` keeps the `//` of a `https://` literal. The fix's own comments name
    `hasLiveData`, `prepareSnapshot`, `snapshotSeed = nil` and every fence, so an un-stripped
    scan would pass on prose.
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
    return _block_at(src, src.find(header) + len(header))


def _idx(src: str, token: str, start: int = 0) -> int:
    at = src.find(token, start)
    assert at != -1, f"{token!r} not found"
    return at


def _widx(src: str, token: str) -> int:
    """`_idx` for a token that starts with an identifier: `loadTask?.cancel()` must not match
    inside `watchlistReloadTask?.cancel()`."""
    m = re.search(r"(?<![\w.])" + re.escape(token), src)
    assert m, f"{token!r} not found"
    return m.start()


def _in_order(src: str, *tokens: str) -> None:
    positions = [_idx(src, t) for t in tokens]
    assert positions == sorted(positions), f"out of order: {list(zip(tokens, positions))}"


def _opening_guard(block: str) -> tuple[str, int]:
    """(condition list, end offset) of the `guard … else { … return … }` that OPENS `block`."""
    m = re.match(r"\{\s*guard\s+(.*?)\s+else\s*\{", block, re.S)
    assert m, "the block no longer OPENS with its guard — a statement now runs before the precondition"
    else_block = _block_at(block, m.end() - 1)
    assert re.search(r"\breturn\b", else_block), "the opening guard's else no longer returns"
    return m.group(1), m.end() - 1 + len(else_block)


def _first_guard(block: str) -> str:
    """The condition list of the guard that OPENS `block`, or fail."""
    return _opening_guard(block)[0]


def _after_first_guard(block: str) -> str:
    return block[_opening_guard(block)[1]:]


def _catch_blocks(src: str) -> list[str]:
    return [_block_at(src, m.start()) for m in re.finditer(r"\bcatch\b[^{]*\{", src)]


def _vm(s, header):
    return _block(s["vm"], header)


# ── PortfolioStore ───────────────────────────────────────────────────────────────

def _check_purge_requires_live(s):
    purge = _block(s["pstore"], _PURGE)
    assert _first_guard(purge).strip() == "hasLiveData", (
        "purgeTickers no longer OPENS with `guard hasLiveData else` — a whole-list PUT can be "
        "built from membership the server never sent this session (data loss)"
    )
    assert "guard !allowed.isEmpty else" in purge, "the empty allow-set refusal is gone"
    assert "attempted = true" in purge and "return attempted" in purge, (
        "purgeTickers no longer reports whether it wrote (the save/refetch decision needs it)"
    )


def _check_live_signal_after_publish(s):
    src = s["pstore"]
    assert src.count("hasLiveData = true") == 1, (
        f"`hasLiveData = true` is written {src.count('hasLiveData = true')} times — exactly once, "
        "after a live answer is published"
    )
    load = _block(src, _STORE_LOAD)
    defer = _block_at(load, _idx(load, "defer"))
    assert "hasLiveData" not in defer, "the live flag is set in the defer — a FAILED load would claim live data"
    do_part = load[_idx(load, "do {"): _idx(load, "} catch {")]
    assert ".getPortfolios" in do_part and "requestReturningBody(" in do_part, (
        "GET /portfolios no longer hands back its bytes (the snapshot's portfolios part)"
    )
    _in_order(do_part, "guard epoch == identityEpoch else", "self.portfolios = loaded",
              "lastLiveBody = body", "hasLiveData = true", "return true")
    catch = load[_idx(load, "} catch {"):]
    _in_order(catch, "guard epoch == identityEpoch else { return false }", "if appError.isCancellation",
              "if case .signInRequired = appError", "loadErrorMessage = appError.message")
    refusal = _block_at(catch, _idx(catch, "if case .signInRequired = appError"))
    assert "loadErrorMessage = nil" in refusal and "appError.message" not in refusal, (
        "a typed sign-in refusal is shown as a load failure — a Retry the client refuses"
    )
    reset = _block(src, "func reset()")
    _in_order(reset, "identityEpoch &+= 1", "hasLiveData = false")
    for token in ("hasLiveData = false", "loadErrorMessage = nil", "lastLiveBody = nil"):
        assert token in reset, f"reset() no longer clears `{token.split(' =')[0]}` — the next session inherits it"
    decl = re.search(r"@Published private\(set\) var loadErrorMessage: String\?", src)
    assert decl, "loadErrorMessage is not a published, read-only property"


def _check_load_returns_outcome(s):
    src = s["pstore"]
    m = re.search(r"@discardableResult\s*func loadPortfolios\(\) async -> Bool \{", src)
    assert m, "loadPortfolios() no longer returns whether a live answer was published"
    body = _block_at(src, m.start())
    assert "return await running.value" in body, "a joiner no longer receives the joined load's outcome"
    assert "if !Task.isCancelled { self.loadTask = nil }" in body, (
        "a reset()-cancelled load that finishes late can unregister a newer one"
    )


def _check_confirmed_writes_bump(s):
    src = s["pstore"]
    assert re.search(r"@Published private\(set\) var confirmedMutationCount: Int = 0", src), (
        "confirmedMutationCount is not a published, read-only counter"
    )
    for header, request in _CONFIRMED_WRITES.items():
        body = _block(src, header)
        assert "confirmedMutationCount &+= 1" in body, (
            f"`{header.split('(')[0]}` confirms a write without bumping confirmedMutationCount — "
            "the saved Holdings snapshot then outlives the edit"
        )
        assert _idx(body, request) < _idx(body, "confirmedMutationCount &+= 1"), (
            f"`{header.split('(')[0]}` bumps BEFORE the server confirmed"
        )
    switch = _block(src, "func setActivePortfolio(_ id: String) async")
    do_part = switch[_idx(switch, "do {"): _idx(switch, "} catch {")]
    _in_order(do_part, "guard epoch == identityEpoch else { return }", "confirmedMutationCount &+= 1")


# ── TrackingViewModel: the account-snapshot contract rows ───────────────────────────

_SEED_BANS = ("hasLoadedOnce =", "loadTask =", "trackedAssets =", "alerts =", "portfolioStore.",
              "Task {", "apiClient.", "startPriceRefreshTimer", "isInsightsEnabled =", "selectedTab =")


def _check_seed_precondition_first(s):
    seed = _vm(s, _SEED)
    cond = " ".join(_first_guard(seed).split())
    for token in ("snapshotSeed == nil", "!hasLiveHoldings", "trackedAssets.isEmpty",
                  "!assetsRequiresSignIn", "!assetsIsReconnecting",
                  "let snapshot = snapshotStore.snapshotForDisplay()",
                  "snapshot.payload.activePortfolioId == portfolioStore.activePortfolioId"):
        assert token in cond, f"the seed precondition lost `{token}`"
    rest = _after_first_guard(seed)
    for ban in _SEED_BANS:
        assert ban not in rest, (
            f"seedFromSnapshot does `{ban}` — the seed is DISPLAY state only (never the store, "
            "the live rows, the latch or a request)"
        )
    for token in ("snapshotSeed = snapshot.payload", "snapshotSeedSavedAt = snapshot.savedAt",
                  "seededEpoch = snapshotStore.epoch"):
        assert token in rest, f"seedFromSnapshot no longer records `{token.split(' =')[0]}`"


def _check_prepare_order(s):
    prep = _vm(s, _PREPARE)
    _in_order(prep, "expireSnapshotIfStale()", "await snapshotStore.prepare(apiClient: apiClient)",
              "dropSeedIfEpochMoved()", "seedFromSnapshot()")
    for token in ("loadData", "loadIfNeeded", "refresh(", "requestReturningBody", "apiClient.request",
                  "Task {"):
        assert token not in prep, f"prepareSnapshot reaches `{token}` — it is disk only, never a request"


def _check_one_save_never_in_a_catch(s):
    vm = s["vm"]
    assert vm.count("snapshotStore.save(") == 1, (
        f"TrackingViewModel has {vm.count('snapshotStore.save(')} `snapshotStore.save(` calls — "
        "exactly one, after a live load"
    )
    for block in _catch_blocks(vm):
        assert "snapshotStore.save(" not in block, "a snapshot is saved from a catch block"
    load = _vm(s, _PERFORM)
    assert "snapshotStore.save(" in load, "the save left performLoad"
    _in_order(load, "let snapshotEpoch = snapshotStore.epoch", "async let feedTask")
    save = load[_idx(load, "snapshotStore.save("):]
    assert re.match(r"snapshotStore\.save\(parts: parts, payload: payload, savedAt: capturedAt, "
                    r"epoch: snapshotEpoch\)", save), "the save no longer passes the PRE-request epoch"
    cond = re.search(r"if bothLive, !purged, generation == loadGeneration,\s*let feedBody = "
                     r"capturedFeedBody, let portfoliosBody = capturedPortfoliosBody \{", load)
    assert cond, "the save is no longer conditioned on both live halves, no purge and this identity"
    assert _idx(load, "snapshotStore.save(") > cond.start()
    assert "let bothLive = feedSucceeded && portfoliosSucceeded && generation == loadGeneration" in load, (
        "`bothLive` no longer requires BOTH halves of this load, for this identity"
    )
    for fn in (_FEED, "func startPriceRefreshTimer()"):
        assert "snapshotStore" not in _vm(s, fn), f"`{fn}` reaches the snapshot store — the poll never saves"


def _check_save_inputs_from_this_load(s):
    load = _vm(s, _PERFORM)
    phase1 = _idx(load, "await (feedTask, portfoliosTask)")
    purge = _idx(load, "await portfolioStore.purgeTickers(")
    for token in ("let capturedFeedBody = lastLiveFeedBody", "let capturedPortfoliosBody = portfolioStore.lastLiveBody",
                  "let capturedActiveId = portfolioStore.activePortfolioId", "let capturedAssets = trackedAssets",
                  "let capturedPortfolios = portfolioStore.portfolios", "let capturedAt = Date()"):
        at = _idx(load, token)
        assert phase1 < at < purge, f"`{token}` is not captured right after phase 1 (before any other await)"
    tail = load[_idx(load, "let early = await earlyInsights"):]
    for token in ("lastLiveFeedBody", ".lastLiveBody", "trackedAssets", "portfolioStore.portfolios",
                  "portfolioStore.activePortfolioId"):
        assert token not in tail, (
            f"performLoad reads `{token}` after the insights await — the snapshot can pair bodies "
            "from different loads"
        )
    assert "TrackingSnapshot.activePartBody(capturedActiveId)" in tail
    assert re.search(r"if case \.answered\(let portfolioId, let score, let body\)\? = settled, "
                     r"portfolioId == capturedActiveId \{", tail), (
        "the insights part is no longer limited to an answer about the captured active group"
    )


def _check_shared_once_in_init(s):
    vm = s["vm"]
    assert vm.count("TrackingSnapshotStore.shared") == 1, (
        f"TrackingViewModel names `TrackingSnapshotStore.shared` {vm.count('TrackingSnapshotStore.shared')} "
        "times — exactly once, assigned in init"
    )
    init = _vm(s, _VM_INIT)
    assert "self.snapshotStore = TrackingSnapshotStore.shared" in init, "the store is not assigned inside init"
    for token in ("prepareSnapshot", "seedFromSnapshot", ".prepare(", "loadData", "loadIfNeeded"):
        assert token not in init, f"init reaches `{token}` — nothing is read or loaded at launch"


def _check_task_prepare_order(s):
    root = _block(s["view"], _LIVE_ROOT)
    task = _block(root, ".task(id: isActiveTab)")
    hidden = _block_at(task, _idx(task, "guard isActiveTab else"))
    _in_order(hidden, "viewModel.stopPriceRefreshTimer()", "await viewModel.prepareSnapshot()", "return")
    active = task[_idx(task, "guard isActiveTab else") + len(hidden):]
    _in_order(active, "await viewModel.prepareSnapshot()", "guard !Task.isCancelled else { return }",
              "await viewModel.loadIfNeeded()")
    assert "onChange(of: isActiveTab)" not in root, (
        "a second activation hook seeds outside prepareSnapshot — one seeding rule"
    )


# Everything identity-scoped that `handleIdentityChange` must clear ABOVE its active-tab gate.
# Pinned token by token: the old first-match regex in test_ios_tabs_reload_on_identity_change.py
# now finds `loadTask = nil` (no account data), so it stays green while the whale, error and
# marker clears move under the gate — and B then sees A's followed investors, A's Recent Trades
# and A's Follow states until the reload lands (auth.md §7).
_IDENTITY_CLEARS = (
    "loadGeneration &+= 1", "loadTask?.cancel()", "loadTask = nil", "isLoading = false",
    "trackedAssets = []", "alerts = []", "hasLiveFeed = false", "lastLiveFeedBody = nil",
    "snapshotSeed = nil", "hasAttemptedLoad = false", "insightsRequestToken &+= 1",
    "portfolioInsights = nil", "portfolioInsightsLoadFailed = false", "portfolioInsightsPortfolioId = nil",
    "portfolioInsightsPhase = .idle", "assetsErrorMessage = nil",
    "assetsRequiresSignIn = false", "assetsIsReconnecting = false",
    "whalesRequiresSignIn = false", "whalesIsReconnecting = false",
    "trackedWhales = []", "allWhaleTrades = []", "groupedWhaleTrades = []", "whaleActivities = []",
    "allPopularWhales = []", "heroWhales = []", "popularWhales = []",
    "hasLoadedOnce = false", "watchlistReloadTask?.cancel()", "watchlistMarkerQueue.removeAll()",
    "recentlyAddedTickers.removeAll()",
)


def _check_identity_clears_then_reseeds(s):
    body = _vm(s, _IDENTITY)
    gate = _idx(body, "guard isActiveTab else { return }")
    reseed = _widx(body, "await prepareSnapshot()")
    for token in _IDENTITY_CLEARS:
        at = _widx(body, token)
        assert at < reseed, f"`{token}` is cleared after the re-seed (or not before the gate)"
    assert reseed < gate, (
        "the identity re-seed sits below the active-tab gate — a hidden tab keeps nothing for the "
        "new identity and the gate order drifted"
    )
    after = body[gate:]
    assert "if generation == loadGeneration { hasLoadedOnce = true }" in after, (
        "the identity reload latches on something other than ITS load completing"
    )


def _check_no_store_write_from_snapshot(s):
    vm = s["vm"]
    assert not re.search(r"portfolioStore\.(portfolios|activePortfolioId)\s*=(?!=)", vm), (
        "TrackingViewModel assigns the store's portfolios or active id directly"
    )
    snapshot_words = ("snapshotSeed", "presentedSnapshot", "seed.", "snapshot.payload")
    mutators = ("portfolioStore.setActivePortfolio(", "setTickers(", "purgeTickers(", "addTicker(",
                "removeTicker(", "setHoldings(", "createPortfolio(", "renamePortfolio(")
    for n, line in enumerate(vm.splitlines(), 1):
        if any(w in line for w in snapshot_words) and any(m in line for m in mutators):
            raise AssertionError(f"TrackingViewModel.swift:{n} feeds snapshot data to a PortfolioStore write")
    for token in ("TrackingSnapshot", "snapshotSeed", "AccountSnapshot"):
        assert token not in s["pstore"], f"PortfolioStore names `{token}` — the snapshot never reaches the store"


# ── TrackingViewModel: the Tracking rows ─────────────────────────────────────────────

def _check_refusal_drops_seed(s):
    feed = _vm(s, _FEED)
    refusal = _block_at(feed, _idx(feed, "if case .signInRequired = appError"))
    for token in ("self.snapshotSeed = nil", "self.hasLiveFeed = false", "self.lastLiveFeedBody = nil"):
        assert token in refusal, f"a sign-in refusal keeps `{token.split(' =')[0].replace('self.', '')}`"
    for token in ("seedFromSnapshot", "prepareSnapshot"):
        assert token not in refusal, "the refusal branch re-seeds the snapshot it must drop"


def _check_feed_fenced(s):
    feed = _vm(s, _FEED)
    _in_order(feed, "let generation = loadGeneration", "requestReturningBody(")
    assert feed.count("guard generation == loadGeneration else { return false }") == 2, (
        "the feed's success AND failure arms must both refuse an answer from another identity"
    )
    _in_order(feed, "requestReturningBody(", "guard generation == loadGeneration else { return false }",
              "self.trackedAssets = ", "self.lastLiveFeedBody = body", "self.hasLiveFeed = true")
    catch = feed[_idx(feed, "} catch {"):]
    _in_order(catch, "guard generation == loadGeneration else { return false }",
              "if appError.isCancellation { return false }", "if case .signInRequired = appError")


def _check_purge_needs_both_live(s):
    load = _vm(s, _PERFORM)
    purge_if = re.search(r"if bothLive \{\s*var allowed = Set\(trackedAssets\.map\(\\\.ticker\)\)", load)
    assert purge_if, "the purge is no longer conditioned on `bothLive`"
    block = _block_at(load, purge_if.start())
    assert "purged = await portfolioStore.purgeTickers(notIn: allowed)" in block
    assert load.count("purgeTickers(") == 1, "a second, unconditioned purge call appeared"


def _check_live_replaces_seed(s):
    load = _vm(s, _PERFORM)
    _in_order(load, "guard generation == loadGeneration else { return }",
              "if bothLive { replaceSnapshotWithLiveHoldings() }", "hasAttemptedLoad = true")
    assert "snapshotSeed = nil" in _vm(s, "private func replaceSnapshotWithLiveHoldings()"), (
        "live holdings never replace the labelled snapshot"
    )


def _check_latch_on_completion(s):
    fn = _vm(s, "func loadIfNeeded() async")
    early = fn[_idx(fn, "guard !hasLoadedOnce else"):]
    early = early[: _idx(early, "return") + len("return")]
    _in_order(early, "startPriceRefreshTimer()", "retryFailedLoadIfNeeded()")
    after = fn[_idx(fn, "let generation = loadGeneration"):]
    between = after[_idx(after, "await loadData()"): _idx(after, "hasLoadedOnce = true")]
    assert "Task.isCancelled" not in between, (
        "the latch waits on the awaiting `.task` surviving again — a completed load is thrown "
        "away on a tab-away and the next activation re-runs five requests"
    )
    _in_order(after, "await loadData()", "guard generation == loadGeneration else { return }",
              "hasLoadedOnce = true", "guard !Task.isCancelled else { return }", "startPriceRefreshTimer()")


def _check_retry_on_activation_only(s):
    retry = _vm(s, "private func retryFailedLoadIfNeeded()")
    assert _first_guard(retry).strip() == "!hasLiveHoldings", (
        "the retry no longer OPENS with `guard !hasLiveHoldings` — one failed 30 s poll re-runs "
        "the whole five-request load on the next activation"
    )
    assert "guard !assetsRequiresSignIn, !assetsIsReconnecting, loadTask == nil else { return }" in retry, (
        "the retry fires while the session is unarmed (or over a load already running)"
    )
    for token in ("Task.sleep", "while ", "repeat "):
        assert token not in retry, "the failed-load retry became a loop"
    timer = _vm(s, "func startPriceRefreshTimer()")
    for token in ("retryFailedLoadIfNeeded", "loadData", "loadPortfolios"):
        assert token not in timer, f"the 30 s poll reaches `{token}` — a periodic retry loop"


def _check_insights_early_and_fenced(s):
    load = _vm(s, _PERFORM)
    body = "\n".join(l for l in load.splitlines() if "defer" not in l)
    _in_order(body, "async let earlyInsights", "await (feedTask, portfoliosTask)", "isLoading = false",
              "let early = await earlyInsights")
    assert "if purged || (settled == nil && earlyInsightsToken == insightsRequestToken) {" in load, (
        "a stale-hint answer is no longer refetched for the live group"
    )
    publish = _vm(s, "private func publishPortfolioInsights(_ fetch: InsightsFetch, token: Int) -> Bool")
    assert re.match(r"\{\s*guard token == insightsRequestToken else \{ return false \}", publish), (
        "publishPortfolioInsights no longer OPENS with its request-token fence"
    )
    assert publish.count("== portfolioStore.activePortfolioId else { return false }") == 2, (
        "an answer (or a failure) about another group can reach the card"
    )
    assert "guard portfolioStore.activePortfolioId == nil else { return false }" in publish


def _check_insights_tristate(s):
    vm = s["vm"]
    assert re.search(r"@Published private\(set\) var portfolioInsightsPhase: PortfolioInsightsPhase = \.idle", vm), (
        "the insights phase no longer STARTS idle — a never-opened hidden tab mounts a spinner"
    )
    gated = re.search(r"var portfolioInsightsIsGated: Bool \{ assetsRequiresSignIn \|\| assetsIsReconnecting \}", vm)
    assert gated, "the insights gate term no longer covers both account-gate flags"
    for header in ("var portfolioInsightsIsResolving: Bool", "var portfolioInsightsDidFail: Bool"):
        cond = _first_guard(_vm(s, header))
        assert "!portfolioInsightsIsGated" in cond, (
            f"`{header}` is not off under the account gate — a spinner or a Retry under Reconnecting"
        )
    resolving = _vm(s, "var portfolioInsightsIsResolving: Bool")
    for token in ("loadErrorMessage", "hasLiveData"):
        assert token not in resolving, (
            "unknown is derived from 'no live data and no error' again — it never ends signed out"
        )
    progress = _vm(s, "var portfolioInsightsShowsProgress: Bool")
    assert "portfolioInsightsPhase == .resolving || isLoading || portfolioStore.isLoading" in progress, (
        "the spinner no longer requires something actually on the wire"
    )
    answer = _vm(s, "private var presentedInsightsAnswer: (known: Bool, score: DiversificationScore?)")
    assert re.match(r"\{\s*if portfolioInsightsIsGated \{ return \(false, nil\) \}", answer), (
        "the presented answer no longer opens with the gate"
    )
    assert "if portfolioInsightsPhase == .known, sameGroup {" in answer, (
        "a live score is shown over a snapshot of another group"
    )
    content = _block(s["section"], "private var content: some View")
    _in_order(content, "if !isEnabled", "} else if isGated {", "} else if let score = score {",
              "} else if isResolving {", "} else if didFail {", "needsMoreHoldingsState", "emptyState")
    spinner = _block(s["section"], "private var resolvingState: some View")
    assert "if showsProgress {" in spinner, "the ProgressView renders without anything in flight"
    assert spinner.count("ProgressView()") == 1 and _idx(spinner, "if showsProgress {") < _idx(spinner, "ProgressView()")
    screen = _block(_block(s["view"], _ASSETS), "private var insightsSection: some View")
    for token in ("isResolving: viewModel.portfolioInsightsIsResolving",
                  "showsProgress: viewModel.portfolioInsightsShowsProgress",
                  "didFail: viewModel.portfolioInsightsDidFail", "isGated: viewModel.portfolioInsightsIsGated",
                  "onRetry: { viewModel.retryPortfolioInsights() }"):
        assert token in screen, f"the screen no longer passes `{token.split(':')[0]}`"


_PHASE_DECL = "@Published private(set) var portfolioInsightsPhase: PortfolioInsightsPhase = .idle {"
_KEPT = "private var keptSeedInsightsForLiveGroup: (portfolioId: String, score: DiversificationScore?)?"


def _check_insights_carry_across_swap(s):
    """Snapshot → live: the rows go live a moment before the score. The card keeps the snapshot's
    answer for the SAME group while that group's request is on the wire, instead of collapsing to
    the spinner and re-expanding (two height changes below the rows)."""
    vm = s["vm"]
    didset = _block_at(vm, _idx(vm, _PHASE_DECL))
    assert "if portfolioInsightsPhase != .resolving { insightsCarriedFromSnapshot = nil }" in didset, (
        "the carried snapshot score outlives the request it stands in for"
    )
    answer = _vm(s, "private var presentedInsightsAnswer: (known: Bool, score: DiversificationScore?)")
    live = answer[_idx(answer, "let liveGroup = portfolioStore.activePortfolioId"):]
    at = _idx(live, "if portfolioInsightsPhase == .resolving,")
    cond = " ".join(live[at: live.index("{", at)].split())
    assert "let kept = keptSeedInsightsForLiveGroup ?? insightsCarriedFromSnapshot" in cond, (
        "the snapshot → live swap drops the card to a spinner — no carried answer"
    )
    assert "kept.portfolioId == liveGroup" in cond, "another group's kept score is carried onto this group's card"
    assert "return (true, kept.score)" in _block_at(live, at)
    replace = _vm(s, "private func replaceSnapshotWithLiveHoldings()")
    _in_order(replace, "if portfolioInsightsPhase == .resolving, let kept = keptSeedInsightsForLiveGroup {",
              "insightsCarriedFromSnapshot = kept", "snapshotSeed = nil")
    kept = " ".join(_first_guard(_vm(s, _KEPT)).split())
    for token in ("loadTask != nil", "seed.insightsKnown", "groupId == portfolioStore.activePortfolioId",
                  "AccountSnapshotPolicy.isDisplayable(savedAt: savedAt, now: Date())"):
        assert token in kept, f"the kept seed answer lost `{token}`"
    # Never in the presented-snapshot branch: there the seed's own answer already stands.
    seed_branch = answer[: _idx(answer, "let liveGroup = portfolioStore.activePortfolioId")]
    assert "insightsCarriedFromSnapshot" not in seed_branch
    # Retired early: by a confirmed edit (the score was for the pre-edit group) and by a request
    # about another group (a later switch back must not resurrect the snapshot's score).
    assert "insightsCarriedFromSnapshot = nil" in _vm(s, "private func discardSnapshotAfterConfirmedEdit()"), (
        "a confirmed edit keeps the pre-edit snapshot score on the card"
    )
    mark = _vm(s, "private func markPortfolioInsightsResolving(for portfolioId: String?)")
    assert re.search(r"if let carried = insightsCarriedFromSnapshot, carried\.portfolioId != portfolioId \{"
                     r"\s*insightsCarriedFromSnapshot = nil\s*\}", mark), (
        "a request about another group keeps the carried snapshot score alive"
    )


def _check_auto_open_needs_known_state(s):
    assets = _block(s["view"], _ASSETS)
    change = _block_at(assets, _idx(assets, ".onChange(of: viewModel.isInsightsEnabled)"))
    assert "if isOn && viewModel.shouldAutoOpenPortfolioConfig {" in change, (
        "turning Insights on opens the config sheet over an unknown or failed answer again"
    )
    auto = _vm(s, "var shouldAutoOpenPortfolioConfig: Bool")
    for token in ("presentedInsightsAnswer.known", "displayedDiversificationScore == nil", "canEditPortfolio"):
        assert token in auto, f"shouldAutoOpenPortfolioConfig lost `{token}`"


_STILL_LOADING_GATES = {
    "func openPortfolioConfigSheet()": "guard canEditPortfolio else",
    "func openNewPortfolioSheet()": "guard canEditPortfolio else",
    "func openEditPortfolioSheet()": "guard canEditPortfolio else",
    "func openManageTickersSheet()": "guard canEditPortfolio else",
    "func removeAsset(_ asset: TrackedAsset)": "guard canEditHoldings else",
    "func removeAssetFromAll(_ asset: TrackedAsset)": "guard canEditHoldings else",
}


def _check_edit_entry_points_gated(s):
    for header, gate in _STILL_LOADING_GATES.items():
        body = _vm(s, header)
        assert re.match(r"\{\s*" + re.escape(gate), body), f"`{header}` no longer OPENS with `{gate}`"
        refusal = _block_at(body, _idx(body, gate))
        assert "reportPortfolioStillLoading(" in refusal, f"`{header}` refuses silently (auth.md §6)"
    save = _vm(s, "func savePortfolioHoldings(_ items: [HoldingUpdateItem]) async throws")
    assert re.match(r"\{\s*guard canEditPortfolio else \{\s*throw APIError\.unknown", save), (
        "saving holdings no longer requires a live portfolio list"
    )
    report = _vm(s, "private func reportPortfolioStillLoading(action: String)")
    assert "AppActions.shared.reportMutationFailure(" in report
    assert re.search(r"var canEditPortfolio: Bool \{ portfolioStore\.hasLiveData \}", s["vm"]), (
        "canEditPortfolio no longer means a LIVE list (hasLoadedOnce only means attempted)"
    )
    assert re.search(r"var canEditHoldings: Bool \{ portfolioStore\.hasLiveData && !isShowingSnapshot \}", s["vm"]), (
        "row edits are offered on snapshot rows"
    )
    assets = _block(s["view"], _ASSETS)
    assert "allowsRemoval: viewModel.canEditHoldings" in _block(assets, "private var holdingsList: some View"), (
        "the screen offers swipe-remove regardless of whether the rows are live"
    )
    assert "configureEnabled: viewModel.canEditPortfolio" in _block(assets, "private var insightsSection: some View")
    swipe = _block_at(s["list"], _idx(s["list"], ".swipeActions(edge: .trailing, allowsFullSwipe: true)"))
    assert re.match(r"\{\s*if allowsRemoval \{\s*Button\(role: \.destructive\)", swipe), (
        "the swipe-remove action is offered on rows the store does not hold"
    )
    panel = _block(s["header"], "private var portfolioPanel: some View")
    assert panel.count("isDisabled: !viewModel.canEditPortfolio") == 2, (
        "New / Edit Portfolios are enabled before the live list has arrived"
    )
    assert s["section"].count(".disabled(!configureEnabled)") == 3, (
        "a holdings editor in the Insights card is enabled before the live list has arrived"
    )


def _check_confirmed_edit_purges(s):
    init = _vm(s, _VM_INIT)
    m = re.search(r"self\.portfolioStore\.\$confirmedMutationCount(.*?)\.store\(in: &cancellables\)", init, re.S)
    assert m, "the ViewModel no longer subscribes to confirmed portfolio writes"
    chain = m.group(1)
    assert "self?.discardSnapshotAfterConfirmedEdit()" in chain
    assert "receive(on:" not in chain, "the purge is deferred — a save can land between the edit and it"
    discard = _vm(s, "private func discardSnapshotAfterConfirmedEdit()")
    assert "snapshotStore.purgeCache()" in discard, (
        "a confirmed edit no longer drops the saved snapshot — the next open paints another group"
    )
    # The seed can be ON SCREEN when the write lands (feed failed, /portfolios live, so the
    # edit entry points are open): a removed ticker left beside "Updated <time>" reads as a
    # failed removal, and the coverage note counts the pre-edit group.
    assert re.search(r"(?<![!=])\bsnapshotSeed = nil", discard), (
        "a confirmed edit leaves the pre-edit snapshot on screen"
    )


def _check_removal_elsewhere_purges_file(s):
    body = _vm(s, "func handleWatchlistChange(_ change: WatchlistChange)")
    assert re.search(r"if !change\.added \{\s*purgeSnapshotAfterRemovalElsewhere\(\)\s*\}", body), (
        "a removal confirmed elsewhere patches the seed on screen but leaves the saved file — the "
        "next cold launch paints the removed ticker back"
    )
    # Even in a session where Tracking never loaded (that is the reviewer's scenario), and never
    # for this tab's own posts (those purge through confirmedMutationCount).
    _in_order(body, "guard change.source != .tracking else { return }",
              "snapshotSeed = Self.removingRow(of: change.ticker, from: seed)",
              "purgeSnapshotAfterRemovalElsewhere()", "guard hasLoadedOnce || loadTask != nil else { return }")
    helper = _vm(s, "private func purgeSnapshotAfterRemovalElsewhere()")
    assert "if seedWasCurrent { seededEpoch = snapshotStore.epoch }" in helper, (
        "the patched seed is re-stamped even when it came from another binding"
    )
    _in_order(helper, "let seedWasCurrent: Bool = snapshotSeed != nil && seededEpoch == snapshotStore.epoch",
              "snapshotStore.purgeCache()", "if seedWasCurrent { seededEpoch = snapshotStore.epoch }")


def _check_render_gate(s):
    assets = _block(s["view"], _ASSETS)
    assert "@Environment(\\.isActiveTab) private var isActiveTab" in assets, (
        "AssetsTabContent no longer reads isActiveTab — the hidden tab renders snapshot rows"
    )
    body = _block(assets, "var body: some View")
    gate = re.search(r"\} else if isActiveTab \|\| !viewModel\.isShowingSnapshot \{\s*holdingsList\s*\} else \{",
                     body)
    assert gate, "the Holdings rows render in a hidden tab while they are a snapshot"
    tail = _block_at(body, gate.end() - 1)
    assert "holdingsList" not in tail and "AssetsListSection" not in tail, (
        "the hidden-snapshot branch draws the rows — the render gate does not cover it"
    )
    assert body.count("holdingsList") == 1 and "AssetsListSection(" not in body, (
        "the rows are reachable from a branch the render gate does not cover"
    )
    # The snapshot's kept score is snapshot content too: drawn only while the tab is shown.
    assert re.search(r"if isActiveTab \|\| !viewModel\.isShowingSnapshot \{\s*insightsSection\s*\}", body), (
        "the Insights card draws the snapshot's score in a hidden tab"
    )
    assert body.count("insightsSection") == 1 and "PortfolioInsightsSection(" not in body
    # The tab bar switches tabs inside `withAnimation`; without this fence the hidden branch's
    # static skeleton cross-fades into the rows, so the first frame after a tap shows it through.
    assert ".transaction(value: isActiveTab) { $0.animation = nil }" in body, (
        "the render gate's activation swap is animated — the skeleton bleeds through the rows"
    )


def _check_never_loaded_is_not_empty(s):
    body = _block(_block(s["view"], _ASSETS), "var body: some View")
    never = _idx(body, "} else if viewModel.filteredAssets.isEmpty && !viewModel.hasAttemptedLoad {")
    branch = _block_at(body, never)
    assert "TrackedAssetsSkeleton(isAnimated: false)" in branch, "the never-loaded branch is not the static skeleton"
    assert _idx(body, "} else if viewModel.filteredAssets.isEmpty && viewModel.isLoading {") < never
    assert never < _idx(body, "AssetsPlaceholderCard("), "the placeholder ('No tickers yet') wins first"
    skel = _block(s["skeleton"], "var body: some View")
    assert skel.count(".shimmer()") == 1, "scan drifted — the skeleton's shimmer moved"
    anim = _block_at(skel, _idx(skel, "if isAnimated {"))
    assert ".shimmer()" in anim, "the skeleton shimmers even when asked not to (a hidden-tab animation)"


def _check_portfolios_failure_not_empty(s):
    body = _block(_block(s["view"], _ASSETS), "var body: some View")
    card = body[_idx(body, "AssetsPlaceholderCard("):]
    assert card.startswith("AssetsPlaceholderCard(\n") and "errorMessage: viewModel.holdingsErrorMessage" in card[:200], (
        "the placeholder speaks only for the feed — a failed /portfolios reads 'No tickers yet'"
    )
    msg = _vm(s, "var holdingsErrorMessage: String?")
    for token in ("assetsErrorMessage", "portfolioStore.loadErrorMessage"):
        assert token in msg, f"holdingsErrorMessage no longer names `{token}`"


def _check_presented_all_or_nothing(s):
    pres = " ".join(_first_guard(_vm(s, "var presentedSnapshot: TrackingSnapshot?")).split())
    for token in ("!hasLiveHoldings", "!assetsRequiresSignIn", "!assetsIsReconnecting",
                  "seed.activePortfolioId == portfolioStore.activePortfolioId",
                  "AccountSnapshotPolicy.isDisplayable(savedAt: savedAt, now: Date())"):
        assert token in pres, f"presentedSnapshot lost `{token}`"
    assert re.search(r"var hasLiveHoldings: Bool \{ hasLiveFeed && portfolioStore\.hasLiveData \}", s["vm"]), (
        "rows switch to live on ONE half — live prices beside snapshot membership"
    )
    rows = _vm(s, "var filteredAssets: [TrackedAsset]")
    assert "let seed: TrackingSnapshot? = presentedSnapshot" in rows, "the rows no longer read the presented snapshot"
    assert "let source: [TrackedAsset] = seed?.assets ?? trackedAssets" in rows, (
        "the rows no longer come from the snapshot while it is presented — not found"
    )
    alerts = _vm(s, "private var activeTickerSet: Set<String>")
    assert "portfolioStore.activePortfolio" in alerts and "presented" not in alerts, (
        "the alerts are scoped by the snapshot's group — they are live-only"
    )


def _check_snapshot_labelled(s):
    header = _block(s["header"], "struct PortfolioHeaderBar: View")
    row = _block(header, "private var rowLayout: some View")
    assert re.search(r"if let label = viewModel\.snapshotUpdatedLabel \{\s*snapshotStatus\(label\)\s*\}", row), (
        "the header no longer says 'Updated <time>' over snapshot prices"
    )
    # Dynamic Type: at accessibility sizes the higher-priority picker takes the row and squeezes
    # the label (lineLimit 1, minimumScaleFactor 0.75) to "U…" — the only sign the rows are old.
    assert "@Environment(\\.dynamicTypeSize) private var dynamicTypeSize" in header
    body = _block(header, "var body: some View")
    assert re.search(r"if dynamicTypeSize\.isAccessibilitySize, let label = viewModel\.snapshotUpdatedLabel \{"
                     r"\s*stackedLayout\(label\)\s*\} else \{\s*rowLayout\s*\}", body), (
        "at accessibility sizes 'Updated <time>' shares the picker's row and is squeezed to an ellipsis"
    )
    stacked = _block(header, "private func stackedLayout(_ label: String) -> some View")
    inner = _block_at(stacked, _idx(stacked, "HStack("))
    assert "portfolioPicker" in inner and "optionsButton" in inner, "scan drifted — the stacked row moved"
    assert "snapshotStatus" not in inner and "snapshotStatus(label)" in stacked, (
        "the stacked layout does not give 'Updated <time>' a line of its own"
    )
    picker = _block(header, "private var portfolioPicker: some View")
    assert 'viewModel.presentedPortfolio?.name ?? "Holdings"' in picker, (
        "the header names the live group over the snapshot's rows — not found"
    )
    status = _block(header, "private func snapshotStatus(_ label: String) -> some View")
    retry = _block_at(status, _idx(status, "if viewModel.snapshotRefreshFailed {"))
    assert "Task { await viewModel.refresh() }" in retry and "Couldn't refresh" in retry, (
        "the header no longer says 'Couldn't refresh' with a Retry once the refresh failed"
    )
    label = _block_at(retry, _idx(retry, "} label: {"))
    assert label.rstrip("}").rstrip().endswith(".contentShape(Rectangle())"), (
        "the header Retry's padding is dead pixels — `.contentShape(Rectangle())` must close its label"
    )
    vm_label = _vm(s, "var snapshotUpdatedLabel: String?")
    assert "AccountSnapshotPolicy.updatedLabel(savedAt: savedAt)" in vm_label, (
        "the 'Updated <time>' label is not built by the one wording source — not found"
    )
    assert "HomeDashboardViewModel" not in s["vm"], "a second wording source (the policy is the one)"


def _check_add_recovery_needs_live_load(s):
    add = _vm(s, "func addTickerFromSearch(_ result: StockSearchResult)")
    # After a FAILED /portfolios the active id still holds the device's remembered hint, so a
    # recovery keyed on `activePortfolioId == nil` alone was skipped — and `addTicker` then
    # returned without a word for a group the store does not hold.
    assert "if !portfolioStore.hasLiveData || portfolioStore.activePortfolioId == nil {" in add, (
        "the star's recovery is keyed on a nil active id only — the remembered hint skips it"
    )
    assert "let loaded = await portfolioStore.loadPortfolios()" in add
    create = _idx(add, 'createPortfolio(named: "Holdings")')
    m = re.search(r"if loaded && portfolioStore\.portfolios\.isEmpty \{", add)
    assert m and m.start() < create < m.start() + len(_block_at(add, m.start())), (
        "a default group is created after a FAILED load — a duplicate beside the real one"
    )
    guard = "guard portfolioStore.hasLiveData, let portfolioId = portfolioStore.activePortfolioId else"
    assert guard in add, "the star writes to a group no live list holds (addTicker no-ops silently)"
    abort_at = _idx(add, guard)
    abort = _block_at(add, abort_at)
    assert "AppActions.shared.reportMutationFailure(" in abort, "the star aborts silently"
    assert abort_at < _idx(add, "recentlyAddedTickers[portfolioId, default: []].insert(symbol)") < _idx(
        add, ".addToWatchlist("), "the star fills (or the POST leaves) before the live-list check"


def _check_watchlist_change_patches_seed(s):
    body = _vm(s, "func handleWatchlistChange(_ change: WatchlistChange)")
    _in_order(body, "snapshotSeed = Self.removingRow(of: change.ticker, from: seed)",
              "guard hasLoadedOnce || loadTask != nil else { return }")


def _check_stale_seed_dropped(s):
    drop = _vm(s, "private func dropSeedIfEpochMoved()")
    assert "seeded != snapshotStore.epoch" in _first_guard(drop) and "snapshotSeed = nil" in drop, (
        "a seed from a previous store binding survives a re-bind with no identity change"
    )
    expire = _vm(s, "func expireSnapshotIfStale(now: Date = Date())")
    assert "!AccountSnapshotPolicy.isDisplayable(savedAt: savedAt, now: now)" in _first_guard(expire), (
        "an on-screen snapshot never expires"
    )
    assert "snapshotSeed = nil" in expire
    root = _block(s["view"], _LIVE_ROOT)
    m = re.search(r"\.onReceive\(\s*NotificationCenter\.default\.publisher\(for: UIApplication\.didBecomeActiveNotification\)"
                  r"\s*\) \{ _ in\s*viewModel\.expireSnapshotIfStale\(\)", root)
    assert m, "an on-screen snapshot outlives its 96 h window across a background stay"
    didset = _block_at(s["vm"], _idx(s["vm"], "@Published private(set) var snapshotSeed: TrackingSnapshot? {"))
    for token in ("snapshotSeedSavedAt = nil", "seededEpoch = nil"):
        assert token in didset, f"clearing the seed keeps `{token.split(' =')[0]}`"


GUARDS: dict[str, Callable[[dict[str, str]], None]] = {
    "purge_requires_live": _check_purge_requires_live,
    "live_signal_after_publish": _check_live_signal_after_publish,
    "load_returns_outcome": _check_load_returns_outcome,
    "confirmed_writes_bump": _check_confirmed_writes_bump,
    "seed_precondition_first": _check_seed_precondition_first,
    "prepare_order": _check_prepare_order,
    "one_save_never_in_a_catch": _check_one_save_never_in_a_catch,
    "save_inputs_from_this_load": _check_save_inputs_from_this_load,
    "shared_once_in_init": _check_shared_once_in_init,
    "task_prepare_order": _check_task_prepare_order,
    "identity_clears_then_reseeds": _check_identity_clears_then_reseeds,
    "no_store_write_from_snapshot": _check_no_store_write_from_snapshot,
    "refusal_drops_seed": _check_refusal_drops_seed,
    "feed_fenced": _check_feed_fenced,
    "purge_needs_both_live": _check_purge_needs_both_live,
    "live_replaces_seed": _check_live_replaces_seed,
    "latch_on_completion": _check_latch_on_completion,
    "retry_on_activation_only": _check_retry_on_activation_only,
    "insights_early_and_fenced": _check_insights_early_and_fenced,
    "insights_tristate": _check_insights_tristate,
    "insights_carry_across_swap": _check_insights_carry_across_swap,
    "auto_open_needs_known_state": _check_auto_open_needs_known_state,
    "edit_entry_points_gated": _check_edit_entry_points_gated,
    "confirmed_edit_purges": _check_confirmed_edit_purges,
    "removal_elsewhere_purges_file": _check_removal_elsewhere_purges_file,
    "render_gate": _check_render_gate,
    "never_loaded_is_not_empty": _check_never_loaded_is_not_empty,
    "portfolios_failure_not_empty": _check_portfolios_failure_not_empty,
    "presented_all_or_nothing": _check_presented_all_or_nothing,
    "snapshot_labelled": _check_snapshot_labelled,
    "add_recovery_needs_live_load": _check_add_recovery_needs_live_load,
    "watchlist_change_patches_seed": _check_watchlist_change_patches_seed,
    "stale_seed_dropped": _check_stale_seed_dropped,
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


def _rep(old: str, new: str) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        assert src.count(old) >= 1, f"mutation anchor {old!r} not in source"
        return src.replace(old, new, 1)
    return mutate


def _sub(pattern: str, repl: str, flags: int = re.S) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        out, n = re.subn(pattern, repl, src, count=1, flags=flags)
        assert n == 1, f"mutation pattern {pattern!r} did not match"
        return out
    return mutate


def _within(header: str, inner: Callable[[str], str]) -> Callable[[str], str]:
    """Apply `inner` to one declaration's block only."""
    def mutate(src: str) -> str:
        block = _block(src, header)
        return src.replace(block, inner(block), 1)
    return mutate


def _move_line(token: str, after: str) -> Callable[[str], str]:
    """Cut the line holding `token` and paste it after the line holding `after`."""
    def mutate(src: str) -> str:
        lines = src.split("\n")
        i = next(n for n, l in enumerate(lines) if token in l)
        moved = lines.pop(i)
        j = next(n for n, l in enumerate(lines) if after in l)
        lines.insert(j + 1, moved)
        return "\n".join(lines)
    return mutate


def _move_line_before(token: str, before: str) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        lines = src.split("\n")
        i = next(n for n, l in enumerate(lines) if token in l)
        moved = lines.pop(i)
        j = next(n for n, l in enumerate(lines) if before in l)
        lines.insert(j, moved)
        return "\n".join(lines)
    return mutate


_W = _within

MUTATIONS: list[tuple] = [
    # ── PortfolioStore ──
    ("purge-no-live-guard", "purge_requires_live", "pstore",
     _sub(r"guard hasLiveData else \{.*?return false\s*\}\s*", ""), "`guard hasLiveData else`"),
    ("purge-live-guard-late", "purge_requires_live", "pstore",
     _W(_PURGE, _sub(r"(guard hasLiveData else \{.*?return false\s*\}\s*)(guard !allowed\.isEmpty else \{.*?return false\s*\}\s*)",
                     r"\2\1")), "`guard hasLiveData else`"),
    ("purge-no-outcome", "purge_requires_live", "pstore", _rep("return attempted", "return false"), "reports whether"),
    ("live-flag-before-fence", "live_signal_after_publish", "pstore",
     _W(_STORE_LOAD, _move_line_before("hasLiveData = true", "guard epoch == identityEpoch else {")), "out of order"),
    ("live-flag-in-defer", "live_signal_after_publish", "pstore",
     _W(_STORE_LOAD, _sub(r"(if epoch == identityEpoch \{ hasLoadedOnce = true \})", r"\1; hasLiveData = true")),
     "exactly once"),
    ("reset-keeps-live", "live_signal_after_publish", "pstore",
     _W("func reset()", _rm("hasLiveData = false")), "hasLiveData = false"),
    ("reset-keeps-body", "live_signal_after_publish", "pstore",
     _W("func reset()", _rm("lastLiveBody = nil")), "lastLiveBody"),
    ("refusal-as-error", "live_signal_after_publish", "pstore",
     _sub(r"if case \.signInRequired = appError \{\s*loadErrorMessage = nil\s*return false\s*\}\s*", ""), "not found"),
    ("store-cancel-as-error", "live_signal_after_publish", "pstore",
     _W(_STORE_LOAD, _sub(r"if appError\.isCancellation \{ return false \}\s*", "")), "not found"),
    ("store-no-body", "live_signal_after_publish", "pstore", _rm("lastLiveBody = body"), "not found"),
    ("store-join-drops-outcome", "load_returns_outcome", "pstore",
     _rep("return await running.value", "_ = await running.value; return false"), "joined load's outcome"),
    ("store-task-clobbers", "load_returns_outcome", "pstore",
     _rep("if !Task.isCancelled { self.loadTask = nil }", "self.loadTask = nil"), "unregister a newer"),
    ("sync-no-bump", "confirmed_writes_bump", "pstore",
     _W("private func syncTickers(for portfolioId: String) async throws", _rm("confirmedMutationCount &+= 1")),
     "syncTickers"),
    ("holdings-no-bump", "confirmed_writes_bump", "pstore",
     _W(list(_CONFIRMED_WRITES)[1], _rm("confirmedMutationCount &+= 1")), "setHoldings"),
    ("create-no-bump", "confirmed_writes_bump", "pstore",
     _W(list(_CONFIRMED_WRITES)[2], _rm("confirmedMutationCount &+= 1")), "createPortfolio"),
    ("rename-no-bump", "confirmed_writes_bump", "pstore",
     _W(list(_CONFIRMED_WRITES)[3], _rm("confirmedMutationCount &+= 1")), "renamePortfolio"),
    ("delete-no-bump", "confirmed_writes_bump", "pstore",
     _W(list(_CONFIRMED_WRITES)[4], _rm("confirmedMutationCount &+= 1")), "deletePortfolio"),
    ("reorder-no-bump", "confirmed_writes_bump", "pstore",
     _W(list(_CONFIRMED_WRITES)[5], _rm("confirmedMutationCount &+= 1")), "reorderPortfolios"),
    ("switch-no-bump", "confirmed_writes_bump", "pstore",
     _W(list(_CONFIRMED_WRITES)[6], _rm("confirmedMutationCount &+= 1")), "setActivePortfolio"),
    ("reorder-bumps-optimistically", "confirmed_writes_bump", "pstore",
     _W(list(_CONFIRMED_WRITES)[5], _move_line_before("confirmedMutationCount &+= 1", "let previous = portfolios")),
     "BEFORE the server confirmed"),
    # ── ViewModel: the account-snapshot contract ──
    ("seed-overwrites-live", "seed_precondition_first", "vm",
     _W(_SEED, _rm("snapshotSeed == nil, !hasLiveHoldings, trackedAssets.isEmpty,")), "snapshotSeed == nil"),
    ("seed-ignores-gate", "seed_precondition_first", "vm",
     _W(_SEED, _rm("!assetsRequiresSignIn, !assetsIsReconnecting,")), "!assetsRequiresSignIn"),
    ("seed-any-group", "seed_precondition_first", "vm",
     _W(_SEED, _sub(r",\s*snapshot\.payload\.activePortfolioId == portfolioStore\.activePortfolioId", "")),
     "activePortfolioId"),
    ("seed-not-first", "seed_precondition_first", "vm",
     _W(_SEED, _sub(r"^\{\s*guard", "{\n        seedShownAt = Date()\n        guard")), "OPENS with its guard"),
    ("seed-latches", "seed_precondition_first", "vm",
     _W(_SEED, _rep("snapshotSeed = snapshot.payload", "snapshotSeed = snapshot.payload\n        hasLoadedOnce = true")),
     "hasLoadedOnce ="),
    ("seed-writes-rows", "seed_precondition_first", "vm",
     _W(_SEED, _rep("snapshotSeed = snapshot.payload", "snapshotSeed = snapshot.payload\n        trackedAssets = snapshot.payload.assets")),
     "trackedAssets ="),
    ("seed-into-store", "seed_precondition_first", "vm",
     _W(_SEED, _rep("snapshotSeed = snapshot.payload", "snapshotSeed = snapshot.payload\n        portfolioStore.objectWillChange.send()")),
     "portfolioStore."),
    ("seed-spawns-load", "seed_precondition_first", "vm",
     _W(_SEED, _rep("snapshotSeed = snapshot.payload", "snapshotSeed = snapshot.payload\n        Task { await self.loadData() }")),
     "Task {"),
    ("seed-no-epoch-record", "seed_precondition_first", "vm",
     _W(_SEED, _rm("seededEpoch = snapshotStore.epoch")), "seededEpoch"),
    ("prepare-seeds-before-read", "prepare_order", "vm",
     _W(_PREPARE, _move_line_before("seedFromSnapshot()", "await snapshotStore.prepare(")), "out of order"),
    ("prepare-no-epoch-drop", "prepare_order", "vm", _W(_PREPARE, _rm("dropSeedIfEpochMoved()")), "not found"),
    ("prepare-no-expiry", "prepare_order", "vm", _W(_PREPARE, _rm("expireSnapshotIfStale()")), "not found"),
    ("prepare-loads", "prepare_order", "vm",
     _W(_PREPARE, _rep("seedFromSnapshot()", "seedFromSnapshot()\n        await loadData()")), "disk only"),
    ("save-twice", "one_save_never_in_a_catch", "vm",
     _W(_FEED, _rep("} catch {", "} catch {\n            snapshotStore.save(parts: [:], payload: TrackingSnapshot("
                    "assets: [], portfolios: [], activePortfolioId: nil, insights: .unknown), savedAt: Date(), "
                    "epoch: snapshotStore.epoch)")), "exactly one"),
    ("save-moved-into-catch", "one_save_never_in_a_catch", "vm",
     lambda src: _W(_FEED, _rep("} catch {", "} catch {\n            snapshotStore.save(parts: parts, payload: "
                                "payload, savedAt: capturedAt, epoch: snapshotEpoch)"))(
         _rm("snapshotStore.save(parts: parts, payload: payload, savedAt: capturedAt, epoch: snapshotEpoch)")(src)),
     "from a catch block"),
    ("epoch-after-request", "one_save_never_in_a_catch", "vm",
     _W(_PERFORM, _move_line("let snapshotEpoch = snapshotStore.epoch", "await (feedTask, portfoliosTask)")),
     "out of order"),
    ("save-wrong-epoch", "one_save_never_in_a_catch", "vm",
     _rep("savedAt: capturedAt, epoch: snapshotEpoch)", "savedAt: capturedAt, epoch: snapshotStore.epoch)"),
     "PRE-request epoch"),
    ("save-on-partial", "one_save_never_in_a_catch", "vm",
     _rep("let bothLive = feedSucceeded && portfoliosSucceeded && generation == loadGeneration",
          "let bothLive = feedSucceeded && generation == loadGeneration"), "BOTH halves"),
    ("save-after-purge", "one_save_never_in_a_catch", "vm", _rep("if bothLive, !purged, generation", "if bothLive, generation"),
     "conditioned on both live"),
    ("save-from-poll", "one_save_never_in_a_catch", "vm",
     _W("func startPriceRefreshTimer()", _rep("await self.loadTrackingFeed()",
                                             "await self.loadTrackingFeed()\n                _ = self.snapshotStore.epoch")),
     "the poll never saves"),
    ("late-capture", "save_inputs_from_this_load", "vm",
     _rep("parts[TrackingSnapshot.assetsPart] = feedBody", "parts[TrackingSnapshot.assetsPart] = lastLiveFeedBody ?? feedBody"),
     "after the insights await"),
    ("capture-after-purge", "save_inputs_from_this_load", "vm",
     _W(_PERFORM, _move_line("let capturedPortfoliosBody = portfolioStore.lastLiveBody",
                             "purged = await portfolioStore.purgeTickers(")), "right after phase 1"),
    ("insights-any-group", "save_inputs_from_this_load", "vm",
     _rep("= settled, portfolioId == capturedActiveId {", "= settled {"), "captured active group"),
    ("shared-twice", "shared_once_in_init", "vm",
     _W(_PREPARE, _rep("expireSnapshotIfStale()", "expireSnapshotIfStale()\n        _ = TrackingSnapshotStore.shared.epoch")),
     "exactly once"),
    ("shared-outside-init", "shared_once_in_init", "vm",
     lambda src: _W(_PREPARE, _rep("expireSnapshotIfStale()", "expireSnapshotIfStale()\n        _ = TrackingSnapshotStore.shared.epoch"))(
         _rep("self.snapshotStore = TrackingSnapshotStore.shared", "self.snapshotStore = AccountSnapshotStore<TrackingSnapshot>"
              ".inMemory(config: .tracking)")(src)), "assigned inside init"),
    ("init-prepares", "shared_once_in_init", "vm",
     _W(_VM_INIT, _rep("self.snapshotStore = TrackingSnapshotStore.shared",
                       "self.snapshotStore = TrackingSnapshotStore.shared\n        Task { await self.prepareSnapshot() }")),
     "nothing is read or loaded at launch"),
    ("task-no-hidden-prepare", "task_prepare_order", "view",
     _sub(r"(viewModel\.stopPriceRefreshTimer\(\)\n)\s*await viewModel\.prepareSnapshot\(\)\n", r"\1"), "not found"),
    ("task-prepare-before-stop", "task_prepare_order", "view",
     _sub(r"(\s*viewModel\.stopPriceRefreshTimer\(\))(\n\s*await viewModel\.prepareSnapshot\(\))", r"\2\1"), "out of order"),
    ("task-no-cancel-guard", "task_prepare_order", "view",
     _W(_LIVE_ROOT, _rm("guard !Task.isCancelled else { return }")), "not found"),
    ("task-load-before-prepare", "task_prepare_order", "view",
     _sub(r"(\n\s*await viewModel\.prepareSnapshot\(\)\n)(\s*guard !Task\.isCancelled else \{ return \}\n)"
          r"(\s*await viewModel\.loadIfNeeded\(\)\n)", r"\n\2\3\1"), "out of order"),
    ("task-onchange-seed", "task_prepare_order", "view",
     _sub(r"(\.task\(id: isActiveTab\) \{)", r".onChange(of: isActiveTab) { _, _ in }\n        \1"), "one seeding rule"),
    ("identity-keeps-seed", "identity_clears_then_reseeds", "vm",
     _W(_IDENTITY, _rm("snapshotSeed = nil")), "not found"),
    ("identity-rows-below-gate", "identity_clears_then_reseeds", "vm",
     _W(_IDENTITY, _move_line("trackedAssets = []", "guard isActiveTab else { return }")), "trackedAssets = []"),
    ("identity-body-below-gate", "identity_clears_then_reseeds", "vm",
     _W(_IDENTITY, _move_line("lastLiveFeedBody = nil", "guard isActiveTab else { return }")), "lastLiveFeedBody"),
    ("identity-whales-below-gate", "identity_clears_then_reseeds", "vm",
     _W(_IDENTITY, _move_line("trackedWhales = []", "guard isActiveTab else { return }")), "trackedWhales = []"),
    ("identity-roster-below-gate", "identity_clears_then_reseeds", "vm",
     _W(_IDENTITY, _move_line("heroWhales = []", "guard isActiveTab else { return }")), "heroWhales = []"),
    ("identity-error-below-gate", "identity_clears_then_reseeds", "vm",
     _W(_IDENTITY, _move_line("assetsErrorMessage = nil", "guard isActiveTab else { return }")), "assetsErrorMessage"),
    ("identity-markers-below-gate", "identity_clears_then_reseeds", "vm",
     _W(_IDENTITY, _move_line("recentlyAddedTickers.removeAll()", "guard isActiveTab else { return }")),
     "recentlyAddedTickers.removeAll()"),
    ("identity-joins-old-load", "identity_clears_then_reseeds", "vm",
     _W(_IDENTITY, _rm("loadTask?.cancel()")), "not found"),
    ("identity-keeps-score", "identity_clears_then_reseeds", "vm",
     _W(_IDENTITY, _rm("portfolioInsights = nil")), "not found"),
    ("identity-reseed-before-clear", "identity_clears_then_reseeds", "vm",
     _W(_IDENTITY, _move_line_before("await prepareSnapshot()", "trackedAssets = []")), "cleared after the re-seed"),
    ("identity-reseed-below-gate", "identity_clears_then_reseeds", "vm",
     _W(_IDENTITY, _move_line("await prepareSnapshot()", "guard isActiveTab else { return }")), "below the active-tab gate"),
    ("identity-latch-any", "identity_clears_then_reseeds", "vm",
     _rep("if generation == loadGeneration { hasLoadedOnce = true }", "if !Task.isCancelled { hasLoadedOnce = true }"),
     "ITS load completing"),
    ("snapshot-written-to-store", "no_store_write_from_snapshot", "vm",
     _W("private func dropSeedIfEpochMoved()",
        _rep("snapshotSeed = nil", "Task { await portfolioStore.setActivePortfolio(snapshotSeed?.activePortfolioId ?? \"\") }\n        snapshotSeed = nil")),
     "feeds snapshot data"),
    ("store-names-snapshot", "no_store_write_from_snapshot", "pstore",
     _rep("private(set) var lastLiveBody: Data?", "private(set) var lastLiveBody: Data?\n    var shown: TrackingSnapshot?"),
     "never reaches the store"),
    # ── ViewModel: the Tracking rows ──
    ("refusal-keeps-seed", "refusal_drops_seed", "vm", _W(_FEED, _rm("self.snapshotSeed = nil")), "snapshotSeed"),
    ("refusal-keeps-feed", "refusal_drops_seed", "vm", _W(_FEED, _rm("self.hasLiveFeed = false")), "hasLiveFeed"),
    ("refusal-reseeds", "refusal_drops_seed", "vm",
     _W(_FEED, _rep("self.snapshotSeed = nil", "self.snapshotSeed = nil\n                seedFromSnapshot()")), "re-seeds"),
    ("feed-unfenced-success", "feed_fenced", "vm",
     _W(_FEED, _sub(r"(responseType: TrackingFeedResponse\.self\s*\)\s*)guard generation == loadGeneration else \{ return false \}",
                    r"\1")), "both refuse"),
    ("feed-cancel-as-error", "feed_fenced", "vm", _W(_FEED, _rm("if appError.isCancellation { return false }")), "not found"),
    ("feed-no-body", "feed_fenced", "vm", _W(_FEED, _rm("self.lastLiveFeedBody = body")), "not found"),
    ("feed-generation-late", "feed_fenced", "vm",
     _W(_FEED, _move_line("let generation = loadGeneration", "responseType: TrackingFeedResponse.self")), "out of order"),
    ("purge-on-feed-only", "purge_needs_both_live", "vm",
     _rep("if bothLive {\n            var allowed", "if feedSucceeded {\n            var allowed"), "conditioned on `bothLive`"),
    ("purge-twice", "purge_needs_both_live", "vm",
     _rep("var purged = false", "var purged = false\n        _ = await portfolioStore.purgeTickers(notIn: [])"),
     "second, unconditioned purge"),
    ("live-keeps-seed", "live_replaces_seed", "vm", _rm("if bothLive { replaceSnapshotWithLiveHoldings() }"), "not found"),
    ("replace-keeps-seed", "live_replaces_seed", "vm",
     _W("private func replaceSnapshotWithLiveHoldings()", _rep("snapshotSeed = nil", "seedShownAt = nil")),
     "never replace"),
    ("latch-skipped-on-tab-away", "latch_on_completion", "vm",
     _W("func loadIfNeeded() async", _rep("await loadData()", "await loadData()\n        guard !Task.isCancelled else { return }")),
     "latch waits on the awaiting"),
    ("latch-other-identity", "latch_on_completion", "vm",
     _W("func loadIfNeeded() async", _rm("guard generation == loadGeneration else { return }")), "not found"),
    ("timer-behind-hidden-tab", "latch_on_completion", "vm",
     _W("func loadIfNeeded() async", _sub(r"(hasLoadedOnce = true\s*)guard !Task\.isCancelled else \{ return \}", r"\1")),
     "not found"),
    ("retry-not-on-activation", "latch_on_completion", "vm",
     _W("func loadIfNeeded() async", _rm("retryFailedLoadIfNeeded()")), "not found"),
    ("retry-after-failed-poll", "retry_on_activation_only", "vm",
     _W("private func retryFailedLoadIfNeeded()", _sub(r"guard !hasLiveHoldings else \{ return \}\s*", "")),
     "`guard !hasLiveHoldings`"),
    ("retry-while-unarmed", "retry_on_activation_only", "vm",
     _W("private func retryFailedLoadIfNeeded()", _rm("!assetsRequiresSignIn, !assetsIsReconnecting, ")), "unarmed"),
    ("retry-loop", "retry_on_activation_only", "vm",
     _W("private func retryFailedLoadIfNeeded()", _rep("guard feedFailed", "while false {}\n        guard feedFailed")),
     "became a loop"),
    ("retry-from-timer", "retry_on_activation_only", "vm",
     _W("func startPriceRefreshTimer()", _rep("await self.loadTrackingFeed()",
                                             "await self.loadTrackingFeed()\n                self.retryFailedLoadIfNeeded()")),
     "periodic retry loop"),
    ("early-awaited-before-gate", "insights_early_and_fenced", "vm",
     _W(_PERFORM, _move_line_before("let early = await earlyInsights", "hasAttemptedLoad = true")), "out of order"),
    ("insights-serial", "insights_early_and_fenced", "vm",
     _rep("async let earlyInsights: InsightsFetch = fetchPortfolioInsights(for: hintedPortfolioId)",
          "let earlyResult: InsightsFetch = await fetchPortfolioInsights(for: hintedPortfolioId)"), "not found"),
    ("no-refetch-on-mismatch", "insights_early_and_fenced", "vm",
     _rep("if purged || (settled == nil && earlyInsightsToken == insightsRequestToken) {", "if purged {"), "refetched"),
    ("publish-any-token", "insights_early_and_fenced", "vm",
     _rm("guard token == insightsRequestToken else { return false }"), "request-token fence"),
    ("publish-any-id", "insights_early_and_fenced", "vm",
     _W("private func publishPortfolioInsights(_ fetch: InsightsFetch, token: Int) -> Bool",
        _rm("guard portfolioId == portfolioStore.activePortfolioId else { return false }")), "another group"),
    ("initial-phase-resolving", "insights_tristate", "vm",
     _rep("var portfolioInsightsPhase: PortfolioInsightsPhase = .idle", "var portfolioInsightsPhase: PortfolioInsightsPhase = .resolving"),
     "STARTS idle"),
    ("gate-term-dropped", "insights_tristate", "vm",
     _rep("var portfolioInsightsIsGated: Bool { assetsRequiresSignIn || assetsIsReconnecting }",
          "var portfolioInsightsIsGated: Bool { assetsRequiresSignIn }"), "both account-gate flags"),
    ("resolving-ignores-gate", "insights_tristate", "vm",
     _W("var portfolioInsightsIsResolving: Bool", _rm("!portfolioInsightsIsGated, ")), "account gate"),
    ("failed-ignores-gate", "insights_tristate", "vm",
     _W("var portfolioInsightsDidFail: Bool", _rm("!portfolioInsightsIsGated, ")), "account gate"),
    ("no-error-means-resolving", "insights_tristate", "vm",
     _W("var portfolioInsightsIsResolving: Bool",
        _rep("return !portfolioInsightsDidFail",
             "return !portfolioInsightsDidFail || (portfolioStore.loadErrorMessage == nil && !portfolioStore.hasLiveData)")),
     "no live data and no error"),
    ("spinner-always", "insights_tristate", "vm",
     _rep("return portfolioInsightsPhase == .resolving || isLoading || portfolioStore.isLoading", "return true"),
     "actually on the wire"),
    ("gated-answer-known", "insights_tristate", "vm", _rm("if portfolioInsightsIsGated { return (false, nil) }"),
     "opens with the gate"),
    ("snapshot-shows-other-group-score", "insights_tristate", "vm",
     _rep("if portfolioInsightsPhase == .known, sameGroup {", "if portfolioInsightsPhase == .known {"), "another group"),
    ("carry-outlives-request", "insights_carry_across_swap", "vm",
     _rm("if portfolioInsightsPhase != .resolving { insightsCarriedFromSnapshot = nil }"), "outlives"),
    ("swap-spinner", "insights_carry_across_swap", "vm",
     _rep("let kept = keptSeedInsightsForLiveGroup ?? insightsCarriedFromSnapshot,", "let kept = insightsCarriedFromSnapshot,"),
     "drops the card to a spinner"),
    ("carry-any-group", "insights_carry_across_swap", "vm",
     _sub(r",\s*kept\.portfolioId == liveGroup \{", " {"), "another group's kept score"),
    ("carry-not-captured", "insights_carry_across_swap", "vm",
     _W("private func replaceSnapshotWithLiveHoldings()", _rm("insightsCarriedFromSnapshot = kept")), "not found"),
    ("carry-after-seed-cleared", "insights_carry_across_swap", "vm",
     _W("private func replaceSnapshotWithLiveHoldings()",
        _move_line_before("snapshotSeed = nil", "if portfolioInsightsPhase == .resolving, let kept")), "out of order"),
    ("edit-keeps-carry", "insights_carry_across_swap", "vm",
     _W("private func discardSnapshotAfterConfirmedEdit()", _rm("insightsCarriedFromSnapshot = nil")),
     "pre-edit snapshot score"),
    ("carry-survives-group-switch", "insights_carry_across_swap", "vm",
     _rep("carried.portfolioId != portfolioId", "false"), "another group keeps"),
    ("kept-any-age", "insights_carry_across_swap", "vm",
     _W(_KEPT, _sub(r",\s*let savedAt = snapshotSeedSavedAt,\s*AccountSnapshotPolicy\.isDisplayable\(savedAt: savedAt, now: Date\(\)\)", "")),
     "isDisplayable"),
    ("kept-outside-the-swap", "insights_carry_across_swap", "vm",
     _W(_KEPT, _rm("loadTask != nil, ")), "loadTask != nil"),
    ("kept-any-group", "insights_carry_across_swap", "vm",
     _W(_KEPT, _rep("groupId == portfolioStore.activePortfolioId", "true")), "groupId =="),
    ("failure-shows-setup", "insights_tristate", "section",
     _sub(r"\} else if didFail \{\s*failedState\s*", "} "), "not found"),
    ("section-no-gate-branch", "insights_tristate", "section",
     _sub(r"\} else if isGated \{\s*gatedHint\s*", "} "), "not found"),
    ("progress-unconditional", "insights_tristate", "section", _rep("if showsProgress {", "if true {"), "without anything"),
    ("screen-drops-resolving", "insights_tristate", "view",
     _rm("isResolving: viewModel.portfolioInsightsIsResolving,"), "isResolving"),
    ("screen-drops-gate", "insights_tristate", "view", _rm("isGated: viewModel.portfolioInsightsIsGated,"), "isGated"),
    ("auto-open-on-nil", "auto_open_needs_known_state", "view",
     _rep("if isOn && viewModel.shouldAutoOpenPortfolioConfig {", "if isOn && viewModel.displayedDiversificationScore == nil {"),
     "unknown or failed"),
    ("auto-open-unknown", "auto_open_needs_known_state", "vm",
     _rep("presentedInsightsAnswer.known && displayedDiversificationScore == nil", "displayedDiversificationScore == nil"),
     "presentedInsightsAnswer.known"),
    ("config-ungated", "edit_entry_points_gated", "vm",
     _W("func openPortfolioConfigSheet()", _sub(r"guard canEditPortfolio else \{.*?return\s*\}\s*", "")), "OPENS with"),
    ("new-portfolio-ungated", "edit_entry_points_gated", "vm",
     _W("func openNewPortfolioSheet()", _sub(r"guard canEditPortfolio else \{.*?return\s*\}\s*", "")), "OPENS with"),
    ("remove-ungated", "edit_entry_points_gated", "vm",
     _W("func removeAsset(_ asset: TrackedAsset)", _sub(r"guard canEditHoldings else \{.*?return\s*\}\s*", "")), "OPENS with"),
    ("remove-all-ungated", "edit_entry_points_gated", "vm",
     _W("func removeAssetFromAll(_ asset: TrackedAsset)", _sub(r"guard canEditHoldings else \{.*?return\s*\}\s*", "")),
     "OPENS with"),
    ("blocked-edit-silent", "edit_entry_points_gated", "vm",
     _W("func openManageTickersSheet()", _rep("reportPortfolioStillLoading(action: \"manage this portfolio's tickers\")",
                                              "_ = 0")), "refuses silently"),
    ("save-holdings-ungated", "edit_entry_points_gated", "vm",
     _W("func savePortfolioHoldings(_ items: [HoldingUpdateItem]) async throws",
        _sub(r"guard canEditPortfolio else \{\s*throw[^\n]*\n\s*\}\s*", "")), "requires a live portfolio list"),
    ("edit-on-attempted", "edit_entry_points_gated", "vm",
     _rep("var canEditPortfolio: Bool { portfolioStore.hasLiveData }", "var canEditPortfolio: Bool { portfolioStore.hasLoadedOnce }"),
     "hasLoadedOnce only means attempted"),
    ("edit-holdings-on-snapshot", "edit_entry_points_gated", "vm",
     _rep("portfolioStore.hasLiveData && !isShowingSnapshot }", "portfolioStore.hasLiveData }"), "snapshot rows"),
    ("swipe-ungated", "edit_entry_points_gated", "list", _rep("if allowsRemoval {", "if true {"), "does not hold"),
    ("screen-allows-removal", "edit_entry_points_gated", "view",
     _rep("allowsRemoval: viewModel.canEditHoldings", "allowsRemoval: true"), "regardless of whether"),
    ("header-new-enabled", "edit_entry_points_gated", "header",
     _rm("isDisabled: !viewModel.canEditPortfolio"), "New / Edit Portfolios"),
    ("configure-enabled", "edit_entry_points_gated", "section", _rm(".disabled(!configureEnabled)"), "holdings editor"),
    ("no-purge-subscription", "confirmed_edit_purges", "vm",
     _rep("self?.discardSnapshotAfterConfirmedEdit()", "_ = self"), "discardSnapshotAfterConfirmedEdit"),
    ("purge-deferred", "confirmed_edit_purges", "vm",
     _rep(".dropFirst()\n            .sink { [weak self] _ in self?.discardSnapshotAfterConfirmedEdit() }",
          ".dropFirst()\n            .receive(on: RunLoop.main)\n            .sink { [weak self] _ in self?.discardSnapshotAfterConfirmedEdit() }"),
     "deferred"),
    ("discard-no-purge", "confirmed_edit_purges", "vm",
     _W("private func discardSnapshotAfterConfirmedEdit()", _rep("snapshotStore.purgeCache()", "_ = snapshotStore.epoch")),
     "another group"),
    ("discard-keeps-seed", "confirmed_edit_purges", "vm",
     _W("private func discardSnapshotAfterConfirmedEdit()", _rm("snapshotSeed = nil")), "pre-edit snapshot on screen"),
    ("removal-file-kept", "removal_elsewhere_purges_file", "vm",
     _W("func handleWatchlistChange(_ change: WatchlistChange)", _rm("purgeSnapshotAfterRemovalElsewhere()")),
     "leaves the saved file"),
    ("removal-purge-only-after-load", "removal_elsewhere_purges_file", "vm",
     _W("func handleWatchlistChange(_ change: WatchlistChange)",
        _sub(r"(\n\s*if !change\.added \{\s*purgeSnapshotAfterRemovalElsewhere\(\)\s*\})"
             r"(\n\s*guard hasLoadedOnce \|\| loadTask != nil else \{ return \})", r"\2\1")), "out of order"),
    ("removal-restamps-foreign-seed", "removal_elsewhere_purges_file", "vm",
     _rep("if seedWasCurrent { seededEpoch = snapshotStore.epoch }", "seededEpoch = snapshotStore.epoch"),
     "another binding"),
    ("removal-restamp-before-purge", "removal_elsewhere_purges_file", "vm",
     _W("private func purgeSnapshotAfterRemovalElsewhere()",
        _move_line_before("if seedWasCurrent { seededEpoch = snapshotStore.epoch }", "snapshotStore.purgeCache()")),
     "out of order"),
    ("render-hidden-snapshot", "render_gate", "view",
     _rep("} else if isActiveTab || !viewModel.isShowingSnapshot {", "} else if true {"), "hidden tab"),
    ("env-dropped", "render_gate", "view",
     _W(_ASSETS, _rm("@Environment(\\.isActiveTab) private var isActiveTab")), "no longer reads isActiveTab"),
    ("hidden-branch-renders-rows", "render_gate", "view",
     _sub(r"(\} else \{\n\s*)TrackedAssetsSkeleton\(isAnimated: false\)\n(\s*\.padding\(\.horizontal, AppSpacing\.lg\)\n\s*\}\n\n)",
          r"\1holdingsList\n\2"), "render gate does not cover"),
    ("gate-swap-animated", "render_gate", "view",
     _sub(r"\n[ \t]*\.transaction\(value: isActiveTab\) \{ \$0\.animation = nil \}", ""), "bleeds through"),
    ("hidden-snapshot-score", "render_gate", "view",
     _sub(r"if isActiveTab \|\| !viewModel\.isShowingSnapshot \{\s*insightsSection\s*\}", "insightsSection"),
     "hidden tab"),
    ("never-loaded-shows-empty", "never_loaded_is_not_empty", "view",
     _rep("} else if viewModel.filteredAssets.isEmpty && !viewModel.hasAttemptedLoad {", "} else if false {"), "not found"),
    ("never-loaded-animated", "never_loaded_is_not_empty", "view",
     _sub(r"(hasAttemptedLoad \{\n\s*)TrackedAssetsSkeleton\(isAnimated: false\)", r"\1TrackedAssetsSkeleton()"), "static skeleton"),
    ("hidden-shimmer", "never_loaded_is_not_empty", "skeleton",
     _sub(r"if isAnimated \{\s*placeholderStack\.shimmer\(\)\s*\} else \{\s*placeholderStack\s*\}",
          "if isAnimated {\n                placeholderStack\n            } else {\n                placeholderStack.shimmer()\n            }"),
     "asked not to"),
    ("placeholder-feed-only", "portfolios_failure_not_empty", "view",
     _rep("errorMessage: viewModel.holdingsErrorMessage", "errorMessage: viewModel.assetsErrorMessage"), "'No tickers yet'"),
    ("holdings-error-ignores-portfolios", "portfolios_failure_not_empty", "vm",
     _rep("return portfolioStore.hasLiveData ? nil : portfolioStore.loadErrorMessage", "return nil"),
     "portfolioStore.loadErrorMessage"),
    ("mixed-sources", "presented_all_or_nothing", "vm",
     _rep("hasLiveFeed && portfolioStore.hasLiveData }", "hasLiveFeed || portfolioStore.hasLiveData }"), "ONE half"),
    ("snapshot-over-gate", "presented_all_or_nothing", "vm",
     _W("var presentedSnapshot: TrackingSnapshot?", _rm("!assetsRequiresSignIn, !assetsIsReconnecting,")),
     "!assetsRequiresSignIn"),
    ("snapshot-any-group", "presented_all_or_nothing", "vm",
     _W("var presentedSnapshot: TrackingSnapshot?", _rm("seed.activePortfolioId == portfolioStore.activePortfolioId,")),
     "seed.activePortfolioId"),
    ("snapshot-no-age", "presented_all_or_nothing", "vm",
     _W("var presentedSnapshot: TrackingSnapshot?",
        _sub(r",\s*AccountSnapshotPolicy\.isDisplayable\(savedAt: savedAt, now: Date\(\)\)", "")), "isDisplayable"),
    ("rows-ignore-snapshot", "presented_all_or_nothing", "vm",
     _rep("let source: [TrackedAsset] = seed?.assets ?? trackedAssets", "let source: [TrackedAsset] = trackedAssets"),
     "not found"),
    ("alerts-on-snapshot", "presented_all_or_nothing", "vm",
     _W("private var activeTickerSet: Set<String>", _rep("portfolioStore.activePortfolio?", "presentedPortfolio?")),
     "live-only"),
    ("status-unrendered", "snapshot_labelled", "header",
     _sub(r"if let label = viewModel\.snapshotUpdatedLabel \{\s*snapshotStatus\(label\)\s*\}", ""), "'Updated <time>'"),
    ("picker-live-name", "snapshot_labelled", "header",
     _rep("viewModel.presentedPortfolio?.name", "viewModel.portfolioStore.activePortfolio?.name"), "not found"),
    ("ax-status-squeezed", "snapshot_labelled", "header",
     _rep("if dynamicTypeSize.isAccessibilitySize, let label", "if false, let label"), "squeezed to an ellipsis"),
    ("ax-status-in-row", "snapshot_labelled", "header",
     _W("private func stackedLayout(_ label: String) -> some View",
        _rep("Spacer(minLength: AppSpacing.xs)\n", "Spacer(minLength: AppSpacing.xs)\n                snapshotStatus(label)\n")),
     "a line of its own"),
    ("retry-dead-pixels", "snapshot_labelled", "header",
     _W("private func snapshotStatus(_ label: String) -> some View", _rm(".contentShape(Rectangle())")), "dead pixels"),
    ("label-own-wording", "snapshot_labelled", "vm",
     _rep("return AccountSnapshotPolicy.updatedLabel(savedAt: savedAt)", 'return "Updated"'), "not found"),
    ("duplicate-group-after-failure", "add_recovery_needs_live_load", "vm",
     _rep("if loaded && portfolioStore.portfolios.isEmpty {", "if portfolioStore.portfolios.isEmpty {"), "FAILED load"),
    ("add-aborts-silently", "add_recovery_needs_live_load", "vm",
     _W("func addTickerFromSearch(_ result: StockSearchResult)",
        _sub(r"(Still no live active portfolio[^\n]*\n)\s*AppActions\.shared\.reportMutationFailure\(\s*APIError\.unknown"
             r"\(message: Self\.portfolioStillLoadingMessage\), action: \"add \\\(symbol\)\"\s*\)\n", r"\1")),
     "aborts silently"),
    ("recovery-on-nil-id-only", "add_recovery_needs_live_load", "vm",
     _rep("if !portfolioStore.hasLiveData || portfolioStore.activePortfolioId == nil {",
          "if portfolioStore.activePortfolioId == nil {"), "remembered hint"),
    ("add-to-unheld-group", "add_recovery_needs_live_load", "vm",
     _rep("guard portfolioStore.hasLiveData, let portfolioId", "guard let portfolioId"), "no live list holds"),
    ("star-fills-before-check", "add_recovery_needs_live_load", "vm",
     _W("func addTickerFromSearch(_ result: StockSearchResult)",
        _move_line_before("recentlyAddedTickers[portfolioId, default: []].insert(symbol)",
                          "guard portfolioStore.hasLiveData, let portfolioId")), "before the live-list check"),
    ("seed-not-patched", "watchlist_change_patches_seed", "vm",
     _rm("snapshotSeed = Self.removingRow(of: change.ticker, from: seed)"), "not found"),
    ("seed-patched-after-guard", "watchlist_change_patches_seed", "vm",
     _W("func handleWatchlistChange(_ change: WatchlistChange)",
        _move_line("snapshotSeed = Self.removingRow(of: change.ticker, from: seed)",
                   "guard hasLoadedOnce || loadTask != nil else { return }")), "out of order"),
    ("epoch-drop-noop", "stale_seed_dropped", "vm", _rep("seeded != snapshotStore.epoch", "seeded != seeded"),
     "previous store binding"),
    ("expiry-never", "stale_seed_dropped", "vm",
     _rep("!AccountSnapshotPolicy.isDisplayable(savedAt: savedAt, now: now)", "false"), "never expires"),
    ("no-foreground-expiry", "stale_seed_dropped", "view", _rm("viewModel.expireSnapshotIfStale()"), "96 h window"),
    ("seed-clear-keeps-date", "stale_seed_dropped", "vm",
     _W("@Published private(set) var snapshotSeed: TrackingSnapshot?", _rm("snapshotSeedSavedAt = nil")),
     "snapshotSeedSavedAt"),
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
        "// guard hasLiveData else { return false }\n"
        "/// snapshotStore.save(parts: parts, payload: payload, savedAt: capturedAt, epoch: snapshotEpoch)\n"
        "/* if isActiveTab || !viewModel.isShowingSnapshot {\n"
        "   TrackingSnapshotStore.shared */\n"
        'let url = "https://example.com"  // confirmedMutationCount &+= 1\n'
    )
    code = _strip(prose)
    for token in ("hasLiveData", "snapshotStore.save(", "isShowingSnapshot", "TrackingSnapshotStore",
                  "confirmedMutationCount"):
        assert token not in code, f"{token!r} survived the stripper"
    assert "https://example.com" in code, "the stripper ate a URL literal"
    assert len(code.splitlines()) == len(prose.splitlines()), "the stripper must keep line structure"


def test_the_scans_read_the_live_root_not_the_preview_copy():
    """`TrackingContentView` in TrackingView.swift is preview-only; the `.task` and the
    foreground expiry scanned above must be on the LIVE root, and the Assets content block must
    stop at its own closing brace."""
    s = _sources()
    live = _block(s["view"], _LIVE_ROOT)
    assert "AssetsTabContent(viewModel: viewModel)" in live
    preview = _block(s["view"], "struct TrackingContentView: View")
    assert "prepareSnapshot" not in preview, "scan drifted — the preview copy is the one being changed"
    assets = _block(s["view"], _ASSETS)
    assert "MostPopularWhalesSection" not in assets, "the AssetsTabContent block ran into WhalesTabContent"
