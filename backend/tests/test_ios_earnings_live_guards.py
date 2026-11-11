"""iOS source-scan guards for live earnings results (owner request 2026-10-29).

There is no XCTest target, so the iOS half of the feature is pinned here, the way every other
screen's contract is:
  * the wire: `EarningsLiveResponse` and its nested DTOs mirror the backend models key for
    key; `AlertDTO` and `TrackingFeedResponse` carry the new feed keys, and each
    reaches the card;
  * the route: `APIEndpoint.getEarningsLive` is the backend's path, signed-in only;
  * the honesty rule: LIVE is drawn from the SERVER's status, never from date math on the
    device (`LiveNewsHeader`: nothing live on screen, no "Live");
  * the poll: it stops when the server stops asking, its starter cancels before re-arming (two
    pollers would double the traffic), it never reads the status, and a failed first load retries
    by itself, bounded;
  * the route: "See live results" is offered only while the endpoint serves that report;
  * the sheet: its header follows the card's own answer, it claims nothing before the first
    answer, it says "Waiting…" only while the report is live, it stops saying LIVE when the
    device stops asking, and a held figure reads the same on the card and the sheet;
  * the previews: sample figures, no request, and no way into a production path;
  * the formatting defects the card shipped with ("$-0.25", "$0.0B") stay fixed;
  * the date parser reads the whole seconds the server sends;
  * the figures: each sits under its own label, and a beat and a miss never swap.
Every scan is comment-stripped first, so a doc comment naming a removed token cannot satisfy
or fail it.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.schemas.tracking import (
    AlertResponse,
    EarningsLiveFilingResponse,
    EarningsLiveMetricResponse,
    EarningsLiveQuarterResponse,
    EarningsLiveResponse,
    EarningsLiveYearAgoResponse,
    TrackingFeedResponse,
)
from app.services.earnings_live_service import _quarter_result
from app.utils.market_hours import previous_trading_day

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _strip_comments(src: str) -> str:
    """Block comments, whole-line comments and a `//` tail, spaced or glued to the code. The
    look-behind keeps the `//` of a URL in a string."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"(?<!:)//.*$", "", line))
    return "\n".join(out)


def _code(rel: str) -> str:
    path = _IOS / rel
    assert path.exists(), f"missing: {path}"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _norm(code: str) -> str:
    """One space between tokens, so a pin does not depend on where a call wraps."""
    return re.sub(r"\s+", " ", code)


def _struct_block(src: str, name: str) -> str:
    start = src.index(f"struct {name}")
    return src[start: src.index("\n}", start)]


def _bracketed(src: str, marker: str, opener: str = "{", closer: str = "}") -> str:
    """What the first `opener` after `marker` encloses, found by counting, so a nested type's body
    and a sibling's never leak into the scan."""
    start = src.index(opener, src.index(marker))
    depth = 0
    for at in range(start, len(src)):
        if src[at] == opener:
            depth += 1
        elif src[at] == closer:
            depth -= 1
            if depth == 0:
                return src[start + 1: at]
    raise AssertionError(f"no closing {closer} after {marker}")


def _coding_keys(src: str, name: str) -> set:
    block = _struct_block(src, name)
    keys = set(re.findall(r'case\s+\w+\s*=\s*"(\w+)"', block))
    enum_at = block.find("enum CodingKeys")
    if enum_at == -1:
        return keys
    for line in re.findall(r"case\s+([\w,\s]+)\n", block[enum_at:]):
        if "=" in line:
            continue
        keys |= {t.strip() for t in line.split(",") if t.strip()}
    return keys


def _body(src: str, opener: str) -> str:
    """The brace-balanced body of the declaration `opener` introduces, which ends at its `{`."""
    start = src.index(opener) + len(opener) - 1
    assert src[start] == "{", f"{opener!r} must end at its opening brace"
    depth = 0
    for i in range(start, len(src)):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start: i + 1]
    raise AssertionError(f"unbalanced braces after {opener!r}")


_DEBUG_BLOCK = re.compile(r"^[ \t]*#if DEBUG\n(.*?)^[ \t]*#endif[^\n]*\n?", re.S | re.M)


def _debug_only(src: str) -> str:
    return "\n".join(_DEBUG_BLOCK.findall(src))


def _release(src: str) -> str:
    """What a Release build compiles: the source with every `#if DEBUG` block gone."""
    return _DEBUG_BLOCK.sub("", src)


_MODELS = "Models/EarningsLiveModels.swift"
_TRACKING_MODELS = "Models/TrackingModels.swift"
_DESTINATIONS = "Models/AlertDestination.swift"
_HOURS = "Core/Utilities/MarketHoursUtil.swift"
_VM = "ViewModels/EarningsLiveViewModel.swift"
_CARD = "Views/Organisms/EarningsLiveResultsCard.swift"
_SCREEN = "Views/Screens/EarningsLiveScreen.swift"
_DETAIL = "Views/Screens/AlertDetailView.swift"
_CHIPS = "Views/Molecules/EarningsResultChips.swift"
_UPDATED = "func updated(with live: EarningsLiveResponse) -> AppAlert.EarningsData {"
_CARD_INIT = ("init(ticker: String, reportDate: String?, fallback: AppAlert.EarningsData? = nil,\n"
              "         onAnswer: ((EarningsLiveResponse) -> Void)? = nil,\n"
              "         onPollEnded: ((Bool) -> Void)? = nil) {")


def test_a_comment_cannot_satisfy_a_scan_however_it_is_glued_on():
    assert _strip_comments("if a {// token") == "if a {"
    assert _strip_comments("if a { // token") == "if a { "
    assert _strip_comments("    // token\nlet b = 1") == "let b = 1"
    assert _strip_comments("let a = 1 /* token */ + 2") == "let a = 1  + 2"
    url = 'let u = "https://example.test/a"'
    assert _strip_comments(url) == url, "a URL in a string is code"
    assert _strip_comments(url + "// token") == url


@pytest.mark.parametrize("swift_name,model", [
    ("EarningsLiveResponse", EarningsLiveResponse),
    ("EarningsLiveMetricDTO", EarningsLiveMetricResponse),
    ("EarningsLiveQuarterDTO", EarningsLiveQuarterResponse),
    ("EarningsLiveYearAgoDTO", EarningsLiveYearAgoResponse),
    ("EarningsLiveFilingDTO", EarningsLiveFilingResponse),
])
def test_the_live_dtos_mirror_the_backend_models_key_for_key(swift_name, model):
    ios = _coding_keys(_code(_MODELS), swift_name)
    backend = set(model.model_fields)
    assert ios == backend, (
        f"{swift_name} vs {model.__name__}: iOS-only {sorted(ios - backend)}, "
        f"backend-only {sorted(backend - ios)}"
    )


def test_the_alert_dto_decodes_every_live_card_key():
    ios = _coding_keys(_code("Models/TrackingModels.swift"), "AlertDTO")
    live_keys = {
        "earnings_status", "report_date", "eps_actual", "revenue_actual", "eps_surprise_pct",
        "revenue_surprise_pct", "eps_basis", "results_preliminary", "results_checked_at",
        "eps_checking", "revenue_checking",
    }
    assert live_keys <= set(AlertResponse.model_fields), "backend dropped a live key"
    assert live_keys <= ios, f"AlertDTO is missing {sorted(live_keys - ios)}"
    dto = _struct_block(_code(_TRACKING_MODELS), "AlertDTO")
    for key in sorted(live_keys):
        head, *rest = key.split("_")
        field = head + "".join(word.capitalize() for word in rest)
        assert f'case {field} = "{key}"' in dto, f"{key} is decoded into the wrong field"


