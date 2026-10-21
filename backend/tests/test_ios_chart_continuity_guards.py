"""Chart continuity, scroll-back history and chart lag (2026-10-20) — the iOS half, pinned.

The detail chart used to (a) throw the user's pan and zoom away on every 30 s tick and on the
fast-core → full-overview swap (both bumped `chartDataVersion`), (b) stop at the range's own
bars with no way to scroll further back, and (c) re-run the whole chart body — every
indicator over every loaded bar, both canvases — on each scrub step, because the canvases
observed the crosshair. The approved plan (§I3–§I5) fixes each, and these guards pin the
shapes that keep them fixed:

  * the crosshair is HELD, never observed, by the chart; the canvases are `Equatable` over
    every stored property and wrapped in `.equatable()`;
  * indicators are computed once, in `ChartSeries.build`, never in a view;
  * the viewport window is right-anchored and published only when it changes;
  * 1D (one session laid out by time) never pans or zooms;
  * no renderer property is an `ArraySlice` (renderers subscript with zero-based offsets);
  * every detail ViewModel funnels each bar write through ONE function that absorbs into the
    history loader and bumps `chartDataVersion` only on a new stamp — never a timer or tick;
  * the history endpoint sends exactly the backend's contract and is account-only;
  * a pan that starts on the chart never also pops the screen (BackSwipe);
  * the loader keeps only bars strictly older than each payload and never bridges a gap.

There is no XCTest target (testing.md §3): every scan is comment-stripped and brace- or
paren-bounded to the declaration it means. Mutation-tested by hand once each — a COPY of the
named Swift source, mutated in a throwaway Python snippet (never the Swift file), turns the
named test red:
  * `@State private var crosshairState` → `@StateObject private var crosshairState`
                                                      → test_the_chart_holds_the_crosshair…
  * add `@ObservedObject var crosshairState: CrosshairState` to MainChartCanvas
                                                      → test_the_chart_holds_the_crosshair…
  * drop `&& lhs.overlays == rhs.overlays` from MainChartCanvas.==  → test_the_canvases_are…
  * add a stored `let note: String` to MainChartCanvas (not in ==)  → test_the_canvases_are…
  * drop the `.equatable()` after MainChartCanvas(…)               → test_the_canvases_are…
  * call `TechnicalIndicatorCalculator.rsi(` from SubChartCanvas    → test_indicators_are…
  * `slowStochastic(` → `stochastic(` in ChartSeries.build          → test_indicators_are…
  * `if clamped != window { window = clamped }` → `window = clamped` → test_the_viewport…
  * `var endOffset: Double` → `var startIndex: Double` in ChartWindow → test_the_viewport…
  * `canNavigate: !usesIntradayTimeMapping && …` → `canNavigate: !awaiting && …`
                                                      → test_1d_never_pans_or_zooms
  * `let drawRange: Range<Int>` → `let drawRange: ArraySlice<Int>` in a renderer
                                                      → test_no_renderer_property…
  * put `chartDataVersion += 1` in the ETF `refreshLiveSlice`        → test_every_detail_view_model…
  * `if stamp != lastChartStamp {` → `if true {` in a writer         → test_every_detail_view_model…
  * drop `if let limit` (send limit unconditionally)                → test_the_history_endpoint…
  * move `.getChartHistory` to `.public`                            → test_the_history_endpoint…
  * delete the chartTouchRegion check from BackSwipe's onEnded      → test_backswipe_leaves…
  * apply `.environment(\\.chartTouchRegion, …)` BEFORE `.backSwipe` → test_backswipe_leaves…
  * `older = []` deleted from the loader's `cut == store.count` arm → test_the_loader_keeps…
  * `dates[mid] < cutoff` → `dates[mid] <= cutoff` in lowerBound     → test_the_loader_keeps…
"""
from __future__ import annotations

import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"
_VIEWS = _IOS / "Views"
_CHART = _VIEWS / "Molecules" / "TickerChartView.swift"
_CHART_DIR = _VIEWS / "Molecules" / "Chart"
_MAIN_CANVAS = _CHART_DIR / "MainChartCanvas.swift"
_SUB_CANVAS = _CHART_DIR / "SubChartCanvas.swift"
_CROSSHAIR = _CHART_DIR / "ChartCrosshair.swift"
_SERIES = _IOS / "Core" / "ChartSeries.swift"
_VIEWPORT = _IOS / "Models" / "ChartViewport.swift"
_LOADER = _IOS / "Core" / "ViewModels" / "ChartHistoryLoader.swift"
_HISTORY_MODELS = _IOS / "Models" / "ChartHistoryModels.swift"
_ENDPOINT = _IOS / "Core" / "Services" / "APIEndpoint.swift"
_BACKSWIPE = _VIEWS / "Modifiers" / "BackSwipe.swift"
_BACKEND = _REPO / "backend" / "app" / "api" / "v1"

# (ViewModel, Screen, the ONE chart-write function, the symbol the loader pages by,
#  the ChartHistoryAsset case, the extendedHours argument, the timer / live-slice functions)
_DETAIL = [
    ("TickerDetailViewModel", "TickerDetailView", "private func recordChartWrite(", "tickerSymbol",
     "stocks", "stamp.extendedHours",
     ["private func startChartRefreshTimer()", "private func pollQuotePrice()"]),
    ("ETFDetailViewModel", "ETFDetailView", "private func didWriteChartBars(", "etfSymbol",
     "etfs", "false",
     ["private func startChartRefreshTimer()", "private func refreshLiveSlice(includeChart: Bool)"]),
    ("IndexDetailViewModel", "IndexDetailView", "private func didWriteChartBars(", "indexSymbol",
     "indices", "false",
     ["private func startChartRefreshTimer()", "private func refreshLiveSlice(includeChart: Bool)"]),
    ("CryptoDetailViewModel", "CryptoDetailView", "private func didWriteChartBars(", "cryptoSymbol",
     "crypto", "false",
     ["private func startChartRefreshTimer()", "private func refreshLiveSlice(includeChart: Bool)",
      "private func refreshHeaderOnly()", "private func refreshChartOnly()"]),
    ("CommodityDetailViewModel", "CommodityDetailView", "private func didWriteChartBars(", "commoditySymbol",
     "commodities", "false",
     ["private func startChartRefreshTimer()",
      "private func refreshLiveSlice(includeChart: Bool, userInitiated: Bool)"]),
]


