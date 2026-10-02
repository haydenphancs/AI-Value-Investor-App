//
//  ChartModels.swift
//  ios
//
//  Models for chart types, technical indicators, and chart settings
//

import Combine
import Foundation
import SwiftUI

// MARK: - Chart Type

enum ChartType: String, CaseIterable, Identifiable {
    case line = "Line"
    case candle = "Candle"
    case area = "Area"
    case bar = "Bar"

    var id: String { rawValue }

    var iconName: String {
        switch self {
        case .line:   return "chart.xyaxis.line"
        case .candle: return "chart.bar.xaxis"
        case .area:   return "chart.line.uptrend.xyaxis"
        case .bar:    return "chart.bar.xaxis.ascending"
        }
    }
}

// MARK: - Technical Indicator Type

enum TechnicalIndicatorType: String, CaseIterable, Identifiable, Hashable {
    case ma20 = "MA(20)"
    case ma50 = "MA(50)"
    case ma200 = "MA(200)"
    case bollingerBands = "Bollinger Bands"
    case volume = "Volume"
    case rsi14 = "RSI(14)"
    case macd = "MACD(9-12-26)"
    case stochastic = "Stoch(14,3,3)"

    var id: String { rawValue }

    /// What `ChartSettings` stores — NOT `rawValue`, which is the sheet LABEL
    /// ("MACD(9-12-26)"). Storing the label meant relabelling or retuning a pane would
    /// silently switch it off for everyone who had it on. A storage contract: never change
    /// an id; a new case gets its own (the switch has no `default:`, so it cannot compile
    /// without one).
    var storageID: String {
        switch self {
        case .ma20:           return "ma20"
        case .ma50:           return "ma50"
        case .ma200:          return "ma200"
        case .bollingerBands: return "bollinger_bands"
        case .volume:         return "volume"
        case .rsi14:          return "rsi14"
        case .macd:           return "macd"
        case .stochastic:     return "stochastic"
        }
    }

    /// `nil` for an id this build does not know (a pane removed in a later version) — the
    /// caller drops it rather than guessing.
    init?(storageID: String) {
        guard let match = Self.allCases.first(where: { $0.storageID == storageID }) else { return nil }
        self = match
    }

    var isOverlay: Bool {
        switch self {
        case .ma20, .ma50, .ma200, .bollingerBands: return true
        case .volume, .rsi14, .macd, .stochastic:   return false
        }
    }

    /// Series colour AND the settings-sheet legend swatch — one source, so the
    /// key always matches the chart.
    ///
    /// These were SwiftUI SYSTEM colours, which are Apple's tints, not this
    /// app's palette: `.orange` measured 2.02:1 on the page in light mode (the
    /// MA(50) line was a pale thread), and the Bollinger/RSI swatches disagreed
    /// with what their renderers actually drew.
    var defaultColor: Color {
        switch self {
        case .ma20:           return AppColors.primaryGraphic
        case .ma50:           return AppColors.alertOrange
        case .ma200:          return AppColors.alertPurple
        case .bollingerBands: return AppColors.accentGraphic   // matches OverlayRenderer
        case .volume:         return AppColors.growthSectorGray
        case .rsi14:          return AppColors.cautionGraphic  // matches SubChartCanvas
        case .macd:           return AppColors.gainGraphic
        case .stochastic:     return AppColors.accentCyan
        }
    }
}

// MARK: - Chart Interval

enum ChartInterval: String, CaseIterable, Identifiable {
    case oneMin = "1min"
    case fiveMin = "5min"
    case fifteenMin = "15min"
    case thirtyMin = "30min"
    case oneHour = "1hour"
    case daily = "daily"
    case weekly = "weekly"
    case monthly = "monthly"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .oneMin:     return "1 min"
        case .fiveMin:    return "5 min"
        case .fifteenMin: return "15 min"
        case .thirtyMin:  return "30 min"
        case .oneHour:    return "1 hour"
        case .daily:      return "Daily"
        case .weekly:     return "Weekly"
        case .monthly:    return "Monthly"
        }
    }

    /// Whether this interval produces intraday datetime strings
    var isIntraday: Bool {
        switch self {
        case .oneMin, .fiveMin, .fifteenMin, .thirtyMin, .oneHour:
            return true
        default:
            return false
        }
    }
}

