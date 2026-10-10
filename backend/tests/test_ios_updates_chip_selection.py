"""The selected Updates chip is an OUTLINE on the ordinary chip fill, never a coloured fill.

The selected chip used to fill with `primaryBlue`, a TEXT-role token used as a surface. The
gain/loss % drawn on it measured 1.05-1.12:1 (effectively invisible) and the ticker 3.43 light
/ 2.54 dark. The fix keeps the fill constant (`cardBackgroundLight`, the surface gain/loss are
declared on) and marks the selection with a full-opacity `primaryBlue` stroke (4.52 light /
5.55 dark against the chip). `borderFocus` / `chipSelectedBackground` alias `primaryFill`,
3.12 in dark, so the "obvious" swaps fail too.

This is a POSITIVE allowlist rather than a ban on the one old spelling: every `.background(`
and `.fill(` must name only `cardBackgroundLight`, and exactly one stroke carries the selection.
A ban list would pass on the next tinted fill nobody thought to list.

Comment-stripped (block, whole-line AND trailing `//`) and brace-bound to the struct, per
testing.md §3 — the explanatory comment beside the fix names every token asserted below.
Mutation-tested by hand on 2026-10-09: the old fill, a `chipSelectedBackground` fill, a
0.5-opacity stroke, a deleted trait and a commented-out overlay each turn this file red; so do
(added after review) an unconditional `.isSelected` trait, a dimmed outline overlay, a tinted
`isSelected` overlay and a `.background { … }` closure.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_BUTTON = _IOS / "Views" / "Molecules" / "UpdatesTabButton.swift"
_BAR = _IOS / "Views" / "Organisms" / "UpdatesTabBar.swift"

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


def _button() -> str:
    block = _decl_block(_code(_BUTTON), "struct UpdatesTabButton: View")
    # Anti-vacuity: this is the live chip body, not a preview or a stray copy.
    assert "tab.title" in block and "formattedChange" in block, "bound the wrong block"
    assert "#Preview" not in block, "the brace bound ran into the previews"
    return block


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_comment_stripper_actually_strips_this_file():
    raw = _raw(_BUTTON)
    assert _COMMENT_PHRASE in raw, (
        f"the fix's comment no longer says {_COMMENT_PHRASE!r} — pick a new phrase from it"
    )
    assert _COMMENT_PHRASE not in _code(_BUTTON), "comments survive stripping — every scan is vacuous"


def test_the_stripper_removes_a_trailing_comment():
    probe = ".background(AppColors.cardBackgroundLight) // .strokeBorder(isSelected ? x : y)"
    assert ".strokeBorder" not in _strip_comments(probe)


# ── the fill never changes with selection ────────────────────────────────────

_ALLOWED_BACKGROUND = re.compile(
    r"\((?:AppColors\.cardBackgroundLight"
    r"|Capsule\(\)\.fill\(AppColors\.cardBackgroundLight\)"
    r"|AppColors\.cardBackgroundLight,in:Capsule\(\))\)"
)
_ALLOWED_FILL = re.compile(r"\(AppColors\.cardBackgroundLight\)")


def test_every_fill_is_the_constant_chip_surface():
    """A text token (primaryBlue) or a `*Fill` alias under the gain/loss % is the bug.
    Positive: each `.background(`/`.fill(` must be exactly the chip surface."""
    block = _button()
    backgrounds = _calls(block, "background")
    fills = _calls(block, "fill")
    assert backgrounds or fills, "the chip lost its fill — scan has nothing to check"
    for _, args in backgrounds:
        assert "isSelected" not in args, f"the chip fill varies with selection: .background{args}"
        assert _ALLOWED_BACKGROUND.fullmatch(re.sub(r"\s+", "", args)), (
            f".background{args} must name only AppColors.cardBackgroundLight"
        )
    for _, args in fills:
        assert "isSelected" not in args, f"the chip fill varies with selection: .fill{args}"
        assert _ALLOWED_FILL.fullmatch(re.sub(r"\s+", "", args)), (
            f".fill{args} must name only AppColors.cardBackgroundLight"
        )
    assert any("cardBackgroundLight" in a for _, a in backgrounds + fills)
    # The trailing-closure spelling `.background { … }` has no parenthesised argument, so the
    # allowlist above would never see a tint written that way.
    assert not re.search(r"\.background\s*\{", block), (
        "a `.background { … }` closure in the chip — the fill must be the one allowlisted call"
    )


def test_no_overlay_varies_with_selection_except_the_outline():
    """`.overlay(isSelected ? AppColors.primaryBlue.opacity(0.2) : .clear)` would tint over the
    gain/loss % exactly as the old fill did — only the outline's overlay may read isSelected."""
    for _, args in _calls(_button(), "overlay"):
        if "isSelected" in args:
            assert ".strokeBorder(" in args, (
                f".overlay{args} varies with selection but is not the outline"
            )


