"""Home paints instantly on a cold launch — and never paints the wrong account's dashboard.

TestFlight 1.0 (9), 2026-09-23, roaming after the US close: "At initial, it loads so slow". The
first Home frame waited for launch + DNS/TCP/TLS + the server's six-way gather + the download,
under a full-screen `LoadingOverlay` that swallowed every touch. Four fixes are pinned here:

  A1. `HomeDashboardSnapshotStore` keeps the last LIVE dashboard on the device (raw response
      bytes, Library/Caches, owner-fenced). `AppState.configure` primes it before the tab tree
      mounts, `HomeDashboardViewModel` seeds from it, and the pulse header says
      "Updated <time>" until a live load lands.
  A2. The overlay is gone; a first load renders `HomeDashboardSkeleton` IN the scroll content.
  A3. `APIClient.refreshArmedTokenIfExpired` refreshes an expired access token BEFORE sending
      (all four transports, through the existing single-flight) instead of eating a 401 and a
      second round trip — and can never end a session, clear a token or throw.
  A4. A transient first-load failure is retried at +2 s and +5 s (scheduled, never joined, never
      on auth or a 429), `GET /home/dashboard` times out at 15 s instead of 30 s, and Home
      reloads when the network comes back.

Review fixes (2026-10-01): a snapshot from an EARLIER US-market (ET) day no longer says
"Today's Top Movers" / "#1 today" (it reads "Top Movers · Sep 28", re-dated on foreground and
after a failed load, never wider than the original title); a degraded empty watchlist never
overwrites a saved one; the snapshot is published to memory only after every fence, and both
`bindOwner` and `prime` really bind; `expireSnapshotIfStale` drops only a stale snapshot; and
`requestReturningBody` gets `request<T>`'s DEBUG localhost/Railway failover.

Round 3 (2026-10-01): the snapshot is dated by the SESSION its numbers describe — the backend's
`_numbers_session` (the 09:30 ET open; weekends and holidays step back), copied into
`MarketHoursUtil.numbersSessionDay` and compared with both instants shifted back by the backend's
grace — re-dated on tab activation too; the watchlist refusal also covers the same list emptied
by a failed quote fetch and lapses with the display window; and the one-token mutations that
survived round 2 (a discarded relabel, an inverted "already dated" check, `&&` → `||`) now fail.
The Swift session rule, holiday table and grace are checked against the real backend code.

The owner fence is the dangerous half (auth.md §7): the file holds one account's watchlist and
prices, so every path that changes WHO is signed in must re-bind or clear it, and a load that
left under one identity must never be saved under the next.

Source scans (there is no XCTest target): comments stripped and every check scoped to its
brace-bounded declaration (.claude/rules/testing.md §3). `MUTATIONS` breaks each property once
and asserts its guard fails (the A3/A4 rows also name the assertion they must fail WITH). The
backend↔iOS halves (token lifetimes, the `sub` claim, the pulse-strip size, the pre-flight skew,
the dashboard timeout against the server's section guards) are asserted against the real
backend code.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Callable

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"

_FILES = {
    "store": _IOS / "Core/Repositories/HomeDashboardSnapshotStore.swift",
    "repo": _IOS / "Core/Repositories/HomeRepository.swift",
    "api": _IOS / "Core/Services/APIClient.swift",
    "models": _IOS / "Models/HomeDashboardModels.swift",
    "vm": _IOS / "ViewModels/HomeDashboardViewModel.swift",
    "view": _IOS / "Views/Screens/HomeDashboardView.swift",
    "skeleton": _IOS / "Views/Molecules/HomeDashboardSkeleton.swift",
    "app": _IOS / "Core/State/AppState.swift",
    "settings": _IOS / "Views/Screens/AppSettingsView.swift",
    "endpoint": _IOS / "Core/Services/APIEndpoint.swift",
    "card": _IOS / "Views/Molecules/ScannerCard.swift",
    "theme": _IOS / "Theme/AppTheme.swift",
    "hours": _IOS / "Core/Utilities/MarketHoursUtil.swift",
}

_STORE_CLASS = "final class HomeDashboardSnapshotStore"
_VM_CLASS = "final class HomeDashboardViewModel: ObservableObject"
_VIEW = "struct HomeDashboardView: View"


# ── Scanning helpers ─────────────────────────────────────────────────────────────────

def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails, keeping line structure.

    `(?<![:/])` keeps the `//` of a `https://` literal. The fixes' own comments name
    `seedFromSnapshot`, `lastAuthenticatedUserId`, `LoadingOverlay` and the store's methods, so
    an un-stripped scan would pass on prose.
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


def _store(s: dict[str, str]) -> str:
    return _block(s["store"], _STORE_CLASS)


def _vm(s: dict[str, str]) -> str:
    return _block(s["vm"], _VM_CLASS)


def _content(s: dict[str, str]) -> str:
    return _block(_block(s["view"], _VIEW), "private var content: some View")


def _refusal_branch(perform_load: str) -> str:
    at = _idx(perform_load, "if case .signInRequired = appError")
    return _block_at(perform_load, at)


def _arm(switch_body: str, case: str) -> str:
    """One `case` arm of a flat switch: from `case X` to the next `case`/end."""
    at = _idx(switch_body, case)
    nxt = switch_body.find("\n        case ", at + len(case))
    return switch_body[at : nxt if nxt != -1 else len(switch_body)]


# ── A1: the owner fence in AppState ──────────────────────────────────────────────────

def _check_session_end_clears(s):
    funnel = _block(s["app"], "private func discardDataForEndedSession()")
    assert "HomeDashboardSnapshotStore.shared.clearForEndedSession()" in funnel, (
        "the session-end funnel no longer clears the Home snapshot — the ended account's "
        "watchlist and prices paint on the next cold launch (auth.md §7)"
    )


def _check_primed_before_the_tabs_mount(s):
    cfg = _block(s["app"], "func configure(apiClient: APIClient, authService: AuthService)")
    _in_order(
        cfg,
        "await primeStoredCredential()",
        "HomeDashboardSnapshotStore.shared.prime(",
        'await restoreSession(trigger: "launch")',
    )
    call = cfg[_idx(cfg, "HomeDashboardSnapshotStore.shared.prime("):]
    call = call[: _idx(call, 'await restoreSession(trigger: "launch")')]
    assert "await HomeDashboardSnapshotStore.shared.prime(" in cfg, "prime is not awaited"
    assert "WidgetJWT.subject(of:" in call and "getStoredToken()" in call, (
        "prime is not given the stored credential's `sub` — the snapshot has no owner to check"
    )


def _check_apply_profile_binds(s):
    block = _block(s["app"], "func applyProfile(")
    assert "HomeDashboardSnapshotStore.shared.bindOwner(profile.id)" in block, (
        "applyProfile no longer binds the snapshot owner, so a sign-in as another account "
        "could reseed the previous account's dashboard"
    )


def _check_account_switch_rebinds(s):
    oa = _block(s["app"], "private func onAuthenticated(userId: String? = nil, identity: Int) async")
    m = re.search(r"if\s+let\s+userId\s*,\s*let\s+previous\s*=\s*lastAuthenticatedUserId\s*,"
                  r"\s*previous\s*!=\s*userId\s*\{", oa)
    assert m, "the account-switch branch moved — this scan has drifted"
    switch = _block_at(oa, m.start())
    _in_order(switch, "discardDataForEndedSession()", "HomeDashboardSnapshotStore.shared.bindOwner(userId)")


# ── A1: the store ────────────────────────────────────────────────────────────────────

def _check_display_is_owner_and_age_fenced(s):
    store = _store(s)
    disp = _block(store, "func snapshotForDisplay(")
    for token in ("boundOwner", "snapshot.ownerUserId == owner", "isDisplayable("):
        assert token in disp, f"snapshotForDisplay lost `{token}`"
    window = _block(store, "nonisolated static func isDisplayable(")
    assert "maxDisplayAge" in window and "maxFutureSkew" in window, (
        "the display window no longer bounds BOTH the age and a future-dated save"
    )


_SAVE = "func save(body: Data, data: HomeDashboardData, epoch captured: Int)"
_SAVE_FENCES = (
    "guard captured == epoch",
    "guard let owner = boundOwner",
    "guard data.isWorthPersisting",
    "data.hasDegradedWatchlist(comparedTo: previous.data)",
    "Self.maxBodyBytes",
)


def _check_save_is_fenced(s):
    save = _block(_store(s), _SAVE)
    _in_order(save, *_SAVE_FENCES, "enqueue {")
    # `current` is what the next reseed paints: it must be set only once EVERY fence has passed
    # (the FIRST assignment — a copy left above the fences counts).
    published = _idx(save, "current = Snapshot(")
    assert published > max(_idx(save, fence) for fence in _SAVE_FENCES), (
        "save publishes the snapshot to memory before its fences — a load that left as account A "
        "and lands after the switch to B puts A's dashboard into `current` under owner B, and the "
        "next reseed paints it for B (auth.md §7)"
    )
    assert published < _idx(save, "enqueue {"), "the file is written before the in-memory snapshot"


def _check_save_keeps_a_good_watchlist(s):
    save = _block(_store(s), _SAVE)
    assert "if let previous = current," in save, (
        "a degraded watchlist read overwrites a saved watchlist — the backend answers a timed-out "
        "or failed read as ('Your Watchlist', not a group, no tiles), and the next cold launch "
        "paints Home without the user's own tickers"
    )
    at = _idx(save, "if let previous = current,")
    condition = save[at : save.index("{", at)]
    for token in ("previous.ownerUserId == owner", "data.hasDegradedWatchlist(comparedTo: previous.data)"):
        assert token in condition, f"the degraded-watchlist refusal lost `{token}`"
    assert "Self.isDisplayable(savedAt: previous.savedAt, now: Date())" in condition, (
        "the degraded-watchlist refusal never lapses — it must hold only while the saved snapshot "
        "is still displayable, or a user who really emptied their watchlist has every save refused "
        "after the old snapshot expired, and the next cold launch paints the skeleton"
    )
    assert _norm(condition) == (
        "if let previous = current, previous.ownerUserId == owner, "
        "Self.isDisplayable(savedAt: previous.savedAt, now: Date()), "
        "data.hasDegradedWatchlist(comparedTo: previous.data)"
    ), f"the degraded-watchlist refusal's condition changed — re-derive: {_norm(condition)!r}"
    refusal = _block_at(save, at)
    assert re.search(r"\breturn\b", refusal), "the degraded-watchlist refusal does not refuse"
    assert "current =" not in refusal and "enqueue" not in refusal, "the degraded-watchlist refusal still saves"
    ext = _block(s["models"], "extension HomeDashboardData")
    shape = _block(ext, "var hasDefaultEmptyWatchlist: Bool")
    for token in ("watchlist.isEmpty", "!watchlistIsGroup", "watchlistTitle == Self.defaultWatchlistTitle"):
        assert token in shape, (
            f"hasDefaultEmptyWatchlist lost `{token}` — it no longer matches ONLY the server's "
            "degraded-read shape"
        )
    assert _norm(shape) == "{ watchlist.isEmpty && !watchlistIsGroup && watchlistTitle == Self.defaultWatchlistTitle }", (
        "hasDefaultEmptyWatchlist is not the conjunction of all three — `||` matches every user "
        "without a group, so once a snapshot has tiles every later save is refused"
    )
    degraded = _block(ext, "func hasDegradedWatchlist(comparedTo saved: HomeDashboardData) -> Bool")
    assert "guard watchlist.isEmpty, !saved.watchlist.isEmpty else { return false }" in degraded, (
        "hasDegradedWatchlist refuses more than an EMPTY watchlist over a saved one WITH tiles"
    )
    assert "if hasDefaultEmptyWatchlist { return true }" in degraded, (
        "hasDegradedWatchlist lost the server's degraded-read shape — a group user's timed-out "
        "read ('Your Watchlist', not a group, no tiles) overwrites their saved group"
    )
    assert "return watchlistTitle == saved.watchlistTitle && watchlistIsGroup == saved.watchlistIsGroup" in degraded, (
        "hasDegradedWatchlist lost the same-list rule — a failed quote fetch (every tile of the "
        "same list dropped) overwrites the saved watchlist"
    )
    assert _norm(degraded) == (
        "{ guard watchlist.isEmpty, !saved.watchlist.isEmpty else { return false } "
        "if hasDefaultEmptyWatchlist { return true } "
        "return watchlistTitle == saved.watchlistTitle && watchlistIsGroup == saved.watchlistIsGroup }"
    ), f"hasDegradedWatchlist changed — re-derive: {_norm(degraded)!r}"
    fallback = _block(_block(s["repo"], "final class HomeRepository: HomeRepositoryProtocol"),
                      "private static func watchlistTitle(")
    assert "HomeDashboardData.defaultWatchlistTitle" in fallback and '"Your Watchlist"' not in fallback, (
        "the repository's watchlist-title fallback is a second literal — it can drift from the "
        "title the degraded-watchlist refusal compares against"
    )


def _check_identity_changes_bump_the_epoch(s):
    store = _store(s)
    clear = _block(store, "func clearForEndedSession()")
    _in_order(clear, "epoch &+= 1", "enqueueDelete()")
    assert "boundOwner = nil" in clear and "current = nil" in clear, "clear keeps the binding or the snapshot"
    bind = _block(store, "func bindOwner(_ userId: String)")
    _in_order(bind, "guard owner != boundOwner", "epoch &+= 1", "enqueueDelete()")
    assert "current = nil" in bind, "a new owner keeps the previous owner's snapshot in memory"
    assert re.search(r"^\s*boundOwner = owner\s*$", bind[_idx(bind, "guard owner != boundOwner"):], re.M), (
        "bindOwner no longer binds the new owner — the next live save is filed under the PREVIOUS "
        "account's id, and that account's next launch paints this one's dashboard"
    )
    purge = _block(store, "func purgeCache()")
    _in_order(purge, "epoch &+= 1", "enqueueDelete()")


def _check_disk_ops_are_serial(s):
    store = _store(s)
    enqueue = _block(store, "private func enqueue(")
    _in_order(enqueue, "let previous = diskTail", "diskTail = Task.detached", "await previous?.value", "operation()")
    read = _block(store, "private func readOnDiskTail(")
    _in_order(read, "let previous = diskTail", "await previous?.value", "HomeDashboardSnapshotDisk.read(at:")
    assert "diskTail = " in read, "the launch read does not take its place on the tail"
    # Every write/delete goes through the tail, and the class does no other detached work.
    for op in ("HomeDashboardSnapshotDisk.write(", "HomeDashboardSnapshotDisk.delete("):
        for line in (ln for ln in store.splitlines() if op in ln):
            assert "enqueue {" in line, f"`{op}` runs outside the serial tail: {line.strip()}"
    assert store.count("Task.detached") == 3, "a disk operation runs outside enqueue/readOnDiskTail"
    assert "Data(contentsOf:" not in store, "the store reads the file outside the tail (on main)"


def _check_prime_rejects_and_rechecks(s):
    store = _store(s)
    prime = _block(store, "func prime(ownerUserId: String?, apiClient: APIClient = .shared) async")
    head = prime[: _idx(prime, "guard let owner else")]
    assert re.search(r"^\s*boundOwner = owner\s*$", head, re.M), (
        "prime does not bind the stored credential's owner — the snapshot never paints, and "
        "applyProfile's bindOwner (nil → this account) then deletes it on every launch"
    )
    _in_order(head, "if owner != boundOwner", "boundOwner = owner", "let primedEpoch = epoch")
    changed = _block_at(head, _idx(head, "if owner != boundOwner"))
    assert "epoch &+= 1" in changed and "current = nil" in changed, (
        "prime keeps the previous owner's snapshot (or a captured epoch) across an owner change"
    )
    no_owner = _block_at(prime, _idx(prime, "guard let owner else"))
    assert "enqueueDelete()" in no_owner, "with no stored credential the file is kept"
    assert "await readOnDiskTail(" in prime, "prime does not read through the serial tail"
    assert prime.count("guard epoch == primedEpoch, boundOwner == owner") >= 2, (
        "prime does not re-check the binding after BOTH of its awaits"
    )
    switch = _block_at(prime, _idx(prime, "switch outcome"))
    assert "enqueueDelete()" in _arm(switch, "case .corrupt"), "a corrupt file is kept"
    assert "enqueueDelete()" not in _arm(switch, "case .readFailed"), (
        "a READ failure deletes the file — a launch before first unlock would lose it"
    )
    assert "enqueueDelete()" not in _arm(switch, "case .missing")
    rejected = prime[_idx(prime, "HomeDashboardSnapshotDisk.rejection("):]
    _in_order(rejected, "HomeDashboardSnapshotDisk.rejection(", "enqueueDelete()", "return")
    decode = prime[_idx(prime, "HomeRepository.dashboard(fromSnapshotBody:"):]
    assert "} catch {" in decode and "enqueueDelete()" in decode[_idx(decode, "} catch {"):], (
        "an undecodable body is kept, so every launch decodes it again"
    )
    # The decoded snapshot must actually be PUBLISHED, after the post-await re-check: a prime
    # that decodes and drops it leaves every launch on the skeleton with 100% of guards green.
    decoded = decode[: _idx(decode, "} catch {")]
    _in_order(decoded, "guard epoch == primedEpoch, boundOwner == owner, current == nil",
              "current = Snapshot(ownerUserId: owner, savedAt: envelope.savedAt, data: data)")
    disk = _block(s["store"], "nonisolated enum HomeDashboardSnapshotDisk")
    rejection = _block(disk, "static func rejection(")
    for token in ("schemaVersion", "normalizedOwner(envelope.ownerUserId) != owner", "isDisplayable("):
        assert token in rejection, f"the launch read no longer rejects on `{token}`"


# ── A1: the ViewModel ────────────────────────────────────────────────────────────────

def _check_saved_only_after_a_live_success(s):
    vm = _vm(s)
    load = _block(vm, "private func performLoad() async")
    _in_order(
        load,
        "let snapshotEpoch = snapshotStore.epoch",
        "try await repository.fetchHomeDashboard()",
        "snapshotStore.save(body: body, data: fetched.data, epoch: snapshotEpoch)",
        "} catch {",
    )
    assert vm.count("snapshotStore.save(") == 1, "the snapshot is saved from somewhere other than a live success"
    success = load[_idx(load, "try await repository.fetchHomeDashboard()") : _idx(load, "} catch {")]
    assert "snapshotSavedAt = nil" in success, "a live load keeps the 'Updated <time>' label"


def _check_refusal_branch_unchanged(s):
    branch = _refusal_branch(_block(_vm(s), "private func performLoad() async"))
    assert "data = nil" in branch, "a refused load keeps data on screen under the account gate"
    assert "seedFromSnapshot" not in branch, (
        "a refused (unarmed) load reseeds the snapshot — the account's dashboard stacked under "
        "'Reconnecting…' while the token is disarmed (auth.md §5; critic option A)"
    )


def _check_seeding_never_stamps_freshness(s):
    vm = _vm(s)
    seed = _block(vm, "private func seedFromSnapshot()")
    assert "snapshotForDisplay(" in seed and "guard data == nil" in seed
    assert "lastLoadedAt" not in seed, "seeding stamps lastLoadedAt, so loadIfStale skips the live load"
    init = _block(vm, "init(repository: HomeRepositoryProtocol? = nil, snapshotStore: HomeDashboardSnapshotStore? = nil)")
    assert "seedFromSnapshot()" in init, "the ViewModel no longer seeds the first frame"


def _check_identity_change_reseeds_in_order(s):
    handler = _block(_vm(s), "func handleIdentityChange(isActiveTab: Bool) async")
    _in_order(handler, "data = nil", "snapshotSavedAt = nil", "seedFromSnapshot()", "guard isActiveTab")


def _check_pulse_header_is_honest(s):
    content = _content(s)
    assert "viewModel.pulseHeader(for: data)" in content, "Market Pulse no longer asks pulseHeader(for:)"
    # The call's argument window (parens, not braces, so it is bounded by its last argument).
    call = content[_idx(content, "MarketPulseSection(") :]
    call = call[: _idx(call, "onTap: openPulse")]
    assert "data.marketStatusText" not in call and "data.marketIsOpen" not in call, (
        "a snapshot would claim the server's live 'Markets Open' from the moment of the save"
    )
    header = _block(_vm(s), "func pulseHeader(for data: HomeDashboardData) -> (text: String, isOpen: Bool)")
    snap = _block_at(header, _idx(header, "if let savedAt = snapshotSavedAt"))
    assert "snapshotStatusText(" in snap and "false)" in snap, "the snapshot arm is not 'Updated <time>' + muted"


def _check_clear_cache_purges(s):
    clear = _block(s["settings"], "private func clearCache()")
    assert "HomeDashboardSnapshotStore.shared.purgeCache()" in clear, "Clear Cache leaves the Home snapshot"


def _check_is_worth_persisting(s):
    ext = _block(s["models"], "extension HomeDashboardData")
    tiles = _block(ext, "var equityPulseTileCount: Int")
    assert "!= .crypto" in tiles, "the equity tile count counts the crypto tile (backend `_equity_tile_count`)"
    worth = _block(ext, "var isWorthPersisting: Bool")
    assert "equityPulseTileCount >= Self.expectedEquityPulseTiles" in worth
    assert "scanners.isEmpty && signals.isEmpty && themes.isEmpty && trillionClub.isEmpty" in worth


def _check_bytes_reach_the_repository(s):
    repo = _block(s["repo"], "final class HomeRepository: HomeRepositoryProtocol")
    fetch = _block(repo, "func fetchHomeDashboard() async throws -> HomeDashboardFetch")
    assert "apiClient.requestReturningBody(" in fetch and "endpoint: .getHomeDashboard" in fetch, (
        "the live fetch no longer returns its response bytes, so nothing is ever persisted"
    )
    snap = _block(repo, "static func dashboard(fromSnapshotBody body: Data")
    assert "apiClient.decodeBody(HomeDashboardResponseDTO.self" in snap and "map(dto)" in snap, (
        "the snapshot is not decoded through the SAME DTO + mapping as a live fetch"
    )
    mock = _block(s["repo"], "final class MockHomeRepository: HomeRepositoryProtocol")
    assert "body: nil" in _block(mock, "func fetchHomeDashboard() async throws -> HomeDashboardFetch"), (
        "the mock returns wire bytes, so preview data could be persisted as a user's snapshot"
    )
    api = _block(s["api"], "func requestReturningBody<T: Decodable>(")
    assert "try await downloadData(endpoint: endpoint" in api, (
        "requestReturningBody no longer rides downloadData — it lost the gate / 401 refresh / retry"
    )
    decode = _block(s["api"], "func decodeBody<T: Decodable>(")
    assert "APIError.decodingError(" in decode


# ── A2: no blocking overlay; an inert in-content skeleton ───────────────────────────

def _check_no_blocking_overlay(s):
    view = _block(s["view"], _VIEW)
    assert "HomeHeader(" in view and "CustomTabBar(" in view, "Home scan drifted"
    assert "LoadingOverlay" not in view, (
        "a LoadingOverlay is back on Home — it swallows every touch, header and tab bar "
        "included, for the whole first load"
    )


def _check_skeleton_renders_in_content(s):
    content = _content(s)
    data_at = _idx(content, "if let data = viewModel.data {")
    data_block = _block_at(content, data_at)
    assert "InlineDisclaimerNotice(" in data_block, "the disclaimer left `if let data` (a stray header on a cold start)"
    after = content[content.index("{", data_at) + len(data_block) :]
    assert re.match(r"\s*else if viewModel\.showsFirstLoadSkeleton \{", after), (
        "the skeleton is not the `else` of `if let data` — it can render beside the dashboard"
    )
    assert "HomeDashboardSkeleton()" in _block_at(after, 0)
    for lazy in ("LazyVStack", "LazyHStack", "LazyVGrid", "LazyHGrid"):
        assert lazy not in content, f"{lazy} in Home's content (the in-place-resize hang)"


def _check_skeleton_condition(s):
    cond = _block(_vm(s), "var showsFirstLoadSkeleton: Bool")
    for token in ("data == nil", "!requiresSignIn", "!isReconnecting", "!hasAttemptedLoad"):
        assert token in cond, f"showsFirstLoadSkeleton lost `{token}`"


def _check_skeleton_is_inert(s):
    src = s["skeleton"]
    skel = _block(src, "struct HomeDashboardSkeleton: View")
    for token in (".shimmer()", ".accessibilityElement(children: .ignore)", ".accessibilityLabel("):
        assert token in skel, f"the skeleton lost `{token}`"
    for banned in ("Button", "onTapGesture", "Gesture(", "Lazy", "Color(hex:", "@Environment(AppState"):
        assert banned not in skel, f"the skeleton contains `{banned}`"
    assert src.count("#Preview") >= 2, "the skeleton needs light AND dark previews"


# ── A3: the pre-flight token refresh (APIClient) ─────────────────────────────────────

# The four transports. Each header stops BEFORE its `{` (`_block` bounds the first `{` after it).
_TRANSPORTS = {
    "request<T>": "func request<T: Decodable>(",
    "request(void)": "func request(endpoint: APIEndpoint, allowAuthRetry: Bool = true)",
    "downloadData": "func downloadData(endpoint: APIEndpoint,",
    "openStream": "private func openStream(endpoint: APIEndpoint,",
}
_PREFLIGHT_HEADER = "private func refreshArmedTokenIfExpired(for endpoint: APIEndpoint) async"
_PREFLIGHT_CALL = "await refreshArmedTokenIfExpired(for: endpoint)"
_SINGLE_FLIGHT_CALL = re.compile(r"\brefreshTokenSingleFlight\s*\(")


def _preflight(s: dict[str, str]) -> str:
    api = s["api"]
    assert re.search(re.escape(_PREFLIGHT_HEADER) + r"\s*\{", api), (
        "the pre-flight is gone, or it can throw now — a transport would fail on a refresh that "
        "merely could not complete"
    )
    return _block(api, _PREFLIGHT_HEADER)


def _check_preflight_runs_first_in_every_transport(s):
    api = s["api"]
    for name, header in _TRANSPORTS.items():
        body = _block(api, header)
        assert body[1:].lstrip().startswith(_PREFLIGHT_CALL + "\n"), (
            f"{name}: the pre-flight refresh is not the transport's first statement — an expired "
            "token is sent, 401s, and costs a second round trip"
        )
        built = [i for i in (body.find("buildRequest("), body.find("openStreamOnce(")) if i != -1]
        assert built and body.index(_PREFLIGHT_CALL) < min(built), (
            f"{name}: the request is built (Authorization header set) before the pre-flight refresh"
        )
    assert api.count(_PREFLIGHT_CALL) == len(_TRANSPORTS), (
        f"the pre-flight is called {api.count(_PREFLIGHT_CALL)}x, expected once per transport"
    )


def _check_preflight_cannot_end_a_session(s):
    pf = _preflight(s)
    for token in ("!endpoint.isAuthEndpoint", "tokenRefresher != nil", "WidgetJWT.expiry(of: token)",
                  "await self.refreshTokenSingleFlight()"):
        assert token in pf, f"the pre-flight lost `{token}`"
    banned = {
        "handleUnrecoverableAuthFailure": "ends the session",
        "authFailureHandler": "reports an auth failure",
        "setAuthToken": "re-arms the credential",
        "Keychain": "reads the Keychain",
        "tokenRefresher?(": "bypasses the single-flight",
        "tokenRefresher!(": "bypasses the single-flight",
        "throw": "throws",
    }
    for token, what in banned.items():
        assert token not in pf, (
            f"the pre-flight token refresh can end a session: it {what} (`{token}`) — only the 401 "
            "interceptor may interpret a failed refresh (auth.md §3/§5)"
        )
    assert not re.search(r"\bauthToken\s*=(?!=)", pf), (
        "the pre-flight token refresh can end a session: it assigns authToken — only the "
        "single-flight's own task may arm a refreshed token"
    )
    assert not re.search(r"\btry\b", pf), (
        "the pre-flight token refresh can end a session: it calls something that throws"
    )


def _check_preflight_rechecks_after_joining(s):
    pf = _preflight(s)
    join = _block_at(pf, _idx(pf, "if let inFlight = refreshInFlight"))
    assert "await inFlight.value" in join, "the pre-flight no longer waits for a refresh in flight"
    assert "return" not in join, (
        "the pre-flight returns after joining an in-flight refresh instead of re-checking the "
        "armed token — a token re-armed meanwhile goes out expired"
    )
    _in_order(pf, "if let inFlight = refreshInFlight", "guard let token = authToken",
              "WidgetJWT.expiry(of: token)", "await self.refreshTokenSingleFlight()")


def _check_preflight_judges_each_token_once(s):
    pf = _preflight(s)
    assert re.search(r"guard let token = authToken, token != preflightExemptToken else \{ return \}", pf), (
        "the pre-flight judges the same token again — a device clock set ahead refreshes on "
        "every request (a refresh storm)"
    )
    call = _idx(pf, "await self.refreshTokenSingleFlight()")
    threshold = _idx(pf, "guard secondsLeft <= Self.proactiveRefreshSkew else { return }")
    assert threshold < call, "the refresh is not behind the expiry threshold"
    assert "preflightExemptToken = token" in pf[threshold:call], (
        "the pre-flight judges the same token again: the exemption is not set BEFORE the refresh "
        "is awaited, so requests arriving meanwhile judge it too"
    )
    refreshed = pf[_idx(pf, "case .refreshed(let fresh):"):]
    refreshed = refreshed[: refreshed.find("case ", len("case "))]
    assert "preflightExemptToken = fresh" in refreshed, (
        "the pre-flight judges the same token again: a freshly refreshed token is not exempted, "
        "so a wrong device clock refreshes it once more"
    )
    unknown_means_server = (
        "an unreadable exp is treated as expired — a token the client cannot parse would be "
        "refreshed before every send; unknown means the server decides"
    )
    assert re.search(r"guard let exp = claimedExpiry else \{", pf), unknown_means_server
    unreadable = _block_at(pf, _idx(pf, "guard let exp = claimedExpiry else"))
    assert "return" in unreadable and "refreshTokenSingleFlight" not in unreadable, unknown_means_server


def _check_preflight_threshold(s):
    pf = _preflight(s)
    assert "let secondsLeft = exp.timeIntervalSinceNow" in pf, "the expiry threshold moved — scan drifted"
    assert "guard secondsLeft <= Self.proactiveRefreshSkew else { return }" in pf, (
        "the pre-flight refreshes a token that is NOT about to expire (or never refreshes one that is)"
    )


def _check_single_flight_caller_set(s):
    """Every caller of the single-flight, `self.`-prefixed or not: the four 401 interceptors
    (test_ios_transient_refresh_keeps_session.py T1 counts the literal `await
    refreshTokenSingleFlight()` there) plus the pre-flight. A sixth would be a refresh nobody
    reviewed against auth.md §3/§5."""
    api = s["api"]
    assert api.count("private func refreshTokenSingleFlight(") == 1, "the single-flight moved — scan drifted"
    total = len(_SINGLE_FLIGHT_CALL.findall(api)) - 1  # minus the declaration
    per = {name: len(_SINGLE_FLIGHT_CALL.findall(_block(api, header))) for name, header in _TRANSPORTS.items()}
    per["pre-flight"] = len(_SINGLE_FLIGHT_CALL.findall(_preflight(s)))
    assert all(n == 1 for n in per.values()), f"single-flight calls per site: {per}"
    assert total == sum(per.values()) == 5, (
        f"a refresh call site outside the four transports and the pre-flight ({total} calls, {per})"
    )


# ── A4: the bounded fast retry, the timeout, the network-restore reload ──────────────

_SCHEDULER = "private func scheduleFirstLoadRetryIfNeeded(after appError: AppError, underlying error: Error)"
_CLASSIFIER = "nonisolated static func isTransientFirstLoadFailure(_ appError: AppError, underlying error: Error) -> Bool"
_SCHEDULE_CALL = "scheduleFirstLoadRetryIfNeeded(after: appError, underlying: error)"


def _arms(switch_body: str) -> list[tuple[str, str]]:
    """`(pattern, body)` per arm of a FLAT brace-bounded switch, in source order."""
    inner = switch_body[1:-1]
    heads = list(re.finditer(r"^\s*(case\b[^:]*|default)\s*:", inner, flags=re.M))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(inner)
        pattern = m.group(1).strip()
        out.append(("default" if pattern == "default" else pattern[len("case"):].strip(), inner[m.end():end]))
    return out


def _arm_for(arms: list[tuple[str, str]], case: str) -> tuple[str, str]:
    """The arm a value of `case` lands in: the first `case` naming it, else `default`."""
    for pattern, body in arms:
        if pattern != "default" and re.search(re.escape(case) + r"\b", pattern):
            return pattern, body
    for pattern, body in arms:
        if pattern == "default":
            return pattern, body
    raise AssertionError(f"no arm reaches `{case}` and there is no default")


def _check_retry_is_bounded(s):
    vm = _vm(s)
    m = re.search(r"nonisolated static let firstLoadRetryDelays: \[Duration\] = \[([^\]]*)\]", vm)
    assert m, "firstLoadRetryDelays is gone or no longer a plain literal — scan drifted"
    entries = [e.strip() for e in m.group(1).split(",") if e.strip()]
    delays = [float(x) for x in re.findall(r"^\.seconds\(([\d.]+)\)$", "\n".join(entries), flags=re.M)]
    assert len(delays) == len(entries) and delays == [2.0, 5.0], (
        f"the fast retries are not bounded to +2 s and +5 s (got {entries}) — plan A4"
    )
    sched = _block(vm, _SCHEDULER)
    guard_clause = sched[: _idx(sched, "else {")]
    for token in ("firstLoadRetryAttempt < Self.firstLoadRetryDelays.count", "!hasLiveData",
                  "!firstLoadRetrySuspended", "Self.isTransientFirstLoadFailure(appError, underlying: error)"):
        assert token in guard_clause, f"the fast retry is unbounded: its guard lost `{token}`"
    _in_order(sched, "let delay = Self.firstLoadRetryDelays[firstLoadRetryAttempt]",
              "firstLoadRetryAttempt += 1", "firstLoadRetryTask = Task")
    declined = _block_at(sched, _idx(sched, "else {"))
    assert "cancelFirstLoadRetry(resetBudget: false)" in declined and "return" in declined, (
        "a declined retry leaves the skeleton up (isFirstLoadRetryPending) or an earlier retry armed"
    )


def _check_retry_classifier(s):
    cls = _block(_vm(s), _CLASSIFIER)
    arms = _arms(_block_at(cls, _idx(cls, "switch appError")))
    true_arms = [p for p, b in arms if p != "default" and re.fullmatch(r"\s*return true\s*", b)]
    assert len(true_arms) == 1, f"expected one transient arm, got {true_arms}"
    named = {c.strip() for c in true_arms[0].split(",")}
    assert named == {".noConnection", ".timeout", ".serverError", ".authUnavailable"}, (
        f"the first-load retry fires on {sorted(named)} — only transport, 5xx and AUTH_UNAVAILABLE "
        "failures are transient (never auth, a 429, a 4xx or a decode drift)"
    )
    for never in (".rateLimited", ".signInRequired", ".cancelled", ".unauthorized", ".tokenExpired",
                  ".sessionEnded", ".forbidden", ".notFound", ".validationFailed", ".apiError",
                  ".insufficientCredits", ".planUpgradeRequired"):
        pattern, body = _arm_for(arms, never)
        assert re.fullmatch(r"\s*return false\s*", body), (
            f"the first-load retry fires on {never} (arm `{pattern}`) — never auth, a 429, a 4xx "
            "or a decode drift"
        )
    _, unknown = _arm_for(arms, ".unknown")
    assert re.fullmatch(
        r"\s*if let apiError = error as\? APIError, case \.networkError = apiError \{\s*return true\s*\}"
        r"\s*return false\s*", unknown), (
        "the first-load retry fires on every `.unknown` — a 4xx detail message and a decode drift "
        "land there too; only an APIError.networkError is a transport failure"
    )


def _check_retry_only_from_the_failure_arm(s):
    vm = _vm(s)
    load = _block(vm, "private func performLoad() async")
    refusal = _refusal_branch(load)
    assert "scheduleFirstLoadRetryIfNeeded" not in refusal, (
        "a refused (unarmed) load schedules a fast retry — a refusal heals through the session, "
        "never the clock"
    )
    assert "cancelFirstLoadRetry(resetBudget: false)" in refusal, (
        "a refused load leaves a fast retry armed (and the skeleton under the account gate)"
    )
    rest = load[load.index(refusal) + len(refusal):]
    assert rest.lstrip().startswith("else {"), "the non-auth failure arm moved — scan drifted"
    assert _SCHEDULE_CALL in _block_at(rest, 0), "a transient failure no longer schedules a fast retry"
    assert vm.count("scheduleFirstLoadRetryIfNeeded(") == 2, (
        "a fast retry is scheduled from somewhere other than performLoad's non-auth failure arm"
    )
    success = load[_idx(load, "try await repository.fetchHomeDashboard()"): _idx(load, "} catch {")]
    assert "cancelFirstLoadRetry(resetBudget: true)" in success, (
        "a live success leaves a fast retry armed, or never restores the budget"
    )


def _check_retry_is_scheduled_not_joined(s):
    vm = _vm(s)
    sched = _block(vm, _SCHEDULER)
    task = _block_at(sched, _idx(sched, "firstLoadRetryTask = Task"))
    for token in ("[weak self]", "try? await Task.sleep(for: delay)",
                  "guard !Task.isCancelled, let self else { return }", "await self.load()"):
        assert token in task, f"the fast retry is not a scheduled, cancellable task: lost `{token}`"
    _in_order(task, "Task.sleep(for: delay)", "guard !Task.isCancelled", "await self.load()")
    _in_order(sched, "firstLoadRetryTask?.cancel()", "firstLoadRetryTask = Task")
    for name in ("private func performLoad() async", "func load() async"):
        assert "Task.sleep" not in _block(vm, name), (
            f"the fast retry sleeps inside `{name}` — every caller joining the load is held "
            "through the backoff"
        )


def _check_retry_cancelled_on_identity_and_hide(s):
    vm = _vm(s)
    handler = _block(vm, "func handleIdentityChange(isActiveTab: Bool) async")
    _in_order(handler, "cancelFirstLoadRetry(resetBudget: true)", "guard isActiveTab")
    _in_order(handler, "hasAttemptedLoad = false", "guard isActiveTab")
    stop = _block(vm, "func stopAutoRefresh()")
    assert "cancelFirstLoadRetry(resetBudget: false)" in stop, "a hidden tab keeps its fast retry armed"
    assert "firstLoadRetrySuspended = true" in stop, (
        "a load in flight when the tab is hidden can still schedule a fast retry"
    )
    assert "firstLoadRetrySuspended = false" in _block(vm, "func loadIfStale("), (
        "nothing re-enables the fast retry when Home is shown again"
    )
    assert len(re.findall(r"(?<!var )\bfirstLoadRetrySuspended = false", vm)) == 1, (
        "the fast retry is re-enabled from a trigger that is not a visible one"
    )
    cancel = _block(vm, "private func cancelFirstLoadRetry(resetBudget: Bool)")
    for token in ("firstLoadRetryTask?.cancel()", "isFirstLoadRetryPending = false",
                  "if resetBudget { firstLoadRetryAttempt = 0 }"):
        assert token in cancel, f"cancelFirstLoadRetry lost `{token}`"
    assert "firstLoadRetryTask?.cancel()" in _block(vm, "deinit"), "a deallocated ViewModel's retry still fires"


def _check_skeleton_is_first_load_only(s):
    cond = _block(_vm(s), "var showsFirstLoadSkeleton: Bool")
    assert "isFirstLoadRetryPending" in cond, "the skeleton drops while a fast retry is pending"
    assert not re.search(r"\bisLoading\b", cond), (
        "the skeleton is re-raised by every 60 s poll during an outage (a bare `isLoading`) — it "
        "is for the first load and its fast retries only"
    )


def _home_timeout_seconds(endpoint_src: str) -> int:
    switch = _block(endpoint_src, "nonisolated var timeout: TimeInterval")
    assert switch.count(".getHomeDashboard") <= 1, ".getHomeDashboard shares a timeout arm — re-derive"
    m = re.search(r"case \.getHomeDashboard:\s*return (\d+)\b", switch)
    assert m, "no `.getHomeDashboard` timeout arm — a stalled first load waits the 30 s default"
    return int(m.group(1))


def _check_home_timeout(s):
    assert _home_timeout_seconds(s["endpoint"]) < 30, (
        "`.getHomeDashboard` waits as long as the 30 s default — a stalled first load is not cut"
    )


def _check_network_restore_reloads(s):
    view = _block(s["view"], _VIEW)
    assert view.count("NetworkMonitor.didRestoreNotification") == 1, (
        "Home no longer reloads when the network comes back"
    )
    at = _idx(view, "NetworkMonitor.didRestoreNotification")
    closure = _block_at(view, view.index(")", at))
    assert re.search(r"guard isActiveTab, !viewModel\.hasLiveData else \{ return \}", closure), (
        "the network-restore reload is not limited to a visible Home with nothing live on screen"
    )
    assert "await viewModel.load()" in closure, "the network-restore receiver does not load"


# ── Review fixes: honest snapshot labels, expiry direction, DEBUG failover ───────────

_EXPIRE = "func expireSnapshotIfStale(now: Date = Date())"
_RELABEL = "func relabelSnapshotIfDayRolledOver(now: Date = Date())"
_PRESENTED = "private static func presentedSnapshot("
_EARLIER_DAY = "nonisolated static func earlierMarketDay(savedAt: Date, now: Date = Date()) -> String?"
_MOVERS_TITLE = "nonisolated static func earlierDayMoversTitle(_ day: String) -> String"


def _check_expiry_drops_only_a_stale_snapshot(s):
    expire = _block(_vm(s), _EXPIRE)
    guard_clause = expire[: _idx(expire, "else { return }")]
    assert "guard let savedAt = snapshotSavedAt" in guard_clause, (
        "expireSnapshotIfStale can drop LIVE data — it must act on the snapshot only"
    )
    assert re.search(r"!\s*HomeDashboardSnapshotStore\.isDisplayable\(savedAt: savedAt, now: now\)", guard_clause), (
        "expireSnapshotIfStale drops a snapshot that is still inside its display window (and keeps "
        "one past it) — every transient failure would blank a fresh snapshot"
    )
    dropped = expire[_idx(expire, "else { return }"):]
    assert "data = nil" in dropped and "snapshotSavedAt = nil" in dropped, "an expired snapshot stays on screen"


def _check_snapshot_movers_are_dated(s):
    vm = _vm(s)
    seed = _block(vm, "private func seedFromSnapshot()")
    assert re.search(r"data = Self\.presentedSnapshot\(snapshot\.data, savedAt: snapshot\.savedAt, now: Date\(\)\)"
                     r" \?\? snapshot\.data", seed), (
        "a snapshot from an earlier day is seeded as saved — Friday's ranking paints under "
        "\"Today's Top Movers\" on Monday"
    )
    assert len(re.findall(r"(?<![\w.])data = ", seed)) == 1, (
        "a snapshot from an earlier day is seeded as saved: a second assignment overrides the dated one"
    )
    # The seed must also say it IS a snapshot: `snapshotSavedAt` is what turns the pulse header
    # into "Updated <time>" instead of the saved "Markets Open".
    assert re.search(r"\?\? snapshot\.data\n\s*snapshotSavedAt = snapshot\.savedAt\n", seed), (
        "the seed no longer stamps snapshotSavedAt right after painting — a snapshot paints "
        "under a live-looking market header"
    )
    present = _block(vm, _PRESENTED)
    # A COPY with only the scanners replaced: a field-by-field rebuild could drop a section
    # (the watchlist; the defaulted Trillion Club) and still compile.
    assert "HomeDashboardData(" not in present, (
        "presentedSnapshot rebuilds the dashboard field by field — a dropped field compiles and "
        "silently empties that section on every dated snapshot"
    )
    _in_order(present, "var presented: HomeDashboardData = data",
              "presented.scanners = data.scanners.map", "return presented")
    assert "guard let day = earlierMarketDay(savedAt: savedAt, now: now) else { return nil }" in present, (
        "presentedSnapshot no longer asks whether the save was on an earlier US-market day"
    )
    assert re.search(r"guard data\.scanners\.contains\(where: \{ \$0\.kind == \.movers && \$0\.title != title \}\)"
                     r" else \{ return nil \}", present), (
        "presentedSnapshot never (or always) relabels — it must skip only a movers card that "
        "already carries this dated title"
    )
    assert "scanner.kind == .movers ? scanner.relabelled(title: title, asOfDayLabel: day) : scanner" in present, (
        "the relabel touches more than the movers card — only it claims to be today's"
    )
    day = _block(vm, _EARLIER_DAY)
    for token in ('TimeZone(identifier: "America/New_York")', "calendar.timeZone = eastern",
                  "formatter.timeZone = eastern", 'Locale(identifier: "en_US_POSIX")'):
        assert token in day, (
            f"the snapshot is not dated by its America/New_York day (lost `{token}`) — a reader "
            "roaming in another zone sees the wrong day, or 'Today's' on yesterday's session"
        )
    # The backend's rule, both sides shifted back by its grace (`_same_numbers_session`).
    sessions = (
        "let grace: TimeInterval = MarketHoursUtil.numbersSessionGraceSeconds",
        "let savedSession: Date = MarketHoursUtil.numbersSessionDay(at: savedAt.addingTimeInterval(-grace))",
        "let currentSession: Date = MarketHoursUtil.numbersSessionDay(at: now.addingTimeInterval(-grace))",
    )
    for line in sessions:
        assert line in day, (
            "the snapshot is not judged by the SESSION its numbers describe (the 09:30 ET open, "
            f"the backend's grace) — lost `{line}`: a Monday 07:00 save, Friday's moves, keeps "
            "\"Today's\" after Monday's open"
        )
    assert "startOfDay" not in day, "the snapshot is judged by its calendar day again, not its session"
    assert re.search(r"guard savedSession != currentSession else \{ return nil \}", day), (
        "the snapshot is dated unless its session differs from now's — a same-session snapshot "
        "must keep \"Today's\", an earlier one must not"
    )
    assert re.search(r"return formatter\.string\(from: savedSession\)\s*\}\s*$", day), (
        "the dated title shows the save's calendar day, not the session its numbers describe — a "
        "Sunday save would read 'Sep 27' over Friday's ranking"
    )
    title = _block(vm, _MOVERS_TITLE)
    assert '"Top Movers' in title and "today" not in title.lower(), "the dated movers title says Today"


def _check_snapshot_relabels_on_rollover(s):
    vm = _vm(s)
    relabel = _block(vm, _RELABEL)
    assert re.search(r"guard let savedAt = snapshotSavedAt, let shown = data,", relabel), (
        "relabelSnapshotIfDayRolledOver can relabel LIVE data — it must act on the snapshot only"
    )
    assert "Self.presentedSnapshot(shown, savedAt: savedAt, now: now)" in relabel
    after_guard = relabel[_idx(relabel, "else { return }") + len("else { return }"):]
    assert _norm(after_guard) == "data = relabelled }", (
        "relabelSnapshotIfDayRolledOver computes the dated snapshot and throws it away — a snapshot "
        "on screen past the open keeps \"Today's\""
    )
    activation = _block(vm, "func loadIfStale(")
    assert re.search(r"expireSnapshotIfStale\(\)\s*relabelSnapshotIfDayRolledOver\(\)", activation), (
        "a snapshot still on screen is not re-dated on tab activation — switching back to Home "
        "after the open (no background trip) keeps \"Today's\" until a load fails"
    )
    _in_order(activation, "relabelSnapshotIfDayRolledOver()", "if let last = lastLoadedAt")
    view = _block(s["view"], _VIEW)
    at = _idx(view, "UIApplication.didBecomeActiveNotification")
    foreground = _block_at(view, view.index(")", at))
    assert "viewModel.relabelSnapshotIfDayRolledOver()" in foreground, (
        "a snapshot still shown when the US-market day rolls over is not re-dated when the app "
        "returns to the foreground"
    )
    _in_order(foreground, "viewModel.expireSnapshotIfStale()", "viewModel.relabelSnapshotIfDayRolledOver()",
              "guard isActiveTab")
    load = _block(vm, "private func performLoad() async")
    refusal = _refusal_branch(load)
    failure = _block_at(load, load.index(refusal) + len(refusal))
    assert re.search(r"expireSnapshotIfStale\(\)\s*relabelSnapshotIfDayRolledOver\(\)", failure), (
        "a snapshot kept through failed loads is not re-dated after a failed load — midnight ET "
        "passes with the app open and offline, and it still says \"Today's\""
    )


def _check_dated_card_drops_today(s):
    card = _block(s["card"], "struct ScannerCard: View")
    assert card.count("#1 today") == 1, "a second '#1 today' — scan drifted"
    assert re.search(r'Text\(scanner\.asOfDayLabel == nil\s*\?\s*"\\\(head\.secondaryText\) · #1 today"\s*'
                     r':\s*"\\\(head\.secondaryText\) · #1"\)', card), (
        "the movers hero says \"#1 today\" on a snapshot from an earlier day"
    )
    rel = _block(s["models"], "func relabelled(title: String, asOfDayLabel: String?) -> DailyScanner")
    assert "copy.asOfDayLabel = asOfDayLabel" in rel and "title: title," in rel, (
        "DailyScanner.relabelled drops the title or the day — the dated card still claims today"
    )
    for field in ("kind: kind", "gainers: gainers", "losers: losers", "entries: entries",
                  "subtitle: subtitle", "badgeText: badgeText", "infoNote: infoNote"):
        assert field in rel, f"DailyScanner.relabelled loses `{field}`"


def _swift_literal(src: str) -> str:
    """The first Swift string literal in `src`, with `\\u{…}` escapes decoded and a `\\(name)`
    interpolation left as `{name}`."""
    m = re.search(r'"((?:[^"\\]|\\.)*)"', src)
    assert m, "no string literal — scan drifted"
    body = re.sub(r"\\u\{([0-9A-Fa-f]+)\}", lambda u: chr(int(u.group(1), 16)), m.group(1))
    return re.sub(r"\\\((\w+)\)", r"{\1}", body)


_NO_BREAK_SPACES = {"\u00a0", "\u202f"}


def _check_dated_title_fits_the_header(s):
    """"Top Movers · <day>" must never be wider than "Today's Top Movers", the title the card's
    two-line header (`lineLimit(2)`, the fixed-size toggle beside it) is sized for — measured
    in the header's own font (SF Pro, 14 pt semibold) for EVERY month and day."""
    vm = _vm(s)
    template = _swift_literal(_block(vm, _MOVERS_TITLE))
    assert template.count("{day}") == 1 and template.endswith("{day}"), f"title template drifted: {template!r}"
    dot = template.index("\u00b7")
    assert template[dot - 1] in _NO_BREAK_SPACES and template[dot + 1] == "\u2009", (
        "the dated title can break BEFORE its dot, or is set with full spaces (too wide)"
    )
    fmt_line = _block(vm, _EARLIER_DAY)
    fmt = _swift_literal(fmt_line[_idx(fmt_line, "formatter.dateFormat ="):])
    m = re.fullmatch(r"MMM(.)d", fmt)
    assert m and m.group(1) in _NO_BREAK_SPACES, f"the day format is not MMM<no-break space>d: {fmt!r}"
    live = re.search(r'kind: \.movers,\s*title: "([^"]+)"', s["repo"])
    assert live, "the live movers title moved — scan drifted"
    assert "bodySmallEmphasis : Font { scaled(14, .subheadline, weight: .semibold) }" in s["theme"], (
        "the scanner title's font changed — re-measure with the new size/weight"
    )
    title_at = _idx(s["card"], "Text(scanner.title)")
    assert re.match(r"Text\(scanner\.title\)\s*\.font\(AppTypography\.bodySmallEmphasis\)", s["card"][title_at:]), (
        "the scanner title is no longer set in bodySmallEmphasis — re-measure with its new font"
    )
    image_font = pytest.importorskip("PIL.ImageFont")
    font_path = Path("/System/Library/Fonts/SFNS.ttf")
    if not font_path.exists():
        pytest.skip("SF Pro (SFNS.ttf) is not on this machine")
    font = image_font.truetype(str(font_path), 140)
    axes = []
    for axis in font.get_variation_axes():
        name = axis["name"].decode() if isinstance(axis["name"], bytes) else axis["name"]
        axes.append({"Weight": 600, "Optical Size": 17}.get(name, axis["default"]))
    font.set_variation_by_axes(axes)
    original = font.getlength(live.group(1))
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    widest = max((font.getlength(template.format(day=f"{mon}{m.group(1)}{d}")), mon, d)
                 for mon in months for d in range(1, 32))
    assert widest[0] <= original, (
        f"the dated movers title is wider than {live.group(1)!r} ({widest[0] / 10:.1f} pt vs "
        f"{original / 10:.1f} pt at 14 pt, worst {widest[1]} {widest[2]}) — it reflows the card header"
    )


