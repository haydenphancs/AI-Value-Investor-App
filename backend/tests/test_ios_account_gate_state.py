"""Source-scan guards for the account-gate empty state on every sign-in-gated tab.

TestFlight, build 1.0 (7): *"Just the sign in button as in reports if users don't sign in.
Apply for the rest."* Home answered a refused load with its generic NETWORK-failure banner —
an orange wifi-exclamation glyph and the sentence "Sign in to use this feature." — over a
completely blank page, with nothing to tap. Research › Reports had the deliberate version
(glyph, headline, subtitle, Sign In button); Tracking › Alerts had a third shape; Updates had
a fourth, whose **Try Again** button re-fired a request `APIClient` refuses before it leaves
the device.

The mechanism, which is what these tests actually pin: every tab endpoint is `.signInRequired`,
so an unarmed caller is refused PRE-FLIGHT and the refusal arrives as `AppError.signInRequired`.
The moment a ViewModel flattens that to `AppError.from(error).message`, the view can no longer
tell "you need an account" from "the network died" and renders the wrong affordance. So each
gated LOAD now owns a pair of FLAGS, set from that typed refusal and never re-derived from a
string.

⚠️ Set from the OUTCOME, never from a pre-flight `auth.status` read. The first version of this
pass used `guard AppActions.shared.isSignedIn` up front, the way `ResearchViewModel` does, and
the adversarial review measured what it cost: on every signed-in cold launch
`primeStoredCredential` arms the token while the status still reads `.restoring`, so the guard
refused requests that would have succeeded and Home showed "Reconnecting…" instead of the
dashboard. `test_no_load_decides_the_gate_from_auth_status` pins that it stays gone.

⚠️ And the half that is easiest to get wrong: **`.restoring` is not signed out.** A stored
credential that has not been validated yet means the user IS signed in, and
`AppState.requestSignIn` deliberately declines to prompt in that window, so a Sign In button
there is both a false statement and inert (auth.md §5). Every surface must therefore check
`isReconnecting` BEFORE `requiresSignIn`, and `AccountGateEmptyState.Mode.reconnecting` carries
no closure so the button cannot be wired into it at all.

Comments are stripped and every scan is brace-bound to the declaration it checks
(`.claude/rules/testing.md` §3) — the explanatory comments beside each fix name the very tokens
these tests look for, so an un-stripped scan would pass on prose after a revert.
"""

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"

_MOLECULE = _IOS / "Views/Molecules/AccountGateEmptyState.swift"

_HOME_VIEW = _IOS / "Views/Screens/HomeDashboardView.swift"
_HOME_VM = _IOS / "ViewModels/HomeDashboardViewModel.swift"
_UPDATES_VIEW = _IOS / "Views/Screens/UpdatesView.swift"
_UPDATES_VM = _IOS / "ViewModels/UpdatesViewModel.swift"
_TRACKING_VIEW = _IOS / "Views/Screens/TrackingView.swift"
_TRACKING_VM = _IOS / "ViewModels/TrackingViewModel.swift"
_REPORTS_LIST = _IOS / "Views/Organisms/ReportsListSection.swift"
_LEARN_VIEW = _IOS / "Views/Screens/LearnView.swift"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails, so a comment MENTIONING a token never
    satisfies an assertion about the token being present in code."""
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
                return _strip_comments(src[open_brace:i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


def _call_block(src: str, header: str) -> str:
    """The parenthesised argument list of a call, comments stripped."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_paren = src.index("(", start)
    depth = 0
    for i in range(open_paren, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_paren:i + 1])
    pytest.fail(f"unbalanced parens after {header!r}")


# The LIVE declaration per surface, the declaration INSIDE it that holds the gate branches,
# and the flag pair that surface reads.
#
# ⚠️ Tracking's live root is `TrackingContentViewWithBinding`; `TrackingContentView` in the
# same file is preview-only, and "fixing" that one is the live/preview trap
# `test_ios_tabs_reload_on_identity_change.py` records for this file.
#
# The inner declaration is needed because `ReportsListSection` DECLARES `requiresSignIn` and
# `isReconnecting` as properties, in that order — a struct-wide scan would read the
# declaration order and prove nothing about the branch order, which is the whole invariant.
_GATED_VIEWS = {
    "Home": (_HOME_VIEW, "struct HomeDashboardView: View", "private var content: some View",
             "isReconnecting", "requiresSignIn"),
    "Updates": (_UPDATES_VIEW, "struct UpdatesView: View", "var body: some View",
                "isReconnecting", "requiresSignIn"),
    "Tracking/Assets": (_TRACKING_VIEW, "struct AssetsTabContent: View", "var body: some View",
                        "assetsIsReconnecting", "assetsRequiresSignIn"),
    "Tracking/Whales": (_TRACKING_VIEW, "struct WhalesTabContent: View", "var body: some View",
                        "whalesIsReconnecting", "whalesRequiresSignIn"),
    "Reports": (_REPORTS_LIST, "struct ReportsListSection: View", "var body: some View",
                "isReconnecting", "requiresSignIn"),
}

