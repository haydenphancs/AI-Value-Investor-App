"""iOS source-scan guards for live earnings results (owner request 2026-10-29).

There is no XCTest target, so the iOS half of the feature is pinned here, the way every other
screen's contract is:
  * the wire: `EarningsLiveResponse` and its nested DTOs mirror the backend models key for
    key; `AlertDTO` and `TrackingFeedResponse` carry the new feed keys;
  * the route: `APIEndpoint.getEarningsLive` is the backend's path, signed-in only;
  * the honesty rule: LIVE is drawn from the SERVER's status, never from date math on the
    device (`LiveNewsHeader`: nothing live on screen, no "Live");
  * the poll: it stops when the server stops asking, its starter cancels before re-arming (two
    pollers would double the traffic), and a failed first load retries by itself, bounded;
  * the route: "See live results" is offered only while the endpoint serves that report;
  * the sheet: its header follows the card's own answer, it claims nothing before the first
    answer, it says "Waiting…" only while the report is live, and a held figure reads the same
    on the card and the sheet;
  * the previews: sample figures, no request, and no way into a production path;
  * the formatting defects the card shipped with ("$-0.25", "$0.0B") stay fixed.
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
from app.utils.market_hours import previous_trading_day

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _code(rel: str) -> str:
    path = _IOS / rel
    assert path.exists(), f"missing: {path}"
    src = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _struct_block(src: str, name: str) -> str:
    start = src.index(f"struct {name}")
    return src[start: src.index("\n}", start)]


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
    assert "guard let seconds = self.nextPollSeconds else { break }" in start
    assert "minimumPollSeconds" in start
    next_poll = _body(vm, "private var nextPollSeconds: Int? {")
    assert "guard let seconds = live.pollSeconds, seconds > 0 else { return nil }" in next_poll, (
        "a real answer with poll_seconds null must end the poll"
    )
    card = _code(_CARD)
    for hook in (".onAppear { viewModel.start() }", ".onDisappear { viewModel.stop() }",
                 "willResignActiveNotification", "didBecomeActiveNotification"):
        assert hook in card, f"the live card lost its lifecycle hook: {hook}"


def test_the_sheet_hosts_the_live_card_without_changing_its_own_signature():
    detail = _code("Views/Screens/AlertDetailView.swift")
    assert "EarningsLiveResultsCard(ticker: data.ticker, reportDate: data.reportDate," in detail
    assert "var onOpen: (AlertDestination) -> Void" in detail


def test_the_push_offers_live_results_first_and_the_cover_opens_them():
    dest = _code("Models/AlertDestination.swift")
    assert "case earningsLive(ticker: String, reportDate: String?)" in dest
    assert 'label: "See live results"' in dest
    first = dest.index('label: "See live results"')
    assert first < dest.index('label: "Open \\(symbol)"'), "live results must lead"
    cover = _code("Views/Modifiers/AlertDestinationCover.swift")
    assert "EarningsLiveScreen(ticker: ticker, reportDate: reportDate)" in cover


def test_the_consensus_formatting_defects_stay_fixed():
    models = _code("Models/TrackingModels.swift")
    assert 'String(format: "EPS Est: $%.2f"' not in models, "prints a loss as $-0.25"
    assert 'String(format: "Rev Est: $%.1fB"' not in models, "prints $23.5M as $0.0B"
    fmt = _code(_MODELS)
    eps = fmt[fmt.index("static func eps(_ value: Double) -> String"):]
    eps = eps[: eps.index("\n    }")]
    assert 'let sign = value < 0 ? "-" : ""' in eps, "the EPS sign goes after the $ again"
    assert 'String(format: "$%.2f", abs(value))' in eps
    assert 'String(format: "$%.1fM", v / 1_000_000)' in fmt


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
    assert "etCalendar" in helper, "the floor is the date in New York, not on the device"
    assert "previousTradingDay(before:" in helper
    assert '"%04d-%02d-%02d"' in helper


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
    card = _code(_CARD)
    body = _body(card, "var body: some View {")
    assert "if viewModel.loadFailed {" in body
    assert "viewModel.loadFailed && fallback == nil" not in card, (
        "the sheet always has a fallback, so this hides the notice from it"
    )


def test_the_retry_is_bounded_and_a_refusal_ends_the_poll_at_once():
    """A report_date ages out under an open sheet, and the 400 it then gets is permanent."""
    vm = _code(_VM)
    next_poll = _body(vm, "private var nextPollSeconds: Int? {")
    assert "failedLoads < Self.maxFailedLoads" in next_poll
    assert re.search(r"private static let maxFailedLoads = \d+\b", vm)
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
    header = _body(detail, "private var headerAlert: AppAlert {")
    assert "guard case .earnings(let data) = alert, let liveAnswer else { return alert }" in header
    assert ".earnings(data.updated(with: liveAnswer))" in header
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
    fields = {derived.get(name, name) for name in read}
    updated = _body(_code(_MODELS),
                    "func updated(with live: EarningsLiveResponse) -> AppAlert.EarningsData {")
    missing = sorted(f for f in fields | {"revenueEstimate", "epsSurprisePct", "revenueSurprisePct",
                                          "isPreliminary", "checkedAt", "epsChecking",
                                          "revenueChecking"}
                     if f"{f}:" not in updated)
    assert not missing, f"updated(with:) leaves the tap's copy of {missing}"
    assert "consensus: Self.consensusText(" in updated, (
        "the sentence prints the consensus string, not the estimates beside it"
    )


def test_an_answer_about_another_report_never_repaints_this_ones_figures():
    updated = _body(_code(_MODELS),
                    "func updated(with live: EarningsLiveResponse) -> AppAlert.EarningsData {")
    assert "let sameReport: Bool = live.reportDate == reportDate" in updated
    assert "if hasResults, answer == .notScheduled || !sameReport { return self }" in updated
    guard_at = updated.index("{ return self }")
    assert guard_at < updated.index("return AppAlert.EarningsData("), (
        "figures already shown must be kept before anything is copied"
    )
    assert "reportTime: sameReport ? reportTime : nil" in updated, (
        "the time of day belongs to the date the card was drawn for"
    )
    assert "Self.dayAndMonth(of: live.reportDate)" in updated, (
        "a report that moved must not keep the old date beside the new status"
    )
    models = _code(_MODELS)
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


# ── the sheet before its first answer ────────────────────────────────────────


def test_the_screen_claims_nothing_before_the_first_answer():
    """Before the first answer the push route has no figures at all: it must not say the opposite
    of the push ("Results will appear here…"), and `isLoading` could not drive this: it is
    false on the first frame, and a cancelled load's `defer` clears it under its replacement."""
    card = _code(_CARD)
    assert "private var hasNothingToShow: Bool { live == nil && fallback == nil }" in card
    body = _body(card, "var body: some View {")
    assert body.index("if hasNothingToShow") < body.index('metricRow(title: "Revenue"')
    line = _body(card, "private var statusLine: String {")
    assert line.index("if hasNothingToShow") < line.index("switch status")
    assert "Loading results" in line and "aren't available right now" in line
    assert "isLoading" not in card and "isLoading" not in _code(_VM)


