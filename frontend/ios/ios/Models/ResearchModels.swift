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

/// Persona used to drive AI research. Originally an enum; now a struct so the
/// list can be hot-swapped from the backend `agent_personas` table without an
/// app release. Static instances + `allCases` preserve enum-style ergonomics.
struct AnalysisPersona: Identifiable, Hashable {
    let key: String           // snake_case backend key (e.g. "warren_buffett")
    let name: String          // display name (e.g. "Warren Buffett")
    let tagline: String
    let iconName: String
    let systemIconName: String
    let accentColorHex: String
    let description: String

    var id: String { key }

    /// Backwards-compat alias — many call-sites use `persona.rawValue`.
    var rawValue: String { name }

    /// Backwards-compat alias — call-sites used `persona.backendKey`.
    var backendKey: String { key }

    /// Short "running" label, e.g. "Quality Agent" — shown while a report is processing.
    ///
    /// Keyed on `key`, NOT derived from `name`. `name` is now a style name, so the old
    /// `name.split(" ").last + " Agent"` produced "Compounder Agent" while the backend
    /// sent "Quality Agent" — a visible mismatch. Mirrors
    /// persona_config.PersonaConfig.agent_label_text; keep the two in sync.
    var agentLabel: String {
        "\(shortName) Agent"
    }

    /// Short tag label, e.g. "Quality". Mirrors the backend's agent_label stem.
    var shortName: String {
        switch key {
        case "warren_buffett": return "Quality"
        case "cathie_wood":    return "Disruption"
        case "peter_lynch":    return "GARP"
        case "bill_ackman":    return "Activist"
        case "michael_burry":  return "Contrarian"
        default:
            // Unknown key (a persona added backend-first): fall back to the last word
            // of the display name rather than showing nothing.
            return name.split(separator: " ").last.map(String.init) ?? name
        }
    }

    /// The display name split for the two-line persona card, with the leading article
    /// dropped: "The Quality Compounder" → ("Quality", "Compounder"); "The Everyday
    /// Growth Hunter" → ("Everyday Growth", "Hunter").
    ///
    /// PersonaCard used to split `name` itself into first-word / last-word, which read
    /// correctly while names were real people ("Warren" / "Buffett") and became
    /// "The" / "Compounder" once the personas were renamed to style names.
    var cardNameLines: (top: String, bottom: String) {
        var words = name.split(separator: " ").map(String.init)
        if words.count > 1, words[0].caseInsensitiveCompare("the") == .orderedSame {
            words.removeFirst()
        }
        guard let last = words.last else { return ("", name) }
        return (words.dropLast().joined(separator: " "), last)
    }

    /// Lens word for the persona-aware headline label ("Strong <lens> Profile").
    /// Mirrors ReportAgentPersona.lensWord — keep the two in sync.
    var lensWord: String {
        switch key {
        case "cathie_wood":   return "Growth"
        case "peter_lynch":   return "GARP"
        case "bill_ackman":   return "Value"
        case "michael_burry": return "Contrarian"
        default:              return "Value"   // warren_buffett + unknown
        }
    }

    var accentColor: Color { Color(hex: accentColorHex) }

    static func == (lhs: AnalysisPersona, rhs: AnalysisPersona) -> Bool {
        lhs.key == rhs.key
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(key)
    }

    // ── Hardcoded fallbacks ──────────────────────────────────────────────
    // Used when the backend fetch fails so the UI still works offline.

    static let warrenBuffett = AnalysisPersona(
        key: "warren_buffett",
        name: "The Quality Compounder",
        tagline: "Safe, Long-term Value",
        iconName: "icon_persona_buffett",
        systemIconName: "building.columns.fill",
        accentColorHex: "3B82F6",
        description: "Focuses on fundamental value, strong moats, consistent earnings, and long-term competitive advantages. Ideal for conservative investors."
    )