_VM_CLASS = {
    _HOME_VM: "final class HomeDashboardViewModel: ObservableObject",
    _UPDATES_VM: "final class UpdatesViewModel: ObservableObject",
    _TRACKING_VM: "class TrackingViewModel: ObservableObject",
}

# Every load that can be REFUSED for want of an armed token, and the flag pair it owns.
#
# Per LOAD, not per ViewModel. The first version of this file scanned whole classes, and the
# adversarial review showed that deleting every guard from `loadTrackingFeed` left the suite
# green — `loadWhaleList`, in the same class, still carried the tokens.
_GATED_LOADS = {
    "Home": (_HOME_VM, "private func performLoad() async",
             "isReconnecting", "requiresSignIn"),
    "Updates": (_UPDATES_VM, "private func loadFeed(for tab: NewsFilterTab, force: Bool) async",
                "isReconnecting", "requiresSignIn"),
    "Tracking/Assets": (_TRACKING_VM, "private func loadTrackingFeed() async -> Bool",
                        "assetsIsReconnecting", "assetsRequiresSignIn"),
    "Tracking/Whales": (_TRACKING_VM, "private func loadWhaleList(retryCount: Int = 3) async",
                        "whalesIsReconnecting", "whalesRequiresSignIn"),
}


def _load_block(name: str) -> str:
    path, header, _, _ = _GATED_LOADS[name]
    return _decl_block(_decl_block(_read(path), _VM_CLASS[path]), header)


def _refusal_branch(block: str) -> str:
    """The CLASSIFYING `if case .signInRequired = appError` arm of a catch.

    Anchored on `= appError` deliberately: `loadWhaleList` also has an in-loop
    `if case .signInRequired = AppError.from(error) { break }`, which comes FIRST and is not
    where the gate is decided."""
    at = block.find("if case .signInRequired = appError")
    assert at != -1, "no typed-refusal branch in this load"
    brace = block.index("{", at)
    depth = 0
    for i in range(brace, len(block)):
        if block[i] == "{":
            depth += 1
        elif block[i] == "}":
            depth -= 1
            if depth == 0:
                return block[at:i + 1]
    pytest.fail("unbalanced braces in the refusal branch")


# ── The ViewModels keep the TYPED state ────────────────────────────────────────


@pytest.mark.parametrize("load", sorted(_GATED_LOADS), ids=sorted(_GATED_LOADS))
def test_every_gate_flag_is_observable(load):
    """`@Published` is the load-bearing half. A plain `var` compiles, reads correctly from a
    debugger, and never re-renders the view — so the gate would paint once at whatever the
    first load decided and never heal on screen when the session does."""
    path, _, recon, signed = _GATED_LOADS[load]
    cls = _decl_block(_read(path), _VM_CLASS[path])
    for flag in (signed, recon):
        assert re.search(rf"@Published[^\n]*var {flag}\b", cls), (
            f"{load}: `{flag}` is not an observable property, so the view either cannot see "
            "the gate at all or sees it once and never updates when the session heals"
        )


@pytest.mark.parametrize("load", sorted(_GATED_LOADS), ids=sorted(_GATED_LOADS))
def test_each_load_classifies_the_typed_refusal(load):
    """The refusal arrives TYPED (`APIClient.buildRequest` throws `APIError.authRequired` →
    `AppError.signInRequired`). Flattening it to `.message` is the whole bug: the view can no
    longer tell "you need an account" from "the network died"."""
    _, _, recon, signed = _GATED_LOADS[load]
    branch = _refusal_branch(_load_block(load))
    assert "AppActions.shared.isRestoringSession" in branch, (
        f"{load} collapses 'reconnecting' into 'signed out', so a user whose session is merely "
        "being restored is offered a Sign In button that requestSignIn refuses to act on"
    )
    assert re.search(rf"{recon}\s*=\s*reconnecting", branch), (
        f"{load}'s refusal does not set its own reconnecting flag"
    )
    assert re.search(rf"{signed}\s*=\s*!reconnecting", branch), (
        f"{load}'s refusal does not set its own signed-out flag"
    )


@pytest.mark.parametrize("load", sorted(_GATED_LOADS), ids=sorted(_GATED_LOADS))
def test_no_load_decides_the_gate_from_auth_status(load):
    """The regression the adversarial review found in this pass's FIRST version, measured on
    the simulator: every signed-in cold launch showed "Reconnecting…" instead of the dashboard.

    `AppActions.shared.isSignedIn` is `status == .authenticated`, but `primeStoredCredential`
    ARMS the token while the status still reads `.restoring` — AppState documents that ordering
    as load-bearing. A pre-flight status guard therefore refused requests that would have
    succeeded. The only honest answer to "is a token armed?" is APIClient's own refusal, and
    it costs nothing: the refused call never leaves the device.

    The fix's comments name `AppActions.shared.isSignedIn` to explain why it is NOT used, so
    this is also the scan where comment-stripping is doing real work."""
    block = _load_block(load)
    assert "AppActions.shared.isSignedIn" not in block, (
        f"{load} decides the gate from auth.status again. On every signed-in cold launch the "
        "token is armed while the status reads .restoring, so this refuses a request that "
        "would have succeeded and shows Reconnecting over a dashboard that was on its way"
    )