# ── helpers (the style of test_ios_chart_extended_window.py) ─────────────────


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


def _walk(src: str, start: int, open_ch: str = "{") -> str:
    """The delimiter-balanced span whose opener is at `start`."""
    close_ch = {"{": "}", "(": ")"}[open_ch]
    assert src[start] == open_ch, src[start: start + 40]
    depth = 0
    for j in range(start, len(src)):
        if src[j] == open_ch:
            depth += 1
        elif src[j] == close_ch:
            depth -= 1
            if depth == 0:
                return src[start: j + 1]
    raise AssertionError(f"unbalanced {open_ch}{close_ch}")


def _block(src: str, header: str) -> str:
    i = src.find(header)
    assert i != -1, f"guard is stale — {header!r} not found"
    return _walk(src, src.find("{", i))


def _call(src: str, opener: str) -> str:
    """The paren-balanced arguments of the first `opener(` call in `src`."""
    assert opener.endswith("("), opener
    i = src.find(opener)
    assert i != -1, f"guard is stale — {opener!r} not found"
    return _walk(src, i + len(opener) - 1, "(")


def _after_call(src: str, opener: str) -> str:
    """What follows the first `opener(` … `)` call — where its modifiers are."""
    i = src.find(opener)
    assert i != -1, f"guard is stale — {opener!r} not found"
    args = _walk(src, i + len(opener) - 1, "(")
    return src[i + len(opener) - 1 + len(args):]


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _stored_properties(block: str) -> list[str]:
    """Names of the stored `let`/`var` properties declared at the TOP level of a type body
    (`block` starts at its `{`). Computed properties (a `{` on the line), property-wrapped
    ones (`@Environment …`) and anything nested deeper are skipped."""
    names, depth = [], 0
    for line in block.splitlines():
        if depth == 1:
            m = re.match(
                r"\s*(?:(?:private|fileprivate|internal|public)(?:\(set\))?\s+)?(?:let|var)\s+(\w+)\s*:", line
            )
            if m and "{" not in line:
                names.append(m.group(1))
        depth += line.count("{") - line.count("}")
    return names


# ── 1. The crosshair is held, never observed, by the chart ───────────────────


def test_the_chart_holds_the_crosshair_but_never_observes_it():
    """Observed by TickerChartView, every scrub step re-ran the whole chart body; observed by
    MainChartCanvas, it redrew the price path on every step. Only the leaf overlays (scrub
    header, crosshair, pane readouts) may observe it."""
    chart = _block(_code(_CHART), "struct TickerChartView: View")
    assert "@State private var crosshairState = CrosshairState()" in chart, (
        "TickerChartView must OWN the crosshair as plain @State (identity only, no observation)"
    )
    assert not re.search(
        r"@(?:StateObject|ObservedObject|EnvironmentObject)\s+(?:private\s+)?var\s+\w+\s*(?::\s*CrosshairState\b|=\s*CrosshairState\()",
        chart,
    ), "TickerChartView observes the crosshair again — every scrub step re-renders the chart"

    canvas = _code(_MAIN_CANVAS)
    assert "struct MainChartCanvas" in canvas, "guard is stale — MainChartCanvas moved"
    assert "CrosshairState" not in canvas and "crosshairState" not in canvas, (
        "MainChartCanvas must not touch the crosshair at all — a scrub step would redraw it"
    )
    assert "crosshairState:" not in _call(chart, "MainChartCanvas("), "the canvas is handed the crosshair"

    pane = _block(_code(_SUB_CANVAS), "private struct SubChartPane: View, Equatable")
    assert "CrosshairState" not in pane and "crosshairState" not in pane, (
        "the indicator pane must not observe the crosshair — only its line and readout do"
    )


# ── 2. The canvases are Equatable over every stored property ─────────────────


_MAIN_EQ_FIELDS = [
    "series.revision", "frame", "yRange", "isPositive", "chartType", "overlays",
    "showExtendedHours", "chartEventDates", "useIntradayTimeMapping", "sessionWindow",
    "baselineClose",
]


def _assert_eq_names_every_stored_property(body: str, eq: str, type_name: str) -> None:
    stored = _stored_properties(body)
    assert len(stored) >= 3, f"guard is stale — {type_name}'s stored properties not found: {stored}"
    for prop in stored:
        # `series` is compared by its revision (an O(1) stand-in for its bars).
        token = "series.revision" if prop == "series" else prop
        assert f"lhs.{token} == rhs.{token}" in eq, (
            f"{type_name}.== ignores `{prop}` — a change to it would never redraw the canvas"
        )


