//
//  TickerDetailModels.swift
//  ios
//
//  Data models for the Ticker Detail screen
//

import Foundation
import SwiftUI

// MARK: - Ticker Detail Tab
enum TickerDetailTab: String, CaseIterable {
    case overview = "Overview"
    case news = "News"
    case analysis = "Analysis"
    case financials = "Financials"
    case holders = "Holders"
}

// MARK: - Chart Time Range
enum ChartTimeRange: String, CaseIterable {
    case oneDay = "1D"
    case oneWeek = "1W"
    case threeMonths = "3M"
    case sixMonths = "6M"
    case oneYear = "1Y"
    case fiveYears = "5Y"
    case all = "ALL"

    var displayName: String { rawValue }
}

// MARK: - Market Status
enum MarketStatus {
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

// MARK: - Key Statistic
struct KeyStatistic: Identifiable {
    let id = UUID()
    let label: String
    let value: String
    let isHighlighted: Bool

    init(label: String, value: String, isHighlighted: Bool = false) {
        self.label = label
        self.value = value
        self.isHighlighted = isHighlighted
    }
}

// MARK: - Key Statistics Group (for card grouping)
struct KeyStatisticsGroup: Identifiable {
    let id = UUID()
    let statistics: [KeyStatistic]
}

// MARK: - Performance Period
struct PerformancePeriod: Identifiable {
    let id = UUID()
    let label: String
    let changePercent: Double
    let vsMarketPercent: Double?
    let benchmarkLabel: String

    init(label: String, changePercent: Double, vsMarketPercent: Double? = nil, benchmarkLabel: String = "S&P") {
        self.label = label
        self.changePercent = changePercent
        self.vsMarketPercent = vsMarketPercent
        self.benchmarkLabel = benchmarkLabel
    }

    var isPositive: Bool {
        changePercent >= 0
    }

    var formattedChange: String {
        let sign = changePercent >= 0 ? "+" : ""
        return "\(sign)\(Self.formatLargePercent(changePercent, defaultFormat: "%.2f"))%"
    }

    var formattedVsMarket: String? {
        guard let vs = vsMarketPercent else { return nil }
        let sign = vs >= 0 ? "+" : ""
        return "\(benchmarkLabel): \(sign)\(Self.formatLargePercent(vs, defaultFormat: "%.1f"))%"
    }

    /// Formats large percentages cleanly: 1,000%+ drops decimals and adds commas
    private static func formatLargePercent(_ value: Double, defaultFormat: String) -> String {
        if abs(value) >= 1000 {
            let formatter = NumberFormatter()
            formatter.numberStyle = .decimal
            formatter.maximumFractionDigits = 0
            formatter.groupingSeparator = ","
            return formatter.string(from: NSNumber(value: value)) ?? String(format: "%.0f", value)
        }
        return String(format: defaultFormat, value)
    }

    var isBeatingMarket: Bool {
        (vsMarketPercent ?? 0) >= 0
    }
}

// MARK: - Snapshot Rating Level
enum SnapshotRatingLevel: Int, CaseIterable {
    case excellent = 5
    case strong = 4
    case average = 3
    case weak = 2
    case poor = 1

    var displayName: String {
        switch self {
        case .excellent: return "High"
        case .strong: return "Solid"
        case .average: return "Moderate"
        case .weak: return "Soft"
        case .poor: return "Low"
        }
    }

    var starCount: Int { rawValue }

    var color: Color {
        switch self {
        case .excellent, .strong: return AppColors.bullish
        case .average: return AppColors.neutral
        case .weak, .poor: return AppColors.bearish
        }
    }

    var hasStroke: Bool {
        self == .excellent || self == .poor
    }
}

// MARK: - Snapshot Category
enum SnapshotCategory: String {
    case profitability = "Profitability"
    case growth = "Growth"
    case price = "Price"
    case financialHealth = "Financial Health"
    case insidersOwnership = "Insiders & Ownership"

    var iconName: String {
        switch self {
        case .profitability: return "chart.pie.fill"
        case .growth: return "chart.line.uptrend.xyaxis"
        case .price: return "dollarsign.circle.fill"
        case .financialHealth: return "cross.case.fill"
        case .insidersOwnership: return "person.2.fill"
        }
    }
}

// MARK: - Snapshot Metric
struct SnapshotMetric: Identifiable {
    let id = UUID()
    let name: String
    let value: String
}

// MARK: - Snapshot Item
struct SnapshotItem: Identifiable {
    let id = UUID()
    let category: SnapshotCategory
    let rating: SnapshotRatingLevel
    let metrics: [SnapshotMetric]
    let fullReportAvailable: Bool

    init(category: SnapshotCategory, rating: SnapshotRatingLevel, metrics: [SnapshotMetric], fullReportAvailable: Bool = true) {
        self.category = category
        self.rating = rating
        self.metrics = metrics
        self.fullReportAvailable = fullReportAvailable
    }
}

// MARK: - Sector & Industry Info
struct SectorIndustryInfo {
    let sector: String
    let industry: String
    let sectorPerformance: Double
    let industryRank: String

    var formattedPerformance: String {
        let sign = sectorPerformance >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", sectorPerformance))%"
    }

    var performanceColor: Color {
        sectorPerformance >= 0 ? AppColors.bullish : AppColors.bearish
    }
}

// MARK: - Company Profile
struct CompanyProfile {
    let description: String
    let ceo: String
    let founded: String
    let employees: Int
    let headquarters: String
    let website: String

    var formattedEmployees: String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.groupingSeparator = ","
        return formatter.string(from: NSNumber(value: employees)) ?? "\(employees)"
    }
}

// MARK: - Related Ticker
struct RelatedTicker: Identifiable {
    let id = UUID()
    let symbol: String
    let name: String
    let price: Double
    let changePercent: Double

    var isPositive: Bool {
        changePercent >= 0
    }

    var formattedPrice: String {
        String(format: "$%.2f", price)
    }

    var formattedChange: String {
        let sign = changePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", changePercent))%"
    }
}

// MARK: - Ticker Detail Data
struct TickerDetailData: Identifiable {
    let id = UUID()
    let symbol: String
    let companyName: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: MarketStatus
    let chartData: [Double]
    let keyStatistics: [KeyStatistic]
    let keyStatisticsGroups: [KeyStatisticsGroup]
    let performancePeriods: [PerformancePeriod]
    let snapshots: [SnapshotItem]
    let sectorIndustry: SectorIndustryInfo
    let companyProfile: CompanyProfile
    let relatedTickers: [RelatedTicker]
    let benchmarkSummary: PerformanceBenchmarkSummary?

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

// MARK: - AI Suggestion for Ticker
struct TickerAISuggestion: Identifiable {
    let id = UUID()
    let text: String