@pytest.mark.parametrize("load", sorted(_GATED_LOADS), ids=sorted(_GATED_LOADS))
def test_each_load_clears_its_own_gate_when_it_is_not_refused(load):
    """Set on a refusal, and cleared on EVERY other outcome — success and a real failure alike.
    Home renders the gate and `if let data` as SIBLINGS, so a gate left set over a successful
    load draws "Reconnecting…" on top of the dashboard it is claiming to wait for."""
    _, _, recon, signed = _GATED_LOADS[load]
    block = _load_block(load)
    for flag in (signed, recon):
        cleared = len(re.findall(rf"\b{flag}\s*=\s*false", block))
        assert cleared >= 2, (
            f"{load} clears `{flag}` on {cleared} path(s); it must clear on success AND on a "
            "non-auth failure, or a latched gate survives the load that disproved it"
        )


_IDENTITY_RESETS = {
    "Home": (_HOME_VM, ("requiresSignIn", "isReconnecting")),
    "Updates": (_UPDATES_VM, ("requiresSignIn", "isReconnecting")),
    "Tracking": (_TRACKING_VM, ("assetsRequiresSignIn", "assetsIsReconnecting",
                                "whalesRequiresSignIn", "whalesIsReconnecting")),
}


@pytest.mark.parametrize("vm", sorted(_IDENTITY_RESETS), ids=sorted(_IDENTITY_RESETS))
def test_the_flags_are_cleared_before_the_active_tab_gate(vm):
    """`handleIdentityChange` must clear a latched gate BEFORE it returns for a hidden tab: the
    healing reload is deferred while the tab is hidden, and a `requiresSignIn` left `true` by
    the previous identity is exactly what that reload exists to replace."""
    path, flags = _IDENTITY_RESETS[vm]
    block = _decl_block(_read(path), "func handleIdentityChange(isActiveTab: Bool) async")
    gated = block.find("guard isActiveTab")
    assert gated != -1, f"{vm}'s handleIdentityChange no longer gates on isActiveTab"
    for flag in flags:
        cleared = block.find(f"{flag} = false")
        assert cleared != -1, f"{vm}'s handleIdentityChange never clears {flag}"
        assert cleared < gated, (
            f"{vm} clears {flag} AFTER the isActiveTab early-return, so a hidden tab keeps a "
            "stale gate from the previous identity"
        )


def test_a_refused_pass_is_not_stamped_fresh():
    """Home's freshness window is what suppresses a re-fetch. Stamping `lastLoadedAt` on a pass
    that never saw the account would let it suppress the very reload that heals the gate — the
    staleness guard re-creating the bug it sits next to (`ResearchViewModel:326`)."""
    branch = _refusal_branch(_load_block("Home"))
    assert "lastLoadedAt" not in branch, (
        "the refused path stamps lastLoadedAt, so loadIfStale refuses to re-fetch for the "
        "whole freshness window after the session heals"
    )


def test_the_tracking_halves_do_not_share_a_gate():
    """Measured by the adversarial review: with ONE shared pair, `loadWhaleList` succeeding
    after a heal cleared the flags the Assets load had set. Switching sub-tab fetches nothing,
    so Assets fell through to "No tickers yet" about a portfolio that was never loaded."""
    assets = _load_block("Tracking/Assets")
    whales = _load_block("Tracking/Whales")
    assert "whalesRequiresSignIn" not in assets and "whalesIsReconnecting" not in assets, (
        "the Assets load writes the Whales gate"
    )
    assert "assetsRequiresSignIn" not in whales and "assetsIsReconnecting" not in whales, (
        "the Whales load writes the Assets gate — a whale success after a heal would clear "
        "the Assets gate with no Assets reload behind it"
    )
    cls = _decl_block(_read(_TRACKING_VM), _VM_CLASS[_TRACKING_VM])
    gated = _decl_block(cls, "var isAnySurfaceGated: Bool")
    for flag in ("assetsRequiresSignIn", "assetsIsReconnecting",
                 "whalesRequiresSignIn", "whalesIsReconnecting"):
        assert flag in gated, (
            f"isAnySurfaceGated ignores {flag}, so the session-healed reload skips a screen "
            "that is still waiting on the session"
        )


