"""
Schema parity tests for the Investor Journey content pipeline.

These pin the contract between the backend `GET /api/v1/learn/journey` response and the iOS
`JourneyAPICard` Codable decoder. The backend passes each lesson's `story_content` JSONB through
verbatim, so the real guard is that the authored content keeps the camelCase shape iOS requires.

Read-along coverage (the new word-highlight feature):
  - `readAlongWords` is optional/additive — content without it still validates.
  - When present, each entry is {text, start, end} with start <= end and monotonic starts.
  - CRITICAL: `len(readAlongWords)` must equal the number of whitespace tokens in
    strip_markup(card["text"]). iOS picks the active word by time, then highlights
    wordRanges[index] — those wordRanges come from the SAME tokenization, so a count mismatch
    silently breaks the highlight (the guard in AIVoiceManager then drops to the estimate).

No network / Supabase — data shape only.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas.journey import JourneyResponse

_BUNDLE_JSON = (
    Path(__file__).resolve().parents[2]
    / "frontend/ios/ios/Resources/Journey/journey_lessons.json"
)


def _strip_markup(s: str) -> str:
    return (s or "").replace("**", "")


def _assert_words_wellformed(words, where: str) -> None:
    assert isinstance(words, list), f"{where}: readAlongWords not a list"
    last = -1.0
    for w in words:
        assert {"text", "start", "end"} <= w.keys(), f"{where}: word missing text/start/end ({w})"
        s, e = w["start"], w["end"]
        assert isinstance(s, (int, float)) and isinstance(e, (int, float)), f"{where}: non-numeric"
        assert s <= e + 1e-6, f"{where}: word start {s} > end {e}"
        assert s >= last - 1e-6, f"{where}: words not monotonic ({s} < {last})"
        last = s


def _worst_case_lesson() -> dict:
    """Minimal lesson row: story_content cards carrying only the required `type`."""
    return {
        "id": "00000000-0000-0000-0000-000000000000",
        "title": "Test Lesson",
        "level": "foundation",
        "sort_order": 1,
        "story_content": {
            "lessonLabel": "LESSON 1: TEST",
            "lessonNumber": 1,
            "totalLessonsInLevel": 1,
            "estimatedMinutes": 2,
            "cards": [
                {"type": "title"},
                {"type": "content", "text": "Hello there.", "audioUrl": None},
                {"type": "completion"},
            ],
        },
    }


def test_worst_case_lesson_validates():
    resp = JourneyResponse(lessons=[_worst_case_lesson()])
    assert len(resp.lessons) == 1
    cards = resp.lessons[0].story_content["cards"]
    assert all("type" in c for c in cards), "every card needs a type for the iOS decoder"


def test_readalong_words_present_validates_and_is_optional():
    lesson = _worst_case_lesson()
    lesson["story_content"]["cards"][1]["readAlongWords"] = [
        {"text": "Hello", "start": 0.0, "end": 0.4},
        {"text": "there.", "start": 0.4, "end": 0.9},
    ]
    resp = JourneyResponse(lessons=[lesson])
    card = resp.lessons[0].story_content["cards"][1]
    _assert_words_wellformed(card["readAlongWords"], "content card")
    # index-alignment invariant: one timing per whitespace token of the (stripped) text.
    assert len(card["readAlongWords"]) == len(_strip_markup(card["text"]).split())


def test_bundled_journey_json_decodes_and_word_timings_align():
    """The real authored content the seeder reads must serve cleanly, and every aligned card's
    readAlongWords must index-align with iOS's word tokenization."""
    assert _BUNDLE_JSON.exists(), f"bundle JSON not found at {_BUNDLE_JSON}"
    data = json.loads(_BUNDLE_JSON.read_text())
    assert data.get("lessons"), "bundle has no lessons"

    aligned = 0
    for lesson in data["lessons"]:
        for card in lesson["cards"]:
            words = card.get("readAlongWords")
            if words is None:
                continue
            aligned += 1
            where = f"{card.get('audioClip')}"
            _assert_words_wellformed(words, where)
            expected = len(_strip_markup(card.get("text", "")).split())
            assert len(words) == expected, (
                f"{where}: readAlongWords count {len(words)} != token count {expected} "
                "— would break iOS word highlighting"
            )
    assert aligned > 0, "expected at least some cards to carry readAlongWords"
