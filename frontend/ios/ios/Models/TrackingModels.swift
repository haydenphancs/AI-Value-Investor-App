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
    case dateAdded = "Date Added"

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
    let assetType: String  // "stock", "crypto", "etf", "index", "commodity"
    let marketCap: Double?

    // Portfolio Insights holding info (server-side `watchlist_items` columns).
    // Populated when the user has opted this ticker into Portfolio Insights
    // via the config sheet. Used to pre-fill the sheet's inputs and to decide
    // which rows count toward the diversification score.
    let shares: Double?
    let marketValue: Double?
    let sector: String?
    let country: String?
    /// Authoritative previous close from the backend FMP quote (nil on older
    /// backends / degraded rows). Preferred over the derived value below.
    let backendPreviousClose: Double?

    var isPositive: Bool {
        changePercent >= 0
    }

    /// Previous trading day's close — the sparkline's dotted baseline. Uses the
    /// authoritative backend value when present (matches the detail chart),
    /// else derives it from price and day-change % as a fallback.
    var previousClose: Double? {
        if let pc = backendPreviousClose { return pc }
        let factor = 1 + changePercent / 100
        guard factor != 0 else { return nil }
        return price / factor
    }

    /// `true` when this ticker is opted into Portfolio Insights — i.e. the
    /// user has provided either a share count or a dollar amount for it.
    var isHolding: Bool {
        (shares ?? 0) > 0 || (marketValue ?? 0) > 0
    }

    /// Convert to the ``PortfolioHolding`` shape that ``DiversificationCalculator``
    /// understands. Used by the offline / sample-data fallback in the
    /// ViewModel; production code uses the server-computed insights endpoint.
    func toPortfolioHolding() -> PortfolioHolding {
        let mappedAssetType: AssetType
        switch assetType.lowercased() {
        case "etf":     mappedAssetType = .etf
        case "bond":    mappedAssetType = .bond
        case "crypto":  mappedAssetType = .crypto
        case "cash":    mappedAssetType = .cash
        default:
            mappedAssetType = (country ?? "US") == "US" ? .stock : .internationalStock
        }
        return PortfolioHolding(
            ticker: ticker,
            companyName: companyName,
            marketValue: marketValue ?? 0,
            shares: shares,
            sector: sector,
            assetType: mappedAssetType,
            country: country ?? "US"
        )
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

// MARK: - App Alert

/// Unified alert model for the Alerts & Upcoming Events section.
/// Each case carries its own specific data fields.
enum AppAlert: Identifiable {
    case earnings(EarningsData)
    case market(MarketData)
    case whaleTrade(WhaleTradeAlertData)
    case analystRating(AnalystRatingAlertData)
    case insiderTransaction(InsiderTransactionAlertData)

    var id: UUID {
        switch self {
        case .earnings(let data): return data.id
        case .market(let data): return data.id
        case .whaleTrade(let data): return data.id
        case .analystRating(let data): return data.id
        case .insiderTransaction(let data): return data.id
        }
    }

    // MARK: - Associated Data Types

    struct EarningsData: Identifiable {
        let id = UUID()
        let ticker: String
        let companyName: String
        /// ``nil`` when FMP didn't specify a trading session — matches the
        /// Financials Earnings section, which shows "Time Not Specified"
        /// rather than a hallucinated "before market open".
        let reportTime: EarningsReportTime?
        /// Short consensus string like "EPS Est: $1.00 · Rev Est: $20.0B",
        /// or empty when neither estimate was available.
        let consensus: String
        let day: Int
        let month: String

        var formattedDay: String { String(day) }
        var formattedMonth: String { month.uppercased() }
    }

    struct MarketData: Identifiable {
        let id = UUID()
        let eventName: String
        let description: String
        let day: Int
        let month: String

        var formattedDay: String { String(day) }
        var formattedMonth: String { month.uppercased() }
    }

    struct WhaleTradeAlertData: Identifiable {
        let id = UUID()
        let action: WhaleAction             // .bought / .sold
        let totalAmount: String             // summed across items
        let timeWindowLabel: String         // e.g. "this week"
        let items: [WhaleTradeItem]

        var tickers: [String] { items.map(\.ticker) }
    }

    struct WhaleTradeItem: Identifiable {
        let id = UUID()
        let ticker: String
        let companyName: String
        let whaleCount: Int
        let amount: String
        let rawAmount: Double?
        let leadWhaleId: String?
        let leadWhaleName: String?
        let leadWhaleAvatarName: String?
    }

    struct AnalystRatingAlertData: Identifiable {
        let id = UUID()
        let timeWindowLabel: String         // e.g. "this week"
        let items: [AnalystRatingItem]

        var tickers: [String] { items.map(\.ticker) }
    }

    struct AnalystRatingItem: Identifiable {
        let id = UUID()
        let ticker: String
        let firmName: String
        let action: AnalystRatingAction
        let newRating: String
        let previousRating: String?
        let priceTarget: Double?
        let previousPriceTarget: Double?
        let day: Int
        let month: String

        var formattedDay: String { String(day) }
        var formattedMonth: String { month.uppercased() }
    }

    struct InsiderTransactionAlertData: Identifiable {
        let id = UUID()
        let action: WhaleAction             // .bought / .sold
        let totalAmount: String
        let timeWindowLabel: String
        let items: [InsiderTransactionItem]

        var tickers: [String] { items.map(\.ticker) }
    }

    struct InsiderTransactionItem: Identifiable {
        let id = UUID()
        let ticker: String
        let insiderName: String
        let insiderTitle: String
        let amount: String
        let rawAmount: Double?
        let day: Int
        let month: String

        var formattedDay: String { String(day) }
        var formattedMonth: String { month.uppercased() }
    }

    enum EarningsReportTime: String {
        case beforeOpen = "before_open"
        case afterClose = "after_close"

        var displayText: String {
            switch self {
            case .beforeOpen: return "before market open"
            case .afterClose: return "after market close"
            }
        }
    }

    // MARK: - Display Properties

    var title: String {
        switch self {
        case .earnings: return "Earnings Alert"
        case .market: return "Market"
        case .whaleTrade(let data):
            return data.action == .bought ? "Whales Bought" : "Whales Sold"
        case .analystRating: return "Analyst Ratings"
        case .insiderTransaction(let data):
            return data.action == .bought ? "Insider Bought" : "Insider Sold"
        }
    }

    var description: String {
        switch self {
        case .earnings(let data):
            var pieces: [String] = ["\(data.ticker) reports earnings"]
            if !data.formattedMonth.isEmpty && data.day > 0 {
                pieces.append("on \(data.formattedMonth) \(data.formattedDay)")
            }
            if let timing = data.reportTime {
                pieces.append(timing.displayText)
            }
            var sentence = pieces.joined(separator: " ") + "."
            if !data.consensus.isEmpty {
                sentence += " \(data.consensus)"
            }
            return sentence
        case .market(let data):
            return data.description
        case .whaleTrade(let data):
            return "\(AppAlert.joinTickers(data.tickers)) \(data.timeWindowLabel) — totaling \(data.totalAmount)."
        case .analystRating(let data):
            let countLabel = data.items.count == 1
                ? "1 rating change"
                : "\(data.items.count) rating changes"
            return "\(countLabel) on \(AppAlert.joinTickers(data.tickers)) \(data.timeWindowLabel)."
        case .insiderTransaction(let data):
            return "\(AppAlert.joinTickers(data.tickers)) \(data.timeWindowLabel) — totaling \(data.totalAmount)."
        }
    }

    var iconName: String {
        switch self {
        case .earnings, .market: return "bell.fill"
        case .whaleTrade: return "dollarsign.circle.fill"
        case .analystRating: return "chart.bar.doc.horizontal.fill"
        case .insiderTransaction: return "person.badge.key.fill"
        }
    }

    var iconColor: Color {
        switch self {
        case .earnings, .market: return AppColors.primaryBlue
        case .whaleTrade(let data): return data.action.color
        case .analystRating: return AppColors.alertOrange
        case .insiderTransaction(let data): return data.action.color
        }
    }

    /// Join tickers with truncation ("AAPL, CRM and 2 more").
    static func joinTickers(_ tickers: [String], maxVisible: Int = 4) -> String {
        guard !tickers.isEmpty else { return "" }
        if tickers.count <= maxVisible {
            return tickers.joined(separator: ", ")
        }
        let head = tickers.prefix(maxVisible).joined(separator: ", ")
        return "\(head) and \(tickers.count - maxVisible) more"
    }
}

// MARK: - Analyst Rating Action

enum AnalystRatingAction: String {
    case upgrade
    case downgrade
    case initiate
    /// Matches the backend's `_analyst_common.normalize_fmp_action()` canon
    /// ("upgrade" | "downgrade" | "initiate" | "maintain"). The alert feed
    /// filters maintains out today, but the fallback decode path still needs
    /// a bucket for them if the filter ever loosens.
    case maintain

    var iconName: String {
        switch self {
        case .upgrade: return "arrow.up.right"
        case .downgrade: return "arrow.down.right"
        case .initiate: return "sparkles"
        case .maintain: return "equal"
        }
    }

    var color: Color {
        switch self {
        case .upgrade: return AppColors.bullish
        case .downgrade: return AppColors.bearish
        case .initiate: return AppColors.primaryBlue
        case .maintain: return AppColors.alertOrange
        }
    }
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

// MARK: - Diversification Sub-Score

/// One additive dimension of the diversification score. `points` out of
/// `maxPoints`; the dimensions' maxPoints sum to 100, so the bars add up to the
/// overall score. `zone` ("green"/"yellow"/"red") drives the bar color.
struct DiversificationSubScore: Identifiable {
    let id = UUID()
    let key: String       // "position" | "sector" | "single_top5" | "marketcap"
    let label: String
    let points: Int       // earned, 0…maxPoints
    let maxPoints: Int    // budget for this dimension
    let zone: String      // "green" | "yellow" | "red"

    var progressValue: Double { maxPoints > 0 ? Double(points) / Double(maxPoints) : 0 }
    var pointsText: String { "\(points)/\(maxPoints)" }
}

// MARK: - Diversification Score
struct DiversificationScore: Identifiable {
    let id: UUID
    let score: Int                                 // 0–100 = sum of sub-score points
    let zone: String                               // "green" | "yellow" | "red"
    let effectiveHoldings: Double
    let message: String
    let sectorCount: Int
    let subScores: [DiversificationSubScore]
    let sectorAllocations: [SectorAllocation]
    let marketcapAllocations: [SectorAllocation]

    init(
        id: UUID = UUID(),
        score: Int,
        zone: String,
        effectiveHoldings: Double,
        message: String,
        sectorCount: Int,
        subScores: [DiversificationSubScore] = [],
        sectorAllocations: [SectorAllocation] = [],
        marketcapAllocations: [SectorAllocation] = []
    ) {
        self.id = id
        self.score = score
        self.zone = zone
        self.effectiveHoldings = effectiveHoldings
        self.message = message
        self.sectorCount = sectorCount
        self.subScores = subScores
        self.sectorAllocations = sectorAllocations
        self.marketcapAllocations = marketcapAllocations
    }

    var progressValue: Double { Double(score) / 100.0 }
    var effectiveHoldingsText: String { String(format: "%.1f", effectiveHoldings) }
}

// MARK: - API Response DTOs (Codable)

/// Top-level response from GET /api/v1/tracking/assets
struct TrackingFeedResponse: Codable {
    let assets: [TrackedAssetDTO]
    let alerts: [AlertDTO]
}

// MARK: - Portfolio Insights DTO

/// Response from GET /api/v1/tracking/portfolio-insights — the server-computed
/// diversification health score, sub-scores, breakdown allocations, and nudges.
struct PortfolioInsightsDTO: Codable {
    let score: Int
    let zone: String
    let effectiveHoldings: Double
    let message: String
    let sectorCount: Int
    let subScores: [DiversificationSubScoreDTO]
    let sectorAllocations: [SectorAllocationDTO]
    let marketcapAllocations: [SectorAllocationDTO]
    let holdingsCount: Int
    let totalValue: Double

    enum CodingKeys: String, CodingKey {
        case score, zone, message
        case effectiveHoldings = "effective_holdings"
        case sectorCount = "sector_count"
        case subScores = "sub_scores"
        case sectorAllocations = "sector_allocations"
        case marketcapAllocations = "marketcap_allocations"
        case holdingsCount = "holdings_count"
        case totalValue = "total_value"
    }

    func toDiversificationScore() -> DiversificationScore {
        DiversificationScore(
            score: score,
            zone: zone,
            effectiveHoldings: effectiveHoldings,
            message: message,
            sectorCount: sectorCount,
            subScores: subScores.map { $0.toSubScore() },
            sectorAllocations: sectorAllocations.map { $0.toSectorAllocation() },
            marketcapAllocations: marketcapAllocations.map { $0.toSectorAllocation() }
        )
    }
}

struct SectorAllocationDTO: Codable {
    let name: String
    let percentage: Double

    func toSectorAllocation() -> SectorAllocation {
        SectorAllocation(name: name, percentage: percentage)
    }
}

struct DiversificationSubScoreDTO: Codable {
    let key: String
    let label: String
    let points: Int
    let maxPoints: Int
    let zone: String

    enum CodingKeys: String, CodingKey {
        case key, label, points, zone
        case maxPoints = "max_points"
    }

    func toSubScore() -> DiversificationSubScore {
        DiversificationSubScore(
            key: key, label: label, points: points, maxPoints: maxPoints, zone: zone
        )
    }
}

/// A watchlist item enriched with real-time price data from the backend.
struct TrackedAssetDTO: Codable, Identifiable {
    var id: String { ticker }
    let ticker: String
    let companyName: String
    let price: Double
    let changePercent: Double
    // Optional so an older backend (no key) still decodes cleanly.
    let previousClose: Double?
    let sparklineData: [Double]
    let logoUrl: String?
    let sector: String?
    let country: String?
    let marketCap: Double?
    let assetType: String?
    let shares: Double?
    let marketValue: Double?

    enum CodingKeys: String, CodingKey {
        case ticker
        case companyName = "company_name"
        case price
        case changePercent = "change_percent"
        case previousClose = "previous_close"
        case sparklineData = "sparkline_data"
        case logoUrl = "logo_url"
        case sector, country, shares
        case marketCap = "market_cap"
        case assetType = "asset_type"
        case marketValue = "market_value"
    }

    /// Map to the view-layer model used by AssetsListSection
    func toTrackedAsset() -> TrackedAsset {
        TrackedAsset(
            ticker: ticker,
            companyName: companyName,
            price: price,
            changePercent: changePercent,
            sparklineData: sparklineData,
            assetType: assetType ?? "stock",
            marketCap: marketCap,
            shares: shares,
            marketValue: marketValue,
            sector: sector,
            country: country,
            backendPreviousClose: previousClose
        )
    }
}

/// An alert or event from the backend. The `type` discriminator selects
/// which subset of the optional fields is populated.
struct AlertDTO: Codable, Identifiable {
    var id: String { "\(type)_\(action ?? "")_\(ticker ?? "")_\(description.hashValue)" }
    let type: String
    let title: String
    let description: String

    // earnings / market
    let ticker: String?
    let companyName: String?
    let day: Int?
    let month: String?
    let reportTime: String?
    let epsEstimate: Double?
    let revenueEstimate: Double?

    // shared rollup
    let action: String?
    let totalAmount: String?
    let timeWindowLabel: String?

    // rollup item lists
    let whaleTradeItems: [WhaleTradeItemDTO]?
    let analystRatingItems: [AnalystRatingItemDTO]?
    let insiderTransactionItems: [InsiderTransactionItemDTO]?

    enum CodingKeys: String, CodingKey {
        case type, title, description, ticker
        case companyName = "company_name"
        case day, month
        case reportTime = "report_time"
        case epsEstimate = "eps_estimate"
        case revenueEstimate = "revenue_estimate"
        case action
        case totalAmount = "total_amount"
        case timeWindowLabel = "time_window_label"
        case whaleTradeItems = "whale_trade_items"
        case analystRatingItems = "analyst_rating_items"
        case insiderTransactionItems = "insider_transaction_items"
    }

    func toAppAlert() -> AppAlert {
        switch type {
        case "earnings":
            let mappedTime: AppAlert.EarningsReportTime?
            switch reportTime {
            case "before_open": mappedTime = .beforeOpen
            case "after_close": mappedTime = .afterClose
            default:            mappedTime = nil
            }

            var consensusParts: [String] = []
            if let eps = epsEstimate {
                consensusParts.append(String(format: "EPS Est: $%.2f", eps))
            }
            if let rev = revenueEstimate {
                let revB = rev / 1_000_000_000
                consensusParts.append(String(format: "Rev Est: $%.1fB", revB))
            }
            let consensus = consensusParts.joined(separator: " · ")

            return .earnings(AppAlert.EarningsData(
                ticker: ticker ?? "",
                companyName: companyName ?? "",
                reportTime: mappedTime,
                consensus: consensus,
                day: day ?? 0,
                month: month ?? ""
            ))
        case "whale_trade":
            let items = (whaleTradeItems ?? []).map { $0.toItem() }
            return .whaleTrade(AppAlert.WhaleTradeAlertData(
                action: (action?.lowercased() == "sold") ? .sold : .bought,
                totalAmount: totalAmount ?? "",
                timeWindowLabel: timeWindowLabel ?? "this week",
                items: items
            ))
        case "analyst_rating":
            let items = (analystRatingItems ?? []).map { $0.toItem() }
            return .analystRating(AppAlert.AnalystRatingAlertData(
                timeWindowLabel: timeWindowLabel ?? "this week",
                items: items
            ))
        case "insider_transaction":
            let items = (insiderTransactionItems ?? []).map { $0.toItem() }
            return .insiderTransaction(AppAlert.InsiderTransactionAlertData(
                action: (action?.lowercased() == "sold") ? .sold : .bought,
                totalAmount: totalAmount ?? "",
                timeWindowLabel: timeWindowLabel ?? "this week",
                items: items
            ))
        default:
            return .market(AppAlert.MarketData(
                eventName: title,
                description: description,
                day: day ?? 0,
                month: month ?? ""
            ))
        }
    }
}

// MARK: - Alert Item DTOs

struct WhaleTradeItemDTO: Codable {
    let ticker: String
    let companyName: String
    let whaleCount: Int
    let amount: String
    let rawAmount: Double?
    let leadWhaleId: String?
    let leadWhaleName: String?
    let leadWhaleAvatarName: String?

    enum CodingKeys: String, CodingKey {
        case ticker
        case companyName = "company_name"
        case whaleCount = "whale_count"
        case amount
        case rawAmount = "raw_amount"
        case leadWhaleId = "lead_whale_id"
        case leadWhaleName = "lead_whale_name"
        case leadWhaleAvatarName = "lead_whale_avatar_name"
    }

    func toItem() -> AppAlert.WhaleTradeItem {
        AppAlert.WhaleTradeItem(
            ticker: ticker,
            companyName: companyName,
            whaleCount: whaleCount,
            amount: amount,
            rawAmount: rawAmount,
            leadWhaleId: leadWhaleId,
            leadWhaleName: leadWhaleName,
            leadWhaleAvatarName: leadWhaleAvatarName
        )
    }
}

struct AnalystRatingItemDTO: Codable {
    let ticker: String
    let firmName: String
    let ratingAction: String
    let newRating: String
    let previousRating: String?
    let priceTarget: Double?
    let previousPriceTarget: Double?
    let day: Int?
    let month: String?

    enum CodingKeys: String, CodingKey {
        case ticker
        case firmName = "firm_name"
        case ratingAction = "rating_action"
        case newRating = "new_rating"
        case previousRating = "previous_rating"
        case priceTarget = "price_target"
        case previousPriceTarget = "previous_price_target"
        case day, month
    }

    func toItem() -> AppAlert.AnalystRatingItem {
        AppAlert.AnalystRatingItem(
            ticker: ticker,
            firmName: firmName,
            action: AnalystRatingAction(rawValue: ratingAction.lowercased()) ?? .maintain,
            newRating: newRating,
            previousRating: previousRating,
            priceTarget: priceTarget,
            previousPriceTarget: previousPriceTarget,
            day: day ?? 0,
            month: month ?? ""
        )
    }
}

struct InsiderTransactionItemDTO: Codable {
    let ticker: String
    let insiderName: String
    let insiderTitle: String
    let amount: String
    let rawAmount: Double?
    let day: Int?
    let month: String?

    enum CodingKeys: String, CodingKey {
        case ticker
        case insiderName = "insider_name"
        case insiderTitle = "insider_title"
        case amount
        case rawAmount = "raw_amount"
        case day, month
    }

    func toItem() -> AppAlert.InsiderTransactionItem {
        AppAlert.InsiderTransactionItem(
            ticker: ticker,
            insiderName: insiderName,
            insiderTitle: insiderTitle,
            amount: amount,
            rawAmount: rawAmount,
            day: day ?? 0,
            month: month ?? ""
        )
    }
}

// MARK: - Whale Category
enum WhaleCategory: String, CaseIterable {
    case investors = "Investors"
    case institutions = "Institutions"
    case politicians = "Politicians"

    /// Maps lowercase backend category strings to the enum
    static func fromBackend(_ value: String) -> WhaleCategory {
        switch value.lowercased() {
        case "investors": return .investors
        case "institutions": return .institutions
        case "politicians": return .politicians
        default: return .investors
        }
    }

    /// Returns lowercase category string for API queries
    var backendValue: String {
        switch self {
        case .investors: return "investors"
        case .institutions: return "institutions"
        case .politicians: return "politicians"
        }
    }
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
    let id: String
    let name: String
    let category: WhaleCategory
    let avatarName: String
    let followersCount: Int
    let isFollowing: Bool
    let title: String
    let description: String
    let recentTradeCount: Int

    init(id: String = UUID().uuidString, name: String, category: WhaleCategory, avatarName: String, followersCount: Int, isFollowing: Bool, title: String = "", description: String = "", recentTradeCount: Int = 0) {
        self.id = id
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
    let id: String          // trade group id — matches GET /whales/{whale}/trade-groups/{id}
    let whaleId: String     // owner of the trade group
    let entityName: String
    let entityAvatarName: String
    let category: WhaleCategory?
    let action: WhaleAction
    let tradeCount: Int
    let totalAmount: String
    let summary: String?
    let date: Date

    init(id: String = UUID().uuidString, whaleId: String = "", entityName: String, entityAvatarName: String, category: WhaleCategory? = nil, action: WhaleAction, tradeCount: Int, totalAmount: String, summary: String?, date: Date) {
        self.id = id
        self.whaleId = whaleId
        self.entityName = entityName
        self.entityAvatarName = entityAvatarName
        self.category = category
        self.action = action
        self.tradeCount = tradeCount
        self.totalAmount = totalAmount
        self.summary = summary
        self.date = date
    }

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

// MARK: - Sample Data
extension TrackedAsset {
    static let sampleData: [TrackedAsset] = [
        TrackedAsset(
            ticker: "AAPL",
            companyName: "Apple Inc.",
            price: 178.42,
            changePercent: 2.34,
            sparklineData: [165, 168, 170, 172, 175, 173, 176, 178],
            assetType: "stock",
            marketCap: 2_750_000_000_000,
            shares: 100,
            marketValue: 17_842,
            sector: "Technology",
            country: "US",
            backendPreviousClose: nil
        ),
        TrackedAsset(
            ticker: "NVDA",
            companyName: "NVIDIA Corp.",
            price: 495.22,
            changePercent: 5.67,
            sparklineData: [450, 460, 470, 465, 480, 490, 495],
            assetType: "stock",
            marketCap: 1_220_000_000_000,
            shares: 50,
            marketValue: 24_761,
            sector: "Technology",
            country: "US",
            backendPreviousClose: nil
        ),
        TrackedAsset(
            ticker: "MSFT",
            companyName: "Microsoft Corp.",
            price: 378.91,
            changePercent: -1.23,
            sparklineData: [390, 388, 385, 382, 380, 378, 379],
            assetType: "stock",
            marketCap: 2_810_000_000_000,
            shares: 50,
            marketValue: 18_945.50,
            sector: "Technology",
            country: "US",
            backendPreviousClose: nil
        ),
        TrackedAsset(
            ticker: "GOOGL",
            companyName: "Alphabet Inc.",
            price: 139.67,
            changePercent: 1.89,
            sparklineData: [135, 136, 137, 138, 137, 139, 140],
            assetType: "stock",
            marketCap: 1_750_000_000_000,
            shares: 100,
            marketValue: 13_967,
            sector: "Communication Services",
            country: "US",
            backendPreviousClose: nil
        )
    ]
}

extension AppAlert {
    static let sampleData: [AppAlert] = [
        .earnings(EarningsData(
            ticker: "NVDA",
            companyName: "NVIDIA Corp.",
            reportTime: .afterClose,
            consensus: "EPS Est: $1.00 · Rev Est: $20.0B",
            day: 22,
            month: "FEB"
        )),
        .market(MarketData(
            eventName: "Fed Interest Rate Decision",
            description: "Fed interest rate decision. FOMC meeting announcement",
            day: 24,
            month: "FEB"
        )),
        .whaleTrade(WhaleTradeAlertData(
            action: .bought,
            totalAmount: "$2.52B",
            timeWindowLabel: "this week",
            items: [
                WhaleTradeItem(
                    ticker: "AAPL",
                    companyName: "Apple Inc.",
                    whaleCount: 5,
                    amount: "$2.4B",
                    rawAmount: 2_400_000_000,
                    leadWhaleId: nil,
                    leadWhaleName: "Warren Buffett",
                    leadWhaleAvatarName: "avatar_buffett"
                ),
                WhaleTradeItem(
                    ticker: "CRM",
                    companyName: "Salesforce Inc.",
                    whaleCount: 2,
                    amount: "$120M",
                    rawAmount: 120_000_000,
                    leadWhaleId: nil,
                    leadWhaleName: "Ro Khanna",
                    leadWhaleAvatarName: nil
                )
            ]
        )),
        .whaleTrade(WhaleTradeAlertData(
            action: .sold,
            totalAmount: "$689M",
            timeWindowLabel: "this week",
            items: [
                WhaleTradeItem(
                    ticker: "GOOGL",
                    companyName: "Alphabet Inc.",
                    whaleCount: 3,
                    amount: "$688.4M",
                    rawAmount: 688_400_000,
                    leadWhaleId: nil,
                    leadWhaleName: "Tommy Tuberville",
                    leadWhaleAvatarName: nil
                ),
                WhaleTradeItem(
                    ticker: "AAPL",
                    companyName: "Apple Inc.",
                    whaleCount: 2,
                    amount: "$669K",
                    rawAmount: 669_000,
                    leadWhaleId: nil,
                    leadWhaleName: "Tommy Tuberville",
                    leadWhaleAvatarName: nil
                )
            ]
        )),
        .analystRating(AnalystRatingAlertData(
            timeWindowLabel: "this week",
            items: [
                AnalystRatingItem(
                    ticker: "GOOGL",
                    firmName: "Morgan Stanley",
                    action: .upgrade,
                    newRating: "Overweight",
                    previousRating: "Equal-Weight",
                    priceTarget: 210.0,
                    previousPriceTarget: 185.0,
                    day: 18,
                    month: "FEB"
                ),
                AnalystRatingItem(
                    ticker: "CRM",
                    firmName: "BTIG",
                    action: .upgrade,
                    newRating: "Buy",
                    previousRating: "Neutral",
                    priceTarget: nil,
                    previousPriceTarget: nil,
                    day: 17,
                    month: "FEB"
                )
            ]
        )),
        .insiderTransaction(InsiderTransactionAlertData(
            action: .bought,
            totalAmount: "$5.2M",
            timeWindowLabel: "this week",
            items: [
                InsiderTransactionItem(
                    ticker: "NVDA",
                    insiderName: "Colette Kress",
                    insiderTitle: "CFO",
                    amount: "$5.2M",
                    rawAmount: 5_200_000,
                    day: 16,
                    month: "FEB"
                )
            ]
        ))
    ]
}

extension DiversificationScore {
    static let sampleData: DiversificationScore = {
        if let calculated = DiversificationCalculator.calculate(
            holdings: PortfolioHolding.sampleData
        ) {
            return calculated
        }
        // Fallback (should never reach here with valid sample data)
        return DiversificationScore(
            score: 0,
            zone: "red",
            effectiveHoldings: 0,
            message: "Add assets to see your diversification score",
            sectorCount: 0
        )
    }()
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
            name: "Michael Burry",
            category: .investors,
            avatarName: "avatar_burry",
            followersCount: 98000,
            isFollowing: true,
            recentTradeCount: 1
        )
    ]

    // Top popular whales for main screen (mixed categories)
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

    // All popular whales combined
    static let allPopularWhalesData: [TrendingWhale] = allInvestorsData + allInstitutionsData + allPoliticiansData

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

    // Extended sample data for "All Recent Trades" view
    static let allSampleData: [WhaleTradeGroupActivity] = [
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
        ),
        WhaleTradeGroupActivity(
            entityName: "Cathie Wood",
            entityAvatarName: "avatar_wood",
            action: .bought,
            tradeCount: 5,
            totalAmount: "$1.92B",
            summary: "New positions in AI and robotics sectors",
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 10))!
        ),
        WhaleTradeGroupActivity(
            entityName: "Ray Dalio",
            entityAvatarName: "avatar_dalio",
            action: .sold,
            tradeCount: 7,
            totalAmount: "$3.21B",
            summary: "Reduced exposure to emerging markets",
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 8))!
        ),
        WhaleTradeGroupActivity(
            entityName: "Carl Icahn",
            entityAvatarName: "avatar_icahn",
            action: .bought,
            tradeCount: 3,
            totalAmount: "$5.67B",
            summary: "Large stake in energy sector",
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 5))!
        ),
        WhaleTradeGroupActivity(
            entityName: "Michael Burry",
            entityAvatarName: "avatar_burry",
            action: .sold,
            tradeCount: 9,
            totalAmount: "$2.45B",
            summary: "Closed multiple tech positions",
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 3))!
        ),
        WhaleTradeGroupActivity(
            entityName: "Stanley Druckenmiller",
            entityAvatarName: "avatar_druckenmiller",
            action: .bought,
            tradeCount: 6,
            totalAmount: "$4.11B",
            summary: "Increased healthcare holdings",
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 1))!
        ),
        WhaleTradeGroupActivity(
            entityName: "George Soros",
            entityAvatarName: "avatar_soros",
            action: .bought,
            tradeCount: 4,
            totalAmount: "$3.87B",
            summary: "Strategic moves in financial sector",
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 11, day: 28))!
        ),
        WhaleTradeGroupActivity(
            entityName: "David Tepper",
            entityAvatarName: "avatar_tepper",
            action: .sold,
            tradeCount: 8,
            totalAmount: "$2.93B",
            summary: "Portfolio rebalancing across sectors",
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 11, day: 25))!
        ),
        WhaleTradeGroupActivity(
            entityName: "Ken Griffin",
            entityAvatarName: "avatar_griffin",
            action: .bought,
            tradeCount: 11,
            totalAmount: "$6.78B",
            summary: "Major investment in growth stocks",
            date: Calendar.current.date(from: DateComponents(year: 2025, month: 11, day: 22))!
        )
    ]

    static var allGroupedSampleData: [GroupedWhaleTrades] {
        allSampleData.map { activity in
            GroupedWhaleTrades(
                sectionTitle: activity.formattedDate,
                activities: [activity]
            )
        }
    }
}

