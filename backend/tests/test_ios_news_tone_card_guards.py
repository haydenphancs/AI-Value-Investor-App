"""Source guards for the Updates tab's News Tone card fixes (review 2026-10-10).

Each test pins one fix a green build cannot see, by reading the Swift source (comments
stripped, the declaration brace-bound, per testing.md §3):

  * the window toggle tells VoiceOver which option is selected, and says "7 days", not "7D";
  * day keys are mapped to dates at CALL time — no static formatter freezing the time zone;
  * the summary line's tone word is majority-based and the net score carries its "%";
  * the chart's axis ends on the day the window was CUT (`asOf`), never a live `etToday()`;
  * an empty window says so to VoiceOver too, and the per-day counts are reachable;
  * "Building 90-day history…" is decided on the 90-day answer, not on the cut window;
  * 30D ticks sit at noon, centred under their bars, with room past the plot end;
  * a new ET day re-cuts and refreshes the chart (tab return and app foreground);
  * the chart waits for the SELECTED scope's feed, not any feed of the identity.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
TOGGLE = IOS / "Views" / "Atoms" / "AnalysisTimeframeToggle.swift"
CHART = IOS / "Views" / "Molecules" / "NewsSentimentTrendChart.swift"
MODELS = IOS / "Models" / "UpdatesModels.swift"
VM = IOS / "ViewModels" / "UpdatesViewModel.swift"
VIEW = IOS / "Views" / "Screens" / "UpdatesView.swift"


def _code(path: Path) -> str:
    """The file with `/* */` blocks and `//` / `///` tails removed, line structure kept.

    `(?<![:/])` keeps the `//` of a `https://` literal. The fixes' own comments name every
    token these tests look for, so an un-stripped scan would pass on prose.
    """
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return "\n".join(re.sub(r"(?<![:/])//.*$", "", line) for line in src.splitlines())


def _block(src: str, header: str) -> str:
    """The brace-balanced body of the ONE declaration starting with `header`."""
    assert src.count(header) == 1, f"{header!r} found {src.count(header)}× — this scan has drifted"
    open_brace = src.index("{", src.index(header))
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace:i + 1]
    pytest.fail(f"unbalanced braces after {header!r}")


def _norm(src: str) -> str:
    return " ".join(src.split())


# ── (a) the toggle: selection and spoken labels reach VoiceOver ────────────────────────────


def test_the_timeframe_toggle_says_which_option_is_selected():
    toggle = _block(_code(TOGGLE), "struct AnalysisTimeframeToggle<")
    each = _block(toggle, "ForEach(options, id: \\.rawValue)")
    button = each[each.index("Button {"):]
    assert ".accessibilityAddTraits(selectedOption == option ? [.isButton, .isSelected] : .isButton)" in button, (
        "the selected window is only a fill and an ink colour — VoiceOver cannot tell which one is on"
    )
    assert ".accessibilityLabel(spokenLabel(option))" in button
    # Optional and defaulted, so the three other callers compile unchanged and keep `rawValue`.
    assert "var accessibilityLabelFor: ((T) -> String)? = nil" in toggle
    assert "accessibilityLabelFor?(option) ?? option.rawValue" in _block(toggle, "private func spokenLabel(")


def test_the_news_tone_toggle_speaks_whole_words():
    header = _block(_code(CHART), "private var header: some View")
    call = header[header.index("AnalysisTimeframeToggle("):]
    assert "accessibilityLabelFor: { $0.spokenLabel }" in call, '"7D" is read as "seven D"'
    window = _block(_code(MODELS), "enum SentimentTrendWindow: String, CaseIterable, Equatable")
    assert 'var spokenLabel: String { "\\(days) days" }' in window


# ── (b) day keys → dates at call time, in the zone the device has now ──────────────────────


def test_day_dates_are_built_from_the_key_at_call_time():
    parser = _block(_code(MODELS), "enum SentimentTrendDayParser")
    assert "DateFormatter" not in parser, (
        "a static DateFormatter freezes TimeZone.current at first use; after a zone change "
        "every bar sits a day off its tick"
    )
    parse = _block(parser, "static func parse(")
    assert "DateComponents(year: parts.year, month: parts.month, day: parts.day)" in parse
    assert ".date(from:" in parse
    # The zone is read per call (a default argument is evaluated at each call), Gregorian
    # whatever calendar the device uses.
    head = parser[parser.index("static func parse("):parser.index("static func parse(") + 120]
    assert "in timeZone: TimeZone = Calendar.current.timeZone" in head
    gregorian = _block(parser, "private static func gregorian(in timeZone: TimeZone)")
    assert "Calendar(identifier: .gregorian)" in gregorian and "c.timeZone = timeZone" in gregorian
    # Cached per ZONE (2026-10-11, redraw cost): keyed by the zone asked for, never one shared
    # calendar — a single cached Calendar would freeze the zone exactly like the old formatter.
    assert "gregorianByZone[timeZone]" in gregorian
    assert "static var gregorian:" not in parser and "static let gregorian:" not in parser
    # A rolled-over key ("2026-02-31") is refused, as the strict formatter refused it.
    assert "guard back.year == parts.year, back.month == parts.month, back.day == parts.day" in parse
    # The ET "today" still comes from the New York calendar.
    assert 'TimeZone(identifier: "America/New_York")' in _block(parser, "private static let etCalendar")


def test_a_day_stores_only_its_key_and_derives_its_date():
    day = _block(_code(MODELS), "struct SentimentTrendDay: Identifiable, Equatable")
    assert "let date" not in day, "a stored Date goes stale when the device changes zone"
    assert "var date: Date { SentimentTrendDayParser.parse(dayKey)" in day
    trend = _block(_code(MODELS), "struct SentimentTrend: Equatable")
    assert "let trackingSinceKey: String?" in trend
    assert "var trackingSince: Date? { trackingSinceKey.flatMap" in trend
    assert "let trackingSince:" not in trend


# ── (c) the summary line: majority words, a net score with its "%" ─────────────────────────


def test_the_tone_word_is_majority_based_in_the_agreed_order():
    chart = _code(CHART)
    assert "static func toneWord(_ net: Int)" not in chart, "the net-only rule is back"
    word = _block(chart, "static func toneWord(positive: Int, negative: Int, neutral: Int) -> String")
    lines = [ln.strip() for ln in word.splitlines() if ln.strip().startswith(("if ", "return ", "let "))]
    assert lines == [
        "let total = positive + negative + neutral",
        'if 2 * positive > total { return "Mostly positive" }',
        'if 2 * negative > total { return "Mostly negative" }',
        "let net = SentimentTrend.netScore(positive: positive, negative: negative, total: total)",
        'if net >= 20 { return "Leaning positive" }',
        'if net <= -20 { return "Leaning negative" }',
        'if 2 * neutral > total { return "Mostly neutral" }',
        'return "Mixed"',
    ], lines


def test_the_net_score_carries_its_percent_on_screen_and_to_voiceover():
    chart = _code(CHART)
    summary = _norm(_block(chart, "private var summaryText: String"))
    assert '"(net \\(Self.signed(trend.netScore))%)"' in summary
    a11y = _norm(_block(chart, "private var accessibilitySummary: String"))
    assert "(net \\(Self.signed(trend.netScore))%)." in a11y
    assert "\\(windowToneWord)" in summary and "\\(windowToneWord)" in a11y, "one word source for both"
    # `signed` itself is pinned byte-for-byte against the backend's `_signed`: the "%" is added
    # at the call sites, never inside it.
    assert 'value > 0 ? "+\\(value)" : "\\(value)"' in _block(chart, "static func signed(")
    assert "%" not in _block(chart, "static func signed(")


# ── (d) the axis ends on the day the window was cut ─────────────────────────────────────────


def test_the_axis_ends_on_the_trends_own_day_never_a_live_today():
    chart = _code(CHART)
    for header in ("private var xDomain: ClosedRange<Date>", "private var axisDatesUnsorted: [Date]"):
        block = _block(chart, header)
        assert "etToday(" not in block, f"{header} reads a live ET today — after midnight it adds a slot"
        assert "let today = trend.asOf" in block, header
    models = _code(MODELS)
    trend = _block(models, "struct SentimentTrend: Equatable")
    assert "var asOfDayKey: String = SentimentTrendDayParser.etTodayKey()" in trend
    trimmed = _block(trend, "func trimmed(")
    assert "asOfDayKey: SentimentTrendDayParser.dayKey(for: today, in: calendar.timeZone)" in trimmed
    # The backend's `window_slice`: start <= day <= today, for EVERY window (90D included).
    assert ".filter { calendar.startOfDay(for: $0.date) <= end }" in trimmed
    assert "guard target.days <= window.days else { return self }" in trimmed


def test_a_new_et_day_recuts_and_refreshes_the_chart():
    vm = _code(VM)
    roll = _block(vm, "func refreshTrendIfDayRolledOver()")
    assert "let today = SentimentTrendDayParser.etTodayKey()" in roll
    assert "if sentimentTrend?.asOfDayKey != today { show(source) }" in roll, "re-cut at once, no request"
    gate = roll.index("guard isTabActive, source.asOfDayKey != today else { return }")
    assert gate < roll.index("startTrendLoad(scope: scope, force: true)"), "a hidden tab only re-cuts"
    active = _block(vm, "func setTabActive(_ active: Bool)")
    assert "refreshTrendIfDayRolledOver()" in active
    body = _block(_block(_code(VIEW), "struct UpdatesView: View"), "var body: some View")
    foreground = body[body.index("UIApplication.didBecomeActiveNotification"):]
    foreground = foreground[:foreground.index("}")]
    assert "viewModel.refreshTrendIfDayRolledOver()" in foreground
    # A cached answer from before ET midnight is not served as today's.
    start = _norm(_block(vm, "private func startTrendLoad("))
    assert "hit.trend.asOfDayKey == SentimentTrendDayParser.etTodayKey()" in start


# ── VoiceOver: an empty window, and the per-day counts ─────────────────────────────────────


def test_voiceover_reads_an_empty_window_like_the_screen_and_can_step_through_days():
    chart = _code(CHART)
    a11y = _block(chart, "private var accessibilitySummary: String")
    assert a11y.index("guard trend.total > 0 else { return emptyWindowText }") < a11y.index("headlines("), (
        'VoiceOver read "0 headlines … Mixed" for a window with no scored headline'
    )
    assert "guard total > 0 else { return emptyWindowText }" in _block(chart, "private var summaryText: String")
    assert 'static let noScoredHeadlinesText = "No scored headlines in this window yet."' in chart
    assert "return Self.noScoredHeadlinesText" in _block(chart, "private var emptyWindowText: String")
    view = _block(chart, "private var chart: some View")
    assert ".accessibilityValue(accessibilityValueText)" in view
    assert ".accessibilityAdjustableAction(adjustSelectedDay)" in view
    value = _block(chart, "private var accessibilityValueText: String")
    assert "if let day = selectedDay { return Self.accessibilityDayLine(day) }" in value
    adjust = _block(chart, "private func adjustSelectedDay(")
    assert "case .increment: stepSelectedDay(by: 1)" in adjust
    assert "case .decrement: stepSelectedDay(by: -1)" in adjust
    step = _block(chart, "private func stepSelectedDay(by step: Int)")
    assert "selectedKey = next == 0 ? nil : keys[next - 1]" in step, "position 0 = no day selected"


# ── the building placeholder follows the 90-day answer ─────────────────────────────────────


def test_building_placeholder_is_decided_on_the_source_not_the_cut():
    chart = _code(CHART)
    gate = _block(chart, "private var showsBuildingPlaceholder: Bool")
    assert "!trend.sourceHasDays && trend.days.isEmpty && trend.isBuildingHistory" in gate
    empty = _norm(_block(chart, "private var emptyWindowText: String"))
    assert "the 90-day history is still filling in." in empty
    models = _code(MODELS)
    trend = _block(models, "struct SentimentTrend: Equatable")
    assert "var sourceHasDays: Bool = false" in trend
    assert "sourceHasDays: sourceHasDays," in _block(trend, "func trimmed(")
    ext = models[models.index("extension SentimentTrend {"):]
    assert "self.sourceHasDays = !days.isEmpty" in _block(ext, "init(dto: SentimentTrendResponse")


# ── 30D ticks: noon, centred, with room past the plot end ──────────────────────────────────


def test_30d_ticks_sit_at_noon_centred_with_end_padding():
    chart = _code(CHART)
    unsorted = _block(chart, "private var axisDatesUnsorted: [Date]")
    month = unsorted[unsorted.index("case .month:"):unsorted.index("case .quarter:")]
    assert "bySettingHour: 12, minute: 0, second: 0" in month, "30D ticks at each tick day's noon"
    assert "stride(from: 0, through: 28, by: 7)" in month
    assert ".topTrailing" not in chart, "a trailing-anchored label sits under the days BEFORE its tick"
    anchor = _block(chart, "private func labelAnchor() -> UnitPoint?")
    assert "return trend.window == .month ? .top : nil" in anchor, "30D labels centred on their noon tick"
    assert "anchor: labelAnchor()," in _block(chart, "private var xAxis: some AxisContent")
    view = _block(chart, "private var chart: some View")
    assert ".chartXScale(domain: domain, range: .plotDimension(startPadding: 0, endPadding: xEndPadding))" in view
    assert "trend.window == .month ? Self.monthEndPadding : 0" in _block(chart, "private var xEndPadding: CGFloat")


# ── the chart waits for the SELECTED scope's feed ──────────────────────────────────────────


def test_the_chart_gate_is_per_scope():
    gate = _norm(_block(_code(VIEW), "private var visibleTrend: SentimentTrend?"))
    assert "viewModel.snapshotSavedAt != nil || viewModel.shownFeedScope == viewModel.selectedTab?.scope," in gate, (
        "gated on the once-per-identity flag alone, a chip switch draws the new scope's chart over "
        "its skeleton"
    )
    vm = _code(VM)
    assert "@Published private(set) var shownFeedScope: String?" in vm
    feed = _block(vm, "private func loadFeed(for tab: NewsFilterTab, force: Bool) async")
    cached = _block(feed, "if !force, let cached = feedCache[scope] {")
    assert "shownFeedScope = scope" in cached, "the cached paint"
    live = feed[feed.index("allNewsArticles = articles"):]
    assert live.index("hasShownFeed = true") < live.index("shownFeedScope = scope") < live.index("applyFiltersAndGroup()")
    clear = feed.index("if shownFeedScope != scope { shownFeedScope = nil }")
    assert clear < feed.index("try await apiClient.requestReturningBody("), "cleared before the new scope loads"
    assert clear < feed.index("if !force, let cached = feedCache[scope] {")
    identity = _block(vm, "func handleIdentityChange(isActiveTab: Bool) async")
    assert identity.index("shownFeedScope = nil") < identity.index("guard isActiveTab else { return }")
