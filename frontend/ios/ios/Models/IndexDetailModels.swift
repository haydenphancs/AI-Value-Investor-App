//
//  IndexDetailModels.swift
//  ios
//
//  Data models for the Index Detail screen
//

import Foundation
import SwiftUI

// MARK: - Index Detail Tab
enum IndexDetailTab: String, CaseIterable {
    case overview = "Overview"
    case news = "News"
    case analysis = "Analysis"
}

// MARK: - Index Snapshot Category
enum IndexSnapshotCategory: String, CaseIterable {
    case valuation = "Valuation"
    case sectorPerformance = "Sector Performance"
    case macroForecast = "The Macro Forecast"

    var iconName: String {
        switch self {
        case .valuation: return "chart.bar.fill"
        case .sectorPerformance: return "chart.pie.fill"
        case .macroForecast: return "globe.americas.fill"
        }
    }
}

// MARK: - Index Snapshot Item
struct IndexSnapshotItem: Identifiable {
    let id = UUID()
    let category: IndexSnapshotCategory
    let metrics: [SnapshotMetric]

    init(category: IndexSnapshotCategory, metrics: [SnapshotMetric] = []) {
        self.category = category
        self.metrics = metrics
    }
}

// MARK: - Index Profile
struct IndexProfile {
    let description: String
    let exchange: String
    let numberOfConstituents: Int
    let weightingMethodology: String
    let inceptionDate: String
    let indexProvider: String
    let website: String

    var formattedConstituents: String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.groupingSeparator = ","
        return formatter.string(from: NSNumber(value: numberOfConstituents)) ?? "\(numberOfConstituents)"
    }
}

// MARK: - Index Detail Data
struct IndexDetailData: Identifiable {
    let id = UUID()
    let symbol: String
    let indexName: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: MarketStatus
    let chartData: [Double]
    let keyStatisticsGroups: [KeyStatisticsGroup]
    let performancePeriods: [PerformancePeriod]
    let snapshots: [IndexSnapshotItem]
    let indexProfile: IndexProfile

    var isPositive: Bool {
        priceChange >= 0
    }

    var formattedPrice: String {
        String(format: "$%.2f", currentPrice)
    }

    var formattedChange: String {
        let sign = priceChange >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", priceChange))"
    }

    var formattedChangePercent: String {
        let sign = priceChangePercent >= 0 ? "+" : ""
        return "(\(sign)\(String(format: "%.2f", priceChangePercent))%)"
    }
}

// MARK: - AI Suggestion for Index
struct IndexAISuggestion: Identifiable {
    let id = UUID()
    let text: String

    static let defaultSuggestions: [IndexAISuggestion] = [
        IndexAISuggestion(text: "What's the P/E ratio?"),
        IndexAISuggestion(text: "Which sectors lead?"),
        IndexAISuggestion(text: "What's the outlook?"),
        IndexAISuggestion(text: "Top constituents?")
    ]
}

// MARK: - Sample Data

extension IndexDetailData {
    static let sampleSP500 = IndexDetailData(
        symbol: "^GSPC",
        indexName: "S&P 500",
        currentPrice: 6025.99,
        priceChange: 58.44,
        priceChangePercent: 0.98,
        marketStatus: .closed(
            date: Calendar.current.date(from: DateComponents(year: 2026, month: 2, day: 14))!,
            time: "4:00 PM",
            timezone: "EST"
        ),
        chartData: [5850, 5880, 5920, 5890, 5950, 5980, 5940, 6000, 5970, 6030, 6010, 6060, 6025],
        keyStatisticsGroups: KeyStatisticsGroup.indexSampleData,
        performancePeriods: PerformancePeriod.indexSampleData,
        snapshots: IndexSnapshotItem.sampleData,
        indexProfile: IndexProfile(
            description: "The S&P 500 Index is a market-capitalization-weighted index of 500 leading publicly traded companies in the U.S. It is widely regarded as the best single gauge of large-cap U.S. equities and serves as the foundation for a wide range of investment products.",
            exchange: "NYSE / NASDAQ",
            numberOfConstituents: 503,
            weightingMethodology: "Market-Cap Weighted",
            inceptionDate: "March 4, 1957",
            indexProvider: "S&P Dow Jones Indices",
            website: "www.spglobal.com"
        )
    )
}

extension KeyStatisticsGroup {
    static let indexSampleData: [KeyStatisticsGroup] = [
        // Column 1: Price
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Open", value: "5,998.12"),
            KeyStatistic(label: "Previous Close", value: "5,967.55"),
            KeyStatistic(label: "Day High", value: "6,032.48"),
            KeyStatistic(label: "Day Low", value: "5,985.30"),
            KeyStatistic(label: "52-Week High", value: "6,128.18")
        ]),
        // Column 2: Performance
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "52-Week Low", value: "4,835.04"),
            KeyStatistic(label: "50-Day Avg", value: "5,942.15"),
            KeyStatistic(label: "200-Day Avg", value: "5,654.38"),
            KeyStatistic(label: "YTD Return", value: "+4.82%", isHighlighted: true),
            KeyStatistic(label: "1-Year Return", value: "+24.31%", isHighlighted: true)
        ]),
        // Column 3: Fundamentals
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "P/E (TTM)", value: "28.42"),
            KeyStatistic(label: "P/E (FWD)", value: "22.15"),
            KeyStatistic(label: "Dividend Yield", value: "1.28%"),
            KeyStatistic(label: "Earnings Yield", value: "3.52%"),
            KeyStatistic(label: "Total Market Cap", value: "48.6T")
        ]),
        // Column 4: Volume & Breadth
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Volume", value: "3.82B"),
            KeyStatistic(label: "Avg. Volume (30D)", value: "4.15B"),
            KeyStatistic(label: "Constituents", value: "503"),
            KeyStatistic(label: "Advancers", value: "312"),
            KeyStatistic(label: "Decliners", value: "191")
        ])
    ]
}

extension PerformancePeriod {
    static let indexSampleData: [PerformancePeriod] = [
        PerformancePeriod(label: "1 Month", changePercent: 3.12),
        PerformancePeriod(label: "3 Months", changePercent: 7.45),
        PerformancePeriod(label: "6 Months", changePercent: 12.38),
        PerformancePeriod(label: "YTD", changePercent: 4.82),
        PerformancePeriod(label: "1 Year", changePercent: 24.31),
        PerformancePeriod(label: "5 Years", changePercent: 82.67)
    ]
}

extension IndexSnapshotItem {
    static let sampleData: [IndexSnapshotItem] = [
        IndexSnapshotItem(category: .valuation),
        IndexSnapshotItem(category: .sectorPerformance),
        IndexSnapshotItem(category: .macroForecast)
    ]
}