    static let cathieWood = AnalysisPersona(
        key: "cathie_wood",
        name: "The Disruption Seeker",
        tagline: "Disruptive Innovation",
        iconName: "icon_persona_wood",
        systemIconName: "bolt.fill",
        accentColorHex: "A855F7",
        description: "Emphasizes disruptive innovation, emerging technologies, and high-growth potential companies that could reshape industries."
    )

    static let peterLynch = AnalysisPersona(
        key: "peter_lynch",
        name: "The Everyday Growth Hunter",
        tagline: "Growth at a Reasonable Price",
        iconName: "icon_persona_lynch",
        systemIconName: "chart.line.uptrend.xyaxis",
        accentColorHex: "06B6D4",
        description: "Looks for growth at a reasonable price (GARP), with focus on companies you understand and can spot in everyday life."
    )

    static let billAckman = AnalysisPersona(
        key: "bill_ackman",
        name: "The Activist Concentrator",
        tagline: "Activist Value",
        iconName: "icon_persona_ackman",
        systemIconName: "megaphone.fill",
        accentColorHex: "F97316",
        description: "Takes concentrated positions in high-quality businesses, uses activist strategies to unlock value, and focuses on companies with durable competitive advantages."
    )

    static let michaelBurry = AnalysisPersona(
        key: "michael_burry",
        name: "The Deep Value Skeptic",
        tagline: "Contrarian Deep Value",
        iconName: "icon_persona_burry",
        systemIconName: "magnifyingglass",
        accentColorHex: "DC2626",
        description: "A contrarian skeptic who hunts deeply undervalued, out-of-favor companies with a large margin of safety, scrutinizes the balance sheet for hidden risk, and is wary of hype, crowded trades, and expensive darlings."
    )

    static let allCases: [AnalysisPersona] = [
        .warrenBuffett, .cathieWood, .peterLynch, .billAckman, .michaelBurry
    ]

    static let fallbacks: [AnalysisPersona] = allCases

    /// The user's "Default Analyst" (Settings → AI & Research), stored by `.key`.
    /// Falls back to Warren Buffett when unset or unrecognized.
    static var settingsDefault: AnalysisPersona {
        let storedKey = UserDefaults.standard.string(forKey: "default_persona") ?? warrenBuffett.key
        return allCases.first { $0.key == storedKey } ?? warrenBuffett
    }

    /// Build an `AnalysisPersona` from a backend `agent_personas` row.
    /// Falls back to the matching hardcoded persona's fields when the backend
    /// returns null/empty for icon/color/description.
    static func from(_ backend: BackendPersona) -> AnalysisPersona {
        let fallback = allCases.first { $0.key == backend.key }
        return AnalysisPersona(
            key: backend.key,
            name: backend.name,
            tagline: backend.tagline ?? fallback?.tagline ?? "",
            iconName: fallback?.iconName ?? "icon_persona_default",
            systemIconName: backend.iconName?.nonEmpty
                ?? fallback?.systemIconName
                ?? "person.crop.circle",
            accentColorHex: backend.accentColor?.nonEmpty
                ?? fallback?.accentColorHex
                ?? "3B82F6",
            description: backend.description ?? fallback?.description ?? ""
        )
    }
}

// MARK: - Backend Persona DTO

struct BackendPersona: Decodable, Sendable {
    let id: String
    let key: String
    let name: String
    let tagline: String?
    let description: String?
    let iconName: String?
    let accentColor: String?
    let isActive: Bool?

    enum CodingKeys: String, CodingKey {
        case id, key, name, tagline, description
        case iconName = "icon_name"
        case accentColor = "accent_color"
        case isActive = "is_active"
    }
}

private extension String {
    var nonEmpty: String? { isEmpty ? nil : self }
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
            title: "Hidden Market Signals",
            subtitle: "Smart money flows with congressional trading activity and short positions",
            iconName: "icon_feature_signals",
            systemIconName: "antenna.radiowaves.left.and.right",
            iconColor: Color(hex: "14B8A6")
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

