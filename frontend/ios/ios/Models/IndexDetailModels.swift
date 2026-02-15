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

// MARK: - ──────────────────────────────────────────────
// MARK:   SNAPSHOT MODELS (Hybrid Template System)
// MARK: - ──────────────────────────────────────────────

// MARK: - Valuation Level
enum ValuationLevel: String {
    case bargain = "Bargain"
    case fairValue = "Fair Value"
    case expensive = "Expensive"
    case overheated = "Overheated"

    var color: Color {
        switch self {
        case .bargain: return AppColors.bullish
        case .fairValue: return Color(hex: "FBBF24")   // Yellow
        case .expensive: return AppColors.alertOrange
        case .overheated: return AppColors.bearish
        }
    }

    var bgColor: Color {
        color.opacity(0.15)
    }

    var iconName: String {
        switch self {
        case .bargain: return "tag.fill"
        case .fairValue: return "equal.circle.fill"
        case .expensive: return "exclamationmark.triangle.fill"
        case .overheated: return "flame.fill"
        }
    }

    static func from(pe: Double) -> ValuationLevel {
        switch pe {
        case ..<18: return .bargain
        case 18..<24: return .fairValue
        case 24..<30: return .expensive
        default: return .overheated
        }
    }

    /// Position on the gauge (0.0 – 1.0)
    static func gaugePosition(pe: Double) -> Double {
        // Clamp PE between 10 and 40 for visual range
        let clamped = min(max(pe, 10), 40)
        return (clamped - 10) / 30.0
    }
}

// MARK: - Valuation Tier (for the visual tier bar)
struct ValuationTier: Identifiable {
    let id = UUID()
    let level: ValuationLevel
    let rangeLabel: String
    let isActive: Bool
}

// MARK: - Index Valuation Snapshot
struct IndexValuationSnapshot {
    let peRatio: Double
    let forwardPE: Double
    let earningsYield: Double
    let historicalAvgPE: Double
    let historicalPeriod: String
    let storyTemplate: String

    var level: ValuationLevel {
        ValuationLevel.from(pe: peRatio)
    }

    var gaugePosition: Double {
        ValuationLevel.gaugePosition(pe: peRatio)
    }

    var tiers: [ValuationTier] {
        let current = level
        return [
            ValuationTier(level: .bargain, rangeLabel: "< 18x", isActive: current == .bargain),
            ValuationTier(level: .fairValue, rangeLabel: "18–24x", isActive: current == .fairValue),
            ValuationTier(level: .expensive, rangeLabel: "24–30x", isActive: current == .expensive),
            ValuationTier(level: .overheated, rangeLabel: "> 30x", isActive: current == .overheated)
        ]
    }

    /// Resolves the template by replacing placeholders with live data
    var resolvedStory: String {
        storyTemplate
            .replacingOccurrences(of: "{PE_RATIO}", with: String(format: "%.1fx", peRatio))
            .replacingOccurrences(of: "{FORWARD_PE}", with: String(format: "%.1fx", forwardPE))
            .replacingOccurrences(of: "{EARNINGS_YIELD}", with: String(format: "%.2f%%", earningsYield))
            .replacingOccurrences(of: "{VALUATION_LABEL}", with: level.rawValue)
            .replacingOccurrences(of: "{HISTORICAL_AVG_PE}", with: String(format: "%.0fx", historicalAvgPE))
            .replacingOccurrences(of: "{HISTORICAL_PERIOD}", with: historicalPeriod)
    }
}

// MARK: - Sector Performance Entry
struct SectorPerformanceEntry: Identifiable {
    let id = UUID()
    let sector: String
    let changePercent: Double

    var isPositive: Bool {
        changePercent >= 0
    }

    var color: Color {
        if changePercent > 1.0 { return AppColors.bullish }
        if changePercent > 0 { return AppColors.bullish.opacity(0.7) }
        if changePercent > -1.0 { return AppColors.bearish.opacity(0.7) }
        return AppColors.bearish
    }

