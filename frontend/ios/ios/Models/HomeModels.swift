//
//  HomeModels.swift
//  ios
//
//  Data models for the Home screen
//
//  All models conform to Codable for API JSON decoding.
//  CodingKeys use snake_case raw values to match the backend.
//  Manual inits are preserved for backward compatibility (mock data, other screens).
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

    /// Flexible ISO 8601 parser that handles both Z and +00:00 offsets,
    /// with or without fractional seconds.
    static func parseISO8601(_ string: String) -> Date? {
        let fmt = ISO8601DateFormatter()
        // Try standard first
        fmt.formatOptions = [.withInternetDateTime]
        if let d = fmt.date(from: string) { return d }
        // Try with fractional seconds (Supabase timestamps)
        fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fmt.date(from: string)
    }
}

// MARK: - Market Ticker Type
enum MarketTickerType: String, Codable, Hashable {
    case index      // S&P 500, Nasdaq, Dow Jones → IndexDetailView
    case stock      // Individual stocks → TickerDetailView
    case crypto     // Bitcoin, Ethereum → CryptoDetailView
    case commodity  // Gold, Oil, Silver → CommodityDetailView
    case etf        // ETFs → ETFDetailView
}

// MARK: - Market Ticker
struct MarketTicker: Identifiable, Hashable, Codable {
    let id: UUID
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

    // MARK: Codable
    enum CodingKeys: String, CodingKey {
        case name, symbol, type, price
        case changePercent = "change_percent"
        case sparklineData = "sparkline_data"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.id = UUID()
        self.name = try c.decode(String.self, forKey: .name)
        self.symbol = try c.decode(String.self, forKey: .symbol)
        self.type = try c.decode(MarketTickerType.self, forKey: .type)
        self.price = try c.decode(Double.self, forKey: .price)
        self.changePercent = try c.decode(Double.self, forKey: .changePercent)
        self.sparklineData = try c.decode([Double].self, forKey: .sparklineData)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(name, forKey: .name)
        try c.encode(symbol, forKey: .symbol)
        try c.encode(type, forKey: .type)
        try c.encode(price, forKey: .price)
        try c.encode(changePercent, forKey: .changePercent)
        try c.encode(sparklineData, forKey: .sparklineData)
    }

    // Manual init (backward compat)
    init(name: String, symbol: String, type: MarketTickerType, price: Double, changePercent: Double, sparklineData: [Double]) {
        self.id = UUID()
        self.name = name
        self.symbol = symbol
        self.type = type
        self.price = price
        self.changePercent = changePercent
        self.sparklineData = sparklineData
    }
}

// MARK: - Market Sentiment
enum MarketSentiment: String, Codable {
    case bullish = "Bullish"
    case bearish = "Bearish"
    case neutral = "Neutral"
}

// MARK: - Market Insight
struct MarketInsight: Identifiable, Codable {
    let id: UUID
    let headline: String
    let bulletPoints: [String]
    let sentiment: MarketSentiment
    let updatedAt: Date

    var timeAgo: String {
        SharedFormatters.relativeDateFormatter.localizedString(for: updatedAt, relativeTo: Date())
    }

    // MARK: Codable
    enum CodingKeys: String, CodingKey {
        case headline
        case bulletPoints = "bullet_points"
        case sentiment
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.id = UUID()
        self.headline = try c.decode(String.self, forKey: .headline)
        self.bulletPoints = try c.decode([String].self, forKey: .bulletPoints)
        self.sentiment = try c.decode(MarketSentiment.self, forKey: .sentiment)
        // Decode date from ISO 8601 string (more flexible than .iso8601 strategy)
        let dateString = try c.decode(String.self, forKey: .updatedAt)
        self.updatedAt = SharedFormatters.parseISO8601(dateString) ?? Date()
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(headline, forKey: .headline)
        try c.encode(bulletPoints, forKey: .bulletPoints)
        try c.encode(sentiment, forKey: .sentiment)
        let fmt = ISO8601DateFormatter()
        try c.encode(fmt.string(from: updatedAt), forKey: .updatedAt)
    }