    static let defaultSuggestions: [TickerAISuggestion] = [
        TickerAISuggestion(text: "What's the P/E ratio?"),
        TickerAISuggestion(text: "Why does it move?"),
        TickerAISuggestion(text: "Should I buy?"),
        TickerAISuggestion(text: "Is revenue growing?")
    ]
}

// MARK: - Sample Data
extension TickerDetailData {
    static let sampleApple = TickerDetailData(
        symbol: "AAPL",
        companyName: "Apple Inc.",
        currentPrice: 178.42,
        priceChange: 2.34,
        priceChangePercent: 1.33,
        marketStatus: .closed(
            date: Calendar.current.date(from: DateComponents(year: 2024, month: 12, day: 18))!,
            time: "4:00 PM",
            timezone: "EST"
        ),
        chartData: [165, 168, 170, 172, 169, 174, 171, 175, 173, 178, 176, 180, 177, 182, 178],
        keyStatistics: KeyStatistic.sampleData,
        keyStatisticsGroups: KeyStatisticsGroup.sampleData,
        performancePeriods: PerformancePeriod.sampleData,
        snapshots: SnapshotItem.sampleData,
        sectorIndustry: SectorIndustryInfo(
            sector: "Technology",
            industry: "Consumer Electronics",
            sectorPerformance: 2.87,
            industryRank: "#1 of 42"
        ),
        companyProfile: CompanyProfile(
            description: "Apple Inc. designs, manufactures, and markets smartphones, personal computers, tablets, wearables, and accessories worldwide. The company offers iPhone, Mac, iPad, and Wearables, Home and Accessories products.",
            ceo: "Tim Cook",
            founded: "April 1, 1976",
            employees: 161000,
            headquarters: "Cupertino, CA",
            website: "www.apple.com"
        ),
        relatedTickers: RelatedTicker.sampleData,
        benchmarkSummary: PerformanceBenchmarkSummary(avgAnnualReturn: 28.6, spBenchmark: 10.5)
    )
}

extension KeyStatistic {
    static let sampleData: [KeyStatistic] = [
        KeyStatistic(label: "Open", value: "262.36"),
        KeyStatistic(label: "P/E (TTM)", value: "35.15"),
        KeyStatistic(label: "P/S", value: "52.57"),
        KeyStatistic(label: "Short % of Float", value: "0.83%", isHighlighted: true),
        KeyStatistic(label: "Previous Close", value: "267.26"),
        KeyStatistic(label: "P/E (FWD)", value: "31.84"),
        KeyStatistic(label: "P/S", value: "9.31"),
        KeyStatistic(label: "Shares Outstanding", value: "15.638"),
        KeyStatistic(label: "Volume", value: "39.43M"),
        KeyStatistic(label: "EPS (TTM)", value: "7.47"),
        KeyStatistic(label: "BVPS", value: "4.991"),
        KeyStatistic(label: "Float", value: "15.61B"),
        KeyStatistic(label: "Avg. Volume (3M)", value: "45.23M"),
        KeyStatistic(label: "Dividend & Yield", value: "1.04 (0.39%)"),
        KeyStatistic(label: "Beta", value: "1.09"),
        KeyStatistic(label: "% Held by Insiders", value: "1.69%"),
        KeyStatistic(label: "Market Cap", value: "3.89T"),
        KeyStatistic(label: "Ex-Dividend Date", value: "11/10/2025"),
        KeyStatistic(label: "Next Earnings", value: "1/29"),
        KeyStatistic(label: "% Held Inst.", value: "64.28%")
    ]
}

extension KeyStatisticsGroup {
    static let sampleData: [KeyStatisticsGroup] = [
        // Column 1: Price & Volume
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Open", value: "262.36"),
            KeyStatistic(label: "Previous Close", value: "267.26"),
            KeyStatistic(label: "Volume", value: "39.43M"),
            KeyStatistic(label: "Avg. Volume (3M)", value: "45.23M"),
            KeyStatistic(label: "Market Cap", value: "3.89T")
        ]),
        // Column 2: Valuation Ratios
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "P/E (TTM)", value: "35.15"),
            KeyStatistic(label: "P/E (FWD)", value: "31.84"),
            KeyStatistic(label: "EPS (TTM)", value: "7.47"),
            KeyStatistic(label: "Dividend & Yield", value: "1.04 (0.39%)"),
            KeyStatistic(label: "Ex-Dividend Date", value: "11/10/2025")
        ]),
        // Column 3: Per Share Data
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "P/B", value: "52.57"),
            KeyStatistic(label: "P/S", value: "9.31"),
            KeyStatistic(label: "BVPS", value: "4.991"),
            KeyStatistic(label: "Beta", value: "1.09"),
            KeyStatistic(label: "Next Earnings", value: "1/29")
        ]),
        // Column 4: Ownership & Float
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Short % of Float", value: "0.83%", isHighlighted: true),
            KeyStatistic(label: "Shares Outstanding", value: "15.63B"),
            KeyStatistic(label: "Float", value: "15.61B"),
            KeyStatistic(label: "% Held by Insiders", value: "1.69%"),
            KeyStatistic(label: "% Held Inst.", value: "64.28%")
        ])
    ]
}

extension PerformancePeriod {
    static let sampleData: [PerformancePeriod] = [
        PerformancePeriod(label: "1 Month", changePercent: 8.42, vsMarketPercent: 6.3),
        PerformancePeriod(label: "YTD", changePercent: 42.89, vsMarketPercent: 38.3),
        PerformancePeriod(label: "1 Year", changePercent: 38.24, vsMarketPercent: 16.1),
        PerformancePeriod(label: "3 Years", changePercent: 52.18, vsMarketPercent: 13.5),
        PerformancePeriod(label: "5 Years", changePercent: 287.45, vsMarketPercent: 205.0),
        PerformancePeriod(label: "10 Years", changePercent: 842.31, vsMarketPercent: 649.9)
    ]
}

extension SnapshotItem {
    static let sampleData: [SnapshotItem] = [
        SnapshotItem(
            category: .profitability,
            rating: .excellent,
            metrics: [
                SnapshotMetric(name: "Operating Margin", value: "30.7%"),
                SnapshotMetric(name: "Net Margin", value: "25.3%"),
                SnapshotMetric(name: "Return on Equity (ROE)", value: "147.2%"),
                SnapshotMetric(name: "Return on Assets (ROA)", value: "27.0%")
            ]
        ),
        SnapshotItem(
            category: .growth,
            rating: .average,
            metrics: [
                SnapshotMetric(name: "Revenue Growth (YoY)", value: "-2.8%"),
                SnapshotMetric(name: "EPS Growth", value: "+16.2%"),
                SnapshotMetric(name: "Free Cash Flow Growth (YoY)", value: "+8.9%"),
                SnapshotMetric(name: "Operating Income Growth", value: "+11.4%")
            ]
        ),
        SnapshotItem(
            category: .price,
            rating: .strong,
            metrics: [
                SnapshotMetric(name: "P/E (1.30x sector average 27.04)", value: "35.15"),
                SnapshotMetric(name: "P/S (1.32x sector average 7.19)", value: "9.48"),
                SnapshotMetric(name: "P/FCF (1.0x sector average 25.00)", value: "25.01"),
                SnapshotMetric(name: "EV/EBITDA (1.33x the sector average 18)", value: "24.03")
            ]
        ),
        SnapshotItem(
            category: .financialHealth,
            rating: .poor,
            metrics: [
                SnapshotMetric(name: "Altman Z-Score", value: "4.8"),
                SnapshotMetric(name: "Interest Coverage", value: "43.7x"),
                SnapshotMetric(name: "Cash to Debt", value: "0.38"),
                SnapshotMetric(name: "Free Cash Flow Margin", value: "25.8%"),
                SnapshotMetric(name: "Asset Turnover", value: "1.12")
            ]
        ),
        SnapshotItem(
            category: .insidersOwnership,
            rating: .weak,
            metrics: [
                SnapshotMetric(name: "Institutional Ownership", value: "61.0%"),
                SnapshotMetric(name: "Hedge Fund Holdings", value: "7.2%"),
                SnapshotMetric(name: "Insider Sold (6M shares)", value: "$2.4M"),
                SnapshotMetric(name: "Top 10 Holders", value: "42.6%"),
                SnapshotMetric(name: "Institutional Activity", value: "-3.2%")
            ]
        )
    ]
}