def test_a_refused_whale_load_clears_the_whole_roster():
    """Three reviewers found this independently. The first version cleared `trackedWhales` and
    `allPopularWhales` — the one the gate is KEYED on — but not `heroWhales`/`popularWhales`,
    the two `MostPopularWhalesSection` actually DRAWS. "Reconnecting…" then sat directly above
    the previous load's hero carousel and Most Popular cards, Follow buttons live."""
    branch = _refusal_branch(_load_block("Tracking/Whales"))
    for array in ("trackedWhales", "allPopularWhales", "heroWhales", "popularWhales"):
        assert re.search(rf"\b{array}\s*=\s*\[\]", branch), (
            f"a refused roster load leaves `{array}` populated, so stale whales render under "
            "the account gate"
        )
    whales_view = _decl_block(_read(_TRACKING_VIEW), "struct WhalesTabContent: View")
    section_at = whales_view.find("MostPopularWhalesSection(")
    guard_at = whales_view.find(
        "if !(viewModel.whalesRequiresSignIn || viewModel.whalesIsReconnecting) {")
    assert section_at != -1, "the Whales tab no longer renders MostPopularWhalesSection"
    assert guard_at != -1 and guard_at < section_at, (
        "MostPopularWhalesSection is drawn while the roster is gated — its header renders "
        "unconditionally, so the gate sits above a bare 'Most Popular / See All'"
    )


def test_the_whale_retry_cannot_hot_loop_while_unarmed():
    """`TrackingView.onAppear` calls `retryWhaleListIfNeeded()` on EVERY appearance of the
    Whales sub-tab, and its only other condition — "the roster is empty" — is permanently true
    while unarmed. Each appearance used to spend three refusals and two backoff sleeps."""
    retry = _decl_block(_read(_TRACKING_VM), "func retryWhaleListIfNeeded()")
    assert "whalesRequiresSignIn" in retry and "whalesIsReconnecting" in retry, (
        "retryWhaleListIfNeeded re-fires on every appearance while the roster is gated"
    )
    loop = _load_block("Tracking/Whales")
    brk = loop.find("if case .signInRequired = AppError.from(error) { break }")
    sleep = loop.find("Task.sleep")
    assert brk != -1, (
        "the whale retry loop retries a DETERMINISTIC refusal — every retry is refused "
        "identically and only spends the backoff sleeps"
    )
    assert sleep != -1 and brk < sleep, (
        "the refusal check sits after the backoff sleep, so the sleep is still paid"
    )


def test_updates_refusal_passes_through_the_load_token():
    """Review finding: the first version returned from `loadFeed` BEFORE bumping `loadToken`,
    so a response already on the wire still passed its `loadToken == token` check and
    repopulated the list the gate had just cleared. The refusal now takes the normal path."""
    block = _load_block("Updates")
    bump = block.find("loadToken = token")
    stale_check = block.find("guard loadToken == token else { return }")
    refusal = block.find("if case .signInRequired = ")
    assert bump != -1 and refusal != -1, "loadFeed's token bump or refusal branch is gone"
    assert bump < refusal, "the refusal is classified before the load token is bumped"
    assert stale_check != -1 and stale_check < refusal, (
        "a stale response can reach the refusal branch and overwrite a newer load's gate"
    )


# ── The views render the gate, reconnecting FIRST ──────────────────────────────


@pytest.mark.parametrize("tab", sorted(_GATED_VIEWS), ids=sorted(_GATED_VIEWS))
def test_every_gated_view_renders_the_shared_state(tab):
    path, header, branch_decl, recon_flag, signed_flag = _GATED_VIEWS[tab]
    block = _decl_block(_read(path), header)
    assert "AccountGateEmptyState" in block, (
        f"{tab} renders its own version of the signed-out state again. Five hand-rolled copies "
        "is how they drifted into four different designs in the first place"
    )
    # BOTH modes, not just one. Half the point of this pass is that the two states are
    # different, so a surface that renders only one of them has silently dropped the other —
    # and the one usually dropped is `.reconnecting`, which is the one that is reachable.
    assert "mode: .reconnecting" in block, (
        f"{tab} never renders the reconnecting mode, so a user whose session is merely being "
        "restored falls through to whatever the next branch is"
    )
    assert "mode: .signedOut(onSignIn:" in block, (
        f"{tab} never renders the signed-out mode, so there is no Sign In button on the one "
        "screen the tester asked for it"
    )


