"""The Research tab must reload when auth changes, and must not call a signed-in user signed out.

THE BUG THIS PINS. `ResearchViewModel.requiresSignInForReports` is a SNAPSHOT written inside
`loadReports()`, and the ViewModel's only unconditional load is in `init`. All five tabs mount
eagerly in one opacity-switched `ZStack`, so that `init` runs at launch — while session restore
is still in flight and `AppActions.isSignedIn` (which is `status == .authenticated` ONLY) is
still `false`. The flag latched `true`, and the live view had exactly one lifecycle hook
(`.onAppear { viewModel.selectedTab = ... }`) — no `.task(id:)`, no auth observer — so nothing
ever recomputed it. A signed-in user saw "Sign in to see your analyses" for the whole app run,
directly beneath their own loaded avatar. Only a manual pull-to-refresh cleared it.

It also had no cure for the most direct path: tapping Sign In ON this tab. The sheet dismisses
itself on `.authenticated`, and `AppState.onAuthenticated()`'s fan-out hydrates credits,
settings and the four Learn stores — but nothing research-related.

TWO INVARIANTS, because they fail independently:
  1. the live view re-runs a load when the tab becomes active (heals the launch race), and
  2. it re-runs one when auth transitions to `.authenticated` (heals in-tab sign-in).

⚠️ Scoped to `ResearchViewWithBinding` in `ContentView.swift`. `ResearchView.swift`'s
`ResearchContentView` is PREVIEW-ONLY (its only two references are its own declaration and its
own `#Preview`), and it *already* had richer lifecycle hooks than the live screen — which is
precisely how this went unnoticed. Asserting against the whole file, or against the preview
file, would pass while the live screen stayed broken.

Per the `project_source_scan_guard_vacuity` lesson: brace-bound the declaration, strip comments
(this fix's own explanatory comments name every token asserted below), and never search forward
past the block for the token being asserted.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_CONTENT_VIEW = _REPO / "frontend/ios/ios/ContentView.swift"
_VIEW_MODEL = _REPO / "frontend/ios/ios/ViewModels/ResearchViewModel.swift"
_LIST_SECTION = _REPO / "frontend/ios/ios/Views/Organisms/ReportsListSection.swift"
_APP_ACTIONS = _REPO / "frontend/ios/ios/Core/State/AppActions.swift"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails, so a comment MENTIONING a token never satisfies
    an assertion about the token being present in code."""
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of a declaration, comments stripped."""
    start = src.find(header)
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


def _live_view() -> str:
    return _decl_block(_read(_CONTENT_VIEW), "struct ResearchViewWithBinding: View")


# ── Invariant 1: heals the launch race ─────────────────────────────────────────


def test_live_research_view_reloads_when_the_tab_becomes_active():
    block = _live_view()
    assert "isActiveTab" in block, (
        "ResearchViewWithBinding does not read `isActiveTab`. ContentView injects it for this "
        "tab already — reading it is what lets a load that raced session restore heal on the "
        "next visit, the way HomeDashboardView and UpdatesView do."
    )
    assert re.search(r"\.task\(id:\s*isActiveTab\s*\)", block), (
        "ResearchViewWithBinding has no `.task(id: isActiveTab)`. Without it the only "
        "unconditional load is the one in `ResearchViewModel.init`, which runs at launch while "
        "auth is still `.restoring`."
    )


# ── Invariant 2: heals in-tab sign-in ──────────────────────────────────────────


def test_live_research_view_reloads_on_an_auth_transition():
    block = _live_view()
    assert "reloadOnIdentityChange" in block, (
        "ResearchViewWithBinding does not react to an identity change. Signing in FROM this "
        "tab dismisses the sheet and leaves the signed-out empty state on screen: nothing in "
        "`onAuthenticated()`'s fan-out reloads research."
    )


# ── The same defect existed on EVERY tab root ──────────────────────────────────

# view file → (declaration header of the view that OWNS the @StateObject, ViewModel file)
_TAB_ROOTS = {
    "Research": (
        _CONTENT_VIEW,
        "struct ResearchViewWithBinding: View",
        _REPO / "frontend/ios/ios/ViewModels/ResearchViewModel.swift",
    ),
    "Home": (
        _REPO / "frontend/ios/ios/Views/Screens/HomeDashboardView.swift",
        "struct HomeDashboardView: View",
        _REPO / "frontend/ios/ios/ViewModels/HomeDashboardViewModel.swift",
    ),
    "Updates": (
        _REPO / "frontend/ios/ios/Views/Screens/UpdatesView.swift",
        "struct UpdatesView: View",
        _REPO / "frontend/ios/ios/ViewModels/UpdatesViewModel.swift",
    ),
    # NOTE: the LIVE Tracking root is `TrackingContentViewWithBinding`.
    # `TrackingContentView` in the same file is preview-only (its sole reference is the
    # `#Preview` at the bottom) — the same live/preview trap as ResearchView.swift.
    "Tracking": (
        _REPO / "frontend/ios/ios/Views/Screens/TrackingView.swift",
        "struct TrackingContentViewWithBinding: View",
        _REPO / "frontend/ios/ios/ViewModels/TrackingViewModel.swift",
    ),
}


@pytest.mark.parametrize("tab", sorted(_TAB_ROOTS))
def test_every_tab_root_reloads_when_the_identity_changes(tab):
    """Tabs are opacity-mounted, so a ViewModel is built once at launch and never rebuilt, and
    `AppState.discardDataForEndedSession()` resets the Learn stores / WhaleService / settings /
    push registration but NO tab ViewModel. Without this, signing out left the previous
    account's watchlist, holdings and reports on screen for whoever used the device next."""
    view_file, header, _ = _TAB_ROOTS[tab]
    block = _decl_block(_read(view_file), header)
    assert "reloadOnIdentityChange" in block, (
        f"{tab}'s live root ({header}) does not use `.reloadOnIdentityChange`. Its cached data "
        "survives a sign-in AND a sign-out, because nothing else clears a tab ViewModel."
    )


