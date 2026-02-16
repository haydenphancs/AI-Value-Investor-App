//
//  ETFDetailModels.swift
//  ios
//
//  Data models for the ETF Detail screen
//  Key Statistics and Profile data sourced from Financial Modeling Prep (FMP) API
//

import Foundation
import SwiftUI

// MARK: - ETF Detail Tab
enum ETFDetailTab: String, CaseIterable {
    case overview = "Overview"
    case news = "News"
}

// MARK: - ETF Snapshot Category
enum ETFSnapshotCategory: String, CaseIterable {
    case identityAndRating = "Identity & Rating"
    case netYield = "Net Yield"
    case holdingsAndRisk = "Holdings & Risk"

    var iconName: String {
        switch self {
        case .identityAndRating: return "shield.checkered"
        case .netYield: return "percent"
        case .holdingsAndRisk: return "chart.bar.doc.horizontal.fill"
        }
    }

    var iconColor: Color {
        switch self {
        case .identityAndRating: return AppColors.primaryBlue
        case .netYield: return AppColors.bullish
        case .holdingsAndRisk: return AppColors.neutral
        }
    }
}

// MARK: - ETF Snapshot Item
struct ETFSnapshotItem: Identifiable {
    let id = UUID()
    let category: ETFSnapshotCategory
    let paragraphs: [String]
}

// MARK: - ETF Profile (FMP-based)
struct ETFProfile {
    let description: String
    let symbol: String
    let etfCompany: String
    let assetClass: String
    let expenseRatio: String
    let inceptionDate: String
    let domicile: String
    let indexTracked: String
    let website: String
}

// MARK: - ETF Detail Data
struct ETFDetailData: Identifiable {
    let id = UUID()
    let symbol: String
    let name: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: MarketStatus
    let chartData: [Double]
    let keyStatistics: [KeyStatistic]
    let keyStatisticsGroups: [KeyStatisticsGroup]
    let performancePeriods: [PerformancePeriod]
    let snapshots: [ETFSnapshotItem]
    let etfProfile: ETFProfile
    let relatedETFs: [RelatedTicker]

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

// MARK: - ETF AI Suggestion
struct ETFAISuggestion: Identifiable {
    let id = UUID()
    let text: String

