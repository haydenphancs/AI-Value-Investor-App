"""The two TestFlight defects in the suggestion-chip marquee, pinned.

Build 1.0 (8), tester pdh3107@icloud.com, on a real iPhone 17:

    "It doesnt move. Also, when i try to sweep right, it suddenly 'touch' then ask the
     question. But i intent to sweep right only."

BOTH have the same root, and it is specific to a marquee. `MarqueeChipRow` runs a
`DragGesture(minimumDistance: 0)` as a `.simultaneousGesture` alongside chips that are real
`Button`s, and the row translates 1:1 with the finger:

  * The chip travels UNDER the touch, so the Button's bounds never lose it, so its tap is
    never cancelled — and it fires at the end of every swipe. On a credit-charged surface
    that is an accidental spend, not just a stray navigation.
  * SwiftUI CANCELS a simultaneous DragGesture when a child Button claims the touch, and
    DragGesture has no `onCancelled`. `onEnded` was the only thing that cleared `isTouching`,
    so one touch latched the row paused for the rest of the session — "it doesn't move".

Both fixes are invisible in a screenshot and neither has a runtime assertion, so they are
pinned here. Per `.claude/rules/testing.md` §3 the scan strips comments (`^[ \t]*//`, never
`^\s*//`) and brace-bounds the declaration — load-bearing here because the comments beside
each fix name every token asserted below.
"""

from __future__ import annotations

import re
from pathlib import Path

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_ROW = _IOS / "Views" / "Molecules" / "MarqueeChipRow.swift"

_COMMENT = re.compile(r"^[ \t]*//.*$", re.MULTILINE)


def _source() -> str:
    assert _ROW.exists(), f"{_ROW} moved — this guard is scanning nothing"
    return _COMMENT.sub("", _ROW.read_text())


def _braced(source: str, declaration: str) -> str:
    start = source.index(declaration)
    open_brace = source.index("{", start)
    depth, i = 0, open_brace
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces after {declaration!r}")


def test_a_swipe_does_not_fire_the_chip_underneath_it():
    """The reported spend bug. The chip's action must be gated on the touch not having
    travelled — a Button inside a marquee cannot cancel itself."""
    src = _source()
    row = _braced(src, "private var singleRow")
    assert "guard !didDrag else { return }" in row, (
        "the chip fires unconditionally again — a swipe will send a question and spend a "
        "credit the user did not intend"
    )
    pan = _braced(src, "private var pan")
    assert "didDrag = true" in pan, "nothing ever sets didDrag, so the guard above is dead"
    assert "Self.dragSlop" in pan, "the swipe threshold must come from the named constant"


def test_a_fast_flick_counts_as_a_drag():
    """"Sweep right" is a FLICK, and a flick delivers few `onChanged` events — it can release
    while the instantaneous translation is still under the slop, which would let the chip fire
    on exactly the gesture that was reported. `predictedEndTranslation` carries the velocity,
    so the drag is recognised on the first event rather than on one that never arrives."""
    pan = _braced(_source(), "private var pan")
    assert "predictedEndTranslation" in pan, (
        "only the instantaneous translation is checked — a fast swipe can still fire the chip"
    )


def test_the_drag_flag_is_reset_on_touch_down_and_never_on_touch_up():
    """`onEnded` and the Button's action BOTH fire on release, with no defined order.
    Clearing `didDrag` in `onEnded` would race the one read it exists for — and would do so
    intermittently, which is the worst way for this to come back."""
    pan = _braced(_source(), "private var pan")
    changed, _, ended = pan.partition(".onEnded")
    assert "didDrag = false" in changed, "didDrag must be reset at touch-down"
    assert "didDrag" not in ended, (
        "didDrag is touched in onEnded — that races the Button action on release"
    )


def test_the_touch_latch_cannot_outlive_a_cancelled_gesture():
    """The "it doesn't move" half. A simultaneous DragGesture is CANCELLED when a child
    Button claims the touch, and there is no `onCancelled`, so `onEnded` cannot be the only
    release for `isTouching` — one tap would pause the row for the whole session."""
    pan = _braced(_source(), "private var pan")
    changed, _, ended = pan.partition(".onEnded")
    assert "scheduleResume(" in changed, (
        "no release is armed at touch-down, so a cancelled gesture latches isTouching "
        "forever and the marquee stops permanently after the first touch"
    )
    assert "scheduleResume(" in ended, "the polite post-drag resume is gone"


def test_leaving_the_screen_clears_the_touch_latch():
    """Cancelling the pending release without clearing the latch is the same freeze by
    another door: the row returns believing a finger is still down."""
    body = _braced(_source(), "var body: some View")
    disappear = _braced(body, ".onDisappear")
    assert "isTouching = false" in disappear


def test_the_resume_timeouts_are_ordered():
    """The cancelled-gesture backstop must be LONGER than the post-drag resume, or it would
    pre-empt a drag still in progress and yank the row out from under the finger."""
    src = _source()
    resume = re.search(r"resumeDelay: Duration = \.seconds\(([\d.]+)\)", src)
    cancelled = re.search(r"cancelledGestureTimeout: Duration = \.seconds\(([\d.]+)\)", src)
    assert resume and cancelled, "both timeouts must stay named constants"
    assert float(cancelled.group(1)) > float(resume.group(1))


def test_the_scan_is_not_vacuous():
    """Every assertion above is an `in` test, which passes trivially against an empty string
    — including one the brace extractor failed to find."""
    src = _source()
    for decl in ("private var pan", "private var singleRow", "var body: some View"):
        body = _braced(src, decl)
        assert len(body) > 60, decl
    assert "//" not in _braced(src, "private var pan"), "comments must be stripped"
