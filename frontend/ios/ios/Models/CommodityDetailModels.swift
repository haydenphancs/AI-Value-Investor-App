//
//  CommodityDetailModels.swift
//  ios
//
//  Data models for the Commodity Detail screen
//  Supports: Metals (Gold, Silver, Copper, Platinum),
//  Energy (Crude Oil, Natural Gas, Gasoline),
//  Agriculture (Wheat, Corn, Soybeans, Cotton),
//  Consumables (Coffee, Sugar, Cocoa, Orange Juice)
//

import Foundation
import SwiftUI

// MARK: - Commodity Detail Tab
enum CommodityDetailTab: String, CaseIterable {
    case overview = "Overview"
    case news = "News"
    case analysis = "Analysis"
}

// MARK: - Commodity Category
enum CommodityCategory: String, CaseIterable {
    case metals = "Metals"
    case energy = "Energy"
    case agriculture = "Agriculture"
    case consumables = "Consumables"

    var iconName: String {
        switch self {
        case .metals: return "diamond.fill"
        case .energy: return "bolt.fill"
        case .agriculture: return "leaf.fill"
        case .consumables: return "cup.and.saucer.fill"
        }
    }

    var color: Color {
        switch self {
        case .metals: return Color(lightHex: "8F6100", darkHex: "FFD700")
        case .energy: return Color(lightHex: "C2410C", darkHex: "FF8A5C")
        case .agriculture: return AppColors.bullish
        case .consumables: return Color(lightHex: "8B4513", darkHex: "C08457")
        }
    }
}

// MARK: - Commodity Market Status
enum CommodityMarketStatus {
    case open
    case closed(date: Date, time: String, timezone: String)
    case preMarket
    case afterHours

    var displayText: String {
        switch self {
        case .open:
            return "Market Open"
        case .closed(let date, let time, let timezone):
            let formatter = DateFormatter()
            formatter.dateFormat = "MMM d"
            return "Market Closed  \(formatter.string(from: date)), \(time) \(timezone)"
        case .preMarket:
            return "Pre-Market"
        case .afterHours:
            return "After Hours"
        }
    }
}

// MARK: - Commodity Unit
enum CommodityUnit: String {
    case troyOunce = "per troy oz"
    case pound = "per lb"
    case barrel = "per bbl"
    case mmbtu = "per MMBtu"
    case gallon = "per gal"
    case bushel = "per bu"
    case ton = "per ton"
    case contract = "per contract"

    var shortName: String { rawValue }
}

// MARK: - Commodity Profile
struct CommodityProfile {
    let description: String
    let category: CommodityCategory
    let exchange: String
    let tradingHours: String
    let contractSize: String
    let unit: CommodityUnit
    let currency: String
    let tickSize: String
    let majorProducers: String
    let majorConsumers: String
    let website: String?

    var formattedUnit: String {
        unit.shortName
    }
}

// MARK: - Commodity Detail Data
/// The header's price formatting, in ONE place — shared by `CommodityDetailData` and
/// the fast-core `CommodityCoreData`.
enum CommodityHeaderFormat {
    static func price(_ value: Double) -> String {
        // The >=100 and >=1 arms are deliberately identical; kept as written so this
        // extraction changes no rendered string.
        if value >= 100 {
            return String(format: "$%.2f", value)
        } else if value >= 1 {
            return String(format: "$%.2f", value)
        } else {
            return String(format: "$%.4f", value)
        }
    }

    static func change(_ value: Double) -> String {
        let sign = value >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", value))"
    }

    static func changePercent(_ value: Double) -> String {
        let sign = value >= 0 ? "+" : ""
        return "(\(sign)\(String(format: "%.2f", value))%)"
    }
}

