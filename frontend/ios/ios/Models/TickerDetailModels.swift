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
    case insiders = "Insiders"
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

    var isPositive: Bool {
        changePercent >= 0
    }

    var formattedChange: String {
        let sign = changePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", changePercent))%"
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
        case .excellent: return "Excellent"
        case .strong: return "Strong"
        case .average: return "Average"
        case .weak: return "Weak"
        case .poor: return "Poor"
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
        relatedTickers: RelatedTicker.sampleData
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
        PerformancePeriod(label: "1 Month", changePercent: 8.42),
        PerformancePeriod(label: "3 Months", changePercent: -3.15),
        PerformancePeriod(label: "6 Months", changePercent: 18.67),
        PerformancePeriod(label: "YTD", changePercent: 42.89),
        PerformancePeriod(label: "1 Year", changePercent: 38.24),
        PerformancePeriod(label: "5 Years", changePercent: 287.45)
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
    case extremeBearish = "Extreme Bearish"
    case bearish = "Bearish"
    case neutral = "Neutral"
    case bullish = "Bullish"
    case extremeBullish = "Extreme Bullish"

    var color: Color {
        switch self {
        case .extremeBearish, .bearish:
            return AppColors.bearish
        case .neutral:
            return AppColors.neutral
        case .bullish, .extremeBullish:
            return AppColors.bullish
        }
    }

    static func fromScore(_ score: Int) -> MarketMoodLevel {
        switch score {
        case 0..<20:
            return .extremeBearish
        case 20..<40:
            return .bearish
        case 40..<60:
            return .neutral
        case 60..<80:
            return .bullish
        default:
            return .extremeBullish
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
        last24hMood: .bearish,
        last7dMood: .neutral,
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
            return Color(hex: "991B1B")
        case .sell:
            return AppColors.bearish
        case .hold:
            return AppColors.neutral
        case .buy:
            return Color(hex: "4ADE80")
        case .strongBuy:
            return AppColors.bullish
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