def test_the_feed_row_reaches_the_card_field_by_field():
    """A decoded key proves nothing until it reaches the card. A `report_date` lost here takes the
    results card off the sheet with no error, and a lost status reads every report as upcoming."""
    dto = _norm(_body(_code(_TRACKING_MODELS), "func toAppAlert() -> AppAlert {"))
    assert ('return .earnings(AppAlert.EarningsData( ticker: ticker ?? "", '
            'companyName: companyName ?? "", reportTime: mappedTime, consensus: consensus, '
            'day: day ?? 0, month: month ?? "", status: EarningsLiveStatus(wire: earningsStatus), '
            'reportDate: reportDate, epsEstimate: epsEstimate, revenueEstimate: revenueEstimate, '
            'epsActual: epsActual, revenueActual: revenueActual, epsSurprisePct: epsSurprisePct, '
            'revenueSurprisePct: revenueSurprisePct, epsIsGAAP: epsBasis == "gaap", '
            'isPreliminary: resultsPreliminary ?? false, '
            'checkedAt: EarningsLiveDates.parse(resultsCheckedAt), '
            'epsChecking: epsChecking ?? false, revenueChecking: revenueChecking ?? false ))') in dto


def test_the_feed_response_decodes_the_recent_earnings_list():
    ios = _coding_keys(_code("Models/TrackingModels.swift"), "TrackingFeedResponse")
    assert ios == set(TrackingFeedResponse.model_fields)
    vm = _code("ViewModels/TrackingViewModel.swift")
    assert "feed.recentEarningsAlerts" in vm, "the previous day's cards are decoded and dropped"


def test_the_endpoint_is_the_backend_route_and_sign_in_only():
    src = _code("Core/Services/APIEndpoint.swift")
    assert 'return "/api/v1/tracking/earnings/\\(ticker)/live"' in src
    assert '["report_date": reportDate]' in src
    policy = src[src.index("var authPolicy: AuthPolicy"):]
    # The arm that holds the Tracking feed itself: from its `case` line to its `return`.
    at = policy.index(".getTrackingAssets")
    arm_start = policy.rfind("case ", 0, at)
    arm = policy[arm_start: policy.index("return ", at)]
    tail = policy[policy.index("return ", at):].split("\n", 1)[0]
    assert ".getEarningsLive" in arm, "the live endpoint is not in the Tracking feed's arm"
    assert tail.strip() == "return .signInRequired", tail


def test_live_is_drawn_from_the_server_status_only():
    models = _code("Models/TrackingModels.swift")
    body = models[models.index("var isLiveEarnings: Bool"):]
    body = body[: body.index("\n    }")]
    assert "data.status == .live" in body
    for token in ("Date()", "Calendar", "isMarketActive", "hour"):
        assert token not in body, f"LIVE is inferred on the device again ({token})"
    card = _code("Views/Molecules/AlertCardView.swift")
    assert "leadingBadge: alert.isLiveEarnings ? .live : nil" in card
    assert "ActivityRow(" in card


def test_the_status_decoder_falls_back_to_upcoming():
    """An older backend sends no status; that is exactly the card this build used to draw."""
    src = _code(_MODELS)
    assert 'EarningsLiveStatus(rawValue: (wire ?? "").lowercased()) ?? .upcoming' in src
    assert 'case notScheduled = "none"' in src, "a case named `none` collides with Optional"


def test_the_sheet_poll_stops_when_the_server_stops_asking():
    vm = _code(_VM)
    start = vm[vm.index("func start()"): vm.index("func stop()")]
    assert start.index("pollTask?.cancel()") < start.index("pollTask = Task"), (
        "re-arming leaves the previous poller running"
    )
    assert "pollTask?.cancel()" in _body(vm, "func stop() {")
    assert "guard let seconds = self.nextPollSeconds else {" in start
    loop = _body(start, "while !Task.isCancelled {")
    assert loop.index("guard !Task.isCancelled else { break }") < loop.index("self.nextPollSeconds"), (
        "a cancelled load is no answer: it may not end the poll that replaced it"
    )
    assert "let wait = UInt64(max(seconds, Self.minimumPollSeconds)) * 1_000_000_000" in loop, (
        "`minimumPollSeconds` is a floor: a `min` makes it a ceiling"
    )
    assert "private static let minimumPollSeconds = 15" in vm, "a malformed cadence hammers the route"
    assert "try? await Task.sleep(nanoseconds: wait)" in loop, "the retry fires with no wait"
    next_poll = _norm(_body(vm, "private var nextPollSeconds: Int? {"))
    assert ("if let live { guard let seconds = live.pollSeconds, seconds > 0 else { return nil } "
            "return seconds }") in next_poll, (
        "poll_seconds null must end the poll, and any other value is the cadence: 120 s while "
        "FMP confirms a reported report must not turn into the retry's 30 s"
    )
    assert "tatus" not in vm, (
        "the server's cadence alone decides: an `upcoming` or `awaiting` answer on a day nobody "
        "has read carries a poll, and a view model that reads the status would stop on it"
    )
    card = _norm(_code(_CARD))
    for hook in (".onAppear { viewModel.start() }", ".onDisappear { viewModel.stop() }",
                 "willResignActiveNotification)) { _ in viewModel.stop() }",
                 "didBecomeActiveNotification)) { _ in viewModel.start() }"):
        assert hook in card, f"the live card lost its lifecycle hook: {hook}"


def test_the_sheet_hosts_the_live_card_without_changing_its_own_signature():
    detail = _code("Views/Screens/AlertDetailView.swift")
    assert "EarningsLiveResultsCard(ticker: data.ticker, reportDate: data.reportDate," in detail
    assert "var onOpen: (AlertDestination) -> Void" in detail


def test_the_sheet_opens_on_the_tap_figures_and_the_live_answer_then_wins():
    """The sheet is never blank while the first answer loads, and from that answer on nothing
    on it may go on reading the copy taken at the tap."""
    detail = _norm(_body(_code(_DETAIL),
                         "private func earningsDetail(_ data: AppAlert.EarningsData) -> some View {"))
    assert ("fallback: data, onAnswer: { liveAnswer = $0 }, onPollEnded: { liveCheckEnded = $0 })"
            in detail)
    card = _code(_CARD)
    init = _body(card, _CARD_INIT)
    for stored in ("self.fallback = fallback", "self.onAnswer = onAnswer",
                   "self.onPollEnded = onPollEnded"):
        assert stored in init, f"the card drops what its host gave it: {stored}"
    for opener, order in (
        ("private var status: EarningsLiveStatus {",
         "live?.liveStatus ?? fallback?.status ?? .upcoming"),
        ("private var eps: EarningsMetricOutcome {", "live?.epsOutcome ?? fallback?.epsOutcome"),
        ("private var revenue: EarningsMetricOutcome {",
         "live?.revenueOutcome ?? fallback?.revenueOutcome"),
        ("private var isPreliminary: Bool {",
         "live?.preliminary ?? fallback?.isPreliminary ?? false"),
        ("private var checkedAt: Date? {", "live?.checkedDate ?? fallback?.checkedAt"),
        ("private var companyName: String {", 'live?.companyName ?? fallback?.companyName ?? ""'),
    ):
        assert order in _norm(_body(card, opener)), f"the answer must win: {order}"


