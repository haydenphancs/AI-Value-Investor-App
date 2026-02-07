//
//  TrackingModels.swift
//  ios
//
//  Data models for the Tracking screen
//

import Foundation
import SwiftUI

// MARK: - Notifications
extension Notification.Name {
    static let whaleFollowStateChanged = Notification.Name("whaleFollowStateChanged")
}

// MARK: - Tracking Tab
enum TrackingTab: String, CaseIterable {
    case assets = "Assets"
    case whales = "Whales"
}

// MARK: - Asset Sort Option
enum AssetSortOption: String, CaseIterable {
    case name = "Name"
    case price = "Price"
    case change = "Change"
    case marketCap = "Market Cap"

    var displayName: String { rawValue }
}

// MARK: - Tracked Asset
struct TrackedAsset: Identifiable {
    let id = UUID()
    let ticker: String
    let companyName: String
    let price: Double
    let changePercent: Double
    let sparklineData: [Double]

    var isPositive: Bool {
        changePercent >= 0
    }

    var formattedPrice: String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencySymbol = "$"
        formatter.minimumFractionDigits = 2
        formatter.maximumFractionDigits = 2
        return formatter.string(from: NSNumber(value: price)) ?? String(format: "$%.2f", price)
    }

    var formattedChange: String {
        let sign = changePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", changePercent))%"
    }
}

// MARK: - Alert Event Type
enum AlertEventType: String, CaseIterable {
    case earnings = "earnings"
    case market = "market"
    case smartMoney = "smart_money"

    var iconBackgroundColor: Color {
        switch self {
        case .earnings: return AppColors.primaryBlue
        case .market: return AppColors.primaryBlue
        case .smartMoney: return AppColors.alertOrange
        }
    }

    var systemIconName: String {
        switch self {
        case .earnings: return "bell.fill"
        case .market: return "bell.fill"
        case .smartMoney: return "lightbulb.fill"
        }
    }
}

// MARK: - Alert Event
struct AlertEvent: Identifiable {
    let id = UUID()
    let type: AlertEventType
    let title: String
    let description: String
    let date: Date?
    let day: Int?
    let month: String?

    var hasDate: Bool {
        day != nil && month != nil
    }

    var formattedDay: String {
        guard let day = day else { return "" }
        return String(day)
    }

    var formattedMonth: String {
        month?.uppercased() ?? ""
    }
}

// MARK: - Smart Money Alert
struct SmartMoneyAlert: Identifiable {
    let id = UUID()
    let title: String
    let description: String
    let fundCount: Int
    let ticker: String
    let positionSize: String
}

// MARK: - Sector Allocation
struct SectorAllocation: Identifiable {
    let id = UUID()
    let name: String
    let percentage: Double

    var formattedPercentage: String {
        "\(Int(percentage))%"
    }
}

// MARK: - Diversification Score
struct DiversificationScore: Identifiable {
    let id = UUID()
    let score: Int
    let message: String
    let sectorCount: Int
    let allocations: [SectorAllocation]

    var formattedScore: String {
        "\(score)%"
    }

    var progressValue: Double {
        Double(score) / 100.0
    }
}

// MARK: - Whale Category
enum WhaleCategory: String, CaseIterable {
    case investors = "Investors"
    case institutions = "Institutions"
    case politicians = "Politicians"
    case cryptoWhales = "Crypto"
}

// MARK: - Whale Action
enum WhaleAction: String {
    case bought = "BOUGHT"
    case sold = "SOLD"

    var color: Color {
        switch self {
        case .bought: return AppColors.bullish
        case .sold: return AppColors.bearish
        }
    }
}

// MARK: - Whale Activity
struct WhaleActivity: Identifiable {
    let id = UUID()
    let entityName: String
    let entityAvatarName: String
    let action: WhaleAction
    let ticker: String
    let amount: String
    let source: String
    let date: Date

    var timeAgo: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: date, relativeTo: Date())
    }
}

// MARK: - Trending Whale
struct TrendingWhale: Identifiable {
    let id = UUID()
    let name: String
    let category: WhaleCategory
    let avatarName: String
    let followersCount: Int
    let isFollowing: Bool
    let title: String
    let description: String
    let recentTradeCount: Int

    init(name: String, category: WhaleCategory, avatarName: String, followersCount: Int, isFollowing: Bool, title: String = "", description: String = "", recentTradeCount: Int = 0) {
        self.name = name
        self.category = category
        self.avatarName = avatarName
        self.followersCount = followersCount
        self.isFollowing = isFollowing
        self.title = title
        self.description = description
        self.recentTradeCount = recentTradeCount
    }

