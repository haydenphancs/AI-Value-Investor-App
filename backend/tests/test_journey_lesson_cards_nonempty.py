"""A Journey lesson can never reach the card view with zero cards.

`LessonTopicCardView.swift` reads `storyContent.cards[currentIndex]` with a raw subscript. With
an empty `cards` array that is an index-out-of-range CRASH the instant the lesson opens — and
lesson cards are served from Supabase `lessons.story_content`, so the input is changeable
WITHOUT an app update. A bad seed crashes shipped installs.

The subscript was safe only by convention, upheld in two other files:
  * `JourneyContentStore.cards(forLessonTitled:)` returns `nil` rather than `[]`
  * `InvestorPathViewModel.generateCardsForLesson` always emits a title card

`LessonStoryContent.init` now enforces non-emptiness at the type's boundary instead, so the
guarantee survives a regression in either of those. This file pins all three, because the view
itself is the one place we cannot add a guard right now (it is inside an unrelated in-flight
edit) — and because the whole point is that the invariant must not depend on any single file.

Source + bundled-data inspection only. No network, no Supabase.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"
_MODELS = _IOS / "Models" / "InvestorPathModels.swift"
_STORE = _IOS / "Services" / "JourneyContentStore.swift"
_VIEWMODEL = _IOS / "ViewModels" / "InvestorPathViewModel.swift"
_JOURNEY_JSON = _IOS / "Resources" / "Journey" / "journey_lessons.json"


def _read(p: Path) -> str:
    assert p.exists(), f"expected to exist: {p}"
    return p.read_text(encoding="utf-8")


def _story_content_init() -> str:
    src = _read(_MODELS)
    block = re.search(
        r"struct LessonStoryContent: Identifiable \{(.*?)\n\}", src, re.DOTALL
    )
    assert block, "LessonStoryContent not found in InvestorPathModels.swift"
    init = re.search(r"\n    init\((.*?)\n    \}", block.group(1), re.DOTALL)
    assert init, (
        "LessonStoryContent has no explicit init — it fell back to the memberwise one, which "
        "accepts an empty `cards` array and re-arms the out-of-range crash in LessonTopicCardView"
    )
    return init.group(0)


# ---------------------------------------------------------------------------
# The model-level guarantee (the actual fix)
# ---------------------------------------------------------------------------


def test_explicit_init_guards_empty_cards():
    body = _story_content_init()
    assert "cards.isEmpty" in body, (
        "the explicit init does not check for an empty cards array"
    )
    assert "self.cards = [" in body or "placeholderCard" in body.lower(), (
        "the empty case does not substitute a fallback card"
    )


def test_empty_case_is_logged_not_swallowed():
    """A lesson silently rendering one placeholder card looks like authored content. The log is
    what turns 'this lesson looks wrong' into 'this lesson row failed to decode'."""
    assert "print(" in _story_content_init(), (
        "the zero-card substitution is silent — per CLAUDE.md, an intentionally non-fatal "
        "degradation must still say so"
    )


def test_init_signature_is_unchanged_so_call_sites_still_compile():
    """Deliberately identical to the memberwise init it replaces: `id` was already excluded
    (it is a `let` with an initial value), so every existing caller and #Preview compiles."""
    body = _story_content_init()
    for param in ("lessonLabel:", "lessonNumber:", "totalLessonsInLevel:", "estimatedMinutes:", "cards:"):
        assert param in body, f"init lost the {param!r} parameter — existing call sites break"
    assert "id:" not in body.split("{")[0], "`id` must stay out of the init signature"


def test_placeholder_does_not_impersonate_real_content():
    """It must read as a failure, not as a lesson someone wrote."""
    src = _read(_MODELS)
    block = re.search(
        r"private static var emptyContentPlaceholderCard:.*?\n    \}", src, re.DOTALL
    )
    assert block, "placeholder card accessor not found"
    assert re.search(r"unavailable|couldn't be loaded|could not be loaded", block.group(0), re.I), (
        "the placeholder should state that the lesson failed to load"
    )


# ---------------------------------------------------------------------------
# The upstream conventions that used to be the ONLY protection
# ---------------------------------------------------------------------------


def test_store_still_returns_nil_rather_than_an_empty_array():
    src = _read(_STORE)
    fn = re.search(
        r"func cards\(forLessonTitled[^)]*\)[^{]*\{(.*?)\n    \}", src, re.DOTALL
    )
    assert fn, "cards(forLessonTitled:) not found in JourneyContentStore.swift"
    assert "isEmpty" in fn.group(1), (
        "cards(forLessonTitled:) no longer screens out empty arrays; an empty remote lesson "
        "would flow through as `[]` instead of falling back to generated cards"
    )


def test_viewmodel_generator_always_emits_a_title_card():
    src = _read(_VIEWMODEL)
    fn = re.search(
        r"private func generateCardsForLesson\(_ lesson: Lesson\) -> \[LessonTopicCard\] \{(.*?)\n        return cards",
        src,
        re.DOTALL,
    )
    assert fn, "generateCardsForLesson not found (or its return shape changed)"
    body = fn.group(1)
    assert "cards.append(.titleCard(" in body, "the unconditional title card is gone"
    title_append = body.index("cards.append(.titleCard(")
    preceding = body[:title_append]
    assert "if " not in preceding and "guard " not in preceding, (
        "the title-card append is now conditional, so this generator can return an empty array"
    )


# ---------------------------------------------------------------------------
# The shipped data
# ---------------------------------------------------------------------------


def test_no_bundled_lesson_ships_with_zero_cards():
    data = json.loads(_read(_JOURNEY_JSON))
    lessons = data.get("lessons", [])
    assert lessons, "journey_lessons.json has no lessons at all"
    empty = [l.get("title", "?") for l in lessons if not l.get("cards")]
    assert not empty, f"lessons with zero cards would crash the card view: {empty}"


def test_every_bundled_card_has_a_type_the_decoder_understands():
    """`JourneyAPICard` defaults a missing `type` to "content"; an unknown value would render a
    card with no body. Assert the data uses only the known set."""
    known = {"title", "content", "completion", "quiz", "cta", "image"}
    data = json.loads(_read(_JOURNEY_JSON))
    unknown = {
        c.get("type")
        for l in data.get("lessons", [])
        for c in l.get("cards", [])
        if c.get("type") and c.get("type") not in known
    }
    assert not unknown, f"unrecognized card types in bundled content: {sorted(unknown)}"
