"""Earnings card, round 2 (2026-09-30): the adversarial review of the first fix pass.

Three findings, each pinned twice — a Python port exercised on outlier inputs, and a
source-scan guard that ties the port to the Swift expression it stands for (comments
stripped, brace-bounded; see `.claude/rules/testing.md` §3):

* R10 — the VoiceOver label the first pass added to every 3Y surprise bar rounded to whole
  percent: a +0.3% beat was spoken "+0%" and a -0.4% miss "0%" (no sign: -0.4 rounds to
  -0, and the "+" was added only above 0) — the info sheet's definition of Matched. The
  visible off-scale label printed a clamped 10.3% as "+10%", the axis maximum it runs past.
  Now one formatter (`EarningsQuarterData.surpriseText`) serves the 1Y caption, the 3Y
  off-scale label and the spoken label.
* R31 — the off-scale value labels of two ADJACENT clamped bars shared one line, 1.4
  columns wide and one column apart, so "+900%" ran into "+500%"; and the first column's
  label was clamped inward over column 1's bar. Now labels are centred on their column (the
  clip bleeds sideways instead), a run alternates rows, and a label with a neighbouring bar
  on its side of the 0% line is narrowed to stop short of it.
* R32 — an FMP outage arrives as a 200 with empty quarter lists and `degraded` set, and the
  card read "No Adjusted EPS history available for this ticker." for AAPL. `EarningsData`
  now carries `degraded` (the contract G9 maps from the DTO) and the card says the history
  is temporarily unavailable instead.
"""
from __future__ import annotations

import math
import re

import pytest

from test_ios_earnings_deepcheck_geometry import (
    PLOT_HEIGHT,
    bar_geometry,
    formatted_surprise,
    normalized_y,
    percent_string,
    result,
    signed_percent,
    surprise_domain,
    surprise_text,
)
from test_ios_earnings_deepcheck_guards import (
    _BAR,
    _CARD,
    _IOS,
    _MODELS,
    _code,
    _decl_body,
    _struct,
)

_THEME = _IOS / "Theme" / "AppTheme.swift"


# ══ R10 — one surprise formatter, spoken and drawn ═══════════════════════════


def _first_pass_signed_percent(surprise: float) -> str:
    """The first pass's `signedPercent`: "+" for > 0, then whole-percent `percentString`."""
    return ("+" if surprise > 0 else "") + percent_string(surprise)


@pytest.mark.parametrize("surprise,text", [
    (-0.4, "-0.4%"),    # was "0%": a miss read as an exact match
    (0.3, "+0.3%"),     # was "+0%"
    (0.6, "+0.6%"),     # was "+1%"
    (-0.04, "<0.1%"),   # a real miss under the 2dp floor is never "0%"
    (0.0, "0%"),        # only a true zero may print "0%"
    (10.3, "+10.3%"),   # was "+10%" = the axis max, beside a chevron saying it runs past
    (-88.07, "-88.1%"),
    (99.96, "+100%"),
    (-250.0, "-250%"),
    (1300.0, "+1.3k%"),
    (-1e6, "-1M%"),
    (float("nan"), "—"),
])
def test_bar_text_keeps_sign_and_precision(surprise, text):
    assert signed_percent(surprise) == text


def test_the_fixture_reproduces_the_first_pass_defect():
    """Anti-vacuity: the ported first-pass formatter really did say these."""
    assert _first_pass_signed_percent(-0.4) == "0%"
    assert _first_pass_signed_percent(0.3) == "+0%"
    assert _first_pass_signed_percent(10.3) == "+10%"


def _spoken(q) -> str:
    """Port of the bar's accessibility label in `EarningsSurpriseBarChart.chart`."""
    value = formatted_surprise(q) or signed_percent(q["surprise"])
    word = {"beat": "beat", "missed": "missed", "matched": "matched"}.get(result(q), "")
    outcome = f" {word}," if word else ""
    return f"{q['quarter']}{outcome} surprise {value}"


