"""Revenue Breakdown chart — iOS deep-check guards + a Python port of its geometry (2026-09-30).

Findings #8, #9, #15, #84 (and the iOS half of #83) of the Financials-tab review. The
"How X Makes Money" waterfall sized its 320pt plot from `max(totalRevenue, totalCosts)` with
a floor fixed at 0 in a PROFIT year, never looked at net income for the top, stood a zero
-revenue chart on a constant 1-dollar scale, and laid each column out as a spacer stack that
its frame CENTRED when over-full. Nothing clipped. So:

  * an operating loss rescued by other income (Lyft FY2024 shape) ran the cost column out
    of the bottom of the frame — into the legend;
  * net income above revenue (eBay FY2021 shape) drew the profit bar over the header;
  * a pre-revenue SPAC with trust income drew a bar billions of points tall;
  * a small loss left no room for "-Net Loss", and centring lifted the bar off zero.

The chart now sizes itself from what is DRAWN (one rule for profit and loss, plus a
closed-form caption reservation), places bars by offset, overlays captions, clips as a
backstop, and shows "No revenue reported" when nothing reaches a dollar.

Part 1 is a faithful Python port of `RevenueBreakdownChartView`'s bounds + column extents,
run over the review's fixtures; it also runs the OLD rule over the same fixtures to prove
they really overflowed (so the port is not vacuously green). Part 2 is the source scan that
pins the Swift to that port — comment-stripped, brace-bound, mutation-tested once by hand
(`.claude/rules/testing.md` §3).
"""
from __future__ import annotations

import math
import pathlib
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pytest

# ── Part 1: geometry port ───────────────────────────────────────────────────────────

H = 320.0            # chartHeight
L = 22.0             # captionAllowance
MIN_BAR = 2.0        # minimumNetBarHeight
LABEL_GAP = 16.0     # minimumLabelGap
GAP = 2.0            # caption ↔ bar gap (the alignmentGuide offset)
TEXT_H = 17.0        # one captionSmall line at its 1.4× Dynamic Type cap (14pt font)
OLD_TEXT_H = 13.0    # the same line at the default size — what the old layout stacked
EPS = 1e-6
B = 1e9


@dataclass
class Card:
    """Mirror of `RevenueBreakdownData`'s inputs (the iOS model, not the DTO)."""
    segments: List[float]
    cogs: float
    opex: float
    tax: float
    ni: Optional[float] = None            # reportedNetIncome
    other: Optional[float] = None         # otherExpense
    elim: Optional[float] = None          # intersegmentEliminations

    # -- model ports (RevenueBreakdownModels.swift) --
    @property
    def total_revenue(self) -> float:
        return sum(self.segments)

    @property
    def net_revenue(self) -> float:
        return self.total_revenue - (self.elim or 0.0)

    def cost_items(self) -> List[Tuple[float, bool]]:
        lines = [self.cogs, self.opex, self.tax] + ([self.other] if self.other is not None else [])
        return [(-v, True) if v < 0 else (v, False) for v in lines]          # costLine

    def waterfall_items(self) -> List[Tuple[float, bool]]:
        step = [(self.elim, False)] if self.elim is not None and self.elim > 0 else []
        return step + self.cost_items()

    @property
    def drawn(self) -> float:                                                # drawnWaterfallTotal
        return sum(v for v, credit in self.waterfall_items() if not credit)

    @property
    def total_costs(self) -> float:
        return self.cogs + self.opex + self.tax + (self.other or 0.0)

    @property
    def net_profit(self) -> float:
        return self.ni if self.ni is not None else self.net_revenue - self.total_costs

    @property
    def is_profit(self) -> bool:
        return self.net_profit >= 0

    @property
    def has_chartable_magnitude(self) -> bool:
        largest = max(self.total_revenue, self.drawn, abs(self.net_profit))
        return math.isfinite(largest) and largest >= 1


# -- view ports (RevenueBreakdownChartView.swift) --