def test_the_canvases_are_equatable_over_every_stored_property():
    main = _block(_code(_MAIN_CANVAS), "struct MainChartCanvas: View, Equatable")
    eq = _block(main, "static func == (lhs: MainChartCanvas, rhs: MainChartCanvas) -> Bool")
    for field in _MAIN_EQ_FIELDS:
        assert f"lhs.{field} == rhs.{field}" in eq, f"MainChartCanvas.== no longer compares {field}"
    _assert_eq_names_every_stored_property(main, eq, "MainChartCanvas")

    pane = _block(_code(_SUB_CANVAS), "private struct SubChartPane: View, Equatable")
    pane_eq = _block(pane, "static func == (lhs: SubChartPane, rhs: SubChartPane) -> Bool")
    for field in ("indicator", "series.revision", "frame"):
        assert f"lhs.{field} == rhs.{field}" in pane_eq, f"SubChartPane.== no longer compares {field}"
    _assert_eq_names_every_stored_property(pane, pane_eq, "SubChartPane")

    # `Equatable` alone does nothing: SwiftUI compares with `==` only inside `.equatable()`.
    chart = _block(_code(_CHART), "struct TickerChartView: View")
    assert re.match(r"\s*\.equatable\(\)", _after_call(chart, "MainChartCanvas(")), (
        "MainChartCanvas(…) is no longer wrapped in .equatable() — its == is never consulted"
    )
    container = _block(_code(_SUB_CANVAS), "struct SubChartCanvas: View")
    assert re.match(r"\s*\.equatable\(\)", _after_call(container, "SubChartPane(")), (
        "SubChartPane(…) is no longer wrapped in .equatable()"
    )


# ── 3. Indicators are computed once, in ChartSeries ──────────────────────────


def test_indicators_are_computed_only_in_chart_series():
    """MA/BB/RSI/MACD/Stoch used to be recomputed over every loaded bar inside the canvases'
    bodies — on every pan step and scrub frame. They are built once per series."""
    offenders = [
        str(p.relative_to(_IOS)) for p in sorted(_VIEWS.rglob("*.swift"))
        if "TechnicalIndicatorCalculator." in _code(p)
    ]
    assert offenders == [], f"a view computes indicators (do it in ChartSeries.build): {offenders}"
    # Anti-vacuity: the Chart folder is really scanned.
    assert (_CHART_DIR / "SubChartCanvas.swift") in set(_VIEWS.rglob("*.swift"))

    build = _block(_code(_SERIES), "static func build(_ input: ChartSeriesInput) -> ChartSeries")
    for fn in ("sma(", "bollingerBands(", "rsi(", "macd(", "slowStochastic("):
        assert f"TechnicalIndicatorCalculator.{fn}" in build, f"ChartSeries.build no longer computes {fn}…)"
    # The Stoch pane is the SLOW stochastic (%K smoothed); the fast one is only its input.
    assert "TechnicalIndicatorCalculator.stochastic(" not in build


# ── 4. The viewport window: right-anchored, published only on change ──────────


def test_the_viewport_publishes_only_on_change_and_is_right_anchored():
    vp = _code(_VIEWPORT)
    window = _block(vp, "struct ChartWindow: Equatable")
    assert "var endOffset: Double" in window and "var visibleCount: Double" in window, (
        "the window must be measured from the NEWEST bar — a left-anchored index has to be "
        "shifted on every prepended page, and any frame drawn in between jumps"
    )
    assert _stored_properties(window) == ["endOffset", "visibleCount"], _stored_properties(window)

    bounds = _block(vp, "struct ChartViewportBounds: Equatable")
    assert "Double(total) - w.visibleCount - w.endOffset" in _block(bounds, "func slotLeft(_ w: ChartWindow) -> Double")

    state = _block(vp, "final class ChartViewportState: ObservableObject")
    assert "@Published private(set) var window: ChartWindow" in state
    assert state.count("@Published") == 1, "only the window may publish — anchors and bounds re-render nothing"
    setter = _block(state, "func set(_ w: ChartWindow)")
    assert re.search(r"if\s+clamped\s*!=\s*window\s*\{\s*window\s*=\s*clamped\s*\}", setter), (
        "the window must be assigned only when it changed — every sub-slot touch event "
        "republished it and re-ran the whole chart body"
    )
    writes = re.findall(r"(?<![\w.])window\s*=(?!=)", state)
    assert len(writes) == 1, f"the window is written outside `set` ({len(writes)} writes)"


# ── 5. 1D never pans or zooms ─────────────────────────────────────────────────


def test_1d_never_pans_or_zooms():
    """1D is one session laid out by TIME of day: its x is a clock, not a bar index, so a pan
    or pinch there would slide bars off their times."""
    chart = _block(_code(_CHART), "struct TickerChartView: View")
    assert re.search(r"private var usesIntradayTimeMapping: Bool \{\s*displayedRange == \.oneDay\s*\}", chart)
    layer = _call(chart, "ChartInteractionLayer(")
    assert re.search(r"\bcanNavigate:\s*canNavigate\b", layer), "the layer must get the chart's canNavigate"
    assert re.search(r"\blet canNavigate = !usesIntradayTimeMapping\s*&&", chart), (
        "canNavigate must start `!usesIntradayTimeMapping && …` — 1D would pan"
    )

    interaction = _block(_code(_CROSSHAIR), "struct ChartInteractionLayer: View")
    pinch = _block(interaction, "private var pinchGesture: some Gesture")
    assert re.search(r"guard canNavigate\b", pinch), "the pinch no longer honours canNavigate"
    pan = _call(interaction, "ChartPanGesture(")
    can_begin = _block(pan, "canBegin:")
    assert re.search(r"\bin\s+canNavigate\s*&&", can_begin), "the pan no longer honours canNavigate"


# ── 6. No renderer property is an ArraySlice ──────────────────────────────────