struct CommodityDetailData: Identifiable {
    let id = UUID()
    let symbol: String
    let name: String
    // `var`, mirroring IndexDetailData: the live-price socket merges ticks into these
    // three in place. They were `let`, which is why the commodity screen had no live
    // price path at all — the quote was frozen for the life of the screen.
    var currentPrice: Double
    var priceChange: Double
    var priceChangePercent: Double
    // `var`, like the three price fields above: the light-refresh path merges into this
    // struct IN PLACE. It used to replace the whole value every 30s, which erased every
    // WebSocket tick that had landed since — a 30-second sawtooth on a live price.
    var marketStatus: CommodityMarketStatus
    var chartPricePoints: [StockPricePoint]
    var keyStatisticsGroups: [KeyStatisticsGroup]
    let performancePeriods: [PerformancePeriod]
    let commodityProfile: CommodityProfile
    var relatedCommodities: [RelatedTicker]
    let benchmarkSummary: PerformanceBenchmarkSummary?

    var chartData: [Double] {
        chartPricePoints.map { $0.close }
    }

    var isPositive: Bool {
        priceChange >= 0
    }

    /// Previous close (current price − today's change). Anchors the chart's
    /// dashed baseline to prior close, like Apple Stocks / Robinhood.
    var previousClose: Double {
        currentPrice - priceChange
    }

    var formattedPrice: String { CommodityHeaderFormat.price(currentPrice) }
    var formattedChange: String { CommodityHeaderFormat.change(priceChange) }
    var formattedChangePercent: String {
        CommodityHeaderFormat.changePercent(priceChangePercent)
    }
}

// MARK: - Commodity AI Suggestion
struct CommodityAISuggestion: Identifiable {
    let id = UUID()
    let text: String

    static let defaultSuggestions: [CommodityAISuggestion] = [
        CommodityAISuggestion(text: "What drives the price?"),
        CommodityAISuggestion(text: "Supply & demand outlook"),
        CommodityAISuggestion(text: "How to invest?"),
        CommodityAISuggestion(text: "Seasonal patterns?")
    ]
}

// MARK: - Sample Data

extension CommodityDetailData {
    static let sampleGold = CommodityDetailData(
        symbol: "GCUSD",
        name: "Gold",
        currentPrice: 2345.60,
        priceChange: 18.40,
        priceChangePercent: 0.79,
        marketStatus: .open,
        chartPricePoints: [2280, 2295, 2310, 2305, 2320, 2315, 2330, 2340, 2335, 2350, 2342, 2345].enumerated().map { i, c in
            StockPricePoint(date: "2026-02-\(String(format: "%02d", i+1))", close: c, open: c - 5, high: c + 8, low: c - 8, volume: 200_000)
        },
        keyStatisticsGroups: CommodityKeyStatisticsGroup.sampleGold,
        performancePeriods: CommodityPerformance.sampleGold,
        commodityProfile: CommodityProfile(
            description: "Gold is a precious metal that has been valued throughout human history as a store of wealth, medium of exchange, and safe-haven asset. It is widely used in jewelry, electronics, and central bank reserves. Gold prices are influenced by inflation, interest rates, geopolitical tensions, and currency movements, particularly the US dollar.",
            category: .metals,
            exchange: "COMEX / NYMEX",
            tradingHours: "Sun-Fri 6:00 PM - 5:00 PM ET",
            contractSize: "100 troy ounces",
            unit: .troyOunce,
            currency: "USD",
            tickSize: "$0.10",
            majorProducers: "China, Australia, Russia, USA, Canada",
            majorConsumers: "China, India, USA, Germany, Turkey",
            website: nil
        ),
        relatedCommodities: CommodityRelatedTicker.sampleMetals,
        benchmarkSummary: PerformanceBenchmarkSummary(
            avgAnnualReturn: 8.2,
            spBenchmark: 10.5,
            benchmarkName: "S&P 500",
            sinceDate: "2015",
            badgeThreshold: 2,
            windowLabel: "All-time"
        )
    )

