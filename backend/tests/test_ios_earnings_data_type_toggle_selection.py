"""The selected EPS / Revenue segment is an OUTLINE on the toggle's track, never a coloured fill.

The selected segment used to fill with `primaryBlue`, a TEXT-role token used as a surface, and
ink its label `textPrimary` on it: 3.43 light / 2.54 dark against a 4.5 floor. The app's
control pair (`textPrimary` on `toggleSelectedBackground`, 14.18 / 10.31) was the other fix on
offer, but this toggle's track is `cardBackgroundLight`, and `toggleSelectedBackground` against
it is 1.10 light / 1.37 dark — the selection would all but vanish. So the label stays on the
track (`textPrimary` 15.53 / 14.12, `textSecondary` 6.62 / 7.37) and the selection is a
full-opacity `primaryBlue` stroke (4.52 light / 5.55 dark against the track), the same cue the
Updates chip adopted (`test_ios_updates_chip_selection.py`).

This is a POSITIVE allowlist rather than a ban on the one old spelling: every `.background(`
and `.fill(` must name only `cardBackgroundLight`, every ink only `textPrimary` /
`textSecondary`, and exactly one stroke carries the selection. A ban list would pass on the
next tinted fill nobody thought to list.

Comment-stripped (block, whole-line AND trailing `//`) and brace-bound to the struct, per
testing.md §3 — the explanatory comment beside the fix names every token asserted below.
Mutation-tested by hand on 2026-10-09 (16 mutations, each red): the HEAD file, a `primaryBlue`
fill, a `toggleSelectedBackground` fill, a `.background { … }` closure, a 0.5-opacity stroke, a
`borderFocus` stroke, a dimmed outline overlay, a tinted `isSelected` overlay, a commented-out
overlay, an unconditional `.isSelected` trait, a deleted trait, the trait moved onto the Text, a
dimmed label, a `primaryBlue` label, `let isSelected = true` and a deleted `.contentShape(`.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_TOGGLE = _IOS / "Views" / "Atoms" / "EarningsDataTypeToggle.swift"
_CARD = _IOS / "Views" / "Organisms" / "EarningsSectionCard.swift"

# A phrase from the fix's own comment: present in the raw file, gone once stripped.
_COMMENT_PHRASE = "Selection is an OUTLINE"


def _strip_comments(src: str) -> str:
    """Block comments, full-line `//` comments AND trailing `//` comments — a trailing
    comment on a code line must not satisfy an assertion."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _raw(path: Path) -> str:
    assert path.exists(), f"{path} moved — update this guard, do not delete it"
    return path.read_text(encoding="utf-8")


def _code(path: Path) -> str:
    return _strip_comments(_raw(path))


def _block_at(src: str, at: int, what: str) -> str:
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail(f"unbalanced braces after {what!r}")


def _decl_block(src: str, prefix: str) -> str:
    at = src.find(prefix)
    assert at != -1, f"{prefix!r} not found — this scan has drifted"
    return _block_at(src, at, prefix)