_SESSION_DAY = "nonisolated static func numbersSessionDay(at instant: Date) -> Date"
_TRADING_DAY = "nonisolated private static func isTradingDay(_ day: Date, calendar: Calendar) -> Bool"
_PREVIOUS_DAY = "nonisolated private static func previousTradingDay(before day: Date, calendar: Calendar) -> Date"
_OPEN_MINUTE = re.compile(r"nonisolated static let regularOpenMinuteOfDay: Int = (\d+) \* 60 \+ (\d+)\n")


def _hours(s: dict[str, str]) -> str:
    return _block(s["hours"], "enum MarketHoursUtil")


def _check_numbers_session_rule(s):
    """`MarketHoursUtil.numbersSessionDay` — the Swift copy of the backend's `_numbers_session`
    the snapshot is dated by: the 09:30 ET open, a weekend/holiday step-back to the previous
    trading day. `test_the_swift_session_rule_agrees_with_the_backend` checks the same rule
    against the real backend function."""
    hours = _hours(s)
    m = _OPEN_MINUTE.search(hours)
    assert m and (int(m.group(1)), int(m.group(2))) == (9, 30), (
        "the session rule's open is not 09:30 ET — the movers' day change rolls at the open"
    )
    cal = _block(hours, "nonisolated private static let etCalendar: Calendar =")
    assert 'calendar.timeZone = TimeZone(identifier: "America/New_York")' in cal, (
        "the session rule reads the device's zone, not America/New_York"
    )
    day = _block(hours, _SESSION_DAY)
    assert "let calendar: Calendar = etCalendar" in day and "calendar.startOfDay(for: instant)" in day
    assert "if minuteOfDay >= regularOpenMinuteOfDay && isTradingDay(day, calendar: calendar) {" in day, (
        "a dashboard answered before the 09:30 ET open, or on a closed day, is dated as that day's "
        "session — until the open the screener still ranks the previous session's moves"
    )
    tail = day[_idx(day, "if minuteOfDay >= regularOpenMinuteOfDay"):]
    assert _norm(tail[tail.index("{"):]) == (
        "{ return day } return previousTradingDay(before: day, calendar: calendar) }"
    ), "anything before the open (or on a closed day) must step back to the previous trading day"
    assert _norm(day) == (
        "{ let calendar: Calendar = etCalendar "
        "let parts: DateComponents = calendar.dateComponents([.hour, .minute], from: instant) "
        "let minuteOfDay: Int = (parts.hour ?? 0) * 60 + (parts.minute ?? 0) "
        "let day: Date = calendar.startOfDay(for: instant) "
        "if minuteOfDay >= regularOpenMinuteOfDay && isTradingDay(day, calendar: calendar) { return day } "
        "return previousTradingDay(before: day, calendar: calendar) }"
    ), f"numbersSessionDay drifted from the backend's `_numbers_session` — re-derive: {_norm(day)!r}"
    trading = _block(hours, _TRADING_DAY)
    assert "if weekday == 1 || weekday == 7 { return false }" in trading, (
        "a weekend counts as a trading session (Calendar weekday: Sunday = 1, Saturday = 7) — a "
        "Saturday save would be dated Saturday, not by Friday's session"
    )
    assert 'String(format: "%04d-%02d-%02d", year, month, dayOfMonth)' in trading
    assert re.search(r"return !holidays\.contains\(key\)\s*\}\s*$", trading), (
        "a holiday counts as a trading session — a Good Friday save would be dated Good Friday"
    )
    previous = _block(hours, _PREVIOUS_DAY)
    assert _norm(previous) == (
        "{ var probe: Date = day for _ in 0..<10 { "
        "guard let earlier = calendar.date(byAdding: .day, value: -1, to: probe) else { break } "
        "probe = earlier if isTradingDay(probe, calendar: calendar) { break } } return probe }"
    ), (
        "previousTradingDay no longer steps BACK one day at a time to the first trading day "
        f"(bounded at 10): {_norm(previous)!r}"
    )