    var formattedFollowers: String {
        if followersCount >= 1000 {
            return "\(followersCount / 1000)K followers"
        }
        return "\(followersCount) followers"
    }

    var formattedTradeCount: String {
        if recentTradeCount == 1 {
            return "1 trade"
        }
        return "\(recentTradeCount) trades"
    }
}

// MARK: - Whale Trade Group Activity (for Recent Trades timeline)
struct WhaleTradeGroupActivity: Identifiable {
    let id = UUID()
    let entityName: String
    let entityAvatarName: String
    let action: WhaleAction
    let tradeCount: Int
    let totalAmount: String
    let summary: String?
    let date: Date

    var formattedDate: String {
        let calendar = Calendar.current
        let now = Date()

        if calendar.isDateInToday(date) {
            return "Today"
        } else if calendar.isDateInYesterday(date) {
            return "Yesterday"
        }

        let components = calendar.dateComponents([.day], from: date, to: now)
        if let days = components.day, days <= 7 {
            return "\(days) days ago"
        }

        let formatter = DateFormatter()
        formatter.dateFormat = "MMM dd, yyyy"
        return formatter.string(from: date)
    }

    var formattedAmount: String {
        switch action {
        case .bought:
            return "+\(totalAmount)"
        case .sold:
            return "- \(totalAmount)"
        }
    }

    var formattedTradeCount: String {
        if tradeCount == 1 {
            return "1 trade"
        }
        return "\(tradeCount) trades"
    }
}

// MARK: - Grouped Whale Trades (for timeline sections)
struct GroupedWhaleTrades: Identifiable {
    let id = UUID()
    let sectionTitle: String
    let activities: [WhaleTradeGroupActivity]
}

// MARK: - Whale Alert Banner
struct WhaleAlertBanner: Identifiable {
    let id = UUID()
    let title: String
    let description: String
    let ticker: String?
    let actionTitle: String
}

// MARK: - Sample Data
extension TrackedAsset {
    static let sampleData: [TrackedAsset] = [
        TrackedAsset(
            ticker: "AAPL",
            companyName: "Apple Inc.",
            price: 178.42,
            changePercent: 2.34,
            sparklineData: [165, 168, 170, 172, 175, 173, 176, 178]
        ),
        TrackedAsset(
            ticker: "NVDA",
            companyName: "NVIDIA Corp.",
            price: 495.22,
            changePercent: 5.67,
            sparklineData: [450, 460, 470, 465, 480, 490, 495]
        ),
        TrackedAsset(
            ticker: "MSFT",
            companyName: "Microsoft Corp.",
            price: 378.91,
            changePercent: -1.23,
            sparklineData: [390, 388, 385, 382, 380, 378, 379]
        ),
        TrackedAsset(
            ticker: "GOOGL",
            companyName: "Alphabet Inc.",
            price: 139.67,
            changePercent: 1.89,
            sparklineData: [135, 136, 137, 138, 137, 139, 140]
        )
    ]
}

extension AlertEvent {
    static let sampleData: [AlertEvent] = [
        AlertEvent(
            type: .earnings,
            title: "Earnings Alert",
            description: "NVDA reports earnings tomorrow after market close. Analyst consensus: Beat expected",
            date: Calendar.current.date(byAdding: .day, value: 1, to: Date()),
            day: 22,
            month: "FEB"
        ),
        AlertEvent(
            type: .market,
            title: "Market",
            description: "Fed interest rate decision. FOMC meeting announcement",
            date: Calendar.current.date(byAdding: .day, value: 3, to: Date()),
            day: 24,
            month: "FEB"
        )
    ]
}

extension SmartMoneyAlert {
    static let sampleData: SmartMoneyAlert = SmartMoneyAlert(
        title: "Smart Money Following",
        description: "3 hedge funds you follow bought GOOGL this week. Avg. position size: $1.2B",
        fundCount: 3,
        ticker: "GOOGL",
        positionSize: "$1.2B"
    )
}

extension DiversificationScore {
    static let sampleData: DiversificationScore = DiversificationScore(
        score: 78,
        message: "Your portfolio is well-diversified across 5 sectors",
        sectorCount: 5,
        allocations: [
            SectorAllocation(name: "Tech", percentage: 45),
            SectorAllocation(name: "Consumer", percentage: 22),
            SectorAllocation(name: "Finance", percentage: 18),
            SectorAllocation(name: "Energy", percentage: 15)
        ]
    )
}

