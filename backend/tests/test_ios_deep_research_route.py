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
    is what the one wired-up entry point actually did."""
    body = _decl_body(_code(_CONTENT), ".onChange(of: prefilledTicker)")
    assert "viewModel.searchText = ticker" in body


def test_initial_true_is_kept_for_a_cold_launch():
    """A route parked before this view exists is otherwise never seen."""
    code = _code(_CONTENT)
    assert ".onChange(of: appState.pendingResearchTicker, initial: true)" in code
