//
//  TrackingModels.swift
//  ios
//
//  Data models for the Tracking screen
//

import Foundation
import SwiftUI

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
    case following = "Following"
    case hedgeFunds = "Hedge Funds"
    case politicians = "Politicians"
    case cryptoWhales = "Crypto Whales"
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

    init(name: String, category: WhaleCategory, avatarName: String, followersCount: Int, isFollowing: Bool, title: String = "", description: String = "") {
        self.name = name
        self.category = category
        self.avatarName = avatarName
        self.followersCount = followersCount
        self.isFollowing = isFollowing
        self.title = title
        self.description = description
    }

    var formattedFollowers: String {
        if followersCount >= 1000 {
            return "\(followersCount / 1000)K followers"
        }
        return "\(followersCount) followers"
    }
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
    // Whales the user is following
    static let trackedWhalesData: [TrendingWhale] = [
        TrendingWhale(
            name: "Warren Buffett",
            category: .hedgeFunds,
            avatarName: "avatar_buffett",
            followersCount: 125000,
            isFollowing: true
        ),
        TrendingWhale(
            name: "Michael Burry",
            category: .hedgeFunds,
            avatarName: "avatar_burry",
            followersCount: 98000,
            isFollowing: true
        ),
        TrendingWhale(
            name: "Nancy Pelosi",
            category: .politicians,
            avatarName: "avatar_pelosi",
            followersCount: 156000,
            isFollowing: true
        )
    ]

    // Popular whales to discover
    static let popularWhalesData: [TrendingWhale] = [
        TrendingWhale(
            name: "Cathie Wood",
            category: .hedgeFunds,
            avatarName: "avatar_wood",
            followersCount: 89000,
            isFollowing: false,
            title: "ARK Invest CEO"
        ),
        TrendingWhale(
            name: "Bill Ackman",
            category: .hedgeFunds,
            avatarName: "avatar_ackman",
            followersCount: 76000,
            isFollowing: false,
            title: "Pershing Square Capital"
        ),
        TrendingWhale(
            name: "Ray Dalio",
            category: .hedgeFunds,
            avatarName: "avatar_dalio",
            followersCount: 112000,
            isFollowing: false,
            title: "Bridgewater Associates"
        ),
        TrendingWhale(
            name: "Dan Gallagher",
            category: .politicians,
            avatarName: "avatar_gallagher",
            followersCount: 45000,
            isFollowing: false,
            title: "U.S. Representative"
        ),
        TrendingWhale(
            name: "Tommy Tuberville",
            category: .politicians,
            avatarName: "avatar_tuberville",
            followersCount: 67000,
            isFollowing: false,
            title: "U.S. Senator"
        ),
        TrendingWhale(
            name: "Michael Saylor",
            category: .cryptoWhales,
            avatarName: "avatar_saylor",
            followersCount: 134000,
            isFollowing: false,
            title: "MicroStrategy Chairman"
        ),
        TrendingWhale(
            name: "Vitalik Buterin",
            category: .cryptoWhales,
            avatarName: "avatar_vitalik",
            followersCount: 201000,
            isFollowing: false,
            title: "Ethereum Co-founder"
        )
    ]

    // Hero whales for the carousel
    static let heroWhalesData: [TrendingWhale] = [
        TrendingWhale(
            name: "Warren Buffett",
            category: .hedgeFunds,
            avatarName: "avatar_buffett",
            followersCount: 125000,
            isFollowing: false,
            title: "Berkshire Hathaway CEO",
            description: "The Oracle of Omaha. Value investing legend with 50+ years of market-beating returns."
        ),
        TrendingWhale(
            name: "Cathie Wood",
            category: .hedgeFunds,
            avatarName: "avatar_wood",
            followersCount: 89000,
            isFollowing: false,
            title: "ARK Invest CEO",
            description: "Disruptive innovation champion. Leading the charge in AI, genomics, and blockchain investing."
        ),
        TrendingWhale(
            name: "Ray Dalio",
            category: .hedgeFunds,
            avatarName: "avatar_dalio",
            followersCount: 112000,
            isFollowing: false,
            title: "Bridgewater Associates Founder",
            description: "Macro investing pioneer. Built the world's largest hedge fund with radical transparency."
        ),
        TrendingWhale(
            name: "Michael Burry",
            category: .hedgeFunds,
            avatarName: "avatar_burry",
            followersCount: 98000,
            isFollowing: false,
            title: "Scion Asset Management",
            description: "The Big Short. Legendary contrarian known for calling the 2008 housing crisis."
        )
    ]

    // Combined for backward compatibility
    static let sampleData: [TrendingWhale] = trackedWhalesData + popularWhalesData
}
