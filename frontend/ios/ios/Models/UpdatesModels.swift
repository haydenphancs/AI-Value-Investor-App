//
//  UpdatesModels.swift
//  ios
//
//  Data models for the Updates/News screen
//

import Foundation

// MARK: - Shared Formatters
private enum UpdatesFormatters {
    static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "h:mm a"
        return f
    }()

    static let sectionDateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MMM d, yyyy"
        return f
    }()
}

// MARK: - News Sentiment
enum NewsSentiment: String, CaseIterable {
    case positive = "Positive"
    case negative = "Negative"
    case neutral = "Neutral"

    var displayName: String {
        rawValue
    }
}

// MARK: - News Filter Tab
struct NewsFilterTab: Identifiable, Equatable {
    let id = UUID()
    let title: String
    let ticker: String?
    let changePercent: Double?
    let isMarketTab: Bool
    /// Backend scope key — a ticker symbol, or `UpdatesScope.market`.
    var scope: String = UpdatesScope.market
    var companyName: String? = nil
    var logoURL: URL? = nil

    var isPositive: Bool {
        // Compare the ROUNDED value: -0.04 renders as "0.0%" but would be
        // flagged negative, painting a red "-0.0%" pill for a flat stock.
        (rounded ?? 0) >= 0
    }

    /// Change rounded to the precision actually displayed.
    private var rounded: Double? {
        guard let change = changePercent, change.isFinite else { return nil }
        return (change * 10).rounded() / 10
    }

    var formattedChange: String? {
        guard let change = rounded else { return nil }
        // `change` is already rounded, so `-0.0` cannot survive: it normalises to
        // 0.0 and takes the "+" branch. Formatting the RAW value here is what
        // produced the "-0.0%" seen for tiny negative moves.
        let sign = change >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", change))%"
    }

    /// Identity for selection preservation across refreshes. `id` is a fresh
    /// UUID on every rebuild, so keying selection on it silently reset the user
    /// back to the Market tab on every pull-to-refresh.
    static func == (lhs: NewsFilterTab, rhs: NewsFilterTab) -> Bool {
        lhs.scope == rhs.scope
    }
}

/// Backend scope keys for the Updates screen.
enum UpdatesScope {
    /// Reserved key for the general (non-ticker) market feed. Must match
    /// `MARKET_SCOPE` in backend/app/services/news_cache_service.py.
    static let market = "__MARKET__"
}

// MARK: - News Source
struct NewsSource: Identifiable, Hashable {
    let id = UUID()
    let name: String
    /// Legacy local-asset name. NOTE: none of the historically referenced
    /// `icon_*` assets exist in Assets.xcassets — prefer `logoURL`.
    let iconName: String?
    /// Publisher logo served by the backend (`source_logo_url`). Defaulted so
    /// every existing call site keeps compiling.
    var logoURL: URL? = nil

    /// Display name with domain suffixes like .com, .net, .org stripped
    var displayName: String {
        let suffixes = [".com", ".net", ".org", ".co", ".io", ".us", ".uk", ".ca", ".au"]
        var result = name
        for suffix in suffixes {
            if result.lowercased().hasSuffix(suffix) {
                result = String(result.dropLast(suffix.count))
                break
            }
        }
        return result
    }

    var systemIconName: String {
        "newspaper.fill"
    }
    
    // Hashable conformance based on id
    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
    
    static func == (lhs: NewsSource, rhs: NewsSource) -> Bool {
        lhs.id == rhs.id
    }
}

// MARK: - News Article
struct NewsArticle: Identifiable, Hashable {
    let id = UUID()
    let headline: String
    let summary: String?
    let source: NewsSource
    /// Optional on purpose. An article that has not been AI-enriched has NO
    /// sentiment; defaulting it to `.neutral` renders a confident-looking badge
    /// that no model produced. Views must hide the badge when this is nil.
    /// `var` so a later enrichment response can fill it in.
    var sentiment: NewsSentiment?
    let publishedAt: Date
    let thumbnailName: String?
    let relatedTickers: [String]

    // ── Backend-backed fields ──
    // All default so existing call sites (previews, other screens) still compile.