def _check_body_fetch_fails_over_in_debug(s):
    api = s["api"]
    body = _block(api, "func requestReturningBody<T: Decodable>(")
    handler = body[_idx(body, "} catch {"):]
    assert "#if DEBUG" in handler and "#endif" in handler, (
        "requestReturningBody's failover runs in Release (or is gone) — DEBUG only, like request<T>'s"
    )
    debug = handler[_idx(handler, "#if DEBUG"): _idx(handler, "#endif")]
    for token in ("case .networkError(let underlying) = apiError", "!Self.isCancellation(underlying)",
                  "try? await attemptFailoverReturningBody("):
        assert token in debug, f"requestReturningBody's DEBUG failover lost `{token}`"
    assert re.search(r"#endif\s*throw error\s*\}", handler), "requestReturningBody no longer rethrows the original error"
    decl = _idx(api, "private func attemptFailoverReturningBody<T: Decodable>(")
    assert api.rfind("#if DEBUG", 0, decl) > api.rfind("#endif", 0, decl), (
        "the body failover helper is compiled into Release"
    )
    helper = _block(api, "private func attemptFailoverReturningBody<T: Decodable>(")
    for token in ("guard endpoint.method.isSafeToRetryAfterServerError else { throw originalError }",
                  "guard !env.isManualOverride else { throw originalError }",
                  "guard await env.isLocalhostAvailable() else { throw originalError }",
                  "try validateResponse(httpResponse, data: data)"):
        assert token in helper, f"the body failover lost `{token}` (request<T>'s failover keeps it)"
    _in_order(helper, "guard endpoint.method.isSafeToRetryAfterServerError", "buildRequest(for: endpoint, baseURL:",
              "decodeBody(responseType, from: data)", "await env.resolve()")