extension RelatedTicker {
    static let sampleData: [RelatedTicker] = [
        RelatedTicker(symbol: "MSFT", name: "Microsoft", price: 421.32, changePercent: 1.8),
        RelatedTicker(symbol: "GOOGL", name: "Alphabet", price: 178.69, changePercent: 2.1),
        RelatedTicker(symbol: "AMZN", name: "Amazon", price: 186.43, changePercent: -0.4),
        RelatedTicker(symbol: "TSLA", name: "Tesla", price: 252.18, changePercent: 3.2),
        RelatedTicker(symbol: "META", name: "Meta", price: 512.76, changePercent: 1.8),
        RelatedTicker(symbol: "NVDA", name: "NVIDIA", price: 489.32, changePercent: 4.7)
    ]
}

// MARK: - Analysis Tab Models

// MARK: - Analyst Consensus
enum AnalystConsensus: String, CaseIterable {
    case strongBuy = "STRONG BUY"
    case buy = "BUY"
    case hold = "HOLD"
    case sell = "SELL"
    case strongSell = "STRONG SELL"

    var color: Color {
        switch self {
        case .strongBuy, .buy:
            return AppColors.bullish
        case .hold:
            return AppColors.neutral
        case .sell, .strongSell:
            return AppColors.bearish
        }
    }
}

// MARK: - Analyst Rating Distribution
struct AnalystRatingDistribution: Identifiable {
    let id = UUID()
    let label: String
    let count: Int
    let color: Color

    var formattedCount: String {
        "\(count)"
    }
}

extension AnalystRatingDistribution {
    static let sampleData: [AnalystRatingDistribution] = [
        AnalystRatingDistribution(label: "Strong Buy", count: 18, color: AppColors.bullish),
        AnalystRatingDistribution(label: "Buy", count: 14, color: Color(hex: "4ADE80")),
        AnalystRatingDistribution(label: "Hold", count: 6, color: AppColors.neutral),
        AnalystRatingDistribution(label: "Sell", count: 2, color: AppColors.bearish),
        AnalystRatingDistribution(label: "Strong Sell", count: 0, color: Color(hex: "991B1B"))
    ]
}

// MARK: - Analyst Price Target
struct AnalystPriceTarget {
    let lowPrice: Double
    let averagePrice: Double
    let highPrice: Double
    let currentPrice: Double

    var formattedLow: String {
        String(format: "$%.2f", lowPrice)
    }

    var formattedAverage: String {
        String(format: "%.2f", averagePrice)
    }

    var formattedHigh: String {
        String(format: "$%.2f", highPrice)
    }

    var formattedCurrent: String {
        String(format: "%.2f", currentPrice)
    }

    var upsidePercent: Double {
        ((averagePrice - currentPrice) / currentPrice) * 100
    }

    var formattedUpside: String {
        let sign = upsidePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", upsidePercent))% upside"
    }

    var currentPricePosition: Double {
        guard highPrice > lowPrice else { return 0.5 }
        return (currentPrice - lowPrice) / (highPrice - lowPrice)
    }
    
    var averagePricePosition: Double {
        guard highPrice > lowPrice else { return 0.5 }
        return (averagePrice - lowPrice) / (highPrice - lowPrice)
    }
}

extension AnalystPriceTarget {
    static let sampleData = AnalystPriceTarget(
        lowPrice: 165.00,
        averagePrice: 212.60,
        highPrice: 250.00,
        currentPrice: 178.42
    )
}

// MARK: - Analyst Momentum Period
enum AnalystMomentumPeriod: String, CaseIterable {
    case sixMonths = "6M"
    case oneYear = "1Y"
}

// MARK: - Analyst Momentum Month Data
struct AnalystMomentumMonth: Identifiable {
    let id = UUID()
    let month: String
    let positiveCount: Int
    let negativeCount: Int

    var netValue: Int {
        positiveCount - negativeCount
    }

    var isPositive: Bool {
        netValue >= 0
    }
}

extension AnalystMomentumMonth {
    static let sampleData: [AnalystMomentumMonth] = [
        AnalystMomentumMonth(month: "Jul", positiveCount: 5, negativeCount: 2),
        AnalystMomentumMonth(month: "Aug", positiveCount: 7, negativeCount: 1),
        AnalystMomentumMonth(month: "Sep", positiveCount: 4, negativeCount: 3),
        AnalystMomentumMonth(month: "Oct", positiveCount: 2, negativeCount: 5),
        AnalystMomentumMonth(month: "Nov", positiveCount: 6, negativeCount: 2),
        AnalystMomentumMonth(month: "Dec", positiveCount: 3, negativeCount: 4)
    ]
}

// MARK: - Analyst Actions Summary
struct AnalystActionsSummary {
    let upgrades: Int
    let maintains: Int
    let downgrades: Int
}

extension AnalystActionsSummary {
    static let sampleData = AnalystActionsSummary(
        upgrades: 9,
        maintains: 8,
        downgrades: 2
    )
}

// MARK: - Analyst Action Type
enum AnalystActionType: String, CaseIterable {
    case upgrade = "UPGRADE"
    case downgrade = "DOWNGRADE"
    case maintain = "MAINTAIN"
    case initiated = "INITIATED"
    case reiterated = "REITERATED"

    var color: Color {
        switch self {
        case .upgrade:
            return AppColors.bullish
        case .downgrade:
            return AppColors.bearish
        case .maintain, .initiated, .reiterated:
            return AppColors.textSecondary
        }
    }

    var borderColor: Color {
        switch self {
        case .upgrade:
            return AppColors.bullish
        case .downgrade:
            return AppColors.bearish
        case .maintain, .initiated, .reiterated:
            return AppColors.textMuted
        }
    }

    var hasColoredBadge: Bool {
        switch self {
        case .upgrade, .downgrade:
            return true
        case .maintain, .initiated, .reiterated:
            return false
        }
    }
}

