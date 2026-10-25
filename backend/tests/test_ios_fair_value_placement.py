r"""Where the Caydex Fair Value Estimate shows on iOS, who may see it, and its "temporarily
unavailable" state.

Owner decisions:
  * 2026-10-23 (plan "dcf-v2 + reliability + placement"): R3 — on 2026-10-21 FRED failed, the
    backend sent no estimate anywhere, and the Analysis tab drew NO row: the estimate looked
    like it had never existed. The backend now flags that case (`caydex_estimate_unavailable`)
    and iOS says "temporarily unavailable", and does not cache that overview, so the next open
    asks again. The same day's report placement was reversed after the owner saw it on the
    Simulator: "move 'Valuation and institution' to at the bottom as the old way" — last
    module, collapsed, as before.
  * 2026-10-25: "for Analysis tab, lock caydex fair value estimate for free accounts, it should
    show for Pro and Max only", scoped as "remove 'caydex fair value estimate' at the
    snapshots, then lock for 'caydex fair value estimate' in the analysis only", enforced
    "Server-side (Recommended)". So the Overview's compact row (2026-10-23, "Overview tab row")
    is DELETED for everyone, and the Analysis tab's Valuation card shows `LockedSectionCard`
    — opening the plans — when the server withheld the estimate (`caydex_estimate_locked`).
    Reports and Cay AI chat are not gated.

Pinned, in the Swift that ships:
  * the compact row is gone: no file, no reference, no estimate on any snapshot card, and no
    `currentPrice` threaded into the Snapshots section for it;
  * the Analysis card's arms: estimate → FMP `dcfRow` → unavailable → LOCKED, the lock nested
    (`cardBackgroundNested`) and presenting `PaywallView` with `\.appState` re-injected;
  * the wire contract: `caydex_estimate_unavailable` / `caydex_estimate_locked` (Optional[bool])
    and `caydex_estimate_tier_required` (Optional[str]) ↔ Swift `Bool?` / `String?` with the
    same CodingKeys, each flag mapped only when no estimate came with it, and never
    "unavailable" beside the lock;
  * `StockRepository` skips `setCache` for an unavailable overview (a locked one is cached as
    usual — session end clears the cache);
  * "Valuation & Institutions" is the LAST deep-dive module and every module starts collapsed;
  * the chat context: the Overview turn no longer quotes the estimate; `analysisContext` does,
    through ONE helper (`caydexEstimateContextLine`) that returns nil for a locked snapshot;
  * the lock opens its OWN paywall context, `PaywallContext.caydexFairValue` ("caydex_fair_value",
    the plan row `plan_features` emits), so the sheet highlights the row it is about; the
    bundled plan row carries the owner's copy (paid line "wherever the model can value the
    company" — the owner chose "Lock only real estimates", so a refused stock is "Not modelled"
    on every plan and the paid line promises no number there);
  * a plan change re-fetches the overview: `TickerDetailView`'s `entitlementGeneration`
    observer calls `refreshOverviewAfterEntitlementChange()`, which waits out a load in flight,
    drops the ticker's client cache, re-fetches WITHOUT the loading placeholder, writes through
    the one apply path `loadTickerData()` uses, and keeps the screen on a failure.

Comment-stripped and brace-bounded (.claude/rules/testing.md §3); each guard mutation-tested
by hand once on a copy (break the source, watch it fail, restore).
"""
from __future__ import annotations

import re
import typing
from pathlib import Path

from app.schemas.stock_overview import SnapshotItemResponse

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_COMPACT_NAME = "CaydexFairValueCompactRow"
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
_IOS_ROOT = _IOS.parent  # frontend/ios: the app, the widget and Shared/
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


_VERDICTS = re.compile(r"undervalued|overvalued|underpriced|overpriced|fairly valued|\bcheap\b|"
                       r"bargain|\bbuy\b|\bsell\b|\bupside\b|\bdownside\b|price target:", re.I)


# ── the Overview compact row: removed (owner, 2026-10-25) ─────────────────────────────────

