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
    case strategy = "Strategy"
    case netYield = "Net Yield"
    case holdingsAndRisk = "Holdings & Risk"

    var iconName: String {
        switch self {
        case .identityAndRating: return "shield.checkered"
        case .strategy: return "scope"
        case .netYield: return "percent"
        case .holdingsAndRisk: return "chart.bar.doc.horizontal.fill"
        }
    }

    var iconColor: Color {
        switch self {
        case .identityAndRating: return AppColors.primaryBlue
        case .strategy: return AppColors.accentCyan
        case .netYield: return AppColors.bullish
        case .holdingsAndRisk: return AppColors.neutral
        }
    }
}

// MARK: - ETF Identity & Rating
struct ETFIdentityRating {
    let score: Int
    let maxScore: Int
    let esgRating: String
    let volatilityLabel: String
}

// MARK: - ETF Strategy
struct ETFStrategy {
    let hook: String
    let tags: [String]
}

// MARK: - ETF Dividend Payment
struct ETFDividendPayment: Identifiable {
    let id = UUID()
    let dividendPerShare: String
    let exDividendDate: String
    let payDate: String
}

// MARK: - ETF Net Yield
struct ETFNetYield {
    let expenseRatio: Double
    let feeContext: String
    let dividendYield: Double
    let payFrequency: String
    let yieldContext: String
    let verdict: String
    let lastDividendPayment: ETFDividendPayment

    var formattedExpenseRatio: String {
        "\(String(format: "%g", expenseRatio))%"
    }

    var formattedDividendYield: String {
        String(format: "%.2f%%", dividendYield)
    }
}

// MARK: - ETF Asset Allocation
struct ETFAssetAllocation {
    let equities: Double
    let bonds: Double
    let crypto: Double
    let cash: Double
    let totalAssets: String
}

// MARK: - ETF Sector Weight
struct ETFSectorWeight: Identifiable {
    let id = UUID()
    let name: String
    let weight: Double

    var formattedWeight: String {
        String(format: "%.1f%%", weight)
    }
}

// MARK: - ETF Top Holding
struct ETFTopHolding: Identifiable {
    let id = UUID()
    let symbol: String
    let name: String
    let weight: Double

    var formattedWeight: String {
        String(format: "%.1f%%", weight)
    }
}

// MARK: - ETF Concentration Level
enum ETFConcentrationLevel {
    case low
    case moderate
    case high

    var color: Color {
        switch self {
        case .low: return AppColors.bullish
        case .moderate: return AppColors.neutral
        case .high: return AppColors.bearish
        }
    }

    var label: String {
        switch self {
        case .low: return "Well Diversified"
        case .moderate: return "Moderate"
        case .high: return "Concentrated"
        }
    }
}

// MARK: - ETF Concentration
struct ETFConcentration {
    let topN: Int
    let weight: Double
    let insight: String

    var formattedWeight: String {
        String(format: "%.0f%%", weight)
    }

    var level: ETFConcentrationLevel {
        switch weight {
        case ..<20: return .low
        case 20..<35: return .moderate
        default: return .high
        }
    }
}

// MARK: - ETF Holdings & Risk
struct ETFHoldingsRisk {
    let assetAllocation: ETFAssetAllocation
    let topSectors: [ETFSectorWeight]
    let topHoldings: [ETFTopHolding]
    let concentration: ETFConcentration
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
    let identityRating: ETFIdentityRating
    let strategy: ETFStrategy
    let netYield: ETFNetYield
    let holdingsRisk: ETFHoldingsRisk
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