    /// PREVIEW ONLY. No shipping code path may fall back to this — a fabricated
    /// balance shown as if real is worse than showing none (it contradicted the user's
    /// actual balance on two other tabs). Both `LearnView` and `ResearchViewModel` now
    /// render nothing until a real balance arrives.
    static let mock = CreditBalance(
        credits: 47,
        renewalDate: Calendar.current.date(byAdding: .month, value: 1, to: Date()) ?? Date()
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

    static let standard = AnalysisCost(credits: 20)
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
struct AnalysisReport: Identifiable, Hashable {
    let id = UUID()
    /// Backend `research_reports.id` UUID. Optional because mock data
    /// (and any UI-only AnalysisReport) won't have one. Used by
    /// TickerReportViewModel to fetch the cached `ticker_report_data`
    /// JSONB instead of regenerating via /stocks/{ticker}/report.
    let backendId: String?
    let companyName: String
    let ticker: String
    let industry: String
    let persona: AnalysisPersona
    let status: ReportStatus
    var progress: Double? // 0.0 to 1.0, only for processing
    /// Live progress text from `research_reports.current_step` (e.g.
    /// "Analyzing fundamentals..."). Rendered under the progress bar
    /// while a report is in-flight. `var` so the live generation stream
    /// can mirror its real-time progress onto this row (the list query lags).
    var currentStep: String?
    let rating: Double?   // 0-100, only for ready
    let ratingLabel: String? // e.g. "Strong Quality Business", only for ready
    let date: Date
    let isRefunded: Bool
    /// Credits ACTUALLY charged for THIS row (5 pre-migration, 20 now). Drives the refund
    /// chip's amount so an old row shows its real refund, not a hardcoded constant. Optional
    /// for decode resilience + mock/UI-only reports (the view falls back to the current cost).
    var creditsCharged: Int? = nil

    var formattedDate: String {
        ResearchFormatters.mediumDateFormatter.string(from: date)
    }

    var tickerAndIndustry: String {
        industry.isEmpty ? ticker : "\(ticker) • \(industry)"
    }

    var progressPercent: Int {
        Int((progress ?? 0) * 100)
    }

    /// Copy with status flipped to `.failed` for the client-side timeout path.
    /// Other fields pass through unchanged so the card identity (ticker /
    /// persona / date) stays stable across the transition.
    func withClientTimeout() -> AnalysisReport {
        AnalysisReport(
            backendId: backendId,
            companyName: companyName,
            ticker: ticker,
            industry: industry,
            persona: persona,
            status: .failed,
            progress: progress,
            currentStep: "Service temporarily unavailable. Tap Retry to try again.",
            rating: rating,
            ratingLabel: ratingLabel,
            date: date,
            isRefunded: isRefunded,
            creditsCharged: creditsCharged
        )
    }

    static func == (lhs: AnalysisReport, rhs: AnalysisReport) -> Bool {
        lhs.id == rhs.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }

    static let mockReports: [AnalysisReport] = [
        AnalysisReport(
            backendId: nil,
            companyName: "Oracle Corporation",
            ticker: "ORCL",
            industry: "Enterprise Software",
            persona: .warrenBuffett,
            status: .ready,
            progress: nil,
            currentStep: nil,
            rating: 82,
            ratingLabel: "Strong Quality Business",
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 2, day: 7)) ?? Date(),
            isRefunded: false
        ),
        AnalysisReport(
            backendId: nil,
            companyName: "Tesla Inc.",
            ticker: "TSLA",
            industry: "Automotive",
            persona: .cathieWood,
            status: .processing,
            progress: 0.67,
            currentStep: "Analyzing fundamentals...",
            rating: nil,
            ratingLabel: nil,
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 2, day: 20)) ?? Date(),
            isRefunded: false
        ),
        AnalysisReport(
            backendId: nil,
            companyName: "Meta Platforms",
            ticker: "META",
            industry: "Social Media",
            persona: .billAckman,
            status: .failed,
            progress: nil,
            currentStep: nil,
            rating: nil,
            ratingLabel: nil,
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 2, day: 19)) ?? Date(),
            isRefunded: true
        ),
        AnalysisReport(
            backendId: nil,
            companyName: "Apple Inc.",
            ticker: "AAPL",
            industry: "Technology",
            persona: .warrenBuffett,
            status: .ready,
            progress: nil,
            currentStep: nil,
            rating: 90,
            ratingLabel: "Excellent Quality Business",
            date: Calendar.current.date(from: DateComponents(year: 2024, month: 12, day: 24)) ?? Date(),
            isRefunded: false
        ),
        AnalysisReport(
            backendId: nil,
            companyName: "NVIDIA Corp.",
            ticker: "NVDA",
            industry: "Semiconductors",
            persona: .peterLynch,
            status: .ready,
            progress: nil,
            currentStep: nil,
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

// MARK: - Report Time Section
/// Time bands used to group the Reports list for scannability. `allCases`
/// order IS the display order (newest → oldest). Bucketing uses lower date
/// cutoffs so the bands tile the timeline with no gap and no overlap: the
/// "last 7 days" rolling window and the "previous calendar month" band would
/// otherwise leave the 8–30-day region homeless — `.lastMonth` absorbs it.
enum ReportTimeSection: String, CaseIterable {
    case recent    = "RECENT"      // within the last 7 days (rolling)
    case lastMonth = "LAST MONTH"  // older than Recent, on/after the 1st of the previous calendar month
    case older     = "OLDER"       // everything else

    /// Classify a report date. `now` / `calendar` are injectable for tests.
    static func bucket(for date: Date, now: Date = Date(), calendar: Calendar = .current) -> ReportTimeSection {
        // Recent: within the last 7 days. Anchored to startOfDay so the window
        // is stable regardless of the current time-of-day (and future-dated
        // rows from clock skew fall here, which is correct).
        let startToday = calendar.startOfDay(for: now)
        if let recentCutoff = calendar.date(byAdding: .day, value: -7, to: startToday), date >= recentCutoff {
            return .recent
        }
        // Last Month: older than Recent but on/after the 1st of the previous
        // calendar month (absorbs the 8–30-day "gap" region).
        let comps = calendar.dateComponents([.year, .month], from: now)
        if let firstThisMonth = calendar.date(from: comps),
           let firstPrevMonth = calendar.date(byAdding: .month, value: -1, to: firstThisMonth),
           date >= firstPrevMonth {
            return .lastMonth
        }
        return .older
    }
}

/// One time-grouped section of the Reports list. A struct (not a tuple) so it's
/// `Identifiable` — SwiftUI `ForEach` can't key off a tuple element.
struct ReportSectionGroup: Identifiable {
    let section: ReportTimeSection
    let reports: [AnalysisReport]
    var id: ReportTimeSection { section }
}

// MARK: - Backend → UI Mapping

extension AnalysisReport {
    /// Map a backend report list item to a local AnalysisReport for UI.
    static func from(_ item: BackendReportListItem) -> AnalysisReport {
        // Look up persona by backend key; default to first fallback if unknown.
        let persona = AnalysisPersona.allCases.first {
            $0.key == item.investorPersona
        } ?? .warrenBuffett

        // Map backend status string to enum
        let status: ReportStatus = {
            switch item.status {
            case "completed": return .ready
            case "failed": return .failed
            default: return .processing // "pending" or "processing"
            }
        }()

        // Parse ISO 8601 date
        let date: Date = {
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let d = formatter.date(from: item.createdAt) { return d }
            // Retry without fractional seconds
            formatter.formatOptions = [.withInternetDateTime]
            return formatter.date(from: item.createdAt) ?? Date()
        }()

        // Progress: backend sends 0-100 int, UI expects 0.0-1.0 Double
        let progress: Double? = {
            if status == .processing, let p = item.progress {
                return Double(p) / 100.0
            }
            return nil
        }()

        // Rating label from overall_score — route through QualityBand (the single
        // source of truth) so the list row and the report gauge can never disagree.
        // (Previously this used its own 81/61/41 cutoffs + different labels, so the
        // same score showed one band on the list and another on the report.)
        let ratingLabel: String? = {
            guard let score = item.overallScore else { return nil }
            return QualityBand.forScore(Int(score.rounded())).profileLabel(lens: persona.lensWord)
        }()

        return AnalysisReport(
            backendId: item.id,
            companyName: item.companyName ?? item.ticker,
            ticker: item.ticker,
            industry: item.industry ?? "",
            persona: persona,
            status: status,
            progress: progress,
            currentStep: item.currentStep,
            rating: item.overallScore,
            ratingLabel: ratingLabel,
            date: date,
            isRefunded: item.isRefunded ?? false,
            creditsCharged: item.creditsCharged
        )
    }
}

extension CreditBalance {
    /// Map backend credits response to CreditBalance.
    static func from(_ response: BackendCreditsResponse) -> CreditBalance {
        CreditBalance(
            credits: response.remaining,
            renewalDate: Self.parseResetDate(response.resetsAt)
        )
    }

    /// Map `AppState.user.credits` — the app-wide single source of truth — to the
    /// display model. Lets any screen render the SAME balance without issuing its own
    /// `/users/me/credits` request (the Learn tab previously hardcoded a fabricated
    /// one instead, which then disagreed with Research and Profile).
    static func from(_ info: CreditInfo) -> CreditBalance {
        CreditBalance(
            credits: info.remaining,
            renewalDate: Self.parseResetDate(info.resetsAt)
        )
    }

    /// Shared ISO-8601 parsing for the reset timestamp. The backend emits it with and
    /// without fractional seconds depending on the row, so both are attempted; an
    /// unparseable or absent value falls back to one month out rather than to `Date()`
    /// (which would render as "Renews today" forever).
    static func parseResetDate(_ resetsAt: String?) -> Date {
        guard let resetsAt else {
            return Calendar.current.date(byAdding: .month, value: 1, to: Date()) ?? Date()
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = formatter.date(from: resetsAt) { return d }
        formatter.formatOptions = [.withInternetDateTime]
        if let d = formatter.date(from: resetsAt) { return d }
        return Calendar.current.date(byAdding: .month, value: 1, to: Date()) ?? Date()
    }
}

// MARK: - Backend Trending Analysis DTOs

/// Decodable DTO matching backend GET /research/trending response items.
struct BackendTrendingCompany: Decodable, Sendable {
    let ticker: String
    let name: String
    let price: String?
    let marketCap: String?

    enum CodingKeys: String, CodingKey {
        case ticker, name, price
        case marketCap = "market_cap"
    }
}

struct BackendTrendingAnalysis: Decodable, Sendable {
    let title: String
    let description: String
    let companies: [BackendTrendingCompany]
    let interestPercent: Int
    let systemIconName: String
    let iconBackgroundColor: String

    enum CodingKeys: String, CodingKey {
        case title, description, companies
        case interestPercent = "interest_percent"
        case systemIconName = "system_icon_name"
        case iconBackgroundColor = "icon_background_color"
    }
}

extension TrendingAnalysis {
    /// Map a backend trending analysis to a local TrendingAnalysis for UI.
    static func from(_ item: BackendTrendingAnalysis) -> TrendingAnalysis {
        let companies = item.companies.map { c in
            TrendingCompany(
                ticker: c.ticker,
                name: c.name,
                price: c.price ?? "",
                marketCap: c.marketCap ?? ""
            )
        }
        return TrendingAnalysis(
            title: item.title,
            description: item.description,
            companies: companies,
            interestPercent: item.interestPercent,
            iconName: "",
            systemIconName: item.systemIconName,
            iconBackgroundColor: Color(hex: item.iconBackgroundColor)
        )
    }
}