    static let sampleCrudeOil = CommodityDetailData(
        symbol: "CLUSD",
        name: "Crude Oil WTI",
        currentPrice: 78.42,
        priceChange: -1.23,
        priceChangePercent: -1.54,
        marketStatus: .open,
        chartPricePoints: [82, 81, 80, 79, 80, 78, 77, 79, 78, 77, 79, 78].enumerated().map { i, c in
            StockPricePoint(date: "2026-02-\(String(format: "%02d", i+1))", close: Double(c), open: Double(c) + 0.5, high: Double(c) + 1.5, low: Double(c) - 1.5, volume: 500_000)
        },
        keyStatisticsGroups: CommodityKeyStatisticsGroup.sampleCrudeOil,
        performancePeriods: CommodityPerformance.sampleCrudeOil,
        commodityProfile: CommodityProfile(
            description: "West Texas Intermediate (WTI) Crude Oil is a grade of crude oil used as a benchmark in oil pricing. It is the underlying commodity of the New York Mercantile Exchange's oil futures contracts. Crude oil prices are driven by global supply and demand, OPEC production decisions, geopolitical events, and economic growth indicators.",
            category: .energy,
            exchange: "NYMEX",
            tradingHours: "Sun-Fri 6:00 PM - 5:00 PM ET",
            contractSize: "1,000 barrels",
            unit: .barrel,
            currency: "USD",
            tickSize: "$0.01",
            majorProducers: "USA, Saudi Arabia, Russia, Canada, Iraq",
            majorConsumers: "USA, China, India, Japan, South Korea",
            website: nil
        ),
        relatedCommodities: CommodityRelatedTicker.sampleEnergy,
        benchmarkSummary: PerformanceBenchmarkSummary(
            avgAnnualReturn: -2.1,
            spBenchmark: 10.5,
            benchmarkName: "S&P 500",
            sinceDate: "2015",
            badgeThreshold: 2,
            windowLabel: "All-time"
        )
    )
}

// MARK: - Commodity Key Statistics Groups (FMP-based)
enum CommodityKeyStatisticsGroup {
    static let sampleGold: [KeyStatisticsGroup] = [
        // Column 1: Price & Volume
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Open", value: "$2,327.20"),
            KeyStatistic(label: "Previous Close", value: "$2,327.20"),
            KeyStatistic(label: "Day Range", value: "$2,318 - $2,352"),
            KeyStatistic(label: "Volume", value: "218.5K"),
            KeyStatistic(label: "Avg. Volume", value: "195.2K")
        ]),
        // Column 2: Performance
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "52-Week High", value: "$2,450.10"),
            KeyStatistic(label: "52-Week Low", value: "$1,810.80"),
            KeyStatistic(label: "52-Week Change", value: "+18.42%", isHighlighted: true),
            KeyStatistic(label: "50-Day MA", value: "$2,298.50"),
            KeyStatistic(label: "200-Day MA", value: "$2,145.30")
        ]),
        // Column 3: Contract Info
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Contract Size", value: "100 troy oz"),
            KeyStatistic(label: "Tick Size", value: "$0.10"),
            KeyStatistic(label: "Exchange", value: "COMEX"),
            KeyStatistic(label: "Currency", value: "USD"),
            KeyStatistic(label: "Unit", value: "per troy oz")
        ])
    ]

    static let sampleCrudeOil: [KeyStatisticsGroup] = [
        // Column 1: Price & Volume
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Open", value: "$79.65"),
            KeyStatistic(label: "Previous Close", value: "$79.65"),
            KeyStatistic(label: "Day Range", value: "$77.80 - $79.90"),
            KeyStatistic(label: "Volume", value: "412.8K"),
            KeyStatistic(label: "Avg. Volume", value: "385.1K")
        ]),
        // Column 2: Performance
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "52-Week High", value: "$95.03"),
            KeyStatistic(label: "52-Week Low", value: "$63.64"),
            KeyStatistic(label: "52-Week Change", value: "-8.32%", isHighlighted: true),
            KeyStatistic(label: "50-Day MA", value: "$76.42"),
            KeyStatistic(label: "200-Day MA", value: "$79.18")
        ]),
        // Column 3: Contract Info
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Contract Size", value: "1,000 bbl"),
            KeyStatistic(label: "Tick Size", value: "$0.01"),
            KeyStatistic(label: "Exchange", value: "NYMEX"),
            KeyStatistic(label: "Currency", value: "USD"),
            KeyStatistic(label: "Unit", value: "per barrel")
        ])
    ]
}

