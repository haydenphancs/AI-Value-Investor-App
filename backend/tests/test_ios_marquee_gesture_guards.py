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


def _new_gesture_block(changed: str) -> str:
    """The touch-down block keyed on GESTURE IDENTITY (`value.startLocation`), brace-bound.

    Found by its condition rather than by name so the assertion cannot be satisfied by an
    `if !isTouching { … }` block that merely mentions the token in a comment (stripped) or
    somewhere else in `onChanged`.
    """
    m = re.search(r"if [^{\n]*\bstartLocation\b[^{\n]*\{", changed)
    assert m, (
        "no touch-down branch keyed on value.startLocation — the reset is gated on the "
        "timer-released isTouching latch again (F19-1 / F19-2)"
    )
    return _braced(changed, m.group(0)[: m.group(0).index("{")].rstrip())


def test_the_drag_flag_is_reset_on_touch_down_and_never_on_touch_up():
    """`onEnded` and the Button's action BOTH fire on release, with no defined order.
    Clearing `didDrag` in `onEnded` would race the one read it exists for — and would do so
    intermittently, which is the worst way for this to come back.

    And "touch-down" means a NEW GESTURE, not `!isTouching`: that latch is released by a
    timer and stays true for `resumeDelay` after a swipe, so a tap landing in that window
    skipped the reset, inherited the swipe's `didDrag = true`, and was swallowed — with every
    retry re-arming the 3 s watchdog and keeping the row dead (F19-1)."""
    pan = _braced(_source(), "private var pan")
    changed, _, ended = pan.partition(".onEnded")
    assert "didDrag = false" in changed, "didDrag must be reset at touch-down"
    assert "didDrag" not in ended, (
        "didDrag is touched in onEnded — that races the Button action on release"
    )
    latch_block = _braced(changed, "if !isTouching")
    assert "didDrag = false" not in latch_block, (
        "didDrag is reset only under `if !isTouching` — a tap inside the previous swipe's "
        "resume window inherits its didDrag and is swallowed"
    )
    assert "didDrag = false" in _new_gesture_block(changed), (
        "didDrag is not reset in the gesture-identity block, so a new touch can inherit "
        "the previous swipe's flag"
    )


def test_a_still_finger_re_entering_after_the_watchdog_keeps_its_translation():
    """F19-2. The watchdog is re-armed only by MOVE events, so a finger held still for
    `cancelledGestureTimeout` is released while still down and the row drifts under it.
    That is acceptable ONLY if the next nudge applies the incremental delta: zeroing
    `lastTranslation` inside `if !isTouching` re-applied the whole translation since
    touch-down — the ~150 pt jump — on the very "drag, then hold to read" the resume delay
    exists for. Only a NEW gesture (a different `startLocation`) may zero it."""
    pan = _braced(_source(), "private var pan")
    changed, _, ended = pan.partition(".onEnded")
    latch_block = _braced(changed, "if !isTouching")
    assert "lastTranslation = 0" not in latch_block, (
        "lastTranslation is zeroed on every latch re-entry — a finger held still for 3 s "
        "then nudged jumps by its whole translation"
    )
    assert "lastTranslation = 0" in _new_gesture_block(changed), (
        "a NEW gesture must start its translation from zero, or the first delta of the next "
        "touch is measured against the previous gesture's last translation"
    )
    # The same-gesture re-entry still re-latches and commits the drifted position, so the
    # delta below it is measured from where the row actually is.
    assert "isTouching = true" in latch_block and "base = offset(at: .now)" in latch_block
    # …and the incremental delta is still what gets applied.
    assert "let delta = value.translation.width - lastTranslation" in changed
    assert "lastTranslation = 0" in ended, "onEnded must still zero the translation"


def test_gesture_identity_is_cleared_on_end_and_never_by_the_watchdog():
    """`gestureStart` must be cleared in `onEnded` (so a re-tap on the same point is a new
    gesture) and must NOT be cleared by the timer release: with zero events the watchdog
    cannot tell a still finger from a cancelled gesture, and clearing it there brings the
    F19-2 jump straight back."""
    src = _source()
    assert re.search(r"@State private var gestureStart: CGPoint\?", src), (
        "gestureStart must be view @State — a local would not survive between events"
    )
    pan = _braced(src, "private var pan")
    changed, _, ended = pan.partition(".onEnded")
    assert "gestureStart = nil" in ended, "onEnded must clear the gesture identity"
    assert "gestureStart = value.startLocation" in _new_gesture_block(changed), (
        "the identity block must record the new gesture, or every event is a new gesture"
    )
    watchdog = _braced(src, "private func scheduleResume")
    assert "gestureStart" not in watchdog, (
        "the watchdog clears gestureStart — a still finger then nudged is treated as a new "
        "gesture and the ~150 pt jump is back"
    )
    body = _braced(src, "var body: some View")
    assert "gestureStart = nil" in _braced(body, ".onDisappear"), (
        "leaving the screen must drop the identity along with the latch"
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


def test_the_watchdog_is_re_armed_on_every_move_not_only_at_touch_down():
    """Armed only inside the touch-down branch, the 3 s watchdog fired MID-DRAG on any
    finger held longer than that: `isTouching` released under the finger, the row drifted,
    and the next 1 pt move re-entered the branch, zeroed `lastTranslation` and re-applied
    the whole translation — a ~150 pt jump. The `scheduleResume(after:
    cancelledGestureTimeout)` call must sit AFTER the `if !isTouching { … }` block so a
    live drag keeps pushing the deadline back."""
    pan = _braced(_source(), "private var pan")
    changed, _, _ended = pan.partition(".onEnded")
    touch_down = _braced(changed, "if !isTouching")
    assert "scheduleResume(after: Self.cancelledGestureTimeout)" not in touch_down, (
        "the watchdog is armed once at touch-down and never re-armed"
    )
    after_block = changed[changed.index(touch_down) + len(touch_down):]
    assert "scheduleResume(after: Self.cancelledGestureTimeout)" in after_block
    # …and it is armed before the delta is applied, on every event.
    assert after_block.index("scheduleResume(after: Self.cancelledGestureTimeout)") < after_block.index("let delta")


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
