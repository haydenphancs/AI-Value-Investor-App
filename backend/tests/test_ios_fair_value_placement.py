"""Where the Caydex Fair Value Estimate shows on iOS, and its "temporarily unavailable" state.

Owner decisions, 2026-10-23 (plan "dcf-v2 + reliability + placement"):
  * Placement: "Overview tab row" + "Report: move up, expand". The estimate used to show only
    on the Analysis tab (not the default) and as the LAST of nine collapsed report modules,
    so most readers never saw it.
  * R3: on 2026-10-21 FRED failed, the backend sent no estimate anywhere, and the Analysis
    tab drew NO row — the estimate looked like it had never existed. The backend now flags
    that case (`caydex_estimate_unavailable`) and iOS says "temporarily unavailable", and
    does not cache that overview, so the next open asks again.

Pinned, in the Swift that ships:
  * the Overview compact row's states (estimate range-first / refusal / unavailable /
    nothing) and its tap target;
  * it renders for the `.price` snapshot only, outside the expanded branch, with the live
    header price threaded from the Overview;
  * the Analysis card's unavailable arm comes AFTER the FMP `dcfRow` arm;
  * `StockRepository` skips `setCache` for a flagged overview;
  * "Valuation & Institutions" is the FIRST deep-dive module and the ONLY one that starts
    expanded, and `ReportDeepDiveSection` seeds its `@State` from it;
  * the wire contract: Pydantic `caydex_estimate_unavailable: Optional[bool] = None` ↔ Swift
    `Bool?` with the same CodingKey;
  * the chat context: the Overview turn is told the estimate its Price card draws, through ONE
    helper (`caydexEstimateContextLine`) that `analysisContext` shares — estimate / refusal /
    temporarily unavailable / nothing, in the Valuation card's precedence on the Analysis tab.

Comment-stripped and brace-bounded (.claude/rules/testing.md §3); each guard mutation-tested
by hand once (break the source, watch it fail, restore).
"""
from __future__ import annotations

import re
import typing
from pathlib import Path

from app.schemas.stock_overview import SnapshotItemResponse

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_COMPACT = _IOS / "Views" / "Molecules" / "CaydexFairValueCompactRow.swift"
_UNAVAILABLE = _IOS / "Views" / "Molecules" / "CaydexFairValueUnavailableRow.swift"
_SNAPSHOT_CARD = _IOS / "Views" / "Molecules" / "SnapshotCard.swift"
_SNAPSHOTS_SECTION = _IOS / "Views" / "Organisms" / "TickerDetailSnapshotsSection.swift"
_OVERVIEW = _IOS / "Views" / "Organisms" / "TickerDetailOverviewContent.swift"
_METER_SECTION = _IOS / "Views" / "Organisms" / "ValuationMeterSection.swift"
_REPOSITORY = _IOS / "Core" / "Repositories" / "StockRepository.swift"
_SNAP_DTO = _IOS / "Models" / "StockOverviewResponseModels.swift"
_DETAIL_MODELS = _IOS / "Models" / "TickerDetailModels.swift"
_FV_MODELS = _IOS / "Models" / "FairValueModels.swift"
_REPORT_MODELS = _IOS / "Models" / "TickerReportModels.swift"
_REPORT_VM = _IOS / "ViewModels" / "TickerReportViewModel.swift"
_DEEP_DIVE = _IOS / "Views" / "Organisms" / "ReportDeepDiveSection.swift"
_REPORT_VIEW = _IOS / "Views" / "Screens" / "TickerReportView.swift"
_VIEWS = _IOS / "Views"
_VM = _IOS / "ViewModels" / "TickerDetailViewModel.swift"


def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"(?<!:)//.*$", "", line) for line in src.splitlines())


