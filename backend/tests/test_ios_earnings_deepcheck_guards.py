"""Earnings card (TickerDetail → Financials → Earnings): source-scan guards, 2026-09-30.

TestFlight 1.0(9), AVGO, Revenue × 3Y: the Q2 '26 surprise bar ran ~574pt below its 100pt
chart, through the legend, the Next Earnings card and the chat chips. The y-domain was
capped for the outlier, but the bar kept its true length and the plot was not clipped.
The deep check behind this fix found eleven more defects on the same card; each test below
pins one, named in its docstring.

There is no XCTest target, so these read the Swift source. Per `.claude/rules/testing.md`
§3 every scan strips comments first (the comment beside a fix names every token a guard
greps for), is brace-bounded to the declaration it means, and was mutation-tested once by
hand. The numeric behaviour is pinned separately by the Python ports in
`test_ios_earnings_deepcheck_geometry.py`; these guards tie those ports to the Swift.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"

_BAR = _IOS / "Views" / "Organisms" / "EarningsSurpriseBarChart.swift"
_CHART = _IOS / "Views" / "Molecules" / "EarningsChartView.swift"
_CARD = _IOS / "Views" / "Organisms" / "EarningsSectionCard.swift"
_ROW = _IOS / "Views" / "Molecules" / "EarningsSurpriseRow.swift"
_LEGEND = _IOS / "Views" / "Molecules" / "EarningsLegend.swift"
_INFO = _IOS / "Views" / "Molecules" / "EarningsInfoSheet.swift"
_DOT = _IOS / "Views" / "Atoms" / "EarningsResultDot.swift"
_LEGEND_ITEM = _IOS / "Views" / "Atoms" / "EarningsLegendItem.swift"
_TOGGLE = _IOS / "Views" / "Atoms" / "EarningsDataTypeToggle.swift"
_LAYOUT = _IOS / "Views" / "Atoms" / "EarningsChartLayout.swift"
_MODELS = _IOS / "Models" / "TickerDetailModels.swift"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _strip_comments(src: str) -> str:
    """Drop `//` and (nested) `/* */` comments, leaving string literals intact — so a
    `"//"` inside a string is not mistaken for a comment, and a comment that quotes code
    cannot satisfy a guard."""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        if src.startswith('"""', i):
            end = src.find('"""', i + 3)
            end = n if end < 0 else end + 3
            out.append(src[i:end])
            i = end
        elif src[i] == '"':
            j = i + 1
            while j < n and src[j] != '"' and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif src.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            i = j
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _decl_body(src: str, prefix: str) -> str:
    """The brace-balanced body that follows the FIRST occurrence of `prefix`, skipping
    braces inside string literals."""
    at = src.index(prefix)
    start = src.index("{", at)
    depth, i, n = 0, start, len(src)
    while i < n:
        ch = src[i]
        if ch == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            i = j + 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces after {prefix!r}")


