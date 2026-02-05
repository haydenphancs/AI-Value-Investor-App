//
//  WhaleProfileModels.swift
//  ios
//
//  Data models for the Whale Profile screen
//

import Foundation
import SwiftUI

// MARK: - Whale Profile
struct WhaleProfile: Identifiable, Codable {
    let id: String
    let name: String
    let title: String
    let avatarURL: String?
    let riskProfile: WhaleRiskProfile
    let portfolioValue: Double
    let ytdReturn: Double
    let sectorExposure: [WhaleSectorAllocation]
    let currentHoldings: [WhaleHolding]
    let recentTradeGroups: [WhaleTradeGroup]
    let recentTrades: [WhaleTrade]
    let behaviorSummary: WhaleBehaviorSummary
    let sentimentSummary: String
    var isFollowing: Bool

    // MARK: - Formatted Properties

    var formattedPortfolioValue: String {
        formatLargeNumber(portfolioValue)
    }

    var formattedYTDReturn: String {
        let sign = ytdReturn >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", ytdReturn))%"
    }

    var isPositiveReturn: Bool {
        ytdReturn >= 0
    }

    // MARK: - Helpers

    private func formatLargeNumber(_ number: Double) -> String {
        let absNumber = abs(number)
        if absNumber >= 1_000_000_000_000 {
            return String(format: "$%.1fT", number / 1_000_000_000_000)
        } else if absNumber >= 1_000_000_000 {
            return String(format: "$%.1fB", number / 1_000_000_000)
        } else if absNumber >= 1_000_000 {
            return String(format: "$%.1fM", number / 1_000_000)
        } else if absNumber >= 1_000 {
            return String(format: "$%.1fK", number / 1_000)
        }
        return String(format: "$%.0f", number)
    }
}

// MARK: - Whale Risk Profile
enum WhaleRiskProfile: String, Codable {
    case safeLongTermValue = "Safe, Long-term Value"
    case growthFocused = "Growth Focused"
    case aggressive = "Aggressive"
    case moderate = "Moderate"
    case conservative = "Conservative"
    case highRisk = "High Risk"

    var color: Color {
        switch self {
        case .safeLongTermValue, .conservative:
            return AppColors.accentCyan
        case .moderate:
            return AppColors.primaryBlue
        case .growthFocused:
            return AppColors.bullish
        case .aggressive, .highRisk:
            return AppColors.bearish
        }
    }

    var iconName: String {
        switch self {
        case .safeLongTermValue, .conservative:
            return "shield.fill"
        case .moderate:
            return "chart.line.uptrend.xyaxis"
        case .growthFocused:
            return "arrow.up.right"
        case .aggressive, .highRisk:
            return "bolt.fill"
        }
    }
}

// MARK: - Whale Sector Allocation
struct WhaleSectorAllocation: Identifiable, Codable {
    let id: String
    let name: String
    let percentage: Double
    let colorHex: String

    var color: Color {
        Color(hex: colorHex)
    }

    var formattedPercentage: String {
        "\(Int(percentage))%"
    }

    init(id: String = UUID().uuidString, name: String, percentage: Double, colorHex: String) {
        self.id = id
        self.name = name
        self.percentage = percentage
        self.colorHex = colorHex
    }
}

// MARK: - Whale Holding
struct WhaleHolding: Identifiable, Codable {
    let id: String
    let ticker: String
    let companyName: String
    let logoURL: String?
    let allocation: Double
    let changePercent: Double

    var formattedAllocation: String {
        String(format: "%.1f%%", allocation)
    }

    var formattedChange: String {
        let sign = changePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", changePercent))%"
    }

    var isPositive: Bool {
        changePercent >= 0
    }

    init(id: String = UUID().uuidString, ticker: String, companyName: String, logoURL: String? = nil, allocation: Double, changePercent: Double) {
        self.id = id
        self.ticker = ticker
        self.companyName = companyName
        self.logoURL = logoURL
        self.allocation = allocation
        self.changePercent = changePercent
    }
}

// MARK: - Whale Trade Group
/// A batch of trades filed on a specific date, displayed as a single card
struct WhaleTradeGroup: Identifiable, Codable {
    let id: String
    let date: Date
    let tradeCount: Int
    let netAction: WhaleTradeAction
    let netAmount: Double
    let summary: String?
    let insights: [String]
    let trades: [WhaleTrade]

