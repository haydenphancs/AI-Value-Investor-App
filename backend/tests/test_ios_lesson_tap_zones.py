"""Journey lesson player: full-height prev/next tap strips (TestFlight 1.0(6), wiser_learn E5).

Tester: *"I can touch left or right (first half of the screen) but i can't on the first half of
the bottom. So, i need all left and all right can also be touched."* The two 30%-wide strips lived
in a ZStack with the card text only; the orb and the pause button were a sibling BELOW it, so the
whole lower band had no tap target (the horizontal drag still worked there, which is why swiping
did and tapping didn't).

Found in the same code and fixed with it: on the COMPLETION card the strips sat ON TOP of the
full-width "Ask Cay AI about this" button, so its left third went back a card and its right third
did nothing.

Source scans, comments stripped and declarations brace-bounded (see `.claude/rules/testing.md`).
Hit-testing itself was verified on the iPhone 17 Pro simulator (taps beside the orb and level with
the pause button navigate; the far-left end of "Ask Cay AI about this" opens the chat).
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
VIEW = REPO / "frontend/ios/ios/Views/Organisms/LessonTopicCardView.swift"


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


def _code() -> str:
    assert VIEW.exists(), f"{VIEW} is missing — every assertion below would be vacuous"
    return _strip_comments(VIEW.read_text())


def _brace_close(src: str, open_at: int) -> int:
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    pytest.fail("unbalanced braces")


def _block_after(src: str, anchor: str) -> str:
    at = src.find(anchor)
    assert at >= 0, f"`{anchor}` not found"
    open_at = src.index("{", at)
    return src[open_at : _brace_close(src, open_at) + 1]


def _enclosing_block(src: str, at: int, opener: str) -> str:
    """The innermost `opener {` block (e.g. `ZStack`) that contains position `at`."""
    for m in reversed(list(re.finditer(re.escape(opener) + r"\s*\{", src[:at]))):
        close = _brace_close(src, m.end() - 1)
        if close > at:
            return src[m.start() : close + 1]
    pytest.fail(f"no `{opener}` encloses offset {at}")


def test_the_strips_share_one_layer_with_the_orb_and_pause_button():
    code = _code()
    body = _block_after(code, "var body: some View")
    call = body.find("tapZones(width: geometry.size.width)")
    assert call >= 0, "the strips are no longer installed from `body`"
    layer = _enclosing_block(body, call, "ZStack")
    assert "cardContentView" in layer and "bottomControlsView" in layer, (
        "the strips must span the card AND the narration controls — with bottomControlsView "
        "outside their layer, the lower half of the screen is dead again")
    # Drawn AFTER the content, so the strips are on top of the (non-interactive) text.
    assert layer.find("bottomControlsView") < layer.find("tapZones(")


def test_no_strips_on_the_completion_card():
    body = _block_after(_code(), "var body: some View")
    call = body.find("tapZones(width: geometry.size.width)")
    guard = body.rfind("if !isCompletionCard", 0, call)
    assert guard >= 0, "the strips must be gated off the completion card"
    gate = _block_after(body[guard:], "if !isCompletionCard")
    assert "tapZones(" in gate, (
        "on the completion card the strips cover the full-width 'Ask Cay AI about this' button")


def test_the_strips_are_thirty_percent_full_height_and_reach_the_bottom_edge():
    zones = _block_after(_code(), "private func tapZones(width: CGFloat)")
    assert zones.count(".frame(width: width * 0.3)") == 2, zones
    assert "Spacer()" in zones, "the centre 40% must stay neutral for the pause button"
    assert "goToPrevious()" in zones and "goToNext()" in zones
    assert zones.find("goToPrevious()") < zones.find("goToNext()"), "left = back, right = forward"
    assert ".ignoresSafeArea(edges: .bottom)" in zones, (
        "the tester's marks run to the bottom edge — the strips must too")
    assert ".contentShape(Rectangle())" in zones, "a Color.clear strip needs a content shape"


def test_voiceover_gets_named_actions_instead_of_invisible_strips():
    code = _code()
    zones = _block_after(code, "private func tapZones(width: CGFloat)")
    assert ".accessibilityHidden(true)" in zones, (
        "labelled invisible strips over the text would steal VoiceOver touch exploration")
    body = _block_after(code, "var body: some View")
    actions = _block_after(body, ".accessibilityActions")
    assert 'Button("Previous card") { goToPrevious() }' in actions
    gate = _block_after(actions, "if currentIndex < storyContent.totalCards - 1")
    assert 'Button("Next card") { goToNext() }' in gate, (
        "\"Next card\" must only be offered when a next card exists — on the last card goToNext() "
        "is a no-op, the same reason the strips are gone from the completion card")
    assert actions.count('"Next card"') == 1


def test_the_swipe_gesture_still_navigates():
    code = _code()
    assert "handleDragEnd(value: value)" in code
    drag = _block_after(code, "private func handleDragEnd(")
    assert "goToNext()" in drag and "goToPrevious()" in drag
