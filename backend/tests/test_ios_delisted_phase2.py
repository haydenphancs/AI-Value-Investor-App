"""iOS half of delisting Phase 2 (owner, 2026-11-13: "approve phase 2 too. finish everything").

What the owner approved, iOS side:
  1. A new AI report on a delisted ticker is refused BEFORE any charge, on both report doors
     (`POST /research/generate`, `GET /stocks/{t}/report`), with ONE new backend code,
     `TICKER_DELISTED` (409). iOS maps it to a typed `AppError.tickerDelisted(message:)` that
     carries the server's `user_message` verbatim ("ROKU was delisted from NASDAQ on Nov 9, 2026,
     so a new AI report can't be generated. You haven't been charged. ...") with `.goBack`:
     no Retry (the refusal is permanent), not a session error, never a token refresh.
  2. Price alerts on a delisted ticker: the add form gives way to a notice ("Price alerts are
     paused — ROKU is delisted, so its price no longer moves."); existing rules stay listed,
     each showing "Paused: delisted" with its toggle disabled (the server sends
     `paused_reason: "delisted"`; nothing is stored differently, so a relisted ticker resumes).

SOURCE SCAN only (testing.md §3; there is no XCTest target) — nothing here runs Swift, so a
subagent may run the whole file. Comments are stripped first (the fix's own comments name the
tokens asserted on), each check is brace-bound to the ONE declaration it is about, and
`test_each_guard_fails_on_its_mutation` breaks every guard IN MEMORY (no Swift file on disk is
ever edited) and asserts it then fails, so no guard here has only ever been seen passing.

Category 1 (pure) — no network, no Supabase.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"

_FILES: Dict[str, Path] = {
    "app_error": _IOS / "Core/Utilities/AppError.swift",
    "report_vm": _IOS / "ViewModels/TickerReportViewModel.swift",
    "report_view": _IOS / "Views/Screens/TickerReportView.swift",
    "research_vm": _IOS / "ViewModels/ResearchViewModel.swift",
    "models": _IOS / "Models/NotificationModels.swift",
    "row": _IOS / "Views/Molecules/PriceAlertRuleRow.swift",
    "sheet": _IOS / "Views/Screens/PriceAlertsSheet.swift",
    "detail_view": _IOS / "Views/Screens/TickerDetailView.swift",
    "store": _IOS / "Core/Services/PriceAlertStore.swift",
}

_CODE = "TICKER_DELISTED"

# Swift property → wire key, for the five Optional keys the alert DTO decodes.
_ALERT_KEYS: Dict[str, str] = {
    "listingStatus": "listing_status",
    "delistedDate": "delisted_date",
    "delistedExchange": "delisted_exchange",
    "lastTradeDate": "last_trade_date",
    "pausedReason": "paused_reason",
}


# ── extraction (the same rules as test_ios_delisted_listing.py) ──────────────


def _strip(src: str) -> str:
    """Drop `/* */` blocks and `//` comments. A `//` inside a string literal is kept."""
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    out = []
    for line in src.splitlines():
        in_str = False
        cut = len(line)
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\" and in_str:
                i += 2
                continue
            if ch == '"':
                in_str = not in_str
            elif not in_str and line.startswith("//", i):
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def _span(code: str, at: int, opener: str = "{", closer: str = "}") -> Tuple[int, int]:
    start = code.find(opener, at)
    if start == -1:
        raise AssertionError(f"no {opener!r} after offset {at} — this scan has drifted")
    depth = 0
    for i in range(start, len(code)):
        if code[i] == opener:
            depth += 1
        elif code[i] == closer:
            depth -= 1
            if depth == 0:
                return start, i
    raise AssertionError(f"unbalanced {opener}{closer} after offset {at}")


def _braced(code: str, decl: str) -> str:
    """The brace-matched body of the FIRST match of the regex `decl`, or an AssertionError."""
    m = re.search(decl, code)
    if not m:
        raise AssertionError(f"{decl!r} not found — this scan has drifted")
    open_, close = _span(code, m.start())
    return code[open_:close + 1]


def _at(text: str, token: str, start: int = 0) -> int:
    i = text.find(token, start)
    if i == -1:
        raise AssertionError(f"{token!r} not found — the guarded code is gone")
    return i


def _call(code: str, opener: str) -> str:
    """The FIRST `opener(` … `)` call in `code`, paren-balanced."""
    at = _at(code, opener)
    open_, close = _span(code, at + len(opener) - 1, "(", ")")
    return code[open_:close + 1]


def _flat(text: str) -> str:
    return " ".join(text.split())


def _drop_previews(code: str) -> str:
    out = code
    while True:
        m = re.search(r"#Preview\b", out)
        if not m:
            return out
        _, close = _span(out, m.end())
        out = out[:m.start()] + out[close + 1:]


def _arm(switch_body: str, case_label: str) -> str:
    """The text of one `case X:` arm of a switch, up to the next `case`/`default` at any depth."""
    start = _at(switch_body, case_label) + len(case_label)
    nxt = re.search(r"\n\s*(?:case\s|default\s*:)", switch_body[start:])
    end = start + nxt.start() if nxt else len(switch_body)
    return switch_body[start:end]


def _sources() -> Dict[str, str]:
    out = {}
    for key, path in _FILES.items():
        if not path.exists():
            pytest.fail(f"expected file is missing: {path}")
        out[key] = path.read_text(encoding="utf-8")
    return out


def _code(src: Dict[str, str], key: str) -> str:
    return _drop_previews(_strip(src[key]))


def _app_error(src: Dict[str, str]) -> str:
    return _braced(_code(src, "app_error"), r"enum AppError: Error, Identifiable, Equatable, Sendable \{")


# ── A. AppError ──────────────────────────────────────────────────────────────


def _check_the_code_maps_to_its_own_case(src: Dict[str, str]) -> None:
    enum = _app_error(src)
    assert re.search(r"\n\s*case tickerDelisted\(message: String\)\s*\n", enum), (
        "AppError has no `.tickerDelisted(message:)` case — the server's sentence has nowhere to live")
    mapper = _braced(enum, r"private static func mapAPIError\(_ error: APIError\) -> AppError")
    business = mapper[_at(mapper, "case .businessError(let code, let message):"):_at(mapper, "case .decodingError:")]
    branch = re.search(r'if code == "TICKER_DELISTED" \{\s*return \.tickerDelisted\(message: message\)\s*\}', business)
    assert branch, (
        "TICKER_DELISTED is not mapped to `.tickerDelisted(message: message)` in the businessError arm — "
        "it falls through to `.apiError`, whose action is a Retry that meets the same refusal")
    # Exactly once, and only in the business arm: an auth-arm copy would make it an auth error.
    assert mapper.count(f'"{_CODE}"') == 1, f"{_CODE} is mapped more than once in mapAPIError"
    assert f'"{_CODE}"' not in mapper[: mapper.index("case .businessError(let code, let message):")]


def _check_the_case_carries_the_server_sentence_and_goes_back(src: Dict[str, str]) -> None:
    enum = _app_error(src)
    message = _braced(enum, r"var message: String \{")
    arm = _arm(message, "case .tickerDelisted(let message):")
    assert re.search(r"return message\.isEmpty \? \"[^\"]+\" : message\s*$", arm.strip()), (
        "`.tickerDelisted` does not show the server's user_message verbatim")
    assert "\\(" not in arm, "iOS is composing its own delisting sentence instead of the server's"
    action = _braced(enum, r"var suggestedAction: ErrorAction \{")
    assert re.fullmatch(r"\s*return \.goBack\s*", _arm(action, "case .tickerDelisted:")), (
        "`.tickerDelisted` must offer Go Back — a Retry re-sends a request refused for good")
    for name in ("var title: String {", "var id: String {"):
        assert ".tickerDelisted" in _braced(enum, re.escape(name)), f"{name} has no `.tickerDelisted` arm"
    # `analyticsCode` lives in an extension below the enum.
    analytics = _braced(_code(src, "app_error"), r"var analyticsCode: String \{")
    assert re.search(r'case \.tickerDelisted:\s*return "ticker_delisted"', analytics), (
        "the refusal has no stable analytics code — every reportFailed/mutationFailed for it would "
        "read as some other failure")


def _check_the_case_is_not_retryable_or_auth(src: Dict[str, str]) -> None:
    code = _code(src, "app_error")
    for decl in (r"var isRetryable: Bool \{", r"var isAuthError: Bool \{", r"var isExpectedOffline: Bool \{"):
        assert "tickerDelisted" not in _braced(code, decl), (
            f"`.tickerDelisted` is listed in {decl!r} — a permanent refusal is neither retryable, an "
            "auth failure (that path can clear the session) nor an offline blip")
    refresh = _braced(code, r"var triggersTokenRefresh: Bool \{")
    assert _CODE not in refresh, "TICKER_DELISTED would trigger a token refresh and replay the request"


# ── B. the report screens show it, with no Retry ─────────────────────────────


def _check_the_report_screen_shows_the_server_sentence(src: Dict[str, str]) -> None:
    vm = _code(src, "report_vm")
    friendly = _braced(vm, r"private func userFriendlyError\(_ error: Error\) -> String \{")
    arm = _arm(friendly, f'case "{_CODE}":')
    assert re.fullmatch(r"\s*return AppError\.from\(apiError\)\.message\s*", arm), (
        "the report screen does not show the typed AppError's (server) sentence for TICKER_DELISTED")
    fetch = _braced(vm, r"private func _fetchReport\(allowPaidGeneration: Bool = true\) async \{")
    path_b = fetch[_at(fetch, ".getTickerReport("):]
    catch = _braced(path_b, r"\} catch \{")
    assert "self.error = self.userFriendlyError(error)" in catch
    assert "self.errorAction = AppError.from(error).suggestedAction" in catch, (
        "Path B's error action no longer follows AppError — `.goBack` would become a Retry")
    view = _code(src, "report_view")
    error_view = _braced(view, r"private func errorView\(_ message: String\) -> some View \{")
    go_back = _arm(error_view, "case .goBack:")
    assert "dismiss()" in go_back and "loadReport" not in go_back, (
        "the error view's `.goBack` arm is not a plain Go Back")
    assert "if viewModel.errorAction != .goBack {" in error_view


def _check_a_refused_regeneration_over_a_report_shows_the_banner(src: Dict[str, str]) -> None:
    """"Regenerate · 20 credits" is tapped over a report already on screen; Path B answers 409
    TICKER_DELISTED (refused before any charge). `body` renders `reportData` before `error`, so an
    `error` write there brought the old report back with no word — the banner is the one surface."""
    vm = _code(src, "report_vm")
    fetch = _braced(vm, r"private func _fetchReport\(allowPaidGeneration: Bool = true\) async \{")
    path_b = fetch[_at(fetch, ".getTickerReport("):]
    catch = _braced(path_b, r"\} catch \{")
    when_empty = _braced(catch, r"if self\.reportData == nil \{")
    assert "self.error = self.userFriendlyError(error)" in when_empty, (
        "Path B no longer shows its error view when there is no report to fall back on")
    assert "self.errorAction = AppError.from(error).suggestedAction" in when_empty
    after = catch[catch.index(when_empty) + len(when_empty):]
    assert re.match(r"\s*else \{", after), "Path B's `reportData == nil` branch has no else"
    over_report = _braced(after, r"else \{")
    assert re.fullmatch(r"\{\s*self\.transientError = AppError\.from\(error\)\.message\s*\}", over_report), (
        f"a Path B failure over a loaded report does not go to the banner alone: {_flat(over_report)!r}")
    rest = catch.replace(when_empty, "", 1).replace(over_report, "", 1)
    for write in ("self.error =", "self.errorAction =", "self.transientError ="):
        assert write not in rest, f"Path B's catch writes {write!r} outside its reportData branches"
    assert "self.isLoading = false" in rest, "Path B's catch no longer ends the loading state"


def _check_the_research_tab_shows_the_server_sentence(src: Dict[str, str]) -> None:
    gen = _braced(_code(src, "research_vm"), r"func generateAnalysis\(\) \{")
    failed = gen[_at(gen, "case .failed(let appError):"):]
    assert "self.error = appError.message" in failed, (
        "a refused generation no longer shows the AppError's (server) message in the Research alert")
    catch = gen[_at(gen, "let failure = AppError.from(error)"):]
    assert "self.error = failure.message" in catch


# ── C. the alert DTO ─────────────────────────────────────────────────────────


def _check_the_alert_dto_decodes_the_paused_keys(src: Dict[str, str]) -> None:
    dto = _braced(_code(src, "models"), r"struct PriceAlertDTO: Decodable, Identifiable, Hashable, Sendable \{")
    keys = _braced(dto, r"enum CodingKeys: String, CodingKey \{")
    for prop, wire in _ALERT_KEYS.items():
        assert re.search(rf"\n\s*let {prop}: String\?\s*\n", dto), (
            f"PriceAlertDTO.{prop} is missing or non-Optional — an older backend would fail the whole list")
        assert f'case {prop} = "{wire}"' in keys, f"PriceAlertDTO has no CodingKey for {wire}"
    paused = _braced(dto, r"var isPausedForDelisting: Bool \{")
    assert "DelistedListing.from(status: pausedReason," in _flat(paused), (
        "paused_reason is not read through the ONE wire rule (trimmed, any case, fail open)")
    copy = _braced(dto, r"func withIsActive\(_ newValue: Bool\) -> PriceAlertDTO \{")
    assert "pausedReason: pausedReason" in copy, "the optimistic toggle copy drops the paused state"
    quiet = _braced(dto, r"var quietReason: String\? \{")
    assert "guard !isPausedForDelisting else { return nil }" in quiet, (
        "a paused rule still promises it 're-arms when the price moves back'")


# ── D. the row (bell sheet AND Tracking → Alerts) ────────────────────────────


def _check_the_row_shows_paused_and_disables_its_toggle(src: Dict[str, str]) -> None:
    row = _braced(_code(src, "row"), r"struct PriceAlertRuleRow: View \{")
    assert "private var isPaused: Bool { alert.isPausedForDelisting }" in row
    assert "private var isLive: Bool { alert.isActive && !isPaused }" in row, (
        "a paused rule still draws the 'armed and waiting' glyph colour")
    activity = _flat(_call(row, "ActivityRow("))
    assert "iconColor: isLive ? AppColors.caution : AppColors.textMuted" in activity
    assert "footnote: alert.pausedCaption" in activity, "the row no longer says 'Paused: delisted'"
    controls = _braced(row, r"private var trailingControls: some View \{")
    toggle = controls[_at(controls, "Toggle("):_at(controls, "Button(action: onDelete)")]
    assert ".disabled(isPaused)" in toggle, "a paused rule's toggle is still live"
    assert ".disabled(" not in controls[_at(controls, "Button(action: onDelete)"):], (
        "delete must stay available on a paused rule")
    # The dim follows the user's on/off ALONE. An ON paused row keeps its caption at full
    # strength; an OFF row dims whether or not it is paused — the server sends paused_reason for
    # every rule on a delisted ticker (a fired one-shot rule included), and `|| isPaused` lifted
    # the dim from those, so an off row read as live beside its own "Turned off".
    assert "private var rowOpacity: Double { alert.isActive ? 1 : 0.55 }" in row, (
        "the row's dim no longer follows isActive alone — an ON paused row is dimmed below its "
        "caption's contrast bar, or an OFF paused row is drawn at full strength")
    assert row.count("rowOpacity") == 2, "rowOpacity is declared or applied more than once"
    assert ".opacity(rowOpacity)" in row, "the row no longer applies its on/off opacity"
    assert re.search(r"\.opacity\(", row.replace(".opacity(rowOpacity)", "")) is None, (
        "a second opacity in the row overrides the on/off dim")
    # An OFF paused rule: the subtitle still says "Turned off" (quietReason's first guard), and
    # the toggle stays disabled (`.disabled(isPaused)` above does not look at isActive).
    quiet = _braced(_code(src, "models"), r"var quietReason: String\? \{")
    assert _at(quiet, 'guard isActive else { return "Turned off" }') < _at(
        quiet, "guard !isPausedForDelisting else { return nil }"), (
        "an off paused rule lost its 'Turned off' subtitle — the off and paused states no longer agree")
    assert "isActive" not in toggle[_at(toggle, ".disabled("):], "the paused toggle lock now depends on isActive"
    # Both lists render this ONE row (no forked copy without the paused state).
    model = _code(src, "models")
    assert 'static let pausedDelistedCaption = "Paused: delisted"' in model


def _check_the_bell_does_not_badge_a_paused_rule(src: Dict[str, str]) -> None:
    store = _code(src, "store")
    bell = _braced(store, r"func hasActiveAlerts\(ticker: String, assetType: String\? = nil\) -> Bool \{")
    assert "$0.isActive && !$0.isPausedForDelisting" in bell, (
        "the header bell badges a rule the server is not watching, while its row draws it muted")
    # The quota count is the SERVER's (is_active only) — a paused rule still counts toward it.
    count = _braced(store, r"func activeCount\(ticker: String\? = nil, assetType: String\? = nil\) -> Int \{")
    assert "pool.filter(\\.isActive).count" in count and "isPausedForDelisting" not in count


# ── E. the bell sheet ────────────────────────────────────────────────────────


def _check_the_sheet_hides_the_form_when_delisted(src: Dict[str, str]) -> None:
    sheet = _braced(_code(src, "sheet"), r"struct PriceAlertsSheet: View \{")
    assert "private let isDelisted: Bool" in sheet, "the bell sheet has no isDelisted input"
    init = _braced(sheet, r"init\(ticker: String, assetType: String = \"stock\", isDelisted: Bool = false\)")
    assert "self.isDelisted = isDelisted" in init, "the sheet drops the caller's isDelisted"
    body = _braced(sheet, r"var body: some View \{")
    assert "createSection" in body and "createCard" not in body, (
        "the sheet's body draws the add form directly — a delisted ticker would get it")
    section = _braced(sheet, r"private var createSection: some View \{")
    m = re.fullmatch(r"\{\s*if isDelisted \{\s*delistedNotice\s*\} else \{\s*createCard\s*\}\s*\}", section)
    assert m, f"createSection is not `if isDelisted {{ delistedNotice }} else {{ createCard }}`: {_flat(section)!r}"
    notice = _flat(_braced(sheet, r"private var delistedNotice: some View \{"))
    assert "InlineRetryNotice(" in notice
    assert ('message: "Price alerts are paused — \\(viewModel.ticker) is delisted, " '
            '+ "so its price no longer moves."') in notice
    assert 'systemImage: "bell.slash"' in notice and "iconColor: AppColors.textMuted" in notice
    assert "onRetry" not in notice and "retryTitle" not in notice, "information, not a failure: no action"


def _check_the_detail_screen_passes_is_delisted(src: Dict[str, str]) -> None:
    view = _code(src, "detail_view")
    call = _flat(_call(view, "PriceAlertsSheet("))
    assert call == '(ticker: tickerSymbol, assetType: "stock", isDelisted: viewModel.delisting != nil)', (
        f"TickerDetailView does not tell the bell sheet the listing is delisted: {call!r}")


GUARDS: Dict[str, Callable[[Dict[str, str]], None]] = {
    "maps_to_case": _check_the_code_maps_to_its_own_case,
    "server_sentence_go_back": _check_the_case_carries_the_server_sentence_and_goes_back,
    "not_retry_or_auth": _check_the_case_is_not_retryable_or_auth,
    "report_screen": _check_the_report_screen_shows_the_server_sentence,
    "report_regen_banner": _check_a_refused_regeneration_over_a_report_shows_the_banner,
    "research_tab": _check_the_research_tab_shows_the_server_sentence,
    "alert_dto": _check_the_alert_dto_decodes_the_paused_keys,
    "row": _check_the_row_shows_paused_and_disables_its_toggle,
    "bell": _check_the_bell_does_not_badge_a_paused_rule,
    "sheet": _check_the_sheet_hides_the_form_when_delisted,
    "detail_passes": _check_the_detail_screen_passes_is_delisted,
}


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_the_guard_holds_on_the_real_source(name):
    GUARDS[name](_sources())


# ── each guard can fail ──────────────────────────────────────────────────────

# (guard, file key, exact text, replacement) — applied to an IN-MEMORY copy only.
MUTATIONS: List[Tuple[str, str, str, str]] = [
    ("maps_to_case", "app_error", "                return .tickerDelisted(message: message)\n",
     "                return .apiError(code: code, message: message)\n"),
    ("maps_to_case", "app_error", 'if code == "TICKER_DELISTED" {', 'if code == "TICKER_DELISTED_X" {'),
    ("maps_to_case", "app_error", "    case tickerDelisted(message: String)\n", "    case tickerDelisted(String)\n"),
    ("maps_to_case", "app_error", '            case "AUTH_REQUIRED":\n',
     '            case "TICKER_DELISTED":\n                return .tickerDelisted(message: message)\n'
     '            case "AUTH_REQUIRED":\n'),
    ("server_sentence_go_back", "app_error",
     "        case .tickerDelisted:\n            return .goBack\n",
     "        case .tickerDelisted:\n            return .retry\n"),
    ("server_sentence_go_back", "app_error",
     '            return message.isEmpty ? "This stock is no longer listed." : message\n',
     '            return "This stock is no longer listed."\n'),
    ("server_sentence_go_back", "app_error", 'case .tickerDelisted:      return "ticker_delisted"',
     'case .tickerDelisted:      return "unknown"'),
    ("not_retry_or_auth", "app_error", "        case .timeout, .serverError, .rateLimited, .authUnavailable:\n",
     "        case .timeout, .serverError, .rateLimited, .authUnavailable, .tickerDelisted:\n"),
    ("not_retry_or_auth", "app_error", "        case .unauthorized, .tokenExpired, .sessionEnded:\n            return true\n",
     "        case .unauthorized, .tokenExpired, .sessionEnded, .tickerDelisted:\n            return true\n"),
    ("report_screen", "report_vm",
     '                case "TICKER_DELISTED":\n                    return AppError.from(apiError).message\n',
     '                case "TICKER_DELISTED":\n                    return "Ticker \'\\(ticker)\' wasn\'t found."\n'),
    ("report_screen", "report_vm",
     '                case "TICKER_DELISTED":\n                    return AppError.from(apiError).message\n', ""),
    ("report_screen", "report_view",
     '                errorActionButton(icon: "chevron.left", title: "Go Back") { dismiss() }\n',
     '                errorActionButton(icon: "arrow.clockwise", title: "Retry") { viewModel.loadReport() }\n'),
    # The pre-fix shape: the catch wrote `error` whatever was on screen.
    ("report_regen_banner", "report_vm",
     "\n                self.transientError = AppError.from(error).message\n",
     "\n                self.error = self.userFriendlyError(error)\n"),
    ("report_regen_banner", "report_vm", "\n            if self.reportData == nil {\n",
     "\n            if self.reportData != nil {\n"),
    ("report_regen_banner", "report_vm",
     "            self.isLoading = false\n            if self.reportData == nil {\n",
     "            self.isLoading = false\n            self.error = self.userFriendlyError(error)\n"
     "            if self.reportData == nil {\n"),
    ("research_tab", "research_vm", "                                self.error = appError.message\n",
     '                                self.error = "Analysis failed."\n'),
    ("alert_dto", "models", "    let pausedReason: String?\n", "    let pausedReason: String\n"),
    ("alert_dto", "models", 'case pausedReason = "paused_reason"', 'case pausedReason = "pause_reason"'),
    ("alert_dto", "models", "            pausedReason: pausedReason\n", "            pausedReason: nil\n"),
    ("alert_dto", "models", "        guard !isPausedForDelisting else { return nil }\n", ""),
    ("row", "row", "            .disabled(isPaused)\n", ""),
    ("row", "row", "            footnote: alert.pausedCaption,\n", ""),
    ("row", "row", "private var isLive: Bool { alert.isActive && !isPaused }",
     "private var isLive: Bool { alert.isActive }"),
    # The Phase-2 bug the review found: pausing lifted the dim from an OFF rule.
    ("row", "row", "private var rowOpacity: Double { alert.isActive ? 1 : 0.55 }",
     "private var rowOpacity: Double { alert.isActive || isPaused ? 1 : 0.55 }"),
    ("row", "row", "        .opacity(rowOpacity)\n", "        .opacity(isPaused ? 1 : rowOpacity)\n"),
    ("row", "models", '        guard isActive else { return "Turned off" }\n', ""),
    ("row", "row", "            .disabled(isPaused)\n", "            .disabled(isPaused && alert.isActive)\n"),
    ("bell", "store", "contains { $0.isActive && !$0.isPausedForDelisting }", "contains(where: \\.isActive)"),
    ("sheet", "sheet", "                        createSection\n", "                        createCard\n"),
    ("sheet", "sheet", "        if isDelisted {\n            delistedNotice\n",
     "        if false {\n            delistedNotice\n"),
    ("sheet", "sheet", "            iconColor: AppColors.textMuted\n        )\n    }\n",
     "            iconColor: AppColors.textMuted,\n            onRetry: {}\n        )\n    }\n"),
    ("sheet", "sheet", "        self.isDelisted = isDelisted\n", "        self.isDelisted = false\n"),
    ("detail_passes", "detail_view", "isDelisted: viewModel.delisting != nil)", "isDelisted: false)"),
]


@pytest.mark.parametrize("guard, key, old, new", MUTATIONS,
                         ids=[f"{m[0]}-{i}" for i, m in enumerate(MUTATIONS)])
def test_each_guard_fails_on_its_mutation(guard, key, old, new):
    src = _sources()
    assert src[key].count(old) == 1, f"mutation anchor drifted ({src[key].count(old)} matches): {old!r}"
    src[key] = src[key].replace(old, new)
    with pytest.raises(AssertionError):
        GUARDS[guard](src)


def test_every_guard_has_a_mutation():
    assert {m[0] for m in MUTATIONS} == set(GUARDS)


def test_the_stripper_drops_the_comments_that_name_the_fix():
    """Anti-vacuity: the fix's comments name the very tokens asserted on, so a stripper that kept
    comments would let a guard pass on prose after the code was reverted."""
    raw = _FILES["app_error"].read_text(encoding="utf-8")
    assert "TICKER_DELISTED`, HTTP 409" in raw and "TICKER_DELISTED`, HTTP 409" not in _strip(raw)
    raw_row = _FILES["row"].read_text(encoding="utf-8")
    assert "PAUSED (delisting Phase 2" in raw_row and "PAUSED (delisting Phase 2" not in _strip(raw_row)


# ── the wire matches the backend ─────────────────────────────────────────────


def test_the_alert_dto_keys_are_the_backend_contract():
    """The four listing keys are the backend's ONE list (`app/schemas/listing_status.py`), plus
    `paused_reason`; `PriceAlertResponse` declares each with a None default, so an alert iOS
    decodes is never missing one (test_notification_schema_parity.py pins the subset rule)."""
    from app.schemas.listing_status import LISTING_FIELDS
    from app.schemas.price_alerts import PriceAlertResponse

    assert set(_ALERT_KEYS.values()) == set(LISTING_FIELDS) | {"paused_reason"}
    for field in _ALERT_KEYS.values():
        assert field in PriceAlertResponse.model_fields, f"PriceAlertResponse lacks {field}"
        assert PriceAlertResponse.model_fields[field].is_required() is False
        assert PriceAlertResponse.model_fields[field].default is None, f"{field} must default to None"


def test_the_error_code_exists_on_the_backend():
    """iOS maps a code the backend actually emits (invariant #3: a new ErrorCode + a typed iOS branch)."""
    from app.api.error_response import ErrorCode

    assert _CODE in {member.value for member in ErrorCode}, (
        f"iOS maps {_CODE} but the backend ErrorCode enum has no such member")


# MUTATION_LOG — every row of MUTATIONS is exercised by test_each_guard_fails_on_its_mutation on
# each run (in-memory copies only), so this module carries no hand-kept log.