def test_the_report_date_reaches_the_endpoint_from_both_doors():
    """Drop it anywhere on the way and the server picks a report itself: a Friday report opened
    on Monday with no held record reads next quarter's estimates."""
    screen = _body(_code(_SCREEN), "init(ticker: String, reportDate: String?) {")
    assert "self.card = EarningsLiveResultsCard(ticker: ticker, reportDate: reportDate)" in screen
    assert ("StateObject(wrappedValue: EarningsLiveViewModel(ticker: ticker, reportDate: reportDate))"
            in _norm(_body(_code(_CARD), _CARD_INIT)))
    vm = _code(_VM)
    assert "self.reportDate = reportDate" in _body(
        vm, "init(ticker: String, reportDate: String?, apiClient: APIClient = .shared) {")
    assert "endpoint: .getEarningsLive(ticker: ticker, reportDate: reportDate)," in _body(
        vm, "private func load() async {")
    assert 'guard let reportDate, !reportDate.isEmpty else { return nil }' in _code(
        "Core/Services/APIEndpoint.swift")


def test_the_push_offers_live_results_first_and_the_cover_opens_them():
    dest = _code("Models/AlertDestination.swift")
    assert "case earningsLive(ticker: String, reportDate: String?)" in dest
    assert 'label: "See live results"' in dest
    first = dest.index('label: "See live results"')
    assert first < dest.index('label: "Open \\(symbol)"'), "live results must lead"
    cover = _code("Views/Modifiers/AlertDestinationCover.swift")
    assert "EarningsLiveScreen(ticker: ticker, reportDate: reportDate)" in cover


def test_the_consensus_formatting_defects_stay_fixed():
    fmt = _code(_MODELS)
    consensus = _body(
        fmt, "static func consensusText(epsEstimate: Double?, revenueEstimate: Double?) -> String {")
    assert '"EPS Est: \\(EarningsFigureFormat.eps(eps))"' in consensus, "prints a loss as $-0.25"
    assert '"Rev Est: \\(EarningsFigureFormat.revenue(revenue))"' in consensus, "prints $23.5M as $0.0B"
    for code in (fmt, _code(_TRACKING_MODELS)):
        assert 'String(format: "EPS Est: $%.2f"' not in code
        assert 'String(format: "Rev Est: $%.1fB"' not in code
    eps = fmt[fmt.index("static func eps(_ value: Double) -> String"):]
    eps = eps[: eps.index("\n    }")]
    assert 'let sign = value < 0 ? "-" : ""' in eps, "the EPS sign goes after the $ again"
    assert 'String(format: "$%.2f", abs(value))' in eps
    assert 'String(format: "$%.1fM", v / 1_000_000)' in fmt


def test_a_figure_the_server_gave_no_verdict_is_never_coloured():
    """A figure with no surprise beside its consensus is one the server refused to judge: a
    print that covers the consensus, from either source, or a consensus under a cent. Comparing
    the raw values would paint a verdict the card, the sheet and the push cannot share: the card
    only has the report-wide preliminary flag, so the rule reads the server's answer and no
    source. A surprise of 0.0 IS an answer, so a beat that rounds to 0.00 stays a beat."""
    block = _struct_block(_code(_MODELS), "EarningsMetricOutcome")
    body = block[block.index("var result: EarningsQuarterResult"):block.index("var color: Color")]
    rule = "if surprisePct == nil { return .noEstimate }"
    assert body.index("guard !isGAAP, let estimate else") < body.index(rule)
    assert body.index(rule) < body.index("actual > estimate")
    assert "surprisePct == 0" not in body
    assert "isPreliminary" not in body, "the card and the sheet pass this flag differently"


@pytest.mark.parametrize("rel,scope,outcome,passed", [
    (_MODELS, "extension EarningsLiveResponse", "var epsOutcome", "eps.surprisePct"),
    (_MODELS, "extension EarningsLiveResponse", "var revenueOutcome", "revenue.surprisePct"),
    ("Models/TrackingModels.swift", "struct EarningsData", "var epsOutcome", "epsSurprisePct"),
    ("Models/TrackingModels.swift", "struct EarningsData", "var revenueOutcome",
     "revenueSurprisePct"),
])
def test_each_outcome_is_handed_its_own_surprise(rel, scope, outcome, passed):
    """`EarningsMetricOutcome.result` reads the surprise as the server's answer, so a nil here turns
    every figure of that surface into a grey "In", with nothing else to show it."""
    body = _bracketed(_bracketed(_code(rel), scope), outcome)
    assert re.search(rf"\bsurprisePct:\s*{re.escape(passed)}\s*[,)]", body), body


@pytest.mark.parametrize("label,dto_field", [
    ("epsSurprisePct", "epsSurprisePct"),
    ("revenueSurprisePct", "revenueSurprisePct"),
])
def test_the_feed_hands_the_card_each_surprise_it_decoded(label, dto_field):
    models = _code("Models/TrackingModels.swift")
    call = _bracketed(models, ".earnings(AppAlert.EarningsData", "(", ")")
    assert re.search(rf"\b{label}:\s*{dto_field}\s*,", call), call


def test_the_date_parser_reads_the_whole_seconds_the_server_sends():
    """`iso_utc` sends "2026-10-29T20:05:00Z". A formatter asking for fractional seconds refuses
    that, the date comes back nil and "updated N min ago" goes, with no crash and no log."""
    block = _bracketed(_code(_MODELS), "enum EarningsLiveDates")
    plain = _bracketed(block, "private static let plain")
    assert "f.formatOptions = [.withInternetDateTime]" in plain
    assert re.search(r"return\s+plain\.date\(from:\s*raw\)", _bracketed(block, "func parse("))






def test_the_colour_word_and_arrow_all_come_from_the_one_verdict():
    block = _struct_block(_code(_MODELS), "EarningsMetricOutcome")
    for name, end in (("var color: Color", "var word: String?"),
                      ("var word: String?", "var surpriseText: String?"),
                      ("var arrowSymbol: String?", None)):
        start = block.index(name)
        body = block[start: block.index(end) if end else len(block)]
        assert "switch result {" in body, f"{name} works its own verdict out"
        assert "actual" not in body and "estimate" not in body, f"{name} compares raw figures"


def test_the_poll_keeps_running_for_a_live_card_after_twenty_et():
    vm = _code("ViewModels/TrackingViewModel.swift")
    timer = vm[vm.index("func startPriceRefreshTimer()"): vm.index("func stopPriceRefreshTimer()")]
    assert "self.alerts.contains(where: \\.isLiveEarnings)" in timer
    assert "hasLiveEarnings || MarketHoursUtil.shouldRefreshPrices(" in timer