def test_the_footnote_follows_the_eps_basis():
    foot = _body(_code(_CARD), "private var footnote: some View {")
    assert foot.index("eps.isGAAP") < foot.index("EPS is adjusted (non-GAAP)")
    assert "This EPS is GAAP" in foot


def test_a_metric_is_awaited_only_while_the_report_is_live():
    """A report the server finished with no revenue consensus has stopped waiting for revenue,
    so nothing on screen may go on saying it is on its way."""
    card = _code(_CARD)
    actual = _body(card, "private func actualText(_ outcome: EarningsMetricOutcome,\n"
                         "                            format: (Double) -> String) -> String {")
    assert actual.index('"Confirming…"') < actual.index('"Waiting…"')
    assert 'status == .live ? "Waiting…" : "—"' in actual
    body = _body(card, "var body: some View {")
    assert re.search(r'if showsRevenueRow \{\s*metricRow\(title: "Revenue"', body)
    rows = _body(card, "private var showsRevenueRow: Bool {")
    for term in ("status == .live", "revenue.estimate != nil", "revenue.hasLanded",
                 "revenue.isChecking"):
        assert term in rows, f"the Revenue row hides while {term}"

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
    assert "checking: data.revenueChecking" in line and "checking: data.epsChecking" in line
    # "being confirmed" is what the push says (`earnings_live_sender._phrase`).
    sentence = _body(tracking, "static func earningsDescription(_ data: EarningsData) -> String {")
    assert "!data.epsChecking && !data.revenueChecking" in sentence, (
        "a held figure must not read as `Waiting for results…`"
    )
    spoken = _body(_code(_CHIPS), "private var spoken: String {")
    assert '"\\(name) being confirmed"' in spoken


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
