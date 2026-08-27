//
//  CommodityDetailResponseModels.swift
//  ios
//
//  Codable response DTOs for the /api/v1/commodities/{symbol} endpoint.
//  These decode the backend's snake_case JSON and map to UI models.
//

import Foundation

// MARK: - Top-Level Response

extension CommodityMarketStatus {
    /// The backend's `market_status` string -> the view model's enum.
    ///
    /// Extracted so the full and light refresh paths cannot disagree about what
    /// "Market Closed" means — two copies of a mapping is how the same string ends up
    /// rendering two different states on one screen.
    init(backend: String) {
        switch backend.lowercased() {
        case "open", "market open":
            self = .open
        case "pre-market", "premarket":
            self = .preMarket
        case "after-hours", "afterhours":
            self = .afterHours
        default:
            self = .closed(date: Date(), time: "", timezone: "ET")
        }
    }
}

/// Light refresh slice from `GET /commodities/{symbol}/quote`.
///
/// Field names and types deliberately mirror `CommodityDetailResponseDTO`, so the same
/// nested DTOs decode it. `chartData` is empty unless the caller asked for a range.
struct CommodityQuoteResponseDTO: Decodable {
    let symbol: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: String
    let chartData: [CommodityChartPointDTO]
    let keyStatisticsGroups: [KeyStatisticsGroupDTO]
    let relatedCommodities: [RelatedCommodityDTO]?

    enum CodingKeys: String, CodingKey {
        case symbol
        case currentPrice = "current_price"
        case priceChange = "price_change"
        case priceChangePercent = "price_change_percent"
        case marketStatus = "market_status"
        case chartData = "chart_data"
        case keyStatisticsGroups = "key_statistics_groups"
        case relatedCommodities = "related_commodities"
    }
}

struct CommodityDetailResponseDTO: Decodable {
    let symbol: String
    let name: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: String
    let chartData: [CommodityChartPointDTO]
    let keyStatisticsGroups: [KeyStatisticsGroupDTO]
    let performancePeriods: [PerformancePeriodDTO]
    let newsArticles: [CommodityNewsArticleDTO]
    let commodityProfile: CommodityProfileDTO?
    let relatedCommodities: [RelatedCommodityDTO]?
    let benchmarkSummary: BenchmarkSummaryDTO?

    enum CodingKeys: String, CodingKey {
        case symbol, name
        case currentPrice = "current_price"
        case priceChange = "price_change"
        case priceChangePercent = "price_change_percent"
        case marketStatus = "market_status"
        case chartData = "chart_data"
        case keyStatisticsGroups = "key_statistics_groups"
        case performancePeriods = "performance_periods"
        case newsArticles = "news_articles"
        case commodityProfile = "commodity_profile"
        case relatedCommodities = "related_commodities"
        case benchmarkSummary = "benchmark_summary"
    }
}

// MARK: - Chart Point DTO

struct CommodityChartPointDTO: Decodable {
    let date: String
    let open: Double?
    let high: Double?
    let low: Double?
    let close: Double
    let volume: Double?
}

// MARK: - Commodity Profile DTO

struct CommodityProfileDTO: Decodable {
    let description: String?
    let category: String?
    let exchange: String?
    let tradingHours: String?
    let contractSize: String?
    let unit: String?
    let currency: String?
    let tickSize: String?
    let majorProducers: String?
    let majorConsumers: String?

    enum CodingKeys: String, CodingKey {
        case description, category, exchange, currency, unit
        case tradingHours = "trading_hours"
        case contractSize = "contract_size"
        case tickSize = "tick_size"
        case majorProducers = "major_producers"
        case majorConsumers = "major_consumers"
    }

    func toModel() -> CommodityProfile {
        let resolvedCategory: CommodityCategory = {
            switch (category ?? "").lowercased() {
            case "metals": return .metals
            case "energy": return .energy
            case "agriculture": return .agriculture
            case "consumables": return .consumables
            default: return .metals
            }
        }()

        let resolvedUnit: CommodityUnit = {
            switch (unit ?? "").lowercased().replacingOccurrences(of: "_", with: "") {
            case "troyounce": return .troyOunce
            case "barrel": return .barrel
            case "pound": return .pound
            case "mmbtu": return .mmbtu
            case "gallon": return .gallon
            case "bushel": return .bushel
            case "ton": return .ton
            default: return .contract
            }
        }()

        return CommodityProfile(
            description: description ?? "",
            category: resolvedCategory,
            exchange: exchange ?? "",
            tradingHours: tradingHours ?? "",
            contractSize: contractSize ?? "",
            unit: resolvedUnit,
            currency: currency ?? "USD",
            tickSize: tickSize ?? "",
            majorProducers: majorProducers ?? "",
            majorConsumers: majorConsumers ?? "",
            website: nil
        )
    }
}

