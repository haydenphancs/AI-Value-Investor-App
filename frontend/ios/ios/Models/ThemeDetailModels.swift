//
//  ThemeDetailModels.swift
//  ios
//
//  Models for the Emerging Frontiers theme detail screen — decoded from
//  `GET /api/v1/home/themes/{slug}` and mapped to display-ready models so the
//  view stays dumb. Co-located DTO + UI model per the codebase convention.
//
//  The DTOs carry RAW numbers (explicit snake_case CodingKeys — the APIClient
//  decoder does NOT convertFromSnakeCase); `toDisplay()` formats price / percent
//  / market cap and picks the green/red sign here.
//

import SwiftUI

// MARK: - Wire DTOs

/// One constituent company as served by the backend (raw numbers).
struct ThemeConstituentDTO: Decodable {
    let ticker: String
    let companyName: String?
    let price: Double?
    let changePercent: Double?
    let marketCap: Double?
    /// "pure_play" | "diversified" from the monthly review; nil when not reviewed.
    let role: String?
    /// Joined (or came back) in the latest monthly review.
    let isNew: Bool?

    enum CodingKeys: String, CodingKey {
        case ticker, price, role
        case companyName = "company_name"
        case changePercent = "change_percent"
        case marketCap = "market_cap"
        case isNew = "is_new"
    }

    func toDisplay() -> ThemeConstituent {
        ThemeConstituent(
            ticker: ticker,
            name: CompanyNameFormatter.clean((companyName?.isEmpty == false) ? companyName! : ticker),
            priceText: ThemeDetailFormat.price(price),
            changeText: changePercent.map { String(format: "%+.2f%%", $0) } ?? "",
            isPositive: (changePercent ?? 0) >= 0,
            marketCapText: ThemeDetailFormat.marketCap(marketCap),
            roleLabel: ThemeDetailFormat.roleLabel(role),
            isNew: isNew ?? false
        )
    }
}

// Monthly review + daily insights (backend migration 174). Every field is Optional: an
// older backend, an un-migrated database or a theme not reviewed yet simply omits them,
// and the screen then renders exactly as it did before they existed.

/// One change from the latest monthly review ("What changed this month").
struct ThemeChangeDTO: Decodable {
    let ticker: String
    let companyName: String?
    let action: String          // "added" | "returned" | "removed"
    let reason: String?

    enum CodingKeys: String, CodingKey {
        case ticker, action, reason
        case companyName = "company_name"
    }
}

struct ThemePeriodReturnDTO: Decodable {
    let period: String          // "1M" | "YTD" | "1Y"
    let theme: Double?          // fraction: 0.042 = +4.2%
    let benchmark: Double?
}

struct ThemePerformanceDTO: Decodable {
    let asOf: String?
    let benchmarkLabel: String?
    let periods: [ThemePeriodReturnDTO]?
    let themeSeries: [Double]?
    let benchmarkSeries: [Double]?

    enum CodingKeys: String, CodingKey {
        case periods
        case asOf = "as_of"
        case benchmarkLabel = "benchmark_label"
        case themeSeries = "theme_series"
        case benchmarkSeries = "benchmark_series"
    }
}

struct ThemeInsightDTO: Decodable {
    let asOf: String?
    let headline: String?
    let summary: String?
    let tickers: [String]?

    enum CodingKeys: String, CodingKey {
        case headline, summary, tickers
        case asOf = "as_of"
    }
}

struct ThemeNewsItemDTO: Decodable {
    let title: String
    let source: String?
    let url: String?
    let publishedAt: String?
    let ticker: String?

    enum CodingKeys: String, CodingKey {
        case title, source, url, ticker
        case publishedAt = "published_at"
    }
}

/// The full theme drill-down payload.
struct ThemeDetailDTO: Decodable {
    let slug: String
    let title: String
    let subtitle: String?
    let imageUrl: String?
    let accentHex: String
    let constituents: [ThemeConstituentDTO]
    let updatedOn: String?
    let changes: [ThemeChangeDTO]?
    let performance: ThemePerformanceDTO?
    let insight: ThemeInsightDTO?
    let news: [ThemeNewsItemDTO]?