extension WhaleActivity {
    static let sampleData: [WhaleActivity] = [
        WhaleActivity(
            entityName: "Warren Buffett",
            entityAvatarName: "avatar_buffett",
            action: .bought,
            ticker: "AAPL",
            amount: "$2.4B",
            source: "13F Filing",
            date: Calendar.current.date(byAdding: .day, value: -1, to: Date())!
        ),
        WhaleActivity(
            entityName: "Nancy Pelosi",
            entityAvatarName: "avatar_pelosi",
            action: .sold,
            ticker: "NVDA",
            amount: "$1.5M",
            source: "Congressional Disclosure",
            date: Calendar.current.date(byAdding: .day, value: -2, to: Date())!
        )
    ]
}

extension TrendingWhale {
    // Whales the user is following (shown in FollowedWhalesRow)
    static let trackedWhalesData: [TrendingWhale] = [
        TrendingWhale(
            name: "Warren Buffett",
            category: .investors,
            avatarName: "avatar_buffett",
            followersCount: 125000,
            isFollowing: true,
            recentTradeCount: 3
        ),
        TrendingWhale(
            name: "Nancy Pelosi",
            category: .politicians,
            avatarName: "avatar_pelosi",
            followersCount: 156000,
            isFollowing: true,
            recentTradeCount: 8
        ),
        TrendingWhale(
            name: "Bill Ackman",
            category: .institutions,
            avatarName: "avatar_ackman",
            followersCount: 76000,
            isFollowing: true,
            recentTradeCount: 4
        ),
        TrendingWhale(
            name: "Michael Saylor",
            category: .cryptoWhales,
            avatarName: "avatar_saylor",
            followersCount: 134000,
            isFollowing: true,
            recentTradeCount: 2
        ),
        TrendingWhale(
            name: "Michael Burry",
            category: .investors,
            avatarName: "avatar_burry",
            followersCount: 98000,
            isFollowing: true,
            recentTradeCount: 1
        )
    ]

    // Top 5 popular whales for main screen (mixed categories)
    static let topPopularWhalesData: [TrendingWhale] = [
        TrendingWhale(
            name: "Ray Dalio",
            category: .institutions,
            avatarName: "avatar_dalio",
            followersCount: 112000,
            isFollowing: false,
            title: "Bridgewater Associates"
        ),
        TrendingWhale(
            name: "George Soros",
            category: .investors,
            avatarName: "avatar_soros",
            followersCount: 95000,
            isFollowing: false,
            title: "Soros Fund Management"
        ),
        TrendingWhale(
            name: "Cathie Wood",
            category: .institutions,
            avatarName: "avatar_wood",
            followersCount: 89000,
            isFollowing: false,
            title: "ARK Invest CEO"
        ),
        TrendingWhale(
            name: "Vitalik Buterin",
            category: .cryptoWhales,
            avatarName: "avatar_vitalik",
            followersCount: 201000,
            isFollowing: false,
            title: "Ethereum Co-founder"
        ),
        TrendingWhale(
            name: "Tommy Tuberville",
            category: .politicians,
            avatarName: "avatar_tuberville",
            followersCount: 67000,
            isFollowing: false,
            title: "U.S. Senator"
        )
    ]

    // Hero whales for the carousel
    static let heroWhalesData: [TrendingWhale] = [
        TrendingWhale(
            name: "Warren Buffett",
            category: .investors,
            avatarName: "avatar_buffett",
            followersCount: 125000,
            isFollowing: false,
            title: "Berkshire Hathaway CEO",
            description: "The Oracle of Omaha. Value investing legend with 50+ years of market-beating returns."
        ),
        TrendingWhale(
            name: "Cathie Wood",
            category: .institutions,
            avatarName: "avatar_wood",
            followersCount: 89000,
            isFollowing: false,
            title: "ARK Invest CEO",
            description: "Disruptive innovation champion. Leading the charge in AI, genomics, and blockchain investing."
        ),
        TrendingWhale(
            name: "Ray Dalio",
            category: .institutions,
            avatarName: "avatar_dalio",
            followersCount: 112000,
            isFollowing: false,
            title: "Bridgewater Associates Founder",
            description: "Macro investing pioneer. Built the world's largest hedge fund with radical transparency."
        ),
        TrendingWhale(
            name: "Michael Burry",
            category: .investors,
            avatarName: "avatar_burry",
            followersCount: 98000,
            isFollowing: false,
            title: "Scion Asset Management",
            description: "The Big Short. Legendary contrarian known for calling the 2008 housing crisis."
        )
    ]

    // MARK: - All Whales by Category (for AllWhalesView)