// MARK: - Related Commodity DTO

struct RelatedCommodityDTO: Decodable {
    let symbol: String
    let name: String
    let price: Double
    let changePercent: Double

    enum CodingKeys: String, CodingKey {
        case symbol, name, price
        case changePercent = "change_percent"
    }

    func toModel() -> RelatedTicker {
        RelatedTicker(
            symbol: symbol,
            name: name,
            price: price,
            changePercent: changePercent
        )
    }
}

// BenchmarkSummaryDTO is defined in CryptoAPIModels.swift and shared across asset types

// MARK: - News Article DTO

struct CommodityNewsArticleDTO: Decodable {
    let headline: String
    let sourceName: String
    let sourceIcon: String?
    let sentiment: String
    let publishedAt: String
    let thumbnailUrl: String?
    let relatedTickers: [String]
    let summaryBullets: [String]
    let articleUrl: String?

    enum CodingKeys: String, CodingKey {
        case headline, sentiment
        case sourceName = "source_name"
        case sourceIcon = "source_icon"
        case publishedAt = "published_at"
        case thumbnailUrl = "thumbnail_url"
        case relatedTickers = "related_tickers"
        case summaryBullets = "summary_bullets"
        case articleUrl = "article_url"
    }

    // Tolerant decode: a null in `sentiment`/`published_at`/the arrays would
    // otherwise fail decoding of the ENTIRE commodity detail response. Absorb it.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        headline = try c.decodeIfPresent(String.self, forKey: .headline) ?? ""
        sourceName = try c.decodeIfPresent(String.self, forKey: .sourceName) ?? ""
        sourceIcon = try c.decodeIfPresent(String.self, forKey: .sourceIcon)
        sentiment = try c.decodeIfPresent(String.self, forKey: .sentiment) ?? ""
        publishedAt = try c.decodeIfPresent(String.self, forKey: .publishedAt) ?? ""
        thumbnailUrl = try c.decodeIfPresent(String.self, forKey: .thumbnailUrl)
        relatedTickers = try c.decodeIfPresent([String].self, forKey: .relatedTickers) ?? []
        summaryBullets = try c.decodeIfPresent([String].self, forKey: .summaryBullets) ?? []
        articleUrl = try c.decodeIfPresent(String.self, forKey: .articleUrl)
    }

    func toModel() -> TickerNewsArticle {
        let date: Date
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let parsed = formatter.date(from: publishedAt) {
            date = parsed
        } else {
            formatter.formatOptions = [.withInternetDateTime]
            if let parsed = formatter.date(from: publishedAt) {
                date = parsed
            } else {
                let simple = DateFormatter()
                simple.dateFormat = "yyyy-MM-dd HH:mm:ss"
                simple.locale = Locale(identifier: "en_US_POSIX")
                date = simple.date(from: publishedAt) ?? Date()
            }
        }

        let newsSource = NewsSource(name: sourceName, iconName: nil)

        let newsSentiment: NewsSentiment
        switch sentiment.lowercased() {
        case "positive", "bullish":
            newsSentiment = .positive
        case "negative", "bearish":
            newsSentiment = .negative
        default:
            newsSentiment = .neutral
        }

        return TickerNewsArticle(
            apiId: articleUrl ?? UUID().uuidString,
            headline: headline,
            source: newsSource,
            sentiment: newsSentiment,
            publishedAt: date,
            thumbnailName: nil,
            imageURL: thumbnailUrl.flatMap { URL(string: $0) },
            relatedTickers: relatedTickers,
            summaryBullets: summaryBullets,
            articleURL: articleUrl.flatMap { URL(string: $0) },
            aiProcessed: !summaryBullets.isEmpty
        )
    }
}

// MARK: - CommodityDetailResponseDTO → CommodityDetailData Mapping

extension CommodityDetailResponseDTO {
    func toDisplayModel() -> CommodityDetailData {
        let resolvedMarketStatus = CommodityMarketStatus(backend: marketStatus)

        let profile = commodityProfile?.toModel() ?? CommodityProfile(
            description: "",
            category: .metals,
            exchange: "",
            tradingHours: "",
            contractSize: "",
            unit: .contract,
            currency: "USD",
            tickSize: "",
            majorProducers: "",
            majorConsumers: "",
            website: nil
        )

        return CommodityDetailData(
            symbol: symbol,
            name: name,
            currentPrice: currentPrice,
            priceChange: priceChange,
            priceChangePercent: priceChangePercent,
            marketStatus: resolvedMarketStatus,
            chartPricePoints: chartData.map {
                StockPricePoint(date: $0.date, close: $0.close, open: $0.open, high: $0.high, low: $0.low, volume: $0.volume)
            },
            keyStatisticsGroups: keyStatisticsGroups.map { $0.toModel() },
            performancePeriods: performancePeriods.map { $0.toModel() },
            commodityProfile: profile,
            relatedCommodities: relatedCommodities?.map { $0.toModel() } ?? [],
            benchmarkSummary: benchmarkSummary?.toModel()
        )
    }