    enum CodingKeys: String, CodingKey {
        case slug, title, subtitle, constituents, changes, performance, insight, news
        case imageUrl = "image_url"
        case accentHex = "accent_hex"
        case updatedOn = "updated_on"
    }

    func toDisplay() -> ThemeDetail {
        ThemeDetail(
            slug: slug,
            title: title,
            subtitle: subtitle ?? "",
            imageUrl: imageUrl,
            // `.fill`: this is the hero surface when the image fails to load, and it
            // carries `textOnAccent` title/subtitle ink. Deliberately a DIFFERENT role
            // from the same theme's accent on the Home grid tile
            // (`HomeRepository.TrendingTheme.accent`, which stays `.graphic`) — that tile
            // carries no ink of its own, so it has no white-on-it floor to clear. Do not
            // "unify" them.
            accent: Color(themedHex: accentHex, role: .fill, fallback: AppColors.primaryFill),
            companies: constituents.map { $0.toDisplay() },
            // The same hex as a chart STROKE: `.graphic` (3:1 on the card). The `.fill`
            // accent is darkened for white ink and never measured against the card — on the
            // dark card it drew the theme's line weaker than the S&P line beside it.
            chartAccent: Color(themedHex: accentHex, role: .graphic, fallback: AppColors.primaryGraphic),
            reviewedOn: ThemeReviewDate.parse(updatedOn),
            changes: (changes ?? []).compactMap { ThemeChange(dto: $0) },
            performance: performance.flatMap { ThemePerformance(dto: $0) },
            insight: insight.flatMap { ThemeInsight(dto: $0) },
            news: (news ?? []).compactMap { ThemeNewsItem(dto: $0) }
        )
    }
}

// MARK: - Display models

/// One row in the theme's "Companies" list (display-ready strings).
struct ThemeConstituent: Identifiable {
    let id = UUID()
    let ticker: String
    let name: String
    let priceText: String       // "$233.45" or "" when unavailable
    let changeText: String      // "+2.10%" or "" (nil change → hidden)
    let isPositive: Bool
    let marketCapText: String   // "3.5T Cap" or ""
    /// "Pure play" / "Diversified"; nil hides the tag.
    var roleLabel: String? = nil
    var isNew: Bool = false
}

/// One line of "What changed this month".
struct ThemeChange: Identifiable {
    enum Kind { case added, returned, removed }
    let id = UUID()
    let ticker: String
    let name: String
    let kind: Kind
    let reason: String