// MARK: - Analyst Rating Type
enum AnalystRatingType: String, CaseIterable {
    case strongBuy = "Strong buy"
    case buy = "Buy"
    case overweight = "Overweight"
    case equalWeight = "Equal-Weight"
    case neutral = "Neutral"
    case underperform = "Underpeform"
    case sell = "Sell"
    case strongSell = "Strong Sell"

    var isPositive: Bool {
        switch self {
        case .strongBuy, .buy, .overweight:
            return true
        case .equalWeight, .neutral:
            return false
        case .underperform, .sell, .strongSell:
            return false
        }
    }

    var color: Color {
        switch self {
        case .strongBuy, .buy, .overweight:
            return AppColors.bullish
        case .equalWeight, .neutral:
            return AppColors.textSecondary
        case .underperform, .sell, .strongSell:
            return AppColors.bearish
        }
    }
}

// MARK: - Analyst Action (Individual upgrade/downgrade entry)
struct AnalystAction: Identifiable {
    let id = UUID()
    let firmName: String
    let actionType: AnalystActionType
    let date: Date
    let previousRating: AnalystRatingType?
    let newRating: AnalystRatingType
    let previousPriceTarget: Double?
    let newPriceTarget: Double?

    var formattedDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MM/dd/yyyy"
        return formatter.string(from: date)
    }

    var formattedPreviousPrice: String? {
        guard let price = previousPriceTarget else { return nil }
        return String(format: "$%.0f", price)
    }

    var formattedNewPrice: String? {
        guard let price = newPriceTarget else { return nil }
        return String(format: "$%.0f", price)
    }

    var priceChangeColor: Color {
        guard let previous = previousPriceTarget, let new = newPriceTarget else {
            return AppColors.textSecondary
        }
        if new > previous {
            return AppColors.bullish
        } else if new < previous {
            return AppColors.bearish
        }
        return AppColors.textSecondary
    }
}

extension AnalystAction {
    static let sampleData: [AnalystAction] = [
        AnalystAction(
            firmName: "Morgan Stanley",
            actionType: .upgrade,
            date: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 12))!,
            previousRating: .equalWeight,
            newRating: .overweight,
            previousPriceTarget: 325,
            newPriceTarget: 320
        ),
        AnalystAction(
            firmName: "JP Morgan",
            actionType: .upgrade,
            date: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 11))!,
            previousRating: .equalWeight,
            newRating: .overweight,
            previousPriceTarget: 340,
            newPriceTarget: 330
        ),
        AnalystAction(
            firmName: "Goldman Sachs",
            actionType: .maintain,
            date: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 11))!,
            previousRating: .buy,
            newRating: .buy,
            previousPriceTarget: 325,
            newPriceTarget: 325
        ),
        AnalystAction(
            firmName: "BMO Capital Markets",
            actionType: .maintain,
            date: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 8))!,
            previousRating: .buy,
            newRating: .buy,
            previousPriceTarget: 280,
            newPriceTarget: 290
        ),
        AnalystAction(
            firmName: "Barclays",
            actionType: .maintain,
            date: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 7))!,
            previousRating: .buy,
            newRating: .buy,
            previousPriceTarget: 350,
            newPriceTarget: 325
        ),
        AnalystAction(
            firmName: "Piper Sandler",
            actionType: .initiated,
            date: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 3))!,
            previousRating: nil,
            newRating: .buy,
            previousPriceTarget: nil,
            newPriceTarget: 350
        ),
        AnalystAction(
            firmName: "Goldman Sachs",
            actionType: .reiterated,
            date: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 3))!,
            previousRating: .buy,
            newRating: .buy,
            previousPriceTarget: nil,
            newPriceTarget: 325
        ),
        AnalystAction(
            firmName: "Wedbush Securities",
            actionType: .upgrade,
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 14))!,
            previousRating: .buy,
            newRating: .strongBuy,
            previousPriceTarget: 370,
            newPriceTarget: 370
        ),
        AnalystAction(
            firmName: "Morgan Stanley",
            actionType: .downgrade,
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 11, day: 4))!,
            previousRating: .neutral,
            newRating: .underperform,
            previousPriceTarget: 350,
            newPriceTarget: 325
        ),
        AnalystAction(
            firmName: "Scotiabank",
            actionType: .downgrade,
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 11, day: 1))!,
            previousRating: .neutral,
            newRating: .sell,
            previousPriceTarget: 190,
            newPriceTarget: 190
        )
    ]
}

// MARK: - Analyst Ratings Data (Combined)
struct AnalystRatingsData {
    let totalAnalysts: Int
    let updatedDate: Date
    let consensus: AnalystConsensus
    let targetPrice: Double
    let targetUpside: Double
    let distributions: [AnalystRatingDistribution]
    let priceTarget: AnalystPriceTarget
    let momentumData: [AnalystMomentumMonth]
    let netPositive: Int
    let netNegative: Int
    let actionsSummary: AnalystActionsSummary
    let actions: [AnalystAction]

    var formattedTargetPrice: String {
        String(format: "$%.2f", targetPrice)
    }

    var formattedUpside: String {
        let sign = targetUpside >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", targetUpside))% upside"
    }

    var formattedUpdatedDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MM/dd/yyyy"
        return formatter.string(from: updatedDate)
    }
}

extension AnalystRatingsData {
    static let sampleData = AnalystRatingsData(
        totalAnalysts: 40,
        updatedDate: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 5))!,
        consensus: .strongBuy,
        targetPrice: 212.60,
        targetUpside: 17.2,
        distributions: AnalystRatingDistribution.sampleData,
        priceTarget: AnalystPriceTarget.sampleData,
        momentumData: AnalystMomentumMonth.sampleData,
        netPositive: 17,
        netNegative: 7,
        actionsSummary: AnalystActionsSummary.sampleData,
        actions: AnalystAction.sampleData
    )
}

// MARK: - Sentiment Timeframe
enum SentimentTimeframe: String, CaseIterable {
    case last24h = "Last 24H"
    case last7d = "Last 7D"
}

// MARK: - Market Mood Level
enum MarketMoodLevel: String {
    case bullish = "Bullish"
    case neutral = "Neutral"
    case bearish = "Bearish"

    var color: Color {
        switch self {
        case .bearish:
            return AppColors.bullish  // 0-30: Green zone
        case .neutral:
            return Color(hex: "6B7280")  // 31-70: Grey zone
        case .bullish:
            return AppColors.bearish  // 71-100: Red zone
        }
    }

    static func fromScore(_ score: Int) -> MarketMoodLevel {
        switch score {
        case 0...30:
            return .bearish  // Low scores (0-30) = Bearish (left side of gauge)
        case 31...70:
            return .neutral  // Middle scores (31-70) = Neutral
        default:  // 71-100
            return .bullish  // High scores (71-100) = Bullish (right side of gauge)
        }
    }
}


