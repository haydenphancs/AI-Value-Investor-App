//
//  HomeModels.swift
//  ios
//
//  Data models for the Home screen
//

import Foundation

// MARK: - Market Ticker
struct MarketTicker: Identifiable {
    let id = UUID()
    let name: String
    let price: Double
    let changePercent: Double
    let sparklineData: [Double]

    var isPositive: Bool {
        changePercent >= 0
    }

    var formattedPrice: String {
        if price >= 10000 {
            return String(format: "$%.2f", price)
        } else if price >= 1000 {
            return String(format: "$%.2f", price)
        } else {
            return String(format: "$%.2f", price)
        }
    }

    var formattedChange: String {
        let sign = changePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", changePercent))%"
    }
}

// MARK: - Market Sentiment
enum MarketSentiment: String {
    case bullish = "Bullish"
    case bearish = "Bearish"
    case neutral = "Neutral"
}

// MARK: - Market Insight
struct MarketInsight: Identifiable {
    let id = UUID()
    let headline: String
    let bulletPoints: [String]
    let sentiment: MarketSentiment
    let updatedAt: Date

    var timeAgo: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter.localizedString(for: updatedAt, relativeTo: Date())
    }
}

// MARK: - Alert Type
enum AlertType: String {
    case whalesAlert = "whales_alert"
    case earningsAlert = "earnings_alert"
    case whalesFollowing = "whales_following"
    case wiserTrending = "wiser_trending"

    var iconName: String {
        switch self {
        case .whalesAlert: return "icon_whale"
        case .earningsAlert: return "icon_earnings"
        case .whalesFollowing: return "icon_whale_following"
        case .wiserTrending: return "icon_wiser"
        }
    }

    var systemIconName: String {
        switch self {
        case .whalesAlert: return "bell.fill"
        case .earningsAlert: return "chart.line.uptrend.xyaxis"
        case .whalesFollowing: return "bell.fill"
        case .wiserTrending: return "lightbulb.fill"
        }
    }
}

// MARK: - Daily Briefing Item
struct DailyBriefingItem: Identifiable {
    let id = UUID()
    let type: AlertType
    let title: String
    let subtitle: String
    let date: Date?
    let badgeText: String?

    var hasDateBadge: Bool {
        date != nil && badgeText != nil
    }
}

// MARK: - Investor Persona
enum InvestorPersona: String, CaseIterable {
    case warrenBuffett = "Warren Buffett"
    case peterLynch = "Peter Lynch"
    case cathieWood = "Cathie Wood"
    case charleMunger = "Charlie Munger"
    case benjaminGraham = "Benjamin Graham"

    var displayName: String {
        rawValue
    }

    var badgeColor: String {
        switch self {
        case .warrenBuffett: return "4F46E5"
        case .peterLynch: return "059669"
        case .cathieWood: return "DC2626"
        case .charleMunger: return "7C3AED"
        case .benjaminGraham: return "EA580C"
        }
    }
}

// MARK: - Research Report
struct ResearchReport: Identifiable {
    let id = UUID()
    let stockTicker: String
    let stockName: String
    let companyLogoName: String
    let persona: InvestorPersona
    let headline: String
    let summary: String
    let rating: Double
    let targetPrice: Double
    let createdAt: Date
    let gradientColors: [String]

    var formattedRating: String {
        String(format: "%.1f/5", rating)
    }

    var formattedTargetPrice: String {
        "$\(Int(targetPrice))"
    }

    var timeAgo: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: createdAt, relativeTo: Date())
    }
}

// MARK: - Tab Item
enum HomeTab: String, CaseIterable {
    case home = "Home"
    case updates = "Updates"
    case research = "Research"
    case tracking = "Tracking"
    case wiser = "Wiser"

    var iconName: String {
        switch self {
        case .home: return "icon_home"
        case .updates: return "icon_updates"
        case .research: return "icon_research"
        case .tracking: return "icon_tracking"
        case .wiser: return "icon_wiser"
        }
    }

    var systemIconName: String {
        switch self {
        case .home: return "house.fill"
        case .updates: return "chart.bar.doc.horizontal"
        case .research: return "magnifyingglass"
        case .tracking: return "star.fill"
        case .wiser: return "lightbulb.fill"
        }
    }
}
