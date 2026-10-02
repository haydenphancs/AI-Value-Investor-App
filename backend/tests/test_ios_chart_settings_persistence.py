"""Chart settings are set once and stay set — and the remembered range never breaks a screen.

TestFlight 1.0 (9), AVGO: "I did change Extended hours to off but it turns on again. For
everything in here, it should fix or set up once and permanently keep them."

* Build 9 predated `ee8ff0b3`, so Extended Hours defaulted ON and was never saved. Even after
  that fix only `chartType` and `showExtendedHours` were persisted: Overlays, Sub-charts and
  Earnings Dates reset on every screen. And every detail ViewModel owns its OWN `ChartSettings`
  that read UserDefaults only at init, so a change on a detail screen pushed over another left
  the screen underneath showing its stale copy — the toggle "turned back on" on going back.
* Product decision (2026-10-01): persist on THIS device, and ALSO remember the time range and
  the interval picked for each range — one choice across all five asset classes, falling back
  where a class cannot serve it (2Y is crypto-only and 400s elsewhere; crypto has no 5Y/ALL).

Pinned here, iOS half (there is no XCTest target — testing.md §3). Every scan is
comment-stripped and brace-bounded. Mutation-tested by hand — each of these turns the named
test red:
  * drop `guard !isApplyingStoredSettings` from `showEarningsDates`'s didSet  → test_every_sheet_setting…
  * delete the `showEarningsDates` didSet                                     → test_every_sheet_setting…
  * store `enabledIndicators.map(\\.rawValue)`                                → test_indicators_are_stored…
  * make the `enabledIndicators` assignment in applyStoredSettings unconditional → test_live_instances…
  * re-apply `selectedInterval` in applyStoredSettings                        → test_live_instances…
  * delete `self.applyStoredSettings()` from the sync sink / drop `syncSubscription =` /
    flip `!==` to `===`                                                       → test_live_instances…
  * call `rememberUserRange` from the Crypto range sink                       → test_range_and_interval_are_written…
  * call `rememberUserInterval` inside `rememberedInterval`                   → test_range_and_interval_are_written…
  * read `storedIntervals["\\(range)"]` (case name, not the stored rawValue)  → test_range_and_interval_are_written…
  * add a didSet to `selectedInterval`                                        → test_range_and_interval_are_written…
  * move ETF's restore below `$selectedChartRange`                            → test_each_detail_screen_restores…
  * put `newRange.defaultInterval` back in the Index range sink               → test_each_detail_screen_restores…
  * `return screenDefault` first in `resolvedRange`                           → test_the_range_fallback…
  * add `.daily` to 1W's allowed intervals                                    → test_intraday_ness…
  * drop the sheet's `type != effectiveChartType` guard                       → test_the_smaller_fixes…
  * draw the placeholder ON TOP of MainChartCanvas again                      → test_the_smaller_fixes…
  * restore the commodity slice's bare `!light.chartData.isEmpty` guard       → test_the_smaller_fixes…
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_MODELS = _IOS / "Models" / "ChartModels.swift"
_RANGES = _IOS / "Models" / "TickerDetailModels.swift"
_CHART = _IOS / "Views" / "Molecules" / "TickerChartView.swift"
_SHEET = _IOS / "Views" / "Molecules" / "Chart" / "ChartSettingsSheet.swift"

# (ViewModel, Screen, ChartAssetContext case, TickerChartView call sites on the screen)
_SCREENS = [
    ("TickerDetailViewModel", "TickerDetailView", "stock", 2),
    ("ETFDetailViewModel", "ETFDetailView", "etf", 1),
    ("CryptoDetailViewModel", "CryptoDetailView", "crypto", 1),
    ("IndexDetailViewModel", "IndexDetailView", "index", 1),
    ("CommodityDetailViewModel", "CommodityDetailView", "commodity", 1),
]

# The four settings the sheet edits, each persisted under `Self.<name>Key`.
_SHEET_SETTINGS = {"chartType", "enabledIndicators", "showExtendedHours", "showEarningsDates"}


def _strip_swift_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"//.*$", "", raw))
    return "\n".join(out)


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_swift_comments(path.read_text(encoding="utf-8"))


def _walk(src: str, start: int) -> str:
    """The brace-balanced block whose `{` is at `start`."""
    assert src[start] == "{", src[start: start + 40]
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start: j + 1]
    raise AssertionError("unbalanced braces")


def _block(src: str, header: str) -> str:
    i = src.find(header)
    assert i != -1, f"guard is stale — {header!r} not found"
    return _walk(src, src.find("{", i))


def _block_re(src: str, pattern: str) -> str:
    m = re.search(pattern, src)
    assert m, f"guard is stale — /{pattern}/ not found"
    return _walk(src, src.find("{", m.start()))


def _observer(body: str, prop: str) -> str | None:
    """The `{ didSet … }` on `@Published var <prop>: … {`, or None when it has no observer."""
    m = re.search(rf"@Published var {prop}:[^\n{{]*\{{", body)
    if not m:
        return None
    return _walk(body, m.end() - 1)


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _settings() -> str:
    return _block(_code(_MODELS), "final class ChartSettings")


def _memory() -> str:
    return _block(_code(_MODELS), "enum ChartSelectionMemory")


# ── 1. Every setting the sheet edits is persisted ─────────────────────────────


def test_every_sheet_setting_is_persisted_and_announced():
    sheet = _code(_SHEET)
    edited = set(re.findall(r"\$chartSettings\.(\w+)", sheet))
    edited |= set(re.findall(r"chartSettings\.(\w+)\s*=(?!=)", sheet))
    edited |= {m[0] for m in re.findall(r"chartSettings\.(\w+)\.(insert|remove)\(", sheet)}
    # Anti-vacuity both ways: a NEW sheet control fails here until it is persisted below.
    assert edited == _SHEET_SETTINGS, edited

    body = _settings()
    apply = _block(body, "private func applyStoredSettings()")
    for prop in sorted(_SHEET_SETTINGS):
        obs = _observer(body, prop)
        assert obs, f"{prop} has no didSet — it would reset on every screen"
        assert "guard !isApplyingStoredSettings" in obs, prop
        assert "!= oldValue" in obs, prop
        assert "UserDefaults.standard.set(" in obs, prop
        assert f"forKey: Self.{prop}Key" in obs, prop
        assert "announceChange()" in obs, prop
        assert f"forKey: Self.{prop}Key" in apply, f"{prop} is written but never restored"

    keys = re.findall(r'static let (\w+Key) = "([^"]+)"', body + "\n" + _memory())
    assert len(keys) >= 6, keys
    values = [v for _, v in keys]
    assert all(re.fullmatch(r"caydex_[a-z_]+", v) for v in values), values
    assert len(set(values)) == len(values), f"two settings share a UserDefaults key: {values}"


# ── 2. Indicators are stored by a stable id, never by their label ─────────────


def test_indicators_are_stored_by_stable_id_not_label():
    models = _code(_MODELS)
    enum = _block(models, "enum TechnicalIndicatorType")
    cases = dict(re.findall(r'case (\w+) = "([^"]*)"', enum))
    assert len(cases) >= 8, cases

    ids_block = _block(enum, "var storageID: String")
    ids = dict(re.findall(r'case \.(\w+):\s*return "([^"]+)"', ids_block))
    assert set(ids) == set(cases), f"storageID does not cover every case: {set(cases) ^ set(ids)}"
    assert "default" not in ids_block, "a default arm would let a new case share an id"
    assert all(re.fullmatch(r"[a-z0-9_]+", v) for v in ids.values()), ids
    assert len(set(ids.values())) == len(ids), "two panes share a storage id"
    assert not set(ids.values()) & set(cases.values()), "a storage id equals a display label"

    body = _settings()
    obs = _observer(body, "enabledIndicators")
    assert obs and "storageID" in obs and "rawValue" not in obs, obs
    apply = _block(body, "private func applyStoredSettings()")
    assert "compactMap(TechnicalIndicatorType.init(storageID:))" in apply


# ── 3. Live instances follow a change, and the echo cannot loop ───────────────


def test_live_instances_follow_a_change_and_cannot_loop():
    body = _settings()
    init = _block(body, "init()")
    assert "applyStoredSettings()" in init
    assert init.index("applyStoredSettings()") < init.index("NotificationCenter")
    # The RECEIVING side is the stacked-screen fix itself: the subscription must be KEPT
    # (a discarded AnyCancellable cancels at once), and the sink must re-read the store for
    # every announcement except its own.
    assert "syncSubscription = NotificationCenter.default.publisher(for: Self.didChangeNotification)" in init
    sink = _block(init, ".sink {")
    assert "[weak self]" in sink
    assert "!== self" in sink, "the receiver must skip only its OWN announcement"
    assert "self.applyStoredSettings()" in sink, "a stacked screen would keep its stale copy"

    announce = _block(body, "private func announceChange()")
    assert "NotificationCenter.default.post(name: Self.didChangeNotification, object: self)" in announce

    apply = _block(body, "private func applyStoredSettings()")
    assert "isApplyingStoredSettings = true" in apply
    assert "defer { isApplyingStoredSettings = false }" in apply
    # Assign ONLY on difference: `@Published` emits on every assignment, and the stock
    # screen refetches on `$showExtendedHours`.
    for prop in sorted(_SHEET_SETTINGS):
        assert re.search(rf"if\s+{prop}\s*!=\s*(\w+)\s*\{{\s*{prop}\s*=\s*\1\s*\}}", apply), prop
    # The interval is per-SCREEN state; syncing it would drive hidden screens' fetches.
    assert "selectedInterval" not in apply


# ── 4. Range and interval are written ONLY from a tap ─────────────────────────


def test_range_and_interval_are_written_only_from_a_user_tap():
    writers = set()
    for path in _IOS.rglob("*.swift"):
        if re.search(r"rememberUser(Range|Interval)\(", _strip_swift_comments(path.read_text(encoding="utf-8"))):
            writers.add(path.name)
    assert writers == {"ChartModels.swift", "TickerChartView.swift"}, writers

    # Inside ChartModels the names may appear only as the two DECLARATIONS — a call from a
    # reader, a sink or ChartSettings would store a coerced value.
    models = _code(_MODELS)
    mentions = re.findall(r"(static func )?rememberUser(?:Range|Interval)\(", models)
    assert mentions == ["static func ", "static func "], mentions

    memory = _memory()
    assert memory.count("UserDefaults.standard.set(") == 2, "a third write path to the range memory"
    range_writer = _block(memory, "static func rememberUserRange(")
    interval_writer = _block(memory, "static func rememberUserInterval(")
    assert "UserDefaults.standard.set(range.rawValue, forKey: rangeKey)" in range_writer
    # A tap is always a choice: no early exit (a "fallback re-tap" skip lost real choices
    # made on a stacked screen showing an older range).
    assert "return" not in range_writer, range_writer
    assert "map[range.rawValue] = interval.rawValue" in interval_writer
    assert "UserDefaults.standard.set(map, forKey: intervalByRangeKey)" in interval_writer
    for reader in ("static func restoredSelection(", "static func rememberedInterval("):
        r = _block(memory, reader)
        assert ".set(" not in r and "removeObject" not in r, f"{reader} writes"
        assert "rememberUser" not in r, f"{reader} stores a coerced value"

    # The READ side must use the same keys and key form as the writers, or every screen
    # silently opens on its default again — the "it resets" complaint, unpinned.
    stored_range = _block(memory, "private static var storedRange")
    assert "string(forKey: rangeKey)" in stored_range
    assert "ChartTimeRange.init(rawValue:)" in stored_range
    stored_intervals = _block(memory, "private static var storedIntervals")
    assert "dictionary(forKey: intervalByRangeKey) as? [String: String]" in stored_intervals
    assert "storedIntervals[range.rawValue].flatMap(ChartInterval.init(rawValue:))" in \
        _block(memory, "static func rememberedInterval(")

    decl = re.search(r"@Published var selectedInterval:[^\n]*", _settings())
    assert decl and "{" not in decl.group(0), "selectedInterval grew an observer — it would store coerced values"

    chart = _code(_CHART)
    pill = _block(chart, "ForEach(assetContext.allowedRanges")
    assert "ChartSelectionMemory.rememberUserRange(range)" in pill
    row = _block(chart, "private func intervalRow(")
    assert "ChartSelectionMemory.rememberUserInterval(interval, for: selectedRange)" in row


# ── 5. Every detail screen restores before its sinks ──────────────────────────


@pytest.mark.parametrize("vm_name, screen_name, ctx, sites", _SCREENS)
def test_each_detail_screen_restores_before_its_sinks(vm_name, screen_name, ctx, sites):
    vm = _code(_IOS / "ViewModels" / f"{vm_name}.swift")
    assert f"let chartAssetContext: ChartAssetContext = .{ctx}" in vm
    init = _block_re(vm, r"init\(\s*(tickerSymbol|etfSymbol|cryptoSymbol|indexSymbol|commoditySymbol):")

    restore = "ChartSelectionMemory.restoredSelection(in: chartAssetContext, screenDefault: selectedChartRange)"
    assert restore in init, vm_name
    sink_at = init.index("$selectedChartRange")
    assert init.index(restore) < init.index("selectedChartRange = restored.range") < sink_at
    assert init.index(restore) < init.index("chartSettings.selectedInterval = restored.interval") < sink_at

    range_sink = init[sink_at: init.index("chartSettings.$selectedInterval")]
    suppress_on = range_sink.index("self.suppressIntervalReload = true")
    assign = re.search(
        r"self\.chartSettings\.selectedInterval = ChartSelectionMemory\.rememberedInterval\(for: \w+, in: self\.chartAssetContext\)",
        range_sink,
    )
    assert assign, f"{vm_name}: the range sink no longer reads the remembered interval"
    assert suppress_on < assign.start() < range_sink.index("self.suppressIntervalReload = false")

    assert not re.search(r"selectedInterval\s*=\s*[\w.]*defaultInterval", init), \
        f"{vm_name}: a bare defaultInterval would discard the remembered interval"
    assert "rememberUser" not in vm, f"{vm_name} writes the range memory — only a tap may"

    screen = _code(_IOS / "Views" / "Screens" / f"{screen_name}.swift")
    assert screen.count("assetContext: viewModel.chartAssetContext") == sites, screen_name
    assert not re.search(r"assetContext:\s*\.(stock|etf|crypto|index|commodity)\b", screen), screen_name


# ── 6. The fallback: per asset class, deterministic, never a 400 ──────────────


def _range_cases() -> list[tuple[str, str]]:
    enum = _block(_code(_RANGES), "enum ChartTimeRange")
    return re.findall(r'case (\w+) = "([^"]+)"', enum)


def _allowed_ranges() -> dict[str, list[str]]:
    names = [n for n, _ in _range_cases()]
    body = _block(_code(_MODELS), "var allowedRanges: [ChartTimeRange]")
    crypto_arm = body[body.index("case .crypto:"): body.index("case .stock")]
    crypto = re.findall(r"\.(\w+)", crypto_arm.split("return", 1)[1])
    other_arm = body[body.index("case .stock"):]
    assert "ChartTimeRange.allCases.filter { $0 != .twoYears }" in other_arm, other_arm
    others = [n for n in names if n != "twoYears"]
    return {"crypto": crypto, "stock": others, "etf": others, "index": others, "commodity": others}


def test_the_range_fallback_resolves_per_asset_class_and_never_writes_back():
    cases = _range_cases()
    assert [raw for _, raw in cases] == ["1D", "1W", "3M", "6M", "1Y", "2Y", "5Y", "ALL"], \
        "the fallback ranks ranges by declaration order — it must stay shortest → longest"

    models = _code(_MODELS)
    assert _squash(_block(models, "func resolvedRange(")) == (
        "{guardletpreferredelse{returnscreenDefault}"
        "ifallowedRanges.contains(preferred){returnpreferred}"
        "letrank={(range:ChartTimeRange)inChartTimeRange.allCases.firstIndex(of:range)??0}"
        "returnallowedRanges.filter{rank($0)<=rank(preferred)}.max{rank($0)<rank($1)}??screenDefault}"
    )
    assert _squash(_block(models, "func resolvedInterval(")) == (
        "{letallowed=allowedIntervals(for:range)"
        "ifletpreferred,allowed.contains(preferred){returnpreferred}"
        "returnallowed.contains(range.defaultInterval)?range.defaultInterval:(allowed.first??range.defaultInterval)}"
    )

    # The same rule in Python, over every stored range × asset class.
    order = [n for n, _ in cases]
    rank = {n: i for i, n in enumerate(order)}
    allowed = _allowed_ranges()
    assert len(allowed["crypto"]) == 6 and "twoYears" in allowed["crypto"], allowed["crypto"]

    def resolve(stored: str, ctx: str, default: str) -> str:
        if stored in allowed[ctx]:
            return stored
        shorter = [r for r in allowed[ctx] if rank[r] <= rank[stored]]
        return max(shorter, key=rank.__getitem__) if shorter else default

    for ctx in allowed:
        for stored in order:
            got = resolve(stored, ctx, "threeMonths")
            assert got in allowed[ctx], f"{stored} on {ctx} would open on an unoffered range"
            assert rank[got] <= rank[stored]
    for ctx in ("stock", "etf", "index", "commodity"):
        assert resolve("twoYears", ctx, "threeMonths") == "oneYear"
    assert resolve("fiveYears", "crypto", "oneDay") == "twoYears"
    assert resolve("all", "crypto", "oneDay") == "twoYears"

    memory = _memory()
    assert "context.resolvedRange(preferred: storedRange, screenDefault: screenDefault)" in _block(memory, "static func restoredSelection(")
    assert "context.resolvedInterval(preferred: preferred, for: range)" in _block(memory, "static func rememberedInterval(")


def _intervals_by_range() -> tuple[dict[str, list[str]], dict[str, str], set[str]]:
    ranges = _code(_RANGES)
    enum = _block(ranges, "enum ChartTimeRange")
    allowed_block = _block(enum, "var allowedIntervals: [ChartInterval]")
    allowed = {k: re.findall(r"\.(\w+)", v) for k, v in re.findall(r"case \.(\w+):\s*return \[([^\]]*)\]", allowed_block)}
    default_block = _block(enum, "var defaultInterval: ChartInterval")
    defaults = dict(re.findall(r"case \.(\w+):\s*return \.(\w+)", default_block))
    intraday_block = _block(_code(_MODELS), "var isIntraday: Bool")
    true_arm = intraday_block[: intraday_block.index("return true")]
    intraday = set(re.findall(r"\.(\w+)", true_arm))
    return allowed, defaults, intraday


def test_intraday_ness_is_a_property_of_the_range():
    """Ticker's range sink gates its chart timer on `range.defaultInterval.isIntraday`
    (pinned elsewhere). With per-range interval memory that stays right only while every
    interval a range allows has the same intraday-ness as its default."""
    allowed, defaults, intraday = _intervals_by_range()
    assert len(allowed) == 8 and len(defaults) == 8, (allowed, defaults)
    assert len(intraday) == 5, intraday
    for rng, intervals in allowed.items():
        kinds = {i in intraday for i in intervals}
        assert len(kinds) == 1, f"{rng} mixes intraday and daily intervals: {intervals}"
        assert defaults[rng] in intervals, f"{rng}'s default interval is not one it allows"
    # Crypto's single-granularity overrides keep the range's default (so the fallback never
    # needs `allowed.first`, and the picker is hidden where the interval is coerced).
    crypto = _block(_code(_MODELS), "func allowedIntervals(for range: ChartTimeRange)")
    overrides = dict(re.findall(r"case \.(\w+):\s*return \[\.(\w+)\]", crypto))
    assert overrides == {"oneDay": defaults["oneDay"], "oneWeek": defaults["oneWeek"]}, overrides


# ── 7. The smaller fixes the remembered settings made necessary ───────────────


def test_the_smaller_fixes_stay_in():
    sheet = _code(_SHEET)
    guard = sheet.index("guard type != effectiveChartType else { return }")
    assert guard < sheet.index("chartSettings.chartType = type"), \
        "re-tapping the fallback-highlighted type would store it over the user's choice"
    assert "var unavailableSubCharts: Set<TechnicalIndicatorType> = []" in sheet
    subs = _block(sheet, "ForEach(assetContext.allowedSubCharts)")
    assert "unavailableSubCharts.contains(indicator)" in subs and ".disabled(isUnavailable)" in subs

    vm = _code(_IOS / "ViewModels" / "TickerDetailViewModel.swift")
    ext_sink = vm[vm.index("chartSettings.$showExtendedHours"):]
    ext_sink = ext_sink[: ext_sink.index(".store(in: &cancellables)")]
    assert ext_sink.index("guard self.chartSettings.selectedInterval.isIntraday else { return }") \
        < ext_sink.index("fetchChartData"), "a synced toggle refetches a byte-identical daily chart"

    chart = _code(_CHART)
    missing = _block(chart, "private var unavailableSubCharts")
    assert "guard !pricePoints.isEmpty else { return [] }" in missing, "panes would jump in on first bars"
    stoch = missing.index("missing.insert(.stochastic)")
    assert "$0.high != nil && $0.low != nil" in missing[:stoch]
    assert "($0.volume ?? 0) > 0" in missing[stoch:]
    assert "chartSettings" not in missing, "the data filter must never write the preference"
    # A saved Candle/Bar on a close-only series drew flat dashes: the bars decide, the
    # preference is never rewritten, and sheet + canvas agree on the coerced type.
    types = _block(chart, "private var unavailableChartTypes")
    assert "guard !pricePoints.isEmpty else { return [] }" in types
    assert "$0.open != nil && $0.high != nil && $0.low != nil" in types
    assert "[.candle, .bar]" in types and "chartSettings" not in types
    drawn_type = _block(chart, "private var drawnChartType")
    assert "!unavailableChartTypes.contains(preferred)" in drawn_type
    assert "assetContext.allowedChartTypes.contains(preferred)" in drawn_type
    assert "chartType: drawnChartType," in chart
    assert "unavailableChartTypes: unavailableChartTypes" in chart
    assert "&& !unavailableChartTypes.contains(chartSettings.chartType)" in sheet
    assert ".disabled(unavailableChartTypes.contains(type))" in sheet
    drawn = _block(chart, "private var drawnSubCharts")
    assert "activeSubCharts.filter { assetContext.allowedSubCharts.contains($0) }" in drawn
    assert "!missing.contains($0)" in drawn
    assert "ForEach(drawnSubCharts)" in chart
    assert "unavailableSubCharts: unavailableSubCharts" in chart
    # The placeholder REPLACES the canvas — on top of it, the note overprinted the canvas's
    # own centred "No chart data".
    assert re.search(
        r"if pricePoints\.isEmpty, let placeholder \{\s*chartPlaceholderView\(placeholder\)\s*\}"
        r"\s*else \{\s*MainChartCanvas\(",
        chart,
    ), "the placeholder must replace MainChartCanvas, not overlay it"
    assert chart.count("chartPlaceholderView(placeholder)") == 1

    ticker = _code(_IOS / "Views" / "Screens" / "TickerDetailView.swift")
    core = ticker[ticker.index("else if let core = viewModel.coreData"):]
    core = core[: core.index("else if let errorMessage")]
    assert "placeholder: viewModel.isLoading ? .loading : nil" in core
    assert ticker.count("placeholder:") == 1, "only the fast-core chart waits on a later response"

    commodity = _code(_IOS / "Views" / "Screens" / "CommodityDetailView.swift")
    assert "placeholder: viewModel.selectedChartRange.defaultInterval.isIntraday" in commodity
    # The same [] comes back for FRED (never intraday) and for a FAILED ETF-backed metal
    # fetch, so the wording must be true of both — never "no intraday prices" about gold.
    assert "Intraday chart unavailable" in commodity and "No intraday prices" not in commodity
    # A user range change shows ITS answer, even an empty one — else the previous range's
    # bars stay under the new pill and the note can never appear after a tap.
    slice_ = _block(_code(_IOS / "ViewModels" / "CommodityDetailViewModel.swift"),
                    "private func refreshLiveSlice(")
    assert "if includeChart, userInitiated || !light.chartData.isEmpty {" in slice_


# ── 8. The 1W axis hour must read as a TIME on a 24-hour phone ───────────────


def test_week_axis_hour_follows_the_users_clock_setting():
    """Same TestFlight item, the screenshot: the AVGO 1W axis read "Mon 07 · Tue 13 · Thu 12".
    A FIXED `dateFormat = "EEE h a"` is rewritten by iOS to `HH` (AM/PM dropped) when
    24-Hour Time is on, so hours printed as day-of-month numbers. Reproduced on the
    Simulator with AppleICUForce24HourTime. The formatter must be built from a localized
    template, and the 24-hour form must keep its minutes."""
    models = _code(_RANGES)
    fmt = _block(models, "static let weekdayTime: DateFormatter")
    assert "dateFormat =" not in fmt, "a fixed pattern is rewritten under 24-Hour Time"
    assert 'DateFormatter.dateFormat(fromTemplate: "j", options: 0, locale: f.locale)' in fmt
    assert 'f.setLocalizedDateFormatFromTemplate(hourPattern.contains("a") ? "EEEj" : "EEEjmm")' in fmt
    # Anti-vacuity: the 1W intraday arm actually uses this formatter.
    arm = _block(models, "func formatDateForXAxis(")
    week = arm[arm.index("case .oneWeek:"): arm.index("case .threeMonths")]
    assert "ChartDateFormatters.weekdayTime.string(from: date)" in week
