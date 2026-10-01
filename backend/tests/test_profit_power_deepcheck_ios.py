"""Profit Power deep-check fixes (2026-09-30) — iOS half, pinned from Python.

  #17/#40  The margin y-domain was a raw min/max in BOTH Profit Power charts (the live
           Financials card and the report's Profitability drill-down): one tiny-revenue
           period (−40,000%) flattened every other line into the top few points and pushed
           "-40000%" through the 40pt label column. Now `ChartDomain.robust`, every mark is
           drawn CLAMPED, and a pinned value gets an outward edge arrow plus its true value
           (owner decision 1). The tooltip / value rows keep the TRUE number.
  #43      A nil margin was bridged by a straight line (one constant series key per margin).
  #77      The tooltip survived the Annual/Quarterly toggle.
  #78      No 0% rule on a mixed-sign axis; y labels drifted off their grid lines.

Two kinds of test: a Python PORT of the domain / clamp / label geometry (the math the
charts run), and SOURCE-SCAN guards over the Swift. Every scan strips comments and is
brace-bound to the declaration it means (`.claude/rules/testing.md` §3); each was
mutation-tested once by hand.
"""
from __future__ import annotations

import math
import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_PP_CHART = _IOS / "Views" / "Molecules" / "ProfitPowerChartView.swift"
_PROF_CHART = _IOS / "Views" / "Molecules" / "ProfitabilityChartView.swift"
_PP_CARD = _IOS / "Views" / "Organisms" / "ProfitPowerSectionCard.swift"
_PP_LEGEND = _IOS / "Views" / "Molecules" / "ProfitPowerLegendView.swift"
_CHART_DOMAIN = _IOS / "Core" / "Utilities" / "ChartDomain.swift"


# ── Python port of ChartDomain (Core/Utilities/ChartDomain.swift) ────────────

_MIN_SPAN = 1.0


def _make(values, include_zero=True, headroom=0.15, fallback=(0.0, 1.0)):
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return fallback
    lo, hi = min(finite), max(finite)
    if include_zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    span = hi - lo
    pad = max(span * headroom, _MIN_SPAN / 2 if span == 0 else 0.0)
    if hi > 0 or span == 0:
        hi += pad
    if lo < 0:
        lo -= pad
    if hi - lo < _MIN_SPAN:
        mid = (hi + lo) / 2
        lo, hi = mid - _MIN_SPAN / 2, mid + _MIN_SPAN / 2
        if include_zero:
            lo, hi = min(lo, 0.0), max(hi, _MIN_SPAN)
    return (lo, hi)


