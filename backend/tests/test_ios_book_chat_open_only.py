"""The Learn "Ask the Agent" buttons must OPEN a chat, never ask a question.

A TestFlight tester reported it plainly: "Ask the author agent should open the chat only.
No need to open and ask question right away." All three card entry points called the
SEEDING path with a synthesised "Tell me about ..." question, so tapping a card spent a
turn the user never typed and replaced an invitation to ask with a wall of text.

There is no XCTest target, so these invariants are pinned from Python by reading the Swift
source. Per `.claude/rules/testing.md` the scans strip comments first (the explanatory
comment beside a fix otherwise contains every token the test greps for, so the guard passes
on prose after the code is reverted) and brace-bound the declaration under test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"

_CHAT_VM = _IOS / "ViewModels/ChatViewModel.swift"
_CHAT_SCREEN = _IOS / "Views/Screens/AIChatScreen.swift"
_LEARN = _IOS / "Views/Screens/LearnView.swift"
_LIBRARY = _IOS / "Views/Screens/BookLibraryView.swift"
_JOURNEY = _IOS / "Views/Screens/InvestorJourneyView.swift"
_MODELS = _IOS / "Models/LearnModels.swift"

_CARD_SITES = [
    (_LEARN, "private func handleChatWithBook"),
    (_LIBRARY, "private func handleChatWithBook"),
    (_JOURNEY, "private func handleChatWithBook"),
]

# The backend rejects a `context` over CHAT_MESSAGE_HARD_MAX with a Pydantic 422, whose body
# is FastAPI's default shape rather than the {error_code, ...} envelope AppError decodes.
_CONTEXT_HARD_MAX = 8000


def _src(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return path.read_text()


def _strip_comments(text: str) -> str:
    """Drop // line comments and /* */ blocks. String literals in this codebase do not
    contain `//`, and the alternative — asserting against prose — is a vacuous guard."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


def _body(text: str, decl: str) -> str:
    """The brace-bounded body of `decl`, comments stripped."""
    src = _strip_comments(text)
    assert decl in src, f"declaration not found: {decl}"
    start = src.index(decl)
    open_at = src.index("{", start)
    depth, i = 0, open_at
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces after {decl}")


# ── the regression guard for the reported bug ────────────────────────────────────────


@pytest.mark.parametrize("path,decl", _CARD_SITES, ids=lambda v: getattr(v, "stem", v))
def test_the_card_entry_points_do_not_send_a_first_message(path: Path, decl: str):
    body = _body(_src(path), decl)
    assert "firstMessage:" not in body, (
        f"{path.name} still auto-sends a question — the button must OPEN the chat only"
    )
    assert "prepareGroundedConversation" in body


@pytest.mark.parametrize("path,decl", _CARD_SITES, ids=lambda v: getattr(v, "stem", v))
def test_the_card_entry_points_identify_the_book(path: Path, decl: str):
    """Without a reference_id the backend cannot tell which book it is, so no voice fires
    and the source pill has no title. All three used to pass nothing."""
    body = _body(_src(path), decl)
    assert "referenceId:" in body
    assert "curriculumOrder" in body


@pytest.mark.parametrize("path,decl", _CARD_SITES, ids=lambda v: getattr(v, "stem", v))
def test_the_card_entry_points_ground_on_the_study_guide(path: Path, decl: str):
    body = _body(_src(path), decl)
    assert "studyGuideContext" in body


# ── the prepare entry point itself ───────────────────────────────────────────────────


def test_prepare_makes_no_network_call():
    """"No turn spent" means no session row, no credit, no daily-budget claim. The session
    is created lazily by `sendMessage` on the user's first real message."""
    body = _body(_src(_CHAT_VM), "func prepareGroundedConversation")
    for forbidden in ("APIClient", "endpoint:", "await", "Task {"):
        assert forbidden not in body, (
            f"prepareGroundedConversation must not touch the network (found {forbidden!r})"
        )


def test_prepare_leaves_the_session_nil_so_the_first_send_creates_it():
    body = _body(_src(_CHAT_VM), "func prepareGroundedConversation")
    assert "currentSessionId = nil" in body


