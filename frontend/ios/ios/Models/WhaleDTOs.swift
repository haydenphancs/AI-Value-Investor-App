//
//  WhaleDTOs.swift
//  ios
//
//  Codable DTOs for whale API responses.
//  Maps backend snake_case JSON to Swift camelCase view models.
//

import Foundation

// MARK: - Trending Whale DTO

struct TrendingWhaleDTO: Codable, Identifiable {
    let id: String
    let name: String
    let category: String
    let avatarUrl: String?
    let followersCount: Int
    let isFollowing: Bool
    let title: String
    let description: String
    let recentTradeCount: Int

    enum CodingKeys: String, CodingKey {
        case id, name, category, title, description
        case avatarUrl = "avatar_url"
        case followersCount = "followers_count"
        case isFollowing = "is_following"
        case recentTradeCount = "recent_trade_count"
    }

    func toTrendingWhale() -> TrendingWhale {
        TrendingWhale(
            id: id,
            name: name,
            category: WhaleCategory.fromBackend(category),
            avatarName: avatarUrl ?? "",
            followersCount: followersCount,
            isFollowing: isFollowing,
            title: title,
            description: description,
            recentTradeCount: recentTradeCount
        )
    }
}

// MARK: - Whale Trade Group Activity DTO

struct WhaleTradeGroupActivityDTO: Codable, Identifiable {
    let id: String
    let whaleId: String
    let entityName: String
    let entityAvatarName: String
    let action: String
    let tradeCount: Int
    let totalAmount: String
    let summary: String?
    let date: String

    enum CodingKeys: String, CodingKey {
        case id, action, summary, date
        case whaleId = "whale_id"
        case entityName = "entity_name"
        case entityAvatarName = "entity_avatar_name"
        case tradeCount = "trade_count"
        case totalAmount = "total_amount"
    }

    func toWhaleTradeGroupActivity() -> WhaleTradeGroupActivity {
        WhaleTradeGroupActivity(
            id: id,
            entityName: entityName,
            entityAvatarName: entityAvatarName,
            action: WhaleAction(rawValue: action) ?? .bought,
            tradeCount: tradeCount,
            totalAmount: totalAmount,
            summary: summary,
            date: DateParser.parseDate(date)
        )
    }
}

// MARK: - Whale Profile DTO

struct WhaleProfileDTO: Codable {
    let id: String
    let name: String
    let title: String
    let description: String
    let avatarUrl: String?
    let riskProfile: String
    let portfolioValue: Double
    let ytdReturn: Double
    let sectorExposure: [WhaleSectorAllocationDTO]
    let currentHoldings: [WhaleHoldingDTO]
    let recentTradeGroups: [WhaleTradeGroupDTO]
    let recentTrades: [WhaleTradeDTO]
    let behaviorSummary: WhaleBehaviorSummaryDTO
    let sentimentSummary: String
    let isFollowing: Bool

    enum CodingKeys: String, CodingKey {
        case id, name, title, description
        case avatarUrl = "avatar_url"
        case riskProfile = "risk_profile"
        case portfolioValue = "portfolio_value"
        case ytdReturn = "ytd_return"
        case sectorExposure = "sector_exposure"
        case currentHoldings = "current_holdings"
        case recentTradeGroups = "recent_trade_groups"
        case recentTrades = "recent_trades"
        case behaviorSummary = "behavior_summary"
        case sentimentSummary = "sentiment_summary"
        case isFollowing = "is_following"
    }

    func toWhaleProfile() -> WhaleProfile {
        WhaleProfile(
            id: id,
            name: name,
            title: title,
            description: description,
            avatarURL: avatarUrl,
            riskProfile: WhaleRiskProfile.fromBackend(riskProfile),
            portfolioValue: portfolioValue,
            ytdReturn: ytdReturn,
            sectorExposure: sectorExposure.map { $0.toWhaleSectorAllocation() },
            currentHoldings: currentHoldings.map { $0.toWhaleHolding() },
            recentTradeGroups: recentTradeGroups.map { $0.toWhaleTradeGroup() },
            recentTrades: recentTrades.map { $0.toWhaleTrade() },
            behaviorSummary: behaviorSummary.toWhaleBehaviorSummary(),
            sentimentSummary: sentimentSummary,
            isFollowing: isFollowing
        )
    }
}

// MARK: - Whale Sector Allocation DTO