// MARK: - Commodity Performance Sample Data
enum CommodityPerformance {
    static let sampleGold: [PerformancePeriod] = [
        PerformancePeriod(label: "1 Month", changePercent: 3.42, vsMarketPercent: 1.8, benchmarkLabel: "S&P"),
        PerformancePeriod(label: "YTD", changePercent: 12.34, vsMarketPercent: 8.5, benchmarkLabel: "S&P"),
        PerformancePeriod(label: "1 Year", changePercent: 18.42, vsMarketPercent: 22.1, benchmarkLabel: "S&P"),
        PerformancePeriod(label: "3 Years", changePercent: 28.60, vsMarketPercent: 32.5, benchmarkLabel: "S&P"),
        PerformancePeriod(label: "5 Years", changePercent: 62.45, vsMarketPercent: 85.0, benchmarkLabel: "S&P"),
        PerformancePeriod(label: "10 Years", changePercent: 95.31, vsMarketPercent: 180.0, benchmarkLabel: "S&P")
    ]

    static let sampleCrudeOil: [PerformancePeriod] = [
        PerformancePeriod(label: "1 Month", changePercent: -2.18, vsMarketPercent: 1.8, benchmarkLabel: "S&P"),
        PerformancePeriod(label: "YTD", changePercent: -5.42, vsMarketPercent: 8.5, benchmarkLabel: "S&P"),
        PerformancePeriod(label: "1 Year", changePercent: -8.32, vsMarketPercent: 22.1, benchmarkLabel: "S&P"),
        PerformancePeriod(label: "3 Years", changePercent: 12.60, vsMarketPercent: 32.5, benchmarkLabel: "S&P"),
        PerformancePeriod(label: "5 Years", changePercent: -18.45, vsMarketPercent: 85.0, benchmarkLabel: "S&P"),
        PerformancePeriod(label: "10 Years", changePercent: -35.20, vsMarketPercent: 180.0, benchmarkLabel: "S&P")
    ]
}

// MARK: - Related Commodity Sample Data
enum CommodityRelatedTicker {
    static let sampleMetals: [RelatedTicker] = [
        RelatedTicker(symbol: "SIUSD", name: "Silver", price: 27.85, changePercent: 1.2),
        RelatedTicker(symbol: "HGUSD", name: "Copper", price: 4.12, changePercent: -0.8),
        RelatedTicker(symbol: "PLUSD", name: "Platinum", price: 1024.50, changePercent: 0.6),
        RelatedTicker(symbol: "PAUSD", name: "Palladium", price: 985.30, changePercent: -1.4),
        RelatedTicker(symbol: "GLD", name: "SPDR Gold ETF", price: 218.42, changePercent: 0.7),
        RelatedTicker(symbol: "SLV", name: "iShares Silver ETF", price: 25.18, changePercent: 1.1)
    ]

    static let sampleEnergy: [RelatedTicker] = [
        RelatedTicker(symbol: "NGUSD", name: "Natural Gas", price: 2.34, changePercent: 3.2),
        RelatedTicker(symbol: "RBUSD", name: "Gasoline RBOB", price: 2.18, changePercent: -0.5),
        RelatedTicker(symbol: "BZUSD", name: "Brent Crude", price: 82.56, changePercent: -1.2),
        RelatedTicker(symbol: "HOUSD", name: "Heating Oil", price: 2.48, changePercent: 0.8),
        RelatedTicker(symbol: "USO", name: "US Oil Fund ETF", price: 72.34, changePercent: -1.5),
        RelatedTicker(symbol: "XLE", name: "Energy Sector ETF", price: 89.12, changePercent: -0.3)
    ]
}
