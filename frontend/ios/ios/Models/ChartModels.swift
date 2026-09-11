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

    var supportsExtendedHours: Bool {
        self != .crypto
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

class ChartSettings: ObservableObject {
    private static let chartTypeKey = "caydex_preferred_chart_type"

    @Published var chartType: ChartType {
        didSet {
            UserDefaults.standard.set(chartType.rawValue, forKey: Self.chartTypeKey)
        }
    }
    @Published var selectedInterval: ChartInterval = .fiveMin
    @Published var enabledIndicators: Set<TechnicalIndicatorType> = []
    @Published var showExtendedHours: Bool = true
    @Published var showEarningsDates: Bool = false

    init() {
        // Restore persisted chart type, default to .line
        if let saved = UserDefaults.standard.string(forKey: Self.chartTypeKey),
           let type = ChartType(rawValue: saved) {
            self.chartType = type
        } else {
            self.chartType = .line
        }
    }

    var activeOverlays: [TechnicalIndicatorType] {
        enabledIndicators.filter { $0.isOverlay }.sorted { $0.rawValue < $1.rawValue }
    }

    var activeSubCharts: [TechnicalIndicatorType] {
        let displayOrder: [TechnicalIndicatorType] = [.volume, .rsi14, .macd, .stochastic]
        return displayOrder.filter { enabledIndicators.contains($0) }
    }
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
