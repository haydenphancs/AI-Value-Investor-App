"""Guideline 5.2.1 guard for the Learn library's *card-visible* strings.

Migration 103 renamed the five research personas to investing STYLE names, and
`test_legal_pages.py` guards the legal pages and `DisclaimersView.swift`. Nothing guarded the
**Investor Journey**, and the same shape survived there for months: three lesson titles named
after living investors -- "The Buffett Way", "The Lynch Way", "The Cathie Wood Way" -- plus two
descriptions ("...the Oracle of Omaha himself", "Charlie Munger's secret weapon...").

Why that mattered more than it looks: `seed_journey.build_story_content` renders the label as
``f"LESSON {sort_order}: {title.upper()}"``, so the title is the large card header -- exactly the
frame that ends up in an App Store screenshot, which the launch checklist's metadata rule forbids.

WHAT IS DELIBERATELY *NOT* SCANNED, and why the scan would be worse if it were:

  * A card's ``text`` is the **forced-alignment transcript** for its narration audio. Eleven cards
    name a real investor in prose. Editing that text desynchronises the word-level read-along
    highlighting and requires regenerating TTS + re-running alignment. It is also the case the
    checklist explicitly permits: "Describing methodology in prose is fine ... naming a feature
    after a person is not." Scanning ``text`` would make this test permanently red for content
    that is allowed to say those names.
  * ``slug`` / ``audioClip`` / ``imageUrl`` are internal identifiers, never rendered. The row id
    is ``uuid5(NS, slug)``, so renaming a slug mints a NEW lessons row rather than updating one.

So the assertion is deliberately narrow: the fields a user READS on a card, and nothing else.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_BUNDLE_JSON = _REPO / "frontend/ios/ios/Resources/Journey/journey_lessons.json"
_VENDORED_JSON = _REPO / "backend/data/journey_lessons.json"
_INVESTOR_PATH_MODELS = _REPO / "frontend/ios/ios/Models/InvestorPathModels.swift"

# Same roster as test_legal_pages.py, plus the nicknames a plain surname scan misses.
# "Oracle of Omaha" is the one that actually shipped.
_REAL_INVESTORS = (
    "buffett", "lynch", "munger", "graham", "dalio", "ackman",
    "cathie wood", "burry", "soros", "icahn", "druckenmiller", "klarman",
    "oracle of omaha",
)

# Only the fields a user reads on a lesson card.
_USER_FACING_LESSON_FIELDS = ("title", "description")


def _lessons(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["lessons"]


def _assert_clean(value: str | None, where: str) -> None:
    if not value:
        return
    lowered = value.lower()
    for name in _REAL_INVESTORS:
        assert name not in lowered, (
            f"{where} shows a real investor's name to users ({name!r}): {value!r}. "
            f"The research personas are style names (migration 103) -- a Journey card that "
            f"says 'LESSON 1: THE BUFFETT WAY' contradicts that and is an App Store 5.2.1 / "
            f"right-of-publicity exposure in any screenshot that captures it."
        )


@pytest.mark.parametrize("field", _USER_FACING_LESSON_FIELDS)
def test_no_journey_lesson_card_field_names_a_real_investor(field):
    for lesson in _lessons(_BUNDLE_JSON):
        _assert_clean(lesson.get(field), f"journey_lessons.json lesson[{lesson.get('slug')!r}].{field}")


def test_no_journey_card_headline_names_a_real_investor():
    """`headline` is the second-largest string on a card and is NOT narrated (the transcript is
    `text`), so unlike `text` it is both screenshot-visible and free to change."""
    for lesson in _lessons(_BUNDLE_JSON):
        for i, card in enumerate(lesson.get("cards", [])):
            _assert_clean(card.get("headline"), f"lesson[{lesson.get('slug')!r}].cards[{i}].headline")


def test_the_vendored_backend_copy_has_not_drifted():
    """`seed_journey.py` prefers the frontend JSON but falls back to `backend/data/` when the
    frontend tree is absent (a Railway build context, for instance). A stale fallback would
    re-publish the old titles on the next seed run from that environment."""
    if not _VENDORED_JSON.is_file():
        pytest.skip("no vendored copy")
    assert _VENDORED_JSON.read_bytes() == _BUNDLE_JSON.read_bytes(), (
        "backend/data/journey_lessons.json has drifted from the frontend bundle; a seed run "
        "that falls back to it would republish different content"
    )


# ── The Swift live-fallback copy ──────────────────────────────────────────────
#
# `InvestorJourneyData.sampleData` is NOT a preview mock: `InvestorPathViewModel.swift` returns
# `InvestorJourneyData.sampleData.levels` when the remote fetch comes back empty, so these
# literals render to real users on a cold/offline launch. It carried its own copy of all three
# titles and both descriptions.

def _sample_data_block(source: str) -> str:
    """Brace-bound `static let sampleData` so the scan cannot pass vacuously on a match that
    lives in the preview-only `buffettWaySample` extension further down the same file."""
    start = source.index("static let sampleData")
    depth, i = 0, source.index("{", start)
    open_at = i
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_at : i + 1]
        i += 1
    raise AssertionError("unbalanced braces in sampleData")


def test_the_swift_offline_fallback_names_no_real_investor():
    if not _INVESTOR_PATH_MODELS.is_file():
        pytest.skip(f"{_INVESTOR_PATH_MODELS} not present")

    block = _sample_data_block(_INVESTOR_PATH_MODELS.read_text(encoding="utf-8"))
    # Strip comments first: the explanatory comment beside a fix usually contains every token a
    # scan greps for, so an un-stripped scan passes on prose after the code itself is reverted.
    block = re.sub(r"//[^\n]*", "", block)

    for literal in re.findall(r'(?:title|description):\s*"((?:[^"\\]|\\.)*)"', block):
        _assert_clean(literal, "InvestorJourneyData.sampleData")


def test_the_brace_bounding_actually_excludes_the_preview_sample():
    """Guard-the-guard. If `_sample_data_block` ever returned the whole file, the test above
    would fail on the preview-only `buffettWaySample` -- and if it returned nothing, it would
    pass vacuously forever. Pin that it returns a real, bounded slice."""
    source = _INVESTOR_PATH_MODELS.read_text(encoding="utf-8")
    block = _sample_data_block(source)
    assert 0 < len(block) < len(source), "sampleData block is empty or unbounded"
    assert "buffettWaySample" not in block, "the bound leaked into the preview-only extension"
    assert 'title: "' in block, "the bound is too tight to contain any lesson title at all"
