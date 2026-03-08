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
        case .bar:    return "chart.bar.fill"
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
    case stochastic = "Stoch"

    var id: String { rawValue }

    var isOverlay: Bool {
        switch self {
        case .ma20, .ma50, .ma200, .bollingerBands: return true
        case .volume, .rsi14, .macd, .stochastic:   return false
        }
    }

    var defaultColor: Color {
        switch self {
        case .ma20:           return .blue
        case .ma50:           return .orange
        case .ma200:          return .purple
        case .bollingerBands: return .cyan
        case .volume:         return .gray
        case .rsi14:          return .yellow
        case .macd:           return .green
        case .stochastic:     return .pink
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
}

// MARK: - Chart Settings

class ChartSettings: ObservableObject {
    @Published var chartType: ChartType = .line
    @Published var selectedInterval: ChartInterval = .daily
    @Published var enabledIndicators: Set<TechnicalIndicatorType> = []
    @Published var showExtendedHours: Bool = false

    var activeOverlays: [TechnicalIndicatorType] {
        enabledIndicators.filter { $0.isOverlay }.sorted { $0.rawValue < $1.rawValue }
    }

    var activeSubCharts: [TechnicalIndicatorType] {
        enabledIndicators.filter { !$0.isOverlay }.sorted { $0.rawValue < $1.rawValue }
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

    /// Reset to show all data (called when new data arrives)
    func reset(totalCount: Int) {
        self.totalCount = totalCount
        self.visibleStart = 0
        self.visibleEnd = max(totalCount - 1, 0)
    }

    /// Whether the chart is currently zoomed in
    var isZoomed: Bool {
        visibleEnd - visibleStart + 1 < totalCount
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