// MARK: - Chart Asset Context

enum ChartAssetContext {
    case stock
    case etf
    case crypto
    case index
    case commodity

    /// Only the stock screen fetches pre/after-hours bars (`extended_hours=` on
    /// /overview, /overview/core and /chart). The ETF, index and commodity backends
    /// fetch the regular session only and crypto trades round the clock, so the
    /// Extended Hours toggle would be inert there — it used to be shown anyway.
    var supportsExtendedHours: Bool {
        self == .stock
    }

    /// Sub-panes this asset class can draw honestly.
    ///
    /// Stoch(14,3,3) needs high/low; CoinGecko rows carry close + volume only, so on
    /// crypto it degraded to a close-only oscillator under the Stoch label. Same
    /// reasoning as `allowedChartTypes`, which already hides candles there.
    var allowedSubCharts: [TechnicalIndicatorType] {
        switch self {
        case .crypto:
            return TechnicalIndicatorType.allCases.filter { !$0.isOverlay && $0 != .stochastic }
        case .stock, .etf, .index, .commodity:
            return TechnicalIndicatorType.allCases.filter { !$0.isOverlay }
        }
    }

    /// The time-range pills this asset's screen may offer.
    ///
    /// `ChartTimeRange` is a single enum shared by all five detail screens, so it cannot
    /// express "2Y exists, but only for crypto". This does — the same way
    /// `supportsExtendedHours` already gates a per-asset chart behaviour.
    ///
    /// Two independent reasons a pill must not appear:
    ///
    /// * **The backend would 400 it.** Only `crypto.py` accepts "2Y"; the equity, ETF,
    ///   index and commodity range patterns are `^(1D|1W|3M|6M|1Y|5Y|ALL)$`.
    /// * **The data does not exist.** Crypto history comes from CoinGecko Basic, which
    ///   caps at two years. A 5Y or ALL pill there renders a two-year series under a
    ///   five-year label — a pill that draws the wrong window is the same defect class
    ///   as one that draws an empty chart.
    var allowedRanges: [ChartTimeRange] {
        switch self {
        case .crypto:
            // 2Y replaces 5Y/ALL, which the source cannot serve.
            return [.oneDay, .oneWeek, .threeMonths, .sixMonths, .oneYear, .twoYears]
        case .stock, .etf, .index, .commodity:
            // Everything except 2Y. Written as an explicit filter rather than a literal
            // list so a future range added to `ChartTimeRange` reaches these screens
            // automatically, exactly as `allCases` did before this property existed.
            return ChartTimeRange.allCases.filter { $0 != .twoYears }
        }
    }

    /// The intervals the SOURCE can actually serve for a range on this asset class.
    ///
    /// Crypto is priced from CoinGecko, whose intraday feed has one granularity per
    /// window (5-minute at 1D, hourly at 1W) — the picker used to offer 1min/15min/30min
    /// and the chart drew the same bars under every label. Daily ranges keep the full
    /// list: the backend resamples the daily series to weekly/monthly.
    func allowedIntervals(for range: ChartTimeRange) -> [ChartInterval] {
        switch self {
        case .crypto:
            switch range {
            case .oneDay:  return [.fiveMin]
            case .oneWeek: return [.oneHour]
            default:       return range.allowedIntervals
            }
        case .stock, .etf, .index, .commodity:
            return range.allowedIntervals
        }
    }