@pytest.mark.parametrize("tab", sorted(_TAB_ROOTS))
def test_every_tab_view_model_has_an_identity_reset(tab):
    """The handler must CLEAR before it reloads — the refetch can be slow or fail, and the
    previous account's data must not sit on screen while it does."""
    _, _, vm_file = _TAB_ROOTS[tab]
    block = _decl_block(_read(vm_file), "func reloadForIdentityChange()")
    assert block.strip(), f"{tab}'s ViewModel has an empty reloadForIdentityChange()"
    assert re.search(r"=\s*(\[\]|nil|false)", block), (
        f"{tab}'s reloadForIdentityChange() reloads without clearing identity-scoped state "
        "first, so the previous account's data stays on screen during the refetch."
    )


def test_updates_identity_reset_clears_the_hasLoadedOnce_latch():
    """`loadIfNeeded()` early-returns on `hasLoadedOnce`, which latches on first success and is
    never otherwise cleared — the hardest of the four latches. A reset that forgets it is a
    no-op for the whole process."""
    block = _decl_block(_read(_TAB_ROOTS["Updates"][2]), "func reloadForIdentityChange()")
    assert "hasLoadedOnce = false" in block, (
        "UpdatesViewModel.reloadForIdentityChange() does not clear `hasLoadedOnce`, so "
        "`loadIfNeeded()` keeps early-returning and the feed is never refetched."
    )


def test_the_modifier_ignores_restoring_but_fires_on_sign_out():
    """`.restoring` deliberately disarms the client token. Reloading there would refetch as the
    GUEST and replace good account data with guest data on a transient network blip."""
    block = _decl_block(
        _read(_REPO / "frontend/ios/ios/Views/Modifiers/ReloadOnIdentityChange.swift"),
        "static func isIdentityChange(",
    )
    assert "case .restoring" in block and "return false" in block, (
        "the modifier no longer excludes `.restoring`"
    )
    assert "case .unauthenticated" in block, (
        "the modifier does not fire on sign-out — that is the direction that leaks the "
        "previous account's data to the next person using the device"
    )


# ── The ViewModel side ─────────────────────────────────────────────────────────


def test_the_staleness_guard_cannot_suppress_the_healing_reload():
    """`loadIfStale` must not treat a signed-out / reconnecting pass as a completed load, or the
    5-minute window would swallow the very reload that clears the latch.

    Reads `performBackendLoad`, not `loadBackendData`: the latter is now a single-flight
    wrapper that joins an in-flight load, and the fan-out plus the freshness stamp live in the
    former. The assertion is unchanged — only the function that owns the logic moved.
    """
    block = _decl_block(_read(_VIEW_MODEL), "private func performBackendLoad()")
    assert "lastLoadedAt" in block, "performBackendLoad never records a load timestamp"
    assert re.search(
        r"if\s+!requiresSignInForReports\s*&&\s*!isReconnectingReports\s*\{", block
    ), (
        "performBackendLoad marks the load fresh unconditionally. A signed-out or mid-restore "
        "pass would then be 'fresh' for 5 minutes and `loadIfStale()` would skip the reload "
        "that heals the sign-in latch."
    )