    /// Formatted date for the detail header (e.g. "Feb 05, 2026")
    var formattedDateFull: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM dd, yyyy"
        return formatter.string(from: date)
    }

    /// Formatted date — shows relative for recent, or "MMM dd, yyyy" otherwise
    var formattedDate: String {
        let calendar = Calendar.current
        let now = Date()
        let daysAgo = calendar.dateComponents([.day], from: date, to: now).day ?? 0

        if daysAgo == 0 {
            return "Today"
        } else if daysAgo == 1 {
            return "Yesterday"
        } else if daysAgo <= 14 {
            return "\(daysAgo) days ago"
        } else {
            return formattedDateFull
        }
    }

    var formattedTradeCount: String {
        "\(tradeCount) \(tradeCount == 1 ? "trade" : "trades")"
    }

    var formattedNetAmount: String {
        let prefix = netAction == .bought ? "+" : "- "
        return "\(prefix)\(formatAmount(netAmount)) \(netAction.rawValue)"
    }

    private func formatAmount(_ amount: Double) -> String {
        let absAmount = abs(amount)
        if absAmount >= 1_000_000_000 {
            return String(format: "$%.2fB", absAmount / 1_000_000_000)
        } else if absAmount >= 1_000_000 {
            return String(format: "$%.1fM", absAmount / 1_000_000)
        } else if absAmount >= 1_000 {
            return String(format: "$%.0fK", absAmount / 1_000)
        }
        return String(format: "$%.0f", absAmount)
    }

    init(
        id: String = UUID().uuidString,
        date: Date,
        tradeCount: Int,
        netAction: WhaleTradeAction,
        netAmount: Double,
        summary: String? = nil,
        insights: [String] = [],
        trades: [WhaleTrade] = []
    ) {
        self.id = id
        self.date = date
        self.tradeCount = tradeCount
        self.netAction = netAction
        self.netAmount = netAmount
        self.summary = summary
        self.insights = insights
        self.trades = trades
    }
}

// MARK: - Trade Type (filter category)
enum WhaleTradeType: String, CaseIterable, Codable {
    case new = "New"
    case increased = "Increased"
    case decreased = "Decreased"
    case closed = "Closed"

    var iconName: String {
        switch self {
        case .new: return "plus.circle.fill"
        case .increased: return "plus"
        case .decreased: return "minus"
        case .closed: return "minus.circle.fill"
        }
    }

    var iconColor: Color {
        switch self {
        case .new: return AppColors.bullish
        case .increased: return AppColors.bullish
        case .decreased: return AppColors.bearish
        case .closed: return AppColors.bearish
        }
    }
}

// MARK: - Trade Filter Tab
enum TradeFilterTab: String, CaseIterable {
    case all = "All Trades"
    case new = "New"
    case increased = "Increased"
    case decreased = "Decreased"
    case closed = "Closed"

    var iconName: String? {
        switch self {
        case .all: return nil
        case .new: return "plus.circle.fill"
        case .increased: return "plus"
        case .decreased: return "minus"
        case .closed: return "minus.circle.fill"
        }
    }

    var iconColor: Color {
        switch self {
        case .all: return .clear
        case .new: return AppColors.bullish
        case .increased: return AppColors.bullish
        case .decreased: return AppColors.bearish
        case .closed: return AppColors.bearish
        }
    }
}

// MARK: - Whale Trade
/// Individual trade — used inside a trade group detail screen
struct WhaleTrade: Identifiable, Codable {
    let id: String
    let ticker: String
    let companyName: String
    let action: WhaleTradeAction
    let tradeType: WhaleTradeType
    let amount: Double
    let previousAllocation: Double
    let newAllocation: Double
    let date: Date

    var formattedAmount: String {
        formatTradeAmount(amount)
    }

    var formattedAllocationChange: String {
        let prev = previousAllocation == 0 ? "0%" : String(format: "%.1f%%", previousAllocation)
        let new = newAllocation == 0 ? "0%" : String(format: "%.1f %%", newAllocation)
        return "\(prev)  \u{2192}  \(new)"
    }

