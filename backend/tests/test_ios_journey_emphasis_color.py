"""Journey lesson cards: no lone blue word (developer decision 2026-09-22).

The lesson markup's `**word**` emphasis rendered in `accentCyan`, so almost every Journey card
carried one blue word ("real", "more", "curve", "single", …) that read as a link or a stray
highlight. The developer asked for the colour to go. It is removed at the RENDERER, on purpose:

- the `**` markup is still parsed into segments — the read-along tokenization strips it on both
  sides (backend `_forced_align.strip_markup`, iOS `JourneyContentStore.spoken(from:)`), so
  editing ~234 spans out of the content would force a re-align + reseed for nothing;
- remote content keeps shipping without an app update (`.claude/rules/learn-content.md`).

Comment-stripped, brace-bounded source scans.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IOS = REPO / "frontend/ios/ios"
RENDERER = IOS / "Views/Atoms/ReadingHighlightText.swift"
STORE = IOS / "Services/JourneyContentStore.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        m = re.search(r"\s//", line)
        if m and line[: m.start()].count('"') % 2 == 0:
            line = line[: m.start()]
        out.append(line)
    return "\n".join(out)


def _block_after(src: str, anchor: str) -> str:
    at = src.find(anchor)
    assert at >= 0, f"`{anchor}` not found"
    open_at = src.index("{", at)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at : i + 1]
    pytest.fail(f"unbalanced braces after `{anchor}`")


def _segmented() -> str:
    assert RENDERER.exists()
    return _block_after(_strip_comments(RENDERER.read_text()),
                        "struct ReadingHighlightSegmentedText: View")


def test_the_authors_emphasis_is_not_coloured():
    seg = _segmented()
    assert "isHighlighted" not in seg, (
        "a segment's emphasis flag must not pick its colour again — that is the lone blue word")
    for colour in ("accentCyan", "primaryBlue", "highlightColor"):
        assert colour not in seg, f"{colour} is back in the Journey paragraph renderer"


def test_every_plain_run_uses_the_body_colour_and_only_the_spoken_word_brightens():
    seg = _segmented()
    assert "var baseColor: Color = AppColors.textSecondary" in seg
    assert "var readingColor: Color = AppColors.textPrimary" in seg
    spoken = _block_after(seg, "private func buildHighlightedText()")
    assert spoken.count("portion.foregroundColor = baseColor") == 4, spoken
    assert spoken.count("portion.foregroundColor = readingColor") == 1, spoken


def test_the_markup_is_still_parsed_so_read_along_tokenization_is_untouched():
    store = _strip_comments(STORE.read_text())
    assert "HighlightedTextSegment(part, highlighted: index % 2 == 1)" in store, (
        "the `**` markup must still be split out of the spoken text — removing the colour must "
        "not change tokenization, or every aligned lesson's read-along drifts")