    /// Stable server id, used for enrichment requests. Empty for local/mock rows.
    var apiId: String = ""
    /// Remote thumbnail. `thumbnailName` is the legacy local-asset path.
    var imageURL: URL? = nil
    /// Canonical link to the publisher's story.
    var articleURL: URL? = nil
    /// AI summary bullets — these ARE the News Detail "Key Takeaways".
    var summaryBullets: [String] = []
    /// Whether the backend has run AI enrichment on this article.
    var aiProcessed: Bool = false

    /// Enrichable = has a real server id (not a `temp_`/`raw_` placeholder).
    var isEnrichable: Bool {
        !apiId.isEmpty
            && !apiId.hasPrefix("temp_")
            && !apiId.hasPrefix("raw_")
            && !apiId.hasPrefix("sample_")
    }

    var formattedTime: String {
        UpdatesFormatters.timeFormatter.string(from: publishedAt)
    }

    var isToday: Bool {
        Calendar.current.isDateInToday(publishedAt)
    }

    var isYesterday: Bool {
        Calendar.current.isDateInYesterday(publishedAt)
    }

    var sectionTitle: String {
        if isToday {
            return "TODAY"
        } else if isYesterday {
            return "YESTERDAY"
        } else {
            return UpdatesFormatters.sectionDateFormatter.string(from: publishedAt)
        }
    }
    
    // Hashable conformance based on id
    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
    
    static func == (lhs: NewsArticle, rhs: NewsArticle) -> Bool {
        lhs.id == rhs.id
    }
}

// MARK: - News Insight Summary
struct NewsInsightSummary: Identifiable {
    let id = UUID()
    let headline: String
    let bulletPoints: [String]
    let sentiment: MarketSentiment
    let updatedAt: Date
    let summaryType: String

    // ── Provenance (backend-driven) ──
    /// False for the deterministic headline-list fallback. The UI must NOT show
    /// the AI badge when this is false — claiming AI authorship for text no
    /// model wrote is exactly the misinformation this screen was fixed to avoid.
    var isAIGenerated: Bool = true
    /// Past the soft-expiry window: still shown, but labelled as catching up.
    var isStale: Bool = false
    /// A real AI card is being produced; the client re-polls a couple of times.
    var isRefreshing: Bool = false
    var articleCount: Int = 0

    var timeAgo: String {
        let interval = Date().timeIntervalSince(updatedAt)
        // A server clock slightly ahead of the device yields a negative interval,
        // which used to render "Updated -1 hours ago".
        guard interval > 60 else { return "Updated just now" }
        let minutes = Int(interval / 60)
        if minutes < 60 {
            return "Updated \(minutes) min ago"
        }
        let hours = Int(interval / 3600)
        if hours < 24 {
            return "Updated \(hours) hour\(hours == 1 ? "" : "s") ago"
        }
        let days = Int(interval / 86400)
        return "Updated \(days) day\(days == 1 ? "" : "s") ago"
    }

    var summaryBadgeText: String {
        summaryType
    }
}

// MARK: - Grouped News
struct GroupedNews: Identifiable {
    /// Stable identity = the section title (unique per group). A fresh `UUID()`
    /// on every regroup made the pinned-header `LazyVStack` diff all sections as
    /// brand new after each enrichment merge / filter change → header flicker,
    /// scroll jumps, and re-fired row `onAppear` that re-triggered enrich/paging.
    var id: String { sectionTitle }
    let sectionTitle: String
    let articles: [NewsArticle]
}

// MARK: - News Filter Options
struct NewsFilterOptions {
    var sources: [String]
    var sectors: [String]
    var sentiments: [NewsSentiment]

    static var `default`: NewsFilterOptions {
        NewsFilterOptions(sources: [], sectors: [], sentiments: [])
    }

    var hasActiveFilters: Bool {
        !sources.isEmpty || !sectors.isEmpty || !sentiments.isEmpty
    }

    /// Label for the filter chip. Static "All" hid the fact that filters were on.
    var chipLabel: String {
        let n = sources.count + sentiments.count
        return n == 0 ? "All" : "\(n) filter\(n == 1 ? "" : "s")"
    }

