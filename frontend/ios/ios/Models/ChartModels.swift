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
    @Published var enabledIndicators: Set<TechnicalIndicatorType> = []
    @Published var showExtendedHours: Bool = false

    var activeOverlays: [TechnicalIndicatorType] {
        enabledIndicators.filter { $0.isOverlay }.sorted { $0.rawValue < $1.rawValue }
    }

    var activeSubCharts: [TechnicalIndicatorType] {
        enabledIndicators.filter { !$0.isOverlay }.sorted { $0.rawValue < $1.rawValue }
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
