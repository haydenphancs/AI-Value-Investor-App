//
//  ResearchModels.swift
//  ios
//
//  Data models for the Research screen
//

import Foundation
import SwiftUI

// MARK: - Research Tab
enum ResearchTab: String, CaseIterable {
    case research = "Research"
    case reports = "Reports"
}

// MARK: - Quick Ticker
struct QuickTicker: Identifiable, Equatable {
    let id = UUID()
    let symbol: String

    static let defaults: [QuickTicker] = [
        QuickTicker(symbol: "AAPL"),
        QuickTicker(symbol: "TSLA"),
        QuickTicker(symbol: "NVDA"),
        QuickTicker(symbol: "BTC")
    ]
}

// MARK: - Analysis Persona
enum AnalysisPersona: String, CaseIterable, Identifiable {
    case warrenBuffett = "Warren Buffett"
    case cathieWood = "Cathie Wood"
    case peterLynch = "Peter Lynch"
    case rayDalio = "Ray Dalio"
    case satoshiNakamoto = "Satoshi Nakamoto"

    var id: String { rawValue }

    var tagline: String {
        switch self {
        case .warrenBuffett: return "Safe, Long-term Value"
        case .cathieWood: return "Disruptive Innovation"
        case .peterLynch: return "Growth at Value"
        case .rayDalio: return "Risk Parity"
        case .satoshiNakamoto: return "Decentralized Sound Money"
        }
    }

    var iconName: String {
        switch self {
        case .warrenBuffett: return "icon_persona_buffett"
        case .cathieWood: return "icon_persona_wood"
        case .peterLynch: return "icon_persona_lynch"
        case .rayDalio: return "icon_persona_dalio"
        case .satoshiNakamoto: return "icon_persona_satoshi"
        }
    }

    var systemIconName: String {
        switch self {
        case .warrenBuffett: return "building.columns.fill"
        case .cathieWood: return "bolt.fill"
        case .peterLynch: return "chart.line.uptrend.xyaxis"
        case .rayDalio: return "circle.fill"
        case .satoshiNakamoto: return "bitcoinsign.circle.fill"
        }
    }

    var accentColor: Color {
        switch self {
        case .warrenBuffett: return Color(hex: "3B82F6") // Blue
        case .cathieWood: return Color(hex: "A855F7")    // Purple
        case .peterLynch: return Color(hex: "06B6D4")    // Cyan
        case .rayDalio: return Color(hex: "F97316")      // Orange
        case .satoshiNakamoto: return Color(hex: "F97316") // Orange
        }
    }

    var description: String {
        switch self {
        case .warrenBuffett:
            return "Focuses on fundamental value, strong moats, consistent earnings, and long-term competitive advantages. Ideal for conservative investors."
        case .cathieWood:
            return "Emphasizes disruptive innovation, emerging technologies, and high-growth potential companies that could reshape industries."
        case .peterLynch:
            return "Looks for growth at a reasonable price (GARP), with focus on companies you understand and can spot in everyday life."
        case .rayDalio:
            return "Applies risk parity principles, diversification across asset classes, and systematic macro-economic analysis."
        case .satoshiNakamoto:
            return "Focuses on decentralized assets, sound money principles, and blockchain-native investments with long-term store of value potential."
        }
    }
}

// MARK: - Analysis Feature
struct AnalysisFeature: Identifiable {
    let id = UUID()
    let title: String
    let subtitle: String
    let iconName: String
    let systemIconName: String
    let iconColor: Color

    static let allFeatures: [AnalysisFeature] = [
        AnalysisFeature(
            title: "Financial Deep Dive",
            subtitle: "Year historical analysis, ratios, and trend forecasting",
            iconName: "icon_feature_financial",
            systemIconName: "chart.pie.fill",
            iconColor: Color(hex: "22C55E")
        ),
        AnalysisFeature(
            title: "Competitive Position",
            subtitle: "Market share analysis and moat assessment",
            iconName: "icon_feature_competitive",
            systemIconName: "building.2.fill",
            iconColor: Color(hex: "3B82F6")
        ),
        AnalysisFeature(
            title: "AI-Powered Insights",
            subtitle: "Pattern recognition, anomaly detection and beyond!",
            iconName: "icon_feature_ai",
            systemIconName: "sparkles",
            iconColor: Color(hex: "F97316")
        ),
        AnalysisFeature(
            title: "Risk Assessment",
            subtitle: "Comprehensive risk factors and mitigation strategies",
            iconName: "icon_feature_risk",
            systemIconName: "exclamationmark.triangle.fill",
            iconColor: Color(hex: "EF4444")
        ),
        AnalysisFeature(
            title: "Insiders and Whales",
            subtitle: "Track big moves, new opens, increase or decrease their positions",
            iconName: "icon_feature_whales",
            systemIconName: "person.2.fill",
            iconColor: Color(hex: "06B6D4")
        )
    ]
}

// MARK: - Credit Balance
struct CreditBalance {
    let credits: Int
    let renewalDate: Date

    var formattedRenewalDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d, yyyy"
        return "Renews \(formatter.string(from: renewalDate))"
    }

    static let mock = CreditBalance(
        credits: 47,
        renewalDate: Calendar.current.date(from: DateComponents(year: 2025, month: 1, day: 1)) ?? Date()
    )
}

// MARK: - Trending Analysis
struct TrendingAnalysis: Identifiable {
    let id = UUID()
    let title: String
    let companiesCount: Int
    let interestPercent: Int
    let iconName: String
    let systemIconName: String
    let iconBackgroundColor: Color

    var formattedCompaniesCount: String {
        "\(companiesCount) companies analyzed this month"
    }

    var formattedInterest: String {
        "+\(interestPercent)% interest"
    }