def ceiling(c: Card) -> float:
    return max(c.total_revenue, c.net_profit, c.drawn, 0.0) * 1.1


def floor_(c: Card) -> float:
    return min(c.net_profit, c.total_revenue - c.drawn, 0.0) * 1.2


def top(c: Card) -> float:                                                   # chartTopValue
    if not c.is_profit:
        return ceiling(c)
    needed = (c.net_profit * H - L * bottom(c)) / (H - L)
    return max(ceiling(c), needed) if math.isfinite(needed) else ceiling(c)


def bottom(c: Card) -> float:                                                # chartBottomValue
    if c.is_profit:
        return floor_(c)
    needed = (c.net_profit * H - L * top(c)) / (H - L)
    return min(floor_(c), needed) if math.isfinite(needed) else floor_(c)


def rng(c: Card) -> float:
    r = top(c) - bottom(c)
    return r if r > 0 and math.isfinite(r) else 1.0


def y(c: Card, v: float) -> float:
    return (top(c) - v) / rng(c) * H


def zero_y(c: Card) -> float:
    frac = -bottom(c) / rng(c) if bottom(c) < 0 else 0.0
    return H * (1 - frac)


def extents(c: Card) -> dict:
    """[top, bottom] of every drawn thing, in points from the plot top."""
    ppu = H / rng(c)
    z = zero_y(c)
    rev_top = y(c, c.total_revenue)
    rev = (rev_top, rev_top + c.total_revenue / rng(c) * H)
    cost_top = max((top(c) - c.total_revenue) * ppu, 0.0)
    cost = (cost_top, cost_top + c.drawn * ppu)
    true_h = abs(c.net_profit) * ppu
    bar_h = 0.0 if c.net_profit == 0 else max(true_h, MIN_BAR)
    if c.is_profit:
        net = (z - bar_h, z)
        caption = (z - bar_h - GAP - TEXT_H, z - bar_h - GAP)
    else:
        net = (z, z + bar_h)
        caption = (z + bar_h + GAP, z + bar_h + GAP + TEXT_H)
    return {"revenue": rev, "cost": cost, "net": net, "caption": caption, "zero": z}


def grid_values(c: Card) -> List[float]:
    quarter_step = c.net_revenue * 0.25 / rng(c) * H
    if c.is_profit and c.net_revenue > 0 and quarter_step >= LABEL_GAP:
        r = c.net_revenue
        ladder = [0.0, r * 0.25, r * 0.5, r * 0.75, r]
        frac = -bottom(c) / rng(c) if bottom(c) < 0 else 0.0
        return ([bottom(c)] + ladder) if bottom(c) < 0 and frac * H >= LABEL_GAP else ladder
    step = rng(c) / 4
    return [bottom(c), bottom(c) + step, bottom(c) + 2 * step, bottom(c) + 3 * step, top(c)]


def _swift_round(x: float) -> int:
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def percentage_labels(c: Card) -> List[str]:
    if not c.net_revenue > 0:
        return ["—" for _ in grid_values(c)]
    out = []
    for v in grid_values(c):
        pct = v / c.net_revenue * 100
        out.append("—" if not math.isfinite(pct) or abs(pct) > 9999 else f"{_swift_round(pct)}%")
    return out


# -- the OLD rule, for the non-vacuity control --

def old_overflows(c: Card) -> bool:
    """True when some column of the pre-fix layout was taller than its 320pt frame."""
    m = max(c.total_revenue, c.total_costs)
    t = m * 1.1 if m > 0 else 1.0
    b = 0.0 if c.is_profit else min(c.net_profit, c.total_revenue - c.drawn, 0.0) * 1.2
    r = t - b if t - b > 0 else 1.0
    ppu = H / r
    zero_frac = 0.0 if c.is_profit else abs(b) / r
    rev_col = c.total_revenue / r * H + (0 if c.is_profit else zero_frac * H)
    cost_col = max((t - c.total_revenue) * ppu, 0) + c.drawn * ppu
    nb = abs(c.net_profit) * ppu
    net_col = (OLD_TEXT_H + 2 + nb) if c.is_profit else (H * (1 - zero_frac) + nb + 2 + OLD_TEXT_H)
    return max(rev_col, cost_col, net_col) > H + 0.5