def _call_args(src: str, at: int) -> str:
    """The parenthesised argument list of the call whose name starts at `at`."""
    start = src.index("(", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail(f"unbalanced parens in the call at offset {at}")


def _calls(src: str, name: str) -> list[tuple[int, str]]:
    """Every `.name(` call in `src` as (offset, argument list incl. parens)."""
    return [(m.start(), _call_args(src, m.start()))
            for m in re.finditer(rf"\.{re.escape(name)}\s*\(", src)]


def _squash(args: str) -> str:
    return re.sub(r"\s+", "", args)


def _toggle() -> str:
    block = _decl_block(_code(_TOGGLE), "struct EarningsDataTypeToggle: View")
    # Anti-vacuity: this is the live toggle body, not a preview or a stray copy.
    assert "EarningsDataType.allCases" in block and "seriesTitle" in block, "bound the wrong block"
    assert "#Preview" not in block, "the brace bound ran into the previews"
    return block


def _label(block: str) -> str:
    """The Button's `label:` closure — the segment itself."""
    at = block.find("label:")
    assert at != -1, "the segment Button has no `label:` closure — this scan has drifted"
    return _block_at(block, at, "label:")


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_comment_stripper_actually_strips_this_file():
    raw = _raw(_TOGGLE)
    assert _COMMENT_PHRASE in raw, (
        f"the fix's comment no longer says {_COMMENT_PHRASE!r} — pick a new phrase from it"
    )
    assert _COMMENT_PHRASE not in _code(_TOGGLE), "comments survive stripping — every scan is vacuous"


def test_the_stripper_removes_a_trailing_comment():
    probe = ".background(AppColors.cardBackgroundLight) // .strokeBorder(isSelected ? x : y)"
    assert ".strokeBorder" not in _strip_comments(probe)


def test_the_earnings_card_still_renders_this_toggle():
    """A guard on an atom nobody draws proves nothing."""
    assert re.search(r"\bEarningsDataTypeToggle\s*\(", _code(_CARD)), (
        "EarningsSectionCard no longer builds EarningsDataTypeToggle — retarget this guard"
    )


def test_is_selected_means_this_segment_is_the_selected_type():
    """Every assertion below keys off `isSelected`; it must be the per-segment comparison."""
    lets = re.findall(r"\blet\s+isSelected\s*=\s*([^\n]+)", _toggle())
    assert len(lets) == 1, f"expected exactly one `let isSelected = …`, found {len(lets)}"
    assert _squash(lets[0]) == "selectedType==type", (
        f"`let isSelected = {lets[0].strip()}` must be `selectedType == type`"
    )


# ── the fill never changes with selection ────────────────────────────────────

_ALLOWED_BACKGROUND = re.compile(
    r"\((?:AppColors\.cardBackgroundLight"
    r"|RoundedRectangle\(cornerRadius:AppCornerRadius\.\w+\)\.fill\(AppColors\.cardBackgroundLight\)"
    r"|AppColors\.cardBackgroundLight,in:RoundedRectangle\(cornerRadius:AppCornerRadius\.\w+\))\)"
)
_ALLOWED_FILL = re.compile(r"\(AppColors\.cardBackgroundLight\)")


def test_every_fill_is_the_constant_track_surface():
    """A text token (primaryBlue) or a control fill under the label is the bug.
    Positive: each `.background(`/`.fill(` must be exactly the track surface."""
    block = _toggle()
    backgrounds = _calls(block, "background")
    fills = _calls(block, "fill")
    assert backgrounds or fills, "the toggle lost its track — scan has nothing to check"
    for _, args in backgrounds:
        assert "isSelected" not in args and "selectedType" not in args, (
            f"the fill varies with selection: .background{args}"
        )
        assert _ALLOWED_BACKGROUND.fullmatch(_squash(args)), (
            f".background{args} must name only AppColors.cardBackgroundLight"
        )
    for _, args in fills:
        assert "isSelected" not in args and "selectedType" not in args, (
            f"the fill varies with selection: .fill{args}"
        )
        assert _ALLOWED_FILL.fullmatch(_squash(args)), (
            f".fill{args} must name only AppColors.cardBackgroundLight"
        )
    assert any("cardBackgroundLight" in a for _, a in backgrounds + fills)
    # The trailing-closure spelling `.background { … }` has no parenthesised argument, so the
    # allowlist above would never see a tint written that way.
    assert not re.search(r"\.background\s*\{", block), (
        "a `.background { … }` closure in the toggle — the fill must be the one allowlisted call"
    )


def test_no_overlay_varies_with_selection_except_the_outline():
    """`.overlay(isSelected ? AppColors.primaryBlue.opacity(0.2) : .clear)` would tint under
    the label exactly as the old fill did — only the outline's overlay may read selection."""
    for _, args in _calls(_toggle(), "overlay"):
        if "isSelected" in args or "selectedType" in args:
            assert ".strokeBorder(" in args, (
                f".overlay{args} varies with selection but is not the outline"
            )


# ── the label is only ever the two measured inks ─────────────────────────────

_ALLOWED_INK = re.compile(
    r"\((?:isSelected\?AppColors\.textPrimary:AppColors\.textSecondary"
    r"|AppColors\.textPrimary|AppColors\.textSecondary)\)"
)


def test_the_label_ink_is_text_primary_or_secondary_at_full_opacity():
    """15.53 / 14.12 and 6.62 / 7.37 hold only for these two inks, undimmed, on the track."""
    block = _toggle()
    inks = _calls(block, "foregroundColor") + _calls(block, "foregroundStyle")
    assert inks, "the segment label has no explicit ink — scan has nothing to check"
    for _, args in inks:
        assert _ALLOWED_INK.fullmatch(_squash(args)), (
            f"ink {args} must be `isSelected ? AppColors.textPrimary : AppColors.textSecondary`"
        )
    assert not re.search(r"\.opacity\s*\(", block), (
        "an .opacity( in the toggle dims either the label or the outline below its measured ratio"
    )


# ── the selection is one full-opacity primaryBlue outline ────────────────────

def test_selection_is_exactly_one_full_opacity_primary_blue_stroke_border():
    block = _toggle()
    strokes = _calls(block, "strokeBorder")
    assert len(strokes) == 1, f"expected exactly one .strokeBorder(, found {len(strokes)}"
    at, args = strokes[0]
    assert re.search(r"isSelected\s*\?\s*AppColors\.primaryBlue\b", args), (
        f".strokeBorder{args} must be `isSelected ? AppColors.primaryBlue : …` — "
        "borderFocus / primaryFill is 3.12:1 in dark"
    )
    width = re.search(r"lineWidth\s*:\s*([0-9]+(?:\.[0-9]+)?)", args)
    assert width, f".strokeBorder{args} has no literal lineWidth"
    assert float(width.group(1)) >= 1, f"a {width.group(1)}pt outline is too thin to read as selection"

    # It must sit inside an `.overlay(` whose argument also holds the segment's shape.
    enclosing = []
    for o_at, o_args in _calls(block, "overlay"):
        open_paren = block.index("(", o_at)
        if open_paren < at < open_paren + len(o_args):
            enclosing.append((o_at, o_args))
    assert enclosing, "the strokeBorder is not inside an .overlay( — it would replace the fill"
    assert any("RoundedRectangle(" in o_args for _, o_args in enclosing), (
        "the outline's .overlay( does not draw a RoundedRectangle — it would not follow the segment"
    )
    # And the overlay belongs to the segment (the Button's label), not to the whole track.
    assert ".strokeBorder(" in _label(block), "the outline is not drawn on the segment itself"


def test_no_plain_stroke_in_the_toggle():
    """`.stroke` centres on the path, so half of it is clipped by the track's corner radius."""
    assert not re.search(r"\.stroke\s*\(", _toggle())


def test_the_whole_segment_is_the_tap_target():
    """With no fill under an unselected segment, a Button hit-tests only the glyphs it draws."""
    assert re.search(r"\.contentShape\s*\(", _label(_toggle())), (
        "the segment has no .contentShape( — its padding is no longer tappable"
    )


def test_voiceover_hears_the_selection():
    """The outline is the main visual cue, so the state must reach VoiceOver — on the Button,
    not on the Text inside its label."""
    block = _toggle()
    traits = _calls(block, "accessibilityAddTraits")
    assert traits, "no .accessibilityAddTraits( on the segment"
    # Conditional: `.isSelected` alone contains the substring "isSelected", so a bare
    # `[.isButton, .isSelected]` would announce EVERY segment as selected and still pass a
    # substring check.
    assert any(re.search(r"\bisSelected\s*\?[^:]*\.isSelected\b", a) for _, a in traits), (
        "the segment must add .isSelected only when `isSelected ? …` is true"
    )
    assert "accessibilityAddTraits" not in _label(block), (
        "the trait sits inside the Button's label — put it on the Button"
    )
