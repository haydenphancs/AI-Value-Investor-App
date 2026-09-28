"""Backend ↔ iOS contract and source guards for the Updates news-tone chart and its
"Ask Cay AI" entries (2026-09-27).

Two kinds of test:
  * wire parity — the JSON keys the backend sends equal the CodingKeys the Swift DTOs
    declare (parsed from the source, brace-bound, comments stripped), and the toggle's
    windows equal the backend's TREND_DAYS;
  * source guards — the invariants a green build cannot see: every Updates chat entry OPENS
    a grounded chat (no credit) instead of seeding one (1 credit), the chart stays a plain
    child of the feed's single LazyVStack, and the chat is torn down on an identity change.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.schemas.updates import SentimentTrendDayResponse, SentimentTrendResponse
from app.services.news_sentiment_trend_service import TREND_DAYS

IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
MODELS = IOS / "Models" / "UpdatesModels.swift"
VIEW = IOS / "Views" / "Screens" / "UpdatesView.swift"
VM = IOS / "ViewModels" / "UpdatesViewModel.swift"
CARD = IOS / "Views" / "Organisms" / "InsightsSummaryCard.swift"
DETAIL = IOS / "Views" / "Screens" / "InsightsDetailView.swift"
CHART = IOS / "Views" / "Molecules" / "NewsSentimentTrendChart.swift"
CHAT_VM = IOS / "ViewModels" / "ChatViewModel.swift"
CHAT_SCREEN = IOS / "Views" / "Screens" / "AIChatScreen.swift"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//") or line.strip().startswith("///"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of a declaration, comments stripped."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_brace:i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


def _coding_keys(struct_header: str) -> set[str]:
    """The wire keys a Swift DTO decodes: `case a, b` → a, b; `case x = "y"` → y."""
    block = _decl_block(_read(MODELS), struct_header)
    keys_block = _decl_block(block, "enum CodingKeys")
    keys: set[str] = set()
    for line in keys_block.splitlines():
        line = line.strip()
        if not line.startswith("case "):
            continue
        for part in line[len("case "):].split(","):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                keys.add(part.split("=", 1)[1].strip().strip('"'))
            else:
                keys.add(part)
    assert keys, f"{struct_header}: no CodingKeys parsed — this scan would pass vacuously"
    return keys


# ── wire parity ─────────────────────────────────────────────────────────────


def test_trend_envelope_keys_match_the_swift_dto():
    payload = SentimentTrendResponse(scope="ORCL", days=30).model_dump()
    assert set(payload) == _coding_keys("struct SentimentTrendResponse")


def test_trend_day_keys_match_the_swift_dto():
    payload = SentimentTrendDayResponse(date="2026-09-27").model_dump()
    assert set(payload) == _coding_keys("struct SentimentTrendDayDTO")


def test_the_toggle_windows_equal_the_backend_windows():
    block = _decl_block(_read(MODELS), "enum SentimentTrendWindow")
    days_block = _decl_block(block, "var days: Int")
    swift_days = sorted(int(n) for n in re.findall(r"return (\d+)", days_block))
    assert swift_days == sorted(TREND_DAYS)
    labels = re.findall(r'case \w+ = "(\d+)D"', block)
    assert sorted(int(n) for n in labels) == sorted(TREND_DAYS)


def test_a_worst_case_trend_serializes_without_nan():
    body = SentimentTrendResponse(
        scope="__MARKET__", days=90, tracking_since=None,
        series=[SentimentTrendDayResponse(date="2026-09-27", is_partial=True)],
    ).model_dump()
    json.dumps(body, allow_nan=False)


# ── every Updates entry OPENS a chat; none spends a credit on the tap ────────


def test_the_updates_chat_is_opened_grounded_never_seeded():
    fn = _decl_block(_read(VIEW), "private func openUpdatesChat(")
    assert "prepareGroundedConversation(" in fn
    assert "contextType: .updatesScope" in fn
    assert "referenceId: tab.chatReferenceId" in fn
    assert "startNewConversation" not in fn


@pytest.mark.parametrize("path", [VIEW, CARD, DETAIL, CHART], ids=lambda p: p.name)
def test_no_updates_surface_seeds_a_paid_turn(path):
    assert "startNewConversation" not in _strip_comments(_read(path)), (
        f"{path.name} seeds a chat turn — tapping would spend a credit the user never typed"
    )


def test_all_three_entries_route_through_the_one_opener():
    body = _decl_block(_read(VIEW), "var body: some View")
    assert "openUpdatesChat(focus: .card)" in body, "the Insights card's pill"
    assert "openUpdatesChat(focus: .trend)" in body, "the news-tone chart's button"
    assert "onAskCay: { pendingChatFocus = .card }" in body, "the detail sheet's button"
    assert ".sheet(item: $insightSources, onDismiss: presentPendingChat)" in body
    pending = _decl_block(_read(VIEW), "private func presentPendingChat(")
    assert "openUpdatesChat(focus: focus)" in pending
    assert "pendingChatFocus = nil" in pending


def test_a_presentation_reset_cannot_reopen_the_chat():
    body = _decl_block(_read(VIEW), "var body: some View")
    reset = _decl_block(body, ".onPresentationReset")
    assert "showUpdatesChat = false" in reset
    assert reset.index("pendingChatFocus = nil") < reset.index("insightSources = nil"), (
        "clearing the sheet fires its onDismiss; a pending chat must already be gone"
    )


def test_the_detail_button_records_then_dismisses():
    body = _decl_block(_read(DETAIL), "var body: some View")
    ask = _decl_block(body, "AskCayAIPill(")
    assert ask.index("onAskCay()") < ask.index("dismiss()")


def test_the_card_pill_is_its_own_button_not_the_card_tap():
    body = _decl_block(_read(CARD), "var body: some View")
    assert "AskCayAIPill(" in body and "action: onAskCay" in body
    assert "onTapGesture { if hasSources { onOpenSources?() } }" in body


def test_the_chat_is_reset_on_an_identity_change():
    body = _decl_block(_read(VIEW), "var body: some View")
    reload = _decl_block(body, ".reloadOnIdentityChange")
    assert "updatesChat.resetForIdentityChange()" in reload


def test_host_chips_win_in_the_chat_screen_and_die_with_the_conversation():
    suggestions = _decl_block(_read(CHAT_SCREEN), "private var suggestions: [SuggestionChip]")
    assert suggestions.index("viewModel.starterChips") < suggestions.index("startersStore.globalStarters")
    vm = _read(CHAT_VM)
    reset = _decl_block(vm, "func resetConversation()")
    assert "starterChips = []" in reset
    seed = _decl_block(vm, "func startNewConversation(")
    assert "starterChips = []" in seed


# ── the chart stays a plain child of the ONE LazyVStack ─────────────────────


def test_the_chart_is_a_direct_child_of_the_feed_stack():
    body = _decl_block(_read(VIEW), "var body: some View")
    stack = _decl_block(body, "LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders])")
    assert "NewsSentimentTrendChart(" in stack, "the chart left the feed's LazyVStack"
    chart_at = stack.index("NewsSentimentTrendChart(")
    assert chart_at < stack.index("newsSections()"), "the chart must sit above the timeline"
    assert "Section" not in stack[:chart_at].split("InsightsSummaryCard(")[-1], (
        "the chart must not be wrapped in a Section"
    )
    sections = _decl_block(_read(VIEW), "private func newsSections()")
    assert "NewsSentimentTrendChart" not in sections


def test_the_chart_gate_lives_outside_body():
    # `test_ios_account_gate_state` reads the feed gate's branch order from the FIRST mention
    # of each flag in `body`; a chart condition there would silently take that place.
    gate = _decl_block(_read(VIEW), "private var visibleTrend: SentimentTrend?")
    for flag in ("viewModel.isReconnecting", "viewModel.requiresSignIn",
                 "trend.scope == viewModel.selectedTab?.scope", "trend.hasEnoughHistory()"):
        assert flag in gate
    assert "if let trend = visibleTrend" in _decl_block(_read(VIEW), "var body: some View")


# ── the view model: never another scope's or another account's chart ─────────


def test_identity_change_clears_the_trend_before_the_active_tab_gate():
    fn = _decl_block(_read(VM), "func handleIdentityChange(")
    gate = fn.index("guard isActiveTab")
    for token in ("trendCache.removeAll()", "sentimentTrend = nil", "trendTask?.cancel()"):
        assert fn.index(token) < gate, f"{token} must run even for a hidden tab"


def test_a_late_trend_response_for_another_scope_or_window_is_dropped():
    fn = _decl_block(_read(VM), "private func loadTrend(")
    assert fn.count("selectedTab?.scope == scope, trendWindow == window") == 2, (
        "both the success and the failure path must check they still own the chart"
    )


def test_the_trend_load_never_blocks_the_feed():
    fn = _decl_block(_read(VM), "private func loadFeed(")
    assert "startTrendLoad(scope: scope, force: force)" in fn
    start = _decl_block(_read(VM), "private func startTrendLoad(")
    assert "trendTask = Task" in start
    assert "if sentimentTrend?.scope != scope { sentimentTrend = nil }" in start



# ── review 2026-09-27 ────────────────────────────────────────────────────────


def test_an_empty_window_keeps_the_chart_and_its_toggle():
    """Gated on the SCOPE's tracking start, never on the window having bars — otherwise
    choosing an empty 7D removed the card together with the only toggle back to 30D."""
    fn = _decl_block(_read(MODELS), "func hasEnoughHistory(")
    assert "!days.isEmpty" not in fn
    assert "trackedDays(today: today, calendar: calendar) >= Self.minimumTrackedDays" in fn
    tracked = _decl_block(_read(MODELS), "func trackedDays(")
    assert "trackingSince ?? days.first?.date" in tracked
    assert "!days.isEmpty" not in tracked
    assert "SentimentTrendDayParser.etToday()" in _read(MODELS).split("func hasEnoughHistory(")[1][:200]


def test_a_failed_window_switch_keeps_the_scope_chart_and_snaps_the_toggle_back():
    fn = _decl_block(_read(VM), "private func loadTrend(")
    catch = fn[fn.index("} catch {"):]
    assert "if let shown = sentimentTrend, shown.scope == scope" in catch
    assert "trendWindow = shown.window" in catch
    assert catch.index("trendWindow = shown.window") < catch.index("sentimentTrend = nil")


def test_the_chart_axis_ends_on_the_et_day():
    chart = _read(CHART)
    domain = _decl_block(chart, "private var xDomain: ClosedRange<Date>")
    assert "SentimentTrendDayParser.etToday()" in domain
    assert "Calendar.current.startOfDay(for: Date())" not in domain
    assert "max(today, lastDay)" in domain
    axis = _decl_block(chart, "private var axisDatesUnsorted: [Date]")
    assert "SentimentTrendDayParser.etToday()" in axis
    parser = _decl_block(_read(MODELS), "enum SentimentTrendDayParser")
    assert 'TimeZone(identifier: "America/New_York")' in parser


def test_a_fund_is_declared_to_the_chat():
    tab = _decl_block(_read(MODELS), "struct NewsFilterTab")
    ref = _decl_block(tab, "var chatReferenceId: String")
    assert '"etf" ? "\\(scope)|ETF" : scope' in ref
    assert "case assetType = \"asset_type\"" in _decl_block(_read(MODELS), "struct UpdatesTabDTO")
    label = _decl_block(_read(CHAT_SCREEN), "private var groundingReferenceLabel: String?")
    assert 'ref.split(separator: "|").first' in label.split("case .updatesScope:")[1]



# ── 2026-09-27: adaptive window, clipped label, building state ────────────────


def test_a_short_history_opens_on_7d_cut_from_the_30d_answer():
    vm = _read(VM)
    start = _decl_block(vm, "private func startTrendLoad(")
    assert "let fetchWindow: SentimentTrendWindow = userPickedWindow ? window : .month" in start
    assert "if !userPickedWindow && isNewScope { trendWindow = .month }" in start
    present = _decl_block(vm, "private func present(")
    assert "trend.trackedDays() < SentimentTrend.shortHistoryDays ? .week : .month" in present
    assert "sentimentTrend = trend.trimmed(to: display)" in present
    # Only the user's tap marks the window as chosen.
    assert vm.count("userPickedWindow = true") == 1
    assert "userPickedWindow = true" in _decl_block(vm, "func setTrendWindow(")


def test_the_last_30d_label_is_anchored_inside_the_plot():
    chart = _read(CHART)
    axis = _decl_block(chart, "private var xAxis: some AxisContent")
    assert "anchor: isTrailingTick(value.index, of: value.count) ? .topTrailing : nil" in axis
    trailing = _decl_block(chart, "private func isTrailingTick(")
    assert "trend.window == .month" in trailing and "index == count - 1" in trailing
    dates = _decl_block(chart, "private var axisDates: [Date]")
    assert "axisDatesUnsorted.sorted()" in dates, "oldest first, so the last index is today"
    unsorted = _decl_block(chart, "private var axisDatesUnsorted: [Date]")
    assert "$0 >= lower && $0 <= latest" in unsorted, "90D drops a month tick near the axis end"


def test_building_history_shows_a_placeholder_never_invented_bars():
    chart = _read(CHART)
    gate = _decl_block(chart, "private var showsBuildingPlaceholder: Bool")
    assert "trend.days.isEmpty && trend.isBuildingHistory" in gate
    body = _decl_block(chart, "var body: some View")
    assert "if showsBuildingPlaceholder {" in body and "buildingPlaceholder" in body
    visible = _decl_block(_read(VIEW), "private var visibleTrend: SentimentTrend?")
    assert "trend.hasEnoughHistory() || trend.isBuildingHistory" in visible


def test_the_building_poll_is_bounded_and_cancelled():
    vm = _read(VM)
    poll = _decl_block(vm, "private func scheduleTrendPollIfBuilding(")
    assert "sentimentTrend?.isBuildingHistory == true" in poll
    assert "trendPollAttempt < trendPollDelays.count" in poll
    assert "self.selectedTab?.scope == scope" in poll
    load = _decl_block(vm, "private func loadTrend(")
    assert "if !trend.isBuildingHistory { trendCache[key] = (Date(), trend) }" in load
    identity = _decl_block(vm, "func handleIdentityChange(")
    gate = identity.index("guard isActiveTab")
    for token in ("trendPollTask?.cancel()", "userPickedWindow = false"):
        assert identity.index(token) < gate
    assert "trendPollTask?.cancel()" in _decl_block(vm, "    deinit {")
    start = _decl_block(vm, "private func startTrendLoad(")
    assert "trendPollTask?.cancel()" in start


def test_history_status_is_decoded_tolerantly():
    dto = _decl_block(_read(MODELS), "struct SentimentTrendResponse")
    assert "let historyStatus: String?" in dto
    ext = _read(MODELS).split("extension SentimentTrend {")[1]
    assert "SentimentHistoryStatus(rawValue: $0.lowercased())" in ext


# ── review fixes (2026-09-27 deep-check) ─────────────────────────────────────────


def test_a_failed_building_recheck_schedules_the_next_one():
    """Only `present()` scheduled a re-check, and only after a success: one 503 during a
    building poll left the spinner up with nothing scheduled."""
    load = _decl_block(_read(VM), "private func loadTrend(")
    catch = load[load.index("} catch {"):]
    kept = catch[catch.index("if let shown = sentimentTrend, shown.scope == scope {"):]
    kept = kept[:kept.index("} else {")]
    assert "if shown.isBuildingHistory { scheduleTrendPollIfBuilding(scope: scope) }" in kept


def test_exhausted_rechecks_stop_the_spinner_and_its_promise():
    vm = _read(VM)
    poll = _decl_block(vm, "private func scheduleTrendPollIfBuilding(")
    exhausted = poll[poll.index("guard trendPollAttempt < trendPollDelays.count else {"):]
    assert "trendPollExhausted = true" in exhausted[:exhausted.index("}")]
    start = _decl_block(vm, "private func startTrendLoad(")
    head = start[:start.index("let isNewScope")]
    # A tab return / pull restarts the re-checks but must NOT clear a stalled scope's copy.
    assert "trendPollExhausted = stalledScopes.contains(scope)" in head
    assert "trendPollExhausted = false" not in head
    identity = _decl_block(vm, "func handleIdentityChange(")
    assert "trendPollExhausted = false" in identity and "stalledScopes.removeAll()" in identity
    ready = poll[:poll.index("guard trendPollAttempt < trendPollDelays.count else {")]
    assert "stalledScopes.remove(scope)" in ready, "a ready answer clears the stalled copy"
    assert "stalledScopes.insert(scope)" in exhausted[:exhausted.index("}")]
    chart = _read(CHART)
    placeholder = _decl_block(chart, "private var buildingPlaceholder: some View")
    assert "if buildingStalled {" in placeholder
    stalled = placeholder[placeholder.index("if buildingStalled {"):placeholder.index("} else {")]
    assert "ProgressView" not in stalled, "a stalled build does not spin"
    view = _read(VIEW)
    assert "buildingStalled: viewModel.trendPollExhausted" in _strip_comments(view)


def test_rechecks_pause_while_the_tab_is_hidden():
    vm = _read(VM)
    active = _decl_block(vm, "func setTabActive(")
    hidden = active[active.index("if !active {"):active.index("} else if")]
    assert "trendPollTask?.cancel()" in hidden
    assert "startTrendLoad(scope: trend.scope, force: true)" in active
    poll = _decl_block(vm, "private func scheduleTrendPollIfBuilding(")
    assert poll.index("guard isTabActive else { return }") < poll.index("trendPollTask = Task")
    task = _strip_comments(_read(VIEW))
    block = task[task.index(".task(id: isActiveTab) {"):]
    assert block.index("viewModel.setTabActive(isActiveTab)") < block.index("guard isActiveTab else { return }"), \
        "before the guard, so going hidden is reported too"


def test_todays_axis_label_wins_a_collision():
    axis = _decl_block(_read(CHART), "private var xAxis: some AxisContent")
    assert "collisionResolution: .greedy(" in axis
    assert "priority: value.index == value.count - 1 ? 1 : 0" in axis


def test_the_chat_is_told_the_window_the_chart_is_drawing():
    """'Ask about this' on 90D was grounded on a fixed 30 days and quoted numbers the card
    did not show. The window rides in `context` (a control token), never `referenceId`."""
    from app.services.chat_context_resolver import updates_trend_window

    opener = _decl_block(_read(VIEW), "private func openUpdatesChat(")
    assert 'context: visibleTrend.map { "window=\\($0.window.days)" }' in opener
    assert "referenceId: tab.chatReferenceId" in opener
    for days in TREND_DAYS:
        assert updates_trend_window(f"window={days}") == days


def test_the_ask_cay_pill_has_an_in_card_tap_target():
    """~29 pt before: on the Insights card a near miss hit the card's own Sources tap. The
    frame must come AFTER the outline (else the capsule stretches) and BEFORE the final
    `.contentShape` (else the hit region stays the small frame)."""
    pill = _read(IOS / "Views" / "Atoms" / "AskCayAIPill.swift")
    assert "static let minHitHeight: CGFloat = 18 + 2 * AppSpacing.sm" in pill
    body = _decl_block(pill, "var body: some View")
    overlay = body.index(".overlay(")
    frame = body.index(".frame(minHeight: Self.minHitHeight)")
    shape = body.index(".contentShape(Rectangle())")
    assert overlay < frame < shape