# The review's fixtures. Units are dollars; `other` is what the backend's plug sends:
# reported_revenue − cogs − opex − tax − net_income.
FIXTURES = {
    # #15 Lyft FY2024 shape: operating loss, tiny net profit, other income a credit.
    "lyft24_op_loss_net_profit": Card([5.786 * B], 3.4 * B, 2.54 * B, 0.01 * B, ni=0.023 * B,
                                      other=(5.786 - 3.4 - 2.54 - 0.01 - 0.023) * B),
    # #15 eBay FY2021 shape: net income above revenue from a one-off gain.
    "ebay21_ni_above_revenue": Card([10.42 * B], 3.0 * B, 5.0 * B, 1.5 * B, ni=13.6 * B,
                                    other=(10.42 - 9.5 - 13.6) * B),
    # #9(a): NI 3x revenue.
    "ni_three_x_revenue": Card([1 * B], 0.3 * B, 0.5 * B, 0.1 * B, ni=3 * B, other=(1 - 0.9 - 3) * B),
    # #9(b): pre-revenue SPAC with trust-interest income — the 1-dollar scale.
    "spac_zero_revenue_profit": Card([0.0], 0.0, 2e6, 1e6, ni=8e6, other=0 - 2e6 - 1e6 - 8e6),
    # #8 Example A: operating loss covered by non-operating income.
    "example_a_credit_rescue": Card([10 * B], 8 * B, 4 * B, 0.5 * B, ni=1 * B, other=(10 - 12.5 - 1) * B),
    # #8 Example B: valuation-allowance release (tax is a credit).
    "example_b_tax_benefit": Card([5 * B], 3 * B, 2.5 * B, -1 * B, ni=0.3 * B,
                                  other=(5 - 3 - 2.5 + 1 - 0.3) * B),
    # #8 third trigger: a stack 3% short of revenue ("exact" band) on thin margins.
    "thin_margin_short_stack": Card([97 * B], 70 * B, 25 * B, 4 * B, ni=1 * B, other=0.0),
    # #15 / #84: small losses.
    "small_loss_50b": Card([50 * B], 40 * B, 9.5 * B, 0.5 * B, ni=-0.5 * B, other=0.5 * B),
    "small_loss_10b": Card([10 * B], 6 * B, 3.5 * B, 0.3 * B, ni=-0.05 * B, other=0.25 * B),
    "loss_10b_costs_11b": Card([10 * B], 7 * B, 3.5 * B, 0.5 * B, ni=-1 * B, other=0.0),
    # INTC FY2025: gross stack + eliminations, a credit, the loss-floor fixture.
    "intc25_gross_with_credit": Card([32.228 * B, 17.826 * B, 16.919 * B, 3.563 * B], 34.478 * B,
                                     18.398 * B, 1.531 * B, ni=-0.267 * B,
                                     other=(52.853 - 34.478 - 18.398 - 1.531 + 0.267) * B,
                                     elim=17.683 * B),
}

CONTROLS = {
    # Ordinary shapes that must keep rendering inside the frame (and did before).
    "apple_sample": Card([205.5 * B, 73.1 * B, 32.2 * B, 25.1 * B, 20.45 * B], 192 * B, 91 * B, 5 * B),
    "rivian_sample": Card([4.4 * B, 0.3 * B, 0.1 * B], 6.5 * B, 3.2 * B, 0.05 * B),
    "lmt_credit_opex": Card([75.06 * B], 67.429 * B, -0.112 * B, 1.0 * B, ni=5.0 * B,
                            other=(75.06 - 67.429 + 0.112 - 1.0 - 5.0) * B),
    "biotech_pre_revenue_loss": Card([0.0], 0.0, 500e6, 0.0, ni=-480e6, other=0 - 500e6 + 480e6),
    "negative_revenue_filer": Card([0.0], 0.5 * B, 0.3 * B, 0.0, ni=-2.0 * B,
                                   other=(-1.2 - 0.5 - 0.3 + 2.0) * B),
    "breakeven_exact_zero": Card([10 * B], 6 * B, 4 * B, 0.0, ni=0.0, other=0.0),
    "extreme_gain_1e6x": Card([1 * B], 0.3 * B, 0.5 * B, 0.1 * B, ni=1e15, other=1 * B - 0.9 * B - 1e15),
    "extreme_loss_1e6x": Card([1e6], 0.0, 1e12, 0.0, ni=-1e12 + 1e6, other=0.0),
    "one_dollar_revenue": Card([1.0], 0.0, 0.0, 0.0, ni=0.0, other=1.0),
}