    /// The range a screen of this class opens on, given the user's remembered range.
    ///
    /// The remembered range is ONE choice shared by every asset class (product decision,
    /// 2026-10-01), but the classes do not offer the same pills: 2Y exists only on crypto,
    /// and crypto has no 5Y/ALL. Opening on a range the backend 400s (2Y on a stock) or the
    /// source cannot serve is never acceptable, so an unoffered range falls back to the
    /// LONGEST offered range that is not longer than it — 2Y → 1Y elsewhere, 5Y/ALL → 2Y on
    /// crypto. `ChartTimeRange` is declared shortest → longest, so declaration order IS
    /// window length (pinned by a test). Nothing remembered → the screen's own default.
    ///
    /// Pure: the fallback is for DISPLAY only and is never written back over the memory.
    func resolvedRange(preferred: ChartTimeRange?, screenDefault: ChartTimeRange) -> ChartTimeRange {
        guard let preferred else { return screenDefault }
        if allowedRanges.contains(preferred) { return preferred }
        let rank = { (range: ChartTimeRange) in ChartTimeRange.allCases.firstIndex(of: range) ?? 0 }
        return allowedRanges.filter { rank($0) <= rank(preferred) }.max { rank($0) < rank($1) } ?? screenDefault
    }

    /// The interval a range opens on, given the interval the user last picked for it.
    /// One this class cannot serve for that range (a stock's 1D = 1 min on crypto, whose
    /// 1D feed is 5-minute only) falls back to the range's default. Pure, like
    /// `resolvedRange` — a coerced interval is never stored.
    func resolvedInterval(preferred: ChartInterval?, for range: ChartTimeRange) -> ChartInterval {
        let allowed = allowedIntervals(for: range)
        if let preferred, allowed.contains(preferred) { return preferred }
        return allowed.contains(range.defaultInterval) ? range.defaultInterval : (allowed.first ?? range.defaultInterval)
    }

    /// The chart TYPES this asset class can draw honestly.
    ///
    /// CoinGecko rows carry close + volume only — no open/high/low — and the candle /
    /// bar renderers fill a missing range from the close, so every crypto candle was a
    /// zero-height, zero-wick, always-green doji. Line and area need only the close.
    var allowedChartTypes: [ChartType] {
        switch self {
        case .crypto:
            return [.line, .area]
        case .stock, .etf, .index, .commodity:
            return ChartType.allCases
        }
    }
}

// MARK: - Chart Settings

/// The chart preferences the Chart Settings sheet edits — every one of them persisted on
/// this device, and kept in step across every live chart.
///
/// TestFlight 1.0 (9): "I did change Extended hours to off but it turns on again. For
/// everything in here, it should … set up once and permanently keep them." Only `chartType`
/// and (from build 10) `showExtendedHours` were stored; overlays, sub-charts and earnings
/// markers reset on every screen. And because each of the five detail ViewModels owns its
/// OWN instance that read UserDefaults only at init, a change on a detail screen pushed
/// over another (header search, a related ticker, a news chip) left the screen underneath
/// showing its stale copy — the toggle "turned back on" the moment the user went back.
///
/// Now every sheet setting writes through and announces itself; every other live instance
/// re-reads the store (the single source of truth) and assigns only what differs, so a
/// sink downstream never sees a phantom change. `isApplyingStoredSettings` makes a re-read
/// unable to write or announce, so the echo cannot loop.
///
/// Device-only by product decision (2026-10-01), and deliberately NOT cleared by
/// `AppState.discardDataForEndedSession()`: these are display preferences of this phone,
/// not account data — the same standing `caydex_preferred_chart_type` always had.
final class ChartSettings: ObservableObject {
    private static let chartTypeKey = "caydex_preferred_chart_type"
    private static let showExtendedHoursKey = "caydex_show_extended_hours"
    private static let showEarningsDatesKey = "caydex_show_earnings_dates"
    /// Sorted `[String]` of `TechnicalIndicatorType.storageID` — never the display label.
    private static let enabledIndicatorsKey = "caydex_chart_indicators"
    private static let didChangeNotification = Notification.Name("caydexChartSettingsDidChange")