def test_a_result_row_is_hidden_only_for_the_same_report_on_screen():
    """Ticker AND report date: last quarter's result row stays while next quarter's card is
    up, and a row without a report date (sent before the route carried it) is never hidden."""
    tab = _code("Views/Organisms/AlertsTabContent.swift")
    body = tab[tab.index("private func duplicatesLiveResultCard("):]
    body = body[: body.index("\n    }")]
    assert "AlertDestination.isEarningsResult(item.kind)" in body
    assert 'let date = item.route["report_date"], !date.isEmpty else { return false }' in body
    assert 'upcomingReports.contains("\\(ticker)|\\(date)")' in body
    section = tab[tab.index("private func activitySection()"):]
    assert "&& !duplicatesLiveResultCard($0, upcomingReports: upcomingReports)" in section
    upcoming = tab[tab.index("private func duplicatesUpcomingCard("):]
    upcoming = upcoming[: upcoming.index("\n    }")]
    assert "isEarningsResult" not in upcoming, "the upcoming rule must stay ticker-scoped"


def test_the_outlook_is_never_cut_on_the_card():
    """The server prints the company's sentence whole, up to the 400 characters a quote may be, and
    refuses any quote it would have to shorten. A line limit here would undo that, and a limit on
    the card's body reaches every text inside it."""
    src = _code("Views/Organisms/EarningsLiveResultsCard.swift")
    block = _bracketed(src, "func guidanceBlock")
    assert "Text(guidance)" in block
    assert ".fixedSize(horizontal: false, vertical: true)" in block
    for cut in (".lineLimit", ".truncationMode", ".prefix("):
        assert cut not in block
        assert cut not in _bracketed(src, "var body: some View")


def test_every_file_using_published_imports_combine():
    """`SWIFT_UPCOMING_FEATURE_MEMBER_IMPORT_VISIBILITY` is on, so a file cannot lean on the
    Combine that `import SwiftUI` drags in: `@Published` / `ObservableObject` need their own
    `import Combine`. EarningsLiveViewModel shipped without it, written without a compiler."""
    project = _IOS.parent / "ios.xcodeproj/project.pbxproj"
    assert "SWIFT_UPCOMING_FEATURE_MEMBER_IMPORT_VISIBILITY = YES" in project.read_text(
        encoding="utf-8"
    ), "the build setting that makes this guard necessary is gone: retire the guard with it"
    missing = []
    scanned = 0
    for path in sorted(_IOS.parent.rglob("*.swift")):
        code = _code(str(path.relative_to(_IOS))) if _IOS in path.parents else None
        if code is None:
            src = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
            code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))
        if not re.search(r"@Published\b|\bObservableObject\b", code):
            continue
        scanned += 1
        if not re.search(r"^\s*(?:@\w+\s+)?import\s+Combine\b", code, re.M):
            missing.append(str(path.relative_to(_IOS.parent)))
    assert scanned >= 40, f"the scan found only {scanned} files: it is not reading the tree"
    assert not missing, f"these files use @Published/ObservableObject with no import Combine: {missing}"


# ── the route ────────────────────────────────────────────────────────────────


def test_live_results_are_offered_only_while_the_endpoint_serves_the_report():
    """The endpoint answers 400 for a date before the previous trading day, and a row with no
    date is never the report it is about, so both keep Open and See the numbers only."""
    dest = _code(_DESTINATIONS)
    arm = dest[dest.index("if isEarningsResult(item.kind)"): dest.index('label: "See live results"')]
    assert 'let reportDate = route["report_date"]' in arm
    assert "reportDate >= MarketHoursUtil.previousTradingDayKey()" in arm
    assert ".flatMap" not in arm, "a row with no date may not reach the screen"
    assert ".earningsLive(ticker: symbol, reportDate: reportDate)" in dest

    helper = _body(_code(_HOURS),
                   "nonisolated static func previousTradingDayKey(at instant: Date = Date()) -> String {")
    assert "let calendar: Calendar = etCalendar" in helper, (
        "the floor is the date in New York, not on the device"
    )
    # Whole statements: a second walk inside this one offers a date the endpoint refuses.
    assert ("let day: Date = previousTradingDay(before: calendar.startOfDay(for: instant), "
            "calendar: calendar)") in helper
    assert "calendar.dateComponents([.year, .month, .day], from: day)" in helper
    assert ('return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, '
            'parts.day ?? 0)') in helper


def test_the_route_floor_walks_the_calendar_the_endpoint_refuses_by():
    """iOS may hide a date the server still serves, but it must never offer one it refuses. The
    Swift walk, read from the Swift holiday table, against the backend's own function."""
    hours = _code(_HOURS)
    table = hours[hours.index("holidays: Set<String> = ["):]
    table = table[: table.index("]")]
    holidays = set(re.findall(r'"(\d{4}-\d{2}-\d{2})"', table))
    assert len(holidays) >= 30, "the holiday table was not read"

    def swift_walk(day: date) -> date:
        probe = day
        for _ in range(10):
            probe -= timedelta(days=1)
            if probe.weekday() < 5 and probe.isoformat() not in holidays:
                break
        return probe

    day = date(2025, 1, 2)
    while day <= date(2028, 12, 31):
        assert swift_walk(day) == previous_trading_day(day), day.isoformat()
        day += timedelta(days=1)


# ── the sheet's retry ────────────────────────────────────────────────────────


def test_a_failed_first_load_keeps_trying_and_the_sheet_says_so():
    vm = _code(_VM)
    start = _body(vm, "func start() {")
    assert "self.live?.pollSeconds" not in start, "a failed first load ends the poll again"
    next_poll = _body(vm, "private var nextPollSeconds: Int? {")
    assert next_poll.index("if let live") < next_poll.index("Self.noAnswerRetrySeconds"), (
        "a real answer must rule before the retry is even considered"
    )
    assert "private static let noAnswerRetrySeconds = 30" in vm
    assert "fallback" not in next_poll.lower(), (
        "a status guessed from the Tracking card would poll a finished report for ever"
    )
    load = _body(vm, "private func load() async {")
    answered = load[: load.index("} catch {")]
    assert "live = response" in answered, "the card reads nothing but `live`"
    assert "loadFailed = false" in answered, (
        "a retry that recovers leaves the failure notice under the real answer"
    )
    card = _code(_CARD)
    body = _body(card, "var body: some View {")
    notice = _body(body, "if viewModel.loadFailed {")
    assert "InlineRetryNotice(" in notice
    assert "onRetry: { viewModel.start() }" in notice, "Try Again must ask again"
    assert "viewModel.loadFailed && fallback == nil" not in card, (
        "the sheet always has a fallback, so this hides the notice from it"
    )


def test_the_retry_is_bounded_and_a_refusal_ends_the_poll_at_once():
    """A report_date ages out under an open sheet, and the 400 it then gets is permanent."""
    vm = _code(_VM)
    next_poll = _body(vm, "private var nextPollSeconds: Int? {")
    assert "guard loadFailed, failedLoads < Self.maxFailedLoads else { return nil }" in next_poll
    bound = re.search(r"private static let maxFailedLoads = (\d+)\b", vm)
    assert bound and 1 < int(bound.group(1)) <= 40, (
        "a bound of minutes, not one retry and not years at 30 s a try"
    )
    assert next_poll.index("if refused { return nil }") < next_poll.index("if let live"), (
        "a refusal must also end a poll running over a shown answer"
    )
    load = _body(vm, "private func load() async {")
    assert "if case .validationFailed = appError { refused = true }" in load
    assert "if live == nil { failedLoads += 1 }" in load
    assert "loadFailed = live == nil || refused" in load
    start = _body(vm, "func start() {")
    for reset in ("loadFailed = false", "failedLoads = 0", "refused = false"):
        assert start.index(reset) < start.index("pollTask = Task"), (
            f"Try Again must start clean: {reset}"
        )


