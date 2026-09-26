"""iOS side of the Caydex Fair Value Estimate — source-scan guards (no XCTest target exists).

Pins, in the Swift that ships:
  * the wire contract: every DcfFairValueResponse field has a CodingKey with the same snake name,
    everything but symbol/status is Optional, and both carrier DTOs decode the block;
  * the wording rules (documents/research/dcf-methodology-v1.md §5): the title says "Estimate",
    the subtitle says "not a price target", the gap is "Price N% below/above the estimate", and
    no verdict word (Undervalued / Overvalued / Fairly valued / Cheap / Bargain) appears;
  * a value is never shown without its range, and the RANGE is the headline (owner,
    2026-09-26: "not only the exact price") — row, sheet and chart;
  * on the Analysis tab the Caydex row REPLACES FMP's row — never both — with the
    price-vs-range chart under it, fed from data the screen already fetched;
  * the report's "Valuation & Institutions" card leads with it, says its price is the
    report-time price, and draws NO analyst half (heading, Buy/Hold/Sell, targets, Momentum);
    an analyst-era insight is hidden;
  * the chart's pole is the estimate's range in neutral ink;
  * the chat context quotes the published value and no longer names FMP.
Comment-stripped and brace-bounded (.claude/rules/testing.md §3); mutation-tested by hand.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.schemas.dcf_fair_value import DcfFairValueResponse

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_MODELS = _IOS / "Models" / "FairValueModels.swift"
_ROW = _IOS / "Views" / "Molecules" / "CaydexFairValueRow.swift"
_CHART = _IOS / "Views" / "Molecules" / "CaydexFairValueRangeChart.swift"
_REPORT_MODELS = _IOS / "Models" / "TickerReportModels.swift"
_TD_VIEW = _IOS / "Views" / "Screens" / "TickerDetailView.swift"
_SHEET = _IOS / "Views" / "Organisms" / "CaydexFairValueSheet.swift"
_SECTION = _IOS / "Views" / "Organisms" / "ValuationMeterSection.swift"
_BAR = _IOS / "Views" / "Molecules" / "ReportConsensusBar.swift"
_SNAP_DTO = _IOS / "Models" / "StockOverviewResponseModels.swift"
_REPORT_DTO = _IOS / "Models" / "TickerReportResponse.swift"
_VM = _IOS / "ViewModels" / "TickerDetailViewModel.swift"

_VERDICTS = re.compile(r"undervalued|overvalued|underpriced|overpriced|fairly valued|\bcheap\b|"
                       r"bargain|\bbuy\b|\bsell\b|\bupside\b|\bdownside\b|price target:", re.I)
# The disclaimers that deliberately mention buying and selling.
_ALLOWED = ("recommendation to buy or sell", "not a recommendation")


def _verdict_free(text: str) -> bool:
    for phrase in _ALLOWED:
        text = text.replace(phrase, "")
    return not _VERDICTS.search(text)


def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"(?<!:)//.*$", "", line) for line in src.splitlines())


def _code(path: Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip(path.read_text(encoding="utf-8"))


def _decl(src: str, prefix: str) -> str:
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


def _strings(src: str) -> list[str]:
    return re.findall(r'"((?:[^"\\]|\\.)*)"', src)


# ── wire contract ────────────────────────────────────────────────────────────────────────────

def test_every_backend_field_has_a_matching_coding_key():
    dto = _decl(_code(_MODELS), "struct CaydexFairValueDTO")
    keys = _decl(dto, "enum CodingKeys")
    explicit = dict(re.findall(r"case (\w+) = \"(\w+)\"", keys))
    bare = set()
    for line in re.findall(r"case ([\w, ]+)$", keys, flags=re.M):
        bare |= {w.strip() for w in line.split(",") if "=" not in w and w.strip()}
    wire = set(explicit.values()) | bare
    fields = set(DcfFairValueResponse.model_fields)
    assert fields == wire, f"missing on iOS: {fields - wire}; unknown on iOS: {wire - fields}"


def test_only_symbol_and_status_are_required_on_ios():
    dto = _decl(_code(_MODELS), "struct CaydexFairValueDTO")
    lets = dict(re.findall(r"let (\w+):\s*([^\n]+)", dto))
    required = {k for k, t in lets.items() if not t.strip().endswith("?")}
    assert required == {"symbol", "status"}, required


def test_both_carrier_dtos_decode_the_block():
    snap = _decl(_code(_SNAP_DTO), "struct SnapshotItemDTO")
    assert re.search(r"let caydexEstimate:\s*CaydexFairValueDTO\?", snap)
    assert 'case caydexEstimate = "caydex_estimate"' in snap
    report = _decl(_code(_REPORT_DTO), "struct WallStreetConsensusDTO")
    assert re.search(r"let caydexFairValue:\s*CaydexFairValueDTO\?", report)
    assert 'case caydexFairValue = "caydex_fair_value"' in report


def test_an_unknown_status_or_an_incoherent_estimate_is_no_row():
    model = _decl(_code(_MODELS), "struct CaydexFairValue:")
    init = _decl(model, "init?(dto: CaydexFairValueDTO)")
    assert 'case "ok":' in init and 'case "refused":' in init
    assert re.search(r"default:\s*return nil", init)
    assert "lo <= v, v <= hi" in init, "a value outside its own range is not shown"


# ── wording ──────────────────────────────────────────────────────────────────────────────────

def test_title_subtitle_and_gap_wording():
    model = _decl(_code(_MODELS), "struct CaydexFairValue:")
    assert 'static let title = "Caydex Fair Value Estimate"' in model
    assert 'static let subtitle = "DCF model estimate · not a price target · not a recommendation"' in model
    gap = _decl(model, "func formattedGap(versus price: Double?)")
    assert '"Price in line with the estimate"' in gap
    assert 'return "Price \\(magnitude)% \\(gap < 0 ? "below" : "above") the estimate"' in gap
    assert "(price / value - 1) * 100" in _decl(model, "func priceGapPercent(versus price: Double?)")


def test_no_verdict_word_in_any_fair_value_string():
    for path in (_MODELS, _ROW, _SHEET, _CHART):
        for s in _strings(_code(path)):
            assert _verdict_free(s), f"{path.name}: {s!r}"
    info = _code(_IOS / "Views" / "Molecules" / "ValuationInfoSheet.swift")
    for s in _strings(_decl(info, "private var caydexSection: some View")):
        assert _verdict_free(s), s


def _estimate_arm(block: str) -> str:
    assert block.count("case .estimate:") == 1, "one estimate arm, so nothing renders outside it"
    return block[block.index("case .estimate:"):block.index("case .refused")]


def test_the_row_is_range_first_and_never_a_bare_value():
    row = _decl(_code(_ROW), "struct CaydexFairValueRow")
    arm = _estimate_arm(_decl(row, "private var content: some View"))
    order = [arm.index(t) for t in ("CaydexFairValue.rangeLabel", "estimate.formattedRangeBounds",
                                    "estimate.formattedEstimate", "CaydexFairValue.subtitle")]
    assert order == sorted(order), "range label → range → estimate → subtitle"
    assert re.search(r"estimate\.formattedRangeBounds[^\n]*\n\s*\.font\(AppTypography\.dataTitle\)", arm), \
        "the range is the headline type"
    assert "rangeAccessibilityLabel" in arm, "VoiceOver must hear 'from … to …', not a dash"
    assert "formattedValue" not in row, "a bare value is back in the row"
    assert "CaydexFairValueSheet(" in row, "the assumptions must be one tap away"


def test_the_sheet_summary_is_range_first_too():
    card = _decl(_decl(_code(_SHEET), "struct CaydexFairValueSheet"), "private var summaryCard: some View")
    arm = _estimate_arm(card)
    assert arm.index("estimate.formattedRangeBounds") < arm.index("estimate.formattedEstimate")
    assert "formattedValue" not in card


def test_the_model_exposes_the_range_as_the_headline():
    model = _decl(_code(_MODELS), "struct CaydexFairValue:")
    bounds = _decl(model, "var bounds: (low: Double, value: Double, high: Double)?")
    assert "guard case let .estimate(v, lo, hi) = state else { return nil }" in bounds
    assert "return (lo, v, hi)" in bounds
    assert '"\\(Self.money(b.low)) – \\(Self.money(b.high))"' in _decl(model, "var formattedRangeBounds: String?")
    assert '"Estimate \\(Self.money($0))"' in _decl(model, "var formattedEstimate: String?")
    assert 'static let rangeLabel = "Estimate range"' in model
    assert 'static let notInReport = "No Caydex Fair Value Estimate is available for this report."' in model


def test_the_sheet_carries_the_disclaimer_and_the_methodology():
    sheet = _code(_SHEET)
    assert "not a price target and not a recommendation to buy or sell" in sheet
    assert "same for every reader" in sheet
    assert "Stock-based pay is counted as a cost" in sheet   # matches spec §1.2


# ── placement ────────────────────────────────────────────────────────────────────────────────

def test_on_the_analysis_tab_the_caydex_row_replaces_fmps_row():
    section = _decl(_code(_SECTION), "struct ValuationMeterSection")
    body = _decl(section, "var body: some View")
    assert re.search(
        r"if let estimate = snapshot\.caydexEstimate \{.*?CaydexFairValueRow\(estimate: estimate, "
        r"currentPrice: currentPrice\).*?\} else if let dcf = snapshot\.dcf \{\s*dcfRow\(dcf\)",
        body, flags=re.S)


def test_the_report_card_leads_with_the_estimate_and_has_no_analyst_half():
    bar = _decl(_code(_BAR), "struct ReportConsensusBar")
    body = _decl(bar, "var body: some View")
    assert re.search(r"if let estimate = consensus\.caydexFairValue \{\s*CaydexFairValueRow\("
                     r"estimate: estimate, currentPrice: consensus\.currentPrice,\s*"
                     r'priceContext: "at report time"\)', body)
    assert re.search(r"\} else \{\s*Text\(CaydexFairValue\.notInReport\)", body)
    at = body.index("CaydexFairValueRangeChart(")
    call = body[at:body.index(")", body.index("priceLegend", at)) + 1]
    assert "estimate: consensus.caydexFairValue" in call and 'priceLegend: "Price at report time"' in call
    order = [body.index(t) for t in ("CaydexFairValueRow(", "CaydexFairValueRangeChart(",
                                     "hedgeFundsSection", "insightSection")]
    assert order == sorted(order), "estimate → chart → Institutions → insight"
    for banned in ("analystPriceTargetHeader", "Analyst Price Target", "No analyst price targets",
                   "consensusDistributionSection", "analystTargetLine", "momentumSection",
                   "consensus.rating", "analystLevels", "hasAnalyst", "TargetPercent",
                   "consensus.targetPrice", "consensus.lowTarget", "consensus.highTarget",
                   "targetPole", "targetBadges", "AppColors.bullish", "AppColors.bearish"):
        assert banned not in bar, f"the analyst half is back on the report card ({banned})"


def test_the_insight_shows_only_beside_the_estimate_it_was_written_for():
    """Every older insight was written for a card this section no longer draws: analyst era
    ("Buy-rated with a $190 target…") and FMP-DCF era ("…our model, which suggests the stock is
    overpriced", under "No Caydex Fair Value Estimate is available" — seen on the simulator)."""
    bar = _decl(_code(_BAR), "struct ReportConsensusBar")
    insight = _decl(bar, "private var insightSection: some View")
    assert re.search(r"if consensus\.caydexFairValue != nil,\s*"
                     r"!consensus\.insightWasWrittenForAnalystCard,\s*"
                     r"let insight = consensus\.wallStreetInsight", insight)
    model = _decl(_code(_REPORT_MODELS), "struct ReportWallStreetConsensus")
    pred = _decl(model, "var insightWasWrittenForAnalystCard: Bool")
    # Mirrors the backend predicate that chose the insight's prompt, field for field.
    from app.services.agents.narrative_prompts import wall_street_has_analyst_coverage
    for swift, wire in (("targetPrice", "target_price"), ("analystStrongBuy", "analyst_strong_buy"),
                        ("analystBuy", "analyst_buy"), ("analystHold", "analyst_hold"),
                        ("analystSell", "analyst_sell"), ("analystStrongSell", "analyst_strong_sell"),
                        ("momentumUpgrades", "momentum_upgrades"),
                        ("momentumMaintains", "momentum_maintains"),
                        ("momentumDowngrades", "momentum_downgrades")):
        assert swift in pred, swift
        assert wall_street_has_analyst_coverage({wire: 3}), wire
    assert not wall_street_has_analyst_coverage({"target_price": None, "analyst_buy": 0})


def test_the_chart_pole_is_the_estimate_range_in_neutral_ink():
    chart = _decl(_code(_CHART), "struct CaydexFairValueRangeChart")
    assert "private var bounds: (low: Double, value: Double, high: Double)? { estimate?.bounds }" in chart
    for bad in ("consensus.", "targetPrice", "lowTarget", "highTarget", "%+"):
        assert bad not in chart, bad
    assert not re.search(r"AppColors\.(bullish|bearish|gain\w*|loss\w*|caution\w*|neutral)\b", chart), \
        "the estimate is a model value, not a good or bad sign"
    plot = _decl(chart, "private var plot: some View")
    assert re.search(r"if let b = bounds \{\s*rangePole\(b,", plot)
    rows = _decl(chart, "private func columnLabels(in geometry: GeometryProxy)")
    assert "if let b = bounds {" in rows, "the range's labels come from the estimate's own bounds"
    for label in ('"High"', '"Estimate"', '"Low"'):
        assert label in rows
    pole = _decl(chart, "private func rangePole(")
    assert "max(lowY - highY, 2)" in pole, "the pole runs exactly from low to high"
    # The range is drawn at the right edge only: no accent ink outside the pole and its swatch
    # (a band across the plot would read as "a fair value over the past two years").
    outside = chart.replace(pole, "").replace(_decl(chart, "private var rangeSwatch: some View"), "")
    assert "accentGraphic" not in outside


def test_the_chart_sanitises_its_inputs():
    chart = _decl(_code(_CHART), "struct CaydexFairValueRangeChart")
    assert "p.isFinite, p > 0" in _decl(chart, "private var validPrice: Double?")
    series = _decl(chart, "private var series: [Double]")
    assert "$0.isFinite && $0 > 0" in series
    assert "lastRaw.isFinite, lastRaw > 0" in series, "pin only when the last close survived"
    assert "series.count >= 2" in _decl(chart, "private var showsLine: Bool")
    legend = _decl(chart, "private var legendEntries: some View")
    assert "if validPrice != nil {" in legend, "no price legend without a real price"
    rows = _decl(chart, "private func columnLabels(in geometry: GeometryProxy)")
    assert "if let p = validPrice {" in rows, "no 'Price $0.00' label"


def test_the_detail_series_is_derived_from_data_already_fetched():
    vm = _code(_VM)
    assert re.search(r"var earningsData: EarningsData\? \{\s*didSet \{ recomputeValuationPriceHistory\(\) \}", vm)
    assert re.search(r"var holdersData: HoldersData\? \{\s*didSet \{ recomputeValuationPriceHistory\(\) \}", vm)
    fn = _decl(vm, "private func recomputeValuationPriceHistory()")
    assert "earningsData?.dailyPriceHistory" in fn and "holdersData?.hedgeFundsData.dailyPrices" in fn
    assert "row.price.isFinite && row.price > 0" in fn
    assert "fromEarnings.count >= 2 ? fromEarnings : fromHolders" in fn, "filter first, then fall back"
    for banned in ("fetchChartData", "chartPricePoints", "chartRequestGen", "Repository", "apiClient"):
        assert banned not in fn, f"the valuation chart must not fetch or reuse the header chart ({banned})"


def test_on_the_analysis_tab_the_chart_sits_under_the_caydex_row_only():
    section = _decl(_code(_SECTION), "struct ValuationMeterSection")
    body = _decl(section, "var body: some View")
    split = body.index("} else if let dcf = snapshot.dcf {")
    caydex = body[body.index("if let estimate = snapshot.caydexEstimate {"):split]
    assert re.search(r"if estimate\.isEstimate && priceHistory\.count >= 2 \{\s*CaydexFairValueRangeChart\("
                     r"prices: priceHistory, currentPrice: currentPrice,\s*estimate: estimate", caydex)
    assert "chartPlaceholder" in caydex, "reserve the chart's space while its closes load"
    assert "CaydexFairValueRangeChart" not in body[split:], "never under FMP's row"
    td = _code(_TD_VIEW)
    assert "valuationPriceHistory: viewModel.valuationPriceHistory" in td
    assert "isValuationPriceHistoryLoading: !viewModel.isFinancialsLoaded" in td


def test_the_valuation_info_sheet_describes_the_model_actually_shown():
    section = _decl(_code(_SECTION), "struct ValuationMeterSection")
    assert re.search(r"ValuationInfoSheet\(showsCaydexEstimate: snapshot\.caydexEstimate != nil,\s*"
                     r"showsFmpDcf: snapshot\.dcf != nil\)", section)
    sheet = _code(_IOS / "Views" / "Molecules" / "ValuationInfoSheet.swift")
    body = _decl(sheet, "var body: some View")
    assert re.search(r"if showsCaydexEstimate \{\s*caydexSection\s*\} else if showsFmpDcf \{\s*dcfSection", body)
    caydex = _decl(sheet, "private var caydexSection: some View")
    assert "not a price target and not a recommendation" in caydex
    assert "past free cash flow" not in caydex


def test_no_verdict_word_on_the_report_card_or_in_the_chat_branch():
    # The whole live card (brace-bound, so its #Preview mocks do not count) and the chart.
    bar = _decl(_code(_BAR), "struct ReportConsensusBar")
    vm = _code(_VM)
    chat = vm[vm.index("if let estimate = snapshot.caydexEstimate {"):vm.index("} else if let dcf = snapshot.dcf {")]
    for src in (bar, _code(_CHART), chat):
        for s_ in _strings(src):
            assert _verdict_free(s_), s_
    assert "not a recommendation" in chat


def test_the_explanation_names_every_live_input():
    sheet = _code(_SHEET)
    expl = sheet[sheet.index("static let explanation"):]
    for phrase in ("analysts' consensus", "Stock-based pay is counted as a cost", "one point higher or lower",
                   "share count each quarter", "beta", "interest rates", "three months",
                   "does not change the estimate itself", "get no estimate"):
        assert phrase in expl, phrase
    assert "Most of the value" not in expl


def test_the_chat_context_quotes_the_published_value_and_names_no_vendor():
    vm = _code(_VM)
    assert "if let estimate = snapshot.caydexEstimate {" in vm
    assert "FMP discounted cash flow" not in vm