def _code(path: Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip(path.read_text(encoding="utf-8"))


def _match(src: str, start: int, open_: str, close: str) -> str:
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_:
            depth += 1
        elif src[i] == close:
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced {open_}{close} at {start}")


def _decl(src: str, prefix: str) -> str:
    at = src.index(prefix)
    return _match(src, src.index("{", at), "{", "}")


def _root_modifiers(label: str) -> list[str]:
    """Modifier lines on the label closure's OUTERMOST view (depth 0), in order."""
    out, depth = [], 0
    for line in label.splitlines():
        lead = depth
        for ch in line:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
        if lead == 0 and line.strip().startswith("."):
            out.append(line.strip())
    return out


_VERDICTS = re.compile(r"undervalued|overvalued|underpriced|overpriced|fairly valued|\bcheap\b|"
                       r"bargain|\bbuy\b|\bsell\b|\bupside\b|\bdownside\b|price target:", re.I)


# ── the Overview compact row ─────────────────────────────────────────────────────────────────

def _compact() -> str:
    return _decl(_code(_COMPACT), "struct CaydexFairValueCompactRow")


def test_the_compact_row_has_the_estimate_refusal_unavailable_and_absent_states():
    row = _compact()
    body = _decl(row, "var body: some View")
    split = body.index("} else if isUnavailable {")
    assert body.lstrip("{ \n").startswith("if let estimate {"), "the estimate arm leads"
    tail = body[split:]
    assert re.match(r"\} else if isUnavailable \{\s*CaydexFairValueUnavailableRow\(\)", tail), tail
    # Absent (no estimate, not flagged) draws NOTHING — no third arm, no placeholder.
    assert "else {" not in tail and "else{" not in tail, "an absent estimate must draw nothing"
    est = body[:split]
    # Estimate arm, range first: title + range → "Estimate $V · gap" → subtitle.
    arm = est[est.index("case .estimate:"):est.index("case .refused(let reason):")]
    assert "estimateLine(estimate)" in arm and "CaydexFairValue.subtitle" in arm
    order = [est.index(t) for t in ("value: headlineValue(estimate)", "estimateLine(estimate)",
                                    "CaydexFairValue.subtitle")]
    assert order == sorted(order), "range → estimate line → subtitle"
    assert re.search(r"ReportMetricRow\(label: CaydexFairValue\.title,\s*value: headlineValue\(estimate\)", est), \
        "reuse the ReportMetricRow atom for the title + headline line"
    refused = est[est.index("case .refused(let reason):"):]
    assert "Text(reason)" in refused
    head = _decl(row, "private func headlineValue(_ estimate: CaydexFairValue) -> String")
    assert re.search(r"case \.estimate:\s*return estimate\.formattedRangeBounds", head), "the RANGE is the headline"
    assert re.search(r"case \.refused:\s*return CaydexFairValue\.refusedHeadline", head)
    line = _decl(row, "private func estimateLine(_ estimate: CaydexFairValue) -> String?")
    assert "estimate.formattedEstimate" in line and "estimate.formattedGap(versus: currentPrice)" in line
    assert '"\\(mid) · \\(gap)"' in line
    assert "formattedValue" not in row, "a bare value without its range is back"


def test_the_compact_row_opens_the_sheet_and_is_one_tap_target():
    row = _compact()
    body = _decl(row, "var body: some View")
    est = body[:body.index("} else if isUnavailable {")]
    assert "CaydexFairValueSheet(estimate: estimate, currentPrice: currentPrice)" in est
    assert ".buttonStyle(.plain)" in est
    label_at = est.index("} label: {") + len("} label: ")
    label = _match(est, label_at, "{", "}")[1:-1]
    mods = _root_modifiers(label)
    assert mods and mods[-1] == ".contentShape(Rectangle())", f"contentShape must be LAST: {mods}"
    assert any(m.startswith(".padding(") for m in mods), mods
    # The unavailable state has nothing to open.
    unavailable = _decl(_code(_UNAVAILABLE), "struct CaydexFairValueUnavailableRow")
    assert "Button" not in unavailable and "onTapGesture" not in unavailable and ".sheet(" not in unavailable


def test_the_compact_row_draws_no_surface_and_only_text_tokens():
    for src in (_compact(), _decl(_code(_UNAVAILABLE), "struct CaydexFairValueUnavailableRow")):
        for banned in (".cardSurface(", ".cardFill(", ".background(", "Color(hex", "Color.black", ".white"):
            assert banned not in src, f"{banned} — the row lives inside the Snapshots card"
        colors = set(re.findall(r"AppColors\.(\w+)", src))
        assert colors and colors <= {"textPrimary", "textSecondary", "textMuted"}, colors
        for s in re.findall(r'"((?:[^"\\]|\\.)*)"', src):
            assert not _VERDICTS.search(s), s


def test_the_unavailable_copy_is_not_a_refusal():
    model = _decl(_code(_FV_MODELS), "struct CaydexFairValue:")
    m = re.search(r'static let temporarilyUnavailable = "([^"]+)"', model)
    assert m, "the unavailable copy constant is gone"
    copy = m.group(1)
    assert copy == "Temporarily unavailable — we'll try again shortly."
    assert "modelled" not in copy.lower() and "fair value" not in copy.lower()
    row = _decl(_code(_UNAVAILABLE), "struct CaydexFairValueUnavailableRow")
    assert "Text(CaydexFairValue.title)" in row and "Text(CaydexFairValue.temporarilyUnavailable)" in row
    assert "refusedHeadline" not in row, "unavailable is not 'Not modelled'"


# ── placement: the Overview's Price snapshot card ────────────────────────────────────────────

def test_the_compact_row_renders_for_the_price_snapshot_only_and_while_collapsed():
    card = _decl(_code(_SNAPSHOT_CARD), "struct SnapshotCard: View")
    body = _decl(card, "var body: some View")
    assert body.count("CaydexFairValueCompactRow(") == 1
    assert re.search(
        r"if snapshot\.category == \.price \{\s*CaydexFairValueCompactRow\(\s*"
        r"estimate: snapshot\.caydexEstimate,\s*isUnavailable: snapshot\.caydexEstimateUnavailable,\s*"
        r"currentPrice: currentPrice\s*\)\s*\}", body), "the row must be gated on the Price card"
    at = body.index("CaydexFairValueCompactRow(")
    expanded_at = body.index("if isExpanded {")
    expanded = _match(body, body.index("{", expanded_at), "{", "}")
    assert "CaydexFairValueCompactRow" not in expanded, "it must show on the COLLAPSED card"
    assert body.index(".buttonStyle(PlainButtonStyle())") < at, "never inside the header's toggle button"
    assert re.search(r"var currentPrice:\s*Double\?\s*=\s*nil", card)


def test_the_live_price_is_threaded_from_the_overview():
    section = _decl(_code(_SNAPSHOTS_SECTION), "struct TickerDetailSnapshotsSection: View")
    assert re.search(r"var currentPrice:\s*Double\?\s*=\s*nil", section)
    assert "SnapshotCard(snapshot: snapshot, currentPrice: currentPrice)" in section
    overview = _decl(_code(_OVERVIEW), "struct TickerDetailOverviewContent: View")
    call = overview[overview.index("TickerDetailSnapshotsSection("):]
    call = call[:call.index(")") + 1]
    assert "currentPrice: tickerData.currentPrice" in call, call


def test_only_the_snapshot_card_places_the_compact_row():
    hosts = []
    for path in sorted(_VIEWS.rglob("*.swift")):
        if path == _COMPACT:
            continue
        if "CaydexFairValueCompactRow(" in _code(path):
            hosts.append(path.name)
    assert hosts == ["SnapshotCard.swift"], hosts


# ── the Analysis card's unavailable arm ──────────────────────────────────────────────────────

def test_the_analysis_card_says_unavailable_after_the_dcf_row_arm():
    section = _decl(_code(_METER_SECTION), "struct ValuationMeterSection")
    body = _decl(section, "var body: some View")
    m = re.search(r"\} else if let dcf = snapshot\.dcf \{\s*dcfRow\(dcf\)\s*"
                  r"\} else if snapshot\.caydexEstimateUnavailable \{(.*?)\n\s*\}\n", body, flags=re.S)
    assert m, "the unavailable arm must follow the dcfRow arm"
    assert "CaydexFairValueUnavailableRow()" in m.group(1)
    assert body.count("caydexEstimateUnavailable") == 1, "flag read once, in its own arm"
    assert body.index("if let estimate = snapshot.caydexEstimate {") < m.start()
    assert "fair value" not in section.lower()


# ── the wire flag, the mapping, the cache ────────────────────────────────────────────────────

def test_the_dto_decodes_the_flag_and_maps_it_only_without_an_estimate():
    src = _code(_SNAP_DTO)
    dto = _decl(src, "struct SnapshotItemDTO")
    assert re.search(r"let caydexEstimateUnavailable:\s*Bool\?", dto)
    assert 'case caydexEstimateUnavailable = "caydex_estimate_unavailable"' in dto
    assert "let caydexEstimate = dto.caydexEstimate.flatMap { CaydexFairValue(dto: $0) }" in src
    assert "caydexEstimateUnavailable: dto.caydexEstimateUnavailable == true && caydexEstimate == nil" in src
    item = _decl(_code(_DETAIL_MODELS), "struct SnapshotItem: Identifiable")
    assert re.search(r"var caydexEstimateUnavailable:\s*Bool\s*=\s*false", item)
    init = _decl(item, "init(category: SnapshotCategory")
    assert "self.caydexEstimateUnavailable = caydexEstimateUnavailable" in init
    assert re.search(r"caydexEstimateUnavailable:\s*Bool\s*=\s*false",
                     item[item.index("init(category: SnapshotCategory"):item.index(init)])


def test_the_repository_does_not_cache_a_flagged_overview():
    top = _decl(_code(_SNAP_DTO), "struct StockOverviewResponseDTO: Decodable")
    flag = _decl(top, "var hasUnavailableCaydexEstimate: Bool")
    assert "snapshots.contains" in flag
    assert "$0.caydexEstimateUnavailable == true && $0.caydexEstimate == nil" in flag
    repo = _decl(_code(_REPOSITORY), "final class StockRepository: StockRepositoryProtocol")
    fn = _decl(repo, 'func getStockOverview(ticker: String, range: String = "3M"')
    assert fn.count("setCache(") == 1
    assert re.search(r"if response\.hasUnavailableCaydexEstimate \{[^{}]*\} else \{\s*"
                     r"setCache\(cacheKey, value: response\)\s*\}", fn), fn
    assert "return response" in fn[fn.index("setCache("):], "the flagged response is still shown"


def test_the_backend_and_ios_agree_on_the_flag():
    field = SnapshotItemResponse.model_fields.get("caydex_estimate_unavailable")
    assert field is not None, "SnapshotItemResponse lost caydex_estimate_unavailable"
    assert field.default is None and not field.is_required()
    assert typing.get_origin(field.annotation) is typing.Union
    assert set(typing.get_args(field.annotation)) == {bool, type(None)}
    # A shipped build ignores an unknown key, and a None is dropped by no one: both decode.
    assert SnapshotItemResponse(category="Price", rating=3, metrics=[]).caydex_estimate_unavailable is None
    dto = _decl(_code(_SNAP_DTO), "struct SnapshotItemDTO")
    assert re.search(r"let caydexEstimateUnavailable:\s*Bool\?", dto), "the Swift field must be Optional"
    assert '= "caydex_estimate_unavailable"' in dto


# ── the report: "Valuation & Institutions" first, expanded ──────────────────────────────────

def _module_entries() -> list[str]:
    vm = _code(_REPORT_VM)
    at = vm.index("let deepDiveModules: [DeepDiveModule] = [")
    array = _match(vm, vm.index("= [", at) + 2, "[", "]")
    return re.findall(r"DeepDiveModule\(([^()]*)\)", array)


def test_valuation_is_the_first_module_and_the_only_one_expanded():
    entries = _module_entries()
    enum = _decl(_code(_REPORT_MODELS), "enum DeepDiveModuleType")
    cases = set(re.findall(r"case (\w+)", enum))
    assert len(entries) == len(cases) == 9, (len(entries), cases)
    assert {re.search(r"type: \.(\w+)", e).group(1) for e in entries} == cases, "every module once"
    first = entries[0]
    assert 'title: "Valuation & Institutions"' in first and "type: .wallStreetConsensus" in first
    expanded = [e for e in entries if re.search(r"startsExpanded:\s*true", e)]
    assert expanded == [first], f"only Valuation & Institutions starts expanded: {expanded}"
    model = _decl(_code(_REPORT_MODELS), "struct DeepDiveModule: Identifiable")
    assert re.search(r"var startsExpanded\s*=\s*false", model), "every other module defaults collapsed"
    # The screen keeps the view model's order (it only filters out an empty module).
    report = _decl(_code(_REPORT_VIEW), "private func deepDiveModulesSection")
    assert "viewModel.deepDiveModules.filter" in report
    assert ".sorted" not in report and ".reversed" not in report


def test_the_section_seeds_its_state_from_the_module():
    section = _decl(_code(_DEEP_DIVE), "struct ReportDeepDiveSection")
    init = _decl(section, "init(module: DeepDiveModule,")
    assert "_isExpanded = State(initialValue: module.startsExpanded)" in init
    for assigned in ("self.module = module", "self.isLast = isLast", "self.onCollapse = onCollapse",
                     "self.content = content"):
        assert assigned in init, assigned
    assert "@State private var isExpanded" in section
    # The other guards on this file still hold their tokens.
    assert "var onCollapse: (() -> Void)? = nil" in section
    assert section.count("onCollapse?()") == 1


# ── the chat context: the Overview turn is told what its Price card draws ────────────────────
#
# Review finding IOS-2 (2026-10-23): the compact row put the estimate on the DEFAULT tab, but
# `overviewContext` never mentioned it — only `analysisContext` quoted it, and
# `contextForCurrentTab` sends the selected tab's context alone. Cay AI could then say it had
# no estimate beside a card showing one, or reason about value itself.

_HELPER = "private func caydexEstimateContextLine(for snapshot: SnapshotItem) -> String?"


def test_the_overview_chat_context_quotes_the_price_cards_estimate():
    overview = _decl(_code(_VM), "private var overviewContext: String?")
    loop = _decl(overview, "for snap in td.snapshots")
    assert re.search(r"if snap\.category == \.price, let (\w+) = caydexEstimateContextLine\(for: snap\) \{"
                     r"\s*parts\.append\(\1\)\s*\}", loop), \
        "the Overview turn must be told the estimate its Price card draws (CaydexFairValueCompactRow)"
    # The helper owns the wording; the compact row never shows FMP's model.
    assert "CaydexFairValue.title" not in overview
    assert "caydexEstimate" not in overview.replace("caydexEstimateContextLine", "")
    assert "snap.dcf" not in overview and "DCF model value" not in overview


def test_the_estimate_context_helper_covers_the_cards_four_states():
    helper = _decl(_code(_VM), _HELPER)
    assert helper.lstrip("{ \n").startswith("if let estimate = snapshot.caydexEstimate {"), "the estimate leads"
    est = _decl(helper, "if let estimate = snapshot.caydexEstimate")
    ok = est[est.index("case .estimate:"):est.index("case .refused(let reason):")]
    assert "(a DCF model estimate — not a price target, not a recommendation)" in ok
    for token in ("estimate.formattedValue", "estimate.formattedRange",
                  "estimate.formattedGap(versus: tickerData?.currentPrice)"):
        assert token in ok, token
    assert re.search(r"\breturn line\b", ok)
    refused = est[est.index("case .refused(let reason):"):]
    assert 'return "\\(CaydexFairValue.title): none — \\(reason)"' in refused
    # After the estimate block: the unavailable line, then nil for an absent estimate.
    tail = helper[helper.index(est) + len(est):]
    m = re.match(r'\s*if snapshot\.caydexEstimateUnavailable \{\s*return "([^"]*)"\s*\}\s*return nil\s*\}\s*$', tail)
    assert m, f"unavailable → its line, absent → nil: {tail!r}"
    line = m.group(1)
    assert line.startswith("\\(CaydexFairValue.title): temporarily unavailable"), line
    assert "not a refusal" in line
    assert "none —" not in line and "modelled" not in line.lower(), "unavailable is not a refusal"
    assert helper.count("caydexEstimateUnavailable") == 1
    assert "snapshot.dcf" not in helper, "the Caydex line never quotes FMP's model"
    for s in re.findall(r'"((?:[^"\\]|\\.)*)"', helper):
        assert not _VERDICTS.search(s), s


def test_the_analysis_chat_context_shares_the_helper_in_the_valuation_cards_order():
    vm = _code(_VM)
    analysis = _decl(vm, "private var analysisContext: String?")
    head = re.search(r"if snapshot\.caydexEstimate != nil, let (\w+) = caydexEstimateContextLine\(for: snapshot\) \{"
                     r"\s*parts\.append\(\1\)\s*\} else if let dcf = snapshot\.dcf \{", analysis)
    assert head, "the estimate (or its refusal) replaces FMP's DCF line, as on the card"
    dcf_at = head.end() - 1
    dcf_arm = _match(analysis, dcf_at, "{", "}")
    assert "caydexEstimateContextLine" not in dcf_arm
    after = analysis[dcf_at + len(dcf_arm):]
    assert re.match(r"\s*else if let (\w+) = caydexEstimateContextLine\(for: snapshot\) \{\s*parts\.append\(\1\)\s*\}",
                    after), "the unavailable line follows the dcf arm, as on the card"
    # One copy of the wording: the helper's.
    assert "CaydexFairValue.title" not in analysis and "caydexEstimateUnavailable" not in analysis
    helper = _decl(vm, _HELPER)
    assert vm.count("CaydexFairValue.title") == helper.count("CaydexFairValue.title")
    assert vm.count("func caydexEstimateContextLine(") == 1
    assert vm.count("caydexEstimateContextLine(for:") == 3, "three call sites: Overview once, Analysis twice"