    // 10 Investors
    static let allInvestorsData: [TrendingWhale] = [
        TrendingWhale(name: "Warren Buffett", category: .investors, avatarName: "avatar_buffett", followersCount: 125000, isFollowing: false, title: "Berkshire Hathaway CEO"),
        TrendingWhale(name: "Michael Burry", category: .investors, avatarName: "avatar_burry", followersCount: 98000, isFollowing: false, title: "Scion Asset Management"),
        TrendingWhale(name: "George Soros", category: .investors, avatarName: "avatar_soros", followersCount: 95000, isFollowing: false, title: "Soros Fund Management"),
        TrendingWhale(name: "Carl Icahn", category: .investors, avatarName: "avatar_icahn", followersCount: 87000, isFollowing: false, title: "Icahn Enterprises"),
        TrendingWhale(name: "Stanley Druckenmiller", category: .investors, avatarName: "avatar_druckenmiller", followersCount: 83000, isFollowing: false, title: "Duquesne Family Office"),
        TrendingWhale(name: "Peter Lynch", category: .investors, avatarName: "avatar_lynch", followersCount: 72000, isFollowing: false, title: "Fidelity Investments"),
        TrendingWhale(name: "Howard Marks", category: .investors, avatarName: "avatar_marks", followersCount: 68000, isFollowing: false, title: "Oaktree Capital Co-Chairman"),
        TrendingWhale(name: "Seth Klarman", category: .investors, avatarName: "avatar_klarman", followersCount: 54000, isFollowing: false, title: "Baupost Group"),
        TrendingWhale(name: "Joel Greenblatt", category: .investors, avatarName: "avatar_greenblatt", followersCount: 41000, isFollowing: false, title: "Gotham Capital"),
        TrendingWhale(name: "Mohnish Pabrai", category: .investors, avatarName: "avatar_pabrai", followersCount: 38000, isFollowing: false, title: "Pabrai Investment Funds")
    ]

    // 10 Institutions
    static let allInstitutionsData: [TrendingWhale] = [
        TrendingWhale(name: "Ray Dalio", category: .institutions, avatarName: "avatar_dalio", followersCount: 112000, isFollowing: false, title: "Bridgewater Associates"),
        TrendingWhale(name: "Ken Griffin", category: .institutions, avatarName: "avatar_griffin", followersCount: 92000, isFollowing: false, title: "Citadel CEO"),
        TrendingWhale(name: "Cathie Wood", category: .institutions, avatarName: "avatar_wood", followersCount: 89000, isFollowing: false, title: "ARK Invest CEO"),
        TrendingWhale(name: "Bill Ackman", category: .institutions, avatarName: "avatar_ackman", followersCount: 76000, isFollowing: false, title: "Pershing Square Capital"),
        TrendingWhale(name: "Jim Simons", category: .institutions, avatarName: "avatar_simons", followersCount: 78000, isFollowing: false, title: "Renaissance Technologies"),
        TrendingWhale(name: "Steve Cohen", category: .institutions, avatarName: "avatar_cohen", followersCount: 67000, isFollowing: false, title: "Point72 Chairman"),
        TrendingWhale(name: "David Tepper", category: .institutions, avatarName: "avatar_tepper", followersCount: 58000, isFollowing: false, title: "Appaloosa Management"),
        TrendingWhale(name: "Chase Coleman", category: .institutions, avatarName: "avatar_coleman", followersCount: 51000, isFollowing: false, title: "Tiger Global Management"),
        TrendingWhale(name: "Dan Loeb", category: .institutions, avatarName: "avatar_loeb", followersCount: 45000, isFollowing: false, title: "Third Point CEO"),
        TrendingWhale(name: "Philippe Laffont", category: .institutions, avatarName: "avatar_laffont", followersCount: 39000, isFollowing: false, title: "Coatue Management")
    ]