GUARDS: dict[str, Callable[[dict[str, str]], None]] = {
    "session_end_clears": _check_session_end_clears,
    "primed_before_tabs": _check_primed_before_the_tabs_mount,
    "apply_profile_binds": _check_apply_profile_binds,
    "switch_rebinds": _check_account_switch_rebinds,
    "display_fenced": _check_display_is_owner_and_age_fenced,
    "save_fenced": _check_save_is_fenced,
    "epoch_bumps": _check_identity_changes_bump_the_epoch,
    "disk_serial": _check_disk_ops_are_serial,
    "prime_rejects": _check_prime_rejects_and_rechecks,
    "saved_after_success": _check_saved_only_after_a_live_success,
    "refusal_unchanged": _check_refusal_branch_unchanged,
    "seed_no_stamp": _check_seeding_never_stamps_freshness,
    "identity_reseed_order": _check_identity_change_reseeds_in_order,
    "pulse_header_honest": _check_pulse_header_is_honest,
    "clear_cache_purges": _check_clear_cache_purges,
    "worth_persisting": _check_is_worth_persisting,
    "bytes_reach_repo": _check_bytes_reach_the_repository,
    "no_overlay": _check_no_blocking_overlay,
    "skeleton_in_content": _check_skeleton_renders_in_content,
    "skeleton_condition": _check_skeleton_condition,
    "skeleton_inert": _check_skeleton_is_inert,
    # A3
    "preflight_first_everywhere": _check_preflight_runs_first_in_every_transport,
    "preflight_never_ends_session": _check_preflight_cannot_end_a_session,
    "preflight_rechecks_after_join": _check_preflight_rechecks_after_joining,
    "preflight_judges_once": _check_preflight_judges_each_token_once,
    "preflight_threshold": _check_preflight_threshold,
    "single_flight_callers": _check_single_flight_caller_set,
    # A4
    "retry_bounded": _check_retry_is_bounded,
    "retry_classifier": _check_retry_classifier,
    "retry_failure_arm_only": _check_retry_only_from_the_failure_arm,
    "retry_scheduled": _check_retry_is_scheduled_not_joined,
    "retry_cancelled": _check_retry_cancelled_on_identity_and_hide,
    "skeleton_first_load_only": _check_skeleton_is_first_load_only,
    "home_timeout": _check_home_timeout,
    "network_restore_reload": _check_network_restore_reloads,
    # Review fixes
    "save_keeps_watchlist": _check_save_keeps_a_good_watchlist,
    "expiry_direction": _check_expiry_drops_only_a_stale_snapshot,
    "snapshot_movers_dated": _check_snapshot_movers_are_dated,
    "snapshot_relabel_rollover": _check_snapshot_relabels_on_rollover,
    "dated_card_drops_today": _check_dated_card_drops_today,
    "dated_title_fits": _check_dated_title_fits_the_header,
    "body_failover_debug": _check_body_fetch_fails_over_in_debug,
    # Round 3
    "numbers_session_rule": _check_numbers_session_rule,
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


_PRIME_CALL = r"(await HomeDashboardSnapshotStore\.shared\.prime\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))"

MUTATIONS: list[tuple[str, str, str, Callable[[str], str]]] = [
    ("no-session-clear", "session_end_clears", "app", _rm("HomeDashboardSnapshotStore.shared.clearForEndedSession()")),
    ("prime-after-restore", "primed_before_tabs", "app",
     _sub(_PRIME_CALL + r"(\s*)(await restoreSession\(trigger: \"launch\"\))", r"\3\2\1")),
    ("prime-no-owner", "primed_before_tabs", "app",
     _sub(r"authService\.getStoredToken\(\)\.flatMap \{ WidgetJWT\.subject\(of: \$0\) \}", "nil")),
    ("no-apply-bind", "apply_profile_binds", "app", _rm("HomeDashboardSnapshotStore.shared.bindOwner(profile.id)")),
    ("no-switch-rebind", "switch_rebinds", "app", _rm("HomeDashboardSnapshotStore.shared.bindOwner(userId)")),
    ("display-any-owner", "display_fenced", "store", _rm("snapshot.ownerUserId == owner,")),
    ("display-no-age", "display_fenced", "store", _sub(r"age <= maxDisplayAge && ", "")),
    ("save-no-epoch", "save_fenced", "store", _sub(r"guard captured == epoch else", "if captured != epoch")),
    ("save-degraded-ok", "save_fenced", "store", _sub(r"guard data\.isWorthPersisting else", "if false")),
    ("clear-no-bump", "epoch_bumps", "store",
     _sub(r"(func clearForEndedSession\(\) \{\s*)epoch &\+= 1", r"\1")),
    ("bind-no-bump", "epoch_bumps", "store",
     _sub(r"(guard owner != boundOwner else \{ return \}\s*)epoch &\+= 1", r"\1")),
    ("enqueue-unchained", "disk_serial", "store", _sub(r"await previous\?\.value\s*operation\(\)", "operation()")),
    ("write-off-tail", "disk_serial", "store",
     _sub(r"enqueue \{ HomeDashboardSnapshotDisk\.write\(envelope, to: fileURL\) \}",
          "HomeDashboardSnapshotDisk.write(envelope, to: fileURL)")),
    ("prime-no-owner-keeps-file", "prime_rejects", "store",
     _sub(r"(guard let owner else \{\s*)enqueueDelete\(\)", r"\1")),
    ("read-failure-deletes", "prime_rejects", "store",
     _sub(r"(case \.readFailed\(let reason\):)", r"\1\n            enqueueDelete()")),
    ("prime-no-recheck", "prime_rejects", "store",
     _sub(r"guard epoch == primedEpoch, boundOwner == owner else \{", "if false {")),
    ("rejection-any-owner", "prime_rejects", "store",
     _sub(r"if HomeDashboardSnapshotStore\.normalizedOwner\(envelope\.ownerUserId\) != owner \{", "if false {")),
    ("save-in-catch", "saved_after_success", "vm",
     _sub(r"(if let body = fetched\.body \{\s*snapshotStore\.save\(body: body, data: fetched\.data, epoch: snapshotEpoch\)\s*\})"
          r"(.*?\} catch \{)", r"\2\n\1")),
    ("epoch-after-request", "saved_after_success", "vm",
     _sub(r"(let snapshotEpoch = snapshotStore\.epoch\n)(.*?)(let fetched = try await repository\.fetchHomeDashboard\(\)\n)",
          r"\2\3\1")),
    ("refusal-reseeds", "refusal_unchanged", "vm",
     _sub(r"(if case \.signInRequired = appError \{\s*data = nil)", r"\1\n                seedFromSnapshot()")),
    ("seed-stamps", "seed_no_stamp", "vm",
     _sub(r"(snapshotSavedAt = snapshot\.savedAt)", r"\1\n        lastLoadedAt = Date()")),
    ("identity-no-reseed", "identity_reseed_order", "vm",
     _sub(r"(isReconnecting = false\s*)seedFromSnapshot\(\)(\s*guard isActiveTab)", r"\1\2")),
    ("header-from-server", "pulse_header_honest", "view",
     _sub(r"statusText: pulseStatus\.text", "statusText: data.marketStatusText")),
    ("no-purge", "clear_cache_purges", "settings", _rm("HomeDashboardSnapshotStore.shared.purgeCache()")),
    ("count-crypto", "worth_persisting", "models", _sub(r"\.filter \{ \$0\.type != \.crypto \}", "")),
    ("live-fetch-no-bytes", "bytes_reach_repo", "repo",
     _sub(r"apiClient\.requestReturningBody\(", "apiClient.request(")),
    ("overlay-back", "no_overlay", "view",
     _sub(r"(CustomTabBar\(selectedTab: \$selectedTab\)\s*\})", r"\1\n            LoadingOverlay()")),
    ("skeleton-sibling", "skeleton_in_content", "view",
     _sub(r"\} else if viewModel\.showsFirstLoadSkeleton \{", "}\n                if viewModel.showsFirstLoadSkeleton {")),
    ("skeleton-under-gate", "skeleton_condition", "vm", _rm("!isReconnecting && ")),
    ("skeleton-tappable", "skeleton_inert", "skeleton",
     _sub(r"(VStack\(alignment: \.leading, spacing: AppSpacing\.xl\) \{)", r'\1\n            Button("") {}')),
    ("skeleton-no-shimmer", "skeleton_inert", "skeleton", _rm(".shimmer()")),
    # ── A3 (5th field: the assertion message the guard must fail WITH) ──
    ("preflight-missing-in-downloadData", "preflight_first_everywhere", "api",
     _sub(r"(allowAuthRetry: Bool = true\) async throws -> Data \{\n)\s*await refreshArmedTokenIfExpired\(for: endpoint\)\n",
          r"\1"),
     "downloadData: the pre-flight refresh is not the transport's first statement"),
    ("preflight-after-buildRequest", "preflight_first_everywhere", "api",
     _sub(r"(\) async throws -> T \{\n)(\s*await refreshArmedTokenIfExpired\(for: endpoint\)\n)"
          r"(\s*let request = try buildRequest\(for: endpoint\)\n)", r"\1\3\2"),
     "request<T>: the pre-flight refresh is not the transport's first statement"),
    ("preflight-missing-in-openStream", "preflight_first_everywhere", "api",
     _sub(r"(-> URLSession\.AsyncBytes \{\n)\s*await refreshArmedTokenIfExpired\(for: endpoint\)\n(\s*do \{)", r"\1\2"),
     "openStream: the pre-flight refresh is not the transport's first statement"),
    ("preflight-clears-token", "preflight_never_ends_session", "api",
     _sub(r"(case \.transientFailure, \.credentialRejected:)", r"\1\n            authToken = nil"),
     "it assigns authToken"),
    ("preflight-calls-refresher", "preflight_never_ends_session", "api",
     _sub(r"(case \.transientFailure, \.credentialRejected:)", r"\1\n            _ = await tokenRefresher?()"),
     "bypasses the single-flight"),
    ("preflight-ends-session", "preflight_never_ends_session", "api",
     _sub(r"(case \.transientFailure, \.credentialRejected:)",
          r"\1\n            _ = await handleUnrecoverableAuthFailure(.unauthorized, endpoint: endpoint)"),
     "it ends the session"),
    ("preflight-throws", "preflight_never_ends_session", "api",
     _sub(r"(private func refreshArmedTokenIfExpired\(for endpoint: APIEndpoint\) async) \{",
          r"\1 throws {\n        if authToken == nil { throw APIError.authRequired }"),
     "the pre-flight is gone, or it can throw now"),
    ("preflight-returns-after-join", "preflight_rechecks_after_join", "api",
     _sub(r"(_ = await inFlight\.value\n)", r"\1            return\n"),
     "the pre-flight returns after joining an in-flight refresh"),
    ("preflight-exempts-after-await", "preflight_judges_once", "api",
     _sub(r"(guard secondsLeft <= Self\.proactiveRefreshSkew else \{ return \}\n)\s*preflightExemptToken = token\n", r"\1"),
     "the exemption is not set BEFORE the refresh"),
    ("preflight-rejudges-a-token", "preflight_judges_once", "api",
     _sub(r", token != preflightExemptToken", ""),
     "a device clock set ahead refreshes on every request"),
    ("preflight-fresh-not-exempt", "preflight_judges_once", "api", _rm("preflightExemptToken = fresh"),
     "a freshly refreshed token is not exempted"),
    ("preflight-unknown-is-expired", "preflight_judges_once", "api",
     _sub(r"guard let exp = claimedExpiry else \{", "guard let exp = claimedExpiry ?? Date(timeIntervalSince1970: 0) as Date? else {"),
     "an unreadable exp is treated as expired"),
    ("preflight-threshold-flipped", "preflight_threshold", "api",
     _sub(r"secondsLeft <= Self\.proactiveRefreshSkew", "secondsLeft >= Self.proactiveRefreshSkew"),
     "the pre-flight refreshes a token that is NOT about to expire"),
    ("sixth-single-flight-caller", "single_flight_callers", "api",
     _sub(r"(private func refreshArmedTokenIfExpired\(for endpoint: APIEndpoint\) async \{)",
          r"func probe() async { _ = await self.refreshTokenSingleFlight() }\n    \1"),
     "a refresh call site outside the four transports and the pre-flight"),
    # ── A4 ──
    ("retry-third-delay", "retry_bounded", "vm",
     _sub(r"\[\.seconds\(2\), \.seconds\(5\)\]", "[.seconds(2), .seconds(5), .seconds(10)]"),
     "the fast retries are not bounded to +2 s and +5 s"),
    ("retry-no-attempt-bound", "retry_bounded", "vm",
     _sub(r",\s*firstLoadRetryAttempt < Self\.firstLoadRetryDelays\.count", ""),
     "its guard lost `firstLoadRetryAttempt < Self.firstLoadRetryDelays.count`"),
    ("retry-over-live-data", "retry_bounded", "vm", _sub(r"\s*!hasLiveData,", ""),
     "its guard lost `!hasLiveData`"),
    ("retry-on-429", "retry_classifier", "vm",
     _sub(r"case \.noConnection, \.timeout, \.serverError, \.authUnavailable:",
          "case .noConnection, .timeout, .serverError, .authUnavailable, .rateLimited:"),
     "the first-load retry fires on"),
    ("retry-default-true", "retry_classifier", "vm",
     _sub(r"(return false\s*default:\s*)return false", r"\1return true"),
     "the first-load retry fires on .rateLimited"),
    ("retry-any-unknown", "retry_classifier", "vm",
     _sub(r"(case \.networkError = apiError \{\s*return true\s*\}\s*)return false", r"\1return true"),
     "the first-load retry fires on every `.unknown`"),
    ("retry-from-refusal", "retry_failure_arm_only", "vm",
     _sub(r"(if case \.signInRequired = appError \{)",
          r"\1\n                scheduleFirstLoadRetryIfNeeded(after: appError, underlying: error)"),
     "a refused (unarmed) load schedules a fast retry"),
    ("refusal-keeps-retry", "retry_failure_arm_only", "vm",
     _sub(r"(if case \.signInRequired = appError \{.*?)cancelFirstLoadRetry\(resetBudget: false\)\n", r"\1"),
     "a refused load leaves a fast retry armed"),
    ("success-keeps-retry", "retry_failure_arm_only", "vm",
     _sub(r"(snapshotStore\.save\(body: body, data: fetched\.data, epoch: snapshotEpoch\)\s*\}\s*)"
          r"cancelFirstLoadRetry\(resetBudget: true\)", r"\1"),
     "a live success leaves a fast retry armed"),
    ("retry-sleeps-in-performLoad", "retry_scheduled", "vm",
     _sub(r"(private func performLoad\(\) async \{\n)", r"\1        try? await Task.sleep(for: .seconds(2))\n"),
     "the fast retry sleeps inside `private func performLoad() async`"),
    ("retry-not-cancellable", "retry_scheduled", "vm",
     _sub(r"guard !Task\.isCancelled, let self else \{ return \}", "guard let self else { return }"),
     "the fast retry is not a scheduled, cancellable task"),
    ("identity-keeps-retry", "retry_cancelled", "vm",
     _sub(r"(errorMessage = nil\s*)cancelFirstLoadRetry\(resetBudget: true\)", r"\1"),
     "'cancelFirstLoadRetry(resetBudget: true)' not found"),
    ("identity-keeps-attempted", "retry_cancelled", "vm", _sub(r"hasAttemptedLoad = false\n", ""),
     "'hasAttemptedLoad = false' not found"),
    ("hide-keeps-retry", "retry_cancelled", "vm",
     _sub(r"(firstLoadRetrySuspended = true\s*)cancelFirstLoadRetry\(resetBudget: false\)", r"\1"),
     "a hidden tab keeps its fast retry armed"),
    ("hide-not-suspended", "retry_cancelled", "vm", _sub(r"firstLoadRetrySuspended = true\n", ""),
     "a load in flight when the tab is hidden can still schedule"),
    ("skeleton-on-every-poll", "skeleton_first_load_only", "vm",
     _sub(r"\|\| isFirstLoadRetryPending\)", "|| isLoading || isFirstLoadRetryPending)"),
     "re-raised by every 60 s poll"),
    ("home-timeout-default", "home_timeout", "endpoint", _sub(r"case \.getHomeDashboard:\s*return 15\n", ""),
     "no `.getHomeDashboard` timeout arm"),
    ("home-timeout-30", "home_timeout", "endpoint", _sub(r"(case \.getHomeDashboard:\s*return )15", r"\g<1>30"),
     "waits as long as the 30 s default"),
    ("restore-reload-with-live-data", "network_restore_reload", "view",
     _sub(r"guard isActiveTab, !viewModel\.hasLiveData else \{ return \}", "guard isActiveTab else { return }"),
     "not limited to a visible Home with nothing live on screen"),
    ("no-restore-reload", "network_restore_reload", "view",
     _sub(r"NetworkMonitor\.didRestoreNotification", "UIApplication.didReceiveMemoryWarningNotification"),
     "Home no longer reloads when the network comes back"),
    # ── Review fixes: the owner fence (each of these survived every guard before) ──
    ("save-publishes-before-fences", "save_fenced", "store",
     _sub(r"(func save\(body: Data, data: HomeDashboardData, epoch captured: Int\) \{\n)(.*?)"
          r"([ \t]*current = Snapshot\(ownerUserId: owner, savedAt: savedAt, data: data\)\n)", r"\1\3\2"),
     "save publishes the snapshot to memory before its fences"),
    ("bind-no-binding", "epoch_bumps", "store",
     _sub(r"(guard owner != boundOwner else \{ return \}.*?)\n[ \t]*boundOwner = owner\n", r"\1\n"),
     "bindOwner no longer binds the new owner"),
    ("prime-no-binding", "prime_rejects", "store",
     _sub(r"(\n[ \t]*\}\n)[ \t]*boundOwner = owner\n([ \t]*let primedEpoch = epoch)", r"\1\2"),
     "prime does not bind the stored credential's owner"),
    ("prime-keeps-old-snapshot", "prime_rejects", "store",
     _sub(r"(if owner != boundOwner \{\s*epoch &\+= 1\s*)current = nil\n", r"\1"),
     "prime keeps the previous owner's snapshot"),
    ("expiry-inverted", "expiry_direction", "vm",
     _sub(r"!HomeDashboardSnapshotStore\.isDisplayable\(savedAt: savedAt, now: now\) else",
          "HomeDashboardSnapshotStore.isDisplayable(savedAt: savedAt, now: now) else"),
     "drops a snapshot that is still inside its display window"),
    ("expiry-on-live-data", "expiry_direction", "vm",
     _sub(r"guard let savedAt = snapshotSavedAt, data != nil,", "guard let savedAt = Optional(Date()), data != nil,"),
     "expireSnapshotIfStale can drop LIVE data"),
    # ── Review fixes: a degraded watchlist never overwrites a saved one ──
    ("watchlist-refusal-gone", "save_keeps_watchlist", "store",
     _sub(r"if let previous = current,\s*previous\.ownerUserId == owner,\s*Self\.isDisplayable\(savedAt: "
          r"previous\.savedAt, now: Date\(\)\),\s*data\.hasDegradedWatchlist\(comparedTo: previous\.data\) \{",
          "if false {"),
     "a degraded watchlist read overwrites a saved watchlist"),
    ("watchlist-refusal-any-owner", "save_keeps_watchlist", "store",
     _sub(r"\s*previous\.ownerUserId == owner,", ""),
     "the degraded-watchlist refusal lost `previous.ownerUserId == owner`"),
    ("watchlist-shape-any-empty-group", "save_keeps_watchlist", "models", _sub(r" && !watchlistIsGroup", ""),
     "hasDefaultEmptyWatchlist lost `!watchlistIsGroup`"),
    ("watchlist-title-second-literal", "save_keeps_watchlist", "repo",
     _sub(r"HomeDashboardData\.defaultWatchlistTitle", lambda m: '"Your Watchlist"'),
     "the repository's watchlist-title fallback is a second literal"),
    # ── Review fixes: a snapshot from an earlier ET day is dated, never "Today's" ──
    ("seed-undated", "snapshot_movers_dated", "vm",
     _sub(r"data = Self\.presentedSnapshot\(snapshot\.data, savedAt: snapshot\.savedAt, now: Date\(\)\) "
          r"\?\? snapshot\.data", "data = snapshot.data"),
     "a snapshot from an earlier day is seeded as saved"),
    ("day-in-device-zone", "snapshot_movers_dated", "vm",
     _sub(r'TimeZone\(identifier: "America/New_York"\) \?\? \.current', "TimeZone.current"),
     "the snapshot is not dated by its America/New_York day"),
    ("same-session-dated", "snapshot_movers_dated", "vm",
     _sub(r"guard savedSession != currentSession else", "guard savedSession == currentSession else"),
     "the snapshot is dated unless its session differs from now's"),
    ("relabel-every-card", "snapshot_movers_dated", "vm",
     _sub(r"scanner\.kind == \.movers \? scanner\.relabelled", "true ? scanner.relabelled"),
     "the relabel touches more than the movers card"),
    ("dated-title-says-today", "snapshot_movers_dated", "vm",
     _sub(r'"Top Movers', lambda m: "\"Today's Top Movers"),
     "the dated movers title says Today"),
    ("relabel-live-data", "snapshot_relabel_rollover", "vm",
     _sub(r"guard let savedAt = snapshotSavedAt, let shown = data,", "guard let savedAt = Optional(Date()), let shown = data,"),
     "relabelSnapshotIfDayRolledOver can relabel LIVE data"),
    ("no-foreground-relabel", "snapshot_relabel_rollover", "view", _rm("viewModel.relabelSnapshotIfDayRolledOver()"),
     "is not re-dated when the app returns to the foreground"),
    ("no-failure-relabel", "snapshot_relabel_rollover", "vm",
     _sub(r"(errorMessage = appError\.message\n.*?expireSnapshotIfStale\(\)\n)[ \t]*relabelSnapshotIfDayRolledOver\(\)\n",
          r"\1"),
     "is not re-dated after a failed load"),
    ("hero-always-today", "dated_card_drops_today", "card",
     _sub(r"scanner\.asOfDayLabel == nil\s*\?", "true ?"),
     "the movers hero says \"#1 today\" on a snapshot from an earlier day"),
    ("relabel-drops-day", "dated_card_drops_today", "models", _rm("copy.asOfDayLabel = asOfDayLabel"),
     "DailyScanner.relabelled drops the title or the day"),
    ("dated-title-full-spaces", "dated_title_fits", "vm",
     _sub(r"Top Movers\\u\{202F\}\\u\{00B7\}\\u\{2009\}", lambda m: "Top Movers \\u{00B7} "),
     "the dated title can break BEFORE its dot, or is set with full spaces"),
    ("dated-title-wide-date-gap", "dated_title_fits", "vm",
     _sub(r"MMM\\u\{202F\}d", lambda m: "MMM\\u{00A0}d"),
     "the dated movers title is wider than"),
    # ── Review fixes: requestReturningBody's DEBUG failover ──
    ("body-failover-on-cancel", "body_failover_debug", "api", _sub(r"\s*!Self\.isCancellation\(underlying\),", ""),
     "requestReturningBody's DEBUG failover lost `!Self.isCancellation(underlying)`"),
    ("body-failover-any-method", "body_failover_debug", "api",
     _sub(r"(\) async throws -> \(value: T, body: Data\) \{\s*)guard endpoint\.method\.isSafeToRetryAfterServerError "
          r"else \{ throw originalError \}\n", r"\1"),
     "the body failover lost `guard endpoint.method.isSafeToRetryAfterServerError"),
    ("body-failover-in-release", "body_failover_debug", "api",
     _sub(r"#if DEBUG\n(\s*if let apiError = error as\? APIError,)", r"\1"),
     "requestReturningBody's failover runs in Release"),
    ("body-failover-swallows", "body_failover_debug", "api",
     _sub(r"(#endif\n)\s*throw error\n(\s*\}\n\s*let value = try decodeBody)", r"\1\2"),
     "requestReturningBody no longer rethrows the original error"),
    # ── Round 3: one-token mutations that survived every guard in round 2 ──
    ("relabel-result-discarded", "snapshot_relabel_rollover", "vm",
     _sub(r"(\n[ \t]*)data = relabelled\n", r"\1_ = relabelled\n"),
     "relabelSnapshotIfDayRolledOver computes the dated snapshot and throws it away"),
    ("presented-never", "snapshot_movers_dated", "vm",
     _sub(r"\$0\.title != title", lambda m: "$0.title == title"),
     "presentedSnapshot never (or always) relabels"),
    ("seed-overridden", "snapshot_movers_dated", "vm",
     _sub(r"(\?\? snapshot\.data\n)", r"\1        data = snapshot.data\n"),
     "a second assignment overrides the dated one"),
    ("default-watchlist-or", "save_keeps_watchlist", "models",
     _sub(r"watchlist\.isEmpty && !watchlistIsGroup", "watchlist.isEmpty || !watchlistIsGroup"),
     "hasDefaultEmptyWatchlist is not the conjunction of all three"),
    # ── Round 3: dated by the SESSION the numbers describe (the backend's rule) ──
    ("dated-by-calendar-day", "snapshot_movers_dated", "vm",
     _sub(r"MarketHoursUtil\.numbersSessionDay\(at: savedAt\.addingTimeInterval\(-grace\)\)",
          "Calendar.current.startOfDay(for: savedAt)"),
     "the snapshot is not judged by the SESSION its numbers describe"),
    ("no-grace-on-the-save", "snapshot_movers_dated", "vm",
     _sub(r"numbersSessionDay\(at: savedAt\.addingTimeInterval\(-grace\)\)", "numbersSessionDay(at: savedAt)"),
     "the snapshot is not judged by the SESSION its numbers describe"),
    ("label-is-the-save-day", "snapshot_movers_dated", "vm",
     _sub(r"return formatter\.string\(from: savedSession\)", "return formatter.string(from: savedAt)"),
     "the dated title shows the save's calendar day"),
    ("relabel-not-on-activation", "snapshot_relabel_rollover", "vm",
     _sub(r"(firstLoadRetrySuspended = false\n.*?expireSnapshotIfStale\(\)\n)[ \t]*relabelSnapshotIfDayRolledOver\(\)\n",
          r"\1"),
     "is not re-dated on tab activation"),
    ("open-boundary-exclusive", "numbers_session_rule", "hours",
     _sub(r"minuteOfDay >= regularOpenMinuteOfDay", "minuteOfDay > regularOpenMinuteOfDay"),
     "a dashboard answered before the 09:30 ET open, or on a closed day, is dated as that day's"),
    ("open-at-midnight", "numbers_session_rule", "hours",
     _sub(r"regularOpenMinuteOfDay: Int = 9 \* 60 \+ 30", "regularOpenMinuteOfDay: Int = 0 * 60 + 0"),
     "the session rule's open is not 09:30 ET"),
    ("open-or-trading-day", "numbers_session_rule", "hours",
     _sub(r"regularOpenMinuteOfDay && isTradingDay", "regularOpenMinuteOfDay || isTradingDay"),
     "a dashboard answered before the 09:30 ET open, or on a closed day, is dated as that day's"),
    ("pre-open-is-today", "numbers_session_rule", "hours",
     _sub(r"(\{\s*return day\s*\}\s*)return previousTradingDay\(before: day, calendar: calendar\)", r"\1return day"),
     "anything before the open (or on a closed day) must step back to the previous trading day"),
    ("session-in-device-zone", "numbers_session_rule", "hours",
     _sub(r'(etCalendar: Calendar = \{.*?)calendar\.timeZone = TimeZone\(identifier: "America/New_York"\) \?\? \.current',
          r"\1calendar.timeZone = .current"),
     "the session rule reads the device's zone"),
    ("saturday-trades", "numbers_session_rule", "hours",
     _sub(r"if weekday == 1 \|\| weekday == 7 \{ return false \}", "if weekday == 1 { return false }"),
     "a weekend counts as a trading session"),
    ("holidays-ignored", "numbers_session_rule", "hours",
     _sub(r"return !holidays\.contains\(key\)", "return true"),
     "a holiday counts as a trading session"),
    ("step-back-forwards", "numbers_session_rule", "hours",
     _sub(r"byAdding: \.day, value: -1, to: probe", "byAdding: .day, value: 1, to: probe"),
     "previousTradingDay no longer steps BACK"),
    ("step-back-stops-on-a-closed-day", "numbers_session_rule", "hours",
     _sub(r"if isTradingDay\(probe, calendar: calendar\) \{ break \}", "break"),
     "previousTradingDay no longer steps BACK"),
    # ── Round 3: the broadened watchlist refusal, and its lapse ──
    ("watchlist-refusal-never-lapses", "save_keeps_watchlist", "store",
     _sub(r"\s*Self\.isDisplayable\(savedAt: previous\.savedAt, now: Date\(\)\),", ""),
     "the degraded-watchlist refusal never lapses"),
    ("watchlist-refusal-lapse-inverted", "save_keeps_watchlist", "store",
     _sub(r"Self\.isDisplayable\(savedAt: previous\.savedAt, now: Date\(\)\),",
          "!Self.isDisplayable(savedAt: previous.savedAt, now: Date()),"),
     "the degraded-watchlist refusal's condition changed"),
    ("watchlist-same-list-dropped", "save_keeps_watchlist", "models",
     _sub(r"return watchlistTitle == saved\.watchlistTitle && watchlistIsGroup == saved\.watchlistIsGroup",
          "return false"),
     "hasDegradedWatchlist lost the same-list rule"),
    ("watchlist-same-list-or", "save_keeps_watchlist", "models",
     _sub(r"watchlistTitle == saved\.watchlistTitle && watchlistIsGroup", "watchlistTitle == saved.watchlistTitle || watchlistIsGroup"),
     "hasDegradedWatchlist lost the same-list rule"),
    ("watchlist-default-shape-dropped", "save_keeps_watchlist", "models",
     _sub(r"if hasDefaultEmptyWatchlist \{ return true \}\n", ""),
     "hasDegradedWatchlist lost the server's degraded-read shape"),
    ("watchlist-refuses-a-full-list", "save_keeps_watchlist", "models",
     _sub(r"guard watchlist\.isEmpty, !saved\.watchlist\.isEmpty else", "guard !saved.watchlist.isEmpty else"),
     "hasDegradedWatchlist refuses more than an EMPTY watchlist"),
    # ── Round 4: one-token mutations that survived every guard in round 3 ──
    ("presented-rebuilt-fieldwise", "snapshot_movers_dated", "vm",
     _sub(r"(\n[ \t]*)return presented\n",
          r"\1return HomeDashboardData(marketStatusText: data.marketStatusText, marketIsOpen: "
          r"data.marketIsOpen, pulse: data.pulse, scanners: presented.scanners, signals: data.signals, "
          r"themes: data.themes, watchlist: [], watchlistTitle: data.watchlistTitle, "
          r"watchlistIsGroup: data.watchlistIsGroup)\n"),
     "presentedSnapshot rebuilds the dashboard field by field"),
    ("presented-relabel-discarded", "snapshot_movers_dated", "vm",
     _sub(r"presented\.scanners = data\.scanners\.map", "_ = data.scanners.map"),
     "'presented.scanners = data.scanners.map' not found"),
    ("presented-returns-original", "snapshot_movers_dated", "vm",
     _sub(r"(\n[ \t]*)return presented\n", r"\1return data\n"),
     "'return presented' not found"),
    ("seed-not-stamped", "snapshot_movers_dated", "vm",
     _sub(r"(\?\? snapshot\.data\n)[ \t]*snapshotSavedAt = snapshot\.savedAt\n", r"\1"),
     "the seed no longer stamps snapshotSavedAt"),
    ("prime-decodes-but-drops", "prime_rejects", "store",
     _sub(r"current = Snapshot\(ownerUserId: owner, savedAt: envelope\.savedAt, data: data\)", "_ = data"),
     "not found"),
]

# I1's rows carry no expected message; every A3/A4 row does.
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


def test_the_comment_stripper_actually_strips():
    """The CONTROL: every guarded token, written only in comments, must vanish."""
    prose = (
        "// HomeDashboardSnapshotStore.shared.clearForEndedSession()\n"
        "/// seedFromSnapshot()  LoadingOverlay()\n"
        "/* snapshotStore.save(body: body, data: fetched.data, epoch: snapshotEpoch)\n"
        "   lastLoadedAt = Date() */\n"
        'let url = "https://example.com"  // bindOwner(profile.id)\n'
    )
    code = _strip(prose)
    for token in ("clearForEndedSession", "seedFromSnapshot", "LoadingOverlay", "snapshotStore.save",
                  "lastLoadedAt", "bindOwner"):
        assert token not in code, f"{token!r} survived the stripper"
    assert "https://example.com" in code, "the stripper ate a URL literal"
    assert len(code.splitlines()) == len(prose.splitlines()), "the stripper must keep line structure"


# ── Backend ↔ iOS: the numbers the snapshot's safety rests on ───────────────────────

def _swift_seconds(src: str, name: str) -> int:
    m = re.search(rf"static let {name}: TimeInterval = ([\d *]+)\n", src)
    assert m, f"`{name}` is no longer a plain product literal — this scan has drifted"
    value = 1
    for factor in m.group(1).split("*"):
        value *= int(factor.strip())
    return value


def test_the_snapshot_cannot_outlive_a_provably_live_session():
    """A saved snapshot proves only that an access token was minted within ACCESS minutes
    before the save; refresh rotates both tokens. So a snapshot older than REFRESH − ACCESS
    may belong to a session the server already considers dead — it must never paint."""
    from app.config import settings

    max_age = _swift_seconds(_sources()["store"], "maxDisplayAge")
    assert max_age == 96 * 60 * 60, "owner decision 2026-10-01 is 96 h — change it there first"
    bound = (settings.REFRESH_TOKEN_EXPIRE_MINUTES - settings.ACCESS_TOKEN_EXPIRE_MINUTES) * 60
    assert max_age < bound, (
        f"maxDisplayAge {max_age}s ≥ refresh − access lifetime {bound}s: a dead session's "
        "dashboard could paint on a cold launch"
    )


def test_the_access_token_carries_the_owner_the_ios_side_reads():
    """`prime` binds the snapshot to the stored access token's `sub` (`WidgetJWT.subject`),
    and `applyProfile` then binds `/users/me`'s `id`. They must be the same string, or every
    launch reads as an account switch and deletes the snapshot it just loaded."""
    from app.core.security import create_access_token

    uid = "3f2b8c1e-7a4d-4e0b-9c55-0d1e2f3a4b5c"
    token = create_access_token({"sub": uid, "email": "owner@example.com"})
    parts = token.split(".")
    assert len(parts) == 3, "not a compact JWT"
    payload = parts[1].replace("-", "+").replace("_", "/")
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.b64decode(payload, validate=True))
    assert isinstance(claims["sub"], str) and claims["sub"] == uid
    assert isinstance(claims["exp"], int) and not isinstance(claims["exp"], bool)
    assert claims["type"] == "access"


