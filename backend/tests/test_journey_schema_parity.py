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
import re
from pathlib import Path

from app.schemas.journey import JourneyResponse

_REPO = Path(__file__).resolve().parents[2]
_BUNDLE_JSON = _REPO / "frontend/ios/ios/Resources/Journey/journey_lessons.json"
_SEED_JOURNEY_PY = _REPO / "backend/scripts/seed_journey.py"
_JOURNEY_STORE_SWIFT = _REPO / "frontend/ios/ios/Services/JourneyContentStore.swift"

# The camelCase card keys the backend serves (from seed_journey.build_story_content) and the iOS
# JourneyAPICard decoder consumes. Pinned here so a rename on EITHER side without the other fails
# loudly — that drift is exactly what silently drops remote lessons on the device.
_CANONICAL_REMOTE_CARD_KEYS = {
    "type", "headline", "text", "audioUrl", "imageUrl", "videoUrl", "cta", "readAlongWords",
}


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


# --- Remote (seeder) card-shape parity ---------------------------------------------------------
# The bundled-JSON tests above exercise the OFFLINE card shape (audioClip / hasImage). They do NOT
# exercise the REMOTE shape the backend actually serves (audioUrl / imageUrl / videoUrl), which is
# what iOS decodes in production. These pin that remote contract on both sides so a drift can't ship.


def _seeder_card_keys() -> set[str]:
    """The card dict keys seed_journey.build_story_content writes into story_content.cards."""
    src = _SEED_JOURNEY_PY.read_text()
    start = src.index("cards_out.append({")
    block = src[start : src.index("})", start)]
    return set(re.findall(r'"(\w+)"\s*:', block))


def _ios_card_coding_keys() -> set[str]:
    """The CodingKeys the iOS JourneyAPICard decoder declares."""
    src = _JOURNEY_STORE_SWIFT.read_text()
    seg = src[src.index("struct JourneyAPICard") :]
    m = re.search(r"enum CodingKeys[^{]*\{(.*?)\}", seg, re.S)
    assert m, "JourneyAPICard CodingKeys block not found — did the struct move/rename?"
    keys: set[str] = set()
    for case_line in re.findall(r"\bcase\s+([^\n]+)", m.group(1)):
        for part in case_line.split("//")[0].split(","):
            name = part.strip().split("=")[0].strip()
            if name:
                keys.add(name)
    return keys


def test_seeder_writes_the_canonical_remote_card_keys():
    assert _seeder_card_keys() == _CANONICAL_REMOTE_CARD_KEYS, (
        "seed_journey.build_story_content card keys drifted from the iOS contract"
    )


def test_ios_decoder_declares_the_canonical_remote_card_keys():
    assert _ios_card_coding_keys() == _CANONICAL_REMOTE_CARD_KEYS, (
        "iOS JourneyAPICard CodingKeys drifted from the seeder/backend card shape"
    )


def test_backend_and_ios_card_shapes_agree():
    """The real cross-language guard: seeder output keys == iOS decoder keys, byte-for-byte."""
    assert _seeder_card_keys() == _ios_card_coding_keys()


def test_card_missing_type_passes_backend_and_is_ios_tolerated():
    """A card missing `type` (e.g. hand-edited in Supabase Studio) must not be a poison pill.

    The backend passes story_content through verbatim (does not reject it), and the iOS decoder
    defaults a missing `type` to a content card (JourneyAPICard.init) rather than throwing and
    dropping ALL remote lessons. This pins the backend half of that contract.
    """
    lesson = _worst_case_lesson()
    lesson["story_content"]["cards"].append({"headline": "no type here"})
    resp = JourneyResponse(lessons=[lesson])  # does NOT raise
    typeless = resp.lessons[0].story_content["cards"][-1]
    assert "type" not in typeless  # served verbatim; iOS is responsible for tolerating it


def test_non_object_card_in_array_still_serves_verbatim():
    """A card that isn't even an object rides through the backend (Optional[Dict] validates the
    story_content dict, not its cards). iOS drops just that element via FailableDecodable."""
    lesson = _worst_case_lesson()
    lesson["story_content"]["cards"].append("not-an-object")  # type: ignore[arg-type]
    resp = JourneyResponse(lessons=[lesson])  # does NOT raise
    assert resp.lessons[0].story_content["cards"][-1] == "not-an-object"


# ---------------------------------------------------------------------------
# Lesson hero artwork: cards[0].imageUrl only
#
# The contract itself does not change — `imageUrl` was always in
# _CANONICAL_REMOTE_CARD_KEYS, which is the point: populating it breaks nothing.
# What is pinned here is the PLACEMENT (the title card carries the hero, nothing
# else does) and the rollout property the whole plan depends on — a lesson with no
# artwork yet must produce imageUrl=None and must not raise.
# ---------------------------------------------------------------------------