    init?(dto: ThemeChangeDTO) {
        switch dto.action {
        case "added": kind = .added
        case "returned": kind = .returned
        case "removed": kind = .removed
        default: return nil          // an action this build does not know → not shown
        }
        ticker = dto.ticker
        let raw = dto.companyName ?? ""
        name = raw.isEmpty ? dto.ticker : CompanyNameFormatter.clean(raw)
        reason = (dto.reason ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var label: String {
        switch kind {
        case .added: return "Added"
        case .returned: return "Back"
        case .removed: return "Removed"
        }
    }
}

struct ThemePeriodReturn: Identifiable {
    let id = UUID()
    let period: String
    let themeText: String       // "+4.2%" or "—"
    let benchmarkText: String
    let themeIsPositive: Bool
    let hasTheme: Bool
}

struct ThemePerformance {
    let asOf: Date?
    let benchmarkLabel: String
    let periods: [ThemePeriodReturn]
    let themeSeries: [Double]
    let benchmarkSeries: [Double]

    init?(dto: ThemePerformanceDTO) {
        let periods = (dto.periods ?? []).map { p in
            ThemePeriodReturn(
                period: p.period,
                themeText: ThemeDetailFormat.fractionPercent(p.theme),
                benchmarkText: ThemeDetailFormat.fractionPercent(p.benchmark),
                themeIsPositive: ThemeDetailFormat.readsNonNegative(p.theme),
                hasTheme: p.theme.map { $0.isFinite } ?? false
            )
        }
        // Nothing measurable → no card at all (never a row of dashes).
        guard periods.contains(where: { $0.hasTheme }) else { return nil }
        self.asOf = ThemeReviewDate.parse(dto.asOf)
        self.benchmarkLabel = (dto.benchmarkLabel?.isEmpty == false) ? dto.benchmarkLabel! : "S&P 500 ETF"
        self.periods = periods
        self.themeSeries = ThemeTrend.points(dto.themeSeries)
        self.benchmarkSeries = ThemeTrend.points(dto.benchmarkSeries)
    }
}

struct ThemeInsight {
    let asOf: Date?
    let headline: String
    let summary: String
    let tickers: [String]

    init?(dto: ThemeInsightDTO) {
        let summary = (dto.summary ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !summary.isEmpty else { return nil }
        self.asOf = ThemeReviewDate.parse(dto.asOf)
        self.headline = (dto.headline ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        self.summary = summary
        self.tickers = (dto.tickers ?? []).filter { !$0.isEmpty }
    }
}

struct ThemeNewsItem: Identifiable {
    let id = UUID()
    let title: String
    let source: String
    let url: URL?
    let publishedAt: Date?

    init?(dto: ThemeNewsItemDTO) {
        let title = dto.title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return nil }
        self.title = title
        self.source = dto.source ?? ""
        self.url = dto.url.flatMap { URL(string: $0) }.flatMap { ["http", "https"].contains($0.scheme?.lowercased() ?? "") ? $0 : nil }
        self.publishedAt = dto.publishedAt.flatMap { ThemeDetailFormat.timestamp($0) }
    }
}

/// Everything the theme detail screen renders.
struct ThemeDetail {
    let slug: String
    let title: String
    let subtitle: String
    let imageUrl: String?
    let accent: Color
    let companies: [ThemeConstituent]
    var chartAccent: Color = AppColors.primaryGraphic
    var reviewedOn: Date? = nil
    var changes: [ThemeChange] = []
    var performance: ThemePerformance? = nil
    var insight: ThemeInsight? = nil
    var news: [ThemeNewsItem] = []

    var isEmpty: Bool { companies.isEmpty }
}

// MARK: - Formatting

enum ThemeDetailFormat {
    /// Grouped price with two decimals, e.g. `6952.4 → "$6,952.40"`. Empty when
    /// missing / non-positive / non-finite.
    static func price(_ p: Double?) -> String {
        guard let p, p.isFinite, p > 0 else { return "" }
        let n = _priceFormatter.string(from: NSNumber(value: p)) ?? String(format: "%.2f", p)
        return "$" + n
    }

    /// Market cap abbreviated M / B / T with one decimal + " Cap", matching the
    /// scanners: `3.1e12 → "3.1T Cap"`, `4.52e10 → "45.2B Cap"`, `2.6e8 → "260.0M Cap"`.
    static func marketCap(_ cap: Double?) -> String {
        guard let cap, cap.isFinite, cap > 0 else { return "" }
        if cap >= 1_000_000_000_000 { return String(format: "%.1fT Cap", cap / 1_000_000_000_000) }
        if cap >= 1_000_000_000 { return String(format: "%.1fB Cap", cap / 1_000_000_000) }
        return String(format: "%.1fM Cap", cap / 1_000_000)
    }

    /// A server FRACTION as a signed percent: `0.0421 → "+4.2%"`; nil / non-finite → "—".
    static func fractionPercent(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        let pct = value * 100
        let rounded = (pct * 10).rounded() / 10
        return String(format: "%+.1f%%", rounded == 0 ? 0 : rounded)
    }

    /// Whether a fraction READS as non-negative at the precision `fractionPercent` prints:
    /// -0.0004 prints "+0.0%", and colouring that as a loss put a plus sign in red.
    static func readsNonNegative(_ value: Double?) -> Bool {
        guard let value, value.isFinite else { return true }
        return (value * 1000).rounded() / 10 >= 0
    }

    static func roleLabel(_ role: String?) -> String? {
        switch role {
        case "pure_play": return "Pure play"
        case "diversified": return "Diversified"
        default: return nil
        }
    }

    /// ISO-8601 with or without fractional seconds / a zone; a date-only value too.
    static func timestamp(_ raw: String) -> Date? {
        if let d = _iso.date(from: raw) { return d }
        if let d = _isoFractional.date(from: raw) { return d }
        return ThemeReviewDate.parse(raw)
    }

    private static let _iso: ISO8601DateFormatter = ISO8601DateFormatter()
    private static let _isoFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let _priceFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        return f
    }()
}