    var bgColor: Color {
        color.opacity(0.15)
    }

    var formattedChange: String {
        let sign = changePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", changePercent))%"
    }
}

// MARK: - Index Sector Performance Snapshot
struct IndexSectorPerformanceSnapshot {
    let sectors: [SectorPerformanceEntry]
    let storyTemplate: String

    var topSector: SectorPerformanceEntry? {
        sectors.max(by: { $0.changePercent < $1.changePercent })
    }

    var bottomSector: SectorPerformanceEntry? {
        sectors.min(by: { $0.changePercent < $1.changePercent })
    }

    var advancingSectors: Int {
        sectors.filter { $0.isPositive }.count
    }

    var decliningSectors: Int {
        sectors.filter { !$0.isPositive }.count
    }

    /// Resolves the template by replacing placeholders with live data
    var resolvedStory: String {
        storyTemplate
            .replacingOccurrences(of: "{TOP_SECTOR}", with: topSector?.sector ?? "N/A")
            .replacingOccurrences(of: "{TOP_SECTOR_CHANGE}", with: topSector?.formattedChange ?? "N/A")
            .replacingOccurrences(of: "{BOTTOM_SECTOR}", with: bottomSector?.sector ?? "N/A")
            .replacingOccurrences(of: "{BOTTOM_SECTOR_CHANGE}", with: bottomSector?.formattedChange ?? "N/A")
            .replacingOccurrences(of: "{ADVANCING_COUNT}", with: "\(advancingSectors)")
            .replacingOccurrences(of: "{DECLINING_COUNT}", with: "\(decliningSectors)")
    }
}

// MARK: - Risk Severity
enum RiskSeverity: String {
    case elevated = "Elevated"
    case high = "High"
    case critical = "Critical"

    var color: Color {
        switch self {
        case .elevated: return AppColors.alertOrange
        case .high: return Color(hex: "EF4444")
        case .critical: return AppColors.bearish
        }
    }

    var bgColor: Color {
        color.opacity(0.15)
    }

    var iconName: String {
        switch self {
        case .elevated: return "exclamationmark.circle.fill"
        case .high: return "exclamationmark.triangle.fill"
        case .critical: return "xmark.octagon.fill"
        }
    }
}

// MARK: - Systemic Risk Item
struct SystemicRiskItem: Identifiable {
    let id = UUID()
    let title: String
    let description: String
    let severity: RiskSeverity
}

// MARK: - Index Systemic Risk Snapshot
struct IndexSystemicRiskSnapshot {
    let risks: [SystemicRiskItem]
    let storyTemplate: String

    /// Resolves the template by replacing placeholders with live data
    var resolvedStory: String {
        let topRisk = risks.first
        return storyTemplate
            .replacingOccurrences(of: "{TOP_RISK}", with: topRisk?.title ?? "N/A")
            .replacingOccurrences(of: "{TOP_RISK_SEVERITY}", with: topRisk?.severity.rawValue ?? "N/A")
            .replacingOccurrences(of: "{RISK_COUNT}", with: "\(risks.count)")
    }
}

// MARK: - Index Snapshots Data (Combined)
struct IndexSnapshotsData {
    let valuation: IndexValuationSnapshot
    let sectorPerformance: IndexSectorPerformanceSnapshot
    let systemicRisk: IndexSystemicRiskSnapshot
    let generatedDate: Date
    let generatedBy: String  // e.g. "Gemini 2.0 Flash"

