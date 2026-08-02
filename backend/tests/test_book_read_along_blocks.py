"""
`gen_book_read_along.narrated_blocks` — the block list the BAKED book read-along table is built
from (`frontend/ios/ios/Models/BookReadAlong.swift`).

WHY THIS MATTERS: narration for a book core stops at the "Action Plan" heading — the action plan
is never spoken. The generator therefore has to TRUNCATE the section list there. It used to
`continue` (skip just the heading and keep emitting the sections after it), which shifted every
following block by one position against the 1:1 index `BookCoreDetailView` pairs blocks with, so
the wrong sentence highlighted — or none did — for the rest of the core. The bug was latent
because books are only re-baked when regenerated.

Pure function over `(kind, text)` pairs — no audio, no network, no torch.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gen_book_read_along.py"


def _load_narrated_blocks():
    """Import the script by path. It lives in `scripts/` (not a package), and importing it pulls
    in `gen_books_swift` as `gba` — skip cleanly if that side of the toolchain isn't importable."""
    spec = importlib.util.spec_from_file_location("_gen_book_read_along", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(_SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"gen_book_read_along not importable here: {type(exc).__name__}: {exc}")
    finally:
        sys.path.pop(0)
    return module.narrated_blocks


narrated_blocks = _load_narrated_blocks()


def _texts(blocks):
    return [text for _, text in blocks]


def test_truncates_at_the_action_plan_heading():
    """THE regression guard: nothing after the action-plan heading may be emitted."""
    sections = [
        ("heading", "The Big Idea"),
        ("body", "Compounding rewards patience."),
        ("heading", "Action Plan"),
        ("body", "Open a brokerage account."),      # never narrated
        ("heading", "Further Reading"),             # never narrated
        ("body", "See chapter four."),              # never narrated
    ]
    assert _texts(narrated_blocks(sections)) == ["The Big Idea", "Compounding rewards patience."]


def test_a_mid_list_action_plan_does_not_merely_skip_one_block():
    """Explicitly pins the OLD behavior as wrong: `continue` would have kept 4 blocks and shifted
    every post-heading one against the iOS index."""
    sections = [
        ("body", "One."),
        ("heading", "Action Plan"),
        ("body", "Two."),
        ("body", "Three."),
    ]
    out = narrated_blocks(sections)
    assert _texts(out) == ["One."]
    assert len(out) != 3, "skipping the heading instead of truncating is the off-by-one bug"


@pytest.mark.parametrize(
    "heading",
    ["Action Plan", "ACTION PLAN", "action plan", "Your Action Plan", "Action  Plan"],
)
def test_action_plan_heading_is_matched_case_and_spacing_insensitively(heading):
    sections = [("body", "Kept."), ("heading", heading), ("body", "Dropped.")]
    assert _texts(narrated_blocks(sections)) == ["Kept."]


def test_action_plan_as_BODY_text_is_not_a_truncation_point():
    """Only a HEADING ends the narration. A sentence that happens to mention an action plan is
    ordinary narrated prose — truncating there would silently lose real content."""
    sections = [
        ("body", "Write an action plan before you invest."),
        ("body", "Then follow it."),
    ]
    assert _texts(narrated_blocks(sections)) == [
        "Write an action plan before you invest.",
        "Then follow it.",
    ]


def test_no_action_plan_keeps_every_section():
    sections = [("heading", "Intro"), ("body", "A."), ("body", "B.")]
    assert _texts(narrated_blocks(sections)) == ["Intro", "A.", "B."]


def test_headings_are_flagged_and_empty_sections_dropped():
    sections = [("heading", "Title"), ("body", ""), ("body", "Real text.")]
    out = narrated_blocks(sections)
    assert [is_heading for is_heading, _ in out] == [True, False]
    assert _texts(out) == ["Title", "Real text."]


def test_markup_is_stripped_but_whitespace_only_sections_are_KEPT():
    """Documents current behavior deliberately rather than asserting an ideal.

    `strip_markup` only removes `**`, so the `if t:` guard drops a section that is EMPTY but
    keeps one that is whitespace-only. That is only a problem if an authored book actually has
    such a section AND iOS does not render it (the pairing is 1:1 against the rendered sections)
    — neither is true of any book in the repo today, so this is pinned as-is. If a phantom block
    ever shows up in a regenerated table, this is the line to revisit.
    """
    out = narrated_blocks([("body", "**Bold** lead."), ("body", "   ")])
    assert _texts(out) == ["Bold lead.", "   "]


def test_empty_input_is_empty_output():
    assert narrated_blocks([]) == []


def test_leading_action_plan_yields_nothing():
    """Degenerate but real: a core whose first heading is the action plan narrates nothing, and
    must produce an empty block list rather than the remaining prose."""
    assert narrated_blocks([("heading", "Action Plan"), ("body", "Do the thing.")]) == []