    @Published var chartType: ChartType = .line {
        didSet {
            guard !isApplyingStoredSettings, chartType != oldValue else { return }
            UserDefaults.standard.set(chartType.rawValue, forKey: Self.chartTypeKey)
            announceChange()
        }
    }
    /// Per-SCREEN state, deliberately unobserved and unpersisted here. The range sinks
    /// assign it a COERCED value whenever a range changes (crypto's 1D is 5-minute only),
    /// so persisting on assignment would store those over the user's real choice. The
    /// remembered interval lives in `ChartSelectionMemory`, written only from a tap.
    @Published var selectedInterval: ChartInterval = .fiveMin
    @Published var enabledIndicators: Set<TechnicalIndicatorType> = [] {
        didSet {
            guard !isApplyingStoredSettings, enabledIndicators != oldValue else { return }
            UserDefaults.standard.set(enabledIndicators.map(\.storageID).sorted(), forKey: Self.enabledIndicatorsKey)
            announceChange()
        }
    }
    /// Extended Hours is OPT-IN (product decision, 2026-09-17). It defaulted to `true`
    /// while it was inert; now that it works, a default of `true` would make every 1D
    /// stock chart span 16 hours with the regular session squeezed into ~40% of the
    /// width. Persisted like `chartType` so a user's choice survives relaunch.
    @Published var showExtendedHours: Bool = false {
        didSet {
            guard !isApplyingStoredSettings, showExtendedHours != oldValue else { return }
            UserDefaults.standard.set(showExtendedHours, forKey: Self.showExtendedHoursKey)
            announceChange()
        }
    }
    @Published var showEarningsDates: Bool = false {
        didSet {
            guard !isApplyingStoredSettings, showEarningsDates != oldValue else { return }
            UserDefaults.standard.set(showEarningsDates, forKey: Self.showEarningsDatesKey)
            announceChange()
        }
    }

    /// True while `applyStoredSettings()` assigns — the property observers above then
    /// neither write (the value came FROM the store) nor announce (which would echo).
    private var isApplyingStoredSettings = false
    private var syncSubscription: AnyCancellable?

    init() {
        applyStoredSettings()
        // Another instance changed a setting: re-read the store. The poster skips its own
        // announcement (its in-memory value already IS the stored one).
        syncSubscription = NotificationCenter.default.publisher(for: Self.didChangeNotification)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] notification in
                guard let self, (notification.object as AnyObject?) !== self else { return }
                self.applyStoredSettings()
            }
    }

    private func announceChange() {
        NotificationCenter.default.post(name: Self.didChangeNotification, object: self)
    }

    /// Load every persisted setting, assigning ONLY a value that differs — `@Published`
    /// emits on every assignment, and the stock screen refetches on `$showExtendedHours`.
    private func applyStoredSettings() {
        isApplyingStoredSettings = true
        defer { isApplyingStoredSettings = false }

        let storedType = UserDefaults.standard.string(forKey: Self.chartTypeKey)
            .flatMap(ChartType.init(rawValue:)) ?? .line
        if chartType != storedType { chartType = storedType }

        // `bool(forKey:)` is false for an absent key, which is exactly the default.
        let storedExtended = UserDefaults.standard.bool(forKey: Self.showExtendedHoursKey)
        if showExtendedHours != storedExtended { showExtendedHours = storedExtended }

        let storedEarnings = UserDefaults.standard.bool(forKey: Self.showEarningsDatesKey)
        if showEarningsDates != storedEarnings { showEarningsDates = storedEarnings }

        // An id this build does not know is dropped, never guessed; it is rewritten away
        // the next time the user toggles a pane (never on a read).
        let storedIDs = UserDefaults.standard.stringArray(forKey: Self.enabledIndicatorsKey) ?? []
        let storedIndicators = Set(storedIDs.compactMap(TechnicalIndicatorType.init(storageID:)))
        if enabledIndicators != storedIndicators { enabledIndicators = storedIndicators }
    }

    var activeOverlays: [TechnicalIndicatorType] {
        enabledIndicators.filter { $0.isOverlay }.sorted { $0.rawValue < $1.rawValue }
    }

    var activeSubCharts: [TechnicalIndicatorType] {
        let displayOrder: [TechnicalIndicatorType] = [.volume, .rsi14, .macd, .stochastic]
        return displayOrder.filter { enabledIndicators.contains($0) }
    }
}

