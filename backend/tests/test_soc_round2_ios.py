"""Signal of Confidence, round 2 — the iOS half of R48: the Yield view's caption must be
true for EVERY bar it sits under.

`_build_quarters` still builds a bar as that quarter x4 wherever four consecutive
cash-flow quarters are not on file (a recent listing's first bars, the three after a
missing quarter), and the wire does not mark which bars those are. The first pass's
caption promised "trailing 12 months" for every bar regardless — the KO-style swings it
said could not happen were still on screen beneath it. With no per-bar flag the caption
names both bases.

Source-scan guard (testing.md §3): comments stripped, brace-bound to the live card's
`body`, and mutation-tested in place below (and once by hand).
"""
from __future__ import annotations

import pathlib
import re
from typing import List

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_SECTION = _IOS / "Views" / "Organisms" / "SignalOfConfidenceSectionCard.swift"

_OLD_CAPTION = (
    "Each bar: trailing 12 months of dividends or buybacks ÷ market cap at that quarter's end."
)
_HEDGE = "that quarter × 4 where four consecutive quarters aren't on file"


def _strip_comments(src: str) -> str:
    """Drop // line comments and /* */ blocks, leaving string literals intact."""
    out: List[str] = []
    i, n = 0, len(src)
    in_str = False
    while i < n:
        c = src[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _body(code: str, signature: str) -> str:
    """The brace-balanced body that follows the FIRST occurrence of ``signature``."""
    i = code.find(signature)
    assert i != -1, f"declaration not found: {signature!r}"
    j = code.index("{", i + len(signature) - (1 if signature.endswith("{") else 0))
    depth = 0
    for k in range(j, len(code)):
        if code[k] == "{":
            depth += 1
        elif code[k] == "}":
            depth -= 1
            if depth == 0:
                return code[j + 1:k]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _assert_caption_names_both_bases(src: str) -> None:
    code = _strip_comments(src)
    card = _body(code, "struct SignalOfConfidenceSectionCard: View {")
    body = _body(card, "var body: some View")
    caption_block = _body(body, "if selectedView == .yield && !signalData.dataPoints.isEmpty")
    literals = re.findall(r'Text\("((?:[^"\\]|\\.)*)"\)', caption_block)
    assert len(literals) == 1, f"expected the one basis caption, found {literals!r}"
    caption = literals[0]
    assert "trailing 12 months" in caption and "market cap at that quarter's end" in caption
    assert _HEDGE in caption, \
        "the caption promises trailing-12-month bars, but x4-fallback bars are shown unmarked"
    # No other literal anywhere in the card may promise the TTM basis without the hedge.
    for lit in re.findall(r'"((?:[^"\\]|\\.)*)"', card):
        if "trailing 12 months" in lit:
            assert _HEDGE in lit, f"an unhedged TTM claim: {lit!r}"


def test_the_yield_caption_is_true_for_every_bar():
    _assert_caption_names_both_bases(_SECTION.read_text(encoding="utf-8"))


@pytest.mark.parametrize("mutate", [
    # the first pass's caption, back
    lambda s: re.sub(r'Text\("Each bar: trailing 12 months[^"]*"\)', f'Text("{_OLD_CAPTION}")', s),
    # the hedge dropped from the sentence
    lambda s: s.replace(f" ({_HEDGE})", ""),
    # the hedge moved into a comment (prose that a scan without stripping would accept)
    lambda s: s.replace(f" ({_HEDGE})", "") + f"\n// {_HEDGE}\n",
])
def test_the_caption_guard_rejects_each_mutation(mutate):
    src = _SECTION.read_text(encoding="utf-8")
    mutated = mutate(src)
    assert mutated != src, "mutation anchor drifted"
    with pytest.raises(AssertionError):
        _assert_caption_names_both_bases(mutated)
