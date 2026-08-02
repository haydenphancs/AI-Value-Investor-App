"""The backend and iOS read-along tokenizers agree, and disagreement is never silent.

Word-level read-along works only if two tokenizers written in different languages, in different
repos-within-the-repo, produce the SAME number of tokens for the same string:

    backend  scripts/_forced_align.strip_markup(s).split()
    iOS      JourneyContentStore.spoken(from:) -> components(separatedBy: .whitespacesAndNewlines)

Nothing enforced that agreement. It holds today because both happen to strip exactly `**` — a
coincidence maintained by hand. Add one markup token on either side (`__italic__`, `~~strike~~`,
an inline link) and every aligned lesson silently loses word-level highlighting, because
`AIVoiceManager.playClip` discards timings whose count doesn't match.

`test_journey_schema_parity.py` already checks the COUNT invariant on the bundled content. This
file guards the two things it can't:

  1. the tokenizer *definitions* on both sides still strip the same token set, and
  2. when they do disagree, the app SAYS SO — the discard used to be silent, which made a
     mis-highlighting lesson effectively undiagnosable from a user's bug report.

Pure source + bundled-data inspection. No network, no Supabase.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_ALIGN = _REPO / "backend" / "scripts" / "_forced_align.py"
_STORE = _REPO / "frontend" / "ios" / "ios" / "Services" / "JourneyContentStore.swift"
_VOICE = _REPO / "frontend" / "ios" / "ios" / "Services" / "AIVoiceManager.swift"
_JOURNEY_JSON = _REPO / "frontend" / "ios" / "ios" / "Resources" / "Journey" / "journey_lessons.json"


def _read(p: Path) -> str:
    assert p.exists(), f"expected to exist: {p}"
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. The tokenizers strip the same thing
# ---------------------------------------------------------------------------


def test_backend_strip_markup_removes_only_bold_markers():
    """If this changes, the iOS side must change in the same commit."""
    src = _read(_ALIGN)
    fn = re.search(r"def strip_markup\(s: str\) -> str:(.*?)\n\n", src, re.DOTALL)
    assert fn, "strip_markup disappeared from _forced_align.py"
    patterns = re.findall(r"re\.sub\(r?\"([^\"]+)\"", fn.group(1))
    assert patterns == [r"\*\*"], (
        f"backend strip_markup now strips {patterns} — iOS spoken(from:) must be updated to "
        "match, or every aligned lesson loses word-level read-along"
    )


def test_ios_spoken_removes_only_bold_markers():
    src = _read(_STORE)
    fn = re.search(r"static func spoken\(from markup: String\) -> String \{(.*?)\n    \}", src, re.DOTALL)
    assert fn, "spoken(from:) disappeared from JourneyContentStore.swift"
    removals = re.findall(r'replacingOccurrences\(of: "([^"]+)", with: "([^"]*)"\)', fn.group(1))
    assert removals == [("**", "")], (
        f"iOS spoken(from:) now performs {removals} — backend strip_markup must match"
    )


def test_ios_word_tokenizer_still_splits_on_whitespace_and_drops_empties():
    """`.split()` on the backend == `components(separatedBy:.whitespacesAndNewlines).filter{!isEmpty}`
    on iOS. Losing the `.filter` would double-count every run of consecutive spaces."""
    src = _read(_VOICE)
    fn = re.search(
        r"private func calculateWordRanges\(for text: String\) -> \[NSRange\] \{(.*?)\n    \}",
        src,
        re.DOTALL,
    )
    assert fn, "calculateWordRanges disappeared from AIVoiceManager.swift"
    body = fn.group(1)
    assert "components(separatedBy: .whitespacesAndNewlines)" in body, (
        "iOS word tokenization changed; it must stay equivalent to Python's str.split()"
    )
    assert "filter { !$0.isEmpty }" in body, (
        "the empty-token filter is gone — consecutive whitespace would inflate the word count "
        "and break the 1:1 check against the aligner"
    )


# ---------------------------------------------------------------------------
# 2. Disagreement is never silent
# ---------------------------------------------------------------------------


def test_readalong_discard_is_logged():
    """The regression this file was written for: the mismatch branch used to drop aligned
    timings with no output at all."""
    src = _read(_VOICE)
    fn = re.search(
        r"func playClip\(named name: String.*?readAlongWords = \(readAlong\?\.count == wordRanges\.count\)",
        src,
        re.DOTALL,
    )
    assert fn, "the read-along 1:1 guard in playClip changed shape — re-check this test"
    assert "print(" in fn.group(0), (
        "the read-along discard is SILENT again: a lesson mis-highlights every word and nothing "
        "anywhere records that the aligned timings were thrown away"
    )


def test_empty_and_mismatched_alignment_are_reported_distinctly():
    """`readAlong == []` means the alignment run failed upstream; a non-zero mismatch means
    tokenizer drift. Same symptom, completely different fix — they must be distinguishable."""
    src = _read(_VOICE)
    window = re.search(
        r"if let aligned = readAlong, aligned\.count != wordRanges\.count \{(.*?)\n        \}",
        src,
        re.DOTALL,
    )
    assert window, "the mismatch branch is gone or restructured"
    body = window.group(1)
    assert "aligned.isEmpty" in body, "the empty-alignment case is not called out separately"
    assert body.count("print(") >= 2, "both causes must log distinctly"


def test_lost_token_scan_is_reported():
    """`calculateWordRanges` skips a token it cannot relocate, shortening the list and tripping
    the 1:1 check for a reason that has nothing to do with the backend."""
    src = _read(_VOICE)
    fn = re.search(
        r"private func calculateWordRanges\(for text: String\) -> \[NSRange\] \{(.*?)\n    \}",
        src,
        re.DOTALL,
    )
    assert "ranges.count != words.count" in fn.group(1), (
        "no guard on tokens dropped by the range scan — indistinguishable from backend drift"
    )


# ---------------------------------------------------------------------------
# 3. The bundled content actually satisfies the contract, tokenized the iOS way
# ---------------------------------------------------------------------------


def _ios_tokens(text: str) -> list[str]:
    """Mirror of the iOS pipeline: spoken(from:) then whitespace split, empties dropped."""
    return [t for t in re.split(r"\s+", (text or "").replace("**", "")) if t]


def _aligned_cards():
    data = json.loads(_read(_JOURNEY_JSON))
    for lesson in data.get("lessons", []):
        for idx, card in enumerate(lesson.get("cards", [])):
            if card.get("readAlongWords"):
                yield lesson.get("title", "?"), idx, card


def test_bundled_journey_has_aligned_cards_to_check():
    """Guards the test itself: a restructure that empties this generator would make every
    assertion below vacuous."""
    assert sum(1 for _ in _aligned_cards()) > 100


def test_every_bundled_card_matches_the_ios_tokenization():
    """The count invariant, computed the way the DEVICE computes it rather than with Python's
    default split. Any card failing here silently loses word-level highlighting in the app."""
    bad = []
    for title, idx, card in _aligned_cards():
        expected = len(_ios_tokens(card.get("text", "")))
        actual = len(card["readAlongWords"])
        if expected != actual:
            bad.append(f"{title!r} card {idx}: {actual} timings vs {expected} iOS tokens")
    assert not bad, "read-along count mismatch (word highlighting silently disabled):\n" + "\n".join(bad[:20])


def test_no_bundled_lesson_has_zero_cards():
    """`LessonTopicCardView` subscripts `cards[currentIndex]`; an empty array is a crash.
    `LessonStoryContent`'s init now backstops this, but the shipped data must be clean too."""
    data = json.loads(_read(_JOURNEY_JSON))
    empty = [l.get("title", "?") for l in data.get("lessons", []) if not l.get("cards")]
    assert not empty, f"lessons with no cards: {empty}"


@pytest.mark.parametrize("field", ["text", "start", "end"])
def test_word_entries_carry_every_field_the_decoder_needs(field):
    """`ReadAlongSentence.init(from:)` tolerates missing start/end by defaulting to 0 — which
    would silently pin a word's highlight to the beginning of the clip. Assert the data is
    actually complete rather than relying on that tolerance."""
    missing = []
    for title, idx, card in _aligned_cards():
        for w_i, word in enumerate(card["readAlongWords"]):
            if field not in word:
                missing.append(f"{title!r} card {idx} word {w_i}")
    assert not missing, f"readAlongWords entries missing {field!r}: {missing[:10]}"