@pytest.mark.parametrize("tab", sorted(_GATED_VIEWS), ids=sorted(_GATED_VIEWS))
def test_reconnecting_is_branched_before_signed_out(tab):
    """auth.md §5. During a restore we cannot prove the session, but we DO hold a credential,
    so "sign in" is a false statement — and `requestSignIn` declines to prompt there anyway,
    so the button would also do nothing.

    Bound to `body` inside the struct, not to the struct: `ReportsListSection` DECLARES
    `requiresSignIn` and `isReconnecting` as properties, in that order, so a struct-wide scan
    reads the declaration order and says nothing at all about the branch order."""
    path, header, branch_decl, recon_flag, signed_flag = _GATED_VIEWS[tab]
    block = _decl_block(_decl_block(_read(path), header), branch_decl)
    recon = block.find(recon_flag)
    signed_out = block.find(signed_flag)
    assert recon != -1, f"{tab} does not branch on a reconnecting state at all"
    assert signed_out != -1, f"{tab} does not branch on a signed-out state at all"
    assert recon < signed_out, (
        f"{tab} evaluates the signed-out branch BEFORE the reconnecting one, so a user whose "
        "session is merely being restored is told to sign in — with their own avatar loaded "
        "in the header above it"
    )


@pytest.mark.parametrize("tab", sorted(_GATED_VIEWS), ids=sorted(_GATED_VIEWS))
def test_the_sign_in_branch_raises_the_prompt(tab):
    """A CTA that calls nothing is worse than no CTA: the tester's complaint was a state with
    nothing to tap, and a button that does nothing is the same complaint with extra steps."""
    path, header, branch_decl, recon_flag, signed_flag = _GATED_VIEWS[tab]
    block = _decl_block(_read(path), header)
    assert "signedOut(onSignIn:" in block, (
        f"{tab} never constructs the .signedOut mode, so it can only ever render the "
        "reconnecting half of the state"
    )
    # Reports takes its closure as a parameter from ContentView; the four tabs raise it here.
    if tab != "Reports":
        assert "requestSignIn(for:" in block, (
            f"{tab}'s Sign In button does not reach AppState.requestSignIn, so it is a dead "
            "control"
        )


def test_updates_no_longer_offers_try_again_to_a_signed_out_user():
    """Signed out, Updates read "Couldn't load the news / Sign in to use this feature." under a
    Try Again button that re-fires a request refused before it leaves the device — an infinite
    dead end, and the clearest example of what flattening the AppError case costs."""
    block = _decl_block(_read(_UPDATES_VIEW), "struct UpdatesView: View")
    # The gate must OPEN the chain — `if`, not `else if`. An index comparison alone is
    # satisfied by a gate that is merely written higher up while an earlier branch still wins,
    # so pin the chaining itself: the loading branch must be reached only by falling THROUGH
    # the gate.
    assert re.search(r"(?<!else )if viewModel\.isReconnecting \{", block), (
        "the account gate is no longer the first branch of the chain, so a signed-out user "
        "can still be routed to errorState and its unusable Try Again button"
    )
    assert "} else if viewModel.isLoading && viewModel.groupedNews.isEmpty {" in block, (
        "the loading/error chain no longer hangs off the account gate — it is a separate `if`, "
        "so both can render at once, or the error one can win outright"
    )
    gate_at = block.find("if viewModel.isReconnecting {")
    error_at = block.find("viewModel.isLoading && viewModel.groupedNews.isEmpty")
    assert gate_at < error_at, (
        "the account gate is evaluated after the loading/error branches, so a signed-out user "
        "still falls through to errorState and its unusable Try Again button"
    )


def test_the_home_banner_is_left_for_real_failures():
    """`errorBanner` is the NETWORK affordance (a wifi-exclamation glyph). It must still exist
    — a genuine outage needs it — but it must no longer be reachable for the gated case."""
    block = _decl_block(_read(_HOME_VIEW), "struct HomeDashboardView: View")
    assert "errorBanner(errorMessage)" in block, (
        "Home no longer shows a banner for a genuine network/server failure"
    )
    # The banner must hang off the gate as `else if`, not sit in its own `if`. As a sibling it
    # renders IN ADDITION to the gate — "Sign in to see your dashboard" with the old
    # wifi-exclamation line stacked underneath, which is the screenshot plus a button.
    assert "} else if let errorMessage = viewModel.errorMessage {" in block, (
        "the error banner left the account-gate chain, so it can render alongside the gate "
        "instead of only when the gate does not apply"
    )
    gate_at = block.find("if viewModel.isReconnecting {")
    banner_at = block.find("errorBanner(errorMessage)")
    assert gate_at != -1, "the Home account gate is gone"
    assert gate_at < banner_at, (
        "the error banner is evaluated before the account gate, so an auth refusal is painted "
        "as a network failure again — the exact TestFlight screenshot"
    )


# Third element: what the trigger must be narrowed on. Tracking has two gated halves, so it
# keys on `isAnySurfaceGated` (pinned to cover all four flags by
# `test_the_tracking_halves_do_not_share_a_gate`).
_HEALING_ROOTS = {
    "Home": (_HOME_VIEW, "struct HomeDashboardView: View", ("requiresSignIn", "isReconnecting")),
    "Updates": (_UPDATES_VIEW, "struct UpdatesView: View", ("requiresSignIn", "isReconnecting")),
    # The LIVE Tracking root. `TrackingContentView` in the same file is preview-only.
    "Tracking": (_TRACKING_VIEW, "struct TrackingContentViewWithBinding: View",
                 ("isAnySurfaceGated",)),
}