    private func formatTradeAmount(_ amount: Double) -> String {
        let absAmount = abs(amount)
        if absAmount >= 1_000_000_000 {
            return String(format: "$%.2fB", absAmount / 1_000_000_000)
        } else if absAmount >= 1_000_000 {
            return String(format: "$%.2fM", absAmount / 1_000_000)
        } else if absAmount >= 1_000 {
            return String(format: "$%.0fK", absAmount / 1_000)
        }
        return String(format: "$%.0f", absAmount)
    }

    init(
        id: String = UUID().uuidString,
        ticker: String,
        companyName: String,
        action: WhaleTradeAction,
        tradeType: WhaleTradeType = .increased,
        amount: Double,
        previousAllocation: Double = 0,
        newAllocation: Double = 0,
        date: Date = Date()
    ) {
        self.id = id
        self.ticker = ticker
        self.companyName = companyName
        self.action = action
        self.tradeType = tradeType
        self.amount = amount
        self.previousAllocation = previousAllocation
        self.newAllocation = newAllocation
        self.date = date
    }
}

// MARK: - Whale Trade Action
enum WhaleTradeAction: String, Codable {
    case bought = "BOUGHT"
    case sold = "SOLD"

    var color: Color {
        switch self {
        case .bought:
            return AppColors.bullish
        case .sold:
            return AppColors.bearish
        }
    }
}

// MARK: - Whale Behavior Summary
struct WhaleBehaviorSummary: Codable {
    let action: String
    let primaryFocus: String
    let secondaryAction: String
    let secondaryFocus: String

    var formattedSummary: AttributedString {
        var result = AttributedString("This whale is currently ")

        var actionPart = AttributedString(action)
        actionPart.foregroundColor = AppColors.bullish
        result.append(actionPart)

        result.append(AttributedString(" \(primaryFocus) and "))

        var secondaryPart = AttributedString(secondaryAction)
        secondaryPart.foregroundColor = AppColors.textPrimary
        result.append(secondaryPart)

        result.append(AttributedString(" \(secondaryFocus)"))

        return result
    }
}

// MARK: - Date Helper
private func dateFrom(month: Int, day: Int, year: Int) -> Date {
    var components = DateComponents()
    components.year = year
    components.month = month
    components.day = day
    return Calendar.current.date(from: components) ?? Date()
}