def test_the_compact_row_is_gone_from_the_tree():
    assert not (_IOS / "Views" / "Molecules" / f"{_COMPACT_NAME}.swift").exists(), \
        "the Overview compact row is back — the owner removed it on 2026-10-25"
    hosts = [str(path.relative_to(_IOS_ROOT)) for path in sorted(_IOS_ROOT.rglob("*.swift"))
             if _COMPACT_NAME in _code(path)]
    assert hosts == [], hosts
    pbx = (_IOS_ROOT / "ios.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
    assert _COMPACT_NAME not in pbx


def test_no_snapshot_card_draws_the_estimate():
    card = _decl(_code(_SNAPSHOT_CARD), "struct SnapshotCard: View")
    for banned in ("caydexEstimate", "CaydexFairValue", "currentPrice"):
        assert banned not in card, f"{banned} — the Overview's snapshot cards show no estimate"
    section = _decl(_code(_SNAPSHOTS_SECTION), "struct TickerDetailSnapshotsSection: View")
    assert "currentPrice" not in section, "the price was threaded only for the removed row"
    assert "SnapshotCard(snapshot: snapshot)" in section
    overview = _decl(_code(_OVERVIEW), "struct TickerDetailOverviewContent: View")
    call = overview[overview.index("TickerDetailSnapshotsSection("):]
    call = _match(call, call.index("("), "(", ")")
    assert "currentPrice" not in call, call
    assert "snapshots: tickerData.snapshots" in call


def test_the_unavailable_row_draws_no_surface_and_opens_nothing():
    row = _decl(_code(_UNAVAILABLE), "struct CaydexFairValueUnavailableRow")
    for banned in (".cardSurface(", ".cardFill(", ".background(", "Color(hex", "Color.black", ".white"):
        assert banned not in row, f"{banned} — the row lives inside the Valuation card"
    colors = set(re.findall(r"AppColors\.(\w+)", row))
    assert colors and colors <= {"textPrimary", "textSecondary", "textMuted"}, colors
    for s in re.findall(r'"((?:[^"\\]|\\.)*)"', row):
        assert not _VERDICTS.search(s), s
    # The unavailable state has nothing to open.
    assert "Button" not in row and "onTapGesture" not in row and ".sheet(" not in row


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


# ── the Analysis card's unavailable and locked arms ────────────────────────────────────────

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


def _meter_body() -> tuple[str, str]:
    section = _decl(_code(_METER_SECTION), "struct ValuationMeterSection")
    return section, _decl(section, "var body: some View")


def test_the_analysis_card_locks_after_the_unavailable_arm():
    section, body = _meter_body()
    m = re.search(r"\} else if snapshot\.caydexEstimateUnavailable \{.*?CaydexFairValueUnavailableRow\(\)\s*\}\s*"
                  r"\} else if snapshot\.caydexEstimateLocked \{\s*"
                  r"LockedSectionCard\(title: CaydexFairValue\.lockedTitle,\s*"
                  r"message: CaydexFairValue\.lockedMessage,\s*nested: true\) \{\s*"
                  r"showPaywall = true\s*\}\s*\}", body, flags=re.S)
    assert m, "the lock must be the arm after the unavailable one, nested, opening the plans"
    assert body.index("} else if let dcf = snapshot.dcf {") < m.start()
    assert body.count("caydexEstimateLocked") == 1, "flag read once, in its own arm"
    lock_arm = body[body.index("} else if snapshot.caydexEstimateLocked {"):]
    for banned in ("CaydexFairValueRow", "CaydexFairValueRangeChart", "chartPlaceholder", "dcfRow"):
        assert banned not in lock_arm, f"{banned} drawn beside the lock"
    assert "fair value" not in section.lower()


def test_the_lock_presents_the_plans_with_the_app_state():
    section, _ = _meter_body()
    assert re.search(r"@Environment\(\\\.appState\) private var appState", section)
    assert re.search(r"@State private var showPaywall:\s*Bool\s*=\s*false", section)
    sheet_at = section.index(".sheet(isPresented: $showPaywall)")
    sheet = _match(section, section.index("{", sheet_at), "{", "}")
    assert re.search(r"PaywallView\(context: \.caydexFairValue\)\s*\.environment\(\\\.appState, appState\)", sheet), \
        "PaywallView reads \\.appState, and a sheet does not inherit it"
    assert section.count("showPaywall = true") == 1, "only the lock opens the plans"


def test_the_lock_copy_names_pro_and_max():
    model = _decl(_code(_FV_MODELS), "struct CaydexFairValue:")
    assert re.search(r"static let lockedTitle = (Self\.)?title\b", model)
    m = re.search(r'static let lockedMessage = "([^"]+)"', model)
    assert m, "the lock copy constant is gone"
    copy = m.group(1)
    assert "Pro and Max" in copy and "Caydex Fair Value Estimate" in copy
    assert not _VERDICTS.search(copy), copy
    card = _decl(_code(_IOS / "Views" / "Molecules" / "LockedSectionCard.swift"), "struct LockedSectionCard")
    assert "AppColors.cardBackgroundNested" in card, "nested: true must still mean the nested surface"


def test_the_info_sheet_explains_no_model_beside_the_lock():
    # A locked snapshot carries neither `caydexEstimate` nor FMP's `dcf`, so the sheet
    # keys off their presence and explains neither model (no estimate it cannot show).
    section, _ = _meter_body()
    assert re.search(r"ValuationInfoSheet\(showsCaydexEstimate: snapshot\.caydexEstimate != nil,\s*"
                     r"showsFmpDcf: snapshot\.dcf != nil\)", section)


# ── the wire flags, the mapping, the cache ───────────────────────────────────────────────────

def test_the_dto_decodes_the_flag_and_maps_it_only_without_an_estimate():
    src = _code(_SNAP_DTO)
    dto = _decl(src, "struct SnapshotItemDTO")
    assert re.search(r"let caydexEstimateUnavailable:\s*Bool\?", dto)
    assert 'case caydexEstimateUnavailable = "caydex_estimate_unavailable"' in dto
    assert "let caydexEstimate = dto.caydexEstimate.flatMap { CaydexFairValue(dto: $0) }" in src
    assert re.search(r"caydexEstimateUnavailable: dto\.caydexEstimateUnavailable == true && caydexEstimate == nil"
                     r"\s*&& !caydexEstimateLocked,", src), "never unavailable beside the lock"
    item = _decl(_code(_DETAIL_MODELS), "struct SnapshotItem: Identifiable")
    assert re.search(r"var caydexEstimateUnavailable:\s*Bool\s*=\s*false", item)
    init = _decl(item, "init(category: SnapshotCategory")
    assert "self.caydexEstimateUnavailable = caydexEstimateUnavailable" in init
    assert re.search(r"caydexEstimateUnavailable:\s*Bool\s*=\s*false",
                     item[item.index("init(category: SnapshotCategory"):item.index(init)])


def test_the_dto_decodes_the_lock_and_maps_it_only_without_an_estimate():
    src = _code(_SNAP_DTO)
    dto = _decl(src, "struct SnapshotItemDTO")
    assert re.search(r"let caydexEstimateLocked:\s*Bool\?", dto)
    assert re.search(r"let caydexEstimateTierRequired:\s*String\?", dto)
    keys = _decl(dto, "enum CodingKeys")
    assert 'case caydexEstimateLocked = "caydex_estimate_locked"' in keys
    assert 'case caydexEstimateTierRequired = "caydex_estimate_tier_required"' in keys
    assert "let caydexEstimateLocked = dto.caydexEstimateLocked == true && caydexEstimate == nil" in src
    assert "caydexEstimateLocked: caydexEstimateLocked," in src
    assert "caydexEstimateTierRequired: dto.caydexEstimateTierRequired," in src
    item = _decl(_code(_DETAIL_MODELS), "struct SnapshotItem: Identifiable")
    assert re.search(r"var caydexEstimateLocked:\s*Bool\s*=\s*false", item)
    assert re.search(r"var caydexEstimateTierRequired:\s*String\?\s*=\s*nil", item)
    init = _decl(item, "init(category: SnapshotCategory")
    assert "self.caydexEstimateLocked = caydexEstimateLocked" in init
    assert "self.caydexEstimateTierRequired = caydexEstimateTierRequired" in init
    sig = item[item.index("init(category: SnapshotCategory"):item.index(init)]
    assert re.search(r"caydexEstimateLocked:\s*Bool\s*=\s*false", sig)
    assert re.search(r"caydexEstimateTierRequired:\s*String\?\s*=\s*nil", sig)


def test_the_repository_does_not_cache_a_flagged_overview():
    top = _decl(_code(_SNAP_DTO), "struct StockOverviewResponseDTO: Decodable")
    flag = _decl(top, "var hasUnavailableCaydexEstimate: Bool")
    assert "snapshots.contains" in flag
    assert "$0.caydexEstimateUnavailable == true && $0.caydexEstimate == nil && $0.caydexEstimateLocked != true" in flag
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


def _optional_field(name: str, inner: type) -> None:
    field = SnapshotItemResponse.model_fields.get(name)
    assert field is not None, f"SnapshotItemResponse lacks {name} (the backend half of the lock)"
    assert field.default is None and not field.is_required(), name
    assert typing.get_origin(field.annotation) is typing.Union, name
    assert set(typing.get_args(field.annotation)) == {inner, type(None)}, name


def test_the_backend_and_ios_agree_on_the_lock():
    _optional_field("caydex_estimate_locked", bool)
    _optional_field("caydex_estimate_tier_required", str)
    bare = SnapshotItemResponse(category="Price", rating=3, metrics=[])
    assert bare.caydex_estimate_locked is None and bare.caydex_estimate_tier_required is None
    dto = _decl(_code(_SNAP_DTO), "struct SnapshotItemDTO")
    assert re.search(r"let caydexEstimateLocked:\s*Bool\?", dto), "the Swift field must be Optional"
    assert re.search(r"let caydexEstimateTierRequired:\s*String\?", dto), "the Swift field must be Optional"
    assert '= "caydex_estimate_locked"' in dto and '= "caydex_estimate_tier_required"' in dto


# ── the report: "Valuation & Institutions" last, collapsed (owner's reversal) ─────────────

def _module_entries() -> list[str]:
    vm = _code(_REPORT_VM)
    at = vm.index("let deepDiveModules: [DeepDiveModule] = [")
    array = _match(vm, vm.index("= [", at) + 2, "[", "]")
    return re.findall(r"DeepDiveModule\(([^()]*)\)", array)


def test_valuation_is_the_last_module_as_before():
    entries = _module_entries()
    enum = _decl(_code(_REPORT_MODELS), "enum DeepDiveModuleType")
    cases = set(re.findall(r"case (\w+)", enum))
    assert len(entries) == len(cases) == 9, (len(entries), cases)
    assert {re.search(r"type: \.(\w+)", e).group(1) for e in entries} == cases, "every module once"
    last = entries[-1]
    assert 'title: "Valuation & Institutions"' in last and "type: .wallStreetConsensus" in last
    # The screen keeps the view model's order (it only filters out an empty module).
    report = _decl(_code(_REPORT_VIEW), "private func deepDiveModulesSection")
    assert "viewModel.deepDiveModules.filter" in report
    assert ".sorted" not in report and ".reversed" not in report


def test_every_report_module_starts_collapsed():
    section = _decl(_code(_DEEP_DIVE), "struct ReportDeepDiveSection")
    assert re.search(r"@State private var isExpanded:\s*Bool\s*=\s*false", section)
    assert "_isExpanded" not in section, "nothing seeds a module open"
    model = _decl(_code(_REPORT_MODELS), "struct DeepDiveModule: Identifiable")
    assert "startsExpanded" not in model and "startsExpanded" not in _code(_REPORT_VM)
    # The other guards on this file still hold their tokens.
    assert "var onCollapse: (() -> Void)? = nil" in section
    assert section.count("onCollapse?()") == 1


# ── the chat context: told what the Analysis card draws, nothing beside a lock ───────────────
#
# Review finding IOS-2 (2026-10-23) made the Overview turn quote the estimate its compact row
# drew. The row was removed on 2026-10-25, so the Overview turn must not quote it any more —
# the turn is told only what its cards show. The Analysis turn still quotes it, except for a
# locked snapshot (the card shows the lock there, not the number).

_HELPER = "private func caydexEstimateContextLine(for snapshot: SnapshotItem) -> String?"


def test_the_overview_chat_context_no_longer_quotes_the_estimate():
    overview = _decl(_code(_VM), "private var overviewContext: String?")
    for banned in ("caydexEstimateContextLine", "caydexEstimate", "CaydexFairValue", "snap.dcf",
                   "DCF model value"):
        assert banned not in overview, f"{banned} — the Overview no longer shows the estimate"


def test_the_estimate_context_helper_is_silent_for_a_locked_snapshot_and_covers_the_cards_states():
    helper = _decl(_code(_VM), _HELPER)
    assert re.match(r"\{\s*guard !snapshot\.caydexEstimateLocked else \{ return nil \}\s*"
                    r"if let estimate = snapshot\.caydexEstimate \{", helper), \
        "a locked snapshot is told nothing, before any other state"
    assert helper.count("caydexEstimateLocked") == 1
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
    assert vm.count("caydexEstimateContextLine(for:") == 2, "two call sites, both in analysisContext"
    assert analysis.count("caydexEstimateContextLine(for:") == 2


# ── the paywall the lock opens (review fix round, 2026-10-25) ────────────────────────────────
#
# `.general` highlighted the credits row and shared its analytics `reason` with the Profile
# paywall; every other plan lock passes its own context.

_SUBSCRIPTION = _IOS / "Models" / "SubscriptionModels.swift"
_TICKER_VIEW = _IOS / "Views" / "Screens" / "TickerDetailView.swift"
_PAID_ROW = ("A DCF model's fair-value range and estimate on the Analysis tab, wherever the "
             "model can value the company.")
_FREE_ROW = ("Free keeps the valuation meter, multiples and peer comparisons on the Analysis "
             "tab. The Caydex Fair Value Estimate there is paid.")
_PAYWALL_WORDS = re.compile(_VERDICTS.pattern + r"|accura|guarantee|\bevery stock", re.I)


def _paywall_arm(enum_body: str, prop: str, case: str) -> str:
    """The text of `case .<case>:` (alone or in a comma list) in one PaywallContext switch."""
    body = _decl(enum_body, prop)
    m = re.search(r"case (?:\.\w+,\s*)*\." + case + r"\b(?:,\s*\.\w+)*:\s*(.*?)(?=\n\s*(?:case |\}))",
                  body, flags=re.S)
    assert m, f"PaywallContext.{prop.split()[1]} has no .{case} arm"
    return m.group(1)


def test_the_lock_opens_its_own_paywall_context():
    section, _ = _meter_body()
    assert section.count("PaywallView(context:") == 1
    assert "PaywallView(context: .caydexFairValue)" in section
    assert ".general" not in section

    enum = _decl(_code(_SUBSCRIPTION), "enum PaywallContext")
    assert 'case caydexFairValue = "caydex_fair_value"' in enum
    assert _paywall_arm(enum, "var featureKey: String", "caydexFairValue").strip() == 'return "caydex_fair_value"'
    from app.services import plan_features
    assert plan_features.KEY_CAYDEX_FAIR_VALUE == "caydex_fair_value", "the row the paywall highlights"
    assert '"scalemass.fill"' in _paywall_arm(enum, "var symbol: String", "caydexFairValue"), \
        "the header's glyph is its plan row's"
    assert "AppColors.accentCyan" in _paywall_arm(enum, "var accent: Color", "caydexFairValue"), \
        "the plan row's accent is \"signals\" (accentCyan)"
    headline = _paywall_arm(enum, "var headline: String", "caydexFairValue")
    sub = _paywall_arm(enum, "var subheadline: String", "caydexFairValue")
    assert "Caydex Fair Value Estimate" in headline
    assert "Pro and Max" in sub and "Analysis tab" in sub
    for text in (headline, sub):
        assert not _PAYWALL_WORDS.search(text), text


def test_the_bundled_plan_row_carries_the_owners_copy():
    bundled = _code(_SUBSCRIPTION)
    at = bundled.index('key: "caydex_fair_value"')
    row = _match(bundled, bundled.rindex("PlanFeature(", 0, at) + len("PlanFeature"), "(", ")")
    m = re.search(r'detail:\s*paid\s*\?\s*"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"', row, flags=re.S)
    assert m, row
    assert (m.group(1), m.group(2)) == (_PAID_ROW, _FREE_ROW)
    assert "included: paid" in row


# ── a plan change re-fetches the overview (review fix round, 2026-10-25) ─────────────────────
#
# The Price snapshot is tier-shaped and `StockRepository` caches the overview for 120 s under a
# key with no tier: a user who bought Pro from the lock kept the lock until pull-to-refresh.

_REFRESH = "func refreshOverviewAfterEntitlementChange() async"


def test_the_entitlement_observer_refreshes_holders_and_the_overview():
    view = _code(_TICKER_VIEW)
    assert view.count(".onChange(of: appState.entitlementGeneration)") == 1
    body = _decl(view, ".onChange(of: appState.entitlementGeneration)")
    assert "Task { await viewModel.refreshHoldersAfterEntitlementChange() }" in body
    assert "Task { await viewModel.refreshOverviewAfterEntitlementChange() }" in body


def test_the_overview_refresh_invalidates_refetches_and_keeps_the_screen():
    vm = _code(_VM)
    assert vm.count(_REFRESH) == 1
    fn = _decl(vm, _REFRESH)
    # Order: wait out a load in flight, THEN drop the cache, THEN fetch.
    wait = fn.index("if let inFlight = loadTask { await inFlight.value }")
    drop = fn.index("stockRepository.invalidate(symbol: tickerSymbol)")
    fetch = fn.index("try await stockRepository.getStockOverview(")
    apply = fn.index("await applyOverview(response, ticker: ticker, requested: requested, keepBarsOnScreen: true)")
    assert wait < drop < fetch < apply
    assert re.search(r"guard tickerData != nil else \{ return \}", fn[drop:fetch])
    # The newest plan change wins; cancellation writes nothing.
    assert re.search(r"overviewRefreshGeneration &\+= 1\s*let generation = overviewRefreshGeneration", fn)
    assert re.search(r"guard generation == overviewRefreshGeneration else \{", fn[fetch:apply])
    assert re.search(r"guard !Task\.isCancelled else \{ return \}", fn[fetch:apply])
    # No placeholder flash, no second Phase 2, no blanking on a failure.
    for banned in ("isLoading", "loadTickerData(", "errorMessage", "tickerData = nil", "toDisplayModel"):
        assert banned not in fn, f"{banned} in the entitlement refresh"
    catch = _decl(fn, "} catch")
    assert re.search(r"guard !Task\.isCancelled else \{ return \}\s*log\.error\(", catch)


def test_one_apply_path_writes_a_fetched_overview():
    vm = _code(_VM)
    assert vm.count("private func applyOverview(") == 1
    apply = _decl(vm, "private func applyOverview(")
    assert apply.count("response.toDisplayModel()") == 1
    assert vm.count("response.toDisplayModel()") == 1, "the overview is mapped in ONE place"
    assert re.search(r"\(keepBarsOnScreen && !shownBars\.isEmpty\)", apply)
    load = _decl(vm, "func loadTickerData()")
    assert load.count("await self.applyOverview(response, ticker: ticker, requested: requestedStamp)") == 1
    assert "toDisplayModel" not in load
    assert vm.count("applyOverview(") == 3, "the declaration and its two callers"
