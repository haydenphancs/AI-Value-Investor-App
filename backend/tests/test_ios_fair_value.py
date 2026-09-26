"""iOS side of the Caydex Fair Value Estimate — source-scan guards (no XCTest target exists).

Pins, in the Swift that ships:
  * the wire contract: every DcfFairValueResponse field has a CodingKey with the same snake name,
    everything but symbol/status is Optional, and both carrier DTOs decode the block;
  * the wording rules (documents/research/dcf-methodology-v1.md §5): the title says "Estimate",
    the subtitle says "not a price target", the gap is "Price N% below/above the estimate", and
    no verdict word (Undervalued / Overvalued / Fairly valued / Cheap / Bargain) appears;
  * a value is never shown without its range;
  * on the Analysis tab the Caydex row REPLACES FMP's row — never both;
  * the report card renders it and says its price is the report-time price;
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
    for path in (_MODELS, _ROW, _SHEET):
        for s in _strings(_code(path)):
            assert _verdict_free(s), f"{path.name}: {s!r}"
    info = _code(_IOS / "Views" / "Molecules" / "ValuationInfoSheet.swift")
    for s in _strings(_decl(info, "private var caydexSection: some View")):
        assert _verdict_free(s), s


def test_the_row_never_shows_a_value_without_its_range_and_subtitle():
    row = _decl(_code(_ROW), "struct CaydexFairValueRow")
    content = _decl(row, "private var content: some View")
    estimate_arms = re.findall(r"case \.estimate:(.*?)(?=case \.refused)", content, flags=re.S)
    assert len(estimate_arms) == 2, "value header arm + detail arm"
    detail = estimate_arms[1]
    assert "estimate.formattedRange" in detail and "CaydexFairValue.subtitle" in detail
    assert "CaydexFairValueSheet(" in row, "the assumptions must be one tap away"


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


def test_the_report_card_renders_it_at_report_time_above_the_analyst_section():
    bar = _decl(_code(_BAR), "struct ReportConsensusBar")
    body = _decl(bar, "var body: some View")
    assert re.search(r"if let estimate = consensus\.caydexFairValue \{\s*CaydexFairValueRow\("
                     r"estimate: estimate, currentPrice: consensus\.currentPrice,\s*"
                     r'priceContext: "at report time"\)', body)
    assert "cardBackgroundNested" in body, "a card inside a card must step up its fill"
    assert body.index("consensus.caydexFairValue") < body.index("analystPriceTargetHeader"), \
        "never under the 'Analyst Price Target' heading or its BUY/HOLD/SELL line"


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


def test_no_verdict_word_in_the_report_block_or_the_chat_branch():
    bar = _decl(_decl(_code(_BAR), "struct ReportConsensusBar"), "var body: some View")
    block = bar[bar.index("if let estimate = consensus.caydexFairValue"):bar.index("analystPriceTargetHeader")]
    vm = _code(_VM)
    chat = vm[vm.index("if let estimate = snapshot.caydexEstimate {"):vm.index("} else if let dcf = snapshot.dcf {")]
    for src in (block, chat):
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