def test_no_renderer_property_is_an_array_slice():
    """Renderers subscript with ZERO-based offsets; an ArraySlice keeps its parent's indices,
    so `slice[0]` on a window that starts at bar 4,000 traps."""
    files = sorted(_CHART_DIR.glob("*.swift")) + [_CHART]
    assert len(files) >= 8, files
    found = []
    for path in files:
        for m in re.finditer(r"\b(?:let|var)\s+(\w+)\s*:\s*\[?\s*ArraySlice\b", _code(path)):
            found.append(f"{path.name}: {m.group(1)}")
    assert found == [], f"renderer properties typed ArraySlice: {found}"


# ── 7. Every detail ViewModel: one funnel, absorb always, bump only on a new stamp ──


@pytest.mark.parametrize("vm_name, screen_name, writer, symbol, asset, ext, timers", _DETAIL)
def test_every_detail_view_model_absorbs_and_bumps_only_on_a_new_stamp(
    vm_name, screen_name, writer, symbol, asset, ext, timers
):
    vm = _code(_IOS / "ViewModels" / f"{vm_name}.swift")

    assert "let chartHistory: ChartHistoryLoader" in vm, vm_name
    assert re.search(rf"ChartHistoryLoader\(asset:\s*\.{asset}\b", vm), f"{vm_name} pages the wrong router"
    assert ("chartHistory.objectWillChange.sink{[weakself]_inself?.objectWillChange.send()}"
            ".store(in:&cancellables)") in _squash(vm), f"{vm_name} does not re-render on a loaded page"
    older = _block(vm, "func loadOlderChartBars(userInitiated: Bool)")
    assert "chartHistory.requestOlder(force: userInitiated)" in older, vm_name

    # ONE funnel: every bar write absorbs into the loader, so its store stays contiguous.
    body = _block(vm, writer)
    assert vm.count("chartHistory.absorb(") == 1 and "chartHistory.absorb(" in body, (
        f"{vm_name}: every chart write must go through `{writer.strip('(')}`"
    )
    absorb = _call(body, "chartHistory.absorb(")
    assert re.search(rf"\bsymbol:\s*{symbol}\b", absorb), f"{vm_name} absorbs under another symbol"
    assert re.search(rf"\bextendedHours:\s*{re.escape(ext)}\s*\)$", absorb), (
        f"{vm_name} must absorb with `extendedHours: {ext}`"
    )

    # The bump: only in the funnel, after the absorb, behind a stamp comparison.
    bumps = re.findall(r"chartDataVersion\s*(?:\+=|-=|=(?!=))", vm)
    assert len(bumps) == 1, f"{vm_name} writes chartDataVersion {len(bumps)} times — only the funnel may"
    bump_at = body.find("chartDataVersion += 1")
    assert bump_at != -1, f"{vm_name}: the bump left the funnel"
    guard = re.search(
        r"guard\s+stamp\s*!=\s*lastChartStamp\b[^\n]*else\s*\{\s*return\s*\}|if\s+stamp\s*!=\s*lastChartStamp\s*\{",
        body,
    )
    assert guard and body.index("chartHistory.absorb(") < guard.start() < bump_at, (
        f"{vm_name}: chartDataVersion must move only when the stamp changes — a same-stamp "
        "write (tick, core→full swap, pull-to-refresh) would reset the user's window"
    )
    assert guard.start() < body.index("lastChartStamp = stamp") < bump_at, vm_name

    # The 30 s timer and the live slices never touch the version or the stamp themselves.
    for header in timers:
        fn = _block(vm, header)
        assert "chartDataVersion" not in fn, f"{vm_name}.{header} touches chartDataVersion"
        assert "lastChartStamp =" not in fn, f"{vm_name}.{header} rewrites the stamp (a forced bump)"


# ── 8. The history endpoint: the backend's contract, account-only ─────────────


def _arm(switch_block: str, case_name: str) -> str:
    """One `case .<name>…:` arm of a switch, up to the next `case .` / `default:`."""
    m = re.search(rf"\bcase \.{case_name}\b", switch_block)
    assert m, f"guard is stale — no `.{case_name}` arm"
    rest = switch_block[m.end():]
    nxt = re.search(r"\n\s*(?:case \.|default:)", rest)
    return rest[: nxt.start()] if nxt else rest


def _policies(auth_block: str) -> dict[str, str]:
    policies = {}
    for m in re.finditer(
        r"\bcase\s+((?:\.\w+(?:\([^)]*\))?\s*,\s*)*\.\w+(?:\([^)]*\))?)\s*:\s*return\s+\.(\w+)", auth_block
    ):
        for name in re.findall(r"\.(\w+)", m.group(1)):
            policies[name] = m.group(2)
    return policies


def test_the_history_endpoint_sends_the_contract_and_is_account_only():
    ep = _code(_ENDPOINT)
    assert ("case getChartHistory(asset: ChartHistoryAsset, symbol: String, interval: String, "
            "before: String, limit: Int?, extendedHours: Bool)") in ep

    path = _arm(_block(ep, "nonisolated var path: String"), "getChartHistory")
    assert 'return "/api/v1/\\(asset.rawValue)/\\(symbol)/chart/history"' in path, path

    query = _arm(_block(ep, "nonisolated var queryParameters: [String: String]?"), "getChartHistory")
    # `before` is the oldest loaded bar's date, passed through untouched (exclusive cursor).
    assert 'var params = ["interval": interval, "before": before]' in query, query
    assert re.search(r'if let limit \{\s*params\["limit"\] = String\(limit\)\s*\}', query), (
        "limit must be sent only when set — the server's own page size applies otherwise"
    )
    assert re.search(
        r'if extendedHours, case \.stocks = asset \{\s*params\["extended_hours"\] = "true"\s*\}', query
    ), "extended_hours goes only to the stocks route, and only when true"
    assert query.count('params["limit"]') == 1 and query.count('params["extended_hours"]') == 1, query
    assert "return params" in query

    auth = _block(ep, "nonisolated var authPolicy: AuthPolicy")
    assert "default:" not in auth, "authPolicy must stay exhaustive (auth.md §1)"
    policies = _policies(auth)
    assert len(policies) > 100, f"guard is stale — the policy parser found {len(policies)} cases"
    for case in ("getChartHistory", "getCryptoChart"):
        assert policies.get(case) == "signInRequired", (
            f"{case} is {policies.get(case)!r}: licensed price history must be account-only"
        )