@pytest.mark.parametrize("q,spoken", [
    ({"quarter": "Q3 '24", "actual": 13.07, "estimate": 13.0, "surprise": 0.3},
     "Q3 '24 beat, surprise +0.3%"),
    ({"quarter": "Q4 '24", "actual": 9.96, "estimate": 10.0, "surprise": -0.4},
     "Q4 '24 missed, surprise -0.4%"),
    ({"quarter": "Q1 '25", "actual": 9.9996, "estimate": 10.0, "surprise": -0.004},
     "Q1 '25 missed, surprise <0.1%"),
    ({"quarter": "Q2 '25", "actual": 10.0004, "estimate": 10.0, "surprise": 0.0},
     "Q2 '25 beat, surprise <0.1%"),     # backend rounded a real beat to 0.0
    ({"quarter": "Q3 '25", "actual": 0.25, "estimate": 0.25, "surprise": 0.0},
     "Q3 '25 matched, surprise 0%"),
])
def test_voiceover_hears_what_the_row_and_the_dot_say(q, spoken):
    assert _spoken(q) == spoken
    # "0%" is spoken only for a real match.
    if result(q) != "matched":
        assert not spoken.endswith(" 0%")


def test_swift_has_one_formatter_for_caption_label_and_voiceover():
    model = _struct(_MODELS, "EarningsQuarterData")
    text = _decl_body(model, "static func surpriseText(")
    assert "guard surprise.isFinite else" in text
    assert 'return isMatch ? "0%" : "<0.1%"' in text
    assert 'let sign: String = surprise > 0 ? "+" : "-"' in text, \
        "the sign must come from the value, not from the rounded string"
    assert 'String(format: "%.1f", magnitude)' in text
    assert "return Self.surpriseText(surprise, isMatch: outcome == .matched)" in \
        _decl_body(model, "var formattedSurprise: String?")
    outcome = _decl_body(model, "var spokenOutcome: String")
    for case, word in (("beat", "beat"), ("missed", "missed"), ("matched", "matched")):
        assert f'case .{case}: return "{word}"' in outcome

    view = _struct(_BAR, "EarningsSurpriseBarChart")
    signed = _decl_body(view, "static func signedPercent(")
    assert "EarningsQuarterData.surpriseText(surprise, isMatch: surprise == 0)" in signed
    assert "percentString" not in signed, "the whole-percent formatter is back on the bars"
    chart = _decl_body(view, "private func chart(")
    spoken = chart[chart.index("ForEach("):chart.index("RoundedRectangle")]
    assert "let valueText: String = quarter.formattedSurprise ?? Self.signedPercent(surprise)" in spoken
    assert re.search(r'let spoken: String = "\\\(quarter\.quarter\)\\\(outcome\) surprise '
                     r'\\\(valueText\)\\\(scaleNote\)"', spoken), spoken
    assert "quarter.spokenOutcome" in spoken


# ══ R31 — off-scale labels never overlap each other or a neighbouring bar ════

LABEL_ROW_HEIGHT = 14.0  # EarningsSurpriseBarChart.labelRowHeight
LABEL_BLEED = 8.0        # EarningsSurpriseBarChart.labelBleed = AppSpacing.sm
BAR_WIDTH_RATIO = 0.5    # EarningsSurpriseBarChart.barWidthRatio


def label_offset(row: int) -> float:
    return 2 + LABEL_ROW_HEIGHT / 2 + row * (LABEL_ROW_HEIGHT + 1)


def off_scale_label_slots(surprises, domain, step_x):
    """Port of `EarningsSurpriseBarChart.offScaleLabelSlots`."""
    full_width = min(max(step_x * 1.4, 28), step_x + 2 * LABEL_BLEED)
    clear_of_neighbour_bars = max(step_x * 1.5 - 1, 0)
    slots = {}
    for index, surprise in enumerate(surprises):
        if surprise is None:
            continue
        plotted = min(max(surprise, domain[0]), domain[1])
        if plotted == surprise:
            continue
        points_up = surprise > 0
        crowded = False
        for neighbour in (index - 1, index + 1):
            if 0 <= neighbour < len(surprises):
                other = surprises[neighbour]
                if other is not None and other != 0 and (other > 0) != points_up:
                    crowded = True
        width = min(full_width, clear_of_neighbour_bars) if crowded else full_width
        row = 0
        previous = slots.get(index - 1)
        if previous is not None and surprises[index - 1] is not None \
                and (surprises[index - 1] > 0) == points_up and previous[0] == 0:
            row = 1
        slots[index] = (row, width)
    return slots