ALL = {**FIXTURES, **CONTROLS}


@pytest.mark.parametrize("name", list(FIXTURES))
def test_the_old_rule_really_overflowed_on_every_regression_fixture(name):
    """Non-vacuity control: each fixture is one the old layout drew out of its frame."""
    assert old_overflows(FIXTURES[name]), f"{name} never overflowed — it proves nothing"


@pytest.mark.parametrize("name", list(ALL))
def test_every_column_and_caption_stays_inside_the_plot(name):
    c = ALL[name]
    assert c.has_chartable_magnitude
    e = extents(c)
    for part in ("revenue", "cost", "net", "caption"):
        lo, hi = e[part]
        assert all(math.isfinite(v) for v in (lo, hi)), (name, part, e[part])
        assert lo >= -EPS and hi <= H + EPS, f"{name}: {part} spans {lo:.1f}..{hi:.1f} in a {H:.0f}pt plot"


@pytest.mark.parametrize("name", list(ALL))
def test_bars_stand_on_the_zero_line_and_the_waterfall_starts_at_the_stack_top(name):
    c = ALL[name]
    e = extents(c)
    z = e["zero"]
    assert e["revenue"][1] == pytest.approx(z, abs=1e-6), "the revenue stack must stand on zero"
    if c.is_profit:
        assert e["net"][1] == pytest.approx(z, abs=1e-6), "the profit bar must stand on zero"
    else:
        assert e["net"][0] == pytest.approx(z, abs=1e-6), "the loss bar must hang FROM zero"
    assert e["cost"][0] == pytest.approx(e["revenue"][0], abs=1e-6), \
        "the cost column's top must line up with the revenue top"


@pytest.mark.parametrize("name", list(ALL))
def test_the_bounds_contain_every_drawn_value(name):
    c = ALL[name]
    t, b = top(c), bottom(c)
    assert b <= 0 <= t and t > b
    assert t >= c.total_revenue - EPS and t >= c.net_profit - EPS
    assert b <= c.total_revenue - c.drawn + EPS and b <= c.net_profit + EPS


def test_a_profit_year_whose_waterfall_dips_below_zero_gets_a_zero_line():
    c = FIXTURES["lyft24_op_loss_net_profit"]
    assert c.is_profit and bottom(c) < 0, "the floor is no longer pinned at 0 in a profit year"
    assert zero_y(c) < H


def test_the_caption_reservation_is_exact_not_iterated():
    """(NI − bottom)·h/(top − bottom) == l at the binding constraint, to rounding."""
    c = FIXTURES["small_loss_10b"]
    assert bottom(c) < floor_(c), "the caption, not the 1.2 pad, must bind for a small loss"
    assert (c.net_profit - bottom(c)) / rng(c) * H == pytest.approx(L, rel=1e-9)


@pytest.mark.parametrize("card", [
    Card([0.0], 0.0, 0.0, 0.0),                       # the backend's "no data" placeholder
    Card([0.0], 0.0, 0.0, 0.0, ni=0.0, other=0.0),    # a shell with every line at zero
    Card([0.4], 0.0, 0.0, 0.0, ni=0.3, other=0.1),    # sub-dollar
])
def test_nothing_reaching_a_dollar_gets_the_placeholder_not_a_scale(card):
    assert not card.has_chartable_magnitude