def test_the_pulse_completeness_bar_matches_the_backend_strip():
    """`isWorthPersisting` refuses a dashboard whose equity pulse strip is short — the same
    test as the backend's degraded-TTL gate (`_equity_tile_count` vs `len(_PULSE_SYMBOLS)`).
    If the backend's strip changes size, the iOS bar must move with it: too high and no
    snapshot is ever saved, too low and a degraded strip overwrites a good one."""
    from app.services import home_dashboard_service as svc

    m = re.search(r"nonisolated static let expectedEquityPulseTiles = (\d+)", _sources()["models"])
    assert m, "expectedEquityPulseTiles moved — this scan has drifted"
    assert int(m.group(1)) == len(svc._PULSE_SYMBOLS)
    assert all(cfg["type"] != "crypto" for cfg in svc._PULSE_SYMBOLS)
    assert svc._CRYPTO_PULSE_SYMBOL["type"] == "crypto", "the crypto tile is what the count excludes"


def test_the_degraded_watchlist_title_matches_the_backend():
    """`hasDefaultEmptyWatchlist` recognises the server's degraded watchlist by its DEFAULT
    title. If the backend renames it, the refusal silently stops matching and a degraded read
    overwrites a saved watchlist again."""
    from app.services import home_dashboard_service as svc

    m = re.search(r'nonisolated static let defaultWatchlistTitle = "([^"\\]*)"', _sources()["models"])
    assert m, "defaultWatchlistTitle moved — this scan has drifted"
    assert m.group(1) == svc._WATCHLIST_DEFAULT_TITLE


