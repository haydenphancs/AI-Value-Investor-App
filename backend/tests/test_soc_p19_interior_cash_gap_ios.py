"""P19 (2026-10-01) — the iOS half: a Signal of Confidence quarter with NO cash-flow filing
on record reads "—", never a fabricated "0.00%" / "$0M".

The backend keeps the point (its share count is real) and sends the four cash fields as
0.0 PLACEHOLDERS — they stay non-Optional floats because shipped builds decode them as
`Double` in a DTO the report reuses, so a null would break both decodes — plus a new
per-point `cash_flow_reported` flag (default true). iOS:

  * decodes the flag as `Bool?` (nil → reported: older backend, device cache, stored report)
    through ONE DTO helper shared by the Financials tab and the report;
  * keeps the four display Doubles and adds `var cashFlowReported: Bool = true` LAST, so
    every memberwise init and preview compiles unchanged;
  * prints "—" in the chart's dividend/buyback label rows, the mini-chart popup and the
    report's newest-buyback cell, and adds a one-line key under the chart when any quarter
    is unreported;
  * does NOT filter the bars: a 0.0 placeholder is a zero-height bar (how a measured zero
    already looks), and skipping a period would let Swift Charts reorder its category
    column off the index-positioned label rows.

Fix pass (2026-10-01): `cash_flow_reported` is false for TWO causes — a quarter missing
from the filing history, and EVERY quarter when the quarterly cash-flow fetch itself failed
(a 429/5xx: the server's `degraded` carries "cash_flow"). So the card's key names no cause
("— = no cash-flow filing on record" was a false claim about the company during an outage)
and names its two rows (the shares row prints its own "—" for an unreported share count).
The outage itself is not a card at all: `SignalOfConfidenceResponseDTO.cashFlowLegFailed`
(EXACTLY "cash_flow" — never "cash_flow_row" / "cash_flow_statement_missing", which describe
the filing) is the gate the Financials ViewModel uses to drop the card and offer Try Again
instead of a column of dashes over a fabricated 0% / "Low" summary.

No XCTest target exists, so these are SOURCE-SCAN guards (testing.md §3): every scan strips
comments first and is brace-bound to the declaration it means, and every guard is
mutation-tested here — each parametrized mutation edits the REAL Swift source in memory
and must make the guard fail. The Python ports below pin the degraded behaviour on outlier
series (interior / leading-edge / all-unreported gaps, a measured zero, a malformed flagged
point carrying non-zero values).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_REPO = _IOS / "Core" / "Repositories" / "StockRepository.swift"
_REPORT_DTOS = _IOS / "Models" / "TickerReportResponse.swift"
_REPORT_MODELS = _IOS / "Models" / "TickerReportModels.swift"
_MODELS = _IOS / "Models" / "SignalOfConfidenceModels.swift"
_CHART = _IOS / "Views" / "Molecules" / "SignalOfConfidenceChartView.swift"
_MINI = _IOS / "Views" / "Molecules" / "CapitalAllocationMiniChart.swift"
_SECTION = _IOS / "Views" / "Organisms" / "SignalOfConfidenceSectionCard.swift"
_VM = _IOS / "ViewModels" / "TickerDetailViewModel.swift"
_SOC_SERVICE = (Path(__file__).resolve().parents[1] / "app" / "services"
                / "signal_of_confidence_service.py")

_DASH = "—"
_CAPTION = "— in Dividends/Buybacks = cash-flow figures unavailable for that quarter"
# The pre-fix key: it named a cause that is false during a cash-flow fetch outage, and its
# bare "— =" read as explaining the shares row's dash too.
_OLD_CAPTION = "— = no cash-flow filing on record for that quarter"


# ── source helpers ───────────────────────────────────────────────────────────


def _strip_comments(src: str) -> str:
    """Drop // line comments and /* */ blocks, leaving string literals intact."""
    out: List[str] = []
    i, n = 0, len(src)
    in_str = False
    while i < n:
        c = src[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _src(path: Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return path.read_text(encoding="utf-8")


def _body(code: str, signature: str) -> str:
    """The brace-balanced body that follows the FIRST occurrence of ``signature``."""
    i = code.find(signature)
    assert i != -1, f"declaration not found: {signature!r}"
    j = code.index("{", i + len(signature) - (1 if signature.endswith("{") else 0))
    depth = 0
    for k in range(j, len(code)):
        if code[k] == "{":
            depth += 1
        elif code[k] == "}":
            depth -= 1
            if depth == 0:
                return code[j + 1:k]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _top_level_stored_properties(struct_body: str) -> List[str]:
    """Names of the stored `let`/`var` properties declared at the struct's own depth (a
    computed `var x: T {` is excluded — its line opens a brace)."""
    names: List[str] = []
    depth = 0
    for line in struct_body.splitlines():
        if depth == 0:
            m = re.match(r"\s*(?:let|var)\s+(\w+)\s*(?::[^{=]+)?(?:=[^{]*)?$", line)
            if m:
                names.append(m.group(1))
        depth += line.count("{") - line.count("}")
    return names


def _expect_guard_failure(guard: Callable[[str], None], src: str, before: str, after: str):
    """Apply ONE edit to the real Swift source and require the guard to reject it."""
    assert src.count(before) == 1, f"mutation anchor drifted: {before!r}"
    mutated = src.replace(before, after)
    with pytest.raises(AssertionError):
        guard(mutated)


# ── (a) the display model: a defaulted flag, LAST, beside non-Optional Doubles ─


def _assert_display_model(models_src: str) -> None:
    point = _body(_strip_comments(models_src), "struct SignalOfConfidenceDataPoint: Identifiable {")
    assert re.search(r"^\s*var cashFlowReported: Bool = true\s*$", point, re.M), \
        "the flag must default to true so ~20 memberwise inits and previews keep compiling"
    stored = _top_level_stored_properties(point)
    assert stored and stored[-1] == "cashFlowReported", (
        f"cashFlowReported must be the LAST stored property (memberwise order), got {stored!r}"
    )
    # The decision: keep the four Doubles (a placeholder draws a zero-height bar in place).
    for field in ("dividendYield", "buybackYield", "dividendAmount", "buybackAmount"):
        assert re.search(rf"^\s*let {field}: Double\s*$", point, re.M), \
            f"{field} changed type — the flag approach keeps it a non-Optional Double"


def test_display_model_declares_the_flag_last_with_a_true_default():
    _assert_display_model(_src(_MODELS))


@pytest.mark.parametrize("before, after", [
    ("    var cashFlowReported: Bool = true\n", "    var cashFlowReported: Bool\n"),
    ("    var cashFlowReported: Bool = true\n", "    var cashFlowReported: Bool = false\n"),
    # moved ahead of sharesOutstanding: memberwise call sites would take it positionally
    ("    let sharesOutstanding: Double?\n",
     "    var cashFlowReported: Bool = true\n    let sharesOutstanding: Double?\n"),
    ("    let buybackAmount: Double             // Dollar amount in millions",
     "    let buybackAmount: Double?"),
])
def test_display_model_guard_rejects_each_mutation(before, after):
    src = _src(_MODELS)
    if after.startswith("    var cashFlowReported: Bool = true\n    let sharesOutstanding"):
        # move, not duplicate: drop the original declaration too
        src = src.replace("    var cashFlowReported: Bool = true\n\n", "\n", 1)
    _expect_guard_failure(_assert_display_model, src, before, after)


# ── (b) the DTO + ONE mapping helper, used by BOTH paths ──────────────────────


def _assert_dto_and_helper(repo_src: str) -> None:
    code = _strip_comments(repo_src)
    dto = _body(code, "struct SignalOfConfidenceDataPointDTO: Codable {")
    assert re.search(r"^\s*let cashFlowReported: Bool\?\s*$", dto, re.M), \
        "the flag must decode as Bool? — a payload without the key must still decode"
    keys = _body(dto, "enum CodingKeys: String, CodingKey {")
    assert re.search(r'case cashFlowReported = "cash_flow_reported"', keys)
    # The wire placeholders stay non-Optional: shipped builds decode them as Double.
    for field in ("dividendYield", "buybackYield", "dividendAmount", "buybackAmount"):
        assert re.search(rf"^\s*let {field}: Double\s*$", dto, re.M), field

    helper = _body(dto, "func toDisplayPoint() -> SignalOfConfidenceDataPoint")
    assert re.search(r"cashFlowReported:\s*cashFlowReported\s*\?\?\s*true", helper), \
        "a missing key must mean 'reported' (today's behaviour), never 'unreported'"
    for field in ("period", "dividendYield", "buybackYield", "dividendAmount",
                  "buybackAmount", "sharesOutstanding", "marketCap"):
        assert re.search(rf"\b{field}:\s*{field}\b", helper), f"helper drops {field}"

    response = _body(code, "struct SignalOfConfidenceResponseDTO: Codable, FinancialsCacheable {")
    to_display = _body(response, "func toDisplayModel() -> SignalOfConfidenceSectionData")
    assert re.search(r"dataPoints\.map\s*\{\s*\$0\.toDisplayPoint\(\)\s*\}", to_display), \
        "the Financials tab must map through the shared helper"
    assert "SignalOfConfidenceDataPoint(" not in to_display, \
        "an inline construction can silently drop the flag on this path"


def _report_mapper(report_src: str) -> str:
    code = _strip_comments(report_src)
    start = code.index("capitalAllocation: insiderData.capitalAllocation.map")
    region = code[start:]
    return region[: region.index("insiderFlow:")]


def _assert_report_mapping(report_src: str) -> None:
    mapper = _report_mapper(report_src)
    assert re.search(
        r"let points: \[SignalOfConfidenceDataPoint\]\? = c\.dataPoints\?\.map\s*\{\s*\$0\.toDisplayPoint\(\)\s*\}",
        mapper,
    ), "the report's capital-allocation points must map through the shared helper"
    assert "SignalOfConfidenceDataPoint(" not in mapper, \
        "an inline construction can silently drop the flag on the report path"


def test_dto_decodes_the_flag_optionally_and_the_helper_defaults_it_to_reported():
    _assert_dto_and_helper(_src(_REPO))


def test_the_report_maps_points_through_the_same_helper():
    _assert_report_mapping(_src(_REPORT_DTOS))


@pytest.mark.parametrize("before, after", [
    ("    let cashFlowReported: Bool?\n", "    let cashFlowReported: Bool\n"),
    ('        case cashFlowReported = "cash_flow_reported"\n',
     '        case cashFlowReported = "cashflow_reported"\n'),
    ("            cashFlowReported: cashFlowReported ?? true\n",
     "            cashFlowReported: cashFlowReported ?? false\n"),
    ("            sharesOutstanding: sharesOutstanding,\n            marketCap:",
     "            sharesOutstanding: nil,\n            marketCap:"),
    # 2026-10-05: the Capital view's scale floor must ride the shared helper too.
    ("            marketCap: marketCap,\n", ""),
    ("        let points = dataPoints.map { $0.toDisplayPoint() }\n",
     "        let points = dataPoints.map {\n            SignalOfConfidenceDataPoint(\n"
     "                period: $0.period, dividendYield: $0.dividendYield,\n"
     "                buybackYield: $0.buybackYield, dividendAmount: $0.dividendAmount,\n"
     "                buybackAmount: $0.buybackAmount, sharesOutstanding: $0.sharesOutstanding\n"
     "            )\n        }\n"),
])
def test_dto_guard_rejects_each_mutation(before, after):
    _expect_guard_failure(_assert_dto_and_helper, _src(_REPO), before, after)


def test_report_mapping_guard_rejects_the_old_inline_construction():
    _expect_guard_failure(
        _assert_report_mapping, _src(_REPORT_DTOS),
        "c.dataPoints?.map { $0.toDisplayPoint() }",
        "c.dataPoints?.map { p in\n                    SignalOfConfidenceDataPoint(\n"
        "                        period: p.period, dividendYield: p.dividendYield,\n"
        "                        buybackYield: p.buybackYield, dividendAmount: p.dividendAmount,\n"
        "                        buybackAmount: p.buybackAmount, sharesOutstanding: p.sharesOutstanding\n"
        "                    )\n                }",
    )


# ── (c) the chart's label rows print "—" for an unreported quarter ────────────


def _label_re(yield_field: str, amount_field: str) -> re.Pattern:
    return re.compile(
        r'Text\(\s*dataPoint\.cashFlowReported\s*\?\s*\(\s*viewType == \.yield\s*\?\s*'
        rf'String\(format: "%\.2f%%", dataPoint\.{yield_field}\)\s*:\s*'
        rf'formatLargeNumber\(dataPoint\.{amount_field}\)\s*\)\s*:\s*"{_DASH}"\s*\)'
    )


def _assert_label_rows(chart_src: str) -> None:
    code = _strip_comments(chart_src)
    for row, y_field, a_field in (
        ("private var dividendLabels: some View", "dividendYield", "dividendAmount"),
        ("private var buybackLabels: some View", "buybackYield", "buybackAmount"),
    ):
        body = _body(code, row)
        assert _label_re(y_field, a_field).search(body), (
            f"{row}: an unreported quarter must print '{_DASH}' (false arm), the measured "
            "figure only when the flag is true"
        )
        # Exactly one rendering of each figure — a second, ungated Text would reprint the
        # placeholder.
        assert body.count(f"dataPoint.{y_field}") == 1, row
        assert body.count(f"dataPoint.{a_field}") == 1, row


def test_label_rows_print_a_dash_for_an_unreported_quarter():
    _assert_label_rows(_src(_CHART))


_DIV_GATED = (
    '                Text(dataPoint.cashFlowReported\n'
    '                     ? (viewType == .yield\n'
    '                        ? String(format: "%.2f%%", dataPoint.dividendYield)\n'
    '                        : formatLargeNumber(dataPoint.dividendAmount))\n'
    '                     : "—")\n'
)
_BB_GATED = (
    '                Text(dataPoint.cashFlowReported\n'
    '                     ? (viewType == .yield\n'
    '                        ? String(format: "%.2f%%", dataPoint.buybackYield)\n'
    '                        : formatLargeNumber(dataPoint.buybackAmount))\n'
    '                     : "—")\n'
)


@pytest.mark.parametrize("before, after", [
    # the pre-P19 dividend row: the placeholder prints as a measured 0.00%
    (_DIV_GATED,
     '                Text(viewType == .yield\n'
     '                     ? String(format: "%.2f%%", dataPoint.dividendYield)\n'
     '                     : formatLargeNumber(dataPoint.dividendAmount))\n'),
    # arms swapped on the buyback row: real quarters read "—", the gap reads $0
    (_BB_GATED,
     '                Text(!dataPoint.cashFlowReported\n'
     '                     ? (viewType == .yield\n'
     '                        ? String(format: "%.2f%%", dataPoint.buybackYield)\n'
     '                        : formatLargeNumber(dataPoint.buybackAmount))\n'
     '                     : "—")\n'),
    # the dash replaced by a zero
    (_BB_GATED, _BB_GATED.replace('"—"', '"0.00%"')),
])
def test_label_row_guard_rejects_each_mutation(before, after):
    _expect_guard_failure(_assert_label_rows, _src(_CHART), before, after)


# ── (d) the mini-chart popup ─────────────────────────────────────────────────


def _popup_re(color: str, yield_field: str, amount_field: str) -> re.Pattern:
    return re.compile(
        rf"popupMetric\(AppColors\.{color},\s*dp\.cashFlowReported\s*\?\s*\(\s*"
        rf'viewType == \.yield \? String\(format: "%\.2f%%", dp\.{yield_field}\) : '
        rf'formatMoney\(dp\.{amount_field}\)\s*\)\s*:\s*"{_DASH}"\s*\)'
    )


def _assert_popup(mini_src: str) -> None:
    popup = _body(_strip_comments(mini_src),
                  "private func popup(_ dp: SignalOfConfidenceDataPoint) -> some View")
    for color, y_field, a_field in (
        ("confidenceDividends", "dividendYield", "dividendAmount"),
        ("confidenceBuybacks", "buybackYield", "buybackAmount"),
    ):
        assert _popup_re(color, y_field, a_field).search(popup), \
            f"the popup's {color} metric prints the placeholder for an unreported quarter"
        assert popup.count(f"dp.{y_field}") == 1 and popup.count(f"dp.{a_field}") == 1
    # the share metric keeps its own "—" for an unreported count
    assert 'dp.sharesOutstanding.map(formatShares) ?? "—"' in popup


def test_popup_prints_a_dash_for_both_cash_metrics():
    _assert_popup(_src(_MINI))


@pytest.mark.parametrize("before, after", [
    # the pre-P19 dividends metric
    ('                            dp.cashFlowReported\n'
     '                                ? (viewType == .yield ? String(format: "%.2f%%", dp.dividendYield) : formatMoney(dp.dividendAmount))\n'
     '                                : "—")\n',
     '                            viewType == .yield ? String(format: "%.2f%%", dp.dividendYield) : formatMoney(dp.dividendAmount))\n'),
    # the buyback dash replaced by "$0"
    ('                                ? (viewType == .yield ? String(format: "%.2f%%", dp.buybackYield) : formatMoney(dp.buybackAmount))\n'
     '                                : "—")\n',
     '                                ? (viewType == .yield ? String(format: "%.2f%%", dp.buybackYield) : formatMoney(dp.buybackAmount))\n'
     '                                : "$0")\n'),
])
def test_popup_guard_rejects_each_mutation(before, after):
    _expect_guard_failure(_assert_popup, _src(_MINI), before, after)


# ── (e) neither chart filters its bars (column alignment) ────────────────────


def _assert_bars_unfiltered(src: str, container: str, var: str) -> None:
    body = _body(_strip_comments(src), container)
    bars = body.count("BarMark(")
    unfiltered = re.findall(rf"ForEach\(dataPoints\)\s*\{{\s*{var}\s+in\s*BarMark\(", body)
    assert bars == 2 and len(unfiltered) == bars, (
        f"{container}: every BarMark must iterate the UNFILTERED series — skipping a period "
        "reorders its category column off the index-positioned label rows"
    )
    assert "cashFlowReported" not in body, \
        f"{container}: the bars must not branch on the flag (a placeholder is a zero bar)"


def _assert_full_chart_bars(src: str) -> None:
    _assert_bars_unfiltered(src, "private var chartContent: some View", "dataPoint")


def _assert_mini_chart_bars(src: str) -> None:
    _assert_bars_unfiltered(src, "private var barMarks: some ChartContent", "dp")


def test_neither_chart_filters_its_bars():
    _assert_full_chart_bars(_src(_CHART))
    _assert_mini_chart_bars(_src(_MINI))
    # The spec's decision: no category-domain pin was added either (none is needed while
    # every period draws).
    for path in (_CHART, _MINI):
        assert not re.search(r"chartXScale\(\s*domain:", _strip_comments(_src(path))), path.name


def test_bar_guard_rejects_a_filtered_series():
    _expect_guard_failure(
        _assert_full_chart_bars, _src(_CHART),
        '            ForEach(dataPoints) { dataPoint in\n                BarMark(\n'
        '                    x: .value("Period", dataPoint.period),\n'
        '                    y: .value("Buybacks"',
        '            ForEach(dataPoints.filter { $0.cashFlowReported }) { dataPoint in\n'
        '                BarMark(\n'
        '                    x: .value("Period", dataPoint.period),\n'
        '                    y: .value("Buybacks"',
    )
    _expect_guard_failure(
        _assert_mini_chart_bars, _src(_MINI),
        '        ForEach(dataPoints) { dp in\n            BarMark(\n'
        '                x: .value("Quarter", dp.period),\n'
        '                y: .value("Dividends"',
        '        ForEach(dataPoints.filter(\\.cashFlowReported)) { dp in\n            BarMark(\n'
        '                x: .value("Quarter", dp.period),\n'
        '                y: .value("Dividends"',
    )


# ── (f) the report's newest-buyback cell ─────────────────────────────────────


def _assert_newest_buyback(report_models_src: str) -> None:
    ca = _body(_strip_comments(report_models_src), "struct ReportCapitalAllocation {")
    text = _body(ca, "var newestBuybackText: String")
    m = re.search(rf'if dataPoints\.last\?\.cashFlowReported == false \{{ return "{_DASH}" \}}', text)
    assert m, "an unreported newest quarter must read '—', not '$0'"
    assert m.start() < text.index('return "$0"'), \
        "the flag must be checked BEFORE the placeholder can reach the '$0' branch"
    color = _body(ca, "var newestBuybackColor: Color")
    c = re.search(
        r"if dataPoints\.last\?\.cashFlowReported == false \{ return AppColors\.textPrimary \}", color
    )
    assert c and c.start() < color.index("AppColors.confidenceBuybacks"), \
        "an unreported newest quarter must not be drawn in the buyback green"


def test_newest_buyback_cell_reads_a_dash_when_unreported():
    _assert_newest_buyback(_src(_REPORT_MODELS))


@pytest.mark.parametrize("before, after", [
    ('        if dataPoints.last?.cashFlowReported == false { return "—" }\n', ""),
    ('        if dataPoints.last?.cashFlowReported == false { return "—" }\n',
     '        if dataPoints.last?.cashFlowReported == true { return "—" }\n'),
    ("        if dataPoints.last?.cashFlowReported == false { return AppColors.textPrimary }\n", ""),
])
def test_newest_buyback_guard_rejects_each_mutation(before, after):
    _expect_guard_failure(_assert_newest_buyback, _src(_REPORT_MODELS), before, after)


# ── (g) the card's key for the dash ──────────────────────────────────────────


def _assert_caption_wording(caption: str) -> None:
    """What the key may say. `cashFlowReported == false` covers a filing hole AND a failed
    cash-flow fetch, so the key names NO cause; and the shares row prints its own "—" for
    an unreported share count, so the key names the two rows it explains."""
    assert caption.startswith(_DASH), f"the key explains the dash, so it opens with it: {caption!r}"
    assert "Dividends" in caption and "Buybacks" in caption, \
        f"the key must be scoped to the two cash rows (the shares row has its own dash): {caption!r}"
    assert "share" not in caption.lower(), caption
    lowered = caption.lower()
    for claim in ("filing", "filed", "on record", "report", "paid", "none", "no cash"):
        assert claim not in lowered, \
            f"the key names a cause ({claim!r}) that is false during a fetch outage: {caption!r}"
    assert "unavailable" in lowered, caption


def _assert_caption(section_src: str, models_src: str) -> None:
    card = _body(_strip_comments(section_src), "struct SignalOfConfidenceSectionCard: View {")
    body = _body(card, "var body: some View")
    gated = _body(body, "if signalData.hasUnreportedCashFlow")
    assert re.fullmatch(r"\s*if signalData\.hasUnreportedCashFlow\s*", body[
        body.index("if signalData.hasUnreportedCashFlow"):
        body.index("{", body.index("if signalData.hasUnreportedCashFlow"))
    ]), "the key must show in BOTH views — the dash prints in Yield and Capital alike"
    texts = re.findall(r'Text\("((?:[^"\\]|\\.)*)"\)', gated)
    assert len(texts) == 1, texts
    _assert_caption_wording(texts[0])
    assert texts == [_CAPTION]
    assert card.count(f'"{_CAPTION}"') == 1, "the key is printed outside its gate too"
    yield_block = _body(body, "if selectedView == .yield && !signalData.dataPoints.isEmpty")
    assert _CAPTION not in yield_block, "the key must not hide in the Yield-only caption"

    data = _body(_strip_comments(models_src), "struct SignalOfConfidenceSectionData {")
    has = _body(data, "var hasUnreportedCashFlow: Bool")
    assert re.fullmatch(r"\s*dataPoints\.contains\s*\{\s*!\$0\.cashFlowReported\s*\}\s*", has), has


def test_card_keys_the_dash_only_when_a_quarter_is_unreported():
    _assert_caption(_src(_SECTION), _src(_MODELS))


@pytest.mark.parametrize("target, before, after", [
    # the pre-fix key: a cause that is false during a cash-flow fetch outage
    ("section", f'Text("{_CAPTION}")', f'Text("{_OLD_CAPTION}")'),
    # cause-neutral but unscoped: it reads as explaining the shares row's dash too
    ("section", f'Text("{_CAPTION}")',
     'Text("— = cash-flow figures unavailable for that quarter")'),
    ("section", "            if signalData.hasUnreportedCashFlow {\n",
     "            if selectedView == .yield && signalData.hasUnreportedCashFlow {\n"),
    ("section", "            if signalData.hasUnreportedCashFlow {\n", "            if true {\n"),
    ("models", "        dataPoints.contains { !$0.cashFlowReported }\n",
     "        dataPoints.contains { $0.cashFlowReported }\n"),
])
def test_caption_guard_rejects_each_mutation(target, before, after):
    section, models = _src(_SECTION), _src(_MODELS)
    if target == "section":
        _expect_guard_failure(lambda s: _assert_caption(s, models), section, before, after)
    else:
        _expect_guard_failure(lambda s: _assert_caption(section, s), models, before, after)


def test_the_caption_constant_itself_obeys_the_wording_rule():
    """`_CAPTION` is what the exact-match pins, so a future reword that restores a cause
    must fail HERE, not only drift past an updated constant."""
    _assert_caption_wording(_CAPTION)


@pytest.mark.parametrize("caption", [
    _OLD_CAPTION,
    "— = cash-flow figures unavailable for that quarter",            # unscoped
    "— in Dividends/Buybacks = no cash-flow filing for that quarter",  # names a cause
    "— in Dividends/Buybacks = the company reported nothing",          # names a cause
    "— in Dividends/Buybacks/Shares = cash-flow figures unavailable",  # claims the shares dash
    "Dividends/Buybacks: cash-flow figures unavailable for that quarter",  # no dash
])
def test_caption_wording_rule_rejects_each_bad_key(caption):
    with pytest.raises(AssertionError):
        _assert_caption_wording(caption)


# ── (g2) a FAILED cash-flow leg is an outage, not a card of dashes ────────────


def _assert_cash_leg_helper(repo_src: str) -> None:
    response = _body(_strip_comments(repo_src),
                     "struct SignalOfConfidenceResponseDTO: Codable, FinancialsCacheable {")
    helper = _body(response, "var cashFlowLegFailed: Bool")
    assert re.fullmatch(r'\s*\(degraded \?\? \[\]\)\.contains\("cash_flow"\)\s*', helper), (
        "the outage gate is EXACTLY the server's \"cash_flow\" reason — not the filing facts "
        f"(cash_flow_row / cash_flow_statement_missing), not any failed leg: {helper!r}"
    )
    empty = _body(response, "var isEmptyPayload: Bool")
    assert re.fullmatch(r"\s*dataPoints\.isEmpty\s*", empty), (
        "isEmptyPayload keeps its meaning (no quarter at all); the outage has its own name, "
        "so the cache gate and the 'no quarters' retry log stay truthful"
    )


def test_dto_names_the_failed_cash_leg_exactly():
    _assert_cash_leg_helper(_src(_REPO))


_HELPER_EXPR = '        (degraded ?? []).contains("cash_flow")\n'


@pytest.mark.parametrize("before, after", [
    (_HELPER_EXPR, '        (degraded ?? []).contains("cash_flow_row")\n'),
    (_HELPER_EXPR, '        (degraded ?? []).contains { $0.hasPrefix("cash_flow") }\n'),
    (_HELPER_EXPR, "        !(degraded ?? []).isEmpty\n"),
    (_HELPER_EXPR, '        !(degraded ?? []).contains("cash_flow")\n'),
    # folding the outage into isEmptyPayload: the VM would retry but still draw the card
    ("    var isEmptyPayload: Bool {\n        dataPoints.isEmpty\n    }\n\n"
     "    /// The quarterly cash-flow FETCH failed",
     "    var isEmptyPayload: Bool {\n        dataPoints.isEmpty || cashFlowLegFailed\n    }\n\n"
     "    /// The quarterly cash-flow FETCH failed"),
])
def test_cash_leg_helper_guard_rejects_each_mutation(before, after):
    _expect_guard_failure(_assert_cash_leg_helper, _src(_REPO), before, after)


def _degraded_reasons_appended(py_src: str) -> set:
    """Every string literal the service passes to `degraded.append(...)`."""
    import ast

    reasons = set()
    for node in ast.walk(ast.parse(py_src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "degraded"):
            reasons.update(a.value for a in node.args
                           if isinstance(a, ast.Constant) and isinstance(a.value, str))
    return reasons


def test_the_server_still_emits_the_reason_the_ios_gate_matches():
    """Cross-contract: rename the server's "cash_flow" reason and the iOS gate would never
    fire again — silently back to a card of dashes over a fake 0% / "Low" verdict."""
    reasons = _degraded_reasons_appended(_SOC_SERVICE.read_text(encoding="utf-8"))
    assert "cash_flow" in reasons, sorted(reasons)
    # the filing facts stay DISTINCT reasons (the gate must not swallow them)
    assert {"cash_flow_row", "cash_flow_statement_missing"} <= reasons, sorted(reasons)


def _vm_fetch_body(vm_src: str) -> str:
    return _body(_strip_comments(vm_src), "private func fetchSignalOfConfidence(")


_ASSIGN_RE = re.compile(
    r"self\.signalOfConfidenceData\s*=\s*\(?\s*(?:"
    r"dto\.dataPoints\.isEmpty\s*\|\|\s*dto\.cashFlowLegFailed"
    r"|dto\.cashFlowLegFailed\s*\|\|\s*dto\.dataPoints\.isEmpty"
    r")\s*\)?\s*\?\s*nil\s*:\s*dto\.toDisplayModel\(\)"
)


def _assert_vm_treats_cash_leg_as_outage(vm_src: str) -> None:
    fn = _vm_fetch_body(vm_src)
    do_block = fn[: fn.index("catch")]
    assign = _ASSIGN_RE.search(do_block)
    assert assign, "a failed cash-flow leg must leave NO card (its data nil), like an empty payload"
    # an `if` led by the gate (not negated, not conjoined) that returns the section's failure
    for m in re.finditer(r"\bif\s+([^{]+)\{", do_block):
        cond = m.group(1).strip()
        if not re.fullmatch(r"dto\.cashFlowLegFailed(?:\s*\|\|.*)?", cond, re.S):
            continue
        body = _body(do_block[m.start():], "if")
        if re.search(r'return\s+FinancialsFailure\(section:\s*"Signal of Confidence"', body):
            assert assign.start() < m.start(), \
                "the card's data must be cleared BEFORE the early return, or a stale card stays"
            return
    raise AssertionError("a failed cash-flow leg must return the section's FinancialsFailure "
                         "(the tab's Try Again), not answer as a drawn card")


# The ViewModel shape the guard requires: proven against this rendering (and its mutants)
# and against the live TickerDetailViewModel.swift by the test below.
_VM_FIXED = '''
    private func fetchSignalOfConfidence(_ ticker: String, generation: Int) async -> FinancialsFailure? {
        do {
            let dto = try await stockRepository.getSignalOfConfidence(ticker: ticker)
            guard isCurrentFinancialsRun(generation, ticker: ticker, step: "signal of confidence") else { return nil }
            self.signalOfConfidenceData = (dto.dataPoints.isEmpty || dto.cashFlowLegFailed) ? nil : dto.toDisplayModel()
            print("✅ TickerDetailVM: Got signal of confidence for \\(ticker) — \\(dto.dataPoints.count) quarters")
            if dto.cashFlowLegFailed {
                print("⚠️ TickerDetailVM: Signal of confidence for \\(ticker) lost its cash-flow leg (\\(dto.degraded ?? [])) — offering a retry")
                return FinancialsFailure(section: "Signal of Confidence", message: nil)
            }
            let failedLegs = Self.failedDataLegs(dto.degraded)
            if dto.isEmptyPayload, !failedLegs.isEmpty {
                print("⚠️ TickerDetailVM: Signal of confidence for \\(ticker) is degraded with no quarters (\\(failedLegs)) — offering a retry")
                return FinancialsFailure(section: "Signal of Confidence", message: nil)
            }
            return nil
        } catch {
            let failure = financialsFailure("Signal of Confidence", ticker: ticker, error: error)
            guard isCurrentFinancialsRun(generation, ticker: ticker, step: "signal of confidence") else { return nil }
            self.signalOfConfidenceData = nil
            return failure
        }
    }
'''


def test_vm_guard_accepts_the_intended_edit():
    _assert_vm_treats_cash_leg_as_outage(_VM_FIXED)


_VM_GATE_IF = "            if dto.cashFlowLegFailed {\n"
_VM_ASSIGN = ("            self.signalOfConfidenceData = (dto.dataPoints.isEmpty || "
              "dto.cashFlowLegFailed) ? nil : dto.toDisplayModel()\n")


@pytest.mark.parametrize("before, after", [
    # today's code: the card is drawn whenever any quarter arrived
    (_VM_ASSIGN, "            self.signalOfConfidenceData = dto.dataPoints.isEmpty ? nil : dto.toDisplayModel()\n"),
    # the gate negated / conjoined with the (false) empty-payload test
    (_VM_GATE_IF, "            if !dto.cashFlowLegFailed {\n"),
    (_VM_GATE_IF, "            if dto.isEmptyPayload, dto.cashFlowLegFailed {\n"),
    (_VM_GATE_IF, "            if dto.cashFlowLegFailed && dto.isEmptyPayload {\n"),
    # the gate fires but answers "nothing failed"
    ("lost its cash-flow leg (\\(dto.degraded ?? [])) — offering a retry\")\n"
     "                return FinancialsFailure(section: \"Signal of Confidence\", message: nil)\n",
     "lost its cash-flow leg (\\(dto.degraded ?? [])) — offering a retry\")\n"
     "                return nil\n"),
])
def test_vm_guard_rejects_each_mutation(before, after):
    _expect_guard_failure(_assert_vm_treats_cash_leg_as_outage, _VM_FIXED, before, after)


def test_vm_guard_rejects_the_early_return_before_the_card_is_cleared():
    gate = (
        _VM_GATE_IF
        + "                print(\"⚠️ TickerDetailVM: Signal of confidence for \\(ticker) lost its "
          "cash-flow leg (\\(dto.degraded ?? [])) — offering a retry\")\n"
        + "                return FinancialsFailure(section: \"Signal of Confidence\", message: nil)\n"
        + "            }\n"
    )
    assert _VM_FIXED.count(gate) == 1 and _VM_FIXED.count(_VM_ASSIGN) == 1
    moved = _VM_FIXED.replace(gate, "").replace(_VM_ASSIGN, gate + _VM_ASSIGN)
    with pytest.raises(AssertionError):
        _assert_vm_treats_cash_leg_as_outage(moved)


def test_the_live_view_model_treats_a_failed_cash_leg_as_an_outage():
    _assert_vm_treats_cash_leg_as_outage(_src(_VM))


# ── (h) an interior-gap #Preview on every touched view ───────────────────────


def _sample_periods(models_code: str) -> List[str]:
    """Periods of `sampleData`, read from its PAREN-balanced initializer (it holds no brace)."""
    sig = "static let sampleData = SignalOfConfidenceSectionData("
    start = models_code.index(sig) + len(sig) - 1
    depth = 0
    for k in range(start, len(models_code)):
        if models_code[k] == "(":
            depth += 1
        elif models_code[k] == ")":
            depth -= 1
            if depth == 0:
                return re.findall(r'period: "([^"]+)"', models_code[start:k])
    raise AssertionError("unbalanced parentheses in sampleData")


def test_the_interior_gap_preview_sample_is_honest_and_interior():
    code = _strip_comments(_src(_MODELS))
    gap = _body(code, "static var sampleInteriorCashFlowGap: SignalOfConfidenceSectionData")
    target = re.search(r'guard point\.period == "([^"]+)" else \{ return point \}', gap)
    assert target, "the sample must flag exactly one named quarter"
    periods = _sample_periods(code)
    assert len(periods) >= 3 and target.group(1) in periods[1:-1], \
        f"{target.group(1)!r} is not an INTERIOR quarter of {periods!r}"
    assert "cashFlowReported: false" in gap
    for field in ("dividendYield", "buybackYield", "dividendAmount", "buybackAmount"):
        assert re.search(rf"\b{field}: 0\b", gap), f"{field} must be the server's 0.0 placeholder"
    assert "sharesOutstanding: point.sharesOutstanding" in gap, \
        "the share count is real for a cash-flow gap — it stays on the line"


@pytest.mark.parametrize("path", [_CHART, _MINI, _SECTION])
def test_every_touched_view_previews_the_interior_gap(path):
    code = _strip_comments(_src(path))
    previews = code[code.index("#Preview"):]
    assert "SignalOfConfidenceSectionData.sampleInteriorCashFlowGap" in previews, path.name


# ── Python ports: the degraded behaviour on outlier series ───────────────────


def _decode_flag(wire_point: Dict) -> bool:
    """Port of `SignalOfConfidenceDataPointDTO.toDisplayPoint()`'s `cashFlowReported ?? true`
    (`Bool?` decodes an absent key AND a JSON null as nil)."""
    value = wire_point.get("cash_flow_reported")
    return True if value is None else bool(value)


def _money(millions: float) -> str:
    """Port of `SignalOfConfidenceFormat.money(millions:)` — kept equal to the port in
    test_soc_deepcheck_ios.py (zero reads "$0"; under $10M keeps a decimal, 2026-10-05)."""
    if not math.isfinite(millions):
        return "—"
    sign = "-" if millions < 0 else ""
    m = abs(millions)
    if m == 0:
        return "$0"
    if m < 0.05:
        return f"{sign}<$0.1M"
    if m < 9.95:
        return f"{sign}${m:.1f}M"
    if m >= 1_000_000 or round(m / 1_000) >= 1_000:
        return f"{sign}${m / 1_000_000:.1f}T"
    if m >= 10_000:
        return f"{sign}${m / 1_000:.0f}B"
    if m >= 1_000 or round(m) >= 1_000:
        return f"{sign}${m / 1_000:.1f}B"
    return f"{sign}${m:.0f}M"


def _label_row(points: List[Dict], side: str, view: str) -> List[str]:
    """Port of the chart's dividendLabels / buybackLabels (and the popup, same rule)."""
    out = []
    for p in points:
        if not _decode_flag(p):
            out.append(_DASH)
        elif view == "yield":
            out.append(f"{p[f'{side}_yield']:.2f}%")
        else:
            out.append(_money(p[f"{side}_amount"]))
    return out


def _newest_buyback_text(points: List[Dict]) -> str:
    """Port of `ReportCapitalAllocation.newestBuybackText`."""
    if points and not _decode_flag(points[-1]):
        return _DASH
    amt = points[-1]["buyback_amount"] if points else None
    if amt is None or not math.isfinite(amt) or amt <= 0:
        return "$0"
    return _money(amt)


def _has_unreported(points: List[Dict]) -> bool:
    """Port of `SignalOfConfidenceSectionData.hasUnreportedCashFlow`."""
    return any(not _decode_flag(p) for p in points)


def _pt(period: str, dy: float, by: float, da: float, ba: float,
        reported: Optional[bool] = True, shares: Optional[float] = 1000.0) -> Dict:
    p = {"period": period, "dividend_yield": dy, "buyback_yield": by,
         "dividend_amount": da, "buyback_amount": ba, "shares_outstanding": shares}
    if reported is not None:
        p["cash_flow_reported"] = reported
    return p


def _placeholder(period: str, shares: Optional[float] = 4300.0) -> Dict:
    return _pt(period, 0.0, 0.0, 0.0, 0.0, reported=False, shares=shares)


@pytest.mark.parametrize("wire, expected", [
    ({}, True),                                  # older backend / cached DTO / stored report
    ({"cash_flow_reported": None}, True),        # explicit null decodes as nil
    ({"cash_flow_reported": True}, True),
    ({"cash_flow_reported": False}, False),
])
def test_port_flag_decode(wire, expected):
    assert _decode_flag(wire) is expected


def test_port_interior_gap_reads_a_dash_and_keeps_every_column():
    pts = [_pt("Q3 '24", 2.0, 4.0, 500, 1000), _placeholder("Q4 '24"),
           _pt("Q1 '25", 2.0, 4.0, 500, 1000)]
    assert _label_row(pts, "dividend", "yield") == ["2.00%", _DASH, "2.00%"]
    assert _label_row(pts, "buyback", "capital") == ["$1.0B", _DASH, "$1.0B"]
    # one cell per column: the row never shortens, so index positioning stays aligned
    assert len(_label_row(pts, "buyback", "yield")) == len(pts)
    assert _has_unreported(pts)
    assert _newest_buyback_text(pts) == "$1.0B"


def test_port_leading_edge_gap():
    pts = [_placeholder("Q1 '24"), _placeholder("Q2 '24"), _pt("Q3 '24", 1.0, 0.5, 250, 125)]
    assert _label_row(pts, "dividend", "capital") == [_DASH, _DASH, "$250M"]
    assert _has_unreported(pts)


def test_port_every_quarter_unreported():
    pts = [_placeholder(f"Q{i} '25") for i in range(1, 5)]
    assert _label_row(pts, "dividend", "yield") == [_DASH] * 4
    assert _label_row(pts, "buyback", "capital") == [_DASH] * 4
    assert _newest_buyback_text(pts) == _DASH


def test_port_a_measured_zero_is_not_a_dash():
    """A non-payer's REAL zero (row on file, nothing returned) keeps its figures: the dash
    means 'no filing', never 'paid nothing'."""
    pts = [_pt("Q1 '25", 0.0, 0.0, 0.0, 0.0), _pt("Q2 '25", 0.0, 0.0, 0.0, 0.0)]
    assert _label_row(pts, "dividend", "yield") == ["0.00%", "0.00%"]
    # "$0", never "$0M" (TestFlight 1.0 (11), CRWV: every cell of a non-returner read "$0M")
    assert _label_row(pts, "buyback", "capital") == ["$0", "$0"]
    assert not _has_unreported(pts)
    assert _newest_buyback_text(pts) == "$0"


@pytest.mark.parametrize("amt, text", [
    (0.03, "<$0.1M"),     # review 2026-10-07: the header read "$0.0M" — a real payment as zero
    (0.3, "$0.3M"), (0.96, "$1.0M"), (2.59, "$2.6M"), (12.3, "$12M"), (1_499, "$1.5B"),
])
def test_port_newest_buyback_header_matches_the_charts(amt, text):
    pts = [_pt("Q1 '25", 0.0, 0.0, 0.0, 0.0), _pt("Q2 '25", 0.0, 0.01, 0.0, amt)]
    assert _newest_buyback_text(pts) == text
    assert _newest_buyback_text(pts) == _label_row(pts, "buyback", "capital")[-1], \
        "the report header and the chart cells print one figure for one quarter"


def test_newest_buyback_header_delegates_every_amount_to_the_shared_format():
    ca = _body(_strip_comments(_src(_REPORT_MODELS)), "struct ReportCapitalAllocation {")
    text = _body(ca, "var newestBuybackText: String")
    assert "String(format:" not in text, "the header hand-rolls a format again ('$0.0M')"
    assert re.search(r"return SignalOfConfidenceFormat\.money\(millions: amt\)\s*$", text.strip()), text


def test_port_the_flag_wins_over_a_malformed_non_zero_placeholder():
    """Malformed upstream: flagged unreported but carrying non-zero cash. The server said it
    is not a measurement, so it is never printed as one."""
    bad = _pt("Q4 '24", 3.3, 7.7, 900, 1800, reported=False)
    assert _label_row([bad], "dividend", "yield") == [_DASH]
    assert _label_row([bad], "buyback", "capital") == [_DASH]
    assert _newest_buyback_text([_pt("Q3 '24", 1, 1, 1, 1), bad]) == _DASH


def test_port_a_payload_without_the_key_renders_exactly_as_before():
    pts = [_pt("Q1 '25", 1.25, 0.5, 300, 120, reported=None),
           _pt("Q2 '25", 0.0, 0.0, 0.0, 0.0, reported=None)]
    assert _label_row(pts, "dividend", "yield") == ["1.25%", "0.00%"]
    assert not _has_unreported(pts)
    assert _newest_buyback_text(pts) == "$0"


def test_port_unreported_cash_with_an_unreported_share_count():
    """Both gaps on one quarter (weightedAverageShsOut 0 → shares nil, no cash-flow row):
    every cell for that column is a dash, and nothing raises."""
    pts = [_pt("Q1 '25", 1.0, 1.0, 100, 100), _placeholder("Q2 '25", shares=None),
           _pt("Q3 '25", 1.0, 1.0, 100, 100)]
    assert _label_row(pts, "dividend", "yield")[1] == _DASH
    assert _label_row(pts, "buyback", "yield")[1] == _DASH
    assert pts[1]["shares_outstanding"] is None


def test_port_empty_series():
    assert _label_row([], "dividend", "yield") == []
    assert not _has_unreported([])
    assert _newest_buyback_text([]) == "$0"


# Port of TickerDetailViewModel.nonDataLegReasons: reasons that describe the FILING, not an
# outage (a retry returns the same answer).
_FILING_FACTS = {"profile", "benchmarks", "no_metrics", "cash_flow_row", "cash_flow_statement_missing"}


def _cash_leg_failed(degraded: Optional[List[str]]) -> bool:
    """Port of `SignalOfConfidenceResponseDTO.cashFlowLegFailed`."""
    return "cash_flow" in (degraded or [])


def _financials_outcome(points: List[Dict], degraded: Optional[List[str]]) -> tuple:
    """(card drawn, Try Again offered) — the decision `fetchSignalOfConfidence` makes with
    the gate wired (`_VM_FIXED`)."""
    leg = _cash_leg_failed(degraded)
    failed_legs = [r for r in (degraded or []) if r not in _FILING_FACTS]
    card = bool(points) and not leg
    retry = leg or (not points and bool(failed_legs))
    return card, retry


@pytest.mark.parametrize("degraded, expected", [
    (None, False),                                   # older backend: no key
    ([], False),
    (["cash_flow"], True),
    (["income", "cash_flow"], True),
    (["cash_flow_row"], False),                      # a filing fact — retry won't change it
    (["cash_flow_statement_missing"], False),
    (["market_cap"], False),                         # still a valid chart
    (["annual_ratios"], False),
    (["CASH_FLOW"], False),                          # exact, case-sensitive like the Swift
    (["cash_flow "], False),
])
def test_port_cash_leg_gate_matches_exactly_the_fetch_failure(degraded, expected):
    assert _cash_leg_failed(degraded) is expected


def test_port_a_fetch_outage_is_a_retry_not_a_card_of_dashes():
    """The finding: every point flagged (the leg raised → []), points still non-empty. The
    old decision drew a card of dashes over a fabricated 0% / "Low" summary, with no retry."""
    pts = [_placeholder(f"Q{i} '25") for i in range(1, 5)]
    assert _label_row(pts, "buyback", "capital") == [_DASH] * 4      # what the card WOULD show
    assert _financials_outcome(pts, ["cash_flow"]) == (False, True)
    assert _financials_outcome(pts, ["cash_flow", "market_cap"]) == (False, True)


@pytest.mark.parametrize("degraded", [["market_cap"], ["annual_ratios"], ["profile"],
                                      ["cash_flow_row"], ["cash_flow_statement_missing"], None])
def test_port_other_reasons_keep_the_card(degraded):
    pts = [_pt("Q1 '25", 1.0, 1.0, 100, 100), _placeholder("Q2 '25"),
           _pt("Q3 '25", 1.0, 1.0, 100, 100)]
    assert _financials_outcome(pts, degraded) == (True, False)


def test_port_an_empty_build_keeps_its_existing_retry_rule():
    assert _financials_outcome([], ["income"]) == (False, True)
    assert _financials_outcome([], ["cash_flow_statement_missing"]) == (False, False)
    assert _financials_outcome([], None) == (False, False)
