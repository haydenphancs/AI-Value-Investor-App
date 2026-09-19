"""The Extended Hours toggle must change the 1D chart's session axis — and only there.

TestFlight (build 1.0 (8)): with the toggle ON the NVDA 1D chart still spanned the
09:30–16:00 bell and the pre-market prints piled up on the left edge. Two halves, both
pinned here with the backend half in `test_chart_helper_outliers.py`:

* iOS `TickerChartView.sessionWindow` never consulted `showExtendedHours`; it always
  took `TradingDayHelper.window(for:)`, whose only windows were `.regular` and
  `.roundTheClock`. Now a `.extended` (04:00–20:00) window exists and is selected by a
  separate overload, gated on the flag AND on the series actually carrying such bars.
* The toggle was offered on ETF/index/commodity screens whose backends never fetch
  extended bars (inert), and the Stoch pane was offered on crypto whose rows carry no
  high/low (a close-only oscillator under a "Stoch(14,3,3)" label).

Source-scan guards go vacuous easily (`.claude/rules/testing.md` §3): comment-stripped,
brace-bounded, mutation-tested by hand (`4 * 60` → `0`, `hasExtendedBars` → `true`).
"""
from __future__ import annotations

import pathlib
import re

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_COORDS = _IOS / "Views" / "Molecules" / "Chart" / "ChartCoordinateSystem.swift"
_CHART = _IOS / "Views" / "Molecules" / "TickerChartView.swift"
_MODELS = _IOS / "Models" / "ChartModels.swift"
_SHEET = _IOS / "Views" / "Molecules" / "Chart" / "ChartSettingsSheet.swift"
_CRYPTO = _IOS / "Views" / "Screens" / "CryptoDetailView.swift"


def _strip_swift_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"//.*$", "", raw))
    return "\n".join(out)


def _block(src: str, header: str) -> str:
    i = src.find(header)
    assert i != -1, f"guard is stale — {header!r} not found"
    start = src.find("{", i)
    depth, j = 0, start
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start: j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces after {header!r}")


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_swift_comments(path.read_text(encoding="utf-8"))


# ── SessionWindow.extended ───────────────────────────────────────────────────

def test_the_extended_window_is_0400_to_2000():
    body = _block(_code(_COORDS), "struct SessionWindow")
    assert re.search(
        r"static let extended = SessionWindow\(openMinute:\s*4 \* 60,\s*closeMinute:\s*20 \* 60\)", body
    ), body
    # anti-vacuity: the same struct still declares the two windows that existed before
    assert "static let regular" in body and "static let roundTheClock" in body


def test_the_extended_overload_only_widens_the_regular_bell():
    src = _code(_COORDS)
    body = _block(src, "static func window(for context: ChartAssetContext, extendedHours: Bool)")
    assert "window(for: context)" in body, "must derive from the per-asset switch, not re-decide it"
    assert "== .regular" in body and ".extended" in body, body
    assert ".roundTheClock" not in body, "a 24/7 asset must never be widened or narrowed here"


def test_the_original_window_switch_never_returns_extended():
    """`test_ios_chart_session_window.py` regex-parses this switch; keep it untouched."""
    body = _block(_code(_COORDS), "static func window(for context: ChartAssetContext)")
    assert "extended" not in body, body


# ── TickerChartView selects it from the flag AND the data ────────────────────

def test_ticker_chart_session_window_consults_the_flag_and_the_data():
    src = _code(_CHART)
    body = _block(src, "private var sessionWindow")
    # The CONJUNCTION, not token presence — `&& !hasExtendedBars` or `|| hasExtendedBars`
    # would keep every token and break the chart.
    assert re.search(
        r"let wantsExtended = assetContext\.supportsExtendedHours\s*&&\s*usesIntradayTimeMapping\s*&&\s*hasExtendedBars",
        body,
    ), body
    assert "extendedHours: wantsExtended" in body
    # The TOGGLE must not be in the window decision. The view model refetches on the
    # toggle; reading it here as well re-windowed the still-extended series to the bell
    # for the duration of the refetch, piling the pre-market bars on the left edge.
    assert "showExtendedHours" not in body, body
    bars = _block(src, "private var hasExtendedBars")
    assert "pricePoints.contains" in bars and "isExtendedHours" in bars, bars
    assert "visiblePoints" not in bars, "the viewport slice would flip the window mid-gesture"


def test_shading_and_badge_follow_the_data_like_the_window():
    src = _code(_CHART)
    draws = _block(src, "private var drawsExtendedHours")
    assert re.search(r"assetContext\.supportsExtendedHours\s*&&\s*hasExtendedBars", draws), draws
    assert "showExtendedHours" not in draws
    body = _block(src, "var body: some View")
    assert "showExtendedHours: drawsExtendedHours," in body, "MainChartCanvas shading must follow the data"
    assert "drawsExtendedHours && point.isExtendedHours" in body, "the EXT badge must follow the data"
    assert "chartSettings.showExtendedHours && assetContext.supportsExtendedHours" not in body


def test_axis_labels_canvas_and_subcharts_share_one_window():
    src = _code(_CHART)
    uses = [m.start() for m in re.finditer(r"sessionWindow:\s*sessionWindow", src)]
    assert len(uses) >= 2, "MainChartCanvas and ChartXAxisLabels must both receive sessionWindow"
    assert src.index("MainChartCanvas(") < uses[0] < src.index("ChartXAxisLabels(") < uses[1]
    # the sub-panes inherit the same mapping via the fractions computed from that window
    assert "timeFractions: intradayTimeFractions" in src
    fractions = _block(src, "private var intradayTimeFractions")
    assert "window: sessionWindow" in fractions