def test_prepare_invalidates_any_in_flight_seed():
    """Cancellation is cooperative: a seed already past its await would otherwise adopt this
    screen and paint the previous subject's answer into a chat presented as empty."""
    body = _body(_src(_CHAT_VM), "func prepareGroundedConversation")
    assert "seedGeneration &+=" in body
    assert "respondTask?.cancel()" in body


def test_prepare_sets_the_grounding_the_lazy_send_replays():
    """`sendMessage` re-issues these three fields when it finds no session. If prepare stops
    setting one, the first message silently creates an UNGROUNDED session."""
    body = _body(_src(_CHAT_VM), "func prepareGroundedConversation")
    for field in ("currentContext =", "currentContextType =", "currentReferenceId ="):
        assert field in body


def test_the_lazy_session_path_still_replays_the_grounding():
    """The other half of the contract, in sendMessage. Without this, prepare would open a
    grounded-looking chat whose first turn lost the grounding."""
    body = _body(_src(_CHAT_VM), "func sendMessage")
    assert "startNewConversation(" in body
    for field in ("context: currentContext", "contextType: currentContextType",
                  "referenceId: currentReferenceId"):
        assert field in body


# ── the chip must be visible on an EMPTY chat ────────────────────────────────────────


def test_the_grounding_chip_renders_outside_the_conversation_area():
    """`conversationArea` only renders once there is a message, so a chip nested inside it
    can never appear on the empty grounded chat these buttons now open."""
    src = _src(_CHAT_SCREEN)
    assert "GroundedContextChip" in _body(src, "private var chatContent")
    assert "GroundedContextChip" not in _body(src, "private var conversationArea")


def test_the_chip_is_rendered_exactly_once():
    """Two copies would double the chip the moment a conversation starts."""
    stripped = _strip_comments(_src(_CHAT_SCREEN))
    assert stripped.count("GroundedContextChip(") == 1


def test_a_book_reference_resolves_to_a_title_not_a_raw_order():
    body = _body(_src(_CHAT_SCREEN), "private var groundingReferenceLabel")
    assert "case .book:" in body
    assert "curriculumOrder" in body


def test_book_chats_get_book_starter_chips():
    body = _body(_src(_CHAT_SCREEN), "private var suggestions")
    assert "forBook" in body
    assert ".book" in body


# ── copy + budget ────────────────────────────────────────────────────────────────────


def test_the_button_label_no_longer_promises_the_author():
    """Migration 103's rule applied to this surface: describing the METHOD is fine, naming
    the feature after the person is what creates the right-of-publicity / Lanham 43(a) /
    App Review 5.2.1 claim. "Ask the Author Agent" promised the author in a product label."""
    offenders = [
        p.relative_to(_REPO).as_posix()
        for p in _IOS.rglob("*.swift")
        if "Ask the Author Agent" in p.read_text()
    ]
    assert not offenders, f"the author-promising label survives in {offenders}"


def test_the_new_label_is_on_every_book_card():
    for card in ("EducationBookCard", "LibraryBookCard", "SearchBookCard"):
        path = _IOS / f"Views/Molecules/{card}.swift"
        assert 'Text("Ask the Agent")' in _src(path), card


def test_the_grounding_digest_is_bounded_below_the_422_ceiling():
    """An oversized `context` is a Pydantic 422 whose body iOS cannot decode into AppError —
    it surfaces as an unmapped failure rather than a handled one."""
    body = _body(_src(_MODELS), "func studyGuideContext")
    m = re.search(r"maxChars:\s*Int\s*=\s*(\d+)", _strip_comments(_src(_MODELS)))
    assert m, "studyGuideContext must declare a default character budget"
    assert int(m.group(1)) < _CONTEXT_HARD_MAX
    assert "prefix" in body, "the budget must actually be enforced, not just declared"


def test_the_scan_is_not_vacuous():
    """Every assertion above is a substring test; if the slicing silently returned the whole
    file (or nothing) they would pass or fail for the wrong reason."""
    src = _src(_CHAT_VM)
    prepare = _body(src, "func prepareGroundedConversation")
    assert 200 < len(prepare) < len(src)
    assert prepare.startswith("{") and prepare.endswith("}")
    assert "//" not in _strip_comments("code // comment")
