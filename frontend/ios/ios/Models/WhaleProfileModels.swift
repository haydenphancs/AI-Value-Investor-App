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

// MARK: - Whale Trade
struct WhaleTrade: Identifiable, Codable {
    let id: String
    let ticker: String
    let companyName: String
    let action: WhaleTradeAction
    let amount: Double
    let changePercent: Double
    let date: Date

    var formattedAmount: String {
        formatTradeAmount(amount)
    }

    var formattedChange: String {
        let sign = changePercent >= 0 ? "" : ""
        return "\(sign)\(String(format: "%.1f", changePercent))%"
    }

    var timeAgoFormatted: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    private func formatTradeAmount(_ amount: Double) -> String {
        let absAmount = abs(amount)
        if absAmount >= 1_000_000_000 {
            return String(format: "$%.2fB", amount / 1_000_000_000)
        } else if absAmount >= 1_000_000 {
            return String(format: "$%.2fM", amount / 1_000_000)
        } else if absAmount >= 1_000 {
            return String(format: "$%.0fK", amount / 1_000)
        }
        return String(format: "$%.0f", amount)
    }

    init(id: String = UUID().uuidString, ticker: String, companyName: String, action: WhaleTradeAction, amount: Double, changePercent: Double, date: Date) {
        self.id = id
        self.ticker = ticker
        self.companyName = companyName
        self.action = action
        self.amount = amount
        self.changePercent = changePercent
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
            WhaleSectorAllocation(name: "Energy", percentage: 27, colorHex: "F97316")
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
        recentTrades: [
            WhaleTrade(ticker: "ZYZ", companyName: "XYZ", action: .bought, amount: 1_100_000, changePercent: 1.5, date: Calendar.current.date(byAdding: .day, value: -3, to: Date())!),
            WhaleTrade(ticker: "AAPL", companyName: "Apple Inc.", action: .bought, amount: 3_300_000, changePercent: 2.5, date: Calendar.current.date(byAdding: .day, value: -3, to: Date())!),
            WhaleTrade(ticker: "CVX", companyName: "Coca-Cola", action: .bought, amount: 2_640_000, changePercent: 2.5, date: Calendar.current.date(byAdding: .day, value: -3, to: Date())!),
            WhaleTrade(ticker: "CVX", companyName: "Chevron Corp.", action: .bought, amount: 2_500_000, changePercent: 1.5, date: Calendar.current.date(byAdding: .day, value: -3, to: Date())!),
            WhaleTrade(ticker: "BAC", companyName: "Bank of America", action: .sold, amount: 2_100_000, changePercent: 0.5, date: Calendar.current.date(byAdding: .day, value: -3, to: Date())!)
        ],
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
            WhaleSectorAllocation(name: "Tech", percentage: 65, colorHex: "3B82F6"),
            WhaleSectorAllocation(name: "Healthcare", percentage: 20, colorHex: "22C55E"),
            WhaleSectorAllocation(name: "Finance", percentage: 15, colorHex: "F97316")
        ],
        currentHoldings: [
            WhaleHolding(ticker: "TSLA", companyName: "Tesla Inc.", allocation: 12.5, changePercent: -2.3),
            WhaleHolding(ticker: "COIN", companyName: "Coinbase", allocation: 8.2, changePercent: 5.1),
            WhaleHolding(ticker: "ROKU", companyName: "Roku Inc.", allocation: 7.8, changePercent: -1.2)
        ],
        recentTrades: [
            WhaleTrade(ticker: "TSLA", companyName: "Tesla Inc.", action: .bought, amount: 25_000_000, changePercent: -2.3, date: Calendar.current.date(byAdding: .day, value: -1, to: Date())!)
        ],
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