def test_a_cancelled_load_is_dropped_before_the_error_is_mapped():
    load = _body(_code(_VM), "private func load() async {")
    catch = load[load.index("} catch {"):]
    assert catch.index("guard !Task.isCancelled else { return }") < catch.index("AppError.from(error)")
    assert "isCancellation" not in load


# ── the sheet's header ───────────────────────────────────────────────────────


def test_the_sheet_header_reads_the_cards_own_answer():
    detail = _code(_DETAIL)
    body = _body(detail, "var body: some View {")
    assert "Text(alert.title)" not in body and "Text(alert.description)" not in body
    assert body.count("Text(headerAlert.title)") == 2
    assert body.count("Text(headerAlert.description)") == 1
    # The whole getter, down to what it returns: the answer written onto a local copy reaches
    # nothing unless that copy is what the sheet draws.
    assert _norm(_body(detail, "private var headerAlert: AppAlert {")) == (
        "{ guard case .earnings(var data) = alert else { return alert } "
        "if let liveAnswer { data = data.updated(with: liveAnswer) } "
        "data.liveCheckEnded = liveCheckEnded "
        "return .earnings(data) }"
    )
    assert "switch headerAlert {" in _body(detail, "private var detailContent: some View {"), (
        "the Date and Consensus rows must read the same answer as the header above them"
    )
    assert "onAnswer: { liveAnswer = $0 }" in detail

    card = _code(_CARD)
    hook = card[card.index(".onChange(of: viewModel.live)"):]
    hook = hook[: hook.index("\n        }")]
    assert "if let answer { onAnswer?(answer) }" in hook


def test_the_header_is_rebuilt_from_every_field_it_reads():
    tracking = _code(_TRACKING_MODELS)
    read = set(re.findall(r"\bdata\.(\w+)", (
        _body(tracking, "static func earningsDescription(_ data: EarningsData) -> String {")
        + _body(tracking, "static func earningsResultsLine(_ data: EarningsData) -> String {")
    )))
    assert {"status", "epsActual", "revenueActual", "consensus", "reportTime"} <= read, read
    derived = {"formattedMonth": "month", "formattedDay": "day", "hasResults": "epsActual"}
    # `liveCheckEnded` is no answer's to give: the sheet sets it on this copy (`headerAlert`).
    fields = {derived.get(name, name) for name in read} - {"liveCheckEnded"}
    updated = _norm(_body(_code(_MODELS), _UPDATED))
    missing = sorted(f for f in fields if f"{f}:" not in updated)
    assert not missing, f"updated(with:) leaves the tap's copy of {missing}"
    assert "let answer: EarningsLiveStatus = live.liveStatus" in updated
    assert "let eps: EarningsMetricOutcome = live.epsOutcome" in updated
    assert "let revenue: EarningsMetricOutcome = live.revenueOutcome" in updated
    # The whole call, never label-and-value pairs: `epsEstimate: eps.estimate,` also matches the
    # `consensusText(...)` argument just above it, so a pair cannot see a stale estimate.
    assert updated[updated.index("return AppAlert.EarningsData("):] == (
        "return AppAlert.EarningsData( ticker: ticker, "
        "companyName: live.companyName ?? companyName, "
        "reportTime: sameReport ? reportTime : nil, "
        "consensus: Self.consensusText(epsEstimate: eps.estimate, revenueEstimate: revenue.estimate), "
        "day: shownDay, month: shownMonth, status: answer, reportDate: live.reportDate, "
        "epsEstimate: eps.estimate, revenueEstimate: revenue.estimate, "
        "epsActual: eps.actual, revenueActual: revenue.actual, "
        "epsSurprisePct: eps.surprisePct, revenueSurprisePct: revenue.surprisePct, "
        "epsIsGAAP: eps.isGAAP, isPreliminary: live.preliminary ?? isPreliminary, "
        "checkedAt: live.checkedDate ?? checkedAt, "
        "epsChecking: eps.isChecking, revenueChecking: revenue.isChecking ) }"
    ), "updated(with:) must copy every field from the answer, each to its own label"


def test_each_metric_reads_its_own_figures():
    """The card, the chips and the header all read these. A crossed field prints one metric's
    figure under the other's name, and a lost basis colours a GAAP EPS as a beat or a miss."""
    answer = _code(_MODELS)
    assert _norm(_body(answer, "var epsOutcome: EarningsMetricOutcome {")) == (
        "{ EarningsMetricOutcome( estimate: eps.estimate, actual: eps.actual, "
        'surprisePct: eps.surprisePct, isPreliminary: eps.source == "filing", '
        'isGAAP: eps.basis == "gaap", isChecking: eps.checking ?? false ) }'
    )
    assert _norm(_body(answer, "var revenueOutcome: EarningsMetricOutcome {")) == (
        "{ EarningsMetricOutcome( estimate: revenue.estimate, actual: revenue.actual, "
        'surprisePct: revenue.surprisePct, isPreliminary: revenue.source == "filing", '
        "isChecking: revenue.checking ?? false ) }"
    )
    feed = _code(_TRACKING_MODELS)
    assert _norm(_body(feed, "var epsOutcome: EarningsMetricOutcome {")) == (
        "{ EarningsMetricOutcome(estimate: epsEstimate, actual: epsActual, "
        "surprisePct: epsSurprisePct, isPreliminary: isPreliminary, "
        "isGAAP: epsIsGAAP, isChecking: epsChecking) }"
    )
    assert _norm(_body(feed, "var revenueOutcome: EarningsMetricOutcome {")) == (
        "{ EarningsMetricOutcome(estimate: revenueEstimate, actual: revenueActual, "
        "surprisePct: revenueSurprisePct, isPreliminary: isPreliminary, "
        "isChecking: revenueChecking) }"
    )


def test_an_answer_about_another_report_never_repaints_this_ones_figures():
    updated = _norm(_body(_code(_MODELS), _UPDATED))
    # Every statement before the copy, conditions and order included: a branch can be switched
    # off with all its statements still in place, so naming the statements proves nothing.
    assert updated[: updated.index("return AppAlert.EarningsData(")] == (
        "{ let answer: EarningsLiveStatus = live.liveStatus "
        "let sameReport: Bool = live.reportDate == reportDate "
        # Figures on screen outrank `none` and another date, before anything is copied.
        "if hasResults, answer == .notScheduled || !sameReport { return self } "
        # `none` with no figures takes effect, or the header promises a report the card denies.
        "if answer == .notScheduled { var unscheduled = self "
        "unscheduled.status = .notScheduled return unscheduled } "
        # A moved report takes its new date, whole, or the old date sits by the new status. Any
        # other answer keeps the card's own, or the Date row reads "OCT 0".
        "var shownDay: Int = day var shownMonth: String = month "
        "if !sameReport { let moved = Self.dayAndMonth(of: live.reportDate) "
        'shownDay = moved?.day ?? 0 shownMonth = moved?.month ?? "" } '
        "let eps: EarningsMetricOutcome = live.epsOutcome "
        "let revenue: EarningsMetricOutcome = live.revenueOutcome "
    )
    assert "reportTime: sameReport ? reportTime : nil" in updated, (
        "the time of day belongs to the date the card was drawn for"
    )
    assert "day: shownDay, month: shownMonth," in updated, "the copy prints the date chosen above"
    models = _code(_MODELS)
    day_and_month = _norm(_body(
        models, "private static func dayAndMonth(of reportDate: String) -> (day: Int, month: String)? {"))
    assert "guard parts.count == 3, let month = Int(parts[1]), let day = Int(parts[2])," in day_and_month
    assert "(1...12).contains(month), (1...31).contains(day) else { return nil }" in day_and_month, (
        "month 13 would index past the table and crash the sheet"
    )
    table = models[models.index("private static let monthNames"):]
    months = re.findall(r'"([A-Z]{3})"', table[: table.index("]\n")])
    assert months == [m.upper() for m in calendar.month_abbr[1:]], "the feed's month names"
    assert "return (day: day, month: monthNames[month - 1])" in models