@pytest.mark.parametrize("tab", sorted(_HEALING_ROOTS), ids=sorted(_HEALING_ROOTS))
def test_a_healed_session_clears_the_gate_without_waiting(tab):
    """MEASURED on the simulator before this trigger existed: Home sat on "Reconnecting…" for
    ~60 seconds — until its own auto-refresh tick — after `/users/me` had already answered 200.

    Nothing else covers it. `.reloadOnIdentityChange` deliberately ignores the launch hop
    `.restoring → .authenticated` (`AppState.identityGeneration` does not move, because
    discovering an identity is not changing one), and `.task(id: isActiveTab)` has already run
    for whichever tab is on screen at launch. So the load that latched the gate is never re-run.
    """
    path, header, narrowed_on = _HEALING_ROOTS[tab]
    block = _decl_block(_read(path), header)
    assert "onChange(of: appState.auth.status)" in block, (
        f"{tab} has no trigger that re-runs its load when the session heals, so a gate latched "
        "during restore survives until some unrelated timer happens to fire"
    )
    trigger = _decl_block(block, "onChange(of: appState.auth.status)")
    assert "status == .authenticated" in trigger, (
        f"{tab}'s healing trigger does not check that the session actually healed"
    )
    assert all(token in trigger for token in narrowed_on), (
        f"{tab}'s healing trigger is not narrowed to the gated case, so an ordinary sign-in "
        "pays for a duplicate load on top of the one reloadOnIdentityChange already issues"
    )


# ── The molecule enforces the invariant structurally ───────────────────────────


def test_the_reconnecting_mode_carries_no_action():
    """The whole reason Mode is an enum rather than a Bool plus an optional closure: with no
    associated value there is nothing to wire a button to, so the auth.md §5 rule is held by
    the compiler instead of by a comment repeated in five files."""
    block = _decl_block(_read(_MOLECULE), "enum Mode")
    assert re.search(r"case reconnecting\s*$", block, re.M), (
        "Mode.reconnecting gained an associated value — if that is a closure, the reconnecting "
        "state can now offer a Sign In button that requestSignIn refuses to act on"
    )
    assert "case signedOut(onSignIn:" in block, (
        "Mode.signedOut no longer carries its action, so the CTA has nothing to call"
    )


def test_the_button_is_reachable_only_from_the_signed_out_mode():
    block = _decl_block(_read(_MOLECULE), "private var stack: some View")
    button_at = block.find("Button(action: onSignIn)")
    assert button_at != -1, "the molecule no longer renders a Sign In button at all"
    binding_at = block.find("if case .signedOut(let onSignIn) = mode")
    assert binding_at != -1, "the button is no longer bound to the signedOut case"
    assert binding_at < button_at, "the Sign In button escaped its .signedOut binding"


def test_the_signed_out_state_does_not_swallow_its_own_button():
    """`.accessibilityElement(children: .combine)` on the whole stack makes the Sign In button
    unreachable to VoiceOver. It is correct for `.reconnecting` (no control, five fragments of
    label) and wrong for `.signedOut`, so it must be applied per-mode, not to `stack`."""
    stack = _decl_block(_read(_MOLECULE), "private var stack: some View")
    assert "accessibilityElement" not in stack, (
        "the combine modifier moved onto the shared stack, so VoiceOver can no longer reach "
        "the Sign In button — the one control on the screen"
    )
    body = _decl_block(_read(_MOLECULE), "var body: some View")
    assert "accessibilityElement(children: .combine)" in body, (
        "the reconnecting state lost its combined accessibility element"
    )


def test_the_cta_uses_the_ink_its_fill_declares():
    """`primaryFill` is a FROZEN fill and carries `textOnAccent`. `textOnFill` measures 3.81:1
    on it in dark, and `textPrimary`/`.white` are how that pairing gets broken."""
    block = _decl_block(_read(_MOLECULE), "private var stack: some View")
    assert "AppColors.primaryFill" in block and "AppColors.textOnAccent" in block, (
        "the Sign In CTA no longer uses the sanctioned fill+ink pair"
    )
    assert ".white" not in block, "a bare .white on a saturated fill bypasses the ink contract"


def test_the_glyph_scales_with_dynamic_type():
    """The literal this was extracted from was `.font(.system(size: 40))`, which does not
    respond to the user's text size at all."""
    block = _decl_block(_read(_MOLECULE), "private var stack: some View")
    assert "AppTypography.iconXXL" in block, "the glyph is no longer a scaling token"
    assert ".system(size:" not in block, (
        "a raw system font size is back — it ignores Dynamic Type entirely"
    )


# ── Wiser keeps its content, and says what is wrong ────────────────────────────