def test_every_history_asset_names_a_router_that_serves_history():
    """`ChartHistoryAsset.rawValue` IS the path prefix: a case whose router does not exist,
    or does not serve `/chart/history`, is a 404 the loader reads as "start of history"."""
    enum = _block(_code(_HISTORY_MODELS), "nonisolated enum ChartHistoryAsset: String, Sendable")
    cases = re.findall(r"\bcase (\w+)\b", enum)
    assert cases == ["stocks", "etfs", "indices", "commodities", "crypto"], cases
    api = (_BACKEND / "api.py").read_text(encoding="utf-8")
    for case in cases:
        m = re.search(rf'include_router\((\w+)\.router, prefix="/{case}"', api)
        assert m, f"no backend router is mounted at /{case}"
        module = (_BACKEND / "endpoints" / f"{m.group(1)}.py").read_text(encoding="utf-8")
        assert re.search(r'@router\.get\("/\{\w+\}/chart/history"', module), f"/{case} serves no chart history"


# ── 9. BackSwipe leaves a pan that starts on the chart ────────────────────────


@pytest.mark.parametrize("screen_name", [d[1] for d in _DETAIL])
def test_backswipe_leaves_a_pan_that_starts_on_the_chart(screen_name):
    """The chart sits 16 pt from the edge, inside BackSwipe's 44 pt origin zone: a pan right
    on the chart (back in time) also popped the screen."""
    bs = _code(_BACKSWIPE)
    modifier = _block(bs, "struct BackSwipe: ViewModifier")
    assert "@Environment(\\.chartTouchRegion) private var chartTouchRegion" in modifier
    ended = _block(modifier, ".onEnded { value in")
    check = "if let region = chartTouchRegion?.frame, region.contains(value.startLocation) { return }"
    assert check in ended, "BackSwipe no longer consults the chart's region"
    assert ended.index(check) < ended.index("onBack()"), "the chart check must come before the pop"
    # A plain reference box: written on every scroll frame, so it must never publish.
    box = _block(bs, "final class ChartTouchRegion")
    assert "var frame: CGRect" in box and "@Published" not in box
    assert not re.search(r"final class ChartTouchRegion\s*:[^{]*ObservableObject", bs)
    assert "@Entry var chartTouchRegion: ChartTouchRegion? = nil" in bs
    # While the chart can pan OR a scrub just moved: on 1D a quick edge swipe that starts on
    # the chart is the screen's back gesture, but a scrub dragged right from the edge is the
    # chart's own touch and its release must not pop the screen.
    frame_prop = _block(box, "var frame: CGRect")
    assert re.search(r"isPannable\s*\|\|\s*scrubArmed\s*\|\|\s*CACurrentMediaTime\(\)\s*-\s*lastScrubAt\s*<\s*Self\.scrubGrace\s*\?\s*chartFrame\s*:\s*\.null", frame_prop), (
        "BackSwipe must skip the chart's frame while it can pan, while a scrub is armed, and just after one"
    )
    layer = _block(_code(_CROSSHAIR), "struct ChartInteractionLayer: View")
    scrub = _block(layer, "private func scrubGesture(size: CGSize) -> some Gesture")
    arm = "if touchRegion?.scrubArmed == false { touchRegion?.scrubArmed = true }"
    assert arm in scrub, "a scrub no longer marks its touch"
    assert arm not in _block(scrub, "if !crosshairState.isArmed"), (
        "marking the touch must not hang on the isArmed transition (a stale isArmed skipped it)"
    )
    # The grace runs from the scrub's END (a resting finger sends no movement), and the armed
    # flag covers BackSwipe's onEnded running first.
    end = _block(layer, "private func endScrub()")
    assert "touchRegion?.scrubArmed = false" in end and "touchRegion?.lastScrubAt = CACurrentMediaTime()" in end
    assert "endScrub()" in _block(layer, ".onDisappear"), "a torn-down scrub must end fully (isArmed outlives the layer)"

    chart = _block(_code(_CHART), "struct TickerChartView: View")
    assert "@Environment(\\.chartTouchRegion) private var touchRegion" in chart
    assert "touchRegion?.chartFrame = newFrame" in chart, "the chart no longer reports where it is"
    assert "touchRegion?.isPannable = canNavigate" in chart, "the chart no longer says when it pans"

    screen = _code(_VIEWS / "Screens" / f"{screen_name}.swift")
    assert "@State private var chartTouchRegion = ChartTouchRegion()" in screen, screen_name
    inject = ".environment(\\.chartTouchRegion, chartTouchRegion)"
    assert screen.count(inject) == 1, screen_name
    swipe = screen.find(".backSwipe {")
    assert swipe != -1, f"guard is stale — {screen_name} has no .backSwipe"
    swipe_end = swipe + len(_block(screen[swipe:], ".backSwipe {")) + len(".backSwipe ")
    assert screen.index(inject) > swipe_end, (
        f"{screen_name}: the box must be injected OUTSIDE (after) .backSwipe, or the modifier "
        "reads nil while the chart writes a box only it can see"
    )

    # The chart gets plain values, each argument on its own line (no network in the molecule).
    call = _call(screen, "TickerChartView(")
    for arg in ("history: viewModel.chartHistory.state,",
                "onNeedOlderBars: { viewModel.loadOlderChartBars(userInitiated: $0) },",
                "dataRange: viewModel.chartDataRange,"):
        assert re.search(rf"^\s*{re.escape(arg)}\s*$", call, flags=re.M), f"{screen_name}: `{arg}`"
    assert re.search(r"^\s*dataInterval: viewModel\.chartDataInterval\s*,?\s*$", call, flags=re.M), screen_name