// MARK: - Sample Data
extension WhaleProfile {
    static let warrenBuffett = WhaleProfile(
        id: "warren-buffett",
        name: "Warren Buffett",
        title: "Berkshire Hathaway CEO",
        avatarURL: nil,
        riskProfile: .safeLongTermValue,
        portfolioValue: 342_800_000_000,
        ytdReturn: 19.4,
        sectorExposure: [
            WhaleSectorAllocation(name: "Tech", percentage: 42, colorHex: "3B82F6"),
            WhaleSectorAllocation(name: "Finance", percentage: 31, colorHex: "22C55E"),
            WhaleSectorAllocation(name: "Energy", percentage: 17, colorHex: "F97316"),
            WhaleSectorAllocation(name: "Other", percentage: 10, colorHex: "6B7280")
        ],
        currentHoldings: [
            WhaleHolding(ticker: "AAPL", companyName: "Apple Inc.", allocation: 47.8, changePercent: 1.8),
            WhaleHolding(ticker: "BAC", companyName: "Bank of America", allocation: 11.2, changePercent: -0.5),
            WhaleHolding(ticker: "AXP", companyName: "American Express", allocation: 9.4, changePercent: 0.0),
            WhaleHolding(ticker: "KO", companyName: "Coca-Cola", allocation: 8.1, changePercent: 3.2),
            WhaleHolding(ticker: "CVX", companyName: "Chevron", allocation: 6.7, changePercent: 3.2),
            WhaleHolding(ticker: "OXY", companyName: "Occidental Petroleum", allocation: 4.9, changePercent: 2.2),
            WhaleHolding(ticker: "KHC", companyName: "Kraft Heinz", allocation: 3.8, changePercent: 0.0),
            WhaleHolding(ticker: "MCO", companyName: "Moody's Corp", allocation: 2.6, changePercent: 0.0),
            WhaleHolding(ticker: "USB", companyName: "U.S. Bancorp", allocation: 2.1, changePercent: 0.2),
            WhaleHolding(ticker: "BK", companyName: "Bank of NY Mellon", allocation: 1.8, changePercent: -0.5)
        ],
        recentTradeGroups: [
            WhaleTradeGroup(
                date: Calendar.current.date(byAdding: .day, value: -7, to: Date())!,
                tradeCount: 7,
                netAction: .bought,
                netAmount: 4_340_000_000,
                summary: "Significant rebalancing (trimmed 3 Tech positions)",
                insights: [
                    "Net accumulating over last filed +$2.2B new positions",
                    "Significant rebalancing (trimmed 3 Tech positions)"
                ],
                trades: [
                    WhaleTrade(ticker: "XYZ", companyName: "XYZ Corp", action: .bought, tradeType: .new, amount: 3_140_000_000, previousAllocation: 0, newAllocation: 1.5),
                    WhaleTrade(ticker: "AAPL", companyName: "Apple Inc.", action: .bought, tradeType: .increased, amount: 2_300_000_000, previousAllocation: 1.0, newAllocation: 2.5),
                    WhaleTrade(ticker: "KO", companyName: "Coca-Cola", action: .bought, tradeType: .increased, amount: 223_640_000, previousAllocation: 1.5, newAllocation: 2.5),
                    WhaleTrade(ticker: "CVX", companyName: "Chevron", action: .bought, tradeType: .increased, amount: 22_640_000, previousAllocation: 1.3, newAllocation: 1.5),
                    WhaleTrade(ticker: "BAC", companyName: "Bank of America", action: .sold, tradeType: .decreased, amount: 59_100_000, previousAllocation: 1.3, newAllocation: 0.5),
                    WhaleTrade(ticker: "BK", companyName: "Bank of NY Mellon", action: .sold, tradeType: .decreased, amount: 24_100_000, previousAllocation: 2.1, newAllocation: 1.8),
                    WhaleTrade(ticker: "ROKU", companyName: "Roku Inc.", action: .sold, tradeType: .closed, amount: 1_130_000_000, previousAllocation: 1.3, newAllocation: 0)
                ]
            ),
            WhaleTradeGroup(
                date: dateFrom(month: 1, day: 4, year: 2025),
                tradeCount: 12,
                netAction: .bought,
                netAmount: 2_200_000_000,
                insights: ["Broad accumulation across energy and finance sectors"],
                trades: [
                    WhaleTrade(ticker: "OXY", companyName: "Occidental Petroleum", action: .bought, tradeType: .increased, amount: 800_000_000, previousAllocation: 3.2, newAllocation: 4.9),
                    WhaleTrade(ticker: "CVX", companyName: "Chevron", action: .bought, tradeType: .increased, amount: 600_000_000, previousAllocation: 5.1, newAllocation: 6.7),
                    WhaleTrade(ticker: "AAPL", companyName: "Apple Inc.", action: .bought, tradeType: .increased, amount: 500_000_000, previousAllocation: 46.8, newAllocation: 47.8)
                ]
            ),
            WhaleTradeGroup(
                date: dateFrom(month: 12, day: 23, year: 2025),
                tradeCount: 9,
                netAction: .sold,
                netAmount: 4_500_000_000,
                summary: "Significant sold with 3 closed positions in Banking sector",
                insights: [
                    "Significant sold with 3 closed positions in Banking sector",
                    "Reduced exposure to regional banks"
                ],
                trades: [
                    WhaleTrade(ticker: "USB", companyName: "U.S. Bancorp", action: .sold, tradeType: .decreased, amount: 1_200_000_000, previousAllocation: 3.5, newAllocation: 2.1),
                    WhaleTrade(ticker: "BAC", companyName: "Bank of America", action: .sold, tradeType: .decreased, amount: 900_000_000, previousAllocation: 12.1, newAllocation: 11.2)
                ]
            ),
            WhaleTradeGroup(
                date: dateFrom(month: 10, day: 4, year: 2025),
                tradeCount: 9,
                netAction: .sold,
                netAmount: 2_870_000_000,
                trades: [
                    WhaleTrade(ticker: "KHC", companyName: "Kraft Heinz", action: .sold, tradeType: .decreased, amount: 870_000_000, previousAllocation: 4.5, newAllocation: 3.8),
                    WhaleTrade(ticker: "MCO", companyName: "Moody's Corp", action: .sold, tradeType: .decreased, amount: 500_000_000, previousAllocation: 3.2, newAllocation: 2.6)
                ]
            ),
            WhaleTradeGroup(
                date: dateFrom(month: 8, day: 29, year: 2025),
                tradeCount: 11,
                netAction: .bought,
                netAmount: 1_450_000_000,
                trades: [
                    WhaleTrade(ticker: "AAPL", companyName: "Apple Inc.", action: .bought, tradeType: .increased, amount: 650_000_000, previousAllocation: 45.2, newAllocation: 46.8),
                    WhaleTrade(ticker: "AXP", companyName: "American Express", action: .bought, tradeType: .increased, amount: 400_000_000, previousAllocation: 8.8, newAllocation: 9.4)
                ]
            )
        ],
        recentTrades: [],
        behaviorSummary: WhaleBehaviorSummary(
            action: "Accumulating",
            primaryFocus: "energy stocks",
            secondaryAction: "Holding",
            secondaryFocus: "core tech positions"
        ),
        sentimentSummary: "Warren Buffett maintains a conservative, value-driven approach with significant exposure to financial services and technology blue chips. Recent activity shows increased energy sector positioning while maintaining core long-term holdings. His strategy emphasizes quality businesses with strong fundamentals and predictable cash flows.",
        isFollowing: true
    )