    var formattedGeneratedDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d, yyyy"
        return formatter.string(from: generatedDate)
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

// MARK: - ──────────────────────────────────────────────
// MARK:   INDEX DETAIL DATA
// MARK: - ──────────────────────────────────────────────

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
    let snapshotsData: IndexSnapshotsData
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

// MARK: - ──────────────────────────────────────────────
// MARK:   SAMPLE DATA
// MARK: - ──────────────────────────────────────────────

extension IndexValuationSnapshot {
    static let sampleData = IndexValuationSnapshot(
        peRatio: 28.5,
        forwardPE: 22.1,
        earningsYield: 3.51,
        historicalAvgPE: 21,
        historicalPeriod: "10-year",
        storyTemplate: "The S&P 500 is trading at {PE_RATIO} earnings, which is considered {VALUATION_LABEL}. That's a hefty premium to the {HISTORICAL_PERIOD} average of {HISTORICAL_AVG_PE} — investors are clearly pricing in strong future growth. The forward P/E of {FORWARD_PE} tells a slightly better story, suggesting analysts expect earnings to catch up."
    )
}

extension IndexSectorPerformanceSnapshot {
    static let sampleData = IndexSectorPerformanceSnapshot(
        sectors: [
            SectorPerformanceEntry(sector: "Technology", changePercent: 2.1),
            SectorPerformanceEntry(sector: "Communication", changePercent: 1.5),
            SectorPerformanceEntry(sector: "Healthcare", changePercent: 1.2),
            SectorPerformanceEntry(sector: "Consumer Disc.", changePercent: 0.8),
            SectorPerformanceEntry(sector: "Financials", changePercent: 0.6),
            SectorPerformanceEntry(sector: "Industrials", changePercent: 0.3),
            SectorPerformanceEntry(sector: "Materials", changePercent: 0.1),
            SectorPerformanceEntry(sector: "Utilities", changePercent: -0.2),
            SectorPerformanceEntry(sector: "Real Estate", changePercent: -0.4),
            SectorPerformanceEntry(sector: "Consumer Stpl.", changePercent: -0.5),
            SectorPerformanceEntry(sector: "Energy", changePercent: -0.8)
        ],
        storyTemplate: "The rally is narrow — {TOP_SECTOR} ({TOP_SECTOR_CHANGE}) and its mega-cap names are doing the heavy lifting, while defensive plays like {BOTTOM_SECTOR} ({BOTTOM_SECTOR_CHANGE}) are being left behind. {ADVANCING_COUNT} of 11 sectors are green, but the breadth isn't convincing. Watch for rotation if the leaders stumble."
    )
}

extension IndexSystemicRiskSnapshot {
    static let sampleData = IndexSystemicRiskSnapshot(
        risks: [
            SystemicRiskItem(
                title: "Sticky Inflation",
                description: "Core CPI remains above the Fed's 2% target. A resurgence could force the Fed to hold rates higher for longer, squeezing valuations and slowing consumer spending.",
                severity: .high
            ),
            SystemicRiskItem(
                title: "Concentration Risk",
                description: "The top 10 stocks now represent ~35% of the index. A selloff in mega-cap tech could drag the entire market — the \"Magnificent 7\" effect cuts both ways.",
                severity: .elevated
            ),
            SystemicRiskItem(
                title: "Geopolitical Tensions",
                description: "Ongoing conflicts and trade policy uncertainty could disrupt supply chains and trigger risk-off sentiment in global markets.",
                severity: .elevated
            )
        ],
        storyTemplate: "The biggest wildcard right now is {TOP_RISK}. We're tracking {RISK_COUNT} key risks that could derail the current rally. None are flashing red yet, but the market is priced for perfection — any negative surprise gets amplified."
    )
}

extension IndexSnapshotsData {
    static let sampleData = IndexSnapshotsData(
        valuation: IndexValuationSnapshot.sampleData,
        sectorPerformance: IndexSectorPerformanceSnapshot.sampleData,
        systemicRisk: IndexSystemicRiskSnapshot.sampleData,
        generatedDate: Calendar.current.date(from: DateComponents(year: 2026, month: 2, day: 10))!,
        generatedBy: "Gemini 2.0 Flash"
    )
}

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
        snapshotsData: IndexSnapshotsData.sampleData,
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