struct WhaleSectorAllocationDTO: Codable {
    let id: String
    let name: String
    let percentage: Double
    let colorHex: String

    enum CodingKeys: String, CodingKey {
        case id, name, percentage
        case colorHex = "color_hex"
    }

    func toWhaleSectorAllocation() -> WhaleSectorAllocation {
        WhaleSectorAllocation(id: id, name: name, percentage: percentage, colorHex: colorHex)
    }
}

// MARK: - Whale Holding DTO

struct WhaleHoldingDTO: Codable {
    let id: String
    let ticker: String
    let companyName: String
    let logoUrl: String?
    let allocation: Double
    let changePercent: Double

    enum CodingKeys: String, CodingKey {
        case id, ticker, allocation
        case companyName = "company_name"
        case logoUrl = "logo_url"
        case changePercent = "change_percent"
    }

    func toWhaleHolding() -> WhaleHolding {
        WhaleHolding(
            id: id,
            ticker: ticker,
            companyName: companyName,
            logoURL: logoUrl,
            allocation: allocation,
            changePercent: changePercent
        )
    }
}

// MARK: - Whale Trade Group DTO

struct WhaleTradeGroupDTO: Codable {
    let id: String
    let date: String
    let tradeCount: Int
    let netAction: String
    let netAmount: Double
    let summary: String?
    let insights: [String]
    let trades: [WhaleTradeDTO]

    enum CodingKeys: String, CodingKey {
        case id, date, summary, insights, trades
        case tradeCount = "trade_count"
        case netAction = "net_action"
        case netAmount = "net_amount"
    }

    func toWhaleTradeGroup() -> WhaleTradeGroup {
        WhaleTradeGroup(
            id: id,
            date: DateParser.parseDate(date),
            tradeCount: tradeCount,
            netAction: WhaleTradeAction(rawValue: netAction) ?? .bought,
            netAmount: netAmount,
            summary: summary,
            insights: insights,
            trades: trades.map { $0.toWhaleTrade() }
        )
    }
}

// MARK: - Whale Trade DTO

struct WhaleTradeDTO: Codable {
    let id: String
    let ticker: String
    let companyName: String
    let action: String
    let tradeType: String
    let amount: Double
    let previousAllocation: Double
    let newAllocation: Double
    let date: String

    enum CodingKeys: String, CodingKey {
        case id, ticker, action, amount, date
        case companyName = "company_name"
        case tradeType = "trade_type"
        case previousAllocation = "previous_allocation"
        case newAllocation = "new_allocation"
    }

    func toWhaleTrade() -> WhaleTrade {
        WhaleTrade(
            id: id,
            ticker: ticker,
            companyName: companyName,
            action: WhaleTradeAction(rawValue: action) ?? .bought,
            tradeType: WhaleTradeType(rawValue: tradeType) ?? .increased,
            amount: amount,
            previousAllocation: previousAllocation,
            newAllocation: newAllocation,
            date: DateParser.parseDate(date)
        )
    }
}

// MARK: - Whale Behavior Summary DTO

struct WhaleBehaviorSummaryDTO: Codable {
    let action: String
    let primaryFocus: String
    let secondaryAction: String
    let secondaryFocus: String

    enum CodingKeys: String, CodingKey {
        case action
        case primaryFocus = "primary_focus"
        case secondaryAction = "secondary_action"
        case secondaryFocus = "secondary_focus"
    }

    func toWhaleBehaviorSummary() -> WhaleBehaviorSummary {
        WhaleBehaviorSummary(
            action: action,
            primaryFocus: primaryFocus,
            secondaryAction: secondaryAction,
            secondaryFocus: secondaryFocus
        )
    }
}

// MARK: - Follow Response DTO

struct FollowResponseDTO: Codable {
    let isFollowing: Bool
    let followersCount: Int

    enum CodingKeys: String, CodingKey {
        case isFollowing = "is_following"
        case followersCount = "followers_count"
    }
}

// MARK: - Date Parser

enum DateParser {
    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    private static let isoFormatter = ISO8601DateFormatter()

    static func parseDate(_ dateString: String) -> Date {
        if let date = dateFormatter.date(from: dateString) {
            return date
        }
        if let date = isoFormatter.date(from: dateString) {
            return date
        }
        print("[DateParser] ⚠️ Failed to parse date: \(dateString)")
        return Date()
    }
}