def test_the_card_and_the_header_build_the_consensus_with_one_formatter():
    tracking = _code(_TRACKING_MODELS)
    assert "consensusParts" not in tracking
    dto = _body(tracking, "func toAppAlert() -> AppAlert {")
    assert "AppAlert.EarningsData.consensusText(epsEstimate: epsEstimate," in dto


def test_no_scheduled_report_has_its_own_sentence():
    """A sheet saying "No earnings report" must not sit under "reports earnings on…"."""
    body = _body(_code(_TRACKING_MODELS),
                 "static func earningsDescription(_ data: EarningsData) -> String {")
    assert "case .upcoming, .notScheduled:" not in body
    arm = body[body.index("case .notScheduled:"): body.index("case .live where")]
    assert "no earnings report" in arm
    assert "reports earnings" not in arm


def test_the_header_says_what_the_card_under_it_shows():
    """Its title and sentence are rebuilt from each answer, so the words have to follow the
    status and the figures, or the header goes on saying "Waiting" above a landed result."""
    tracking = _code(_TRACKING_MODELS)
    sentence = _norm(_body(tracking,
                           "static func earningsDescription(_ data: EarningsData) -> String {"))
    assert ("case .live where !data.hasResults && !data.epsChecking && !data.revenueChecking:"
            in sentence), "`Waiting for results…` is for a report with nothing in and nothing held"
    assert ('let lead = data.status == .live ? "\\(data.ticker) is reporting." '
            ': (dateText.isEmpty ? "\\(data.ticker) reported." '
            ': "\\(data.ticker) reported \\(dateText).")') in sentence
    assert 'return results.isEmpty ? lead : lead + " " + results' in sentence
    title = _norm(_body(tracking, "var title: String {"))
    assert 'case .reported, .awaiting: return "Earnings Results"' in title
    assert 'case .upcoming, .live, .notScheduled: return "Earnings Alert"' in title


# ── the sheet before its first answer ────────────────────────────────────────


def test_the_screen_claims_nothing_before_the_first_answer():
    """Before the first answer the push route has no figures at all: it must not say the opposite
    of the push ("Results will appear here…"), and `isLoading` could not drive this: it is
    false on the first frame, and a cancelled load's `defer` clears it under its replacement."""
    card = _code(_CARD)
    assert "private var hasNothingToShow: Bool { live == nil && fallback == nil }" in card
    body = _body(card, "var body: some View {")
    first = _body(body, "if hasNothingToShow {")
    assert "metricRow" not in first and "footnote" not in first, "a claim before any answer"
    after = body[body.index(first) + len(first):]
    assert after.lstrip().startswith("else if status == .notScheduled {"), (
        "the rows must be the OTHER branch, or they draw under the spinner"
    )
    assert "ProgressView()" in _body(first, "if !viewModel.loadFailed {"), (
        "a spinner must not turn beside the failure notice"
    )
    line = _body(card, "private var statusLine: String {")
    assert line.index("if hasNothingToShow") < line.index("switch status")
    assert ('return viewModel.loadFailed ? "Results aren\'t available right now" '
            ': "Loading results…"') in line
    label = _body(card, "private func outcomeLabel(_ outcome: EarningsMetricOutcome) -> some View {")
    assert "} else if !outcome.hasLanded, status == .live, !viewModel.loadFailed {" in label, (
        "a row spins only while its figure is on its way and the device is asking"
    )
    assert "isLoading" not in card and "isLoading" not in _code(_VM)


def test_the_footnote_follows_the_eps_basis():
    foot = _body(_code(_CARD), "private var footnote: some View {")
    assert re.search(r'let basis: String = eps\.isGAAP\s*\?\s*"This EPS is GAAP.*?"\s*'
                     r':\s*"EPS is adjusted \(non-GAAP\)', foot), (
        "the sentence must match the basis the row shows"
    )
    assert "Text(basis +" in foot


def test_a_gaap_eps_is_labelled_and_never_compared():
    """The consensus is adjusted, so a GAAP figure beside it is neither a beat nor a miss."""
    card = _code(_CARD)
    assert ('metricRow(title: eps.isGAAP ? "EPS (GAAP)" : "EPS (adjusted)", outcome: eps, '
            "format: EarningsFigureFormat.eps)") in _norm(_body(card, "var body: some View {"))
    label = _body(card, "private func outcomeLabel(_ outcome: EarningsMetricOutcome) -> some View {")
    assert 'Text("Not comparable")' in _body(label, "if outcome.isGAAP, outcome.hasLanded {")
    models = _code(_MODELS)
    assert "guard !isGAAP, let estimate else { return .noEstimate }" in _body(
        models, "var result: EarningsQuarterResult {")
    assert "guard let pct = surprisePct, pct.isFinite, !isGAAP else { return nil }" in _body(
        models, "var surpriseText: String? {")
    line = _body(_code(_TRACKING_MODELS),
                 "static func earningsResultsLine(_ data: EarningsData) -> String {")
    assert 'let epsLabel = data.epsIsGAAP ? "GAAP EPS" : "EPS"' in line
    chips = _code(_CHIPS)
    assert 'if outcome.isGAAP { return "GAAP" }' in _body(
        chips, "private func value(_ outcome: EarningsMetricOutcome) -> String {")
    assert 'if o.isGAAP { return "\\(name) reported on a GAAP basis" }' in _body(
        chips, "private var spoken: String {")


def test_a_beat_and_a_miss_stay_the_right_way_round_in_every_signal():
    """The owner's rule: "red or green for miss or beat". The verdict, its colour, its word, its
    arrow and VoiceOver are separate switches, so one flipped arm calls a miss a beat with
    everything around it still reading correctly."""
    models = _code(_MODELS)
    assert _norm(_body(models, "var result: EarningsQuarterResult {")) == (
        "{ guard let actual else { return .pending } "
        "guard !isGAAP, let estimate else { return .noEstimate } "
        "if surprisePct == nil { return .noEstimate } "
        "if actual > estimate { return .beat } if actual < estimate { return .missed } "
        "return .matched }"
    )
    for opener, arms in (
        ("var color: Color {",
         "case .beat: return AppColors.gain case .missed: return AppColors.loss"),
        ("var word: String? {", 'case .beat: return "Beat" case .missed: return "Miss"'),
        ("var arrowSymbol: String? {",
         "case .beat: return AppSentiment.symbolName(isPositive: true) "
         "case .missed: return AppSentiment.symbolName(isPositive: false)"),
    ):
        assert arms in _norm(_body(models, opener)), f"flipped: {opener}"
    spoken = _norm(_body(_code(_CHIPS), "private var spoken: String {"))
    assert 'case .beat: return "\\(name) beat ' in spoken
    assert 'case .missed: return "\\(name) missed ' in spoken
    assert 'return say("Earnings per share", eps) + ". " + say("Revenue", revenue) + "."' in spoken