    // Manual init (backward compat)
    init(headline: String, bulletPoints: [String], sentiment: MarketSentiment, updatedAt: Date) {
        self.id = UUID()
        self.headline = headline
        self.bulletPoints = bulletPoints
        self.sentiment = sentiment
        self.updatedAt = updatedAt
    }
}

// MARK: - Alert Type
enum AlertType: String, Codable {
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
struct DailyBriefingItem: Identifiable, Codable {
    let id: UUID
    let type: AlertType
    let title: String
    let subtitle: String
    let date: Date?
    let badgeText: String?

    var hasDateBadge: Bool {
        date != nil && badgeText != nil
    }

    // MARK: Codable
    enum CodingKeys: String, CodingKey {
        case type, title, subtitle, date
        case badgeText = "badge_text"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.id = UUID()
        self.type = try c.decode(AlertType.self, forKey: .type)
        self.title = try c.decode(String.self, forKey: .title)
        self.subtitle = try c.decode(String.self, forKey: .subtitle)
        // Flexible date parsing from ISO string or null
        if let dateString = try c.decodeIfPresent(String.self, forKey: .date) {
            self.date = SharedFormatters.parseISO8601(dateString)
        } else {
            self.date = nil
        }
        self.badgeText = try c.decodeIfPresent(String.self, forKey: .badgeText)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
        try c.encode(title, forKey: .title)
        try c.encode(subtitle, forKey: .subtitle)
        if let d = date {
            let fmt = ISO8601DateFormatter()
            try c.encode(fmt.string(from: d), forKey: .date)
        } else {
            try c.encodeNil(forKey: .date)
        }
        try c.encodeIfPresent(badgeText, forKey: .badgeText)
    }

    // Manual init (backward compat)
    init(type: AlertType, title: String, subtitle: String, date: Date?, badgeText: String?) {
        self.id = UUID()
        self.type = type
        self.title = title
        self.subtitle = subtitle
        self.date = date
        self.badgeText = badgeText
    }
}

// MARK: - Investor Persona
/// Analysis styles shown on the Home "recent research" feed.
///
/// These were named after real living investors (Warren Buffett, Cathie Wood, …).
/// Naming a commercial product feature after a living person creates right-of-publicity
/// and false-endorsement exposure, so the labels are now style names. The backend's
/// stable `key` / `agent_tag` identifiers did NOT change — only these labels.
///
/// The wire value is the DISPLAY NAME (see `RecentResearchResponse.persona`), so the
/// legacy names must stay decodable: migrations are applied by hand, so there is a
/// window where the deployed backend still serves the old labels from `agent_personas`.
/// Without the mapping below every report in that window would silently mislabel as
/// the first case.
enum InvestorPersona: String, CaseIterable, Codable {
    case qualityCompounder = "The Quality Compounder"
    case everydayGrowthHunter = "The Everyday Growth Hunter"
    case disruptionSeeker = "The Disruption Seeker"
    case activistConcentrator = "The Activist Concentrator"
    case deepValueSkeptic = "The Deep Value Skeptic"

    /// Pre-rename labels, mapped to their current case.
    private static let legacyNames: [String: InvestorPersona] = [
        "Warren Buffett": .qualityCompounder,
        "Peter Lynch": .everydayGrowthHunter,
        "Cathie Wood": .disruptionSeeker,
        "Bill Ackman": .activistConcentrator,
        "Michael Burry": .deepValueSkeptic
    ]

    /// Also accepts the backend `key` form, in case a caller sends that instead.
    private static let backendKeys: [String: InvestorPersona] = [
        "warren_buffett": .qualityCompounder,
        "peter_lynch": .everydayGrowthHunter,
        "cathie_wood": .disruptionSeeker,
        "bill_ackman": .activistConcentrator,
        "michael_burry": .deepValueSkeptic
    ]

    /// `rawValue` (the synthesized getter) is the single source for the canonical
    /// string — no second copy of the names to drift out of sync. Safe from recursion:
    /// `allCases` and `rawValue` are both synthesized and never call this initializer.
    init?(rawValue: String) {
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if let match = Self.allCases.first(where: { $0.rawValue == trimmed }) {
            self = match
        } else if let legacy = Self.legacyNames[trimmed] {
            self = legacy
        } else if let byKey = Self.backendKeys[trimmed.lowercased()] {
            self = byKey
        } else {
            return nil
        }
    }

