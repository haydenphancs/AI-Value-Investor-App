//
//  CommodityDetailResponseModels.swift
//  ios
//
//  Codable response DTOs for the /api/v1/commodities/{symbol} endpoint.
//  These decode the backend's snake_case JSON and map to UI models.
//

import Foundation

// MARK: - Top-Level Response

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
        let resolvedMarketStatus: CommodityMarketStatus
        switch marketStatus.lowercased() {
        case "open", "market open":
            resolvedMarketStatus = .open
        case "pre-market", "premarket":
            resolvedMarketStatus = .preMarket
        case "after-hours", "afterhours":
            resolvedMarketStatus = .afterHours
        default:
            resolvedMarketStatus = .closed(date: Date(), time: "", timezone: "ET")
        }

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