    /// Does `article` survive the current filters? Empty facets mean "no
    /// constraint", and the two facets AND together.
    func matches(_ article: NewsArticle) -> Bool {
        if !sources.isEmpty {
            let name = article.source.displayName.lowercased()
            guard sources.contains(where: { name == $0.lowercased() }) else { return false }
        }
        if !sentiments.isEmpty {
            // An un-enriched article has no sentiment yet. Filtering it OUT is
            // correct: the user asked for a specific sentiment and we cannot
            // claim this article has it.
            guard let s = article.sentiment, sentiments.contains(s) else { return false }
        }
        return true
    }
}

// MARK: - API DTOs
//
// The shared `APIClient` decoder deliberately does NOT use
// `.convertFromSnakeCase` (see APIClient.swift) — every DTO declares explicit
// snake_case CodingKeys. Dates arrive as ISO-8601 STRINGS and are parsed here,
// not by the decoder's date strategy, because the backend mixes FMP's
// "yyyy-MM-dd HH:mm:ss" with true ISO-8601.

struct UpdatesTabDTO: Codable, Sendable {
    let scope: String
    let title: String
    let companyName: String?
    let changePercent: Double?
    let logoUrl: String?
    let isMarketTab: Bool?

    enum CodingKeys: String, CodingKey {
        case scope, title
        case companyName = "company_name"
        case changePercent = "change_percent"
        case logoUrl = "logo_url"
        case isMarketTab = "is_market_tab"
    }
}

struct UpdatesTabsResponse: Codable, Sendable {
    let tabs: [UpdatesTabDTO]
}

struct AIInsightCardDTO: Codable, Sendable {
    let scope: String
    let headline: String
    let bullets: [String]?
    let sentiment: String?
    let badge: String?
    let articleCount: Int?
    let generatedAt: String?
    let isStale: Bool?
    let refreshing: Bool?
    let aiGenerated: Bool?
    let triggerReason: String?

    enum CodingKeys: String, CodingKey {
        case scope, headline, bullets, sentiment, badge
        case articleCount = "article_count"
        case generatedAt = "generated_at"
        case isStale = "is_stale"
        case refreshing
        case aiGenerated = "ai_generated"
        case triggerReason = "trigger_reason"
    }
}

struct UpdatesArticleDTO: Codable, Sendable {
    let id: String
    let headline: String
    let summary: String?
    let summaryBullets: [String]?
    let sentiment: String?
    let sourceName: String?
    let sourceLogoUrl: String?
    let publishedAt: String?
    let thumbnailUrl: String?
    let articleUrl: String?
    let relatedTickers: [String]?
    let aiProcessed: Bool?

    enum CodingKeys: String, CodingKey {
        case id, headline, summary, sentiment
        case summaryBullets = "summary_bullets"
        case sourceName = "source_name"
        case sourceLogoUrl = "source_logo_url"
        case publishedAt = "published_at"
        case thumbnailUrl = "thumbnail_url"
        case articleUrl = "article_url"
        case relatedTickers = "related_tickers"
        case aiProcessed = "ai_processed"
    }
}

struct UpdatesFeedResponse: Codable, Sendable {
    let scope: String
    let articles: [UpdatesArticleDTO]?
    let insight: AIInsightCardDTO?
    let cached: Bool?
    let cacheAgeSeconds: Int?
    /// Where this page started, echoed back by the backend.
    let offset: Int?
    /// Whether another page of retained history exists. Optional and defaulted
    /// to `false` at the use site — a backend that predates pagination omits
    /// it, and treating a missing value as "more" would loop forever.
    let hasMore: Bool?

    enum CodingKeys: String, CodingKey {
        case scope, articles, insight, cached, offset
        case cacheAgeSeconds = "cache_age_seconds"
        case hasMore = "has_more"
    }
}

struct EnrichUpdatesNewsResponse: Codable, Sendable {
    let scope: String
    let articles: [UpdatesArticleDTO]?
}

// MARK: - DTO → UI mapping

