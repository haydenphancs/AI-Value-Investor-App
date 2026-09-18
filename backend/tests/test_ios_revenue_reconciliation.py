"""iOS guards for the reconciled revenue stack (2026-09-17).

The backend now sends the segments AS REPORTED plus, when they are gross of intersegment
sales, `intersegment_eliminations` (INTC FY2025: 70.5B of segments, 52.9B of revenue,
17.7B eliminated). iOS must: decode it as Optional; keep the STACK gross (`totalRevenue`)
while measuring everything else against `netRevenue`; draw the eliminations as the first
waterfall step so the costs start at reported revenue; list it as a negative legend line
so the revenue column adds to 100%; and NOT also fold an "Other" remainder in (the two
mechanisms are mutually exclusive). Backend half: tests/test_revenue_breakdown_reconciliation.py.

Comment-stripped, brace-bound, mutation-tested by hand (`.claude/rules/testing.md` §3).
"""
from __future__ import annotations

import pathlib
import re

_REPO = pathlib.Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"
_REPO_SWIFT = _IOS / "Core" / "Repositories" / "StockRepository.swift"
_MODEL = _IOS / "Models" / "RevenueBreakdownModels.swift"
_CHART = _IOS / "Views" / "Molecules" / "RevenueBreakdownChartView.swift"
_LEGEND = _IOS / "Views" / "Molecules" / "RevenueBreakdownLegendView.swift"


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


def test_the_dto_decodes_eliminations_as_optional_and_hands_them_to_the_model():
    dto = _decl_body(_code(_REPO_SWIFT), "struct RevenueBreakdownDTO")
    assert re.search(r"let intersegmentEliminations:\s*Double\?", dto), dto
    assert 'case intersegmentEliminations = "intersegment_eliminations"' in dto
    assert "intersegmentEliminations: intersegmentEliminations" in dto
    # The "Other" remainder fold and the eliminations are mutually exclusive.
    fold = dto.index("let remainder = reportedRevenue - segmentSum")
    gate = dto.rfind("if intersegmentEliminations == nil", 0, fold)
    assert gate != -1 and fold - gate < 250, "the remainder fold must be gated on no eliminations"
    assert 'source.name == "Unallocated"' in dto, "the backend's Unallocated slice takes the grey"


def test_the_model_keeps_the_stack_gross_and_measures_against_net_revenue():
    src = _code(_MODEL)
    body = _decl_body(src, "struct RevenueBreakdownData")
    assert re.search(r"let intersegmentEliminations:\s*Double\?", body)
    net = _decl_body(body, "var netRevenue: Double")
    assert re.search(r"totalRevenue\s*-\s*\(intersegmentEliminations \?\? 0\)", net), net
    total = _decl_body(body, "var totalRevenue: Double")
    assert "revenueSources.reduce" in total and "intersegmentEliminations" not in total, (
        "the STACK stays gross — the eliminations are a step, not a rescale"
    )
    profit = _decl_body(body, "var netProfit: Double")
    assert "netRevenue - totalCosts" in profit and "totalRevenue - totalCosts" not in profit
    exceed = _decl_body(body, "var costsExceedRevenue: Bool")
    assert "totalCosts > netRevenue" in exceed


def test_the_waterfall_gets_the_eliminations_first_and_the_legend_a_negative_line():
    src = _code(_MODEL)
    body = _decl_body(src, "struct RevenueBreakdownData")
    step = _decl_body(body, "var eliminationsWaterfallStep: CostItem?")
    assert "guard let intersegmentEliminations, intersegmentEliminations > 0 else { return nil }" in step
    assert "value: intersegmentEliminations" in step
    items = _decl_body(body, "var waterfallItems: [CostItem]")
    assert re.search(r"\(eliminationsWaterfallStep\.map \{ \[\$0\] \} \?\? \[\]\)\s*\+\s*costItems", items), items
    legend = _decl_body(body, "var eliminationsLegendItem: RevenueSource?")
    assert "value: -intersegmentEliminations" in legend, "the legend line is NEGATIVE so the column adds to 100%"
    # and the legend's cost column is untouched — eliminations are not a cost
    costs = _decl_body(body, "var costItems: [CostItem]")
    assert "intersegmentEliminations" not in costs and "eliminations" not in costs.lower()


def test_the_chart_draws_the_bridge_and_marks_100_percent_at_net_revenue():
    src = _code(_CHART)
    waterfall = _decl_body(src, "private func costWaterfallBar")
    assert "data.waterfallItems.filter" in waterfall, "the cost column must draw waterfallItems"
    assert "data.costItems" not in waterfall
    assert "chartTopValue - data.totalRevenue" in waterfall, "the waterfall starts at the GROSS stack top"
    stack = _decl_body(src, "private func revenueStackedBar")
    assert "data.totalRevenue" in stack and "netRevenue" not in stack, "the stack itself stays gross"
    grid = _decl_body(src, "private var gridValues: [Double]")
    assert "data.netRevenue" in grid and "data.totalRevenue" not in grid
    labels = _decl_body(src, "private var percentageLabels: [String]")
    assert "data.netRevenue" in labels and "data.totalRevenue" not in labels


def test_the_legend_lists_the_eliminations_under_revenue_sources():
    body = _decl_body(_code(_LEGEND), "var body: some View")
    sources = body.index("ForEach(data.revenueSources)")
    elim = body.index("if let eliminations = data.eliminationsLegendItem")
    costs = body.index("ForEach(data.costItems)")
    assert sources < elim < costs, "the eliminations line belongs to the revenue column"
    assert "eliminations.formattedPercentage(of: data.revenueBasis)" in body


_VM = _IOS / "ViewModels" / "TickerDetailViewModel.swift"


def test_cay_ai_revenue_mix_uses_the_cards_denominator_and_names_the_eliminations():
    """The chat grounding divided by the GROSS stack, so for INTC it read "Client Computing
    46%" while the legend beside it said 61%. Same denominator, same eliminations line."""
    src = _code(_VM)
    at = src.index("Revenue mix (FY")
    block = src[src.rfind("if let rb = revenueBreakdownData", 0, at): at]
    assert "rb.revenueBasis" in block and "rb.totalRevenue" not in block, block
    assert "rb.eliminationsLegendItem" in block


def test_the_loss_floor_covers_where_the_waterfall_actually_lands():
    """The waterfall skips credit lines, so with a credit it ends BELOW net income; a floor
    from net income alone let the bar bleed out of the frame (INTC FY2025 fixture)."""
    body = _decl_body(_code(_CHART), "private var chartBottomValue: Double")
    assert "waterfallItems.filter { !$0.isCredit }" in body
    assert re.search(r"min\(data\.netProfit,\s*waterfallBottom,\s*0\)\s*\*\s*1\.2", body), body
    assert "data.netProfit * 1.2" not in body