// MARK: - Sentiment Analysis Data
struct SentimentAnalysisData {
    let moodScore: Int // 0-100
    let last24hMood: MarketMoodLevel
    let last7dMood: MarketMoodLevel
    let socialMentions: Double
    let socialMentionsChange: Double
    let newsArticles: Int
    let newsArticlesChange: Double

    var formattedSocialMentions: String {
        if socialMentions >= 1000 {
            return String(format: "%.1fK", socialMentions / 1000)
        }
        return String(format: "%.0f", socialMentions)
    }

    var formattedSocialChange: String {
        let sign = socialMentionsChange >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.0f", socialMentionsChange))% today"
    }

    var formattedNewsArticles: String {
        "\(newsArticles)"
    }

    var formattedNewsChange: String {
        let sign = newsArticlesChange >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.0f", newsArticlesChange))% this week"
    }

    var socialChangeColor: Color {
        socialMentionsChange >= 0 ? AppColors.bullish : AppColors.bearish
    }

    var newsChangeColor: Color {
        newsArticlesChange >= 0 ? AppColors.bullish : AppColors.bearish
    }
}

extension SentimentAnalysisData {
    static let sampleData = SentimentAnalysisData(
        moodScore: 24,
        last24hMood: .bearish,  // 24 is in 0-30 range (Bearish/Red)
        last7dMood: .neutral,   // Neutral is 31-70 range (Grey)
        socialMentions: 12400,
        socialMentionsChange: 24,
        newsArticles: 847,
        newsArticlesChange: 18
    )
}

// MARK: - Technical Signal
enum TechnicalSignal: String, CaseIterable {
    case strongSell = "Strong Sell"
    case sell = "Sell"
    case hold = "Hold"
    case buy = "Buy"
    case strongBuy = "Strong Buy"

    var color: Color {
        switch self {
        case .strongSell:
            return Color(hex: "991B1B")  // Dark red
        case .sell:
            return AppColors.bearish     // Red
        case .hold:
            return Color(hex: "F59E0B")  // Yellow
        case .buy:
            return Color(hex: "4ADE80")  // Light green
        case .strongBuy:
            return AppColors.bullish     // Green
        }
    }

    var gaugePosition: Double {
        switch self {
        case .strongSell: return 0.1
        case .sell: return 0.3
        case .hold: return 0.5
        case .buy: return 0.7
        case .strongBuy: return 0.9
        }
    }
}

// MARK: - Technical Indicator Result
struct TechnicalIndicatorResult {
    let signal: TechnicalSignal
    let matchingIndicators: Int
    let totalIndicators: Int

    var formattedCount: String {
        "\(matchingIndicators) of \(totalIndicators) indicators"
    }
}

// MARK: - Technical Analysis Data
struct TechnicalAnalysisData {
    let dailySignal: TechnicalIndicatorResult
    let weeklySignal: TechnicalIndicatorResult
    let overallSignal: TechnicalSignal
    let gaugeValue: Double // 0.0 to 1.0

    var gaugeLevel: Int {
        switch gaugeValue {
        case 0..<0.2: return 1
        case 0.2..<0.4: return 2
        case 0.4..<0.6: return 3
        case 0.6..<0.8: return 4
        default: return 5
        }
    }
}

extension TechnicalAnalysisData {
    static let sampleData = TechnicalAnalysisData(
        dailySignal: TechnicalIndicatorResult(
            signal: .buy,
            matchingIndicators: 12,
            totalIndicators: 18
        ),
        weeklySignal: TechnicalIndicatorResult(
            signal: .strongBuy,
            matchingIndicators: 14,
            totalIndicators: 18
        ),
        overallSignal: .buy,
        gaugeValue: 0.72
    )
}

// MARK: - Combined Analysis Data
struct TickerAnalysisData {
    let analystRatings: AnalystRatingsData
    let sentimentAnalysis: SentimentAnalysisData
    let technicalAnalysis: TechnicalAnalysisData
}

extension TickerAnalysisData {
    static let sampleData = TickerAnalysisData(
        analystRatings: AnalystRatingsData.sampleData,
        sentimentAnalysis: SentimentAnalysisData.sampleData,
        technicalAnalysis: TechnicalAnalysisData.sampleData
    )
}

// MARK: - Technical Analysis Detail Models

// MARK: - Indicator Signal
enum IndicatorSignal: String {
    case buy = "Buy"
    case sell = "Sell"
    case neutral = "Neutral"

    var color: Color {
        switch self {
        case .buy: return AppColors.bullish
        case .sell: return AppColors.bearish
        case .neutral: return AppColors.textSecondary
        }
    }

    var arrowIcon: String? {
        switch self {
        case .buy: return "arrow.up"
        case .sell: return "arrow.down"
        case .neutral: return nil
        }
    }
}

// MARK: - Indicator Summary (Buy/Neutral/Sell counts)
struct IndicatorSummary {
    let buyCount: Int
    let neutralCount: Int
    let sellCount: Int

    var totalCount: Int {
        buyCount + neutralCount + sellCount
    }
}

// MARK: - Moving Average Indicator
struct MovingAverageIndicator: Identifiable {
    let id = UUID()
    let name: String
    let value: Double
    let signal: IndicatorSignal

    var formattedValue: String {
        String(format: "%.2f", value)
    }
}

extension MovingAverageIndicator {
    static let sampleData: [MovingAverageIndicator] = [
        MovingAverageIndicator(name: "MA(5)", value: 172.34, signal: .buy),
        MovingAverageIndicator(name: "MA(10)", value: 170.89, signal: .buy),
        MovingAverageIndicator(name: "MA(20)", value: 168.45, signal: .sell),
        MovingAverageIndicator(name: "MA(50)", value: 175.23, signal: .buy),
        MovingAverageIndicator(name: "MA(100)", value: 171.67, signal: .buy),
        MovingAverageIndicator(name: "MA(200)", value: 169.12, signal: .buy),
        MovingAverageIndicator(name: "EMA(5)", value: 173.89, signal: .buy),
        MovingAverageIndicator(name: "EMA(10)", value: 172.34, signal: .buy),
        MovingAverageIndicator(name: "EMA(20)", value: 179.56, signal: .neutral),
        MovingAverageIndicator(name: "EMA(50)", value: 180.12, signal: .neutral)
    ]

    static let sampleSummary = IndicatorSummary(buyCount: 8, neutralCount: 2, sellCount: 1)
}

// MARK: - Oscillator Indicator
struct OscillatorIndicator: Identifiable {
    let id = UUID()
    let name: String
    let value: Double
    let signal: IndicatorSignal

    var formattedValue: String {
        String(format: "%.2f", value)
    }
}