@pytest.mark.parametrize("name", list(ALL))
def test_grid_labels_sit_inside_the_plot_and_never_overlap(name):
    c = ALL[name]
    ys = sorted(y(c, v) for v in grid_values(c))
    assert all(-EPS <= v <= H + EPS for v in ys), (name, ys)
    gaps = [b - a for a, b in zip(ys, ys[1:])]
    assert all(g >= LABEL_GAP - EPS for g in gaps), f"{name}: label gaps {gaps}"
    assert len(percentage_labels(c)) == len(grid_values(c))


@pytest.mark.parametrize("rev", [1.0, 3.0, 7.0, 52.853 * B, 75.06 * B, 391.035 * B, 5.786 * B])
def test_the_profit_ladder_still_reads_exactly_0_to_100_percent(rev):
    c = Card([rev], rev * 0.5, rev * 0.2, rev * 0.05, ni=rev * 0.25, other=0.0)
    assert percentage_labels(c) == ["0%", "25%", "50%", "75%", "100%"]


def test_a_profit_floor_label_reads_its_real_share():
    c = FIXTURES["example_a_credit_rescue"]
    labels = percentage_labels(c)
    assert grid_values(c)[0] == pytest.approx(bottom(c)) and labels[0].startswith("-"), labels
    assert labels[1:] == ["0%", "25%", "50%", "75%", "100%"]


def test_no_revenue_means_dashes_not_percentages():
    assert set(percentage_labels(CONTROLS["biotech_pre_revenue_loss"])) == {"—"}
    assert set(percentage_labels(FIXTURES["spac_zero_revenue_profit"])) == {"—"}


def test_an_off_the_chart_share_is_a_dash_not_a_repeated_clamp():
    labels = percentage_labels(CONTROLS["extreme_gain_1e6x"])
    assert "9999%" not in labels and labels.count("—") >= 3, labels


# ── Part 2: source scans pinning the Swift to the port ──────────────────────────────

_REPO = pathlib.Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"
_CHART = _IOS / "Views" / "Molecules" / "RevenueBreakdownChartView.swift"
_MODEL = _IOS / "Models" / "RevenueBreakdownModels.swift"
_LEGEND = _IOS / "Views" / "Molecules" / "RevenueBreakdownLegendView.swift"
_CARD = _IOS / "Views" / "Organisms" / "RevenueBreakdownSectionCard.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _body(src: str, prefix: str) -> str:
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


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def test_the_top_frames_net_profit_and_the_drawn_costs_with_no_one_dollar_scale():
    body = _body(_code(_CHART), "private var chartTopValue: Double")
    assert re.search(r"max\(\s*data\.totalRevenue,\s*data\.netProfit,\s*data\.drawnWaterfallTotal,\s*0\s*\)\s*\*\s*1\.1",
                     body), body
    assert not re.search(r"return\s+1(\.0*)?\s*$", body, re.M), "the constant 1-dollar scale is back"
    assert "data.totalCosts" not in body, "credits are netted into totalCosts — it is not what is drawn"
    assert "captionAllowance" in body and "chartBottomValue" in body, "the profit caption reservation is gone"


def test_the_floor_follows_the_waterfall_in_both_branches():
    body = _body(_code(_CHART), "private var chartBottomValue: Double")
    assert re.search(r"min\(data\.netProfit,\s*waterfallBottom,\s*0\)\s*\*\s*1\.2", body), body
    assert not re.search(r"return\s+0(\.0*)?\s*$", body, re.M), "a profit year's floor is pinned at 0 again"
    floor_at = body.index("waterfallBottom, 0)")
    branch_at = body.index("isProfit")
    assert floor_at < branch_at, "the waterfall floor must be computed BEFORE any profit/loss branch"
    assert "captionAllowance" in body and "chartTopValue" in body, "the loss caption reservation is gone"


