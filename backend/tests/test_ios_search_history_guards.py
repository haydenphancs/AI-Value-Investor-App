"""Guards for the search-history feature on the iOS search screen.

WHY THIS FILE EXISTS. "Recent Searches" was never a history. `SearchViewModel.recentSearches`
held the LIVE results array — reassigned on every debounced keystroke and emptied the instant
the field went blank — while `RecentSearchesSection` rendered it under a "Recent Searches"
heading. So at rest the section could only ever show its empty state, and nothing anywhere
recorded a ticker the user opened or a question they asked Cay AI.

The rebuild introduces a device-global store, which is the single most dangerous shape in this
codebase: a `UserDefaults` key with no user id in it. `WhaleService.followedWhaleIds` and the
four Learn stores each shipped exactly this bug — the next account to sign in on the phone
inherited the previous user's data. `.claude/rules/auth.md` §7 is the rule; test 1 below is the
enforcement.

Per `.claude/rules/testing.md` §3 and the `project_source_scan_guard_vacuity` memory, every scan
here is comment-stripped, brace-bounded, and was mutation-tested by hand.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"

_APP_STATE = _IOS / "Core/State/AppState.swift"
_STORE = _IOS / "Services/SearchHistoryStore.swift"
_SEARCH_VIEW = _IOS / "Views/Screens/SearchView.swift"
_SEARCH_VM = _IOS / "ViewModels/SearchViewModel.swift"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails.

    Load-bearing here: the rationale comments in all four files name `recentSearches`,
    `SearchLatestNewsSection` and `latestNews` while explaining why they are gone. An
    un-stripped scan for their ABSENCE would fail on the explanation, and an un-stripped scan
    for a required token would pass on prose after the code was reverted.
    """
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


# ── 1. The cross-account bleed guard — the important one ───────────────────────


def test_search_history_is_cleared_when_a_session_ends():
    block = _decl_block(_read(_APP_STATE), "private func discardDataForEndedSession()")

    # Anti-vacuity: prove we are looking at the real funnel and not an empty/renamed block.
    assert "WhaleService.shared.reset()" in block, "scan drifted — this is not the session-end funnel"
    assert "LearnIdentityEpoch.bump()" in block, "scan drifted — this is not the session-end funnel"

    assert "SearchHistoryStore.shared.reset()" in block, (
        "search history is not cleared when a session ends. The store writes to a device-global "
        "UserDefaults key with no user id, so the next account to sign in on this device would "
        "read the previous user's searched tickers and questions — and re-tap them into their "
        "own session. Same bug class as WhaleService.followedWhaleIds and the four Learn "
        "stores; see .claude/rules/auth.md §7."
    )


def test_the_store_exposes_reset_separately_from_clear_all():
    """`clearAll()` is a user action; `reset()` is a security boundary. Collapsing them lets a
    future "keep my history across sign-out" preference silently disable the auth §7 clear."""
    src = _strip_comments(_read(_STORE))
    assert "func reset()" in src, "SearchHistoryStore no longer exposes reset()"
    assert "func clearAll()" in src, "SearchHistoryStore no longer exposes clearAll()"


# ── 2. The history is durable, bounded, and actually recorded ──────────────────


def test_the_store_persists_and_is_bounded():
    src = _strip_comments(_read(_STORE))
    assert "UserDefaults" in src, (
        "SearchHistoryStore no longer persists. An in-memory-only history is the exact defect "
        "this feature replaced — it would be empty again on the next open."
    )
    assert "maxEntries" in src, (
        "the entry cap is gone; an unbounded UserDefaults blob is decoded on every launch"
    )


def test_a_selected_ticker_is_recorded():
    """Recorded on SELECTION, not in the debounce sink — otherwise 'AAPL' typed one letter at a
    time lands four rows deep before the user has chosen anything."""
    block = _decl_block(_read(_SEARCH_VM), "func selectSearchResult(")
    assert "history.record(" in block, (
        "opening a search result no longer records it — Recent Searches will stay empty for "
        "tickers, which is the original bug"
    )


def test_the_search_screen_has_no_chat_entry_point():
    """The INVERSE of the guard that used to live here.

    Search could once ask Cay AI — an "Ask Cay AI" row above the results plus a strip of
    starter-question chips. Both are gone: chat has its own door in the global header
    (`AskCayAIButton`), and a second one a few points away had two controls claiming the same
    thing. Re-adding one here is the regression; the header button is the only general entry.
    """
    src = _strip_comments(_read(_SEARCH_VIEW))
    for token in ("askCayAIRow", "aiChatCover", "ChatViewModel", "startNewConversation"):
        assert token not in src, (
            f"SearchView can start a chat again ({token}). Search is ticker-only — the Cay AI "
            "door is AskCayAIButton in GlobalHeaderView."
        )
    # Anti-vacuity: this must still be the real search screen.
    assert "SearchResultsSection(" in src, "scan drifted — SearchView no longer renders results"