def test_wiser_keeps_rendering_its_bundled_content():
    """Books and the Journey roadmap are compiled-in and Money Moves falls back to bundled
    JSON, so gating this page would delete a feature that genuinely works without an account.
    The notice sits BESIDE the content."""
    block = _decl_block(_read(_LEARN_VIEW), "private var learnTabContent: some View")
    assert "fullLearnDashboard" in block, (
        "the Wiser dashboard is no longer rendered from learnTabContent — a gate replaced "
        "content that works offline"
    )
    assert "accountGateNotice" in block, (
        "Wiser says nothing when the session is unarmed, so completion ticks and bookmarks "
        "silently revert to device-local with no explanation"
    )
    assert "AccountGateEmptyState" not in block, (
        "Wiser replaced its content with the full-page gate; it should carry an inline notice "
        "alongside the bundled Books and Journey content instead"
    )


def test_the_wiser_notice_offers_no_button_while_reconnecting():
    block = _decl_block(_read(_LEARN_VIEW), "private var accountGateNotice: some View")
    restoring_at = block.find("case .restoring:")
    signed_out_at = block.find("case .unauthenticated")
    assert restoring_at != -1 and signed_out_at != -1, "the Wiser gate lost one of its arms"
    restoring_arm = block[restoring_at:signed_out_at]
    assert "onRetry" not in restoring_arm, (
        "the reconnecting notice wires an action — requestSignIn declines to prompt during a "
        "restore, so it is a dead control on top of a false statement"
    )
    assert 'retryTitle: "Sign In"' in block[signed_out_at:], (
        "the signed-out notice no longer offers a Sign In affordance"
    )


def test_wiser_reloads_when_the_identity_changes():
    """Wiser was the only one of the five tab roots without this. The Learn stores are
    device-global, so signing in never pulled the new account's progress at all."""
    src = _strip_comments(_read(_LEARN_VIEW))
    assert "reloadOnIdentityChange" in src, (
        "Wiser has no identity-change reload, so a sign-in leaves the previous (cleared) "
        "progress on screen until the app is killed"
    )


# ── Anti-vacuity ───────────────────────────────────────────────────────────────


def test_comment_only_mentions_do_not_satisfy_the_assertions():
    """Every fix in this pass is commented, and those comments name `requiresSignIn`,
    `isReconnecting`, `AppActions.shared.isSignedIn` and `AccountGateEmptyState`. If stripping
    regressed, every assertion above would pass on prose after the code was reverted."""
    stripped = _strip_comments(
        "// requiresSignIn = true\n"
        "/// AppActions.shared.isSignedIn\n"
        "    let x = 1  // AccountGateEmptyState\n"
    )
    assert "requiresSignIn" not in stripped
    assert "isSignedIn" not in stripped
    assert "AccountGateEmptyState" not in stripped
    assert "let x = 1" in stripped


@pytest.mark.parametrize("tab", sorted(_GATED_VIEWS), ids=sorted(_GATED_VIEWS))
def test_the_view_scans_are_bounded_to_their_declaration(tab):
    path, header, branch_decl, recon_flag, signed_flag = _GATED_VIEWS[tab]
    src = _read(path)
    block = _decl_block(src, header)
    assert len(block) > 400, f"{tab}'s block is only {len(block)} chars — the scan has drifted"
    assert len(block) < len(src), (
        f"{tab}'s declaration block is the whole file — `_decl_block` stopped bounding, so "
        "every assertion scoped to this view is now satisfiable from anywhere in it"
    )


def test_the_tracking_scans_read_the_live_declarations_not_the_preview_one():
    """`TrackingContentView` in TrackingView.swift is preview-only. Both sub-tab views scanned
    above are reached from `TrackingContentViewWithBinding`, the live root — asserting against
    the preview copy is how a "fix" ships green and changes nothing."""
    src = _read(_TRACKING_VIEW)
    live = _decl_block(src, "struct TrackingContentViewWithBinding: View")
    assert "AssetsTabContent" in live or "TrackingContentView(" in live, (
        "the live Tracking root no longer reaches the sub-tab content this file scans"
    )
    assets = _decl_block(src, "struct AssetsTabContent: View")
    whales = _decl_block(src, "struct WhalesTabContent: View")
    assert "AssetsListSection" in assets, "AssetsTabContent block has drifted off the real view"
    assert "MostPopularWhalesSection" in whales, "WhalesTabContent block has drifted"
    assert "MostPopularWhalesSection" not in assets, (
        "the AssetsTabContent block ran past its closing brace into WhalesTabContent"
    )


def test_the_molecule_scans_ignore_the_previews():
    """`AccountGateEmptyState.swift` ships two `#Preview` blocks that construct both modes,
    including `.signedOut(onSignIn: {})`. Every assertion above is brace-bound to `stack`,
    `body` or `enum Mode`, so a preview cannot satisfy one — prove that bounding is real."""
    src = _read(_MOLECULE)
    assert "#Preview" in src, "the molecule lost its previews"
    stack = _decl_block(src, "private var stack: some View")
    assert "#Preview" not in stack, "the stack block swallowed the preview blocks"
    assert "AppColors.background" not in stack, (
        "the stack block ran past its closing brace into the previews, which is where the "
        "background token lives"
    )