enum UpdatesDateParser {
    /// The backend returns two shapes: true ISO-8601 (`2026-07-20T17:26:41Z`)
    /// from our own timestamps, and FMP's space-separated
    /// `2026-07-20 17:26:41`. Both are UTC. Returning nil for an unparseable
    /// value lets the caller drop the article rather than stamping it "now" and
    /// floating stale news to the top of the TODAY section.
    static func parse(_ raw: String?) -> Date? {
        guard let raw, !raw.isEmpty else { return nil }
        if let d = isoWithFraction.date(from: raw) { return d }
        if let d = iso.date(from: raw) { return d }
        return fmpSpaced.date(from: raw)
    }

    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static let isoWithFraction: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let fmpSpaced: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        // Fixed locale/timezone: without these the parse follows the DEVICE's
        // calendar and zone, so the same payload yields different dates abroad.
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()
}

extension NewsSentiment {
    /// Maps the backend's article-level sentiment. Returns nil for an unknown
    /// or missing value so the badge is hidden rather than guessed.
    init?(backend raw: String?) {
        guard let raw else { return nil }
        switch raw.trimmingCharacters(in: .whitespaces).lowercased() {
        case "bullish", "positive": self = .positive
        case "bearish", "negative": self = .negative
        case "neutral":             self = .neutral
        default:                    return nil
        }
    }
}

extension MarketSentiment {
    /// Maps the backend's CARD-level sentiment (Capitalized domain).
    init(insight raw: String?) {
        switch (raw ?? "").trimmingCharacters(in: .whitespaces).lowercased() {
        case "bullish", "positive": self = .bullish
        case "bearish", "negative": self = .bearish
        default:                    self = .neutral
        }
    }
}

extension NewsArticle {
    /// Build a UI article from the API DTO. Returns nil when the row is not
    /// renderable (no headline, or an unparseable date) — an honest omission
    /// beats a blank row or a story mis-filed under TODAY.
    init?(dto: UpdatesArticleDTO) {
        let headline = dto.headline.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !headline.isEmpty else { return nil }
        guard let published = UpdatesDateParser.parse(dto.publishedAt) else { return nil }

        self.headline = headline
        self.summary = dto.summary
        self.source = NewsSource(
            name: dto.sourceName?.isEmpty == false ? dto.sourceName! : "News",
            iconName: nil,
            logoURL: URL(string: dto.sourceLogoUrl ?? "")
        )
        self.sentiment = NewsSentiment(backend: dto.sentiment)
        self.publishedAt = published
        self.thumbnailName = nil
        self.relatedTickers = dto.relatedTickers ?? []
        self.apiId = dto.id
        self.imageURL = URL(string: dto.thumbnailUrl ?? "")
        self.articleURL = URL(string: dto.articleUrl ?? "")
        self.summaryBullets = dto.summaryBullets ?? []
        self.aiProcessed = dto.aiProcessed ?? false
    }
}

extension NewsInsightSummary {
    /// Build the Insights card from the API DTO. Returns nil when the payload
    /// cannot back a real card (no headline or no bullets) so the section is
    /// omitted instead of rendering an empty shell.
    init?(dto: AIInsightCardDTO) {
        let headline = dto.headline.trimmingCharacters(in: .whitespacesAndNewlines)
        let bullets = (dto.bullets ?? []).filter {
            !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
        guard !headline.isEmpty, !bullets.isEmpty else { return nil }

        self.headline = headline
        self.bulletPoints = bullets
        self.sentiment = MarketSentiment(insight: dto.sentiment)
        self.updatedAt = UpdatesDateParser.parse(dto.generatedAt) ?? Date()
        self.summaryType = dto.badge ?? "48h"
        self.isAIGenerated = dto.aiGenerated ?? true
        self.isStale = dto.isStale ?? false
        self.isRefreshing = dto.refreshing ?? false
        self.articleCount = dto.articleCount ?? 0
    }
}

extension NewsFilterTab {
    init(dto: UpdatesTabDTO) {
        let isMarket = dto.isMarketTab ?? (dto.scope == UpdatesScope.market)
        self.title = dto.title
        self.ticker = isMarket ? nil : dto.scope
        // Guard non-finite: the backend nulls NaN/Inf, but a future source
        // might not, and a NaN here renders "nan%".
        self.changePercent = (dto.changePercent?.isFinite ?? false) ? dto.changePercent : nil
        self.isMarketTab = isMarket
        self.scope = dto.scope
        self.companyName = dto.companyName
        self.logoURL = URL(string: dto.logoUrl ?? "")
    }
}
