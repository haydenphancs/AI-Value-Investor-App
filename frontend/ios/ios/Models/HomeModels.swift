//
//  HomeModels.swift
//  ios
//
//  Data models for the Home screen
//

import Foundation

// MARK: - Shared Formatters (avoid re-allocating on every computed property access)
private enum SharedFormatters {
    static let priceFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        f.groupingSeparator = ","
        return f
    }()

    static let relativeDateFormatter: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .full
        return f
    }()

    static let reportDateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MMM d, yyyy"
        return f
    }()
}

// MARK: - Market Ticker Type
enum MarketTickerType: Hashable {
    case index      // S&P 500, Nasdaq, Dow Jones → IndexDetailView
    case stock      // Individual stocks → TickerDetailView
    case crypto     // Bitcoin, Ethereum → CryptoDetailView
    case commodity  // Gold, Oil, Silver → CommodityDetailView
    case etf        // ETFs → ETFDetailView
}

// MARK: - Market Ticker
struct MarketTicker: Identifiable, Hashable {
    let id = UUID()
    let name: String
    let symbol: String
    let type: MarketTickerType
    let price: Double
    let changePercent: Double
    let sparklineData: [Double]

    var isPositive: Bool {
        changePercent >= 0
    }

    var formattedPrice: String {
        SharedFormatters.priceFormatter.string(from: NSNumber(value: price)) ?? String(format: "%.2f", price)
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
        SharedFormatters.relativeDateFormatter.localizedString(for: updatedAt, relativeTo: Date())
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
    case billAckman = "Bill Ackman"

    var displayName: String {
        rawValue
    }

    var badgeColor: String {
        switch self {
        case .warrenBuffett: return "4F46E5"
        case .peterLynch: return "059669"
        case .cathieWood: return "DC2626"
        case .billAckman: return "DC2626"
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
    let rating: Double          // 0-100 scale, matches AnalysisReport
    let fairValue: Double
    let createdAt: Date
    let gradientColors: [String]

    var formattedRating: String {
        String(format: "%.0f", rating)
    }

    var formattedFairValue: String {
        "$\(Int(fairValue))"
    }

    var timeAgo: String {
        SharedFormatters.reportDateFormatter.string(from: createdAt)
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