def test_the_port_constants_and_closed_forms_match_the_swift():
    """Part 1 proves the geometry only if it IS the Swift's geometry."""
    src = _code(_CHART)
    for name, value in (("chartHeight", H), ("captionAllowance", L), ("minimumNetBarHeight", MIN_BAR),
                        ("minimumLabelGap", LABEL_GAP)):
        m = re.search(rf"private let {name}: CGFloat = ([0-9.]+)", src)
        assert m and float(m.group(1)) == value, (name, m and m.group(1))
    t = _squash(_body(src, "private var chartTopValue: Double"))
    assert "guarddata.isProfitelse{returnceiling}" in t
    assert "letneeded=(data.netProfit*h-l*chartBottomValue)/(h-l)" in t
    assert "max(ceiling,needed)" in t
    b = _squash(_body(src, "private var chartBottomValue: Double"))
    assert "letfloor=min(data.netProfit,waterfallBottom,0)*1.2" in b
    assert "guard!data.isProfitelse{returnfloor}" in b
    assert "letneeded=(data.netProfit*h-l*chartTopValue)/(h-l)" in b
    assert "min(floor,needed)" in b
    net = _squash(_body(src, "private func netProfitBar"))
    assert "data.netProfit==0?0:max(trueHeight,minimumNetBarHeight)" in net
    assert "alignmentGuide(.top){$0[.bottom]+2}" in net and "alignmentGuide(.bottom){$0[.top]-2}" in net


def test_the_drawn_total_is_one_expression_in_the_model_and_the_floor():
    """`drawnWaterfallTotal` (model, feeds the top + the empty check) and the floor's local
    `drawnCosts` (pinned by test_ios_revenue_reconciliation) must not drift apart."""
    model = _body(_code(_MODEL), "var drawnWaterfallTotal: Double")
    floor_ = _body(_code(_CHART), "private var chartBottomValue: Double")
    expr = "waterfallItems.filter{!$0.isCredit}.reduce(0){$0+$1.value}"
    assert expr in _squash(model), model
    assert ("data." + expr) in _squash(floor_), floor_


def test_the_zero_line_derives_from_the_floor_not_the_sign_of_net_income():
    src = _code(_CHART)
    zero = _body(src, "private var zeroLinePosition: CGFloat")
    assert "data.isProfit" not in zero and "chartBottomValue" in zero
    neg = _body(src, "private var hasNegativeRegion: Bool")
    assert "chartBottomValue < 0" in neg
    content = _body(src, "private var chartContent: some View")
    assert "if hasNegativeRegion" in content and "!data.isProfit" not in content


def test_the_plot_is_clipped_as_a_backstop():
    content = _body(_code(_CHART), "private var chartContent: some View")
    assert ".clipped()" in content, "nothing stops a bar painting over the header and legend"
    # On the GeometryReader itself (after its closing brace), not on one child inside it.
    reader = _body(content, "GeometryReader")
    assert ".clipped()" not in reader and content.index(".clipped()") > content.index(reader)


@pytest.mark.parametrize("func", ["private func revenueStackedBar", "private func costWaterfallBar",
                                  "private func netProfitBar"])
def test_columns_are_placed_by_offset_in_a_top_aligned_frame_never_by_spacers(func):
    body = _body(_code(_CHART), func)
    assert "Spacer(" not in body, f"{func}: an over-full spacer stack is CENTRED by its frame"
    assert ".offset(y:" in body
    assert re.search(r"\.frame\(width:\s*barWidth,\s*height:\s*height,\s*alignment:\s*\.top\)", body), body
    assert "if !data.isProfit" not in body


def test_both_net_bars_stand_on_the_zero_line_with_overlaid_captions():
    body = _body(_code(_CHART), "private func netProfitBar")
    assert re.search(r"let zeroY = height \* \(1 - zeroLinePosition\)", body)
    assert ".offset(y: zeroY - netProfitHeight)" in body, "the profit bar must stand on zero"
    assert ".offset(y: zeroY)" in body, "the loss bar must hang from zero"
    assert body.count(".overlay(alignment:") == 2, "captions must be overlays, not stack members"
    assert "minimumNetBarHeight" in body
    # The stack-top revenue bar shares the same zero: its offset is the stack top.
    stack = _body(_code(_CHART), "private func revenueStackedBar")
    assert "yPosition(for: data.totalRevenue" in stack