def _rects(surprises, plot_width, placement="round2", domain=None):
    """Every bar's and every off-scale label's rect, (x0, y0, x1, y1), in plot coords.
    `domain` overrides the robust fence (to reach shapes 12 quarters cannot produce)."""
    values = [s for s in surprises if s is not None]
    domain = domain or surprise_domain(values)
    step = plot_width / max(len(surprises), 1)
    bar_w = step * BAR_WIDTH_RATIO
    zero = PLOT_HEIGHT - normalized_y(0, PLOT_HEIGHT, domain)
    slots = off_scale_label_slots(surprises, domain, step)
    bars, labels = {}, {}
    for i, s in enumerate(surprises):
        if s is None:
            continue
        x = i * step + step / 2
        center, h, off = bar_geometry(s, PLOT_HEIGHT, domain)
        bars[i] = (x - bar_w / 2, center - h / 2, x + bar_w / 2, center + h / 2)
        if not off:
            continue
        up = s > 0
        if placement == "round2":
            row, width = slots[i]
            offset = label_offset(row)
            y = zero + offset if up else zero - offset
            half_h = LABEL_ROW_HEIGHT / 2
            labels[i] = (x - width / 2, y - half_h, x + width / 2, y + half_h)
        else:  # the first pass: one row at ±8, 1.4 columns wide, clamped inside the plot
            width = max(step * 1.4, 28)
            half = width / 2
            lx = min(max(x, half), max(plot_width - half, half))
            y = zero + 8 if up else zero - 8
            labels[i] = (lx - half, y - 6.5, lx + half, y + 6.5)  # ~13pt caption line
    return bars, labels, domain


