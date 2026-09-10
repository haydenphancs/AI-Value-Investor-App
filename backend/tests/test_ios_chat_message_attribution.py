"""The assistant turn must keep telling VoiceOver who is speaking.

WHY THIS EXISTS. Every assistant message used to open with a visible "✦ Cay AI" row
(`CayAIMessageHeader`: a 20pt avatar plus a name label). It was removed to give the
conversation back ~28pt at the top of every reply — the screen identifies the speaker in
three other places, so on a phone it was expensive repetition.

That row was also the ONLY thing attributing the turn to the assistant. Sighted users still
have the bubble styling; a VoiceOver user, reading turns in sequence, had nothing. The
attribution therefore moved onto the container as an `accessibilityLabel`, and this module
exists because that is now an INVISIBLE invariant: delete the modifier and the screen looks
completely fine, while the chat silently stops saying who is talking.

Deliberately NOT guarded here: whether a visible attribution row exists. That is a design
choice anyone can see in a screenshot, and pinning it would fight a future intentional
change. Guard the invisible half, not the visible one.

Not in `test_ios_a11y_parity.py` despite the name: that module is scoped to Dynamic Type
scaling and has no comment-stripping helper, which this scan requires — `AIMessageContent`'s
own comment explains the removal and necessarily contains the string "Cay AI", so an
un-stripped scan would pass on the prose after the modifier was deleted.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_CONTENT = _IOS / "Views/Molecules/AIMessageContent.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    # `[ \t]*`, NOT `\s*`: `\s` eats the preceding newline and collapses two lines into
    # one, silently shifting every line number derived from the result.
    return re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)


def _code() -> str:
    assert _CONTENT.exists(), f"{_CONTENT} moved — update this guard, do not delete it"
    return _strip_comments(_CONTENT.read_text(encoding="utf-8"))


def _decl_block(src: str, prefix: str) -> str:
    """Brace-matched body of the declaration starting at ``prefix``."""
    at = src.find(prefix)
    assert at != -1, f"{prefix!r} not found — this scan has drifted"
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail(f"unbalanced braces after {prefix!r}")


def test_the_assistant_turn_is_still_attributed_to_cay_ai():
    block = _decl_block(_code(), "var body: some View")
    assert '.accessibilityLabel("Cay AI")' in block, (
        "the assistant message lost its speaker attribution. Nothing on screen changes when "
        "this goes, which is exactly why it needs a test: VoiceOver would read assistant and "
        "user turns back to back with no cue as to which is which."
    )


def test_the_attribution_does_not_flatten_the_answer_into_one_element():
    """`.contain`, never `.combine`.

    The answer holds its own focusable elements — follow-up chips, source pills, the
    expandable thinking card. Combining them collapses the whole turn into a single
    unreadable blob and makes the follow-up chips unreachable, which is a worse outcome
    than the missing label this modifier exists to fix.
    """
    block = _decl_block(_code(), "var body: some View")
    assert ".accessibilityElement(children: .contain)" in block
    assert ".accessibilityElement(children: .combine)" not in block, (
        "combine would make the follow-up chips unfocusable"
    )


def test_this_scan_is_not_vacuous():
    raw = _CONTENT.read_text(encoding="utf-8")
    stripped = _strip_comments(raw)

    assert len(stripped) > 2000, "scanned text implausibly small — the scan may be trivial"
    assert stripped.count("\n") == raw.count("\n"), (
        "the stripper is eating newlines, which shifts every line number derived from it"
    )

    # The comment explaining the removal names "Cay AI". That is what makes stripping
    # load-bearing here: prove the phrase survives in the raw text and that the assertions
    # above are matching CODE, not that explanation.
    assert "Cay AI" in raw
    assert raw.count("Cay AI") > stripped.count("Cay AI"), (
        "expected the removal comment to mention Cay AI; if it was rewritten, this control "
        "needs a new token or the stripper is no longer being exercised"
    )

    # And prove the brace bounder returns a real subset rather than the whole file.
    block = _decl_block(stripped, "var body: some View")
    assert 0 < len(block) < len(stripped)
    assert "private func followUpChips" not in block, "the bounder over-ran its declaration"
