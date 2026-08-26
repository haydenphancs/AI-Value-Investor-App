"""A card that READS as one control must BE one control, edge to edge.

TestFlight, 2026-08-24, on the Signal Ticker Detail screen (Home → Whale
Accumulation / Congressional Buys → a ticker):

    "The first tap looks like it's clickable on everywhere within the tap, but
     it isn't. The tabs under it are perfect."

The header card's `Button` wrapped only the `AMZN ›` line, so the company name,
the "Funds accumulating" line, the price and the market cap were dead pixels
inside a card that is visually a single tappable surface — while every
`SignalHolderRow` beneath it is tappable across its whole width. Nothing catches
this: it compiles, it renders identically, and no runtime assertion or schema
test can see a hit region.

There is no XCTest target, so the invariant is pinned from Python by reading the
Swift source (see .claude/rules/testing.md). Both scans are comment-stripped and
brace-bounded to the declaration they mean, and each carries an anti-vacuity
control — a scan that silently stops matching turns every other assertion green.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SIGNAL_DETAIL = _REPO / "frontend/ios/ios/Views/Screens/SignalTickerDetailView.swift"
_HOLDER_ROW = _REPO / "frontend/ios/ios/Views/Molecules/SignalHolderRow.swift"


def _strip_comments(src: str) -> str:
    """Remove // and /* */ comments so a guard cannot be satisfied by prose.

    The doc comment above `header(_:)` names every token these tests grep for, so
    an un-stripped scan would pass on the EXPLANATION after the code was reverted.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def _decl_body(src: str, prefix: str) -> str:
    """The brace-matched body of the declaration starting at ``prefix``.

    Asserting against a whole FILE passes when the token lives in a different
    declaration — `SignalTickerDetailView` has three other `Button`s (the toolbar
    back button, Retry, and the holder rows' callback), any one of which would
    satisfy a file-wide scan for `.buttonStyle(.plain)`.
    """
    at = src.index(prefix)
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {prefix!r}")


def _code(path: Path) -> str:
    assert path.exists(), f"{path} moved — update this guard, do not delete it"
    return _strip_comments(path.read_text())


# ── the reported defect ──────────────────────────────────────────────────────

def test_the_signal_header_card_is_tappable_as_a_whole():
    """`header(_:)` must OPEN with the Button, so the button's label is the card.

    The regression shape is precise and easy to reintroduce: move the `Button`
    inward so it wraps only the symbol line again. Then the card still renders
    identically and still navigates — from one small run of glyphs.
    """
    body = _decl_body(_code(_SIGNAL_DETAIL), "private func header(")

    # The first statement in the body is the Button — nothing wraps it, so its
    # label IS the card rather than a fragment inside one.
    first = next(ln.strip() for ln in body.splitlines()[1:] if ln.strip())
    assert first.startswith("Button"), (
        f"header(_:) no longer opens with the Button (found {first!r}) — the tap "
        "target has shrunk back to a fragment of the card"
    )

    assert "headerContent(detail)" in body, (
        "the Button's label must be the whole card content"
    )
    assert ".buttonStyle(.plain)" in body, (
        "without .plain the label is tinted as a system button and the card's own "
        "colours are lost"
    )


def test_the_signal_header_content_is_hit_testable_across_its_padding():
    """`.contentShape` AFTER the padding, or the gutter stays dead.

    A `Button` label made of `Text`s and a `Spacer` is hit-tested on the GLYPHS.
    Wrapping the card in a Button is only half the fix: without a content shape
    the padded margins and the `Spacer` gutter between the symbol block and the
    price block still swallow taps — which is most of the card's area, and
    exactly the region the reporter was pressing.
    """
    body = _decl_body(_code(_SIGNAL_DETAIL), "private func headerContent(")

    assert ".contentShape(Rectangle())" in body, (
        "headerContent lost its content shape — the padding and the Spacer gutter "
        "are dead pixels again"
    )
    # Order matters: a content shape applied BEFORE the padding describes the
    # unpadded frame and leaves the margins dead.
    assert body.index(".padding(AppSpacing.lg)") < body.index(".contentShape(Rectangle())"), (
        ".contentShape must come after .padding, or it measures the unpadded frame"
    )


def test_the_header_matches_the_rows_the_reporter_called_perfect():
    """Parity with `SignalHolderRow`, which the report singles out as correct.

    The two sit on the same screen, one above the other. Diverging is what made
    the inconsistency legible to a user in the first place.
    """
    row = _decl_body(_code(_HOLDER_ROW), "struct SignalHolderRow")
    header = _decl_body(_code(_SIGNAL_DETAIL), "private func header(")
    content = _decl_body(_code(_SIGNAL_DETAIL), "private func headerContent(")

    for token in (".buttonStyle(.plain)",):
        assert token in row and token in header, f"{token} must appear in both"
    assert ".contentShape(Rectangle())" in row and ".contentShape(Rectangle())" in content


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_comment_stripper_actually_strips():
    """If this ever stopped stripping, every assertion above would pass on the doc
    comment that explains the rule — the canonical way a source scan goes vacuous."""
    raw = _SIGNAL_DETAIL.read_text()
    assert ".contentShape" in raw
    # The doc comment on header(_:) names `.contentShape` in prose.
    assert "`.contentShape` AFTER the padding" in raw, (
        "the doc comment changed; this control needs a phrase that exists ONLY in "
        "a comment, or it stops proving the stripper works"
    )
    assert "`.contentShape` AFTER the padding" not in _code(_SIGNAL_DETAIL)


def test_the_decl_bounding_actually_bounds():
    """A whole-file scan would be satisfied by the wrong declaration. Prove the
    bound is real: `headerContent` draws the card, `header` does not."""
    src = _code(_SIGNAL_DETAIL)
    assert ".cardSurface(" in _decl_body(src, "private func headerContent(")
    assert ".cardSurface(" not in _decl_body(src, "private func header(")
    # …and the file as a whole contains both, so the distinction is only visible
    # BECAUSE the scan is bounded.
    assert ".cardSurface(" in src


@pytest.mark.parametrize("prefix", [
    "private func header(",
    "private func headerContent(",
])
def test_both_declarations_still_exist(prefix):
    """A rename must fail loudly here rather than silently skipping the guard."""
    assert prefix in _code(_SIGNAL_DETAIL)