// MARK: - Chart Selection Memory (range + interval)

/// The range the user last picked, and the interval they last picked for each range —
/// ONE choice across all five detail screens (product decision, 2026-10-01).
///
/// Restored when a screen OPENS, never synced into a screen already on the stack: a live
/// range change would fire that hidden screen's range sink, which refetches and re-arms the
/// 30-second poller its `onDisappear` had stopped. Going back returns the screen the user
/// left.
///
/// The two `rememberUser…` writers are called ONLY from `TickerChartView`'s taps. Restore,
/// the per-asset fallbacks and every ViewModel sink only READ — so a coerced value (crypto
/// shows 5-minute bars on 1D) can never overwrite a real preference (a stock's 1D = 1 min).
/// Device-only, like `ChartSettings`.
enum ChartSelectionMemory {
    private static let rangeKey = "caydex_chart_range"
    /// `[ChartTimeRange.rawValue: ChartInterval.rawValue]`, e.g. `["1D": "1min"]`.
    private static let intervalByRangeKey = "caydex_chart_interval_by_range"

    private static var storedRange: ChartTimeRange? {
        UserDefaults.standard.string(forKey: rangeKey).flatMap(ChartTimeRange.init(rawValue:))
    }

    private static var storedIntervals: [String: String] {
        UserDefaults.standard.dictionary(forKey: intervalByRangeKey) as? [String: String] ?? [:]
    }

    /// The range + interval a screen of `context` opens on. Read-only.
    static func restoredSelection(
        in context: ChartAssetContext,
        screenDefault: ChartTimeRange
    ) -> (range: ChartTimeRange, interval: ChartInterval) {
        let range = context.resolvedRange(preferred: storedRange, screenDefault: screenDefault)
        return (range, rememberedInterval(for: range, in: context))
    }

    /// The interval `range` opens on for `context` — what each range sink assigns. Read-only.
    static func rememberedInterval(for range: ChartTimeRange, in context: ChartAssetContext) -> ChartInterval {
        let preferred = storedIntervals[range.rawValue].flatMap(ChartInterval.init(rawValue:))
        return context.resolvedInterval(preferred: preferred, for: range)
    }

    /// A range pill was tapped — always a choice, so always stored, re-taps included.
    ///
    /// There used to be one exception: a re-tap of a pill shown only because the remembered
    /// range is not offered here (5Y remembered → crypto shows 2Y) was skipped. But the
    /// remembered range and the shown range cannot tell that apart from a stacked screen
    /// that is merely showing an OLDER choice (stock on 1Y, the user picks 2Y on a crypto
    /// screen above it, comes back and re-taps 1Y) — and that lost a real choice. A tap is
    /// what the user last picked; the coercions this memory must never store come from code
    /// paths (restore, fallback, range sinks), and none of those call this.
    static func rememberUserRange(_ range: ChartTimeRange) {
        UserDefaults.standard.set(range.rawValue, forKey: rangeKey)
    }

    /// An interval row was tapped. Always a real choice: the picker lists only
    /// `allowedIntervals(for:)`, and the only coerced intervals (crypto 1D/1W) have a single
    /// option, so the picker is hidden there.
    static func rememberUserInterval(_ interval: ChartInterval, for range: ChartTimeRange) {
        var map = storedIntervals
        map[range.rawValue] = interval.rawValue
        UserDefaults.standard.set(map, forKey: intervalByRangeKey)
    }
}