    func toNewsArticles() -> [TickerNewsArticle] {
        newsArticles.map { $0.toModel() }
    }
}

// MARK: - ──────────────────────────────────────────────
// MARK:   FAST-CORE FIRST PAINT
// MARK: - ──────────────────────────────────────────────
//
// `GET /api/v1/commodities/{{symbol}}/core` — the header line, and the chart when it was
// already cached server-side.
//
// Why this exists, measured against production on 2026-08-26: a cold `^GSPC` detail
// build took 5.63s (^DJI 11.42s, SCHD 5.89s) while the same build with its caches warm
// took 0.36s — and the whole screen sat behind that ONE response, so a TestFlight tester
// reported "It's very slow at first time open it." The stock screen never had that
// problem despite the SLOWEST full build of the lot (DECK, 7.94s), because it paints a
// core slice in 0.32s first.
//
// NOT the same thing as the `/quote` light slice: that one is a PROJECTION of the full
// build, so on a cold cache it costs exactly what the full detail costs and cannot serve
// first paint. Core is assembled from the two cheap sections only.

/// Fast-core payload for the commodities detail screen. Field names mirror the full detail
/// DTO and reuse its nested types, so `full ?? core` is a drop-in swap in the view.
struct CommodityCoreResponseDTO: Decodable {
    let symbol: String
    let name: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: String
    /// Empty when the server could only have produced bars by pulling the multi-thousand
    /// row daily history. The full response fills them in a moment later.
    let chartData: [CommodityChartPointDTO]

    enum CodingKeys: String, CodingKey {
        case symbol
        case name = "name"
        case currentPrice = "current_price"
        case priceChange = "price_change"
        case priceChangePercent = "price_change_percent"
        case marketStatus = "market_status"
        case chartData = "chart_data"
    }

    func toCoreData() -> CommodityCoreData {
        CommodityCoreData(
            symbol: symbol,
            name: name,
            currentPrice: currentPrice,
            priceChange: priceChange,
            priceChangePercent: priceChangePercent,
            marketStatus: CommodityMarketStatus(backend: marketStatus),
            // `date` is non-optional on CommodityChartPointDTO (unlike the shared
            // StockOverviewPricePointDTO the other three screens use), so no coalescing.
            chartPricePoints: chartData.map {
                StockPricePoint(date: $0.date, close: $0.close,
                                open: $0.open, high: $0.high, low: $0.low,
                                volume: $0.volume)
            }
        )
    }
}

/// The header-only side model the screen paints until the full response lands.
///
/// Every formatted string delegates to `CommodityHeaderFormat`, the SAME helper the full display model
/// uses — so nothing visibly reformats when core is superseded.
struct CommodityCoreData {
    let symbol: String
    let name: String
    var currentPrice: Double
    var priceChange: Double
    var priceChangePercent: Double
    var marketStatus: CommodityMarketStatus
    /// `var`: the range pill is interactive before the full response lands, and the live
    /// socket merges ticks into the core header the same way it merges into the full one.
    var chartPricePoints: [StockPricePoint]

    var chartData: [Double] { chartPricePoints.map { $0.close } }
    var isPositive: Bool { priceChange >= 0 }
    /// Prior close, for the chart's dashed baseline — derived exactly as the full display
    /// model derives it, not shipped by the server, so there is one source for it.
    var previousClose: Double { currentPrice - priceChange }

    var formattedPrice: String { CommodityHeaderFormat.price(currentPrice) }
    var formattedChange: String { CommodityHeaderFormat.change(priceChange) }
    var formattedChangePercent: String {
        CommodityHeaderFormat.changePercent(priceChangePercent)
    }
}

/// What the price header and chart need, and nothing else.
///
/// The full display model and the fast-core model BOTH conform, which is what lets the
/// screen render `full ?? core` from one block of view code instead of two — two copies
/// of the header is how a core slice ends up rendering subtly differently from the model
/// that replaces it a second later.
///
/// Deliberately per-asset rather than one shared protocol: `marketStatus` is a different
/// enum on each screen, and flattening that to a String to share a protocol would throw
/// away the exhaustive switches the header views rely on.
protocol CommodityHeaderRenderable {
    var symbol: String { get }
    var name: String { get }
    var formattedPrice: String { get }
    var formattedChange: String { get }
    var formattedChangePercent: String { get }
    var isPositive: Bool { get }
    var marketStatus: CommodityMarketStatus { get }
    var chartPricePoints: [StockPricePoint] { get }
    var previousClose: Double { get }
}

extension CommodityDetailData: CommodityHeaderRenderable {}
extension CommodityCoreData: CommodityHeaderRenderable {}
