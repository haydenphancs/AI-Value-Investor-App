"""Every chat context type iOS declares must actually be SENT by some call site.

WHY THIS EXISTS. `JOURNEY_LESSON` was fully built and shipped on the backend —
`ChatContextResolver._resolve_journey_lesson`, its own session-type mapping, its own
grounding chip label and icon, and passing tests — while **no iOS call site ever sent
it**. The branch was unreachable in production for its entire life, and nothing failed:
the enum case existed, the tests passed, the code was simply never called.

That is the same shape as `get_research_identity` (wired to nothing) and the notification
kinds with no visible toggle: a declared capability with no caller. The registry guard
catches it for notifications; this catches it for chat grounding.

Source-level on both sides — no build, no network.
"""

import re
from pathlib import Path

import pytest

from app.schemas.chat import ChatContextType

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_ENUM_FILE = _IOS / "Models" / "ChatConversationModels.swift"

# `.none` is the explicit ABSENCE of context — it is the default, never passed.
_NOT_SENT = {"none"}


def _swift_enum_cases() -> dict[str, str]:
    """Swift case name → wire value, from the ChatContextType enum."""
    src = _ENUM_FILE.read_text()
    block = re.search(r"enum ChatContextType: String \{(.*?)\n\}", src, re.S)
    assert block, "ChatContextType enum not found — this guard would pass vacuously"
    return dict(re.findall(r'case\s+(\w+)\s*=\s*"([^"]+)"', block.group(1)))


def _sent_case_names() -> set[str]:
    """Every `contextType: .x` actually passed at a call site."""
    sent: set[str] = set()
    for swift in _IOS.rglob("*.swift"):
        sent.update(re.findall(r"contextType:\s*\.(\w+)", swift.read_text()))
    return sent


def test_the_scan_finds_both_sides():
    """Guard against the guard: either half matching nothing makes this vacuous."""
    cases = _swift_enum_cases()
    assert len(cases) >= 9, f"expected the full enum, found {sorted(cases)}"
    assert len(_sent_case_names()) >= 5, "found almost no contextType call sites — regex drifted"


def test_swift_enum_matches_the_backend_enum():
    wire_values = set(_swift_enum_cases().values())
    backend = {c.value for c in ChatContextType}
    assert wire_values == backend, (
        f"iOS/backend ChatContextType drift — iOS only: {sorted(wire_values - backend)}, "
        f"backend only: {sorted(backend - wire_values)}"
    )


@pytest.mark.parametrize("case_name", sorted(set(_swift_enum_cases()) - _NOT_SENT))
def test_every_declared_context_type_is_actually_sent(case_name):
    """A context type nobody sends is a backend branch that can never run.

    `journeyLesson` was exactly this: resolver, mapping, chip label and icon all shipped,
    with no caller. Deleting the unused case is an equally valid fix — this only forbids
    the state where both sides exist and never meet.
    """
    assert case_name in _sent_case_names(), (
        f"`.{case_name}` is declared on iOS and handled by the backend resolver, but no "
        f"call site passes it — the branch is unreachable in production"
    )


def test_journey_lesson_is_sent_with_a_reference_id():
    """The resolver matches on the backend lesson id OR title, so a bare context type
    with no reference resolves to nothing and the grounding silently degrades."""
    hits = []
    for swift in _IOS.rglob("*.swift"):
        text = swift.read_text()
        for m in re.finditer(r"contextType:\s*\.journeyLesson", text):
            window = text[m.start():m.start() + 300]
            hits.append(("referenceId:" in window, swift.name))
    assert hits, "no .journeyLesson call site found"
    assert any(ok for ok, _ in hits), (
        f"every .journeyLesson call site omits referenceId: {[n for _, n in hits]}"
    )


# ── Journey lesson TITLES are the join key, on both sides ────────────────────

def test_ios_lesson_titles_cover_the_bundled_journey_content():
    """The reference iOS sends for `.journeyLesson` is the lesson TITLE.

    It has no alternative: `Lesson.id` is a client-side `UUID()` regenerated on every
    launch and meaningless to the backend. The app already relies on this join —
    `JourneyContentStore.cards(forLessonTitled:)` is shipped code — so a title that
    exists in one place and not the other silently produces an UNGROUNDED lesson chat:
    the sheet opens, the chip shows, and the model was told nothing.

    Compared against the BUNDLED content (a local file, no network per testing.md). The
    served DB rows come from the same authoring pipeline, and this was verified against
    live content when the path was first wired: 27/27 matched.
    """
    import json

    bundled = json.loads(
        (_IOS / "Resources" / "Journey" / "journey_lessons.json").read_text()
    )
    lessons = bundled.get("lessons") or []
    assert len(lessons) >= 20, f"bundled journey content looks wrong ({len(lessons)} lessons)"

    content_titles = {(l.get("title") or "").strip() for l in lessons if isinstance(l, dict)}
    content_titles.discard("")

    swift = (_IOS / "Models" / "InvestorPathModels.swift").read_text()
    swift_titles = {m for m in re.findall(r'title:\s*"([^"]+)"', swift)}

    missing = sorted(content_titles - swift_titles)
    assert not missing, (
        f"{len(missing)} bundled lesson title(s) have no matching Swift literal, so a "
        f"lesson chat about them would resolve to nothing: {missing[:5]}"
    )
