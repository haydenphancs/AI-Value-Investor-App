//
//  ResearchModels.swift
//  ios
//
//  Data models for the Research screen
//

import Foundation
import SwiftUI

// MARK: - Cached Formatters
private enum ResearchFormatters {
    static let mediumDateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MMM d, yyyy"
        return f
    }()
}

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
    case billAckman = "Bill Ackman"

    var id: String { rawValue }

    var tagline: String {
        switch self {
        case .warrenBuffett: return "Safe, Long-term Value"
        case .cathieWood: return "Disruptive Innovation"
        case .peterLynch: return "Growth at Value"
        case .billAckman: return "Activist Value"
        }
    }

    var iconName: String {
        switch self {
        case .warrenBuffett: return "icon_persona_buffett"
        case .cathieWood: return "icon_persona_wood"
        case .peterLynch: return "icon_persona_lynch"
        case .billAckman: return "icon_persona_ackman"
        }
    }

    var systemIconName: String {
        switch self {
        case .warrenBuffett: return "building.columns.fill"
        case .cathieWood: return "bolt.fill"
        case .peterLynch: return "chart.line.uptrend.xyaxis"
        case .billAckman: return "megaphone.fill"
        }
    }

    var accentColor: Color {
        switch self {
        case .warrenBuffett: return Color(hex: "3B82F6") // Blue
        case .cathieWood: return Color(hex: "A855F7")    // Purple
        case .peterLynch: return Color(hex: "06B6D4")    // Cyan
        case .billAckman: return Color(hex: "F97316")    // Orange
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
        case .billAckman:
            return "Takes concentrated positions in high-quality businesses, uses activist strategies to unlock value, and focuses on companies with durable competitive advantages."
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
            title: "Financials & Forecasts",
            subtitle: "Revenue engine, fundamentals, growth metrics, and future projections",
            iconName: "icon_feature_financial",
            systemIconName: "chart.line.uptrend.xyaxis",
            iconColor: Color(hex: "22C55E")
        ),
        AnalysisFeature(
            title: "Moat & Competitive Edge",
            subtitle: "Competitive position, pricing power, and macro risks",
            iconName: "icon_feature_competitive",
            systemIconName: "shield.fill",
            iconColor: Color(hex: "3B82F6")
        ),
        AnalysisFeature(
            title: "Insider & Wall Street",
            subtitle: "Insider activity, management quality, and analyst consensus",
            iconName: "icon_feature_whales",
            systemIconName: "person.2.fill",
            iconColor: Color(hex: "F97316")
        ),
        AnalysisFeature(
            title: "AI Chat with Report",
            subtitle: "Ask follow-up questions and get instant answers",
            iconName: "icon_feature_ai",
            systemIconName: "sparkles",
            iconColor: Color(hex: "A855F7")
        )
    ]
}

// MARK: - Credit Balance
struct CreditBalance {
    let credits: Int
    let renewalDate: Date

    var formattedRenewalDate: String {
        "Renews \(ResearchFormatters.mediumDateFormatter.string(from: renewalDate))"
    }

    static let mock = CreditBalance(
        credits: 47,
        renewalDate: Calendar.current.date(from: DateComponents(year: 2025, month: 1, day: 1)) ?? Date()
    )
}

// MARK: - Trending Analysis
struct TrendingCompany: Identifiable, Hashable {
    let id = UUID()
    let ticker: String
    let name: String
    let price: String
    let marketCap: String
}

struct TrendingAnalysis: Identifiable, Hashable {
    let id: UUID
    let title: String
    let description: String
    let companies: [TrendingCompany]
    let interestPercent: Int
    let iconName: String
    let systemIconName: String
    let iconBackgroundColor: Color

    init(title: String, description: String, companies: [TrendingCompany], interestPercent: Int, iconName: String, systemIconName: String, iconBackgroundColor: Color) {
        self.id = UUID()
        self.title = title
        self.description = description
        self.companies = companies
        self.interestPercent = interestPercent
        self.iconName = iconName
        self.systemIconName = systemIconName
        self.iconBackgroundColor = iconBackgroundColor
    }

    static func == (lhs: TrendingAnalysis, rhs: TrendingAnalysis) -> Bool {
        lhs.id == rhs.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }

    var companiesCount: Int { companies.count }

    var formattedCompaniesCount: String {
        "\(companiesCount) companies"
    }

    var formattedInterest: String {
        "+\(interestPercent)% interest"
    }