def test_the_chart_molecule_never_fetches():
    chart = _code(_CHART)
    for token in ("ChartHistoryLoader", "StockRepository", "APIClient", "URLSession"):
        assert token not in chart, f"TickerChartView references {token} — it must take plain values"
    decl = _block(chart, "struct TickerChartView: View")
    assert "var history: ChartHistoryState = .empty" in decl
    assert "var onNeedOlderBars: ((Bool) -> Void)? = nil" in decl


# ── 10. The loader: strictly older bars, never a bridged gap ──────────────────


def test_the_loader_keeps_only_strictly_older_bars_and_never_bridges_a_gap():
    """Range payloads for one interval start on different dates (3M after 6M; crypto 1W
    rolls). Keeping a bar ON the payload's first date would duplicate it; keeping older bars
    across a GAP would run every MA/RSI/MACD across the hole, silently wrong."""
    loader = _code(_LOADER)
    absorb = _block(
        loader,
        "func absorb(_ payload: [StockPricePoint], symbol: String, range: ChartTimeRange, "
        "interval: ChartInterval, extendedHours: Bool)",
    )
    assert "let first = payload.first?.date" in absorb
    cut_at = absorb.index("let cut = ChartSeries.lowerBound(store.map(\\.date), first)")
    older_at = absorb.index("var older = Array(store[0..<cut])")
    assert cut_at < older_at

    gap = _block(absorb, "else if cut == store.count")
    assert re.search(r"(?<![\w.])older\s*=\s*\[\]", gap), "a gap before the payload must DROP the older bars"
    seam = _block(absorb, "if cut < store.count, store[cut].date == first")
    assert re.search(r"(?<![\w.])older\s*=\s*\[\]", _block(seam, "> Self.seamTolerance")), (
        "a seam mismatch (a split) must drop the older bars"
    )

    merge_at = absorb.index("store = older + payload")
    assert older_at < merge_at and absorb.index(gap) < merge_at
    assert "payloadStart = older.count" in absorb[merge_at:]
    assert re.findall(r"(?<![\w.])store\s*=\s*([^\n]+)", absorb) == ["payload", "older + payload"], (
        "the store is written only as the payload alone (1D) or older + payload"
    )

    # lowerBound = the first index with date >= cutoff, so `store[0..<cut]` is STRICTLY older.
    lb = _block(_code(_SERIES), "static func lowerBound(_ dates: [String], _ cutoff: String) -> Int")
    assert "if dates[mid] < cutoff { lo = mid + 1 } else { hi = mid }" in lb, (
        "`<=` would make this an upper bound and keep the payload's first bar twice"
    )


# ── 11. Review round 2026-10-21: the loader, the floor, the crosshair, the tail ─


def test_a_settled_restart_never_merges_a_page_cut_at_the_dropped_edge():
    """A page is requested from the oldest STORED bar. When its settled_through says the
    backend's history was rebuilt, the older bars are dropped — and the page, cut at the edge
    just dropped, would sit across a hole from the payload (MA/RSI computed over decades
    missing). It must be discarded, not merged."""
    loader = _code(_LOADER)
    apply = _block(loader, "private func apply(_ page: ChartHistoryResponse, before: String)")
    restart = _block(apply, "if let had = settledThrough, let now = page.settledThrough, had != now")
    assert "let droppedOlder = payloadStart > 0" in restart
    assert restart.index("let droppedOlder = payloadStart > 0") < restart.index("store = Array(store[payloadStart...])")
    drop = _block(restart, "if droppedOlder")
    assert re.search(r"\breturn\b", drop), "a page cut at a dropped edge must not reach the merge"
    assert apply.index(restart) < apply.index("store = bars + store")


def test_the_floor_relaxes_only_at_the_symbols_first_bar():
    """`has_more: false` also ends paging at an intraday look-back bound, CoinGecko's plan
    cap, a refused page. Only the backend's `reached_start` says the leftmost bars are the
    symbol's first — at any other stop they are warm-up whose indicators lack a lookback."""
    chart = _block(_code(_CHART), "struct TickerChartView: View")
    bounds = _block(chart, "private func bounds(for series: ChartSeries) -> ChartViewportBounds")
    # From the series BUILT (a long merge builds in the background; the live flag would floor
    # the old series by bars it does not hold yet).
    assert "let reachedStart = seriesBox.builtKey?.reachedStart ?? false" in bounds
    assert "let floor = reachedStart ? 0 : min(ChartWarmup.bars, series.rangeStartIndex)" in bounds
    assert "isExhausted" not in bounds and "historyExhausted" not in bounds
    assert "history.reachedStart" not in bounds
    assert "reachedStart: usesIntradayTimeMapping ? false : history.reachedStart" in chart

    models = _code(_HISTORY_MODELS)
    dto = _block(models, "struct ChartHistoryResponse: Codable")
    assert 'case reachedStart = "reached_start"' in dto and "let reachedStart: Bool?" in dto

    loader = _code(_LOADER)
    sets = re.findall(r"(?<![\w.])reachedStart\s*=\s*([^\n]+)", loader)
    assert sets, "guard is stale — the loader never sets reachedStart"
    assert all(v.strip() in {"false", "page.reachedStart ?? false"} for v in sets), sets
    # Only a page the backend answered may set it true; a failure never does.
    fail = _block(loader, "private func fail(_ error: Error, key: Key, before: String)")
    assert "reachedStart" not in fail
    apply = _block(loader, "private func apply(_ page: ChartHistoryResponse, before: String)")
    end = _block(apply, "if !page.hasMore")
    assert "reachedStart = page.reachedStart ?? false" in end
    # A cap trims the front: the leftmost bar is no longer the first.
    assert "reachedStart = false" in _block(apply, "if store.count > Self.maxStoredBars")
    # ALL never marks the key exhausted on the client: an empty ALL write stuck it forever, and
    # a key shared with 5Y/1Y must hear from the BACKEND where its first bar is.
    absorb = _block(loader, "func absorb(_ payload: [StockPricePoint], symbol: String, range: ChartTimeRange, "
                            "interval: ChartInterval, extendedHours: Bool)")
    assert not re.search(r"range\s*==\s*\.all", absorb), "the loader must not decide ALL is exhausted"
    assert "exhausted = false" in _block(loader, "private func restartOlder()")