    static let defaultSuggestions: [ETFAISuggestion] = [
        ETFAISuggestion(text: "What does this ETF track?"),
        ETFAISuggestion(text: "Top holdings breakdown"),
        ETFAISuggestion(text: "Expense ratio comparison"),
        ETFAISuggestion(text: "Is this ETF right for me?")
    ]
}

// MARK: - Sample Data

extension ETFDetailData {
    static let sampleSPY = ETFDetailData(
        symbol: "SPY",
        name: "SPDR S&P 500 ETF Trust",
        currentPrice: 528.46,
        priceChange: 3.82,
        priceChangePercent: 0.73,
        marketStatus: .closed(
            date: Calendar.current.date(from: DateComponents(year: 2026, month: 2, day: 14))!,
            time: "4:00 PM",
            timezone: "EST"
        ),
        chartData: [498, 502, 510, 505, 512, 508, 515, 520, 518, 524, 522, 528],
        keyStatistics: ETFKeyStatistic.sampleSPY,
        keyStatisticsGroups: ETFKeyStatisticsGroup.sampleSPY,
        performancePeriods: ETFPerformance.sampleSPY,
        snapshots: ETFSnapshotItem.sampleSPY,
        etfProfile: ETFProfile(
            description: "The SPDR S&P 500 ETF Trust is the oldest and most well-known exchange-traded fund in the world. Launched in 1993 by State Street Global Advisors, SPY tracks the S&P 500 Index, providing broad exposure to 500 of the largest U.S. companies across all major sectors. It is the most liquid ETF on the market, widely used by institutional and retail investors alike for core portfolio allocation, hedging, and tactical trading.",
            symbol: "SPY",
            etfCompany: "State Street Global Advisors",
            assetClass: "Equity",
            expenseRatio: "0.0945%",
            inceptionDate: "January 22, 1993",
            domicile: "United States",
            indexTracked: "S&P 500",
            website: "ssga.com"
        ),
        relatedETFs: ETFRelatedTicker.sampleData
    )
}

// MARK: - ETF Performance Sample Data
enum ETFPerformance {
    static let sampleSPY: [PerformancePeriod] = [
        PerformancePeriod(label: "1 Month", changePercent: 2.14),
        PerformancePeriod(label: "3 Months", changePercent: 5.83),
        PerformancePeriod(label: "6 Months", changePercent: 10.22),
        PerformancePeriod(label: "YTD", changePercent: 4.56),
        PerformancePeriod(label: "1 Year", changePercent: 22.18),
        PerformancePeriod(label: "5 Years", changePercent: 82.47)
    ]
}

// MARK: - ETF Key Statistics Sample Data (FMP-based)
enum ETFKeyStatistic {
    static let sampleSPY: [KeyStatistic] = [
        KeyStatistic(label: "NAV", value: "$528.12"),
        KeyStatistic(label: "Total Assets", value: "$562.3B"),
        KeyStatistic(label: "Expense Ratio", value: "0.0945%", isHighlighted: true),
        KeyStatistic(label: "Avg. Volume", value: "68.4M"),
        KeyStatistic(label: "Dividend Yield", value: "1.22%"),
        KeyStatistic(label: "52W High", value: "$540.72"),
        KeyStatistic(label: "52W Low", value: "$432.18"),
        KeyStatistic(label: "Beta", value: "1.00"),
        KeyStatistic(label: "P/E Ratio", value: "24.86"),
        KeyStatistic(label: "Holdings", value: "503"),
        KeyStatistic(label: "Turnover", value: "2.0%"),
        KeyStatistic(label: "Inception", value: "01/22/1993")
    ]
}

// MARK: - ETF Key Statistics Groups (FMP-based)
enum ETFKeyStatisticsGroup {
    static let sampleSPY: [KeyStatisticsGroup] = [
        // Column 1: Price & NAV
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "NAV", value: "$528.12"),
            KeyStatistic(label: "52W High", value: "$540.72"),
            KeyStatistic(label: "52W Low", value: "$432.18"),
            KeyStatistic(label: "Avg. Volume", value: "68.4M"),
            KeyStatistic(label: "Beta", value: "1.00")
        ]),
        // Column 2: Fund Details
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Total Assets", value: "$562.3B"),
            KeyStatistic(label: "Expense Ratio", value: "0.0945%", isHighlighted: true),
            KeyStatistic(label: "Dividend Yield", value: "1.22%"),
            KeyStatistic(label: "P/E Ratio", value: "24.86"),
            KeyStatistic(label: "Turnover", value: "2.0%")
        ]),
        // Column 3: Structure
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Holdings", value: "503"),
            KeyStatistic(label: "Inception", value: "01/22/1993"),
            KeyStatistic(label: "Asset Class", value: "Equity"),
            KeyStatistic(label: "Domicile", value: "United States"),
            KeyStatistic(label: "Index", value: "S&P 500")
        ])
    ]
}

// MARK: - ETF Snapshot Sample Data
extension ETFSnapshotItem {
    static let sampleSPY: [ETFSnapshotItem] = [
        ETFSnapshotItem(
            category: .identityAndRating,
            paragraphs: []
        ),
        ETFSnapshotItem(
            category: .netYield,
            paragraphs: []
        ),
        ETFSnapshotItem(
            category: .holdingsAndRisk,
            paragraphs: []
        )
    ]
}

// MARK: - Related ETF Sample Data
enum ETFRelatedTicker {
    static let sampleData: [RelatedTicker] = [
        RelatedTicker(symbol: "VOO", name: "Vanguard S&P 500", price: 486.32, changePercent: 0.71),
        RelatedTicker(symbol: "IVV", name: "iShares Core S&P 500", price: 530.18, changePercent: 0.68),
        RelatedTicker(symbol: "QQQ", name: "Invesco QQQ Trust", price: 448.56, changePercent: 1.12),
        RelatedTicker(symbol: "DIA", name: "SPDR Dow Jones", price: 398.42, changePercent: 0.32),
        RelatedTicker(symbol: "IWM", name: "iShares Russell 2000", price: 212.78, changePercent: -0.45),
        RelatedTicker(symbol: "VTI", name: "Vanguard Total Stock", price: 274.93, changePercent: 0.58)
    ]
}