def test_the_last_quarters_read_the_words_the_backend_sends():
    """Anything unmatched reads as no estimate, with no error: a word renamed on either side
    turns every dot grey."""
    quarters = _norm(_body(_code(_MODELS), (
        "var quarterResults: "
        "[(quarter: String, result: EarningsQuarterResult, surprisePct: Double?)] {")))
    assert (f'case "{_quarter_result(2.0, 1.0, True)}": result = .beat '
            f'case "{_quarter_result(1.0, 2.0, True)}": result = .missed '
            f'case "{_quarter_result(1.0, 1.0, True)}": result = .matched '
            "default: result = .noEstimate") in quarters
    assert _quarter_result(1.0, None, None) == "no_estimate", "the arm `default` stands in for"


def test_each_figure_sits_under_its_own_label():
    card = _code(_CARD)
    row = _norm(_body(card, "private func metricRow(title: String, outcome: EarningsMetricOutcome,\n"
                            "                           format: (Double) -> String) -> some View {"))
    assert ('figure(label: "Estimate", value: outcome.estimate.map(format) ?? "—", '
            "color: AppColors.textSecondary)") in row
    assert ('figure(label: "Actual", value: actualText(outcome, format: format), '
            "color: outcome.hasLanded ? outcome.color : AppColors.textMuted, "
            "emphasized: outcome.hasLanded)") in row, "a figure not out yet has no beat colour"
    actual = _body(card, "private func actualText(_ outcome: EarningsMetricOutcome,\n"
                         "                            format: (Double) -> String) -> String {")
    assert "if let actual = outcome.actual { return format(actual) }" in actual
    chips = _body(_code(_CHIPS), "var body: some View {")
    assert 'chip(label: "EPS", outcome: eps)' in chips
    assert 'chip(label: "REV", outcome: revenue)' in chips


def test_a_preliminary_figure_is_marked_and_links_its_filing():
    """It is the company's own number, not yet FMP's, and the card says so."""
    card = _code(_CARD)
    assert "if isPreliminary { preliminaryNote }" in _body(card, "var body: some View {")
    assert "if let url = live?.filingURL {" in _body(card, "private var preliminaryNote: some View {")


def test_a_metric_is_awaited_only_while_the_report_is_live():
    """A report the server finished with no revenue consensus has stopped waiting for revenue,
    so nothing on screen may go on saying it is on its way."""
    card = _code(_CARD)
    actual = _body(card, "private func actualText(_ outcome: EarningsMetricOutcome,\n"
                         "                            format: (Double) -> String) -> String {")
    assert actual.index('"Confirming…"') < actual.index('"Waiting…"')
    assert 'return isLiveNow ? "Waiting…" : "—"' in actual
    body = _body(card, "var body: some View {")
    assert re.search(r'if showsRevenueRow \{\s*metricRow\(title: "Revenue", outcome: revenue,'
                     r'\s+format: EarningsFigureFormat\.revenue\)', body)
    rows = _norm(_body(card, "private var showsRevenueRow: Bool {"))
    assert rows == ("{ status == .live || revenue.estimate != nil || revenue.hasLanded "
                    "|| revenue.isChecking }"), "the Revenue row must hide only with nothing to say"

    chips = _code(_CHIPS)
    value = _body(chips, "private func value(_ outcome: EarningsMetricOutcome) -> String {")
    assert 'status == .live || outcome.isChecking ? "…" : "—"' in value
    assert re.search(r"EarningsResultChips\(eps: data\.epsOutcome, revenue: data\.revenueOutcome,"
                     r"\s+status: data\.status\)", _code("Views/Molecules/AlertCardView.swift"))

    line = _body(_code(_TRACKING_MODELS),
                 "static func earningsResultsLine(_ data: EarningsData) -> String {")
    assert 'data.status == .live || estimate != nil ? "\\(label) not out yet" : nil' in line


def test_a_held_figure_reads_the_same_on_the_card_and_the_sheet():
    """The feed carries the hold, so the Tracking card, its chips and the sheet's first frame
    all say what the sheet says once it has answered."""
    tracking = _code(_TRACKING_MODELS)
    dto = _body(tracking, "func toAppAlert() -> AppAlert {")
    assert "epsChecking: epsChecking ?? false" in dto
    assert "revenueChecking: revenueChecking ?? false" in dto
    assert "isGAAP: epsIsGAAP, isChecking: epsChecking)" in tracking
    assert re.search(r"isPreliminary: isPreliminary,\s+isChecking: revenueChecking\)", tracking)
    line = _body(tracking, "static func earningsResultsLine(_ data: EarningsData) -> String {")
    assert 'if checking { return "\\(label) being confirmed" }' in line
    # Each flag on its own metric: crossed, the card names the wrong figure as held.
    flat = _norm(line)
    assert ('part("Revenue", data.revenueActual, data.revenueEstimate, '
            'checking: data.revenueChecking, EarningsFigureFormat.revenue),') in flat
    assert ('part(epsLabel, data.epsActual, data.epsIsGAAP ? nil : data.epsEstimate, '
            'checking: data.epsChecking, EarningsFigureFormat.eps),') in flat
    # "being confirmed" is what the push says (`earnings_live_sender._phrase`).
    sentence = _body(tracking, "static func earningsDescription(_ data: EarningsData) -> String {")
    assert "!data.epsChecking && !data.revenueChecking" in sentence, (
        "a held figure must not read as `Waiting for results…`"
    )
    spoken = _body(_code(_CHIPS), "private var spoken: String {")
    assert '"\\(name) being confirmed"' in spoken
    answer = _code(_MODELS)
    assert "isChecking: eps.checking ?? false" in _body(answer, "var epsOutcome: EarningsMetricOutcome {")
    assert "isChecking: revenue.checking ?? false" in _body(
        answer, "var revenueOutcome: EarningsMetricOutcome {")


def test_a_held_figure_is_not_called_absent_once_the_window_has_closed():
    """Every figure held and none shown reads `awaiting` from 22:00 ET and on later days: the
    flags must still reach the sentence, or the header says the results aren't in above a row
    saying "Confirming…"."""
    sentence = _body(_code(_TRACKING_MODELS),
                     "static func earningsDescription(_ data: EarningsData) -> String {")
    arm = sentence[sentence.index("case .awaiting:"):]
    assert re.search(r"guard data\.epsChecking \|\| data\.revenueChecking else \{\s*"
                     r"return \"\\\(data\.ticker\)'s\\\(when\) results aren't in yet\.\"\s*\}", arm), (
        "`aren't in yet` is for a report with nothing held"
    )
    assert 'return "\\(data.ticker)\'s\\(when) results: \\(earningsResultsLine(data))"' in arm
    line = _body(_code(_CARD), "private var statusLine: String {")
    assert ('return eps.isChecking || revenue.isChecking ? "Results being confirmed" '
            ': "Results aren\'t in yet"') in _norm(line)