# ── the selection is one full-opacity primaryBlue outline ────────────────────

def test_selection_is_exactly_one_full_opacity_primary_blue_stroke_border():
    block = _button()
    strokes = _calls(block, "strokeBorder")
    assert len(strokes) == 1, f"expected exactly one .strokeBorder(, found {len(strokes)}"
    at, args = strokes[0]
    assert re.search(r"isSelected\s*\?\s*AppColors\.primaryBlue\b", args), (
        f".strokeBorder{args} must be `isSelected ? AppColors.primaryBlue : …` — "
        "borderFocus / primaryFill is 3.12:1 in dark"
    )
    assert ".opacity(" not in args, (
        f".strokeBorder{args} dims primaryBlue — it clears 4.52/5.55 only at full opacity"
    )
    width = re.search(r"lineWidth\s*:\s*([0-9]+(?:\.[0-9]+)?)", args)
    assert width, f".strokeBorder{args} has no literal lineWidth"
    assert float(width.group(1)) >= 1, f"a {width.group(1)}pt outline is too thin to read as selection"

    # It must sit inside an `.overlay(` whose argument also holds the Capsule shape.
    enclosing = []
    for o_at, o_args in _calls(block, "overlay"):
        open_paren = block.index("(", o_at)
        if open_paren < at < open_paren + len(o_args):
            enclosing.append((o_at, o_args))
    assert enclosing, "the strokeBorder is not inside an .overlay( — it would replace the fill"
    assert any("Capsule()" in o_args for _, o_args in enclosing), (
        "the outline's .overlay( does not draw a Capsule() — it would not follow the chip shape"
    )
    for o_at, o_args in enclosing:
        assert ".opacity(" not in o_args, (
            f".overlay{o_args} dims the outline — primaryBlue clears 3:1 only at full opacity"
        )
        # `.overlay(...).opacity(0.5)` dims it from outside the argument list too.
        tail = block[block.index("(", o_at) + len(o_args):]
        assert not re.match(r"\s*\.opacity\s*\(", tail), "the outline's overlay is dimmed"


def test_no_plain_stroke_in_the_chip():
    """`.stroke` centres on the path and is half-clipped by `.clipShape(Capsule())`."""
    assert not re.search(r"\.stroke\s*\(", _button())


def test_voiceover_hears_the_selection():
    """The outline is the only visual cue, so the state must reach VoiceOver."""
    traits = _calls(_button(), "accessibilityAddTraits")
    assert traits, "no .accessibilityAddTraits( on the chip"
    # Conditional: `.isSelected` alone contains the substring "isSelected", so a bare
    # `[.isButton, .isSelected]` would announce EVERY chip as selected and still pass a
    # substring check.
    assert any(re.search(r"\bisSelected\s*\?[^:]*\.isSelected\b", a) for _, a in traits), (
        "the chip must add .isSelected only when `isSelected ? …` is true"
    )


# ── the bar selects by scope ─────────────────────────────────────────────────

def test_the_bar_selects_by_scope_not_by_uuid():
    """`NewsFilterTab ==` compares scope; its UUID id is re-minted on every rebuild."""
    bar = _decl_block(_code(_BAR), "struct UpdatesTabBar: View")
    calls = [m.start() for m in re.finditer(r"\bUpdatesTabButton\s*\(", bar)]
    assert calls, "UpdatesTabBar no longer builds an UpdatesTabButton — scan has drifted"
    for at in calls:
        args = _call_args(bar, at)
        assert re.search(r"isSelected\s*:\s*selectedTab\s*==\s*tab\b", args), (
            f"UpdatesTabButton{args} must pass `isSelected: selectedTab == tab`"
        )
        assert ".id" not in args, f"UpdatesTabButton{args} compares the per-rebuild UUID"