@pytest.mark.asyncio
async def test_a_failed_watchlist_read_is_the_shape_the_snapshot_refuses():
    """The other half of the contract: a watchlist read that fails or times out on the server
    answers (default title, not a group, no tiles) — exactly `hasDefaultEmptyWatchlist`."""
    from app.services import home_dashboard_service as svc

    service = svc.HomeDashboardService.__new__(svc.HomeDashboardService)  # no FMP client needed

    async def _read_failed(user_id):
        raise RuntimeError("supabase read failed")

    service._build_watchlist = _read_failed
    title, is_group, tiles = await service._get_watchlist_guarded("3f2b8c1e-7a4d-4e0b-9c55-0d1e2f3a4b5c")
    assert (title, is_group, tiles) == (svc._WATCHLIST_DEFAULT_TITLE, False, [])


def _swift_holiday_table() -> set[tuple[int, int, int]]:
    m = re.search(r"nonisolated private static let holidays: Set<String> = \[(.*?)\]", _sources()["hours"], re.S)
    assert m, "MarketHoursUtil.holidays moved — this scan has drifted"
    days = {(int(y), int(mo), int(d)) for y, mo, d in re.findall(r'"(\d{4})-(\d{2})-(\d{2})"', m.group(1))}
    assert days, "MarketHoursUtil.holidays parsed empty — this scan has drifted"
    return days