def test_the_suggestion_chips_are_deleted():
    """The chips ("What is P/E ratio?", "Best tech stocks") routed straight to Cay AI, so they
    went with it. Their type and view are deleted, not merely unreferenced."""
    assert not (_IOS / "Views/Molecules/SearchQueryChip.swift").exists(), (
        "SearchQueryChip.swift is back — the starter-question chips fed the removed chat entry"
    )
    assert "SearchQuerySuggestion" not in _read(_IOS / "Models/SearchModels.swift"), (
        "SearchQuerySuggestion is back in SearchModels.swift"
    )


def test_history_loads_tickers_only():
    """A stored `.question` row has nothing to reopen now, so `load` filters it out."""
    block = _decl_block(_read(_STORE), "private static func load(from defaults: UserDefaults)")
    assert ".filter { $0.kind == .ticker }" in block, (
        "SearchHistoryStore.load no longer filters to tickers — saved questions would render as "
        "rows that do nothing when tapped"
    )


def test_the_question_kind_survives_in_the_model():
    """⚠️ The half that is easy to 'clean up' and expensive to get wrong.

    `record(question:)` is gone and `load` filters `.question` out, so the enum case looks dead.
    It is not: installs upgrading from an earlier build still have `"kind":"question"` rows in
    UserDefaults, `load` decodes the array in ONE shot, and its `catch` deletes the ENTIRE blob.
    Delete the case and every one of those users loses their TICKER history too.
    """
    src = _strip_comments(_read(_IOS / "Models/SearchModels.swift"))
    # Anchored, NOT a substring test. `"case question" in src` is satisfied by
    # `case questionAnything` — a rename would have slipped straight through it. (Found by
    # mutation-testing this very assertion, which is the argument for doing so.)
    assert re.search(r"^\s*case question\s*$", src, re.MULTILINE), (
        "SearchHistoryEntry.Kind.question was removed or renamed. Stored question rows will now "
        "fail to decode, and SearchHistoryStore.load deletes the whole history on a decode "
        "error — so this silently wipes the user's ticker history on upgrade."
    )
    assert "func record(question:" not in _read(_STORE), (
        "record(question:) is back — nothing should write question rows any more"
    )


def test_history_survives_an_emptied_search_field():
    """The original defect in one assertion.

    The debounce sink must clear the LIVE results only. If it ever clears the history again —
    directly, or by going back to one shared array — "Recent Searches" returns to being
    permanently empty at rest, which is exactly how this shipped for so long.
    """
    src = _strip_comments(_read(_SEARCH_VM))
    # Bounded on REAL CODE, not on a `// MARK:` comment: comments are stripped above, so a
    # comment boundary silently degrades to "rest of file" and the assertion below then trips
    # on `removeHistoryEntry` further down. (Caught while writing this file — which is the
    # argument for mutation-testing every guard.)
    start = src.index("$searchText")
    end = src.index(".store(in: &cancellables)", start)
    sink = src[start:end]
    assert "self.results = []" in sink, "scan drifted — the debounce sink no longer clears results"
    assert "history" not in sink, (
        "the debounce sink touches the history store. Emptying the search field must never "
        "erase the user's recorded searches — that WAS the bug."
    )


# ── 3. The news is gone ────────────────────────────────────────────────────────


def test_the_search_screen_has_no_news():
    body = _decl_block(_read(_SEARCH_VIEW), "var body: some View")

    # Anti-vacuity: this must still be the real search screen.
    assert "RecentSearchesSection(" in body, "scan drifted — SearchView no longer renders history"
    assert "SearchResultsSection(" in body, "scan drifted — SearchView no longer renders results"

    for token in ("SearchLatestNewsSection", "latestNews", "NewsDetailView", "selectedNewsArticle"):
        assert token not in body, (
            f"SearchView renders news again ({token}). This screen is search + Cay AI; market "
            "news lives on the Updates tab."
        )


def test_the_news_components_are_deleted():
    for gone in (
        _IOS / "Views/Organisms/SearchLatestNewsSection.swift",
        _IOS / "Views/Molecules/SearchNewsCard.swift",
    ):
        assert not gone.exists(), f"{gone.name} is back — the search screen's news was removed"
    assert "SearchNewsItem" not in _read(_IOS / "Models/SearchModels.swift"), (
        "SearchNewsItem is back in SearchModels.swift"
    )