def _intersects(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _collisions(bars, labels):
    hits = []
    keys = sorted(labels)
    for n, i in enumerate(keys):
        for j in keys[n + 1:]:
            if _intersects(labels[i], labels[j]):
                hits.append(("label", i, "label", j))
        for j, bar in bars.items():
            if _intersects(labels[i], bar):
                hits.append(("label", i, "bar", j))
    return hits


_FUTURE = [None, None]  # the card's two estimate columns (no bar)

# name → per-column surprises (12 reported + 2 future = the 3Y card's 14 columns).
_FIXTURES = {
    # The reviewer's: two adjacent near-break-even quarters with tiny consensus.
    "adjacent_up_pair": [3, 2, 4, 5, 1, 3, 2, 4, 3, 900, 500, 2] + _FUTURE,
    # The first column clamped up, column 1 a long miss on the label's side.
    "first_column_vs_miss": [900, -4, 2, 4, 1, 3, 2, 4, 3, 2, 1, 2] + _FUTURE,
    "first_column_down_vs_beat": [-900, 4, 2, 3, 1, 3, 2, 4, 3, 2, 1, 2] + _FUTURE,
    # The last column (no estimate columns after it), neighbour on the label's side.
    "last_column_vs_miss": [2, 3, 1, 4, 2, 3, 1, 2, 3, 4, -4, 900],
    # A down run, and two adjacent outliers in opposite directions, each crowded by the
    # other (and by a long miss after them). Twelve quarters clamp at most two values: a
    # third outlier becomes the fence's Q3 itself — longer runs are pinned below with a
    # forced domain.
    "run_of_two_down": [2, 3, -900, -800, 2, 3, 1, 4, 3, 1, 2, 1] + _FUTURE,
    "opposite_pair": [2, 3, 900, -900, -4, 1, 2, 3, 1, 4, 2, 3] + _FUTURE,
    # The AVGO shape from the TestFlight report.
    "avgo": [0.3, 0.2, 2.1, 4.0, 0.5, 1.9, 2.1, 1.4, 0.8, 3.0, 0.9, -88.0] + _FUTURE,
}

# Plot widths: card = screen − 64; plot = card − the gutter (40 EPS / 50 revenue).
# 375pt (SE / mini) revenue → 261; 393 revenue → 279, EPS → 289; 402 EPS → 298; the
# reviewer's 281; 430 (Plus / Max) EPS → 326.
_WIDTHS = [261.0, 279.0, 281.0, 289.0, 298.0, 326.0]


@pytest.mark.parametrize("width", _WIDTHS)
@pytest.mark.parametrize("name", sorted(_FIXTURES))
def test_off_scale_labels_clear_every_label_and_bar(name, width):
    bars, labels, _ = _rects(_FIXTURES[name], width)
    assert labels, f"{name}: the fixture no longer clamps any bar"
    assert _collisions(bars, labels) == [], name
    for x0, y0, x1, y1 in labels.values():
        # Inside the plot vertically (the clip is exact there) and within the bleed.
        assert 0 <= y0 and y1 <= PLOT_HEIGHT
        assert x0 >= -LABEL_BLEED - 1e-9 and x1 <= width + LABEL_BLEED + 1e-9
    for i, (x0, y0, x1, y1) in labels.items():
        # Wide enough for "+10.3%" at the 0.7 minimum scale (≈ 27pt of 11pt semibold)
        # whenever no neighbouring bar forces it narrower.
        assert x1 - x0 >= 26.5, (name, width, i, x1 - x0)


@pytest.mark.parametrize("name", ["adjacent_up_pair", "first_column_vs_miss"])
def test_the_fixtures_reproduce_the_first_pass_overlap(name):
    """Anti-vacuity: the first pass's placement really collides on these inputs."""
    bars, labels, _ = _rects(_FIXTURES[name], 281.0, placement="first_pass")
    assert _collisions(bars, labels), name


# Shapes the robust fence cannot produce from 12 quarters, placed under a forced ±10
# domain: the placement must hold for any input it is handed.
_FORCED = {
    "run_of_three_up": [2, 3, 1, 4, 2, 900, 800, 700, 3, 1, 2, 1] + _FUTURE,
    "zigzag": [2, 900, -900, 900, -3, 1, 2, 3, 1, 4, 2, 3] + _FUTURE,
    "all_off_scale": [50, 60, -70, -80, 90, 40, 30, -20, -30, 25, 35, -45] + _FUTURE,
}


@pytest.mark.parametrize("width", _WIDTHS)
@pytest.mark.parametrize("name", sorted(_FORCED))
def test_long_runs_under_a_forced_domain_still_never_collide(name, width):
    bars, labels, _ = _rects(_FORCED[name], width, domain=(-10.0, 10.0))
    assert len(labels) >= 3, name
    assert _collisions(bars, labels) == [], name


def test_a_run_alternates_rows_and_a_lone_outlier_hugs_the_zero_line():
    slots = off_scale_label_slots(_FORCED["run_of_three_up"], (-10.0, 10.0), 281.0 / 14)
    assert [slots[i][0] for i in (5, 6, 7)] == [0, 1, 0]
    slots = off_scale_label_slots(_FIXTURES["adjacent_up_pair"],
                                  surprise_domain([v for v in _FIXTURES["adjacent_up_pair"] if v is not None]),
                                  281.0 / 14)
    assert [slots[i][0] for i in (9, 10)] == [0, 1]
    _, _, domain = _rects(_FIXTURES["avgo"], 281.0)
    assert off_scale_label_slots(_FIXTURES["avgo"], domain, 281.0 / 14) == {
        11: (0, min(max(281.0 / 14 * 1.4, 28), 281.0 / 14 + 16))}


def test_degenerate_columns_place_nothing():
    assert off_scale_label_slots([], (-1.0, 1.0), 20.0) == {}
    assert off_scale_label_slots([None, None], (-1.0, 1.0), 20.0) == {}
    # A single bar can never be clamped (its own magnitude sets the domain).
    values = [1e6]
    assert off_scale_label_slots(values, surprise_domain(values), 20.0) == {}


def test_swift_label_placement_matches_the_port():
    view = _struct(_BAR, "EarningsSurpriseBarChart")
    assert "static let labelRowHeight: CGFloat = 14" in view
    assert "static let labelBleed: CGFloat = AppSpacing.sm" in view
    assert re.search(r"static let sm: CGFloat = 8\b", _code(_THEME)), \
        "AppSpacing.sm moved: update LABEL_BLEED in the port"

    offset = _decl_body(view, "static func labelOffset(row: Int)")
    assert "let half: CGFloat = labelRowHeight / 2" in offset
    assert "let step: CGFloat = labelRowHeight + 1" in offset
    assert "return 2 + half + CGFloat(row) * step" in offset

    slots = _decl_body(view, "static func offScaleLabelSlots(")
    for expr in (
        "let fullWidth: CGFloat = min(max(stepX * 1.4, 28), stepX + 2 * labelBleed)",
        "let clearOfNeighbourBars: CGFloat = max(stepX * 1.5 - 1, 0)",
        "let plotted: Double = min(max(surprise, domain.min), domain.max)",
        "guard plotted != surprise else { continue }",
        "for neighbour in [index - 1, index + 1] where surprises.indices.contains(neighbour)",
        "if let other = surprises[neighbour], other != 0, (other > 0) != pointsUp",
        "let width: CGFloat = crowded ? min(fullWidth, clearOfNeighbourBars) : fullWidth",
        "(previousSurprise > 0) == pointsUp",
        "previous.row == 0",
    ):
        assert expr in slots, expr

    chart = _decl_body(view, "private func chart(")
    assert "Self.offScaleLabelSlots(" in chart and "slot: labelSlots[index]" in chart
    # The bar's step and width are the port's (BAR_WIDTH_RATIO, one column per quarter).
    assert "let stepX: CGFloat = width / CGFloat(quarterCount)" in chart
    assert "private var barWidthRatio: CGFloat { 0.5 }" in view

    marker = _decl_body(view, "private func offScaleMarker(")
    assert "let labelOffset: CGFloat = Self.labelOffset(row: row)" in marker
    assert ".frame(width: labelWidth, height: Self.labelRowHeight)" in marker
    assert ".position(x: x, y: labelY)" in marker, "the label must stay centred on its column"
    assert "halfLabel" not in marker, "the inward edge clamp is back"

    # The left bleed lands in the gutter's empty trailing padding, which must stay as wide.
    axis = _decl_body(view, "private func yAxisLabels(")
    assert ".padding(.trailing, AppSpacing.sm)" in axis


# ══ R32 — an outage is never "No … history available for this ticker" ═══════


def test_earnings_data_carries_degraded_as_a_defaulted_var():
    """The contract with G9: `EarningsDTO.toDisplayModel()` assigns it after construction,
    so it must be a defaulted `var` (every existing three-argument init keeps compiling)."""
    model = _struct(_MODELS, "EarningsData")
    assert "var degraded: [String] = []" in model
    assert "var isDegraded: Bool { !degraded.isEmpty }" in model
    init = _decl_body(model, "init(")
    assert "degraded" not in init, "the init must not require the new field"


def test_the_card_tells_an_outage_from_an_empty_history():
    card = _struct(_CARD, "EarningsSectionCard")
    state = _decl_body(card, "private var emptySeriesState")
    degraded = _decl_body(state, "if earningsData.isDegraded")
    assert "temporarily unavailable" in degraded
    assert "InlineRetryNotice(" in degraded and "onRetry: onRetry" in degraded
    assert "history available" not in degraded, "an outage is stated as a fact about the company"

    rest = state[state.index(degraded) + len(degraded):]
    assert rest.lstrip().startswith("else"), "the complete-build branch must be the else arm"
    complete = _decl_body(rest, "else")
    assert 'Text("No \\(selectedDataType.seriesTitle) history available for this ticker.")' in complete

    # The "no history" copy exists ONLY in that else arm.
    assert card.count("history available for this ticker") == 1

    body = _decl_body(card, "var body: some View")
    assert "emptySeriesState" in _decl_body(body, "if displayQuarters.isEmpty")
    # A partial build that still draws says it may be incomplete.
    drawn = body[body.index("if displayQuarters.isEmpty"):body.index("EarningsChartView(")]
    assert re.search(r"if earningsData\.isDegraded \{\s*partialDataNote\s*\}", drawn)

    init = _decl_body(card, "init(")
    assert "self.onRetry = onRetry" in init
    assert re.search(r"onRetry: \(\(\) -> Void\)\? = nil", card), \
        "existing call sites pass no retry — it must default"


def test_the_outage_preview_is_really_degraded():
    sample = _code(_MODELS)
    block = _decl_body(sample, "static let sampleTemporarilyUnavailable: EarningsData =")
    assert re.search(r'data\.degraded = \["\w+"', block)
    assert "epsQuarters: [], revenueQuarters: []" in block
