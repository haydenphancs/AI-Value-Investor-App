"""The "AI Deep Research" / "AI Analyst" button must land where the asset belongs.

TestFlight: *"AI deep search button must go to the Research screen. Not to chat with Cay AI.
This is a stock/company. Not index or crypto."*

The route already existed but rode on an injected `onNavigateToResearch` closure that exactly
ONE of ~14 call sites supplied, so every other entry point fell through to a chat. It is parked
on `AppState.pendingResearchTicker` now, which works from all of them.

There is no XCTest target here, so — like `test_ios_paid_path_guards.py` and
`test_ios_auth_policy_parity.py` — this pins the invariant by reading the Swift source. Each
guard is **brace-bounded** to the declaration it is about and reads **comment-stripped** source:
this file's own prose and the fixed code's own comments quote every token asserted on, so an
un-stripped whole-file scan would pass on the explanation after the fix was reverted.

These pin the SHAPE of the source, not runtime behaviour. A semantically-equivalent rewrite
passes without the guard running; a rename fails it without breaking the app.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_TICKER = _IOS / "Views/Screens/TickerDetailView.swift"
_ETF = _IOS / "Views/Screens/ETFDetailView.swift"
_CRYPTO = _IOS / "Views/Screens/CryptoDetailView.swift"
_INDEX = _IOS / "Views/Screens/IndexDetailView.swift"
_ROUTER = _IOS / "Views/Molecules/AssetDetailRouter.swift"
_APP_STATE = _IOS / "Core/State/AppState.swift"
_CONTENT = _IOS / "ContentView.swift"


def _src(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return path.read_text()


def _code(path: Path) -> str:
    """Comment-stripped source — a guard must never be satisfied by prose."""
    src = _src(path)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def _decl_body(src: str, prefix: str, open_ch: str = "{") -> str:
    """The delimiter-matched body of the declaration starting at `prefix`."""
    close_ch = {"{": "}", "[": "]", "(": ")"}[open_ch]
    at = src.index(prefix)
    start = src.index(open_ch, at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_ch:
            depth += 1
        elif src[i] == close_ch:
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced {open_ch}{close_ch} after {prefix!r}")


# ── A stock goes to Research, and cannot fall back to chat ──────────────────

def test_stock_button_parks_the_research_route():
    body = _decl_body(_code(_TICKER), "private func handleDeepResearchTap()")
    assert "pendingResearchTicker" in body


def test_stock_button_has_no_chat_fallback_left():
    """The `else` branch WAS the bug: it opened Cay AI from 13 of 14 entry points."""
    body = _decl_body(_code(_TICKER), "private func handleDeepResearchTap()")
    assert "startNewConversation" not in body
    assert "showAIChat" not in body


def test_the_route_is_not_an_injected_closure_anywhere():
    """A closure only one call site passes is indistinguishable from a dead button."""
    for path in (_TICKER, _ETF, _CRYPTO, _INDEX, _ROUTER, _IOS / "Views/Screens/TrackingView.swift"):
        assert "onNavigateToResearch" not in _code(path), path.name


# ── The non-stock buttons open chat, and are not inert ──────────────────────

@pytest.mark.parametrize("path,ctx", [(_ETF, ".etf"), (_CRYPTO, ".crypto")])
def test_non_stock_button_seeds_a_chat(path, ctx):
    """The report pipeline needs an FMP company profile, so these must NOT reach Research."""
    body = _decl_body(_code(path), "private func handleDeepResearchTap()")
    assert "startNewConversation" in body
    assert ctx in body
    assert "pendingResearchTicker" not in body


def test_the_etf_button_is_no_longer_a_no_op():
    """It was `if let onNavigateToResearch { … }` with NO else, and nothing ever passed that
    closure — so the button did literally nothing in every shipped build."""
    body = _decl_body(_code(_ETF), "private func handleDeepResearchTap()")
    assert "showAIChat = true" in body


# ── A tap during a live answer must not present a stale conversation ────────

@pytest.mark.parametrize("path", [_ETF, _CRYPTO])
def test_seeded_chat_is_guarded_on_the_return_value(path):
    """`startNewConversation` returns false when a previous turn is still streaming, meaning
    NOTHING was seeded. Presenting anyway shows the PREVIOUS conversation."""
    body = _decl_body(_code(path), "private func handleDeepResearchTap()")
    assert re.search(r"if\s+seeded\s*\{", body), body


def test_index_button_is_guarded_too():
    """The index handler is written inline in `tabContent`, not as a named func."""
    body = _decl_body(_code(_INDEX), "onAIAnalystTap:")
    assert "startNewConversation" in body
    assert re.search(r"if\s+chatViewModel\.startNewConversation", body), body


# ── The route plumbing exists on both ends ──────────────────────────────────

def test_appstate_declares_and_clears_the_route():
    code = _code(_APP_STATE)
    assert "var pendingResearchTicker: String?" in code
    # Device-global with no user id — auth.md §7. Left behind, it fires into the next account.
    ended = _decl_body(code, "private func discardDataForEndedSession()")
    assert "pendingResearchTicker = nil" in ended


def test_contentview_consumes_the_route_and_clears_it():
    code = _code(_CONTENT)
    body = _decl_body(code, ".onChange(of: appState.pendingResearchTicker")
    assert "selectedTab = .research" in body
    assert "researchTickerSymbol = ticker" in body
    # ONE OWNER PER ROUTE KIND: whoever reads it must clear it, or it re-fires.
    assert "appState.pendingResearchTicker = nil" in body


def test_the_research_tab_reacts_to_a_later_prefill():
    """`prefilledTicker` reaches the ViewModel through a `StateObject` autoclosure that runs
    ONCE. Without this observer the tab switches and then shows an EMPTY search field — which
    is what the one wired-up entry point actually did.

    The handoff now goes through `applyPrefilledTicker` rather than assigning `searchText`
    directly — see `test_the_prefill_goes_through_the_single_writer` for why that matters. This
    test still owns the "reacts at all" half.
    """
    body = _decl_body(_code(_CONTENT), ".onChange(of: prefilledTicker)")
    assert "ticker" in body and "viewModel." in body, "the observer no longer forwards the ticker"


def test_initial_true_is_kept_for_a_cold_launch():
    """A route parked before this view exists is otherwise never seen."""
    code = _code(_CONTENT)
    assert ".onChange(of: appState.pendingResearchTicker, initial: true)" in code


# ── …and the user has to be able to SEE the tab it switches to ──────────────
#
# The route above works and still read as a dead button, because `dismiss()` closes exactly ONE
# presentation level. Home has no root NavigationStack, so its destinations are modal and the
# ticker screen sits two covers deep (theme → ticker, signals → ticker, search → ticker). The
# tab came forward BEHIND the survivor. Second TestFlight report on the same button.

_TAB_ROOTS = [
    _CONTENT,                                           # the shell + ResearchViewWithBinding
    _IOS / "Views/Screens/HomeDashboardView.swift",
    _IOS / "Views/Screens/UpdatesView.swift",
    _IOS / "Views/Screens/LearnView.swift",
    _IOS / "Views/Screens/TrackingView.swift",
]


def test_the_button_asks_for_the_presentation_stack_to_come_down():
    """THE regression guard. Parking the ticker is necessary and was never sufficient."""
    body = _decl_body(_code(_TICKER), "private func handleDeepResearchTap()")
    assert "pendingResearchTicker" in body, "the route itself is gone"
    assert "dismissAllPresentations()" in body, (
        "handleDeepResearchTap parks the ticker but never asks the presentation stack to come "
        "down, so from a theme / signals / search entry the Research tab switches BEHIND a "
        "cover that is still on screen — indistinguishable from the button doing nothing."
    )


def test_the_button_does_not_also_dismiss_itself():
    """A SECOND dismissal, issued from inside in the same runloop as the root's, is the UIKit
    wedge: a controller already mid-transition refuses one, and the request that loses is the
    OUTER one — stranding the intermediate screen with its binding already nil, which
    `.fullScreenCover(item:)` cannot recover from (an equal item is not a change).

    It is also redundant. The reset covers presented screens (root cover binding) AND pushed
    ones (`.navigationDestination` item), which is why Tracking's block must stay complete —
    `test_tracking_clears_its_navigation_chain` is the other half of this guard.
    """
    body = _decl_body(_code(_TICKER), "private func handleDeepResearchTap()")
    assert "dismiss()" not in body, (
        "handleDeepResearchTap dismisses itself again; the root teardown already covers both "
        "the presented and the pushed shape, and two requests in one runloop wedge"
    )


def test_the_route_is_parked_before_the_teardown():
    """Switch to Research BEFORE the covers animate off, or there is a frame showing Home."""
    body = _decl_body(_code(_TICKER), "private func handleDeepResearchTap()")
    assert body.index("pendingResearchTicker") < body.index("dismissAllPresentations")


def test_tracking_clears_its_navigation_chain():
    """Tracking is the ONE tab that reaches the ticker screen by a push, so with `dismiss()`
    gone these four items are the only thing that pops it. Whale → trade group → ticker is three
    pushes deep; nil-ing the outermost item retires the chain."""
    code = _code(_IOS / "Views/Screens/TrackingView.swift")
    live_at = code.index("struct TrackingContentViewWithBinding")
    body = _decl_body(code[live_at:], ".onPresentationReset")
    for item in (
        "selectedAssetNavigation",   # the watchlist push — the common one
        "selectedSearchResult",
        "selectedWhaleId",
        "selectedTradeGroup",
    ):
        assert item in body, f"Tracking's reset no longer pops {item}; that chain is stranded"


def test_tracking_clears_the_alert_handoff_before_the_sheet():
    """`.sheet(item: $viewModel.selectedAlert, onDismiss: { opened = pending; pending = nil })`.
    Clearing `selectedAlert` while `pendingAlertDestination` is set makes that `onDismiss`
    RE-PRESENT the destination cover on top of the Research tab."""
    code = _code(_IOS / "Views/Screens/TrackingView.swift")
    live_at = code.index("struct TrackingContentViewWithBinding")
    body = _decl_body(code[live_at:], ".onPresentationReset")
    assert "pendingAlertDestination" in body, "the alert hand-off would re-present after dismissal"
    assert body.index("pendingAlertDestination") < body.index("selectedAlert")


def test_appstate_declares_the_reset_token():
    code = _code(_APP_STATE)
    assert "presentationResetToken" in code, "the reset signal is gone"
    body = _decl_body(code, "func dismissAllPresentations()")
    assert "presentationResetToken" in body, "dismissAllPresentations no longer bumps the token"


def test_the_token_is_not_reset_at_session_end():
    """The EXEMPTION from the auth.md §7 habit, pinned because it looks like an omission.

    The four `pending*` routes beside it are parked INSTRUCTIONS that fire later, so one left
    behind executes against the next account. A bump here is consumed synchronously by every
    mounted observer in the same runloop — nothing is inherited, so a reset buys nothing. It
    also costs: `= 0` would be the only DECREMENT in the system, and a decrement is a change,
    making it the one thing able to tear down a presentation at sign-out — including the Account
    cover the user is signing out from.
    """
    ended = _decl_body(_code(_APP_STATE), "private func discardDataForEndedSession()")
    assert "presentationResetToken" not in ended, (
        "the token is reset at session end again — that is the only decrement in the system, "
        "and it can tear down the very cover the user is signing out from"
    )


def test_the_reset_ignores_a_decrement():
    """Insurance for the above: even if someone re-adds a reset, an observer must not act on it."""
    body = _code(_IOS / "Views/Modifiers/PresentationReset.swift")
    assert "newValue > oldValue" in body, (
        "PresentationReset no longer guards against a decrement / non-increment"
    )


def test_the_token_is_monotonic_not_a_flag():
    """A Bool (or piggybacking on `pendingResearchTicker`) races the consumer that clears it —
    the "ONE OWNER PER ROUTE KIND" hazard the push-route handler documents. Only-ever-increasing
    means every observer sees every bump, with no clear to lose."""
    body = _decl_body(_code(_APP_STATE), "func dismissAllPresentations()")
    assert "&+= 1" in body or "+= 1" in body, (
        "dismissAllPresentations no longer increments; a flag would be race-prone"
    )


@pytest.mark.parametrize("path", _TAB_ROOTS, ids=lambda p: p.name)
def test_every_tab_root_takes_its_presentations_down(path):
    """Clearing a ROOT's own state unwinds every cover nested beneath it, which is what makes a
    screen presented three deep need no code of its own. A tab that drops out of this list
    silently reintroduces the bug for its own entry points."""
    assert ".onPresentationReset" in _code(path), (
        f"{path.name} no longer honours the presentation reset"
    )


def test_home_clears_every_one_of_its_presentations():
    """Home is the reported path and the densest: seven presentation states, and a newly-added
    cover here is the most likely thing to be forgotten."""
    body = _decl_body(_code(_IOS / "Views/Screens/HomeDashboardView.swift"), ".onPresentationReset")
    for state in (
        "selectedTicker",
        "signalDetailTarget",
        "themeDetailTarget",   # ← the reported chain
        "pushRoute",
        "showSearch",
        "showProfile",
    ):
        assert state in body, f"HomeDashboardView's reset no longer clears {state}"


def test_tracking_reset_is_on_the_live_variant():
    """`TrackingContentView` is dead code — `ContentView` mounts `TrackingContentViewWithBinding`.
    A fix applied to the dead one looks identical in a diff and ships nothing."""
    code = _code(_IOS / "Views/Screens/TrackingView.swift")
    live_at = code.index("struct TrackingContentViewWithBinding")
    assert ".onPresentationReset" in code[live_at:], (
        "the reset landed on the dead TrackingContentView, not the mounted variant"
    )


# ── The prefill must set the target the user actually reads ─────────────────

def test_the_prefill_goes_through_the_single_writer():
    """`TargetSelectionSection` renders `selectedTarget` and falls back to `searchText` only when
    it is nil, while `generateAnalysis()` reads `searchText`. Writing one of them left the chip
    showing a STALE company while Generate spent 20 credits on the new one."""
    body = _decl_body(_code(_CONTENT), ".onChange(of: prefilledTicker)")
    assert "applyPrefilledTicker" in body, "the prefill no longer goes through the ViewModel"
    assert "viewModel.searchText =" not in body, (
        "the prefill assigns searchText directly again, so selectedTarget can shadow it and the "
        "displayed target stops matching the one being charged for"
    )


def test_apply_prefilled_ticker_sets_both_fields():
    vm = _code(_IOS / "ViewModels/ResearchViewModel.swift")
    body = _decl_body(vm, "func applyPrefilledTicker(")
    assert "searchText =" in body, "applyPrefilledTicker no longer sets the generated ticker"
    assert "selectedTarget =" in body, "applyPrefilledTicker no longer sets the displayed target"


def test_the_guards_are_not_vacuous():
    """Mutation control. Rewrite the source back to its pre-fix shape IN MEMORY and confirm each
    window stops matching. Every token asserted above also appears in the fix's own COMMENTS, so
    without `_code()` these would pass on prose with the fix reverted."""
    ticker_code = _code(_TICKER)
    content_code = _code(_CONTENT)

    # (1) The headline guard must be able to fail.
    body = _decl_body(ticker_code, "private func handleDeepResearchTap()")
    assert "dismissAllPresentations()" in body, "nothing to mutate; the guard proves nothing"
    broken = body.replace("appState.dismissAllPresentations()", "")
    assert "dismissAllPresentations()" not in broken, (
        "the mutation did not remove the call — the guard would pass on the broken shape"
    )

    # (2) Comment stripping is load-bearing: prove the raw source DOES carry the tokens in prose,
    #     and that `_code()` is what removes them.
    raw = _src(_TICKER)
    assert "presentation" in raw.lower()
    commentary = "\n".join(ln for ln in raw.splitlines() if ln.strip().startswith("///"))
    assert "dismiss()" in commentary, (
        "expected the explanatory comment to quote the tokens these guards assert on"
    )
    assert "///" not in ticker_code, "_code() is not stripping doc comments"

    # (3) The prefill guard's negative assertion must be able to fail.
    prefill = _decl_body(content_code, ".onChange(of: prefilledTicker)")
    assert "viewModel.searchText =" not in prefill
    assert "viewModel.searchText = ticker" in prefill.replace(
        "viewModel.applyPrefilledTicker(ticker)", "viewModel.searchText = ticker"
    ), "the mutation baseline is wrong — the prefill is not routed through the ViewModel"