def _swift_session_constants() -> tuple[int, float]:
    hours = _hours(_sources())
    m = _OPEN_MINUTE.search(hours)
    assert m, "regularOpenMinuteOfDay moved — this scan has drifted"
    g = re.search(r"nonisolated static let numbersSessionGraceSeconds: TimeInterval = (\d+)\n", hours)
    assert g, "numbersSessionGraceSeconds is no longer a plain literal — this scan has drifted"
    return int(m.group(1)) * 60 + int(m.group(2)), float(g.group(1))


def test_the_swift_holiday_table_matches_the_backend():
    """The snapshot's session rule steps over the holidays in `MarketHoursUtil.holidays` (its
    comment says "keep both in sync"); a closure the backend knows and iOS does not would date a
    holiday save by the holiday instead of the session before it."""
    from app.utils import market_hours

    assert _swift_holiday_table() == set(market_hours.US_MARKET_HOLIDAYS)


def test_the_swift_early_close_table_matches_the_backend():
    """`MarketHoursUtil.earlyCloses` gates the detail screens' price poll and session badge at the
    13:00 half-day bell. It sits beside `holidays` (pinned above), and extending one table without
    the other left 2028's two half-days reading "Open" until 20:00 ET while the backend's
    `session_phase` said closed."""
    from app.utils import market_hours

    m = re.search(r"private static let earlyCloses: Set<String> = \[(.*?)\]", _sources()["hours"], re.S)
    assert m, "MarketHoursUtil.earlyCloses moved — this scan has drifted"
    days = {(int(y), int(mo), int(d)) for y, mo, d in re.findall(r'"(\d{4})-(\d{2})-(\d{2})"', m.group(1))}
    assert days, "MarketHoursUtil.earlyCloses parsed empty — this scan has drifted"
    assert days == set(market_hours.US_MARKET_EARLY_CLOSES)