class _StubStorage:
    """Minimal stand-in for the Supabase storage client. No network."""

    def __init__(self, bucket):
        self._bucket = bucket

    def get_public_url(self, path):
        return f"https://stub.supabase.co/storage/v1/object/public/{self._bucket}/{path}?"

    def upload(self, *_a, **_k):
        raise AssertionError("upload must not run in a unit test")

    def list(self, *_a, **_k):
        return []


class _StubSupabase:
    def __init__(self):
        self.storage = self

    def from_(self, bucket):
        return _StubStorage(bucket)


def _seed_module():
    import sys
    sys.path.insert(0, str(_REPO / "backend/scripts"))
    import seed_journey
    return seed_journey


def _one_lesson():
    data = json.loads(_BUNDLE_JSON.read_text())
    return data["lessons"][0]


def test_only_the_title_card_carries_an_image_url(tmp_path, monkeypatch):
    """The hero belongs to the first screen. Every other card — including the
    completion card, whose artwork was deliberately dropped — stays None."""
    seed = _seed_module()
    lesson = _one_lesson()
    slug = lesson["slug"]

    art_dir = tmp_path / "journey_art"
    art_dir.mkdir()
    hero = art_dir / f"{slug}.hero.jpg"
    hero.write_bytes(b"\xff\xd8\xff\xdb-not-a-real-jpeg")
    (art_dir / f"{slug}.manifest.json").write_text(json.dumps({
        "slug": slug,
        "masters": {"hero": {"file": hero.name, "sha256": "deadbeef", "bytes": 123}},
        "uploaded": {"hero": "deadbeef"},
    }))
    monkeypatch.setattr(seed, "ART_DIR", art_dir)
    monkeypatch.setattr(seed, "_EXISTING_IMAGES", {hero.name})
    monkeypatch.setattr(seed, "DRY", True)
    monkeypatch.setitem(seed._LEVEL_TOTALS, lesson["level"], 7)

    story = seed.build_story_content(_StubSupabase(), lesson, slug)
    cards = story["cards"]

    assert cards[0]["type"] == "title"
    assert cards[0]["imageUrl"], "title card should carry the hero URL"
    assert "journey-images/lessons/" in cards[0]["imageUrl"]
    assert not cards[0]["imageUrl"].endswith("?"), (
        "trailing ? is a distinct URLCache key on device")
    for card in cards[1:]:
        assert card["imageUrl"] is None, (
            f"{card['type']} card must not carry artwork, got {card['imageUrl']!r}")


def test_image_url_is_none_when_the_lesson_has_no_art(tmp_path, monkeypatch):
    """The whole rollout rests on this: 27 lessons are seeded before all 27 images
    exist, and a lesson with no artwork must serve imageUrl=None without raising."""
    seed = _seed_module()
    lesson = _one_lesson()
    monkeypatch.setattr(seed, "ART_DIR", tmp_path / "empty")
    monkeypatch.setattr(seed, "DRY", True)
    monkeypatch.setitem(seed._LEVEL_TOTALS, lesson["level"], 7)

    story = seed.build_story_content(_StubSupabase(), lesson, lesson["slug"])
    assert all(c["imageUrl"] is None for c in story["cards"])


def test_seeder_never_emits_a_relative_or_empty_image_url(tmp_path, monkeypatch):
    """iOS dispatches on hasPrefix("http"); anything else falls through to a bundle
    asset lookup and renders the placeholder. Empty string is worse — it still
    reserves the slot."""
    seed = _seed_module()
    lesson = _one_lesson()
    art_dir = tmp_path / "journey_art"
    art_dir.mkdir()
    (art_dir / f"{lesson['slug']}.manifest.json").write_text(json.dumps({
        "slug": lesson["slug"],
        "masters": {"hero": {"file": "x.hero.jpg", "sha256": "abc", "bytes": 1}},
    }))
    monkeypatch.setattr(seed, "ART_DIR", art_dir)
    monkeypatch.setattr(seed, "_EXISTING_IMAGES", {"x.hero.jpg"})
    monkeypatch.setattr(seed, "DRY", True)
    monkeypatch.setitem(seed._LEVEL_TOTALS, lesson["level"], 7)

    story = seed.build_story_content(_StubSupabase(), lesson, lesson["slug"])
    for card in story["cards"]:
        url = card["imageUrl"]
        assert url is None or url.startswith("https://"), f"bad imageUrl: {url!r}"
        assert url != "", "an empty imageUrl still reserves the slot on iOS"