    var formattedChangePill: String {
        let sign = priceChangePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", priceChangePercent))%"
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
        identityRating: ETFIdentityRating(
            score: 5,
            maxScore: 5,
            esgRating: "A",
            volatilityLabel: "Low Volatility"
        ),
        strategy: ETFStrategy(
            hook: "Tracks the 500 largest U.S. companies. A single bet on the American economy.",
            tags: ["Passive", "Large Cap Blend", "Index"]
        ),
        netYield: ETFNetYield(
            expenseRatio: 0.0945,
            feeContext: "You pay $9.45 per year on a $10,000 investment.",
            dividendYield: 1.22,
            payFrequency: "Quarterly",
            yieldContext: "You earn ~$122 per year on a $10,000 investment.",
            verdict: "This fund pays you 13x more in dividends than it charges in fees.",
            lastDividendPayment: ETFDividendPayment(
                dividendPerShare: "$1.7742",
                exDividendDate: "Dec 20, 2025",
                payDate: "Jan 31, 2026"
            )
        ),
        holdingsRisk: ETFHoldingsRisk(
            assetAllocation: ETFAssetAllocation(
                equities: 99.5,
                bonds: 0.0,
                crypto: 0.0,
                cash: 0.5,
                totalAssets: "$562.3B"
            ),
            topSectors: [
                ETFSectorWeight(name: "Technology", weight: 31.7),
                ETFSectorWeight(name: "Financials", weight: 13.5),
                ETFSectorWeight(name: "Healthcare", weight: 12.2),
                ETFSectorWeight(name: "Consumer Disc.", weight: 10.1),
                ETFSectorWeight(name: "Communication", weight: 9.2)
            ],
            topHoldings: [
                ETFTopHolding(symbol: "MSFT", name: "Microsoft", weight: 7.2),
                ETFTopHolding(symbol: "AAPL", name: "Apple", weight: 6.8),
                ETFTopHolding(symbol: "NVDA", name: "NVIDIA", weight: 6.1),
                ETFTopHolding(symbol: "AMZN", name: "Amazon", weight: 3.7),
                ETFTopHolding(symbol: "META", name: "Meta", weight: 2.5),
                ETFTopHolding(symbol: "GOOGL", name: "Alphabet A", weight: 2.1),
                ETFTopHolding(symbol: "GOOG", name: "Alphabet C", weight: 1.8),
                ETFTopHolding(symbol: "BRK.B", name: "Berkshire", weight: 1.7),
                ETFTopHolding(symbol: "AVGO", name: "Broadcom", weight: 1.7),
                ETFTopHolding(symbol: "JPM", name: "JPMorgan", weight: 1.4)
            ],
            concentration: ETFConcentration(
                topN: 10,
                weight: 35,
                insight: "Over a third of your money is in just 10 companies. If Big Tech stumbles, this fund feels it."
            )
        ),
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

// MARK: - Gemini System Prompt for Weekly Snapshot Generation

enum ETFSnapshotPrompts {
    /// System prompt for Gemini AI agent to generate weekly ETF Snapshot content.
    /// Feed this as the system instruction along with FMP API data for the target ETF.
    static let geminiSystemPrompt: String = """
    You are an ETF Analyst AI writing for novice investors who may have never bought a stock before.
    Your job is to generate weekly ETF Snapshot reports. Given an ETF symbol and its data from Financial
    Modeling Prep (FMP), produce the following structured JSON.

    VOICE & TONE:
    - Write like you are explaining to a smart friend over coffee. Zero jargon without explanation.
    - Be honest about risks. Never sugarcoat.
    - Use concrete dollar amounts based on a $10,000 investment to make costs and returns tangible.
    - Keep insights quotable and memorable — one punchy sentence that sticks.

    OUTPUT SCHEMA:
    {
      "symbol": "SPY",
      "generatedDate": "2026-02-16",

      "identityRating": {
        "score": <int 1-5>,           // Overall fund quality: AUM, tracking error, liquidity, longevity
        "maxScore": 5,
        "esgRating": "<A-F>",         // ESG letter grade based on underlying holdings
        "volatilityLabel": "<string>" // "Low Volatility" | "Moderate Volatility" | "High Volatility"
      },

      "strategy": {
        "hook": "<string max 120 chars>",  // One punchy sentence: what this fund does in plain English
        "tags": ["<string>", ...]          // 2-4 from: Passive, Active, Index, Large Cap, Mid Cap,
                                           // Small Cap, Blend, Growth, Value, Sector, Thematic,
                                           // Bond, International, Dividend, ESG
      },

      "netYield": {
        "expenseRatio": <double>,          // e.g. 0.03 means 0.03%
        "feeContext": "<string>",          // "You pay $X per year on a $10,000 investment."
        "dividendYield": <double>,         // e.g. 1.42 means 1.42%
        "payFrequency": "<string>",        // Monthly | Quarterly | Semi-Annually | Annually
        "yieldContext": "<string>",        // "You earn ~$X per year on a $10,000 investment."
        "verdict": "<string>",            // "This fund pays you Nx more in dividends than it charges
                                          //  in fees." (N = dividendYield / expenseRatio, rounded)
        "lastDividendPayment": {
          "dividendPerShare": "<string>",  // e.g. "$1.7742"
          "exDividendDate": "<string>",    // e.g. "Dec 20, 2025"
          "payDate": "<string>"            // e.g. "Jan 31, 2026"
        }
      },

      "holdingsRisk": {
        "assetAllocation": {
          "equities": <double>,    // percentage, all four should sum to ~100
          "bonds": <double>,
          "crypto": <double>,
          "cash": <double>,
          "totalAssets": "<string>" // e.g. "$562.3B"
        },
        "topSectors": [                    // Top 5, sorted largest → smallest
          { "name": "<string>", "weight": <double> }
        ],
        "topHoldings": [                   // Top 10, sorted largest → smallest
          { "symbol": "<string>", "name": "<string>", "weight": <double> }
        ],
        "concentration": {
          "topN": 10,
          "weight": <double>,              // Sum of top 10 weights
          "insight": "<string>"            // If >30% warn about concentration. If <20% praise
                                           // diversification. One punchy memorable sentence.
        }
      }
    }

    RULES:
    1. score: 5 = institutional-grade blue chip ETF, 4 = strong, 3 = average, 2 = niche/risky, 1 = speculative
    2. For the hook, never exceed 120 characters. Think tweet-sized.
    3. Fee context and yield context MUST use $10,000 as the base investment amount.
    4. The verdict multiplier = dividendYield / expenseRatio, rounded to nearest integer.
       If expenseRatio is 0, say "This fund charges nothing in fees."
    5. Concentration insight: be direct. Use "your money" language to make it personal.
    6. Output ONLY valid JSON. No markdown, no commentary.
    """
}
