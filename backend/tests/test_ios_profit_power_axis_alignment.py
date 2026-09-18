"""Profit Power chart: the marks and the x-axis labels share ONE geometry (2026-09-17).

The chart used a categorical `period` x-axis over a flush-divided HStack of labels.
Swift Charts' band placement and the HStack columns are different geometries, so every
dot sat about half a column LEFT of its year / quarter (INTC, annual and quarterly).
GrowthChartView / ProfitabilityChartView never had the problem because they use a
NUMERIC index axis (0…N-1, `.plotDimension(padding: edgeLabelPad)`) and position each
label at `xCenter(index)` — the same pixel the scale gives the mark. This guard pins
Profit Power to that scheme, including the tap → column mapping.

Comment-stripped, brace-bound, mutation-tested by hand (`.claude/rules/testing.md` §3).
"""
from __future__ import annotations

import pathlib
import re

_REPO = pathlib.Path(__file__).resolve().parents[2]
_CHART = _REPO / "frontend" / "ios" / "ios" / "Views" / "Molecules" / "ProfitPowerChartView.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _decl_body(src: str, prefix: str) -> str:
    at = src.index(prefix)
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced braces after {prefix!r}")


def _code() -> str:
    assert _CHART.exists(), "guard is stale — ProfitPowerChartView.swift moved"
    return _strip_comments(_CHART.read_text(encoding="utf-8"))


def test_marks_use_the_numeric_index_axis_not_the_period_string():
    src = _code()
    view = _decl_body(src, "struct ProfitPowerChartView")
    assert view.count('x: .value("i", Double(index))') == 4, "all four mark builders index the x-axis"
    assert 'x: .value("Period"' not in view, "a categorical period axis is the bug"
    assert '.chartXScale(domain: xDomain(), range: .plotDimension(padding: edgeLabelPad))' in view
    dom = _decl_body(view, "private func xDomain()")
    assert "0.0 ... Double(n - 1)" in dom


def test_labels_sit_at_the_same_column_centres_as_the_marks():
    src = _code()
    view = _decl_body(src, "struct ProfitPowerChartView")
    center = _decl_body(view, "private func xCenter(")
    assert re.search(r"edgeLabelPad \+ CGFloat\(index\) / CGFloat\(n - 1\) \* usable", center), center
    labels = _decl_body(view, "private func xAxisLabels(plotWidth: CGFloat)")
    assert ".position(x: xCenter(index, plotWidth: plotWidth)" in labels
    assert "HStack" not in labels, "a flush-divided HStack is the old geometry"
    assert ".frame(width: plotWidth, height: xAxisHeight, alignment: .leading)" in labels
    assert "xAxisLabels(plotWidth: contentWidth)" in view, "the label row must be given the plot width"


def test_tap_selection_maps_through_the_same_scale():
    src = _code()
    view = _decl_body(src, "struct ProfitPowerChartView")
    sel = _decl_body(view, "private func updateSelection(")
    assert "nearestIndex(atX: location.x, plotWidth: chartWidth)" in sel
    assert "ChartDomain.columnIndex" not in sel, "flush columns are not where the marks are"
    near = _decl_body(view, "private func nearestIndex(")
    assert re.search(r"\(\(x - edgeLabelPad\) / usable \* CGFloat\(n - 1\)\)\.rounded\(\)", near), near
    assert "Swift.min(Swift.max(raw, 0), CGFloat(n - 1))" in near, "clamped — never indexes out of range"