# ── MUTATION_LOG ───────────────────────────────────────────────────────────────
#
# Run by hand on 2026-09-23; every source restored byte-for-byte afterwards (sha256 checked).
# 17 mutations, 17 killed.
#
#  1. HomeDashboardViewModel: `guard AppActions.shared.isSignedIn` → `guard false`
#     -> test_the_gate_is_decided_pre_flight_not_from_an_error_string[Home] FAILED ✅
#  2. HomeDashboardViewModel: `isReconnecting` loses `@Published` (COMPILES, and the view
#     simply never re-renders when the session heals)
#     -> test_every_gated_view_model_carries_both_flags[Home] FAILED ✅
#     ⚠️ The first attempt here DELETED the declaration instead, and SURVIVED — the name
#     still appeared elsewhere in the class, and the mutant would not have compiled either.
#     A non-compiling mutant is a non-result; the assertion was rewritten to pin `@Published`.
#  3. HomeDashboardViewModel: `if case .signInRequired = appError` → `if false`
#     -> test_a_mid_flight_credential_death_is_caught_too[Home] FAILED ✅
#  4. HomeDashboardViewModel: the two clears moved below `guard isActiveTab`
#     -> test_the_flags_are_cleared_before_the_active_tab_gate[Home] FAILED ✅
#  5. HomeDashboardViewModel: gated path stamps `lastLoadedAt = Date()`
#     -> test_a_gated_pass_is_not_stamped_fresh FAILED ✅
#  6. HomeDashboardView: gate condition reordered so signed-out wins
#     -> test_reconnecting_is_branched_before_signed_out[Home] FAILED ✅
#  7. TrackingViewModel: `isSignedIn` guard dropped from `retryWhaleListIfNeeded`
#     -> test_the_whale_retry_cannot_hot_loop_while_unarmed FAILED ✅
#  8. TrackingView/Assets: the `.reconnecting` construction swapped for a second `.signedOut`
#     -> test_every_gated_view_renders_the_shared_state[Tracking/Assets] FAILED ✅
#     ⚠️ First attempt replaced it with `EmptyView()` and SURVIVED: the sibling `.signedOut`
#     still satisfied a bare containment check. The assertion now pins BOTH modes.
#  9. AccountGateEmptyState: `case reconnecting` gains `(onSignIn: () -> Void)`
#     -> test_the_reconnecting_mode_carries_no_action FAILED ✅
# 10. AccountGateEmptyState: `.accessibilityElement(children: .combine)` moved onto `stack`
#     -> test_the_signed_out_state_does_not_swallow_its_own_button FAILED ✅
# 11. AccountGateEmptyState: `AppTypography.iconXXL` → `.system(size: 40)`
#     -> test_the_glyph_scales_with_dynamic_type FAILED ✅
# 12. AccountGateEmptyState: `AppColors.textOnAccent` → `.white`
#     -> test_the_cta_uses_the_ink_its_fill_declares FAILED ✅
# 13. UpdatesView: gate demoted to `else if` below the loading branch
#     -> test_updates_no_longer_offers_try_again_to_a_signed_out_user FAILED ✅
#     ⚠️ First attempt only added `if false,` in front of the same token and SURVIVED — the
#     index comparison was unmoved. The assertion now pins the CHAINING (`if` vs `else if`),
#     not just the order of two substrings.
# 14. HomeDashboardView: banner split out of the chain into its own `if`
#     -> test_the_home_banner_is_left_for_real_failures FAILED ✅ (same lesson as 13)
# 15. LearnView: `accountGateNotice` removed from `learnTabContent`
#     -> test_wiser_keeps_rendering_its_bundled_content FAILED ✅
# 16. LearnView: `onRetry:` added to the `.restoring` notice
#     -> test_the_wiser_notice_offers_no_button_while_reconnecting FAILED ✅
# 17. LearnView: `.reloadOnIdentityChange` → `.task`
#     -> test_wiser_reloads_when_the_identity_changes FAILED ✅
#
# ⚠️ What these mutations do NOT cover, deliberately:
#   * That the gate is ever REACHED at runtime. These are source scans; the `.restoring`
#     window was driven on the simulator instead (launch with USE_LOCAL=1 and no local server).
#   * The COPY. Every headline/subtitle is a caller's string by design, so no test pins one —
#     a guard on the wording would only make the next copy edit red.
#   * Whether `AppActions.shared` is configured. That is `AppActions.configure(appState:)`,
#     already pinned elsewhere; an unconfigured bridge reports `isSignedIn == false`, which
#     fails safe into the gate rather than into a blank page.