    static let mockTrending: [TrendingAnalysis] = [
        TrendingAnalysis(
            title: "AI & Machine Learning Stocks",
            description: "NVDA, MSFT, GOOGL leading the AI infrastructure boom with record data center spending",
            companies: [
                TrendingCompany(ticker: "NVDA", name: "NVIDIA Corp.", price: "$142.50", marketCap: "$3.5T"),
                TrendingCompany(ticker: "MSFT", name: "Microsoft Corp.", price: "$428.30", marketCap: "$3.2T"),
                TrendingCompany(ticker: "GOOGL", name: "Alphabet Inc.", price: "$178.90", marketCap: "$2.2T"),
                TrendingCompany(ticker: "AMD", name: "Advanced Micro Devices", price: "$164.20", marketCap: "$265B"),
                TrendingCompany(ticker: "PLTR", name: "Palantir Technologies", price: "$24.80", marketCap: "$54B"),
                TrendingCompany(ticker: "SNOW", name: "Snowflake Inc.", price: "$162.40", marketCap: "$53B"),
                TrendingCompany(ticker: "AI", name: "C3.ai Inc.", price: "$28.50", marketCap: "$3.5B"),
                TrendingCompany(ticker: "PATH", name: "UiPath Inc.", price: "$22.10", marketCap: "$12.5B"),
                TrendingCompany(ticker: "AMZN", name: "Amazon.com Inc.", price: "$186.40", marketCap: "$1.9T"),
                TrendingCompany(ticker: "META", name: "Meta Platforms Inc.", price: "$512.60", marketCap: "$1.3T"),
                TrendingCompany(ticker: "CRM", name: "Salesforce Inc.", price: "$272.80", marketCap: "$264B"),
                TrendingCompany(ticker: "ORCL", name: "Oracle Corp.", price: "$128.90", marketCap: "$351B"),
            ],
            interestPercent: 127,
            iconName: "icon_trending_ai",
            systemIconName: "brain.head.profile",
            iconBackgroundColor: Color(hex: "3B82F6")
        ),
        TrendingAnalysis(
            title: "Clean Energy Sector",
            description: "Solar and EV supply chains rebounding as global policy shifts accelerate adoption",
            companies: [
                TrendingCompany(ticker: "ENPH", name: "Enphase Energy Inc.", price: "$124.50", marketCap: "$16.8B"),
                TrendingCompany(ticker: "SEDG", name: "SolarEdge Technologies", price: "$68.30", marketCap: "$3.8B"),
                TrendingCompany(ticker: "FSLR", name: "First Solar Inc.", price: "$198.70", marketCap: "$21.2B"),
                TrendingCompany(ticker: "NEE", name: "NextEra Energy Inc.", price: "$76.40", marketCap: "$157B"),
                TrendingCompany(ticker: "TSLA", name: "Tesla Inc.", price: "$248.50", marketCap: "$792B"),
                TrendingCompany(ticker: "RIVN", name: "Rivian Automotive", price: "$16.20", marketCap: "$16.4B"),
                TrendingCompany(ticker: "PLUG", name: "Plug Power Inc.", price: "$3.85", marketCap: "$2.8B"),
                TrendingCompany(ticker: "BE", name: "Bloom Energy Corp.", price: "$14.60", marketCap: "$3.3B"),
            ],
            interestPercent: 89,
            iconName: "icon_trending_energy",
            systemIconName: "bolt.fill",
            iconBackgroundColor: Color(hex: "22C55E")
        ),
        TrendingAnalysis(
            title: "Quantum Computing",
            description: "IONQ, RGTI, QBTS gaining momentum as enterprise pilots move toward production",
            companies: [
                TrendingCompany(ticker: "IONQ", name: "IonQ Inc.", price: "$12.40", marketCap: "$2.7B"),
                TrendingCompany(ticker: "RGTI", name: "Rigetti Computing", price: "$1.85", marketCap: "$340M"),
                TrendingCompany(ticker: "QBTS", name: "D-Wave Quantum Inc.", price: "$1.20", marketCap: "$230M"),
                TrendingCompany(ticker: "IBM", name: "IBM Corp.", price: "$188.60", marketCap: "$172B"),
                TrendingCompany(ticker: "GOOGL", name: "Alphabet Inc.", price: "$178.90", marketCap: "$2.2T"),
                TrendingCompany(ticker: "HON", name: "Honeywell International", price: "$204.30", marketCap: "$134B"),
            ],
            interestPercent: 156,
            iconName: "icon_trending_quantum",
            systemIconName: "atom",
            iconBackgroundColor: Color(hex: "A855F7")
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
    let rating: Double?   // 0-100, only for ready
    let ratingLabel: String? // e.g. "Strong Quality Business", only for ready
    let date: Date
    let isRefunded: Bool

    var formattedDate: String {
        ResearchFormatters.mediumDateFormatter.string(from: date)
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
            rating: 82,
            ratingLabel: "Strong Quality Business",
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
            persona: .billAckman,
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
            rating: 90,
            ratingLabel: "Excellent Quality Business",
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
            rating: 95,
            ratingLabel: "Excellent Quality Business",
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