    static let cathieWood = WhaleProfile(
        id: "cathie-wood",
        name: "Cathie Wood",
        title: "ARK Invest CEO",
        avatarURL: nil,
        riskProfile: .growthFocused,
        portfolioValue: 14_200_000_000,
        ytdReturn: -8.3,
        sectorExposure: [
            WhaleSectorAllocation(name: "Tech", percentage: 55, colorHex: "3B82F6"),
            WhaleSectorAllocation(name: "Healthcare", percentage: 20, colorHex: "22C55E"),
            WhaleSectorAllocation(name: "Finance", percentage: 15, colorHex: "F97316"),
            WhaleSectorAllocation(name: "Other", percentage: 10, colorHex: "6B7280")
        ],
        currentHoldings: [
            WhaleHolding(ticker: "TSLA", companyName: "Tesla Inc.", allocation: 12.5, changePercent: -2.3),
            WhaleHolding(ticker: "COIN", companyName: "Coinbase", allocation: 8.2, changePercent: 5.1),
            WhaleHolding(ticker: "ROKU", companyName: "Roku Inc.", allocation: 7.8, changePercent: -1.2)
        ],
        recentTradeGroups: [
            WhaleTradeGroup(
                date: Calendar.current.date(byAdding: .day, value: -2, to: Date())!,
                tradeCount: 8,
                netAction: .bought,
                netAmount: 156_000_000,
                summary: "Heavy TSLA accumulation across ARK funds",
                insights: ["Heavy TSLA accumulation across ARK funds"],
                trades: [
                    WhaleTrade(ticker: "TSLA", companyName: "Tesla Inc.", action: .bought, tradeType: .increased, amount: 120_000_000, previousAllocation: 10.5, newAllocation: 12.5),
                    WhaleTrade(ticker: "COIN", companyName: "Coinbase", action: .bought, tradeType: .increased, amount: 36_000_000, previousAllocation: 7.5, newAllocation: 8.2)
                ]
            ),
            WhaleTradeGroup(
                date: Calendar.current.date(byAdding: .day, value: -9, to: Date())!,
                tradeCount: 5,
                netAction: .sold,
                netAmount: 42_000_000,
                trades: [
                    WhaleTrade(ticker: "ROKU", companyName: "Roku Inc.", action: .sold, tradeType: .decreased, amount: 42_000_000, previousAllocation: 8.5, newAllocation: 7.8)
                ]
            )
        ],
        recentTrades: [],
        behaviorSummary: WhaleBehaviorSummary(
            action: "Buying",
            primaryFocus: "innovation stocks",
            secondaryAction: "Reducing",
            secondaryFocus: "mature tech holdings"
        ),
        sentimentSummary: "Cathie Wood continues her focus on disruptive innovation, emphasizing AI, genomics, and blockchain technologies.",
        isFollowing: false
    )
}
