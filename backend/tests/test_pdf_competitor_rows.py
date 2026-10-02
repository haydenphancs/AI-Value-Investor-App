"""PDF Competitive Landscape: the "competes in" sub-line and the order note (2026-10-01).

TestFlight #57: the AVGO report listed NVIDIA first with nothing saying what the order
meant. Research-sourced lists are now ordered most direct first and say so; industry-peer
lists and every report stored before the change (no `competitor_order`) are ordered by
threat score — and must never be labelled "most direct".

The note needs the marker in the render context. Until the context carries the key, the
template prints NO note (never a wrong one) — pinned below as the "absent key" case.

Network-free: `build_context` + Jinja only (no WeasyPrint).
"""

from __future__ import annotations

import re

import pytest

from app.services.pdf_report_service import build_context, render_html


def _report(competitors):
    return {
        "symbol": "AVGO",
        "company_name": "Broadcom Inc.",
        "moat_competition": {"dimensions": [], "competitors": competitors},
    }


_ROWS = [
    {"name": "Marvell Technology, Inc.", "ticker": "MRVL", "competitive_score": 7.1,
     "market_share_percent": 0.0, "threat_level": "high",
     "segment": "Custom AI accelerators & networking", "score_basis": "relative"},
    {"name": "NVIDIA Corporation", "ticker": "NVDA", "competitive_score": 9.0,
     "market_share_percent": 0.0, "threat_level": "high", "score_basis": "relative"},
]


def _landscape(html: str) -> str:
    start = html.index("Competitive Landscape")
    return html[start:html.index("</table>", start)]


def _ctx(competitors, order="__absent__"):
    ctx = build_context(_report(competitors), None)
    if order != "__absent__":
        ctx["moat"]["competitor_order"] = order
    return ctx


def test_a_known_segment_renders_under_the_name_and_an_unknown_one_does_not():
    block = _landscape(render_html(_ctx(_ROWS, "direct")))
    assert "Competes in: Custom AI accelerators &amp; networking" in block
    assert block.count("Competes in:") == 1                # NVDA has no segment


def test_the_segment_is_autoescaped():
    rows = [{**_ROWS[0], "segment": "<script>alert(1)</script>"}]
    block = _landscape(render_html(_ctx(rows, "direct")))
    assert "<script>" not in block and "&lt;script&gt;" in block


@pytest.mark.parametrize("empty", ["", None])
def test_an_empty_segment_renders_no_sub_line(empty):
    rows = [{**_ROWS[0], "segment": empty}]
    assert "Competes in:" not in _landscape(render_html(_ctx(rows, "direct")))


def test_a_direct_list_says_most_direct_first():
    block = _landscape(render_html(_ctx(_ROWS, "direct")))
    assert "Most direct competitor first" in block
    assert "Highest threat score first" not in block


@pytest.mark.parametrize("order", ["threat", None, "", "DIRECT", "direct "])
def test_anything_but_exactly_direct_is_labelled_threat_ordered(order):
    block = _landscape(render_html(_ctx(_ROWS, order)))
    assert "Highest threat score first" in block
    assert not re.search(r"most direct", block, re.IGNORECASE)


def test_the_marker_travels_from_the_report_through_build_context():
    """`build_context` passes the report's marker through: a stored "direct" list says
    most direct first; a report stored before the marker existed (None) is
    threat-ordered and must say so — never "most direct"."""
    direct = _report(_ROWS)
    direct["moat_competition"]["competitor_order"] = "direct"
    block = _landscape(render_html(build_context(direct, None)))
    assert "Most direct competitor first" in block

    legacy = _report(_ROWS)                                 # no marker at all
    assert build_context(legacy, None)["moat"]["competitor_order"] is None
    block = _landscape(render_html(build_context(legacy, None)))
    assert "Highest threat score first" in block
    assert not re.search(r"most direct", block, re.IGNORECASE)
    assert "MRVL" in block                                  # the table still renders


def test_no_note_at_all_when_a_context_lacks_the_marker_key():
    """Template-level guard for a hand-built context without the key."""
    ctx = _ctx(_ROWS)
    ctx["moat"].pop("competitor_order", None)
    block = _landscape(render_html(ctx))
    assert "Most direct" not in block and "Highest threat score first" not in block


def test_no_competitors_renders_no_landscape():
    assert "Competitive Landscape" not in render_html(_ctx([], "direct"))