def test_the_empty_state_replaces_the_chart_and_the_legend():
    src = _code(_CHART)
    body = _body(src, "var body: some View")
    assert "if data.hasChartableMagnitude" in body and "noRevenuePlaceholder" in body
    assert '"No revenue reported"' in _body(src, "private var noRevenuePlaceholder: some View")
    card = _body(_code(_CARD), "var body: some View")
    gate = card.index("if data.hasChartableMagnitude")
    assert gate < card.index("RevenueBreakdownLegendView(data: data)"), "the legend lists zeros again"
    model = _body(_code(_MODEL), "var hasChartableMagnitude: Bool")
    assert re.search(r"max\(totalRevenue,\s*drawnWaterfallTotal,\s*abs\(netProfit\)\)", model), model
    assert ">= 1" in model


def test_the_ladder_keeps_its_labels_apart():
    body = _body(_code(_CHART), "private var gridValues: [Double]")
    assert "quarterStep >= minimumLabelGap" in body, "a gain far above revenue squeezes the ladder"
    assert "floorGap >= minimumLabelGap" in body


def test_percentages_are_nan_not_zero_without_positive_revenue():
    model = _code(_MODEL)
    # Scope CostItem FIRST: RevenueSource declares an identically-signed function earlier.
    cost = _body(_body(model, "struct CostItem: Identifiable"), "func percentage(of total: Double) -> Double")
    src_ = _body(_body(model, "struct RevenueSource: Identifiable"), "func percentage(of total: Double) -> Double")
    net = _body(model, "func netProfitPercentage() -> Double")
    for name, body in (("CostItem", cost), ("RevenueSource", src_), ("netProfitPercentage", net)):
        guard = re.search(r"guard [^\n]*> 0 else \{ return ([^}]*)\}", body)
        assert guard, (name, body)
        assert guard.group(1).strip() == ".nan", f"{name}: '0%' beside a non-zero amount is back"


def test_the_legend_prints_a_negative_reported_revenue_signed():
    model = _body(_code(_MODEL), "func legendValue(for source: RevenueSource) -> String")
    assert "reportedRevenue < 0" in model and "CompactNumberFormat.string(reportedRevenue)" in model
    assert 'source.name == "Total Revenue"' in model and "revenueSources.count == 1" in model
    legend = _body(_code(_LEGEND), "var body: some View")
    rev_col = legend[legend.index("ForEach(data.revenueSources)"): legend.index("ForEach(data.costItems)")]
    assert "data.legendValue(for: source)" in rev_col
    assert "source.formattedValue" not in rev_col


def test_previews_carry_the_outlier_shapes():
    raw = _CHART.read_text(encoding="utf-8")
    preview = raw[raw.index("#Preview"):]
    for sample in ("sampleOperatingLossTurnedProfit", "sampleGainAboveRevenue", "sampleSmallLoss",
                   "sampleNoRevenue"):
        assert sample in preview, f"#Preview lost the {sample} case"
        assert f"static let {sample}" in _code(_MODEL)


def test_the_scan_bounds_really_bound():
    """Brace-bounding control: each scanned body is a small part of its file."""
    chart = _code(_CHART)
    for prefix in ("private var chartTopValue: Double", "private var chartBottomValue: Double",
                   "private func netProfitBar", "private var chartContent: some View"):
        assert len(_body(chart, prefix)) < len(chart) / 4, prefix
    # The stripping really strips: the file's comments narrate the removed spacer layout,
    # and none of that prose may survive into the code the scans read.
    raw = _CHART.read_text(encoding="utf-8")
    assert "spacer" in raw.lower() and "spacer" not in chart.lower()
