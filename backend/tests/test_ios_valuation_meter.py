"""Valuation Meter + DCF row on the Analysis tab (TestFlight E9, 2026-09-17).

FMP's `discounted-cash-flow` is a mechanical intrinsic-value estimate: −59% on AAPL and
−87% on TER the day this shipped. So the card must (a) label it as a MODEL value, never
"fair value"; (b) compute the gap against the LIVE price, never a wire field (the snapshot
is cached 24h); (c) add a caveat past ±50%; (d) send NO value for a negative model and
explain why; (e) decode everything Optional so shipped builds and the other four snapshot
categories keep decoding. The gauge reuses the Technical Meter's drawing so the two cards
match, and the rating is the Overview's own valuation rating so the two tabs agree.

Comment-stripped, brace-bounded (`.claude/rules/testing.md` §3).
"""
from __future__ import annotations

import re
from pathlib import Path

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_MODELS = _IOS / "Models" / "TickerDetailModels.swift"
_DTO = _IOS / "Models" / "StockOverviewResponseModels.swift"
_METER = _IOS / "Views" / "Molecules" / "ValuationMeter.swift"
_SECTION = _IOS / "Views" / "Organisms" / "ValuationMeterSection.swift"
_TECH_METER = _IOS / "Views" / "Molecules" / "TechnicalMeter.swift"
_VM = _IOS / "ViewModels" / "TickerDetailViewModel.swift"
_FINANCIALS = _IOS / "Views" / "Organisms" / "TickerFinancialsContent.swift"
_DETAIL_VIEW = _IOS / "Views" / "Screens" / "TickerDetailView.swift"


def _strip(src: str) -> str:
    """Block, full-line AND trailing `//` comments — a trailing comment on a code line
    must not satisfy an `in` assertion."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


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


# ── decode ───────────────────────────────────────────────────────────────────

def test_the_dcf_decodes_optional_end_to_end():
    dto = _decl(_code(_DTO), "struct DcfEstimateDTO")
    assert re.search(r"let value:\s*Double\?", dto) and re.search(r"let asOf:\s*String\?", dto)
    assert 'case asOf = "as_of"' in dto
    snap = _decl(_code(_DTO), "struct SnapshotItemDTO")
    assert re.search(r"let dcf:\s*DcfEstimateDTO\?", snap), snap
    assert "case category, rating, metrics, dcf" in snap
    model = _decl(_code(_MODELS), "struct SnapshotItem: Identifiable")
    assert re.search(r"var dcf:\s*DcfEstimate\?\s*=\s*nil", model)


def test_an_unrecognised_dcf_status_is_no_row_not_a_blank_one():
    """A future backend status must not draw an orphan divider — the DTO init returns nil."""
    model = _decl(_code(_MODELS), "init?(dto: DcfEstimateDTO)")
    assert re.search(
        r"guard let status = Status\(rawValue: dto\.status\), status != \.unavailable else \{ return nil \}",
        model,
    ), model
    assert "?? .unavailable" not in model, "an unknown status was being laundered into a blank row"


def test_the_gap_is_computed_from_the_live_price_not_a_wire_field():
    dto = _decl(_code(_DTO), "struct DcfEstimateDTO")
    assert "gap" not in dto.lower(), "a stored gap would rot against the live price"
    model = _decl(_code(_MODELS), "struct DcfEstimate: Equatable")
    gap = _decl(model, "func gapPercent(versus price: Double?)")
    assert "(value - price) / price * 100" in gap
    assert "price > 0" in gap and "value.isFinite" in gap, "never a fabricated 0% gap"
    init = _decl(model, "init?(dto: DcfEstimateDTO)")
    assert "$0 > 0" in init and "isFinite" in init, "a non-positive model value is not a value"


# ── rendering ────────────────────────────────────────────────────────────────

def test_the_row_is_a_labelled_model_value_with_the_three_states():
    section = _decl(_code(_SECTION), "struct ValuationMeterSection")
    assert 'static let dcfModelLabel = "DCF model value"' in section
    assert "not a price target" in section
    assert 'static let dcfNegativeCopy = "No DCF' in section
    row = _decl(section, "private func dcfRow(_ dcf: DcfEstimate)")
    assert "case .ok:" in row and "case .negativeCashFlow:" in row and "case .unavailable:" in row
    assert "dcf.needsCaveat(versus: currentPrice)" in row
    assert "dcf.formattedGap(versus: currentPrice)" in row
    assert "fair value" not in section.lower(), "never present the model as fair value"


def test_the_caveat_threshold_is_fifty_percent():
    model = _decl(_code(_MODELS), "struct DcfEstimate: Equatable")
    assert re.search(r"static let caveatThresholdPercent:\s*Double\s*=\s*50\.0", model)
    caveat = _decl(model, "func needsCaveat(versus price: Double?)")
    assert "abs(gap) > Self.caveatThresholdPercent" in caveat


def test_the_meter_reuses_the_technical_gauge_and_the_overviews_rating():
    tech = _code(_TECH_METER)
    assert "struct MeterGauge" in tech
    wrapper = _decl(tech, "struct TechnicalGauge")
    assert "MeterGauge(label: signal.rawValue, labelColor: signal.color, gaugeValue: gaugeValue)" in wrapper
    meter = _decl(_code(_METER), "struct ValuationMeter")
    assert "MeterGauge(" in meter and "TechnicalLevelIndicatorsRow(" in meter
    assert 'static let scaleLabels = ["Expensive", "Pricey", "Fair", "Cheap", "Bargain"]' in meter
    vm = _decl(_code(_VM), "var valuationSnapshot: SnapshotItem?")
    assert "$0.category == .price" in vm, "the meter must show the Overview's own valuation rating"


def test_the_section_carries_a_full_width_fair_value_disclaimer():
    body = _decl(_code(_SECTION), "var body: some View")
    i = body.index("AnalysisDisclaimerText.fairValue")
    assert ".frame(maxWidth: .infinity)" in body[i: i + 120]


# ── placement ────────────────────────────────────────────────────────────────

def test_street_estimates_are_gone_and_the_detail_view_wires_the_meter():
    """The Street Estimates card was removed outright on 2026-09-17 (it never lived on
    Financials for more than a day). The meter still gets the snapshot AND the live price
    from the detail view — the gap is computed client-side, never shipped."""
    fin = _decl(_code(_FINANCIALS), "var body: some View")
    assert "AnalystForecastsSection(" not in fin and "forwardEstimates" not in fin
    assert "EarningsSectionCard(" in fin  # anti-vacuity
    view = _code(_DETAIL_VIEW)
    analysis = view[view.index("case .analysis:"): view.index("case .financials:")]
    assert "valuationSnapshot: viewModel.valuationSnapshot" in analysis
    assert "currentPrice: viewModel.tickerData?.currentPrice" in analysis
    financials = view[view.index("case .financials:"): view.index("case .holders:")]
    assert "analystRatingsData" not in financials, "Financials takes the ratings model again"


def test_the_ai_context_grounds_the_valuation_chip_with_the_same_caveats():
    body = _decl(_code(_VM), "private var analysisContext: String?")
    assert "ValuationMeter.label(for: snapshot.rating)" in body
    assert "not a price target" in body
    assert "case .negativeCashFlow:" in body and "negative free cash flow" in body