# ── Where the toggle and panes are offered ───────────────────────────────────

def test_only_the_stock_screen_offers_extended_hours():
    body = _block(_code(_MODELS), "var supportsExtendedHours: Bool")
    assert re.sub(r"\s+", "", body) == "{self==.stock}", body
    ctx = _block(_code(_MODELS), "enum ChartAssetContext")
    assert "case commodity" in ctx and "case etf" in ctx  # anti-vacuity


def test_extended_hours_is_opt_in_and_persisted():
    body = _block(_code(_MODELS), "class ChartSettings")
    assert re.search(r"var showExtendedHours:\s*Bool\s*=\s*false", body), body
    assert "showExtendedHoursKey" in body
    assert re.search(r"UserDefaults\.standard\.set\(showExtendedHours,", body), body
    assert re.search(r"UserDefaults\.standard\.bool\(forKey:\s*Self\.showExtendedHoursKey\)", body), body


def test_crypto_never_offers_the_stochastic_pane():
    models = _code(_MODELS)
    body = _block(models, "var allowedSubCharts")
    arm = body[body.index("case .crypto:"): body.index("case .stock")]
    assert "!= .stochastic" in arm, arm
    sheet = _code(_SHEET)
    assert "assetContext.allowedSubCharts" in sheet, "the settings sheet must iterate the allowed list"
    assert "TechnicalIndicatorType.allCases.filter({ !$0.isOverlay })" not in sheet
    chart = _code(_CHART)
    assert "activeSubCharts.filter { assetContext.allowedSubCharts.contains($0) }" in chart


def test_earnings_dates_toggle_is_stock_only():
    sheet = _code(_SHEET)
    gate = _block(sheet, "if assetContext == .stock")
    assert "showEarningsDates" in gate, "the Earnings Dates toggle is outside the stock-only gate"
    assert sheet.count("showEarningsDates") == gate.count("showEarningsDates"), "a second, ungated toggle exists"
    assert "assetContext == .etf" not in sheet


def test_the_request_follows_the_toggle():
    """The window half is pinned above; this is the REQUEST half. Hard-coding
    `useExtendedHours = true` would refetch 04:00–20:00 bars with the toggle off and
    recreate the edge pile-up under a regular window."""
    vm = _code(_IOS / "ViewModels" / "TickerDetailViewModel.swift")
    sites = re.findall(r"let (useExtendedHours|ext|currentExtended) = self\.chartSettings\.showExtendedHours\s*&&\s*self\.chartSettings\.selectedInterval\.isIntraday", vm)
    sites += re.findall(r"let (useExtendedHours) = chartSettings\.showExtendedHours\s*&&\s*chartSettings\.selectedInterval\.isIntraday", vm)
    assert len(sites) >= 3, f"the toggle no longer drives the request at every fetch site: {sites}"
    assert "extendedHours: useExtendedHours" in vm and "extendedHours: ext" in vm
    assert not re.search(r"extendedHours:\s*(true|false)\b", vm), "a fetch site hard-codes the flag"


def test_crypto_2y_chart_carries_the_history_caption():
    src = _code(_CRYPTO)
    i = src.index("History limited to 2 years")
    assert "selectedChartRange == .twoYears" in src[max(0, i - 300): i]
    assert "AppColors.textMuted" in src[i: i + 300]


# ── F19-7: an early-close day narrows the session axis to the 13:00 bell ──────


def test_a_half_day_narrows_the_equity_session_to_the_early_bell():
    """On the day after Thanksgiving the 1D line stopped at 54% under a 4:00 PM axis while
    the card's sparkline (server-computed span) filled 100%. The chart now narrows its
    own window from the bars' date; `window(for:)` stays class-only and untouched."""
    coords = _code(_COORDS)
    win = _block(coords, "struct SessionWindow")
    assert re.search(r"static let halfDay = SessionWindow\(openMinute: 9 \* 60 \+ 30, closeMinute: 13 \* 60\)", win), win
    chart = _code(_CHART)
    body = _block(chart, "private var sessionWindow")
    assert "MarketHoursUtil.isEarlyClose(" in body
    assert "pricePoints.last?.date" in body, "the day is read from the bars themselves"
    assert "window != .roundTheClock" in body, "crypto has no early close"
    assert ".halfDay" in body
    # Extended hours on a half-day: pre-market as usual, after-hours 13:00–17:00 ET (the
    # exchanges do trade it and the backend passes those bars through), so the extended
    # window ends at 17:00 — narrowing it to 13:00 piled two hours of real prints on the
    # right edge (W2 E-3).
    assert "TradingDayHelper.SessionWindow.extendedHalfDay" in body
    assert re.search(r"static let extendedHalfDay = SessionWindow\(openMinute: 4 \* 60, closeMinute: 17 \* 60\)", win), win
    assert "halfDay.closeMinute" not in body
    # The class switch is unchanged (a conditional return there would break the
    # session-window regex test and re-window every class).
    switch = _block(coords, "static func window(for context: ChartAssetContext) -> SessionWindow")
    assert "isEarlyClose" not in switch and "halfDay" not in switch
    util = _code(_IOS / "Core" / "Utilities" / "MarketHoursUtil.swift")
    fn = _block(util, "static func isEarlyClose(_ ymd: String) -> Bool")
    assert "earlyCloses.contains(ymd)" in fn