extension OscillatorIndicator {
    static let sampleData: [OscillatorIndicator] = [
        OscillatorIndicator(name: "RSI(14)", value: 58.34, signal: .neutral),
        OscillatorIndicator(name: "Stoch(9,6)", value: 42.67, signal: .buy),
        OscillatorIndicator(name: "StochRSI", value: 38.92, signal: .buy),
        OscillatorIndicator(name: "MACD(12,26)", value: 1.23, signal: .buy),
        OscillatorIndicator(name: "ADX(14)", value: 28.45, signal: .neutral),
        OscillatorIndicator(name: "Williams %R", value: -35.67, signal: .neutral),
        OscillatorIndicator(name: "CCI(14)", value: 45.23, signal: .buy),
        OscillatorIndicator(name: "ATR(14)", value: 3.89, signal: .neutral)
    ]

    static let sampleSummary = IndicatorSummary(buyCount: 4, neutralCount: 4, sellCount: 0)
}

// MARK: - Pivot Point Level
struct PivotPointLevel: Identifiable {
    let id = UUID()
    let name: String
    let value: Double
    let levelType: PivotLevelType

    var formattedValue: String {
        String(format: "%.2f", value)
    }

    var valueColor: Color {
        levelType.color
    }
}

enum PivotLevelType {
    case resistance
    case pivot
    case support

    var color: Color {
        switch self {
        case .resistance: return AppColors.bullish
        case .pivot: return AppColors.textPrimary
        case .support: return AppColors.bearish
        }
    }
}

// MARK: - Pivot Points Data
struct PivotPointsData {
    let method: String
    let levels: [PivotPointLevel]
}

extension PivotPointsData {
    static let sampleData = PivotPointsData(
        method: "Classic Method",
        levels: [
            PivotPointLevel(name: "R3", value: 184.56, levelType: .resistance),
            PivotPointLevel(name: "R2", value: 182.34, levelType: .resistance),
            PivotPointLevel(name: "R1", value: 180.67, levelType: .resistance),
            PivotPointLevel(name: "Pivot", value: 178.42, levelType: .pivot),
            PivotPointLevel(name: "S1", value: 176.23, levelType: .support),
            PivotPointLevel(name: "S2", value: 174.89, levelType: .support),
            PivotPointLevel(name: "S3", value: 172.45, levelType: .support)
        ]
    )
}

// MARK: - Volume Analysis Data
struct VolumeAnalysisData {
    let currentVolume: Double
    let currentVolumeChange: Double
    let avgVolume30d: Double
    let volumeTrend: VolumeTrend
    let obv: Double
    let moneyFlowIndex: Double

    var formattedCurrentVolume: String {
        formatVolume(currentVolume)
    }

    var formattedVolumeChange: String {
        let sign = currentVolumeChange >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.0f", currentVolumeChange))%"
    }

    var volumeChangeColor: Color {
        currentVolumeChange >= 0 ? AppColors.bullish : AppColors.bearish
    }

    var formattedAvgVolume: String {
        formatVolume(avgVolume30d)
    }

    var formattedOBV: String {
        let sign = obv >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", obv))"
    }

    var obvColor: Color {
        obv >= 0 ? AppColors.bullish : AppColors.bearish
    }

    var formattedMFI: String {
        String(format: "%.2f", moneyFlowIndex)
    }

    private func formatVolume(_ volume: Double) -> String {
        if volume >= 1_000_000 {
            return String(format: "%.1fM", volume / 1_000_000)
        } else if volume >= 1_000 {
            return String(format: "%.1fK", volume / 1_000)
        }
        return String(format: "%.0f", volume)
    }
}

enum VolumeTrend: String {
    case increasing = "Increasing"
    case decreasing = "Decreasing"
    case stable = "Stable"

    var color: Color {
        switch self {
        case .increasing: return AppColors.bullish
        case .decreasing: return AppColors.bearish
        case .stable: return AppColors.textSecondary
        }
    }

    var icon: String {
        switch self {
        case .increasing: return "arrow.up"
        case .decreasing: return "arrow.down"
        case .stable: return "minus"
        }
    }
}

extension VolumeAnalysisData {
    static let sampleData = VolumeAnalysisData(
        currentVolume: 52_800_000,
        currentVolumeChange: 23,
        avgVolume30d: 42_900_000,
        volumeTrend: .increasing,
        obv: 2.48,
        moneyFlowIndex: 64.23
    )
}

// MARK: - Fibonacci Level
struct FibonacciLevel: Identifiable {
    let id = UUID()
    let percentage: String
    let value: Double
    let isKey: Bool // High, Low markers

    var formattedValue: String {
        String(format: "%.2f", value)
    }
}

// MARK: - Fibonacci Retracement Data
struct FibonacciRetracementData {
    let timeframe: String
    let levels: [FibonacciLevel]
}

extension FibonacciRetracementData {
    static let sampleData = FibonacciRetracementData(
        timeframe: "Weekly Levels",
        levels: [
            FibonacciLevel(percentage: "0.0%", value: 182.45, isKey: true),
            FibonacciLevel(percentage: "23.6%", value: 180.78, isKey: false),
            FibonacciLevel(percentage: "38.2%", value: 179.34, isKey: false),
            FibonacciLevel(percentage: "50.0%", value: 178.42, isKey: false),
            FibonacciLevel(percentage: "61.8%", value: 177.23, isKey: false),
            FibonacciLevel(percentage: "78.6%", value: 175.89, isKey: false),
            FibonacciLevel(percentage: "100.0%", value: 174.39, isKey: true)
        ]
    )
}

// MARK: - Support/Resistance Level
struct SupportResistanceLevel: Identifiable {
    let id = UUID()
    let name: String
    let value: Double
    let strength: LevelStrength
}

enum LevelStrength: String {
    case strong = "Strong"
    case moderate = "Moderate"
    case weak = "Weak"

    var color: Color {
        switch self {
        case .strong: return AppColors.bullish
        case .moderate: return AppColors.neutral
        case .weak: return AppColors.textMuted
        }
    }
}

// MARK: - Key Support & Resistance Data
struct SupportResistanceData {
    let currentPrice: Double
    let resistanceLevels: [SupportResistanceLevel]
    let supportLevels: [SupportResistanceLevel]

    var formattedCurrentPrice: String {
        String(format: "$%.2f", currentPrice)
    }
}

extension SupportResistanceData {
    static let sampleData = SupportResistanceData(
        currentPrice: 178.42,
        resistanceLevels: [
            SupportResistanceLevel(name: "R3", value: 184.56, strength: .strong),
            SupportResistanceLevel(name: "R2", value: 182.34, strength: .moderate),
            SupportResistanceLevel(name: "R1", value: 180.67, strength: .weak)
        ],
        supportLevels: [
            SupportResistanceLevel(name: "S1", value: 176.23, strength: .weak),
            SupportResistanceLevel(name: "S2", value: 174.89, strength: .moderate),
            SupportResistanceLevel(name: "S3", value: 172.45, strength: .strong)
        ]
    )
}

// MARK: - Complete Technical Analysis Detail Data
struct TechnicalAnalysisDetailData {
    let symbol: String
    let movingAverages: [MovingAverageIndicator]
    let movingAveragesSummary: IndicatorSummary
    let oscillators: [OscillatorIndicator]
    let oscillatorsSummary: IndicatorSummary
    let pivotPoints: PivotPointsData
    let volumeAnalysis: VolumeAnalysisData
    let fibonacciRetracement: FibonacciRetracementData
    let supportResistance: SupportResistanceData
}