def test_the_research_load_is_single_flight():
    """Tab activation and the identity-change reload land within milliseconds of each other on a
    signed-in launch. `lastLoadedAt` cannot collapse them — it is only stamped once a load
    COMPLETES — so without an in-flight join both fan out four requests each."""
    block = _decl_block(_read(_VIEW_MODEL), "private func loadBackendData()")
    assert "loadTask" in block and "await running.value" in block, (
        "loadBackendData no longer joins an in-flight load. Concurrent triggers will each "
        "issue their own /research/reports + /users/me/credits + /trending + /personas."
    )


def test_the_research_view_model_does_not_load_in_init():
    """`ContentView` opacity-mounts all five tabs, so a load in `init` is launch traffic for a
    tab the user may never open — and it goes out before session restore has armed the token,
    which for a `.guestAllowed` endpoint means it is ANSWERED as the per-install guest."""
    block = _decl_block(_read(_VIEW_MODEL), "init(prefilledTicker: String? = nil")
    assert "loadBackendData()" not in block, (
        "ResearchViewModel.init loads again. The view's `.task(id: isActiveTab)` owns the "
        "first-visit load; see UpdatesViewModel.init for the rule."
    )


def test_reconnecting_is_distinguished_from_signed_out():
    block = _decl_block(_read(_VIEW_MODEL), "func loadReports()")
    assert "isRestoringSession" in block, (
        "loadReports collapses `.restoring` into signed-out. A stored-but-unarmed credential is "
        "not a signed-out user — see .claude/rules/auth.md §5."
    )
    assert re.search(r"requiresSignInForReports\s*=\s*!reconnecting", block), (
        "the two states are not mutually exclusive; a reconnecting user could still be shown "
        "the sign-in prompt"
    )


def test_app_actions_exposes_the_restoring_query():
    src = _strip_comments(_read(_APP_ACTIONS))
    assert "var isRestoringSession" in src
    assert "hasUnusedStoredCredential" in src, (
        "isRestoringSession must derive from AppState.hasUnusedStoredCredential — the single "
        "definition of 'a credential is stored but not armed'"
    )


# ── The view side ──────────────────────────────────────────────────────────────


def test_reconnecting_state_wins_over_the_sign_in_prompt():
    src = _read(_LIST_SECTION)
    block = _decl_block(src, "var body: some View")
    recon = block.find("isReconnecting")
    signed_out = block.find("requiresSignIn")
    assert recon != -1, "ReportsListSection.body does not branch on `isReconnecting`"
    assert signed_out != -1, "ReportsListSection.body no longer branches on `requiresSignIn`"
    assert recon < signed_out, (
        "the `requiresSignIn` branch is evaluated BEFORE `isReconnecting`, so a reconnecting "
        "user still gets 'Sign in to see your analyses'"
    )


def test_the_reconnecting_state_offers_no_sign_in_button():
    """`AppState.requestSignIn` declines to prompt while a restore is pending, so a CTA here
    would be inert — and telling a signed-in user to sign in is the bug being fixed."""
    block = _decl_block(_read(_LIST_SECTION), "private var reconnectingState: some View")
    assert "Sign In" not in block, "reconnectingState offers a Sign In button"
    assert "onSignIn" not in block, "reconnectingState wires the sign-in action"
    assert "Reconnecting" in block, "reconnectingState does not say what it is doing"


def test_both_call_sites_pass_the_reconnecting_flag():
    """The preview-only ResearchView.swift is kept in step deliberately: it drifting AHEAD of the
    live screen is how the missing lifecycle hooks stayed invisible."""
    for path in (_CONTENT_VIEW, _REPO / "frontend/ios/ios/Views/Screens/ResearchView.swift"):
        src = _strip_comments(_read(path))
        assert "isReconnecting: viewModel.isReconnectingReports" in src, (
            f"{path.name} constructs ReportsListSection without the reconnecting flag"
        )


# ── Anti-vacuity ───────────────────────────────────────────────────────────────


def test_the_scan_reads_the_live_view_not_the_preview_one():
    block = _live_view()
    assert len(block) > 800, f"ResearchViewWithBinding block is only {len(block)} chars — drifted"
    assert "ReportsListSection" in block or "reportsTabContent" in block

    # The preview-only view must NOT be what we measured.
    research_view = _read(_REPO / "frontend/ios/ios/Views/Screens/ResearchView.swift")
    assert "ResearchContentView" in research_view
    assert "ResearchContentView" not in block


def test_comment_only_mentions_do_not_satisfy_the_assertions():
    """The fix's own comments name `.task(id: isActiveTab)` and `auth.status`. If comment
    stripping regressed, every assertion above would pass on prose alone."""
    stripped = _strip_comments("// .task(id: isActiveTab)\n// appState.auth.status\nlet x = 1")
    assert "isActiveTab" not in stripped
    assert "auth.status" not in stripped
    assert "let x = 1" in stripped