def test_the_numbers_session_grace_matches_the_backend():
    """iOS shifts the save and the clock back by the backend's `_SESSION_GRACE_SECONDS`, exactly as
    `_same_numbers_session` does: an answer in the first minutes after the open may carry pre-open
    numbers. A different grace dates those minutes differently from the server that built them."""
    from app.services import home_dashboard_service as svc

    _, grace = _swift_session_constants()
    assert grace == float(svc._SESSION_GRACE_SECONDS), (
        f"MarketHoursUtil.numbersSessionGraceSeconds {grace:.0f} s != backend _SESSION_GRACE_SECONDS "
        f"{svc._SESSION_GRACE_SECONDS} s — mirror the backend value in Swift"
    )


def _swift_rule(open_minute: int, holidays: set[tuple[int, int, int]]):
    """`MarketHoursUtil.numbersSessionDay`, transliterated with the constants READ from the Swift
    source (the guard `numbers_session_rule` pins the Swift statements this mirrors)."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")

    def trading(d) -> bool:
        return d.weekday() < 5 and (d.year, d.month, d.day) not in holidays

    def session(ts: float):
        from datetime import datetime, timezone

        local = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(eastern)
        day = local.date()
        if local.hour * 60 + local.minute >= open_minute and trading(day):
            return day
        probe = day
        for _ in range(10):
            probe -= timedelta(days=1)
            if trading(probe):
                break
        return probe

    return session


def _et(y: int, mo: int, d: int, h: int, mi: int, sec: int = 0) -> float:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime(y, mo, d, h, mi, sec, tzinfo=ZoneInfo("America/New_York")).timestamp()


def test_the_swift_session_rule_agrees_with_the_backend(monkeypatch):
    """Every day 2025-2028 at the instants that matter (overnight, pre-market, the open's
    minute either side, the session, after-hours, the 20:00 close of after-hours), plus the
    DST and holiday weeks: the Swift rule names the same session as `_numbers_session`."""
    from datetime import date, timedelta

    from app.services import home_dashboard_service as svc
    from app.utils import market_hours

    # A closure another test LEARNED at run time is process-local backend state iOS cannot know.
    monkeypatch.setattr(market_hours, "_OBSERVED_CLOSURES", set())
    open_minute, _ = _swift_session_constants()
    swift = _swift_rule(open_minute, _swift_holiday_table())
    times = [(0, 0, 0), (3, 59, 59), (4, 0, 0), (7, 0, 0), (9, 29, 0), (9, 29, 59), (9, 30, 0),
             (9, 31, 0), (12, 0, 0), (13, 5, 0), (16, 0, 0), (19, 59, 0), (20, 0, 0), (23, 59, 59)]
    day = date(2025, 1, 1)
    mismatches = []
    while day <= date(2028, 12, 31):
        for h, mi, sec in times:
            ts = _et(day.year, day.month, day.day, h, mi, sec)
            if swift(ts) != svc._numbers_session(ts):
                mismatches.append((day.isoformat(), f"{h:02d}:{mi:02d}:{sec:02d}", swift(ts), svc._numbers_session(ts)))
        day += timedelta(days=1)
    assert not mismatches, f"the Swift session rule disagrees with the backend: {mismatches[:10]}"


def test_the_snapshot_is_dated_exactly_when_the_backend_calls_it_another_session(monkeypatch):
    """`earlierMarketDay(savedAt:now:)` — the Swift rule with both instants shifted back by the grace
    — relabels exactly when `_same_numbers_session` says no, and labels with the session the SAVE's
    numbers describe. The reviewer's cases are named; a sweep of save ages covers the rest."""
    from datetime import date

    from app.services import home_dashboard_service as svc
    from app.utils import market_hours

    monkeypatch.setattr(market_hours, "_OBSERVED_CLOSURES", set())
    open_minute, grace = _swift_session_constants()
    swift = _swift_rule(open_minute, _swift_holiday_table())

    def earlier_market_day(saved: float, now: float):
        saved_session = swift(saved - grace)
        return None if saved_session == swift(now - grace) else saved_session

    named = [
        # Monday 07:00 save (Friday's moves) viewed after Monday's open → Friday's date.
        (_et(2026, 9, 28, 7, 0), _et(2026, 9, 28, 10, 0), date(2026, 9, 25)),
        # Monday 01:00 save viewed after the open → Friday.
        (_et(2026, 9, 28, 1, 0), _et(2026, 9, 28, 10, 0), date(2026, 9, 25)),
        # Sunday save viewed Monday after the open → Friday's session, never "Sep 27".
        (_et(2026, 9, 27, 12, 0), _et(2026, 9, 28, 10, 0), date(2026, 9, 25)),
        # Sunday save viewed Monday pre-market → the same (Friday's) session: undated.
        (_et(2026, 9, 27, 12, 0), _et(2026, 9, 28, 8, 0), None),
        # Friday 16:02 save viewed Saturday → undated (no session since).
        (_et(2026, 9, 25, 16, 2), _et(2026, 9, 26, 11, 0), None),
        # A save inside the grace after the open may hold pre-open numbers → dated by Friday.
        (_et(2026, 9, 28, 9, 31), _et(2026, 9, 28, 12, 0), date(2026, 9, 25)),
        # A save after the grace is Monday's → undated on Monday.
        (_et(2026, 9, 28, 9, 33), _et(2026, 9, 28, 12, 0), None),
        # Good Friday 2026-04-03 save viewed Monday after the open → Thursday's session.
        (_et(2026, 4, 3, 12, 0), _et(2026, 4, 6, 10, 0), date(2026, 4, 2)),
        # Across the 2026-11-01 DST change: Friday 18:00 save viewed Monday 10:00 → Friday.
        (_et(2026, 10, 30, 18, 0), _et(2026, 11, 2, 10, 0), date(2026, 10, 30)),
    ]
    for saved, now, expected in named:
        assert earlier_market_day(saved, now) == expected
        assert (expected is None) == svc._same_numbers_session(saved, now)
        if expected is not None:
            assert expected == svc._numbers_session(saved - svc._SESSION_GRACE_SECONDS)

    ages = [60, 149, 151, 300, 3_600, 6 * 3_600, 86_400, 50 * 3_600, 72 * 3_600, 95 * 3_600]
    starts = [_et(2026, m, d, h, mi) for m, d in ((3, 6), (3, 9), (4, 2), (4, 3), (9, 25), (9, 28), (11, 25),
                                                   (11, 27), (12, 24), (12, 28))
              for h, mi in ((0, 0), (4, 0), (9, 27), (9, 29), (9, 30), (9, 33), (13, 30), (16, 30), (21, 0))]
    for saved in starts:
        for age in ages:
            now = saved + age
            label = earlier_market_day(saved, now)
            assert (label is None) == svc._same_numbers_session(saved, now), (saved, age)
            if label is not None:
                assert label == svc._numbers_session(saved - svc._SESSION_GRACE_SECONDS), (saved, age)


def test_the_body_cap_leaves_room_for_a_real_dashboard():
    """`maxBodyBytes` must sit far above a real response (tens of KB) or nothing is saved."""
    m = re.search(r"nonisolated static let maxBodyBytes = (\d+) \* (\d+)", _sources()["store"])
    assert m, "maxBodyBytes moved — this scan has drifted"
    assert int(m.group(1)) * int(m.group(2)) >= 256 * 1024


def test_the_preflight_skew_is_small_against_the_access_token_life():
    """The pre-flight refreshes a token within `proactiveRefreshSkew` of its `exp`. Large enough
    to cover a send; a sliver of the access token's life, or every request in the token's last
    stretch would refresh early and burn refresh-limiter budget."""
    from app.config import settings

    skew = _swift_seconds(_sources()["api"], "proactiveRefreshSkew")
    life = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert 10 <= skew <= life / 100, (
        f"proactiveRefreshSkew {skew}s is outside [10 s, 1% of the {life}s access token life]"
    )


# The server's per-section guards that `get_dashboard` gathers behind (each one is the most a
# section may add to the response), and the users-row read, which has no guard of its own.
_HOME_SECTION_GUARDS = {
    "app.services.home_dashboard_service": ("_PULSE_BUILD_TIMEOUT_SECONDS", "_SCANNER_BUILD_TIMEOUT_SECONDS",
                                            "_WATCHLIST_BUILD_TIMEOUT_SECONDS", "_THEMES_BUILD_TIMEOUT_SECONDS"),
    "app.services.signals_service": ("_SIGNALS_BUILD_TIMEOUT_SECONDS",),
    "app.services.trillion_club_service": ("_GROUP_TIMEOUT_SECONDS",),
}
_USERS_ROW_ALLOWANCE_SECONDS = 6  # the unguarded users-row read: 5.4 s seen in production


def test_the_home_dashboard_timeout_outlasts_the_servers_slowest_guard():
    """`.getHomeDashboard` cuts a stalled flow at 15 s instead of 30 s. It must still outlast the
    slowest answer the server can legitimately give — its slowest section guard plus the
    users-row read — or a slow-but-healthy dashboard is cut off and retried forever."""
    import importlib

    guards = {}
    for module_name, names in _HOME_SECTION_GUARDS.items():
        module = importlib.import_module(module_name)
        for name in names:
            value = getattr(module, name)  # AttributeError = a guard was renamed: re-derive
            assert isinstance(value, (int, float)) and value > 0, f"{module_name}.{name} = {value!r}"
            guards[f"{module_name}.{name}"] = float(value)
    slowest = max(guards.values())
    timeout = _home_timeout_seconds(_sources()["endpoint"])
    assert timeout >= slowest + _USERS_ROW_ALLOWANCE_SECONDS, (
        f".getHomeDashboard times out at {timeout}s, below the slowest server guard "
        f"({slowest}s, {max(guards, key=guards.get)}) + the users-row read"
    )
    assert timeout < 30, "no better than the 30 s default"