    var displayName: String {
        rawValue
    }

    var badgeColor: String {
        switch self {
        case .qualityCompounder: return "4F46E5"
        case .everydayGrowthHunter: return "059669"
        case .disruptionSeeker: return "DC2626"
        case .activistConcentrator: return "DC2626"
        case .deepValueSkeptic: return "991B1B"
        }
    }
}

// MARK: - Research Report
struct ResearchReport: Identifiable, Codable {
    let id: UUID
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

    // MARK: Codable
    enum CodingKeys: String, CodingKey {
        case id
        case stockTicker = "stock_ticker"
        case stockName = "stock_name"
        case companyLogoName = "company_logo_name"
        case persona, headline, summary, rating
        case fairValue = "fair_value"
        case createdAt = "created_at"
        case gradientColors = "gradient_colors"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // Backend sends UUID string — parse it; fall back to random UUID
        if let idString = try c.decodeIfPresent(String.self, forKey: .id),
           let uuid = UUID(uuidString: idString) {
            self.id = uuid
        } else {
            self.id = UUID()
        }
        self.stockTicker = try c.decode(String.self, forKey: .stockTicker)
        self.stockName = try c.decode(String.self, forKey: .stockName)
        self.companyLogoName = try c.decode(String.self, forKey: .companyLogoName)
        // Tolerant: an unknown/newly-added persona display name must NOT throw —
        // a single un-decodable element aborts the WHOLE home-feed array decode and
        // collapses the entire feed to mock data. Default rather than crash.
        self.persona = (try? c.decode(InvestorPersona.self, forKey: .persona)) ?? .qualityCompounder
        self.headline = try c.decode(String.self, forKey: .headline)
        self.summary = try c.decode(String.self, forKey: .summary)
        self.rating = try c.decode(Double.self, forKey: .rating)
        self.fairValue = try c.decode(Double.self, forKey: .fairValue)
        let dateString = try c.decode(String.self, forKey: .createdAt)
        self.createdAt = SharedFormatters.parseISO8601(dateString) ?? Date()
        self.gradientColors = try c.decode([String].self, forKey: .gradientColors)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id.uuidString, forKey: .id)
        try c.encode(stockTicker, forKey: .stockTicker)
        try c.encode(stockName, forKey: .stockName)
        try c.encode(companyLogoName, forKey: .companyLogoName)
        try c.encode(persona, forKey: .persona)
        try c.encode(headline, forKey: .headline)
        try c.encode(summary, forKey: .summary)
        try c.encode(rating, forKey: .rating)
        try c.encode(fairValue, forKey: .fairValue)
        let fmt = ISO8601DateFormatter()
        try c.encode(fmt.string(from: createdAt), forKey: .createdAt)
        try c.encode(gradientColors, forKey: .gradientColors)
    }

    // Manual init (backward compat)
    init(stockTicker: String, stockName: String, companyLogoName: String, persona: InvestorPersona, headline: String, summary: String, rating: Double, fairValue: Double, createdAt: Date, gradientColors: [String]) {
        self.id = UUID()
        self.stockTicker = stockTicker
        self.stockName = stockName
        self.companyLogoName = companyLogoName
        self.persona = persona
        self.headline = headline
        self.summary = summary
        self.rating = rating
        self.fairValue = fairValue
        self.createdAt = createdAt
        self.gradientColors = gradientColors
    }
}

// MARK: - Home Feed Response (API DTO)
struct HomeFeedResponse: Codable {
    let marketTickers: [MarketTicker]
    let marketInsight: MarketInsight?
    let dailyBriefings: [DailyBriefingItem]
    let recentResearch: [ResearchReport]

    enum CodingKeys: String, CodingKey {
        case marketTickers = "market_tickers"
        case marketInsight = "market_insight"
        case dailyBriefings = "daily_briefings"
        case recentResearch = "recent_research"
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