    // 10 Politicians
    static let allPoliticiansData: [TrendingWhale] = [
        TrendingWhale(name: "Nancy Pelosi", category: .politicians, avatarName: "avatar_pelosi", followersCount: 156000, isFollowing: false, title: "U.S. Representative (CA)"),
        TrendingWhale(name: "Tommy Tuberville", category: .politicians, avatarName: "avatar_tuberville", followersCount: 67000, isFollowing: false, title: "U.S. Senator (AL)"),
        TrendingWhale(name: "Dan Crenshaw", category: .politicians, avatarName: "avatar_crenshaw", followersCount: 53000, isFollowing: false, title: "U.S. Representative (TX)"),
        TrendingWhale(name: "Ro Khanna", category: .politicians, avatarName: "avatar_khanna", followersCount: 35000, isFollowing: false, title: "U.S. Representative (CA)"),
        TrendingWhale(name: "Josh Gottheimer", category: .politicians, avatarName: "avatar_gottheimer", followersCount: 31000, isFollowing: false, title: "U.S. Representative (NJ)"),
        TrendingWhale(name: "Mark Green", category: .politicians, avatarName: "avatar_green", followersCount: 28000, isFollowing: false, title: "U.S. Representative (TN)"),
        TrendingWhale(name: "Michael McCaul", category: .politicians, avatarName: "avatar_mccaul", followersCount: 24000, isFollowing: false, title: "U.S. Representative (TX)"),
        TrendingWhale(name: "John Curtis", category: .politicians, avatarName: "avatar_curtis", followersCount: 22000, isFollowing: false, title: "U.S. Senator (UT)"),
        TrendingWhale(name: "Pat Fallon", category: .politicians, avatarName: "avatar_fallon", followersCount: 19000, isFollowing: false, title: "U.S. Representative (TX)"),
        TrendingWhale(name: "Dan Sullivan", category: .politicians, avatarName: "avatar_sullivan", followersCount: 18000, isFollowing: false, title: "U.S. Senator (AK)")
    ]

    // 5 Crypto Whales
    static let allCryptoWhalesData: [TrendingWhale] = [
        TrendingWhale(name: "Vitalik Buterin", category: .cryptoWhales, avatarName: "avatar_vitalik", followersCount: 201000, isFollowing: false, title: "Ethereum Co-founder"),
        TrendingWhale(name: "Changpeng Zhao", category: .cryptoWhales, avatarName: "avatar_cz", followersCount: 175000, isFollowing: false, title: "Binance Founder"),
        TrendingWhale(name: "Michael Saylor", category: .cryptoWhales, avatarName: "avatar_saylor", followersCount: 134000, isFollowing: false, title: "MicroStrategy Chairman"),
        TrendingWhale(name: "Brian Armstrong", category: .cryptoWhales, avatarName: "avatar_armstrong", followersCount: 88000, isFollowing: false, title: "Coinbase CEO"),
        TrendingWhale(name: "Chris Larsen", category: .cryptoWhales, avatarName: "avatar_larsen", followersCount: 48000, isFollowing: false, title: "Ripple Co-founder")
    ]

    // All popular whales combined
    static let allPopularWhalesData: [TrendingWhale] = allInvestorsData + allInstitutionsData + allPoliticiansData + allCryptoWhalesData

    // Combined for backward compatibility
    static let sampleData: [TrendingWhale] = trackedWhalesData + topPopularWhalesData
}

extension WhaleTradeGroupActivity {
    static let sampleData: [WhaleTradeGroupActivity] = [
        WhaleTradeGroupActivity(
            entityName: "Warren Buffett",
            entityAvatarName: "avatar_buffett",
            action: .bought,
            tradeCount: 6,
            totalAmount: "$4.34B",
            summary: "Significant rebalancing, trimmed 3 Tech positions",
            date: Calendar.current.date(byAdding: .day, value: -7, to: Date())!
        ),
        WhaleTradeGroupActivity(
            entityName: "Bill Ackman",
            entityAvatarName: "avatar_ackman",
            action: .bought,
            tradeCount: 4,
            totalAmount: "$2.82B",
            summary: nil,
            date: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 26))!
        ),
        WhaleTradeGroupActivity(
            entityName: "Nancy Pelosi",
            entityAvatarName: "avatar_pelosi",
            action: .bought,
            tradeCount: 8,
            totalAmount: "$6.53B",
            summary: "Huge bought, add 4 new positions in Tech sector",
            date: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 2))!
        ),
        WhaleTradeGroupActivity(
            entityName: "Warren Buffett",
            entityAvatarName: "avatar_buffett",
            action: .sold,
            tradeCount: 12,
            totalAmount: "$4.5B",
            summary: "Significant sold with 3 closed positions in Banking sector",
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 14))!
        )
    ]

    static var groupedSampleData: [GroupedWhaleTrades] {
        sampleData.map { activity in
            GroupedWhaleTrades(
                sectionTitle: activity.formattedDate,
                activities: [activity]
            )
        }
    }
}

extension WhaleAlertBanner {
    static let sampleData = WhaleAlertBanner(
        title: "Whale Alert",
        description: "Large crypto whale just moved $50M into COIN stock",
        ticker: "COIN",
        actionTitle: "View Full Alert"
    )
}