def test_a_status_only_publish_never_rebuilds_the_series():
    """The chart's series memo keys on the history revision: a status change (loading,
    failed) that bumped it re-ran the whole merge and every indicator."""
    loader = _code(_LOADER)
    publish = _block(loader, "private func publish(status: ChartHistoryState.Status? = nil, barsChanged: Bool = false)")
    assert "revision: barsChanged ? state.revision &+ 1 : state.revision" in publish
    assert "older = state.olderBars" in publish
    request = _block(loader, "func requestOlder(force: Bool = false)")
    assert "publish(status: .loading)" in request and "barsChanged" not in request
    fail = _block(loader, "private func fail(_ error: Error, key: Key, before: String)")
    assert "barsChanged" not in fail
    absorb = _block(loader, "func absorb(_ payload: [StockPricePoint], symbol: String, range: ChartTimeRange, "
                            "interval: ChartInterval, extendedHours: Bool)")
    assert "publish()" not in absorb, "every payload write must rebuild (publish(barsChanged: true))"


def test_an_empty_payload_keeps_the_store():
    """An empty write (a failed range switch) used to WIPE the paged bars while `exhausted` /
    `settledThrough` kept describing them — that key could never page in again."""
    loader = _code(_LOADER)
    absorb = _block(loader, "func absorb(_ payload: [StockPricePoint], symbol: String, range: ChartTimeRange, "
                            "interval: ChartInterval, extendedHours: Bool)")
    empty = _block(absorb, "guard let first = payload.first?.date else")
    assert "payloadPresent = false" in empty and "store" not in empty
    request = _block(loader, "func requestOlder(force: Bool = false)")
    assert "payloadPresent" in request.split("else { return }")[0]


def test_the_crosshair_follows_its_bar_across_a_prepend():
    """The selection is a GLOBAL index; a page of older bars landing mid-scrub shifts every
    index by its length, and the readouts showed another bar's price until the finger moved.
    Every reader resolves the selection by the bar's date."""
    crosshair = _code(_CROSSHAIR)
    state = _block(crosshair, "final class CrosshairState: ObservableObject")
    resolve = _block(state, "func index(in series: ChartSeries) -> Int?")
    assert "series.dates[i] == date" in resolve and "series.lowerBound(date)" in resolve
    layer = _block(crosshair, "struct ChartInteractionLayer: View")
    assert "crosshairState.index(in: series)" in _block(layer, "var body: some View")
    update = _block(layer, "private func updateCrosshair(at location: CGPoint, in size: CGSize)")
    assert "crosshairState.selectedDate = series.dates[index]" in update

    readers = {
        "ChartScrubHeader": _block(_code(_CHART), "private struct ChartScrubHeader: View"),
        "SubChartReadout": _block(_code(_SUB_CANVAS), "private struct SubChartReadout: View"),
        "SubChartCrosshairLine": _block(_code(_SUB_CANVAS), "private struct SubChartCrosshairLine: View"),
    }
    for name, body in readers.items():
        assert "crosshairState.index(in: series)" in body, f"{name} reads a stale index"
        assert "crosshairState.selectedIndex" not in body, f"{name} reads the raw index"
    # The newest bar is re-dated in place (crypto's live point): follow it, don't end the scrub.
    assert "if selectedFromEnd == 0, let last = series.dates.last, last > date { return series.count - 1 }" in resolve
    assert "crosshairState.selectedFromEnd = series.count - 1 - index" in update
    # A refresh that slides the selected bar past the edge moves the selection to the edge bar,
    # and the dot is drawn only on a bar the price scale covers.
    rebase = _block(layer, ".onChange(of: series.revision)")
    assert "visibleRange" not in rebase, "this pass's frame is pre-append: clamping here shifts the selection"
    assert "frame.visibleRange.contains(i)" in _block(layer, "var body: some View")
    chart_src = _code(_CHART)
    on_rev = _block(_block(chart_src, "struct TickerChartView: View"), ".onChange(of: series.revision)")
    assert on_rev.index("absorbTailAppend()") < on_rev.index("keepScrubOnVisibleBars()"), (
        "the clamp must run AFTER the tail append moved the window"
    )
    keep = _block(chart_src, "private func keepScrubOnVisibleBars()")
    assert "let visible = frame.visibleRange" in keep and "crosshairState.selectedDate = series.dates[j]" in keep
    assert "guard !visible.isEmpty, !visible.contains(i) else { return }" in keep, "a visible selection must be left alone"
    # Snapping lands only on a bar the price scale covers.
    frame = _block(_code(_SERIES), "nonisolated struct ChartFrame: Equatable, Sendable")
    snap = _block(frame, "func index(atX x: CGFloat, width: CGFloat) -> Int?")
    assert "let r = visibleRange" in snap and "return min(max(i, r.lowerBound), r.upperBound - 1)" in snap