    static let mockTrending: [TrendingAnalysis] = [
        TrendingAnalysis(
            title: "AI & Machine Learning Stocks",
            companiesCount: 12,
            interestPercent: 127,
            iconName: "icon_trending_ai",
            systemIconName: "brain.head.profile",
            iconBackgroundColor: Color(hex: "3B82F6")
        ),
        TrendingAnalysis(
            title: "Clean Energy Sector",
            companiesCount: 8,
            interestPercent: 89,
            iconName: "icon_trending_energy",
            systemIconName: "bolt.fill",
            iconBackgroundColor: Color(hex: "22C55E")
        ),
        TrendingAnalysis(
            title: "Cryptocurrency & Blockchain",
            companiesCount: 15,
            interestPercent: 203,
            iconName: "icon_trending_crypto",
            systemIconName: "bitcoinsign.circle.fill",
            iconBackgroundColor: Color(hex: "F97316")
        )
    ]
}

// MARK: - Analysis Cost
struct AnalysisCost {
    let credits: Int

    static let standard = AnalysisCost(credits: 5)
}

// MARK: - Report Status
enum ReportStatus: String {
    case processing = "Processing"
    case failed = "Failed"
    case ready = "Ready"

    var color: Color {
        switch self {
        case .processing: return Color(hex: "3B82F6")  // Blue
        case .failed: return Color(hex: "EF4444")       // Red
        case .ready: return Color(hex: "22C55E")        // Green
        }
    }

    var backgroundColor: Color {
        switch self {
        case .processing: return Color(hex: "3B82F6").opacity(0.2)
        case .failed: return Color(hex: "EF4444").opacity(0.2)
        case .ready: return Color(hex: "22C55E").opacity(0.2)
        }
    }
}

// MARK: - Analysis Report
struct AnalysisReport: Identifiable {
    let id = UUID()
    let companyName: String
    let ticker: String
    let industry: String
    let persona: AnalysisPersona
    let status: ReportStatus
    let progress: Double? // 0.0 to 1.0, only for processing
    let rating: Double?   // 0.0 to 5.0, only for ready
    let ratingLabel: String? // e.g. "Good quality business", only for ready
    let date: Date
    let isRefunded: Bool

    var formattedDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d, yyyy"
        return formatter.string(from: date)
    }

    var tickerAndIndustry: String {
        "\(ticker) • \(industry)"
    }

    var progressPercent: Int {
        Int((progress ?? 0) * 100)
    }

    static let mockReports: [AnalysisReport] = [
        AnalysisReport(
            companyName: "Oracle Corporation",
            ticker: "ORCL",
            industry: "Enterprise Software",
            persona: .warrenBuffett,
            status: .ready,
            progress: nil,
            rating: 4.2,
            ratingLabel: "Good quality business",
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 2, day: 7)) ?? Date(),
            isRefunded: false
        ),
        AnalysisReport(
            companyName: "Tesla Inc.",
            ticker: "TSLA",
            industry: "Automotive",
            persona: .cathieWood,
            status: .processing,
            progress: 0.67,
            rating: nil,
            ratingLabel: nil,
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 2, day: 20)) ?? Date(),
            isRefunded: false
        ),
        AnalysisReport(
            companyName: "Meta Platforms",
            ticker: "META",
            industry: "Social Media",
            persona: .rayDalio,
            status: .failed,
            progress: nil,
            rating: nil,
            ratingLabel: nil,
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 2, day: 19)) ?? Date(),
            isRefunded: true
        ),
        AnalysisReport(
            companyName: "Apple Inc.",
            ticker: "AAPL",
            industry: "Technology",
            persona: .warrenBuffett,
            status: .ready,
            progress: nil,
            rating: 4.5,
            ratingLabel: "Excellent quality business",
            date: Calendar.current.date(from: DateComponents(year: 2024, month: 12, day: 24)) ?? Date(),
            isRefunded: false
        ),
        AnalysisReport(
            companyName: "NVIDIA Corp.",
            ticker: "NVDA",
            industry: "Semiconductors",
            persona: .peterLynch,
            status: .ready,
            progress: nil,
            rating: 5.0,
            ratingLabel: "Outstanding quality business",
            date: Calendar.current.date(from: DateComponents(year: 2024, month: 12, day: 23)) ?? Date(),
            isRefunded: false
        )
    ]
}

// MARK: - Community Insight
struct CommunityInsight: Identifiable {
    let id = UUID()
    let userName: String
    let userAvatarName: String
    let postedAt: Date
    let comment: String
    let likesCount: Int
    let commentsCount: Int

    var timeAgo: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: postedAt, relativeTo: Date())
    }

    static let mockInsights: [CommunityInsight] = [
        CommunityInsight(
            userName: "David Martinez",
            userAvatarName: "avatar_david",
            postedAt: Date().addingTimeInterval(-7200), // 2h ago
            comment: "Just completed a Buffett-style analysis on $AAPL. The moat is stronger than ever with the services ecosystem. Highly recommend!",
            likesCount: 24,
            commentsCount: 8
        ),
        CommunityInsight(
            userName: "Sarah Johnson",
            userAvatarName: "avatar_sarah",
            postedAt: Date().addingTimeInterval(-18000), // 5h ago
            comment: "The Cathie Wood persona nailed the $NVDA analysis. AI infrastructure thesis is spot on. Worth every credit!",
            likesCount: 41,
            commentsCount: 15
        )
    ]
}

// MARK: - Report Sort Option
enum ReportSortOption: String, CaseIterable {
    case dateNewest = "Newest First"
    case dateOldest = "Oldest First"
    case ratingHigh = "Highest Rated"
    case ratingLow = "Lowest Rated"
}
