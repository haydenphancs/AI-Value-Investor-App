"""The persona card must not truncate a persona's name or tagline.

TestFlight, build 1.0 (7): *"For ios below 26, the 'growth' got cut off."* The card
is a fixed 100pt wide and splits the name across two `lineLimit(1)` rows, so
`peter_lynch` rendered as "Everyday Growth" / "Hunter" — and **"Everyday Growth"
measures 100.1pt of 12pt semibold in a 100pt box.** It overflowed at the DEFAULT
text size, by a tenth of a point, with no Dynamic Type involved. A margin that thin
is settled by how a given SwiftUI release rounds text measurement, which is why one
tester saw "Everyday Grow…" and another saw the full name on the same build.

Two guards, because the two halves fail independently:

* the CARD must be able to shrink text rather than clip it (source scan), and
* the DATA must fit at the scale floor the card offers (arithmetic over the real
  strings, so a future persona with a long name fails the build instead of the
  card).

The existing coverage in `test_ios_a11y_parity.py` is HEIGHT-only — `_SCROLLER_CARDS`
pins this card's `minHeight` and `test_persona_tagline_is_no_longer_a_hard_28pt_box`
pins the tagline box. Nothing looked at width, which is why this shipped. PersonaCard
is also absent from that module's `_GUARDED_SITES`.

Widths below are measured, not guessed: rendered with `NSFont.systemFont` at the
sizes `AppTypography` resolves to (`labelSmall` 12pt semibold, `caption` 11pt
regular) and at the 1.4x reading cap those tokens honour.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.agents.persona_config import PERSONA_KEYS, get_persona_config

_REPO = Path(__file__).resolve().parents[2]
_CARD = _REPO / "frontend/ios/ios/Views/Molecules/PersonaCard.swift"

# From PersonaCard.swift: `cardWidth = 100`, and `.frame` deliberately precedes
# `.padding`, so the padding is OUTSIDE the box and the text really is proposed 100pt.
_CARD_WIDTH = 100.0

# Average advance per character, derived from the measured strings rather than from a
# font table: "Everyday Growth" (15 chars) = 100.1pt and "Concentrator" (12) = 78.3pt
# at 12pt semibold, i.e. ~6.6pt/char. Used only to flag a name that is CLEARLY too
# long; the precise check is the render harness, which is not runnable from pytest.
_PT_PER_CHAR_TITLE = 6.7
_READING_CAP = 1.4


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)


def _card() -> str:
    assert _CARD.exists(), f"{_CARD} moved — update this guard, do not delete it"
    return _strip_comments(_CARD.read_text())


def _card_name_lines(name: str) -> tuple[str, str]:
    """Mirror of `AnalysisPersona.cardNameLines` (ResearchModels.swift).

    Kept in step deliberately: the split is what decides the longest line, so a
    change to the Swift rule that this does not follow makes the arithmetic below
    describe a layout that no longer exists.
    """
    words = name.split()
    if len(words) > 1 and words[0].lower() == "the":
        words = words[1:]
    if not words:
        return ("", name)
    return (" ".join(words[:-1]), words[-1])


def _scale_floor(src: str, count: int) -> float:
    floors = re.findall(r"\.minimumScaleFactor\(([\d.]+)\)", src)
    assert len(floors) >= count, (
        f"expected at least {count} minimumScaleFactor sites on the card, found "
        f"{len(floors)} — a Text lost its ability to shrink"
    )
    return max(float(f) for f in floors)


# ── the card can shrink ───────────────────────────────────────────────────────

def test_every_text_on_the_card_can_shrink_before_it_truncates():
    """Three Texts: two title lines and the tagline. The tagline had NO floor at
    all, so truncation was the only remedy its modifier chain permitted."""
    src = _card()
    assert src.count(".minimumScaleFactor(") == 3, (
        "the card has three Texts (title top, title bottom, tagline) and each needs a "
        "scale floor; the tagline shipped without one and that is half this bug"
    )
    assert ".fixedSize(horizontal: false, vertical: true)" in src, (
        "the tagline needs the full ideal height for its width, or a bad height "
        "proposal forces truncation regardless of the scale floor"
    )
    assert src.count(".allowsTightening(true)") == 3, (
        "tightening compresses letter spacing before the glyphs shrink — free margin "
        "on a 100pt card"
    )


def test_the_title_floor_leaves_room_for_the_longest_line():
    """0.8 was not enough: "Everyday Growth" needed 0.75 at the cap.

    Asserted per-Text in source order, NOT as `max(floors)` — the tagline's 0.85
    satisfies a max-based check no matter what the title lines carry, so that
    version of this test passed with the exact floor that shipped the bug.
    """
    floors = [float(f) for f in re.findall(r"\.minimumScaleFactor\(([\d.]+)\)", _card())]
    assert len(floors) == 3, f"expected 3 scale floors, found {floors}"
    title_top, title_bottom, tagline = floors
    for label, value in (("title top", title_top), ("title bottom", title_bottom)):
        assert value <= 0.75, (
            f"the {label} floor is {value}; 0.8 is what shipped 'Everyday Grow…' "
            "because the string needed 0.75 at the 1.4x cap"
        )
    # 0.70, measured by rendering: at 0.85 "Growth at a Reasonable Price" still
    # truncated to "Reasonable Pri…" at the 1.4x cap. The floor has to let the two
    # lines break at a word boundary, and "Reasonable" is 10 unsplittable characters.
    assert tagline <= 0.70, (
        f"the tagline floor is {tagline}; 0.85 was measured to still truncate the "
        "28-character tagline at the 1.4x cap"
    )


def test_the_card_width_pin_survives():
    """`test_ios_a11y_parity` pins this card's HEIGHT only. The width is what
    truncates, and nothing pinned it before this file."""
    src = _card()
    assert "minWidth: cardWidth, maxWidth: cardWidth" in src
    assert "cardWidth: CGFloat = 100" in src, (
        "the card width moved — the arithmetic in this module is calibrated to 100pt"
    )


# ── the data fits ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", sorted(PERSONA_KEYS))
def test_no_persona_name_overflows_the_card_at_the_scale_floor(key):
    """Arithmetic over the REAL names, so adding a sixth persona with a long name
    fails here rather than on a user's card."""
    name = get_persona_config(key).display_name
    top, bottom = _card_name_lines(name)
    floor = _scale_floor(_card(), 3)
    for line in (top, bottom):
        if not line:
            continue
        needed = len(line) * _PT_PER_CHAR_TITLE * _READING_CAP * floor
        assert needed <= _CARD_WIDTH * 1.02, (
            f"{key}: card line {line!r} ({len(line)} chars) needs ~{needed:.0f}pt at the "
            f"{floor} floor and the 1.4x cap, in a {_CARD_WIDTH:.0f}pt card. Shorten the "
            "display_name, or split it differently."
        )


def test_the_reported_name_is_gone_and_the_short_one_is_live():
    assert get_persona_config("peter_lynch").display_name == "The Growth Hunter", (
        "the rename was reverted — 'Everyday Growth' is 100.1pt in a 100pt box"
    )


def test_the_scanners_are_not_vacuous():
    src = _card()
    assert len(src) > 1500, "the card source scan collapsed"
    assert "minimumScaleFactor" in src

    # Comment stripping bites: the fix comments quote every token asserted above.
    assert "minimumScaleFactor" not in _strip_comments("// .minimumScaleFactor(0.75)\nx\n")

    # The line-splitting mirror matches the Swift rule on a known case.
    assert _card_name_lines("The Growth Hunter") == ("Growth", "Hunter")
    assert _card_name_lines("The Everyday Growth Hunter") == ("Everyday Growth", "Hunter")

    # And the arithmetic actually rejects the string that caused the report.
    floor = _scale_floor(src, 3)
    needed = len("Everyday Growth") * _PT_PER_CHAR_TITLE * _READING_CAP * floor
    assert needed > _CARD_WIDTH, (
        "the width arithmetic no longer flags 'Everyday Growth' — it would not have "
        "caught the reported bug"
    )