def _robust(values, include_zero=True, headroom=0.15, fallback=(0.0, 1.0)):
    finite = sorted(v for v in values if math.isfinite(v))
    n = len(finite)
    if n < 4:
        return _make(finite, include_zero, headroom, fallback)
    q1 = finite[n // 4]
    q3 = finite[min((n * 3) // 4, n - 2)]
    iqr = max(q3 - q1, abs(q3) * 0.1, _MIN_SPAN)
    return _make([max(finite[0], q1 - 3 * iqr), min(finite[-1], q3 + 3 * iqr)],
                 include_zero, headroom, fallback)


def _clamp(v, d):
    return min(max(v, d[0]), d[1])


def _is_off_scale(v, d):
    return math.isfinite(v) and (v < d[0] or v > d[1])


def _normalize(v, d, default=0.5):
    span = d[1] - d[0]
    if not (math.isfinite(v) and math.isfinite(span) and span > 0):
        return default
    return min(max((v - d[0]) / span, 0.0), 1.0)


def _grid(d, count=4):
    span = d[1] - d[0]
    step = span / (count + 1)
    return [d[0] + step * k for k in range(1, count + 1)]


# Ports of the two views' domain functions (round 2, 2026-09-30: the pooled fence only
# EXCLUDES a value that is also extreme in size; the axis itself is the old min/max of the
# rest — see `test_profit_power_round2_ios.py`, which reads the threshold from the Swift).

_EXTREME_PCT = 100.0   # ProfitPowerChartView.extremeMarginPct / ProfitabilityChartView.extremeValuePct


def _axis_values(values, fallback):
    finite = [v for v in values if math.isfinite(v)]
    lo, hi = _robust(finite, True, 0.0, fallback)
    return [v for v in finite if abs(v) <= _EXTREME_PCT or lo <= v <= hi]


def _profit_power_domain(values):
    """ProfitPowerChartView.marginDomain."""
    kept = _axis_values(values, (0.0, 50.0))
    rounded = [math.ceil(v / 10) * 10 for v in kept] + [math.floor(v / 10) * 10 for v in kept]
    return _make(rounded, True, 0.0, (0.0, 50.0))


def _old_profit_power_domain(values):
    """The pre-fix marginDomain: every value rounded to 10s, then plain min/max."""
    rounded = [math.ceil(v / 10) * 10 for v in values] + [math.floor(v / 10) * 10 for v in values]
    return _make(rounded, True, 0.0, (0.0, 50.0))


def _profitability_domain(values):
    """ProfitabilityChartView.yDomain."""
    kept = _axis_values(values, (0.0, 1.0))
    lo = min(min(kept, default=0.0), 0.0)
    hi = max(max(kept, default=1.0), 0.0)
    if hi > 0:
        hi *= 1.12
    if lo < 0:
        lo *= 1.12
    if lo == hi:
        hi = lo + 1
    return (lo, hi)


def _old_profitability_domain(values):
    lo = min(min(values, default=0.0), 0.0)
    hi = max(max(values, default=1.0), 0.0)
    if hi > 0:
        hi *= 1.12
    if lo < 0:
        lo *= 1.12
    if lo == hi:
        hi = lo + 1
    return (lo, hi)


def _depth_below_top(v, d):
    """0 = top edge, 1 = bottom edge of the plot."""
    return (d[1] - v) / (d[1] - d[0])


# The live card's sample annual series (ProfitPowerSectionData.sampleData).
_SAMPLE = [38.2, 6.5, 5.2, 9.8, 8.5, 38.5, 8.2, 4.8, 12.5, 9.2, 45.0, 14.8, 11.5, 21.2, 22.5,
           48.2, 18.5, 11.2, 21.5, 20.8, 49.5, 14.5, 11.8, 20.8, 21.2, 52.0, 16.2, 12.5, 22.0,
           21.0]


def _biotech_quarters():
    """20 ordinary quarters of 5 series plus ONE −40,000% net margin (a ~$0.2M quarter)."""
    vals = []
    for i in range(20):
        wobble = (i % 5) - 2
        vals += [60.0 + wobble, 20.0 + 2 * wobble, 15.0 - wobble, 12.0 + wobble, 10.0]
    vals[3] = -40000.0
    return vals


def test_ordinary_data_keeps_exactly_the_old_axis():
    assert _profit_power_domain(_SAMPLE) == _old_profit_power_domain(_SAMPLE) == (0, 60)
    mixed = [-3.0, 78.0, 45.0, 12.0, 20.0, 31.0, -1.0, 8.0]
    assert _profit_power_domain(mixed) == _old_profit_power_domain(mixed) == (-10, 80)
    report = [80.6, 79.1, 72.8, 71.4, 70.5, 65.2, 68.9, 68.1, 69.7, 71.3, 71.4, 74.1]
    assert _profitability_domain(report) == pytest.approx(_old_profitability_domain(report))


def test_one_tiny_revenue_quarter_no_longer_flattens_the_chart():
    vals = _biotech_quarters()
    old = _old_profit_power_domain(vals)
    new = _profit_power_domain(vals)
    assert old[0] == -40000                       # the bug: the axis is the outlier
    assert new[0] >= -100, new                    # the outlier no longer sets the axis
    ordinary = [v for v in vals if v != -40000.0]
    # Ordinary margins now span a real share of the plot, not the top 0.3%.
    spread = _depth_below_top(min(ordinary), new) - _depth_below_top(max(ordinary), new)
    assert spread >= 0.5, (new, spread)
    assert _is_off_scale(-40000.0, new)
    assert _clamp(-40000.0, new) == new[0], "drawn pinned to the bottom edge"
    # R14 (round 2): the guard used to assert only that the OUTLIER is pinned; the
    # first-pass pooled fence also pinned 8 of these 20 ordinary gross margins.
    assert not any(_is_off_scale(v, new) for v in ordinary), "non-outliers stay on scale"
    assert new == _old_profit_power_domain(ordinary), "the rest keeps exactly the old axis"


def test_rivn_shaped_annual_history_reads_better_and_pins_its_first_year():
    # 2021: ~$55M revenue against multi-billion losses; then the losses shrink.
    rows = {
        "2021": [-846, -8540, -9000, -8524, 8],
        "2022": [-207, -405, -393, -407, 9],
        "2023": [-46, -181, -177, -188, 7],
        "2024": [-24, -96, -55, -96, 8],
        "2025": [2, -60, -40, -60, 8],
    }
    vals = [float(v) for r in rows.values() for v in r]
    old = _old_profit_power_domain(vals)
    new = _profit_power_domain(vals)
    assert old[0] == -9000 and new[0] >= -2000, new
    assert all(_is_off_scale(v, new) for v in (-8540, -9000, -8524)), "2021 is pinned"
    assert _depth_below_top(-407, new) >= 0.25, "2022 left the top sliver"
    assert _depth_below_top(-96, new) > 5 * _depth_below_top(-96, old)


def test_profitability_drill_down_roe_outlier_is_pinned():
    # Near-zero equity: ROE 5,000% one year, 15-25% otherwise.
    company = [18.0, 21.0, 5000.0, 24.0, 19.0, 22.0]
    sector = [14.0, 15.0, 15.5, 16.0, 15.0, 14.5]
    d = _profitability_domain(company + sector)
    assert d[1] < 200, d
    assert _is_off_scale(5000.0, d) and _clamp(5000.0, d) == d[1]
    assert all(not _is_off_scale(v, d) for v in company + sector if v != 5000.0)


@pytest.mark.parametrize("value", [-1e6, -40000.0, -1e-9, 0.0, 55.0, 1e6])
def test_every_drawn_value_lies_inside_the_domain(value):
    d = _profit_power_domain(_biotech_quarters() + [value])
    drawn = _clamp(value, d)
    assert d[0] <= drawn <= d[1]
    assert _is_off_scale(value, d) == (drawn != value)


def test_non_finite_values_never_reach_the_domain_or_a_vertex():
    d = _profit_power_domain([float("nan"), float("inf"), 12.0, 30.0, -5.0, 8.0])
    assert all(math.isfinite(b) for b in d) and d[0] < d[1]
    assert not _is_off_scale(float("nan"), d), "a NaN is skipped, never pinned"


def _label_values(d, label_half=7.0, height=240.0, gap=12.0):
    """ProfitPowerChartView.yAxisLabelValues + yLabelCenter."""
    def center(v):
        y = height * (1 - _normalize(v, d))
        return min(max(y, label_half), height - label_half)

    ticks = [d[0]] + _grid(d) + [d[1]]
    if not (d[0] < 0 < d[1]):
        return ticks, center
    zero_y = center(0.0)
    return [t for t in ticks if abs(center(t) - zero_y) >= gap] + [0.0], center


def test_mixed_sign_axis_gets_a_zero_label_that_never_collides():
    d = _profit_power_domain([-3.0, 78.0])                    # -10…80, grid 8/26/44/62
    labels, center = _label_values(d)
    assert 0.0 in labels, "the profit / loss boundary is labelled"
    ys = sorted(center(v) for v in labels)
    assert all(b - a >= 12 for a, b in zip(ys, ys[1:])), ys
    # A tick that lands right on 0 yields ONE "0%" label, not two.
    d2 = _profit_power_domain([-40.0, 60.0])                  # -40…60, grid -20/0/20/40
    labels2, _ = _label_values(d2)
    assert labels2.count(0.0) == 1 and len(labels2) == len(set(labels2))


def test_all_positive_axis_has_no_extra_zero_label():
    labels, _ = _label_values(_profit_power_domain(_SAMPLE))
    assert labels.count(0) == 1, "0 is already the bottom tick"


# ── source-scan guards ────────────────────────────────────────────────────────


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


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _pp_view() -> str:
    return _decl_body(_code(_PP_CHART), "struct ProfitPowerChartView")


def _prof_view() -> str:
    return _decl_body(_code(_PROF_CHART), "struct ProfitabilityChartView")


def test_chart_domain_helpers_still_exist():
    src = _code(_CHART_DOMAIN)
    for name in ("static func robust(", "static func clamp(", "static func isOffScale("):
        assert name in src, name


def test_profit_power_domain_is_robust_and_drives_scale_grid_and_labels():
    view = _pp_view()
    domain = _decl_body(view, "private var marginDomain")
    # Round 2: the fence decides only what is EXCLUDED (`axisValues`); the domain is the old
    # rounded min/max of the rest. Pinned in full by test_profit_power_round2_ios.py.
    assert "axisValues" in domain and "ChartDomain.robust(" not in domain
    axis = _decl_body(view, "private var axisValues")
    assert "ChartDomain.robust(" in axis, "a raw min/max lets one outlier set the axis"
    assert "allValues" in axis
    assert ".chartYScale(domain: marginDomain)" in view
    assert "ChartDomain.gridValues(in: marginDomain" in _decl_body(view, "private var gridValues")
    centre = _decl_body(view, "private func yLabelCenter(")
    assert "ChartDomain.normalize(value, in: marginDomain)" in centre


def test_profit_power_marks_draw_clamped_values_only():
    view = _pp_view()
    verts = _decl_body(view, "private func vertices(for type: ProfitMarginType)")
    assert "ChartDomain.clamp(value, to: domain)" in verts
    assert "ChartDomain.isOffScale(value, in: domain)" in verts
    assert "value.isFinite" in verts, "a non-finite margin must not become a vertex"
    ys = re.findall(r"y: \.value\(\"[^\"]*\", ([^)]*)\)", view)
    marks = [y for y in ys if y not in ("value", "0")]  # the grid and the 0% RuleMarks
    assert marks, "no marks found — the guard proves nothing"
    assert all(y in ("vertex.drawn", "marker.edgeValue") for y in marks), ys
    for builder in ("private func marginPointMark(", "private var sectorAveragePointMark"):
        assert ".filter { !$0.isOffScale }" in _decl_body(view, builder), builder


def test_profit_power_pinned_values_get_an_edge_arrow_and_their_true_value():
    view = _pp_view()
    marks = _decl_body(view, "private var offScaleMarks")
    assert "ForEach(offScaleMarkers)" in marks
    assert "arrowtriangle.up.fill" in marks and "arrowtriangle.down.fill" in marks
    assert ".annotation(" in marks
    label = _decl_body(view, "private func offScaleLabel(")
    assert "CompactNumberFormat.percentString(marker.actual)" in label, "the TRUE value"
    assert "AppColors.textSecondary" in label, "text ink, never a 3:1 series token"
    builder = _decl_body(view, "private var offScaleMarkers")
    assert "actual: vertex.actual" in builder and "edgeValue: vertex.drawn" in builder
    chart = _decl_body(view, "private func chartArea(")
    assert re.search(r"^\s*offScaleMarks\s*$", chart, flags=re.M), "the arrows must be drawn"


def test_profit_power_tooltip_keeps_the_true_value():
    tooltip = _decl_body(_code(_PP_CHART), "struct ProfitPowerTooltipView")
    for field in ("dataPoint.grossMargin", "dataPoint.operatingMargin", "dataPoint.fcfMargin",
                  "dataPoint.netMargin", "dataPoint.sectorAverageNetMargin"):
        assert f"value: {field}" in tooltip, field
    assert "clamp" not in tooltip


def test_profit_power_lines_break_at_a_nil_gap():
    view = _pp_view()
    series = re.findall(r"series: \.value\(\"Series\", (\"[^\"]*\")\)", view)
    assert len(series) == 2, series
    assert all(r"\(vertex.seg)" in s for s in series), (
        "a constant series key bridges a missing period with a straight line"
    )
    verts = _decl_body(view, "private func vertices(for type: ProfitMarginType)")
    assert "prevWasNil = true" in verts and "seg += 1" in verts


def test_profit_power_draws_and_labels_a_zero_rule():
    view = _pp_view()
    assert "minMargin < 0 && maxMargin > 0" in _decl_body(view, "private var showsZeroRule")
    chart = _decl_body(view, "private func chartArea(")
    zero = re.search(r"if showsZeroRule \{\s*RuleMark\(y: \.value\(\"Zero\", 0\)\)", chart)
    assert zero, "the 0% rule"
    labels = _decl_body(view, "private var yAxisLabelValues")
    assert "+ [0]" in labels and "minLabelGap" in labels


def test_profit_power_y_labels_never_wrap():
    labels = _decl_body(_pp_view(), "private var yAxisLabels")
    assert ".lineLimit(1)" in labels and ".minimumScaleFactor(" in labels
    assert "VStack" not in labels, "the Spacer column drifted off the grid lines"
    assert ".position(x: labelWidth / 2, y: yLabelCenter(value))" in labels


def test_profit_power_empty_state_needs_a_company_margin():
    has_data = _decl_body(_pp_view(), "private var hasData")
    assert "sectorAverageNetMargin" not in has_data, (
        "a peer line alone (an all-gap pre-revenue history) is not a Profit Power chart"
    )
    for field in ("p.grossMargin", "p.operatingMargin", "p.fcfMargin", "p.netMargin"):
        assert field in has_data, field


def test_tooltip_is_shown_only_for_a_point_on_this_chart():
    body = _decl_body(_pp_view(), "private var chartBody")
    assert "dataPoints.contains(where: { $0.id == selectedDataPoint.id })" in body


def test_period_toggle_clears_the_selection():
    card = _decl_body(_code(_PP_CARD), "struct ProfitPowerSectionCard")
    m = re.search(r"\.onChange\(of: selectedPeriod\)\s*\{\s*selectedDataPoint = nil\s*\}", card)
    assert m, "switching Annual/Quarterly must clear the tooltip"


def test_legend_drops_the_peer_entry_when_no_peer_line_is_drawn():
    """Decision 4 hides the quarterly peer line for off-calendar fiscal quarters; the
    legend must not keep advertising a line that is not drawn."""
    legend = _decl_body(_code(_PP_LEGEND), "struct ProfitPowerLegendView")
    assert re.search(
        r"if showsPeerLine \{\s*ProfitPowerLegendItem\(\s*marginType: \.sectorAverage", legend,
    ), "the peer legend entry must be conditional"
    card = _decl_body(_code(_PP_CARD), "struct ProfitPowerSectionCard")
    assert "showsPeerLine: currentDataPoints.contains { $0.sectorAverageNetMargin != nil }" in card


def test_profitability_drill_down_domain_is_robust_and_marks_are_clamped():
    view = _prof_view()
    domain = _decl_body(view, "private var yDomain")
    assert "axisValues" in domain and "allValues" not in domain
    assert "ChartDomain.robust(" in _decl_body(view, "private var axisValues")
    chart = _decl_body(view, "private func chartArea(")
    lines = [
        re.search(r"y: \.value\(\"[^\"]*\", ([^\n]*)\),", chart[at: at + 400]).group(1)
        for at in (m.start() for m in re.finditer(r"LineMark\(", chart))
    ]
    assert len(lines) == 2, lines
    assert all(y == "ChartDomain.clamp(item.value, to: yDomain)" for y in lines), lines
    assert chart.count(".filter { !isOffScale($0.value) }") == 2, "dots only for in-range values"
    assert re.search(r"^\s*offScaleMarks\s*$", chart, flags=re.M)
    marks = _decl_body(view, "private var offScaleMarks")
    assert "arrowtriangle.up.fill" in marks and "arrowtriangle.down.fill" in marks
    band = _decl_body(view, "private var bandSegments")
    assert band.count("flatMap(drawnValue)") == 2, "the band must use the drawn (clamped) values"
    drawn = _decl_body(view, "private func drawnValue(")
    assert "ChartDomain.clamp(value, to: yDomain)" in drawn and "isFinite" in drawn


def test_profitability_value_rows_keep_the_true_value():
    view = _prof_view()
    assert "p.company.map { fmtPct($0) }" in _decl_body(view, "private func companyLabels(")
    assert "p.sector.map { fmtPct($0) }" in _decl_body(view, "private func sectorLabels(")
