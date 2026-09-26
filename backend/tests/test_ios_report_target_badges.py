"""The fair-value chart's label column: every label sits level with its own mark (E4 lineage).

History: the report's analyst price-target chart (`ReportConsensusBar.targetBadges`) placed
two-line price-over-percent badges, and positioned them so that each percent sat half a line
below its dot and read as the label of the next dot (TestFlight 2026-09-02, E4). The fix moved
with the pole into `CaydexFairValueRangeChart` on 2026-09-26, and the owner then reported that
"the chart and prices don't fit or align at all": the two-line badges (label OVER price) in a
60pt gutter made a ragged column, "Low" sat directly under the estimate's price and read as its
label, and the dashed price line ended at the pole with no number on it.

The chart now draws ONE right-hand column of single-line labels — "Price $341.07",
"High $266.46", "Estimate $213.29", "Low $176.20" — all starting at the same x, each centred on
the y of the mark it names, pushed apart only where two would overlap. This file pins that from
the Swift source and re-implements the collision resolver in Python line for line, so a change
to the Swift has to be mirrored here on purpose.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_CHART = _REPO / "frontend/ios/ios/Views/Molecules/CaydexFairValueRangeChart.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)


def _code() -> str:
    assert _CHART.exists(), f"{_CHART} moved — update this guard, do not delete it"
    return _strip_comments(_CHART.read_text(encoding="utf-8"))


def _decl_block(src: str, prefix: str) -> str:
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


# ── source scan ──────────────────────────────────────────────────────────────

def test_every_label_starts_at_the_same_x_and_sits_at_its_resolved_y():
    col = _decl_block(_code(), "private func labelColumn(")
    assert "Self.resolvedLabelPositions(" in col, "the collision resolver is no longer used"
    assert "rows.map(\\.y)" in col, "the resolver must be fed the marks' own y"
    # One x for every row — the column is a left-aligned list, not a ragged stack.
    assert re.search(r"\.frame\(width: Layout\.labelWidth, alignment: \.leading\)\s*"
                     r"\.position\(x: centerX, y: anchors\[index\]\)", col)
    assert col.count(".position(") == 1


def test_a_label_is_one_line_name_then_price():
    label = _decl_block(_code(), "private func columnLabel(")
    assert "HStack(" in label and "VStack(" not in label, "two-line badges are back"
    assert label.index("row.name") < label.index("row.price")
    assert ".lineLimit(1)" in label and ".minimumScaleFactor(0.7)" in label


def test_the_column_labels_the_range_AND_the_price():
    rows = _decl_block(_code(), "private func columnLabels(")
    for name in ('"High"', '"Estimate"', '"Low"', '"Price"'):
        assert name in rows, name
    # Each row's y is its own mark's y, through the one scale.
    for src in ("b.high", "b.value", "b.low", "p"):
        assert f"yPosition(for: {src}, in: geometry)" in rows, src
    assert ".sorted { ($0.y, $0.rank) < ($1.y, $1.rank) }" in rows, "rows must be sorted by y"


def test_the_marks_still_sit_at_their_true_price_y():
    pole = _decl_block(_code(), "private func rangePole(")
    assert "resolvedLabelPositions" not in pole, "the marks must never be nudged — only the labels"
    assert pole.count(".position(x: xPos, y: highY)") == 1
    assert pole.count(".position(x: xPos, y: valueY)") == 1
    assert pole.count(".position(x: xPos, y: lowY)") == 1


def test_the_dashed_price_line_runs_to_the_column():
    ind = _decl_block(_code(), "private func currentPriceIndicator(")
    assert "Layout.leadingPadding + chartWidth" in ind


def test_the_column_is_wide_enough_and_the_13f_axis_shares_it():
    src = _code()
    layout = _decl_block(src, "enum Layout")
    width = float(re.search(r"trailingGutter: CGFloat = (\d+)", layout).group(1))
    assert width >= 96, "\"Estimate $213.29\" needs ~92pt of caption text"
    bar = _strip_comments((_REPO / "frontend/ios/ios/Views/Molecules/ReportConsensusBar.swift")
                          .read_text(encoding="utf-8"))
    bars = _decl_block(bar, "private func volumeBarsChart(")
    assert "CaydexFairValueRangeChart.Layout.labelGap" in bars, "the 13F axis must start where the labels do"
    assert "CaydexFairValueRangeChart.Layout.trailingGutter" in bars


def test_the_plot_is_inset_for_the_labels_when_there_is_a_column():
    y = _decl_block(_code(), "private func yPosition(")
    assert "showsColumn ? labelInset : 0" in y
    assert "return top + plotHeight * (1 - normalizedValue)" in y
    fmt = _decl_block(_code(), "private func formatBadgePrice(")
    assert "abs(value) >= 1000" in fmt, "four-digit values must drop their cents"


# ── the resolver, mirrored ───────────────────────────────────────────────────

def _resolve(ys, *, min_gap, top, bottom):
    if not ys:
        return []
    gap = max(0.0, min_gap)
    lowest = max(top, bottom)
    starts, counts, sums = [], [], []
    for y in ys:
        starts.append(y)
        counts.append(1)
        sums.append(y)
        while len(starts) >= 2:
            j = len(starts) - 1
            if starts[j - 1] + counts[j - 1] * gap <= starts[j]:
                break
            counts[j - 1] += counts[j]
            sums[j - 1] += sums[j]
            starts.pop()
            counts.pop()
            sums.pop()
            n = counts[j - 1]
            starts[j - 1] = sums[j - 1] / n - (n - 1) * gap / 2
    out = [s + k * gap for s, c in zip(starts, counts) for k in range(c)]
    out[0] = max(out[0], top)
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1] + gap)
    last = len(out) - 1
    if out[last] > lowest:
        out[last] = lowest
        for i in range(last - 1, -1, -1):
            out[i] = min(out[i], out[i + 1] - gap)
    return [min(max(v, top), lowest) for v in out]


LINE = 13.0
GAP = LINE + 3                 # 16
INSET = LINE / 2 + 1           # 7.5
H = 200.0


def _r(*ys, height=H):
    return _resolve(list(ys), min_gap=GAP, top=INSET, bottom=height - INSET)


def test_the_swift_resolver_matches_this_mirror_line_for_line():
    body = _decl_block(_code(), "static func resolvedLabelPositions(")
    for line in (
        "let lowest = max(top, bottom)",
        "if starts[j - 1] + CGFloat(counts[j - 1]) * gap <= starts[j] { break }",
        "counts[j - 1] += counts[j]",
        "sums[j - 1] += sums[j]",
        "starts[j - 1] = sums[j - 1] / n - (n - 1) * gap / 2",
        "out.append(start + CGFloat(k) * gap)",
        "out[0] = max(out[0], top)",
        "out[i] = max(out[i], out[i - 1] + gap)",
        "out[i] = min(out[i], out[i + 1] - gap)",
        "return out.map { min(max($0, top), lowest) }",
    ):
        assert line in body, f"resolver drifted from the mirror at: {line}"


def test_well_spaced_labels_stay_exactly_on_their_marks():
    # The owner's AAPL screenshot scale: price far above, the range spread out below.
    assert _r(12.0, 96.0, 140.0, 183.0) == [12.0, 96.0, 140.0, 183.0]


def test_a_crowded_group_is_centred_on_its_marks():
    # Two marks 6pt apart: centred on their mean (103), GAP apart — not one pushed away.
    assert _r(100.0, 106.0) == [95.0, 111.0]
    # Three at once: the middle label stays on the middle mark.
    a, b, c = _r(100.0, 104.0, 108.0)
    assert b == 104.0 and b - a == GAP and c - b == GAP
    # A tie (low == estimate == high) spreads around the shared mark.
    assert _r(50.0, 50.0, 50.0) == [34.0, 50.0, 66.0]


def test_a_merged_group_absorbs_a_neighbour_it_now_overlaps():
    # 100/106 centre to 95/111; a third label at 118 now overlaps 111 → one group of three.
    out = _r(100.0, 106.0, 118.0)
    assert all(out[i + 1] - out[i] >= GAP - 1e-9 for i in range(2))
    assert abs(sum(out) / 3 - (100 + 106 + 118) / 3) < 1e-9, "the group stays centred on its marks"


def test_the_top_edge_pushes_down_never_stacking():
    out = _r(0.0, 5.0, 10.0)
    assert out[0] == INSET
    assert all(out[i + 1] - out[i] >= GAP - 1e-9 for i in range(2))


def test_the_bottom_edge_pushes_up():
    out = _r(190.0, 195.0, 200.0)
    assert out[-1] == H - INSET
    assert all(out[i + 1] - out[i] >= GAP - 1e-9 for i in range(2))
    assert out[0] >= INSET


def test_a_column_too_short_keeps_order_and_the_top_wins():
    out = _r(0.0, 10.0, 20.0, 30.0, height=40.0)
    assert out[0] == INSET
    assert out == sorted(out)
    assert all(INSET <= v <= 40.0 - INSET for v in out)


def test_single_and_empty_inputs():
    assert _r() == []
    assert _r(3.0) == [INSET]
    assert _r(100.0) == [100.0]