def test_the_spoken_chip_says_what_the_card_says():
    """A report can be finished with one figure missing from the feed: the card says it is not
    out yet, or nothing. "Not reported" beside that is a false claim about the company, and
    the rule has to stay the card's (`earningsResultsLine`)."""
    spoken = _norm(_body(_code(_CHIPS), "private var spoken: String {"))
    assert 'if o.isChecking { return "\\(name) being confirmed" }' in spoken
    assert ('return status == .live || o.estimate != nil ? "\\(name) not out yet" '
            ': "\\(name) not available"') in spoken
    assert "not reported" not in spoken
    line = _body(_code(_TRACKING_MODELS),
                 "static func earningsResultsLine(_ data: EarningsData) -> String {")
    assert 'data.status == .live || estimate != nil ? "\\(label) not out yet"' in line


# ── a poll that has given up ─────────────────────────────────────────────────


def test_a_poll_that_gave_up_says_so_and_a_try_again_clears_it():
    """It ends with no answer once the retries run out, or on a refusal shown answer or not. A
    poll the server itself stopped is not this: the answer then says how the report stands."""
    vm = _code(_VM)
    assert "@Published private(set) var pollEnded = false" in vm
    start = _body(vm, "func start() {")
    assert start.index("pollEnded = false") < start.index("pollTask = Task"), (
        "Try Again and a return to the foreground must draw LIVE again while they ask"
    )
    stopped = _norm(_body(start, "guard let seconds = self.nextPollSeconds else {"))
    assert stopped == "{ self.pollEnded = self.refused || self.live == nil break }"


def test_nothing_on_the_sheet_says_live_once_the_device_has_stopped_asking():
    """`LiveBadge`: LIVE only while it is genuinely being checked. With the retry over, the status
    the card reads is a last word from before that, and the sheet stays open for as long as
    the user leaves it."""
    card = _code(_CARD)
    assert "private var isLiveNow: Bool { status == .live && !viewModel.pollEnded }" in card
    assert card.count("LiveBadge()") == 1
    badge = _body(_body(card, "private var header: some View {"), "if isLiveNow {")
    assert "LiveBadge()" in badge
    line = _body(card, "private var statusLine: String {")
    arm = line[line.index("case .live:"): line.index("case .reported:")]
    assert arm.index("guard isLiveNow else {") < arm.index("Checking for results")
    assert 'guard isLiveNow else { return "Latest results aren\'t available right now" }' in arm
    # The rows: `actualText` is pinned by `test_a_metric_is_awaited_only_while_the_report_is_live`.
    assert "!viewModel.loadFailed" in _body(
        card, "private func outcomeLabel(_ outcome: EarningsMetricOutcome) -> some View {")
    assert re.search(r"loadFailed = live == nil \|\| refused", _code(_VM)), (
        "an ended poll must always be a failed one, or the row keeps spinning"
    )


def test_the_header_stops_waiting_when_the_card_does():
    """The header sits right above the card: it may not go on saying "Waiting for results…"."""
    card = _code(_CARD)
    hook = _norm(card[card.index(".onChange(of: viewModel.pollEnded)"):])
    assert hook.startswith(".onChange(of: viewModel.pollEnded) { _, ended in onPollEnded?(ended) }"), (
        "Bool, not fire-and-forget: Try Again has to take it back"
    )
    detail = _code(_DETAIL)
    assert "@State private var liveCheckEnded = false" in detail
    header = _body(detail, "private var headerAlert: AppAlert {")
    assert header.index("data.updated(with: liveAnswer)") < header.index(
        "data.liveCheckEnded = liveCheckEnded"), "`updated(with:)` builds a fresh copy"
    tracking = _code(_TRACKING_MODELS)
    assert "var liveCheckEnded: Bool = false" in tracking
    sentence = _body(tracking, "static func earningsDescription(_ data: EarningsData) -> String {")
    assert 'return data.liveCheckEnded ? sentence : sentence + " Waiting for results…"' in sentence
    assert sentence.count("Waiting for results") == 1


# ── the off switch ───────────────────────────────────────────────────────────


def test_the_sheet_shows_the_results_card_only_with_a_report_date():
    """With the feature off, or on an older backend, the feed sends no `report_date`, and the
    card would still ask the endpoint for a report nobody is watching."""
    detail = _body(_code(_DETAIL), "private func earningsDetail(_ data: AppAlert.EarningsData) -> some View {")
    gate = _body(detail, "if data.reportDate != nil {")
    assert "EarningsLiveResultsCard(" in gate
    assert detail.count("EarningsLiveResultsCard(") == 1, "a second card outside the gate"


# ── the previews ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rel,call", [
    (_CARD, "EarningsLiveResultsCard(previewing: .sampleData)"),
    (_SCREEN, "EarningsLiveScreen(previewing: .sampleData)"),
])
def test_each_preview_draws_sample_data_and_makes_no_request(rel, call):
    code = _code(rel)
    assert "#Preview" not in _release(code), "a #Preview outside DEBUG breaks the Release archive"
    assert call in _debug_only(code)
    previews = _debug_only(code)[_debug_only(code).index("#Preview"):]
    assert "ticker:" not in previews, "this preview still goes through the live initialiser"


def test_the_preview_seam_cannot_reach_a_production_path():
    """`ios-swiftui.md`: sample market data is never painted onto a failed load."""
    for rel in (_VM, _CARD, _SCREEN):
        code = _code(rel)
        assert "init(previewing answer: EarningsLiveResponse)" in _debug_only(code), rel
        assert "previewing" not in _release(code), f"{rel}: the seam is compiled into Release"
    models = _code(_MODELS)
    assert "static let sampleData = EarningsLiveResponse(" in _debug_only(models)
    assert "sampleData" not in _release(models)

    vm = _code(_VM)
    start = _body(vm, "func start() {")
    assert start.index("guard !isPreview else { return }") < start.index("pollTask = Task")
    assert "private func load() async {" in vm, "only `start()` may reach the network"
    assert "self.isPreview = false" in _body(
        vm, "init(ticker: String, reportDate: String?, apiClient: APIClient = .shared) {")

    callers = []
    for path in sorted(_IOS.rglob("*.swift")):
        rel = str(path.relative_to(_IOS))
        if re.search(r"Earnings\w+\(previewing:|\bpreviewing: \.sampleData", _release(_code(rel))):
            callers.append(rel)
    assert not callers, f"the preview seam is called from a production path: {callers}"


def test_the_sample_answer_is_labelled_as_sample_data():
    sample = _debug_only(_code(_MODELS))
    assert 'ticker: "SMPL"' in sample
    assert 'companyName: "Sample Company (sample data)"' in sample


# ── isolation ────────────────────────────────────────────────────────────────


def test_the_figure_formatters_are_nonisolated():
    """They are passed as plain `(Double) -> String` values, which a main-actor static cannot
    be without a warning, and they touch nothing but Foundation's string formatting."""
    fmt = _body(_code(_MODELS), "enum EarningsFigureFormat {")
    for name in ("eps", "revenue", "growth"):
        assert f"nonisolated static func {name}(" in fmt, name
