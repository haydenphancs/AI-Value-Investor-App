"""The fair-value chart's pole reaches the price: a gray extension, and a price label that moves left.

Owner, 2026-10-25: "extend the pole, so it can have the current price on the pole. but the extend
is gray color. so users can distinguish the range. but make sure the length of the pole don't far
too high to the top or too low at the bottom ... the current price may near the estimate, so we
can move the current price to left of the range, so it won't mess up with the estimate or high or
low." Length: "Exactly to the price".

`CaydexFairValueRangeChart` therefore:
  * draws a GRAY (`AppColors.textMuted`) segment on the pole from the nearer range end EXACTLY to
    the price when the price is outside the range — the range keeps its accent ink;
  * marks the price on the pole with a gray cap (inside the range too), drawn UNDER the accent
    High / Low caps and the estimate dot — a price on a range end must not turn that cap gray,
    or the range's end reads as the price (review, 2026-10-25);
  * moves "Price $X" to the LEFT of the pole when its mark is closer than one label gap to a
    High / Estimate / Low mark (or where their labels land), and then leaves Price out of the
    right-hand column;
  * puts that left label just ABOVE or just BELOW the dashed price line, never on it — level with
    the line, its opaque backing erased the line's most recent stretch, which always ends at the
    price (review, 2026-10-25). The side is the one the price line crosses less within the
    label's span; the centre is clamped inside the plot and flips sides rather than land on the
    dashed line. Its opaque `cardBackground` backing is TEXT-SIZED (applied before the frame).

The decisions are pure statics (`priceExtension`, `priceLabelGoesLeft`, `leftLabelSide`,
`leftLabelCenterY`). This file pins their Swift source line for line (comments stripped,
brace-bounded — .claude/rules/testing.md §3) and tests a Python mirror on the edge cases.
Mutation-tested by hand on a scratch copy, and by the in-memory table at the bottom.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_CHART = _REPO / "frontend/ios/ios/Views/Molecules/CaydexFairValueRangeChart.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"(?<!:)//.*$", "", line) for line in src.splitlines())


def _code(src: str | None = None) -> str:
    if src is None:
        assert _CHART.exists(), f"{_CHART} moved — update this guard, do not delete it"
        src = _CHART.read_text(encoding="utf-8")
    code = _strip_comments(src)
    # The live view only: #Preview blocks must not satisfy (or trip) a scan.
    cut = code.find("#Preview")
    return code if cut == -1 else code[:cut]


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


_EXT_DECL = "static func priceExtension(price: Double, low: Double, high: Double) -> (from: Double, to: Double)?"
_LEFT_DECL = "static func priceLabelGoesLeft(priceY: CGFloat, markYs: [CGFloat], minGap: CGFloat) -> Bool"
_SIDE_DECL = "static func leftLabelSide(priceY: CGFloat, pointYsInSpan: [CGFloat], tolerance: CGFloat) -> LeftLabelSide"
_CENTER_DECL = ("static func leftLabelCenterY(priceY: CGFloat, side: LeftLabelSide, offset: CGFloat,\n"
                "                                 clearance: CGFloat, top: CGFloat, bottom: CGFloat) -> CGFloat")


# ── the Swift source, pinned ────────────────────────────────────────────────────────────────

def _check_extension_source(code: str) -> None:
    assert _EXT_DECL in code, "priceExtension's signature drifted from the mirror"
    body = _decl_block(code, _EXT_DECL)
    lines = [l.strip() for l in body.strip("{}").splitlines() if l.strip()]
    assert lines == [
        "guard price.isFinite, low.isFinite, high.isFinite, low <= high else { return nil }",
        "if price > high { return (from: high, to: price) }",
        "if price < low { return (from: low, to: price) }",
        "return nil",
    ], f"priceExtension drifted from the mirror: {lines}"


def _check_left_source(code: str) -> None:
    assert _LEFT_DECL in code, "priceLabelGoesLeft's signature drifted from the mirror"
    body = _decl_block(code, _LEFT_DECL)
    lines = [l.strip() for l in body.strip("{}").splitlines() if l.strip()]
    assert lines == [
        "let gap = max(0, minGap)",
        "return markYs.contains { abs($0 - priceY) < gap }",
    ], f"priceLabelGoesLeft drifted from the mirror: {lines}"


def _check_side_source(code: str) -> None:
    assert _SIDE_DECL in code, "leftLabelSide's signature drifted from the mirror"
    body = _decl_block(code, _SIDE_DECL)
    lines = [l.strip() for l in body.strip("{}").splitlines() if l.strip()]
    assert lines == [
        "let tol = max(0, tolerance)",
        "let above = pointYsInSpan.filter { $0 < priceY - tol }.count",
        "let below = pointYsInSpan.filter { $0 > priceY + tol }.count",
        "return below < above ? .below : .above",
    ], f"leftLabelSide drifted from the mirror: {lines}"
    side_enum = _decl_block(code, "enum LeftLabelSide")
    assert [l.strip() for l in side_enum.strip("{}").splitlines() if l.strip()] == ["case above", "case below"]


def _check_center_source(code: str) -> None:
    assert _CENTER_DECL in code, "leftLabelCenterY's signature drifted from the mirror"
    body = _decl_block(code, _CENTER_DECL)
    lines = [l.strip() for l in body.strip("{}").splitlines() if l.strip()]
    assert lines == [
        "let lowest = max(top, bottom)",
        "let wanted = side == .above ? priceY - offset : priceY + offset",
        "let first = min(max(wanted, top), lowest)",
        "if abs(first - priceY) >= clearance { return first }",
        "let flipped = side == .above ? priceY + offset : priceY - offset",
        "let other = min(max(flipped, top), lowest)",
        "return abs(other - priceY) > abs(first - priceY) ? other : first",
    ], f"leftLabelCenterY drifted from the mirror: {lines}"


def _check_pole_draws_extension_and_cap(code: str) -> None:
    pole = _decl_block(code, "private func rangePole(")
    # The extension is the pure decision, drawn in neutral gray at the range body's width/opacity.
    assert "Self.priceExtension(price: price, low: b.low, high: b.high)" in pole
    ext_at = pole.index("Self.priceExtension(")
    ext = pole[ext_at:pole.index("Capsule()", ext_at)]
    assert "let fromY = yPosition(for: ext.from, in: geometry)" in ext
    assert "let toY = yPosition(for: ext.to, in: geometry)" in ext
    assert ".fill(AppColors.textMuted.opacity(0.35))" in ext, "the extension is gray, same opacity as the range"
    assert ".frame(width: 6, height: max(abs(toY - fromY), 2))" in ext
    assert ".position(x: xPos, y: (fromY + toY) / 2)" in ext
    assert not re.search(r"AppColors\.(accent\w*|gain\w*|loss\w*|bullish|bearish|caution\w*|primary\w*)", ext), \
        "the extension must be neutral gray, never the range's accent or a gain/loss colour"
    # The price cap sits AT the price — the y of `price` itself, never a computed point past it.
    assert "let priceY = yPosition(for: price, in: geometry)" in pole
    assert re.search(r"Capsule\(\)\s*\.fill\(AppColors\.textMuted\)\s*\.frame\(width: 14, height: 3\)\s*"
                     r"\.position\(x: xPos, y: priceY\)", pole), "a gray 14x3 cap marks the price"
    assert pole.count("yPosition(for: price") == 1
    # Draw order: range body, extension, price cap, High and Low caps, estimate dot LAST — the
    # gray price cap must never cover an accent range cap (a price at High would read as High).
    marks = (".fill(AppColors.accentGraphic.opacity(0.35))",
             ".fill(AppColors.textMuted.opacity(0.35))",
             ".position(x: xPos, y: priceY)",
             ".position(x: xPos, y: highY)",
             ".position(x: xPos, y: lowY)",
             ".position(x: xPos, y: valueY)")
    for t in marks:
        assert pole.count(t) == 1, f"{t!r} must be drawn exactly once"
    order = [pole.index(t) for t in marks]
    assert order == sorted(order), \
        "draw order: body, extension, price cap, High/Low caps, estimate dot last"
    plot = _decl_block(code, "private var plot: some View")
    assert "rangePole(b, price: validPrice," in plot, "the pole is fed only the sanitised price"


def _check_left_label(code: str) -> None:
    col = _decl_block(code, "private func labelColumn(")
    assert "let priceGoesLeft = priceLabelIsLeft(in: geometry)" in col
    assert "let rows = priceGoesLeft ? allRows.filter { !$0.isPrice } : allRows" in col, \
        "the right column must leave Price out when it goes left"
    assert "rows.map(\\.y)" in col and "allRows.map(\\.y)" not in col, "the resolver gets the filtered rows"
    assert "if priceGoesLeft, let priceRow = allRows.first(where: { $0.isPrice }) {" in col
    decide = _decl_block(code, "private func priceLabelIsLeft(")
    assert "guard let p = validPrice, let b = bounds else { return false }" in decide
    for mark in ("b.high", "b.value", "b.low"):
        assert f"yPosition(for: {mark}, in: geometry)" in decide, mark
    assert "Self.priceLabelGoesLeft(priceY: yPosition(for: p, in: geometry)," in decide
    assert "markYs: marks + anchors, minGap: labelMinGap)" in decide
    left = _decl_block(code, "private func leftPriceLabel(")
    assert ".background(AppColors.cardBackground)" in left, "the left label needs an OPAQUE card-colour backing"
    assert ".opacity(" not in left, "a translucent backing lets the dashed line cross the text"
    assert "Self.poleX(chartWidth: chartWidth) - Layout.capHalfWidth - Layout.leftLabelGap" in left
    assert ".frame(width: boxWidth, alignment: .trailing)" in left, "right-aligned toward the pole"
    # Beside the dashed line, never on it: the side the line crosses less, then a clamped centre.
    assert "let side = Self.leftLabelSide(" in left
    assert "priceY: row.y," in left
    assert ("pointYsInSpan: linePointYs(from: textTrailingX - Layout.labelWidth, "
            "chartWidth: chartWidth, in: geometry),") in left, "the span uses the worst-case label width"
    assert "tolerance: Layout.leftLabelSideTolerance" in left
    assert "let y = Self.leftLabelCenterY(" in left
    assert "side: side," in left
    assert "offset: labelLineHeight / 2 + Layout.leftLabelLineClearance," in left
    assert "clearance: labelLineHeight / 2," in left
    assert "top: labelInset," in left and "bottom: geometry.size.height - labelInset" in left
    assert ".position(x: centerX, y: y)" in left
    assert "row.y, labelInset" not in left, "the label must not sit level with the price line again"
    # TEXT-SIZED backing: applied to the padded text before the fixed-width frame, so it masks
    # only the text, never a 96pt box over the line's last stretch.
    assert left.index(".background(AppColors.cardBackground)") < left.index(".frame(width: boxWidth"), \
        "the backing must be applied before the frame (text-sized)"
    span = _decl_block(code, "private func linePointYs(")
    assert "guard showsLine else { return [] }" in span
    assert "where Self.lineX(index: index, count: points.count, chartWidth: chartWidth) >= startX {" in span
    assert "ys.append(yPosition(for: price, in: geometry))" in span
    line = _decl_block(code, "private func priceLineChart(")
    assert "Self.lineX(index: index, count: points.count, chartWidth: chartWidth)" in line, \
        "the drawn line and the side choice share one x formula"
    lx = _decl_block(code, "private static func lineX(")
    assert "let lineWidth = max(chartWidth - Layout.poleGap, 1)" in lx
    assert "columnLabel(row)" in left, "same font and colours as the column label"
    layout = _decl_block(code, "enum Layout")
    assert "static let capHalfWidth: CGFloat = 7" in layout
    assert "static let leftLabelGap: CGFloat = 6" in layout
    assert "static let leftLabelLineClearance: CGFloat = 3" in layout
    assert "static let leftLabelSideTolerance: CGFloat = 1.5" in layout
    rows = _decl_block(code, "private struct ColumnLabel")
    assert "static let priceRank = 3" in rows and "var isPrice: Bool { rank == Self.priceRank }" in rows
    labels = _decl_block(code, "private func columnLabels(")
    assert "rank: ColumnLabel.priceRank," in labels and '"Price"' in labels


def test_price_extension_source_matches_the_mirror():
    _check_extension_source(_code())


def test_label_goes_left_source_matches_the_mirror():
    _check_left_source(_code())


def test_left_label_side_source_matches_the_mirror():
    _check_side_source(_code())


def test_left_label_center_source_matches_the_mirror():
    _check_center_source(_code())


def test_the_pole_draws_a_gray_extension_and_a_gray_price_cap():
    _check_pole_draws_extension_and_cap(_code())


def test_the_price_label_moves_left_beside_the_line_on_an_opaque_backing():
    _check_left_label(_code())


# ── the mirrors ─────────────────────────────────────────────────────────────────────────────

def _finite(x: float) -> bool:
    return not (math.isnan(x) or math.isinf(x))


def price_extension(price, low, high):
    if not (_finite(price) and _finite(low) and _finite(high) and low <= high):
        return None
    if price > high:
        return (high, price)
    if price < low:
        return (low, price)
    return None


def price_label_goes_left(price_y, mark_ys, min_gap):
    gap = max(0.0, min_gap)
    return any(abs(y - price_y) < gap for y in mark_ys)


def left_label_side(price_y, point_ys, tolerance):
    tol = max(0.0, tolerance)
    above = sum(1 for y in point_ys if y < price_y - tol)
    below = sum(1 for y in point_ys if y > price_y + tol)
    return "below" if below < above else "above"


def left_label_center_y(price_y, side, offset, clearance, top, bottom):
    lowest = max(top, bottom)
    wanted = price_y - offset if side == "above" else price_y + offset
    first = min(max(wanted, top), lowest)
    if abs(first - price_y) >= clearance:
        return first
    flipped = price_y + offset if side == "above" else price_y - offset
    other = min(max(flipped, top), lowest)
    return other if abs(other - price_y) > abs(first - price_y) else first


def test_extension_below_the_range_runs_from_low_down_to_the_price():
    # NVDA-like: range 268-352, price 211.
    assert price_extension(211, 268, 352) == (268, 211)


def test_extension_above_the_range_runs_from_high_up_to_the_price():
    # AAPL-like: range 160-237, price 348.
    assert price_extension(348, 160, 237) == (237, 348)


def test_extension_ends_exactly_at_the_price_never_past_it():
    for price, low, high in ((211, 268, 352), (348, 160, 237), (263, 180, 260), (0.01, 5, 9), (1e6, 1, 2)):
        ext = price_extension(price, low, high)
        assert ext is not None and ext[1] == price
        assert ext[0] in (low, high)


def test_no_extension_inside_or_on_the_range():
    assert price_extension(222, 180, 260) is None          # inside, near the estimate
    assert price_extension(190, 180, 260) is None          # inside, far from every mark
    assert price_extension(180, 180, 260) is None          # exactly at low
    assert price_extension(260, 180, 260) is None          # exactly at high
    assert price_extension(220, 180, 260) is None          # price == estimate


def test_degenerate_range():
    # low == high == estimate: on it → nothing; off it → from that one point.
    assert price_extension(100, 100, 100) is None
    assert price_extension(120, 100, 100) == (100, 120)
    assert price_extension(80, 100, 100) == (100, 80)
    # A malformed range or a non-finite input draws nothing rather than a wrong segment.
    assert price_extension(120, 200, 100) is None
    assert price_extension(float("nan"), 100, 200) is None
    assert price_extension(float("inf"), 100, 200) is None


GAP = 16.0


def test_label_goes_left_when_near_the_estimate():
    # Marks High 40, Estimate 100, Low 160; the price at 104 (≈ the estimate).
    assert price_label_goes_left(104, [40, 100, 160], GAP)
    assert price_label_goes_left(100, [40, 100, 160], GAP), "price == estimate"


def test_label_stays_right_when_far_from_every_mark():
    assert not price_label_goes_left(70, [40, 100, 160], GAP)
    assert not price_label_goes_left(190, [40, 100, 160], GAP)   # well below the range
    assert not price_label_goes_left(5, [40, 100, 160], GAP)     # well above it


def test_label_goes_left_just_outside_high_or_low():
    assert price_label_goes_left(36, [40, 100, 160], GAP)        # just above High
    assert price_label_goes_left(165, [40, 100, 160], GAP)       # just below Low
    assert price_label_goes_left(40, [40, 100, 160], GAP)        # exactly at High


def test_exactly_min_gap_apart_is_no_collision():
    assert not price_label_goes_left(84, [100], GAP)
    assert not price_label_goes_left(116, [100], GAP)
    assert price_label_goes_left(84.01, [100], GAP)
    assert price_label_goes_left(115.99, [100], GAP)


def test_all_marks_on_one_point():
    assert price_label_goes_left(100, [100, 100, 100], GAP)
    assert not price_label_goes_left(130, [100, 100, 100], GAP)


def test_no_marks_or_no_gap_never_moves_the_label():
    assert not price_label_goes_left(100, [], GAP)
    assert not price_label_goes_left(100, [100], 0)
    assert not price_label_goes_left(100, [100], -5), "a negative gap is clamped to 0"


TOL = 1.5


def test_side_all_points_below_puts_the_label_above():
    # Screen y grows downward: "below the line" = a larger y.
    assert left_label_side(100, [110, 120, 130], TOL) == "above"


def test_side_all_points_above_puts_the_label_below():
    assert left_label_side(100, [90, 80, 70], TOL) == "below"


def test_side_mixed_points_pick_the_emptier_side():
    assert left_label_side(100, [90, 80, 110], TOL) == "below"      # 2 above, 1 below
    assert left_label_side(100, [90, 110, 120, 130], TOL) == "above"  # 1 above, 3 below


def test_side_empty_span_is_above():
    assert left_label_side(100, [], TOL) == "above"


def test_side_tie_is_above():
    assert left_label_side(100, [90, 110], TOL) == "above"
    assert left_label_side(100, [80, 90, 110, 120], TOL) == "above"


def test_side_points_within_tolerance_are_ignored():
    # The pinned last point sits ON the line; points within 1.5pt count for neither side.
    assert left_label_side(100, [100, 101.5, 98.5, 101.0], TOL) == "above"
    assert left_label_side(100, [98.5, 101.5], TOL) == "above", "exactly at the tolerance counts for neither"
    assert left_label_side(100, [98.4, 101.0, 100.2], TOL) == "below", "only 98.4 counts (above the line)"
    assert left_label_side(100, [99.0], -3) == "below", "a negative tolerance is clamped to 0"


LINE, OFFSET, CLEAR, TOP, BOTTOM = 13.0, 13.0 / 2 + 3, 13.0 / 2, 7.5, 192.5


def test_center_sits_beside_the_line_never_on_it():
    assert left_label_center_y(100, "above", OFFSET, CLEAR, TOP, BOTTOM) == 100 - OFFSET
    assert left_label_center_y(100, "below", OFFSET, CLEAR, TOP, BOTTOM) == 100 + OFFSET
    for price_y in (TOP, TOP + 2, 50, 100, BOTTOM - 2, BOTTOM):
        for side in ("above", "below"):
            y = left_label_center_y(price_y, side, OFFSET, CLEAR, TOP, BOTTOM)
            assert TOP <= y <= BOTTOM, (price_y, side, y)
            assert abs(y - price_y) >= CLEAR, f"the text must clear the dashed line: {(price_y, side, y)}"


def test_center_flips_when_the_clamp_would_land_on_the_line():
    # The price at the very top: "above" would clamp onto the line → below instead.
    assert left_label_center_y(TOP, "above", OFFSET, CLEAR, TOP, BOTTOM) == TOP + OFFSET
    assert left_label_center_y(BOTTOM, "below", OFFSET, CLEAR, TOP, BOTTOM) == BOTTOM - OFFSET
    # Partly clamped but still clear of the line: keep the chosen side.
    y = left_label_center_y(TOP + 8, "above", OFFSET, CLEAR, TOP, BOTTOM)
    assert y == TOP and abs(y - (TOP + 8)) >= CLEAR


def test_center_on_a_plot_too_short_for_either_side_keeps_the_farther_one():
    # top == bottom: both sides clamp to the same y; the first (chosen) side wins.
    assert left_label_center_y(50, "above", OFFSET, CLEAR, 50, 50) == 50
    # A short plot where the flip lands farther from the line than the clamp.
    assert left_label_center_y(10, "above", OFFSET, CLEAR, 8, 14) == 14


# ── the guards are not vacuous (mutation self-check on in-memory copies) ────────────────────

_MUTATIONS = (
    (_check_extension_source, "if price > high { return (from: high, to: price) }",
     "if price > high { return (from: high, to: price * 1.05) }"),
    (_check_extension_source, "low <= high else", "low < high else"),
    (_check_left_source, "abs($0 - priceY) < gap", "abs($0 - priceY) <= gap"),
    (_check_pole_draws_extension_and_cap, ".fill(AppColors.textMuted.opacity(0.35))",
     ".fill(AppColors.accentGraphic.opacity(0.35))"),
    (_check_pole_draws_extension_and_cap, ".position(x: xPos, y: priceY)",
     ".position(x: xPos, y: priceY - 4)"),
    (_check_left_label, ".background(AppColors.cardBackground)",
     ".background(AppColors.cardBackground.opacity(0.6))"),
    (_check_left_label, "let rows = priceGoesLeft ? allRows.filter { !$0.isPrice } : allRows",
     "let rows = allRows"),
    (_check_left_label, "offset: labelLineHeight / 2 + Layout.leftLabelLineClearance,", "offset: 0,"),
    (_check_left_label, "pointYsInSpan: linePointYs(from: textTrailingX - Layout.labelWidth,",
     "pointYsInSpan: linePointYs(from: textTrailingX,"),
    (_check_left_label, """            .background(AppColors.cardBackground)
            .frame(width: boxWidth, alignment: .trailing)""",
     """            .frame(width: boxWidth, alignment: .trailing)
            .background(AppColors.cardBackground)"""),
    (_check_side_source, "return below < above ? .below : .above", "return below <= above ? .below : .above"),
    (_check_side_source, "filter { $0 < priceY - tol }", "filter { $0 < priceY }"),
    (_check_center_source, "if abs(first - priceY) >= clearance { return first }", "return first"),
)


_PRICE_CAP = """            if let price {
                let priceY = yPosition(for: price, in: geometry)
                Capsule()
                    .fill(AppColors.textMuted)
                    .frame(width: 14, height: 3)
                    .position(x: xPos, y: priceY)
            }
"""


def test_the_draw_order_guard_fails_when_the_price_cap_covers_the_range_caps():
    src = _CHART.read_text(encoding="utf-8")
    assert src.count(_PRICE_CAP) == 1, "price-cap block anchor gone — update this mutation"
    _check_pole_draws_extension_and_cap(_code(src))
    moved = src.replace(_PRICE_CAP, "", 1)
    anchor = "            Circle()\n                .fill(AppColors.accentGraphic)"
    assert moved.count(anchor) == 1
    moved = moved.replace(anchor, _PRICE_CAP + "\n" + anchor, 1)   # price cap AFTER High/Low caps
    with pytest.raises(AssertionError):
        _check_pole_draws_extension_and_cap(_code(moved))


@pytest.mark.parametrize("check, real, broken", _MUTATIONS)
def test_each_guard_fails_on_its_mutation(check, real, broken):
    src = _CHART.read_text(encoding="utf-8")
    assert real in src, f"mutation anchor {real!r} gone — update the mutation table"
    check(_code(src))                       # green on the real source
    with pytest.raises(AssertionError):
        check(_code(src.replace(real, broken, 1)))