// MARK: - Chart Placeholder

/// What the main chart frame shows while it has no bars, instead of an empty 140 pt box.
enum ChartPlaceholder: Equatable {
    /// The bars are still on their way (the stock fast-core carries no chart on a daily
    /// range, so a remembered 3M opens on 2–5 s of nothing without this).
    case loading
    /// The source has no bars for this range, and says so.
    case note(String)
}

// MARK: - Chart Viewport State (pinch-to-zoom + pan)

class ChartViewportState: ObservableObject {
    @Published var visibleStart: Int = 0
    @Published var visibleEnd: Int = 0

    /// The total number of data points
    private(set) var totalCount: Int = 0

    /// Minimum number of visible points (prevent over-zoom)
    private let minVisibleCount = 10

    /// Reset visible range (called when new data arrives).
    /// `displayStart` offsets the left edge so warm-up data used by
    /// technical indicators is available but not rendered.
    func reset(totalCount: Int, displayStart: Int = 0) {
        self.totalCount = totalCount
        self.visibleStart = min(displayStart, max(totalCount - 1, 0))
        self.visibleEnd = max(totalCount - 1, 0)
    }

    /// Whether the chart is currently zoomed in
    var isZoomed: Bool {
        visibleEnd - visibleStart + 1 < totalCount
    }

    /// Extend the visible range to include newly appended data points
    /// without resetting zoom/pan state.
    func extendToEnd(newTotalCount: Int) {
        guard newTotalCount > totalCount else { return }
        let added = newTotalCount - totalCount
        totalCount = newTotalCount
        visibleEnd = min(visibleEnd + added, totalCount - 1)
    }

    /// The number of currently visible points
    var visibleCount: Int {
        visibleEnd - visibleStart + 1
    }

    /// Apply a zoom scale around the center of the visible range
    func zoom(scale: CGFloat) {
        guard totalCount > minVisibleCount else { return }
        let center = Double(visibleStart + visibleEnd) / 2.0
        let currentHalf = Double(visibleEnd - visibleStart) / 2.0
        let newHalf = currentHalf / Double(scale)

        let newStart = Int(max(0, center - newHalf))
        let newEnd = Int(min(Double(totalCount - 1), center + newHalf))

        // Enforce minimum visible count
        if newEnd - newStart + 1 >= minVisibleCount {
            visibleStart = newStart
            visibleEnd = newEnd
        }
    }

    /// Pan by a number of data points (negative = left, positive = right)
    func pan(byPoints delta: Int) {
        guard isZoomed else { return }
        let count = visibleEnd - visibleStart
        var newStart = visibleStart + delta
        var newEnd = visibleEnd + delta

        // Clamp to bounds
        if newStart < 0 {
            newStart = 0
            newEnd = count
        }
        if newEnd >= totalCount {
            newEnd = totalCount - 1
            newStart = max(0, newEnd - count)
        }

        visibleStart = newStart
        visibleEnd = newEnd
    }
}

// MARK: - Indicator Result Models

struct MAData {
    let period: Int
    let values: [Double?]
    let color: Color
}

struct BollingerBandData {
    let upper: [Double?]
    let middle: [Double?]
    let lower: [Double?]
}

struct RSIData {
    let values: [Double?]
}

struct MACDData {
    let macdLine: [Double?]
    let signalLine: [Double?]
    let histogram: [Double?]
}

struct StochasticData {
    let kValues: [Double?]
    let dValues: [Double?]
}

// MARK: - Chart Event Dates (Earnings & Dividends)

struct ChartEventDates: Codable {
    let earningsDates: [String]   // "yyyy-MM-dd"
    let dividendDates: [String]   // "yyyy-MM-dd"

    enum CodingKeys: String, CodingKey {
        case earningsDates = "earnings_dates"
        case dividendDates = "dividend_dates"
    }
}