extension TechnicalAnalysisDetailData {
    static let sampleData = TechnicalAnalysisDetailData(
        symbol: "AAPL",
        movingAverages: MovingAverageIndicator.sampleData,
        movingAveragesSummary: MovingAverageIndicator.sampleSummary,
        oscillators: OscillatorIndicator.sampleData,
        oscillatorsSummary: OscillatorIndicator.sampleSummary,
        pivotPoints: PivotPointsData.sampleData,
        volumeAnalysis: VolumeAnalysisData.sampleData,
        fibonacciRetracement: FibonacciRetracementData.sampleData,
        supportResistance: SupportResistanceData.sampleData
    )
}

// MARK: - Earnings Section Models

// MARK: - Earnings Data Type (EPS vs Revenue)
enum EarningsDataType: String, CaseIterable {
    case eps = "EPS"
    case revenue = "Revenue"
}

// MARK: - Earnings Time Range
enum EarningsTimeRange: String, CaseIterable {
    case oneYear = "1Y"
    case threeYears = "3Y"
}

// MARK: - Earnings Quarter Result
enum EarningsQuarterResult {
    case beat       // Green - actual > estimate
    case missed     // Red - actual < estimate
    case matched    // Green with dashed border - actual == estimate (0% surprise)
    case pending    // Gray - future quarter, only estimate available

    var dotColor: Color {
        switch self {
        case .beat, .matched:
            return AppColors.bullish
        case .missed:
            return AppColors.bearish
        case .pending:
            return AppColors.textSecondary
        }
    }

    var hasDashedBorder: Bool {
        self == .matched
    }
}

// MARK: - Earnings Quarter Data
struct EarningsQuarterData: Identifiable {
    let id = UUID()
    let quarter: String          // e.g., "Q1 '24"
    let actualValue: Double?     // nil for future quarters
    let estimateValue: Double
    let surprisePercent: Double? // nil for future quarters

    var result: EarningsQuarterResult {
        guard let actual = actualValue, let surprise = surprisePercent else {
            return .pending
        }

        if surprise == 0 {
            return .matched
        } else if actual > estimateValue {
            return .beat
        } else {
            return .missed
        }
    }

    var formattedSurprise: String? {
        guard let surprise = surprisePercent else { return nil }
        if surprise == 0 {
            return "0%"
        }
        let sign = surprise > 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", surprise))%"
    }

    var surpriseColor: Color {
        guard let surprise = surprisePercent else { return AppColors.textSecondary }
        if surprise > 0 {
            return AppColors.bullish
        } else if surprise < 0 {
            return AppColors.bearish
        }
        return AppColors.accentCyan
    }
}

// MARK: - Earnings Price Data Point (for price overlay)
struct EarningsPricePoint: Identifiable {
    var id = UUID()
    let quarter: String
    let price: Double
}

// MARK: - Earnings Data (Combined)
// MARK: - Next Earnings Date
enum EarningsReportTiming: String {
    case beforeMarketOpen = "Before Market Open"
    case afterMarketClose = "After Market Close"
    case duringMarketHours = "During Market Hours"
    case unknown = "Time Not Specified"
}

struct NextEarningsDate {
    let date: Date
    let isConfirmed: Bool
    let timing: EarningsReportTiming

    var formattedDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMMM d, yyyy"
        return formatter.string(from: date)
    }

    var statusText: String {
        isConfirmed ? "Confirmed" : "Expected"
    }
}

extension NextEarningsDate {
    static let sample = NextEarningsDate(
        date: {
            var components = DateComponents()
            components.year = 2024
            components.month = 1
            components.day = 25
            return Calendar.current.date(from: components) ?? Date()
        }(),
        isConfirmed: false,
        timing: .afterMarketClose
    )
}

struct EarningsData {
    let epsQuarters: [EarningsQuarterData]
    let revenueQuarters: [EarningsQuarterData]
    let priceHistory: [EarningsPricePoint]
    let nextEarningsDate: NextEarningsDate?

    init(
        epsQuarters: [EarningsQuarterData],
        revenueQuarters: [EarningsQuarterData],
        priceHistory: [EarningsPricePoint],
        nextEarningsDate: NextEarningsDate? = nil
    ) {
        self.epsQuarters = epsQuarters
        self.revenueQuarters = revenueQuarters
        self.priceHistory = priceHistory
        self.nextEarningsDate = nextEarningsDate
    }

    func quarters(for dataType: EarningsDataType) -> [EarningsQuarterData] {
        switch dataType {
        case .eps:
            return epsQuarters
        case .revenue:
            return revenueQuarters
        }
    }
}