def test_a_tail_refresh_is_measured_on_a_bar_left_of_the_right_edge():
    """Crypto's live point and a weekly/monthly bucket are RE-DATED in place on a refresh:
    counting bars after the old newest date called that one appended bar and walked a
    panned-back window one bar older on every refresh."""
    chart = _code(_CHART)
    absorb = _block(chart, "private func absorbTailAppend()")
    assert "Self.barsAddedAtTail(from: previous, to: series, endOffset: viewportState.window.endOffset)" in absorb
    assert "upperBound(previousLast)" not in absorb
    tail = _block(chart, "static func barsAddedAtTail(from old: ChartSeries, to new: ChartSeries, endOffset: Double) -> Int")
    assert "let anchor = old.count - 1 - max(1, Int(endOffset.rounded(.up)))" in tail, (
        "the anchor must sit at least one bar left of the newest bar"
    )
    assert "return (new.count - 1 - j) - (old.count - 1 - anchor)" in tail
    vp = _block(_code(_VIEWPORT), "func absorbAppend(_ appended: Int)")
    assert "guard appended != 0, window.endOffset > 0.5 else { return }" in vp


def test_pane_and_price_scales_are_measured_on_the_bars_on_screen():
    """`drawRange` holds the partly visible bar past each edge (so a line reaches it); a
    value nobody can see must not stretch the price scale, a pane's scale or the baseline."""
    chart = _block(_code(_CHART), "struct TickerChartView: View")
    y = _call(chart, "ChartYRange.compute(")
    assert "range: frame.visibleRange" in y
    assert "frame.visibleRange.first" in _block(chart, "private func baselineClose(series: ChartSeries, frame: ChartFrame) -> Double?")
    sub = _code(_SUB_CANVAS)
    assert "for i in frame.visibleRange { if let v = series.volume[i], v > maxVol { maxVol = v } }" in sub
    assert "for i in frame.visibleRange {" in _block(sub, "private func drawMACD(")
    macd = _block(sub, "private func drawMACD(")
    assert "1e-9" not in macd, "an absolute MACD floor flattens sub-cent coins"
    assert "let noiseFloor = priceLevel * 1e-12" in macd, "the MACD floor must be relative to the price"
    frame = _block(_code(_SERIES), "nonisolated struct ChartFrame: Equatable, Sendable")
    assert "let perPoint = slotCount / Double(width)" in _block(frame, "func bucketSize(_ width: CGFloat) -> Int")


def test_1d_bars_are_one_interval_wide_and_a_flat_window_is_centred():
    """1D candles/volume are sized by the bar interval (lower-median gap, capped at the
    interval in hand) — a halt or a 3-bar morning never widens them; a flat window gets a band
    instead of a 2e-16 span that pinned the line and threw the edge bar off the canvas."""
    coord = _code(_CHART_DIR / "ChartCoordinateSystem.swift")
    slot = _block(coord, "static func slotFraction(minutes: ArraySlice<Int>, window: SessionWindow, intervalMinutes: Int? = nil) -> CGFloat")
    assert "gap = gaps[(gaps.count - 1) / 2]" in slot, "the UPPER median is the halt when there are two gaps"
    assert "if let intervalMinutes, intervalMinutes > 0 { gap = min(gap, intervalMinutes) }" in slot
    chart = _block(_code(_CHART), "struct TickerChartView: View")
    assert "intervalMinutes: displayedInterval.minutes" in chart
    y = _block(coord, "static func compute(")
    assert "if hi - lo <= max(abs(hi), abs(lo)) * 1e-9 {" in y
    chip = _block(_code(_CHART), "private struct ChartScrubHeader: View")
    assert "let fingerOnLeft = crosshairState.fingerOnLeft ?? (frame.x(i, width: 1) < 0.5)" in chip
    update = _block(_block(_code(_CROSSHAIR), "struct ChartInteractionLayer: View"),
                    "private func updateCrosshair(at location: CGPoint, in size: CGSize)")
    assert "let onLeft = x < size.width / 2" in update


# ── anti-vacuity for the helpers ──────────────────────────────────────────────


def test_the_helpers_are_not_vacuous():
    fake = (
        "struct Decoy { let x: Int }\n"
        "struct Real: View, Equatable {\n"
        "    // let commented: Int\n"
        "    @Environment(\\.colorScheme) private var scheme\n"
        "    let a: Int\n"
        "    var b: Bool = false\n"
        "    private var computed: Int { 1 }\n"
        "    static func == (lhs: Real, rhs: Real) -> Bool { lhs.a == rhs.a }\n"
        "}\n"
    )
    body = _block(_strip_swift_comments(fake), "struct Real: View, Equatable")
    assert _stored_properties(body) == ["a", "b"], _stored_properties(body)
    assert "x" not in _stored_properties(body), "the scan leaked into a neighbouring type"
    with pytest.raises(AssertionError):
        _assert_eq_names_every_stored_property(body, _block(body, "static func =="), "Real")
    assert _call("f(a: g(1), b: 2) .equatable()", "f(") == "(a: g(1), b: 2)"
    assert re.match(r"\s*\.equatable\(\)", _after_call("f(a: g(1)) .equatable()", "f("))