def _call_args(src: str, call: str) -> str:
    """The parenthesised argument list of the first `call(` in `src`."""
    at = src.index(call)
    start = src.index("(", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced parens after {call!r}")


def _struct(path: pathlib.Path, name: str) -> str:
    return _decl_body(_code(path), f"struct {name}")


def test_helpers_are_not_vacuous():
    """The scans are `substring in body`; prove the helpers really strip and bound."""
    stripped = _strip_comments('let a = "x // y" // gone\n/* a /* nested */ b */let b = 1')
    assert '"x // y"' in stripped and "gone" not in stripped and "nested" not in stripped
    assert "let b = 1" in stripped
    body = _decl_body("struct A { var x: Int { 1 } } struct B { let y = 2 }", "struct A")
    assert "y = 2" not in body and "var x" in body
    assert _decl_body('func f() { let s = "}"; return }', "func f") == '{ let s = "}"; return }'


# ── 1. The overflow itself (#0 #3) ───────────────────────────────────────────


def test_bar_length_comes_from_the_clamped_value_never_the_raw_surprise():
    """AVGO: the domain was capped at ±6% for a −88% quarter, but the bar was built from
    `normalizedY(surprise, …)` — 624pt tall inside a 100pt frame."""
    view = _struct(_BAR, "EarningsSurpriseBarChart")
    geo = _decl_body(view, "static func barGeometry(")
    assert "let plotted = min(max(surprise, domain.min), domain.max)" in geo
    assert "normalizedY(plotted, height: height, domain: domain)" in geo
    assert "isOffScale: plotted != surprise" in geo
    assert "normalizedY(surprise" not in view, "a raw surprise reaches the bar geometry again"

    chart = _decl_body(view, "private func chart(")
    assert "Self.barGeometry(surprise, height: height, domain: domain)" in chart
    assert ".frame(width: barWidth, height: bar.height)" in chart
    assert ".position(x: x, y: bar.centerY)" in chart


def test_plot_is_clipped_as_a_backstop():
    """Exact vertically (the backstop); wider only horizontally, by the label bleed, so an
    edge column's centred value label is not cut (round 2, R31)."""
    chart = _decl_body(_struct(_BAR, "EarningsSurpriseBarChart"), "private func chart(")
    assert re.search(
        r"\.frame\(height: chartHeight\)\s*\.clipShape\(PlotBleedClip\(horizontalBleed: Self\.labelBleed\)\)",
        chart), "the plot GeometryReader lost its clip backstop"
    clip = _struct(_BAR, "PlotBleedClip")
    assert "Path(rect.insetBy(dx: -horizontalBleed, dy: 0))" in clip, \
        "the clip must stay exact vertically"


def test_a_clamped_bar_is_marked_and_states_its_true_value():
    """A clip alone would hide the outlier silently — in 3Y there is no other caption
    (EarningsSurpriseRow renders only in 1Y), so the pinned bar would read as the axis
    edge. The owner's decision: cap at the edge, chevron, and the real value label."""
    view = _struct(_BAR, "EarningsSurpriseBarChart")
    chart = _decl_body(view, "private func chart(")
    assert re.search(r"if bar\.isOffScale \{\s*offScaleMarker\(", chart)

    marker = _decl_body(view, "private func offScaleMarker(")
    assert "OffScaleChevron(pointsUp: pointsUp)" in marker
    assert "Text(Self.signedPercent(surprise))" in marker
    # Inside the plot: chevron in the 7.5% inset, label across the 0% line on its slot's
    # row (round 2, R31 — the placement itself is pinned in test_ios_earnings_round2.py).
    assert re.search(r"let chevronY(: CGFloat)? = pointsUp \? inset / 2 : height - inset / 2", marker)
    assert re.search(
        r"let labelY(: CGFloat)? = pointsUp \? zeroLineY \+ labelOffset : zeroLineY - labelOffset", marker)
    assert ".lineLimit(1)" in marker

    signed = _decl_body(view, "static func signedPercent(")
    assert "EarningsQuarterData.surpriseText(surprise, isMatch: surprise == 0)" in signed


def test_every_bar_speaks_its_true_value_to_voiceover():
    """The TRUE (unclamped) value, in the 1Y row's words (round 2, R10: the first pass
    spoke a -0.4% miss as "0%")."""
    chart = _decl_body(_struct(_BAR, "EarningsSurpriseBarChart"), "private func chart(")
    before_bar = chart.split("RoundedRectangle")[0]
    assert "quarter.formattedSurprise ?? Self.signedPercent(surprise)" in before_bar
    assert ".accessibilityLabel(Text(spoken))" in chart
    assert '", beyond the chart scale"' in chart


def test_non_finite_and_no_consensus_surprises_get_no_bar():
    """One gate (`barSurprise`) for the domain, the bars and the label placement."""
    view = _struct(_BAR, "EarningsSurpriseBarChart")
    gate = _decl_body(view, "static func barSurprise(")
    assert "surprise.isFinite" in gate and "quarter.result != .noEstimate" in gate
    plottable = _decl_body(view, "private var plottableSurprises")
    assert "Self.barSurprise($0)" in plottable
    chart = _decl_body(view, "private func chart(")
    loop = chart[chart.index("ForEach("):chart.index("let x: CGFloat = CGFloat(index)")]
    assert "if let surprise = Self.barSurprise(quarter)" in loop
    assert "quarters.map { Self.barSurprise($0) }" in chart


def test_no_surprises_means_no_chart_not_an_invented_axis():
    """#66: an empty series printed "10% / 0% / -10%" over an empty strip."""
    body = _decl_body(_struct(_BAR, "EarningsSurpriseBarChart"), "var body: some View")
    assert "if !plottableSurprises.isEmpty" in body


def test_the_domain_is_a_symmetric_robust_fence():
    """The Python port in the geometry tests mirrors these exact expressions."""
    dom = _decl_body(_struct(_BAR, "EarningsSurpriseBarChart"), "static func surpriseDomain(")
    assert "ChartDomain.robust(magnitudes, includeZero: true, headroomFraction: 0).upperBound" in dom
    assert "if absMax > fence * 2" in dom
    assert "absMax > secondLargest * 10" in dom and "cap = secondLargest * 1.5" in dom
    assert "let rounded = max(ceil(min(cap, absMax)), 1)" in dom
    assert "return (min: -rounded, max: rounded)" in dom
    norm = _decl_body(_struct(_BAR, "EarningsSurpriseBarChart"), "static func normalizedY(")
    assert "CGFloat(normalized) * height * 0.85 + height * 0.075" in norm


def test_the_preview_carries_the_avgo_outlier():
    raw = _BAR.read_text(encoding="utf-8")
    preview = _decl_body(raw, '#Preview("3Y Revenue - One Outlier")')
    code = _strip_comments(preview)
    assert "surprisePercent: -88.0" in code and "dataType: .revenue" in code
    # Eleven ordinary quarters within ±4% around the outlier.
    ordinary = [float(v) for v in re.findall(r"surprisePercent: (-?[\d.]+)\)", code)]
    assert len([v for v in ordinary if abs(v) <= 4]) == 11, ordinary


# ── 2. One shared y-axis gutter (#13 #29) ────────────────────────────────────


@pytest.mark.parametrize("path,name", [
    (_BAR, "EarningsSurpriseBarChart"),
    (_CHART, "EarningsChartView"),
    (_ROW, "EarningsSurpriseRow"),
])
def test_all_three_charts_read_the_one_gutter(path, name):
    """Revenue × 3Y: the bar chart's 40pt gutter vs the dot chart's 50pt put every bar up
    to half a column left of its dot."""
    view = _struct(path, name)
    assert "EarningsChartLayout.yAxisWidth(for: dataType)" in view
    assert "? 50 : 40" not in view, f"{name} carries its own gutter copy again"
    assert not re.search(r"yAxisWidth: CGFloat \{\s*\d+\s*\}", view)


def test_the_gutter_is_defined_once_and_the_card_passes_the_data_type():
    layout = _decl_body(_code(_LAYOUT), "enum EarningsChartLayout")
    assert "dataType == .revenue ? 50 : 40" in layout
    body = _decl_body(_struct(_CARD, "EarningsSectionCard"), "var body: some View")
    args = _call_args(body, "EarningsSurpriseBarChart(")
    assert "dataType: selectedDataType" in args, args


# ── 3. The dot chart's axis (#14 #28 #68 #69) ────────────────────────────────


def test_value_domain_never_pads_across_zero():
    """Revenue cannot be negative; AVGO's axis read "-2.5B"."""
    view = _struct(_CHART, "EarningsChartView")
    lo = _decl_body(view, "private var minValue: Double")
    assert "return lo >= 0 ? max(lo - pad, 0) : lo - pad" in lo
    hi = _decl_body(view, "private var maxValue: Double")
    assert "return (hi <= 0 && lo < 0) ? min(hi + pad, 0) : hi + pad" in hi
    values = _decl_body(view, "private var earningsValues")
    assert "actual.isFinite" in values and "quarter.hasEstimate" in values


def test_y_labels_and_gridlines_sit_where_their_values_plot():
    view = _struct(_CHART, "EarningsChartView")
    labels = _decl_body(view, "private func yAxisLabels()")
    assert labels.count("normalizedY(") == 3, "each label must be placed at its value's y"
    assert "Spacer()" not in labels, "a Spacer-spread VStack is the old, wrong geometry"
    assert labels.count(".lineLimit(1)") == 3 and labels.count(".minimumScaleFactor(0.7)") == 3
    assert "if hasValues" in labels, "an empty series must print no axis"
    grid = _decl_body(view, "private func gridLines(")
    assert "for value in axisValues" in grid and "normalizedY(value" in grid
    assert "Spacer()" not in grid
    assert "[maxValue, (maxValue + minValue) / 2, minValue]" in _decl_body(view, "private var axisValues")


def test_eps_axis_precision_is_chosen_by_magnitude():
    fmt = _decl_body(_struct(_CHART, "EarningsChartView"), "private func formatYValue(")
    assert "let magnitude = abs(value)" in fmt
    assert "magnitude >= 100" in fmt and "magnitude >= 10" in fmt
    assert "value >= 100" not in fmt and "value >= 10" not in fmt


def test_no_estimate_dot_for_a_quarter_without_consensus():
    body = _decl_body(_struct(_CHART, "EarningsChartView"), "var body: some View")
    estimate_loop = body[body.index("ForEach("):body.index(".fill(AppColors.textSecondary)")]
    assert "if quarter.hasEstimate" in estimate_loop


def test_price_overlay_is_scaled_on_the_visible_window():
    """#67: the whole ~5-year series set the scale, flattening 1Y into a sliver."""
    view = _struct(_CHART, "EarningsChartView")
    line = _decl_body(view, "private func dailyPriceLine(")
    assert "Self.visiblePriceWindow(placed, width: width)" in line
    assert "minPrice" not in line and "maxPrice" not in line, \
        "the daily line is normalised on the full series again"
    window = _decl_body(view, "static func visiblePriceWindow(")
    assert "placed[$0].x >= 0 && placed[$0].x <= width" in window
    assert "inside.count >= 2 ? inside.map { placed[$0].price } : placed.map { $0.price }" in window


# ── 4. "No estimate" is not "matched" (#26 #30 #60) ──────────────────────────


def test_quarter_carries_has_estimate_after_fiscal_date():
    """W8 maps `dto.hasEstimate ?? true` by assignment after construction, so the property
    must be a defaulted `var` declared after `fiscalDate` (existing memberwise calls)."""
    model = _struct(_MODELS, "EarningsQuarterData")
    fiscal = model.index("var fiscalDate: String? = nil")
    has = model.index("var hasEstimate: Bool = true")
    assert fiscal < has < model.index("var result: EarningsQuarterResult")


def test_result_checks_has_estimate_before_comparing():
    result = _decl_body(_struct(_MODELS, "EarningsQuarterData"), "var result: EarningsQuarterResult")
    gate = result.index("guard hasEstimate else")
    assert gate < result.index("return .noEstimate") < result.index("actual > estimateValue")
    assert result.index("guard let actual = actualValue") < gate
    assert "surprisePercent" not in result, "a nil surprise is also a real 0 estimate"


def test_no_estimate_is_its_own_neutral_case_everywhere():
    enum = _decl_body(_code(_MODELS), "enum EarningsQuarterResult")
    assert "case noEstimate" in enum
    dot = _decl_body(enum, "var dotColor: Color")
    assert re.search(r"case \.noEstimate:\s*return AppColors\.primaryBlue", dot)
    assert not re.search(r"case [^:\n]*\.noEstimate[^:\n]*,|case [^:\n]*,\s*\.noEstimate", dot), \
        "noEstimate must not share a beat/miss/match colour"
    assert "self == .matched" in _decl_body(enum, "var hasDashedBorder: Bool")

    model = _struct(_MODELS, "EarningsQuarterData")
    colour = _decl_body(model, "var surpriseColor: Color")
    assert "switch result" in colour
    assert "case .pending, .noEstimate:\n            return AppColors.textSecondary" in colour
    caption = _decl_body(model, "var formattedSurprise: String?")
    assert "guard outcome != .noEstimate, outcome != .pending else { return nil }" in caption


def test_reported_dot_has_a_legend_and_an_info_sheet_row():
    item = _code(_LEGEND_ITEM)
    enum = _decl_body(item, "enum EarningsLegendType")
    assert "case reported" in enum
    assert re.search(r"case \.reported:\s*return EarningsQuarterResult\.noEstimate\.dotColor", enum)
    legend = _struct(_LEGEND, "EarningsLegend")
    assert legend.count("if showsReported") == 2, "both legend layouts must offer the entry"
    body = _decl_body(_struct(_CARD, "EarningsSectionCard"), "var body: some View")
    assert "EarningsLegend(showsReported: displayQuarters.contains { $0.result == .noEstimate })" in body

    info = _struct(_INFO, "EarningsInfoSheet")
    assert '"Reported — no analyst consensus"' in info
    assert "dotColor: EarningsQuarterResult.noEstimate.dotColor" in info
    assert "EarningsResultDot(result: .noEstimate)" in _code(_DOT)


def test_row_shows_a_dash_for_a_quarter_without_consensus():
    row = _struct(_ROW, "EarningsSurpriseRow")
    assert re.search(r"else if quarter\.result == \.noEstimate \{\s*Text\(\"—\"\)", row)


# ── 5. The 1Y caption (#70) ──────────────────────────────────────────────────


def test_surprise_caption_is_compact_bounded_and_one_line():
    model = _struct(_MODELS, "EarningsQuarterData")
    caption = _decl_body(model, "var formattedSurprise: String?")
    assert "return Self.surpriseText(surprise, isMatch: outcome == .matched)" in caption
    text = _decl_body(model, "static func surpriseText(")
    assert "CompactNumberFormat.percentString(magnitude)" in text
    assert 'return isMatch ? "0%" : "<0.1%"' in text
    assert "magnitude >= 1000" in text and "magnitude >= 99.95" in text
    row = _struct(_ROW, "EarningsSurpriseRow")
    text = row[row.index("Text(surprise)"):row.index("} else if")]
    assert ".lineLimit(1)" in text and ".minimumScaleFactor(0.75)" in text


# ── 6. Empty state (#66) and the EPS basis label (#63) ───────────────────────


def test_card_shows_an_honest_empty_state_before_any_chart():
    card = _struct(_CARD, "EarningsSectionCard")
    body = _decl_body(card, "var body: some View")
    assert body.index("if displayQuarters.isEmpty") < body.index("EarningsChartView(")
    empty = _decl_body(body, "if displayQuarters.isEmpty")
    assert "emptySeriesState" in empty and "EarningsChartView(" not in empty
    # Both empty copies (degraded / complete) live in `emptySeriesState` — pinned in
    # test_ios_earnings_round2.py (R32).
    assert "history available" in _decl_body(card, "private var emptySeriesState")
    # The Next Earnings card is not part of the gated block.
    assert body.index("NextEarningsDateCard(") > body.index("EarningsLegend(")


def test_earnings_eps_is_labelled_adjusted():
    """The Earnings card plots adjusted EPS, the Growth card GAAP; both read plain "EPS"."""
    enum = _decl_body(_code(_MODELS), "enum EarningsDataType")
    assert re.search(r'case \.eps: return "Adjusted EPS"', enum)
    assert 'case eps = "EPS"' in enum, "the raw value is the toggle's ForEach id — keep it"
    body = _decl_body(_struct(_CARD, "EarningsSectionCard"), "var body: some View")
    assert 'Text("\\(selectedDataType.seriesTitle) vs. analyst consensus")' in body
    assert ".accessibilityLabel(type.seriesTitle)" in _struct(_TOGGLE, "EarningsDataTypeToggle")
    info = _struct(_INFO, "EarningsInfoSheet")
    assert '"Adjusted EPS (Earnings Per Share)"' in info
    assert "GAAP" in info and "Growth card" in info