extension EarningsData {
    static let sampleData = EarningsData(
        epsQuarters: [
            // 2022 data
            EarningsQuarterData(quarter: "Q1 '22", actualValue: 0.45, estimateValue: 0.42, surprisePercent: 7.1),
            EarningsQuarterData(quarter: "Q2 '22", actualValue: 0.52, estimateValue: 0.50, surprisePercent: 4.0),
            EarningsQuarterData(quarter: "Q3 '22", actualValue: 0.48, estimateValue: 0.52, surprisePercent: -7.7),
            EarningsQuarterData(quarter: "Q4 '22", actualValue: 0.55, estimateValue: 0.55, surprisePercent: 0),
            // 2023 data
            EarningsQuarterData(quarter: "Q1 '23", actualValue: 0.58, estimateValue: 0.55, surprisePercent: 5.5),
            EarningsQuarterData(quarter: "Q2 '23", actualValue: 0.62, estimateValue: 0.60, surprisePercent: 3.3),
            EarningsQuarterData(quarter: "Q3 '23", actualValue: 0.55, estimateValue: 0.58, surprisePercent: -5.2),
            EarningsQuarterData(quarter: "Q4 '23", actualValue: 0.68, estimateValue: 0.65, surprisePercent: 4.6),
            // 2024 data
            EarningsQuarterData(quarter: "Q1 '24", actualValue: 0.65, estimateValue: 0.58, surprisePercent: 4.2),
            EarningsQuarterData(quarter: "Q2 '24", actualValue: 1.20, estimateValue: 1.10, surprisePercent: 5.8),
            EarningsQuarterData(quarter: "Q3 '24", actualValue: 0.52, estimateValue: 0.55, surprisePercent: -1.2),
            EarningsQuarterData(quarter: "Q4 '24", actualValue: 0.25, estimateValue: 0.25, surprisePercent: 0),
            // 2025 future data
            EarningsQuarterData(quarter: "Q1 '25", actualValue: nil, estimateValue: 0.72, surprisePercent: nil),
            EarningsQuarterData(quarter: "Q2 '25", actualValue: nil, estimateValue: 1.35, surprisePercent: nil)
        ],
        revenueQuarters: [
            // 2022 data
            EarningsQuarterData(quarter: "Q1 '22", actualValue: 78.5, estimateValue: 76.0, surprisePercent: 3.3),
            EarningsQuarterData(quarter: "Q2 '22", actualValue: 82.3, estimateValue: 80.0, surprisePercent: 2.9),
            EarningsQuarterData(quarter: "Q3 '22", actualValue: 79.8, estimateValue: 82.0, surprisePercent: -2.7),
            EarningsQuarterData(quarter: "Q4 '22", actualValue: 88.2, estimateValue: 88.2, surprisePercent: 0),
            // 2023 data
            EarningsQuarterData(quarter: "Q1 '23", actualValue: 85.5, estimateValue: 83.0, surprisePercent: 3.0),
            EarningsQuarterData(quarter: "Q2 '23", actualValue: 90.2, estimateValue: 88.5, surprisePercent: 1.9),
            EarningsQuarterData(quarter: "Q3 '23", actualValue: 86.8, estimateValue: 89.0, surprisePercent: -2.5),
            EarningsQuarterData(quarter: "Q4 '23", actualValue: 95.5, estimateValue: 93.0, surprisePercent: 2.7),
            // 2024 data
            EarningsQuarterData(quarter: "Q1 '24", actualValue: 94.8, estimateValue: 92.5, surprisePercent: 2.5),
            EarningsQuarterData(quarter: "Q2 '24", actualValue: 98.2, estimateValue: 95.0, surprisePercent: 3.4),
            EarningsQuarterData(quarter: "Q3 '24", actualValue: 89.5, estimateValue: 91.0, surprisePercent: -1.6),
            EarningsQuarterData(quarter: "Q4 '24", actualValue: 102.3, estimateValue: 102.3, surprisePercent: 0),
            // 2025 future data
            EarningsQuarterData(quarter: "Q1 '25", actualValue: nil, estimateValue: 96.0, surprisePercent: nil),
            EarningsQuarterData(quarter: "Q2 '25", actualValue: nil, estimateValue: 105.0, surprisePercent: nil)
        ],
        priceHistory: [
            // 2022 data
            EarningsPricePoint(quarter: "Q1 '22", price: 0.42),
            EarningsPricePoint(quarter: "Q2 '22", price: 0.48),
            EarningsPricePoint(quarter: "Q3 '22", price: 0.52),
            EarningsPricePoint(quarter: "Q4 '22", price: 0.58),
            // 2023 data
            EarningsPricePoint(quarter: "Q1 '23", price: 0.55),
            EarningsPricePoint(quarter: "Q2 '23", price: 0.63),
            EarningsPricePoint(quarter: "Q3 '23", price: 0.68),
            EarningsPricePoint(quarter: "Q4 '23", price: 0.75),
            // 2024 data
            EarningsPricePoint(quarter: "Q1 '24", price: 0.60),
            EarningsPricePoint(quarter: "Q2 '24", price: 0.95),
            EarningsPricePoint(quarter: "Q3 '24", price: 0.98),
            EarningsPricePoint(quarter: "Q4 '24", price: 1.05),
            // 2025 future data
            EarningsPricePoint(quarter: "Q1 '25", price: 0.95),
            EarningsPricePoint(quarter: "Q2 '25", price: nil)
        ],
        nextEarningsDate: .sample
    )

    // Extended 3-year sample data
    static let sampleData3Year = EarningsData(
        epsQuarters: [
            EarningsQuarterData(quarter: "Q1 '22", actualValue: 0.45, estimateValue: 0.42, surprisePercent: 7.1),
            EarningsQuarterData(quarter: "Q2 '22", actualValue: 0.52, estimateValue: 0.50, surprisePercent: 4.0),
            EarningsQuarterData(quarter: "Q3 '22", actualValue: 0.48, estimateValue: 0.52, surprisePercent: -7.7),
            EarningsQuarterData(quarter: "Q4 '22", actualValue: 0.55, estimateValue: 0.55, surprisePercent: 0),
            EarningsQuarterData(quarter: "Q1 '23", actualValue: 0.58, estimateValue: 0.55, surprisePercent: 5.5),
            EarningsQuarterData(quarter: "Q2 '23", actualValue: 0.62, estimateValue: 0.60, surprisePercent: 3.3),
            EarningsQuarterData(quarter: "Q3 '23", actualValue: 0.55, estimateValue: 0.58, surprisePercent: -5.2),
            EarningsQuarterData(quarter: "Q4 '23", actualValue: 0.68, estimateValue: 0.65, surprisePercent: 4.6),
            EarningsQuarterData(quarter: "Q1 '24", actualValue: 0.65, estimateValue: 0.58, surprisePercent: 4.2),
            EarningsQuarterData(quarter: "Q2 '24", actualValue: 1.20, estimateValue: 1.10, surprisePercent: 5.8),
            EarningsQuarterData(quarter: "Q3 '24", actualValue: 0.52, estimateValue: 0.55, surprisePercent: -1.2),
            EarningsQuarterData(quarter: "Q4 '24", actualValue: 0.25, estimateValue: 0.25, surprisePercent: 0)
        ],
        revenueQuarters: [
            EarningsQuarterData(quarter: "Q1 '22", actualValue: 78.5, estimateValue: 76.0, surprisePercent: 3.3),
            EarningsQuarterData(quarter: "Q2 '22", actualValue: 82.3, estimateValue: 80.0, surprisePercent: 2.9),
            EarningsQuarterData(quarter: "Q3 '22", actualValue: 79.8, estimateValue: 82.0, surprisePercent: -2.7),
            EarningsQuarterData(quarter: "Q4 '22", actualValue: 88.2, estimateValue: 88.2, surprisePercent: 0),
            EarningsQuarterData(quarter: "Q1 '23", actualValue: 85.5, estimateValue: 83.0, surprisePercent: 3.0),
            EarningsQuarterData(quarter: "Q2 '23", actualValue: 90.2, estimateValue: 88.5, surprisePercent: 1.9),
            EarningsQuarterData(quarter: "Q3 '23", actualValue: 86.8, estimateValue: 89.0, surprisePercent: -2.5),
            EarningsQuarterData(quarter: "Q4 '23", actualValue: 95.5, estimateValue: 93.0, surprisePercent: 2.7),
            EarningsQuarterData(quarter: "Q1 '24", actualValue: 94.8, estimateValue: 92.5, surprisePercent: 2.5),
            EarningsQuarterData(quarter: "Q2 '24", actualValue: 98.2, estimateValue: 95.0, surprisePercent: 3.4),
            EarningsQuarterData(quarter: "Q3 '24", actualValue: 89.5, estimateValue: 91.0, surprisePercent: -1.6),
            EarningsQuarterData(quarter: "Q4 '24", actualValue: 102.3, estimateValue: 102.3, surprisePercent: 0)
        ],
        priceHistory: []
    )
}

// Fix for nil price in sample data
extension EarningsPricePoint {
    init(quarter: String, price: Double?) {
        self.id = UUID()
        self.quarter = quarter
        self.price = price ?? 0
    }
}
