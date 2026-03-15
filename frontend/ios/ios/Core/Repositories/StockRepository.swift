//
//  StockRepository.swift
//  ios
//
//  Repository Pattern - Data Access Layer
//
//  Repositories abstract data fetching and caching from ViewModels:
//  - ViewModels call repositories for data
//  - Repositories handle: API calls, caching, data transformation
//  - Single source of truth for data operations
//
//  Benefits:
//  - ViewModels stay focused on UI logic
//  - Easy to add caching without changing ViewModels
//  - Testable with mock repositories
//

import Foundation
import SwiftUI

// MARK: - Stock Repository Protocol

/// Protocol for stock data access
/// Allows mocking for tests and previews
@MainActor
protocol StockRepositoryProtocol {
    func searchStocks(query: String, limit: Int) async throws -> [StockSearchResult]
    func getStock(ticker: String) async throws -> StockDetail
    func getStockOverview(ticker: String, range: String, interval: String?, extendedHours: Bool) async throws -> StockOverviewResponseDTO
    func getStockQuote(ticker: String) async throws -> StockQuote
    func getStockNews(ticker: String, limit: Int) async throws -> TickerNewsFeedResponse
    func enrichStockNews(ticker: String, articleIds: [String]) async throws -> EnrichStockNewsResponse
    func getStockChart(ticker: String, range: String, interval: String?, extendedHours: Bool) async throws -> StockChartResponse
    func getAnalystAnalysis(ticker: String) async throws -> AnalystAnalysisDTO
    func getSentimentAnalysis(ticker: String) async throws -> SentimentAnalysisDTO
    func getTechnicalAnalysis(ticker: String) async throws -> TechnicalAnalysisDTO
    func getTechnicalAnalysisDetail(ticker: String) async throws -> TechnicalAnalysisDetailDTO
    func getChartEvents(ticker: String) async throws -> ChartEventDates
    func getEarnings(ticker: String) async throws -> EarningsDTO
}

// MARK: - Stock Repository

/// Repository for stock-related data operations
@MainActor
final class StockRepository: StockRepositoryProtocol {

    private let apiClient: APIClient
    private var cache: [String: CacheEntry] = [:]

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
    }

    // MARK: - Search

    func searchStocks(query: String, limit: Int = 10) async throws -> [StockSearchResult] {
        try await apiClient.request(
            endpoint: .searchStocks(query: query, limit: limit),
            responseType: [StockSearchResult].self
        )
    }

    // MARK: - Stock Detail

    func getStock(ticker: String) async throws -> StockDetail {
        let cacheKey = "stock_\(ticker)"

        // Check cache
        if let cached: StockDetail = getCached(cacheKey, maxAge: 300) {
            // Trigger background refresh if stale
            if isCacheStale(cacheKey, maxAge: 60) {
                Task {
                    try? await refreshStock(ticker)
                }
            }
            return cached
        }

        // Fetch from API
        let stock = try await apiClient.request(
            endpoint: .getStock(ticker: ticker),
            responseType: StockDetail.self
        )

        setCache(cacheKey, value: stock)
        return stock
    }

    private func refreshStock(_ ticker: String) async throws {
        let stock = try await apiClient.request(
            endpoint: .getStock(ticker: ticker),
            responseType: StockDetail.self
        )
        setCache("stock_\(ticker)", value: stock)
    }

    // MARK: - Overview (aggregated endpoint)

    func getStockOverview(ticker: String, range: String = "3M", interval: String? = nil, extendedHours: Bool = false) async throws -> StockOverviewResponseDTO {
        let cacheKey = "overview_\(ticker)_\(range)_\(interval ?? "default")_\(extendedHours)"

        if let cached: StockOverviewResponseDTO = getCached(cacheKey, maxAge: 300) {
            return cached
        }

        let response = try await apiClient.request(
            endpoint: .getStockOverview(ticker: ticker, range: range, interval: interval, extendedHours: extendedHours),
            responseType: StockOverviewResponseDTO.self
        )

        setCache(cacheKey, value: response)
        print("✅ StockRepository: Got overview for \(ticker), range=\(range), interval=\(interval ?? "default"), extendedHours=\(extendedHours)")
        return response
    }

    // MARK: - Quote

    func getStockQuote(ticker: String) async throws -> StockQuote {
        let cacheKey = "quote_\(ticker)"

        // Short cache for quotes (1 minute)
        if let cached: StockQuote = getCached(cacheKey, maxAge: 60) {
            return cached
        }

        let quote = try await apiClient.request(
            endpoint: .getStockQuote(ticker: ticker),
            responseType: StockQuote.self
        )

        setCache(cacheKey, value: quote)
        return quote
    }

    // MARK: - News

    func getStockNews(ticker: String, limit: Int = 50) async throws -> TickerNewsFeedResponse {
        let cacheKey = "news_\(ticker)"

        // Cache news for 5 minutes
        if let cached: TickerNewsFeedResponse = getCached(cacheKey, maxAge: 300) {
            return cached
        }

        let response = try await apiClient.request(
            endpoint: .getStockNews(ticker: ticker, limit: limit),
            responseType: TickerNewsFeedResponse.self
        )

        setCache(cacheKey, value: response)
        return response
    }

    func enrichStockNews(ticker: String, articleIds: [String]) async throws -> EnrichStockNewsResponse {
        let response = try await apiClient.request(
            endpoint: .enrichStockNews(ticker: ticker, articleIds: articleIds),
            responseType: EnrichStockNewsResponse.self
        )
        return response
    }

    // MARK: - Chart

    func getStockChart(ticker: String, range: String, interval: String? = nil, extendedHours: Bool = false) async throws -> StockChartResponse {
        let cacheKey = "chart_\(ticker)_\(range)_\(interval ?? "default")_\(extendedHours)"

        // Cache chart data for 5 minutes
        if let cached: StockChartResponse = getCached(cacheKey, maxAge: 300) {
            return cached
        }

        let chart = try await apiClient.request(
            endpoint: .getStockChart(ticker: ticker, range: range, interval: interval, extendedHours: extendedHours),
            responseType: StockChartResponse.self
        )

        setCache(cacheKey, value: chart)
        return chart
    }

    // MARK: - Analyst Analysis

    func getAnalystAnalysis(ticker: String) async throws -> AnalystAnalysisDTO {
        let cacheKey = "analyst_\(ticker)"

        if let cached: AnalystAnalysisDTO = getCached(cacheKey, maxAge: 300) {
            return cached
        }

        let response = try await apiClient.request(
            endpoint: .getAnalystAnalysis(ticker: ticker),
            responseType: AnalystAnalysisDTO.self
        )

        setCache(cacheKey, value: response)
        print("✅ StockRepository: Got analyst analysis for \(ticker) — \(response.totalAnalysts) analysts")
        return response
    }

    // MARK: - Sentiment Analysis

    func getSentimentAnalysis(ticker: String) async throws -> SentimentAnalysisDTO {
        let cacheKey = "sentiment_\(ticker)"

        if let cached: SentimentAnalysisDTO = getCached(cacheKey, maxAge: 900) {
            return cached
        }

        let response = try await apiClient.request(
            endpoint: .getSentimentAnalysis(ticker: ticker),
            responseType: SentimentAnalysisDTO.self
        )

        setCache(cacheKey, value: response)
        print("✅ StockRepository: Got sentiment for \(ticker) — mood: \(response.moodScore)")
        return response
    }

    // MARK: - Technical Analysis

    func getTechnicalAnalysis(ticker: String) async throws -> TechnicalAnalysisDTO {
        let cacheKey = "technical_\(ticker)"

        if let cached: TechnicalAnalysisDTO = getCached(cacheKey, maxAge: 1800) {
            return cached
        }

        let response = try await apiClient.request(
            endpoint: .getTechnicalAnalysis(ticker: ticker),
            responseType: TechnicalAnalysisDTO.self
        )

        setCache(cacheKey, value: response)
        print("✅ StockRepository: Got technical analysis for \(ticker) — gauge: \(response.gaugeValue)")
        return response
    }

    // MARK: - Technical Analysis Detail

    func getTechnicalAnalysisDetail(ticker: String) async throws -> TechnicalAnalysisDetailDTO {
        let cacheKey = "tech_detail_\(ticker)"

        if let cached: TechnicalAnalysisDetailDTO = getCached(cacheKey, maxAge: 1800) {
            return cached
        }

        let response = try await apiClient.request(
            endpoint: .getTechnicalAnalysisDetail(ticker: ticker),
            responseType: TechnicalAnalysisDetailDTO.self
        )

        setCache(cacheKey, value: response)
        print("✅ StockRepository: Got technical analysis detail for \(ticker)")
        return response
    }

    func getChartEvents(ticker: String) async throws -> ChartEventDates {
        let cacheKey = "chart_events_\(ticker)"

        if let cached: ChartEventDates = getCached(cacheKey, maxAge: 3600) {
            return cached
        }

        let response = try await apiClient.request(
            endpoint: .getChartEvents(ticker: ticker),
            responseType: ChartEventDates.self
        )

        setCache(cacheKey, value: response)
        print("✅ StockRepository: Got chart events for \(ticker)")
        return response
    }

    // MARK: - Earnings

    func getEarnings(ticker: String) async throws -> EarningsDTO {
        let cacheKey = "earnings_\(ticker)"

        if let cached: EarningsDTO = getCached(cacheKey, maxAge: 300) {
            return cached
        }

        let response = try await apiClient.request(
            endpoint: .getEarnings(ticker: ticker),
            responseType: EarningsDTO.self
        )

        setCache(cacheKey, value: response)
        print("✅ StockRepository: Got earnings for \(ticker) — \(response.epsQuarters.count) EPS quarters")
        return response
    }

    // MARK: - Cache Helpers

    private struct CacheEntry {
        let data: Any
        let timestamp: Date
    }

    private func getCached<T>(_ key: String, maxAge: TimeInterval) -> T? {
        guard let entry = cache[key],
              Date().timeIntervalSince(entry.timestamp) < maxAge,
              let value = entry.data as? T else {
            return nil
        }
        return value
    }

    private func isCacheStale(_ key: String, maxAge: TimeInterval) -> Bool {
        guard let entry = cache[key] else { return true }
        return Date().timeIntervalSince(entry.timestamp) > maxAge
    }

    private func setCache(_ key: String, value: Any) {
        cache[key] = CacheEntry(data: value, timestamp: Date())
    }

    func clearCache() {
        cache.removeAll()
    }
}

// MARK: - Stock Models (DTO)

struct StockSearchResult: Codable, Identifiable {
    var id: String { ticker }
    let ticker: String
    let companyName: String
    let exchange: String?
    let sector: String?
    let logoUrl: String?

    // Backend GET /api/v1/stocks/search returns: symbol, name, exchange_short_name, exchange_full_name, currency
    // sector and logo_url are not in search results (Optional — decode as nil)
    enum CodingKeys: String, CodingKey {
        case ticker = "symbol"
        case companyName = "name"
        case exchange = "exchange_short_name"
        case sector
        case logoUrl = "logo_url"
    }
}

struct StockDetail: Codable, Identifiable {
    var id: String { ticker }
    let ticker: String
    let companyName: String
    let exchange: String?
    let sector: String?
    let industry: String?
    let description: String?
    let website: String?
    let logoUrl: String?
    let marketCap: Double?
    let price: Double?
    let change: Double?
    let changePercent: Double?
    let volume: Double?
    let avgVolume: Double?
    let high52Week: Double?
    let low52Week: Double?
    // Additional fields from FMP profile
    let beta: Double?
    let lastDiv: Double?
    let ceo: String?
    let fullTimeEmployees: Int?
    let country: String?
    let city: String?
    let state: String?
    let ipoDate: String?
    let dcf: Double?

    // Backend (FMP profile) returns: symbol, company_name, image, changes, vol_avg, market_cap,
    // beta, last_div, ceo, full_time_employees, country, city, state, ipo_date, dcf
    enum CodingKeys: String, CodingKey {
        case ticker = "symbol"
        case companyName = "company_name"
        case exchange, sector, industry, description, website
        case logoUrl = "image"
        case marketCap = "market_cap"
        case price
        case change = "changes"
        case changePercent = "change_percent"
        case volume
        case avgVolume = "vol_avg"
        case high52Week = "year_high"
        case low52Week = "year_low"
        case beta
        case lastDiv = "last_div"
        case ceo
        case fullTimeEmployees = "full_time_employees"
        case country, city, state
        case ipoDate = "ipo_date"
        case dcf
    }

    var isPositive: Bool {
        (changePercent ?? 0) >= 0
    }

    var formattedPrice: String {
        guard let price = price else { return "--" }
        return String(format: "$%.2f", price)
    }

    var formattedChange: String {
        guard let change = change else { return "--" }
        let sign = change >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", change))"
    }

    var formattedChangePercent: String {
        guard let percent = changePercent else { return "--" }
        let sign = percent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", percent))%"
    }

    var formattedMarketCap: String {
        guard let cap = marketCap else { return "--" }
        if cap >= 1_000_000_000_000 {
            return String(format: "$%.2fT", cap / 1_000_000_000_000)
        } else if cap >= 1_000_000_000 {
            return String(format: "$%.2fB", cap / 1_000_000_000)
        } else if cap >= 1_000_000 {
            return String(format: "$%.2fM", cap / 1_000_000)
        }
        return String(format: "$%.0f", cap)
    }
}

struct StockQuote: Codable {
    let ticker: String
    let price: Double?
    let change: Double?
    let changePercent: Double?
    let open: Double?
    let high: Double?
    let low: Double?
    let previousClose: Double?
    let volume: Double?
    let timestamp: Int?
    // Additional fields from FMP quote
    let eps: Double?
    let pe: Double?
    let sharesOutstanding: Double?
    let avgVolume: Double?

    // Backend (FMP quote) returns: symbol, changes_percentage, day_high, day_low, timestamp (unix int),
    // eps, pe, shares_outstanding, avg_volume
    enum CodingKeys: String, CodingKey {
        case ticker = "symbol"
        case price, change
        case changePercent = "changes_percentage"
        case open
        case high = "day_high"
        case low = "day_low"
        case previousClose = "previous_close"
        case volume, timestamp
        case eps, pe
        case sharesOutstanding = "shares_outstanding"
        case avgVolume = "avg_volume"
    }
}

struct StockNewsArticle: Codable, Identifiable {
    let id: String
    let title: String
    let summary: String?
    let summaryBullets: [String]?
    let source: String?
    let publishedAt: String?
    let url: String?
    let imageUrl: String?
    let sentiment: String?
    let sentimentConfidence: Int?
    let relatedTickers: [String]?
    let aiProcessed: Bool?

    // Backend returns: headline, source_name, thumbnail_url, article_url, published_at,
    // summary_bullets, sentiment_confidence, ai_processed (from ticker_news_cache)
    enum CodingKeys: String, CodingKey {
        case id
        case title = "headline"
        case summary
        case summaryBullets = "summary_bullets"
        case source = "source_name"
        case publishedAt = "published_at"
        case url = "article_url"
        case imageUrl = "thumbnail_url"
        case sentiment
        case sentimentConfidence = "sentiment_confidence"
        case relatedTickers = "related_tickers"
        case aiProcessed = "ai_processed"
    }
}

// MARK: - Ticker News Feed Response (wrapper from /stocks/{ticker}/news)

struct TickerNewsFeedResponse: Codable {
    let articles: [StockNewsArticle]
    let ticker: String
    let cached: Bool?
    let cacheAgeSeconds: Int?

    enum CodingKeys: String, CodingKey {
        case articles, ticker, cached
        case cacheAgeSeconds = "cache_age_seconds"
    }
}

// MARK: - Enrich Stock News Response (from POST /stocks/{ticker}/news/enrich)

struct EnrichStockNewsResponse: Codable {
    let articles: [StockNewsArticle]
    let ticker: String
}

// MARK: - Stock Chart Response

struct StockChartResponse: Codable {
    let symbol: String
    let prices: [StockPricePoint]
}

struct StockPricePoint: Codable {
    let date: String
    let close: Double
    let open: Double?
    let high: Double?
    let low: Double?
    let volume: Double?

    /// Whether this data point falls outside regular US market hours (09:30–16:00 ET).
    var isExtendedHours: Bool {
        // Only intraday data has time components (length > 10, e.g. "2025-03-10 08:30:00")
        guard date.count > 10 else { return false }
        let timeStr = String(date.suffix(from: date.index(date.startIndex, offsetBy: 11)))
        let parts = timeStr.split(separator: ":")
        guard parts.count >= 2,
              let hour = Int(parts[0]),
              let minute = Int(parts[1]) else { return false }
        let totalMinutes = hour * 60 + minute
        // Regular hours: 09:30 (570) to 15:59 (959)
        return totalMinutes < 570 || totalMinutes >= 960
    }
}

// MARK: - Analyst Analysis DTOs

struct AnalystAnalysisDTO: Codable {
    let symbol: String
    let totalAnalysts: Int
    let updatedDate: String
    let consensus: String
    let targetPrice: Double
    let targetUpside: Double
    let distributions: [AnalystDistributionDTO]
    let priceTarget: AnalystPriceTargetDTO
    let momentumData: [AnalystMomentumDTO]
    let netPositive: Int
    let netNegative: Int
    let actionsSummary: AnalystActionsSummaryDTO
    let actions: [AnalystActionDTO]

    enum CodingKeys: String, CodingKey {
        case symbol
        case totalAnalysts = "total_analysts"
        case updatedDate = "updated_date"
        case consensus
        case targetPrice = "target_price"
        case targetUpside = "target_upside"
        case distributions
        case priceTarget = "price_target"
        case momentumData = "momentum_data"
        case netPositive = "net_positive"
        case netNegative = "net_negative"
        case actionsSummary = "actions_summary"
        case actions
    }
}

struct AnalystDistributionDTO: Codable {
    let label: String
    let count: Int
}

struct AnalystPriceTargetDTO: Codable {
    let lowPrice: Double
    let averagePrice: Double
    let highPrice: Double
    let currentPrice: Double

    enum CodingKeys: String, CodingKey {
        case lowPrice = "low_price"
        case averagePrice = "average_price"
        case highPrice = "high_price"
        case currentPrice = "current_price"
    }
}

struct AnalystMomentumDTO: Codable {
    let month: String
    let positiveCount: Int
    let negativeCount: Int

    enum CodingKeys: String, CodingKey {
        case month
        case positiveCount = "positive_count"
        case negativeCount = "negative_count"
    }
}

struct AnalystActionsSummaryDTO: Codable {
    let upgrades: Int
    let maintains: Int
    let downgrades: Int
}

struct AnalystActionDTO: Codable {
    let firmName: String
    let actionType: String
    let date: String
    let previousRating: String?
    let newRating: String
    let previousPriceTarget: Double?
    let newPriceTarget: Double?

    enum CodingKeys: String, CodingKey {
        case firmName = "firm_name"
        case actionType = "action_type"
        case date
        case previousRating = "previous_rating"
        case newRating = "new_rating"
        case previousPriceTarget = "previous_price_target"
        case newPriceTarget = "new_price_target"
    }
}

// MARK: - Sentiment Analysis DTO

struct SentimentAnalysisDTO: Codable {
    let symbol: String
    // 24h
    let moodScore: Int
    let last24hMood: String
    let socialMentions: Double
    let socialMentionsChange: Double
    let newsArticles: Int
    let newsArticlesChange: Double
    // 7d
    let moodScore7d: Int
    let last7dMood: String
    let socialMentions7d: Double
    let socialMentionsChange7d: Double
    let newsArticles7d: Int
    let newsArticlesChange7d: Double

    enum CodingKeys: String, CodingKey {
        case symbol
        case moodScore = "mood_score"
        case last24hMood = "last_24h_mood"
        case socialMentions = "social_mentions"
        case socialMentionsChange = "social_mentions_change"
        case newsArticles = "news_articles"
        case newsArticlesChange = "news_articles_change"
        case moodScore7d = "mood_score_7d"
        case last7dMood = "last_7d_mood"
        case socialMentions7d = "social_mentions_7d"
        case socialMentionsChange7d = "social_mentions_change_7d"
        case newsArticles7d = "news_articles_7d"
        case newsArticlesChange7d = "news_articles_change_7d"
    }

    func toDisplayModel() -> SentimentAnalysisData {
        SentimentAnalysisData(
            moodScore: moodScore,
            last24hMood: MarketMoodLevel.fromScore(moodScore),
            socialMentions: socialMentions,
            socialMentionsChange: socialMentionsChange,
            newsArticles: newsArticles,
            newsArticlesChange: newsArticlesChange,
            moodScore7d: moodScore7d,
            last7dMood: MarketMoodLevel.fromScore(moodScore7d),
            socialMentions7d: socialMentions7d,
            socialMentionsChange7d: socialMentionsChange7d,
            newsArticles7d: newsArticles7d,
            newsArticlesChange7d: newsArticlesChange7d
        )
    }
}

// MARK: - Technical Analysis DTO

struct TechnicalAnalysisDTO: Codable {
    let symbol: String
    let dailySignal: TechnicalIndicatorResult
    let weeklySignal: TechnicalIndicatorResult
    let overallSignal: TechnicalSignal
    let gaugeValue: Double

    enum CodingKeys: String, CodingKey {
        case symbol
        case dailySignal = "daily_signal"
        case weeklySignal = "weekly_signal"
        case overallSignal = "overall_signal"
        case gaugeValue = "gauge_value"
    }

    func toDisplayModel() -> TechnicalAnalysisData {
        TechnicalAnalysisData(
            dailySignal: dailySignal,
            weeklySignal: weeklySignal,
            overallSignal: overallSignal,
            gaugeValue: gaugeValue
        )
    }
}

// MARK: - Technical Analysis Detail DTO

struct MovingAverageIndicatorDTO: Codable {
    let name: String
    let value: Double?
    let signal: String

    func toDisplayModel() -> MovingAverageIndicator {
        MovingAverageIndicator(
            name: name,
            value: value ?? 0,
            signal: IndicatorSignal(rawValue: signal) ?? .neutral
        )
    }
}

struct OscillatorIndicatorDTO: Codable {
    let name: String
    let value: Double?
    let signal: String

    func toDisplayModel() -> OscillatorIndicator {
        OscillatorIndicator(
            name: name,
            value: value ?? 0,
            signal: IndicatorSignal(rawValue: signal) ?? .neutral
        )
    }
}

struct IndicatorSummaryDTO: Codable {
    let buyCount: Int
    let neutralCount: Int
    let sellCount: Int

    enum CodingKeys: String, CodingKey {
        case buyCount = "buy_count"
        case neutralCount = "neutral_count"
        case sellCount = "sell_count"
    }

    func toDisplayModel() -> IndicatorSummary {
        IndicatorSummary(buyCount: buyCount, neutralCount: neutralCount, sellCount: sellCount)
    }
}

struct PivotPointLevelDTO: Codable {
    let name: String
    let value: Double
    let levelType: String

    enum CodingKeys: String, CodingKey {
        case name, value
        case levelType = "level_type"
    }

    func toDisplayModel() -> PivotPointLevel {
        let type: PivotLevelType
        switch levelType {
        case "resistance": type = .resistance
        case "support": type = .support
        default: type = .pivot
        }
        return PivotPointLevel(name: name, value: value, levelType: type)
    }
}

struct PivotPointsDTO: Codable {
    let method: String
    let levels: [PivotPointLevelDTO]

    func toDisplayModel() -> PivotPointsData {
        PivotPointsData(method: method, levels: levels.map { $0.toDisplayModel() })
    }
}

struct VolumeAnalysisDTO: Codable {
    let currentVolume: Double
    let currentVolumeChange: Double
    let avgVolume30d: Double
    let volumeTrend: String
    let obv: Double
    let moneyFlowIndex: Double

    enum CodingKeys: String, CodingKey {
        case currentVolume = "current_volume"
        case currentVolumeChange = "current_volume_change"
        case avgVolume30d = "avg_volume_30d"
        case volumeTrend = "volume_trend"
        case obv
        case moneyFlowIndex = "money_flow_index"
    }

    func toDisplayModel() -> VolumeAnalysisData {
        VolumeAnalysisData(
            currentVolume: currentVolume,
            currentVolumeChange: currentVolumeChange,
            avgVolume30d: avgVolume30d,
            volumeTrend: VolumeTrend(rawValue: volumeTrend) ?? .stable,
            obv: obv,
            moneyFlowIndex: moneyFlowIndex
        )
    }
}

struct FibonacciLevelDTO: Codable {
    let percentage: String
    let value: Double
    let isKey: Bool

    enum CodingKeys: String, CodingKey {
        case percentage, value
        case isKey = "is_key"
    }

    func toDisplayModel() -> FibonacciLevel {
        FibonacciLevel(percentage: percentage, value: value, isKey: isKey)
    }
}

struct FibonacciRetracementDTO: Codable {
    let timeframe: String
    let levels: [FibonacciLevelDTO]

    func toDisplayModel() -> FibonacciRetracementData {
        FibonacciRetracementData(timeframe: timeframe, levels: levels.map { $0.toDisplayModel() })
    }
}

struct SupportResistanceLevelDTO: Codable {
    let name: String
    let value: Double
    let strength: String

    func toDisplayModel() -> SupportResistanceLevel {
        SupportResistanceLevel(
            name: name,
            value: value,
            strength: LevelStrength(rawValue: strength) ?? .moderate
        )
    }
}

struct SupportResistanceDTO: Codable {
    let currentPrice: Double
    let resistanceLevels: [SupportResistanceLevelDTO]
    let supportLevels: [SupportResistanceLevelDTO]

    enum CodingKeys: String, CodingKey {
        case currentPrice = "current_price"
        case resistanceLevels = "resistance_levels"
        case supportLevels = "support_levels"
    }

    func toDisplayModel() -> SupportResistanceData {
        SupportResistanceData(
            currentPrice: currentPrice,
            resistanceLevels: resistanceLevels.map { $0.toDisplayModel() },
            supportLevels: supportLevels.map { $0.toDisplayModel() }
        )
    }
}

struct TechnicalAnalysisDetailDTO: Codable {
    let symbol: String
    let movingAverages: [MovingAverageIndicatorDTO]
    let movingAveragesSummary: IndicatorSummaryDTO
    let oscillators: [OscillatorIndicatorDTO]
    let oscillatorsSummary: IndicatorSummaryDTO
    let pivotPoints: PivotPointsDTO
    let volumeAnalysis: VolumeAnalysisDTO
    let fibonacciRetracement: FibonacciRetracementDTO
    let supportResistance: SupportResistanceDTO

    enum CodingKeys: String, CodingKey {
        case symbol
        case movingAverages = "moving_averages"
        case movingAveragesSummary = "moving_averages_summary"
        case oscillators
        case oscillatorsSummary = "oscillators_summary"
        case pivotPoints = "pivot_points"
        case volumeAnalysis = "volume_analysis"
        case fibonacciRetracement = "fibonacci_retracement"
        case supportResistance = "support_resistance"
    }

    func toDisplayModel() -> TechnicalAnalysisDetailData {
        TechnicalAnalysisDetailData(
            symbol: symbol,
            movingAverages: movingAverages.map { $0.toDisplayModel() },
            movingAveragesSummary: movingAveragesSummary.toDisplayModel(),
            oscillators: oscillators.map { $0.toDisplayModel() },
            oscillatorsSummary: oscillatorsSummary.toDisplayModel(),
            pivotPoints: pivotPoints.toDisplayModel(),
            volumeAnalysis: volumeAnalysis.toDisplayModel(),
            fibonacciRetracement: fibonacciRetracement.toDisplayModel(),
            supportResistance: supportResistance.toDisplayModel()
        )
    }
}

// MARK: - DTO → Display Model Mapper

extension AnalystAnalysisDTO {
    /// Convert backend DTO to the display model used by SwiftUI views.
    func toDisplayModel() -> AnalystRatingsData {
        let dateFormatter = DateFormatter()
        dateFormatter.dateFormat = "yyyy-MM-dd"
        dateFormatter.locale = Locale(identifier: "en_US_POSIX")
        let parsedDate = dateFormatter.date(from: updatedDate) ?? Date()

        let consensusEnum = AnalystConsensus(rawValue: consensus) ?? .hold

        let distColors: [String: Color] = [
            "Strong Buy": AppColors.bullish,
            "Buy": Color(hex: "4ADE80"),
            "Hold": AppColors.neutral,
            "Sell": AppColors.bearish,
            "Strong Sell": Color(hex: "991B1B"),
        ]
        let distModels = distributions.map { dto in
            AnalystRatingDistribution(
                label: dto.label,
                count: dto.count,
                color: distColors[dto.label] ?? AppColors.textSecondary
            )
        }

        let pt = AnalystPriceTarget(
            lowPrice: priceTarget.lowPrice,
            averagePrice: priceTarget.averagePrice,
            highPrice: priceTarget.highPrice,
            currentPrice: priceTarget.currentPrice
        )

        let momentum = momentumData.map { dto in
            AnalystMomentumMonth(
                month: dto.month,
                positiveCount: dto.positiveCount,
                negativeCount: dto.negativeCount
            )
        }

        let summary = AnalystActionsSummary(
            upgrades: actionsSummary.upgrades,
            maintains: actionsSummary.maintains,
            downgrades: actionsSummary.downgrades
        )

        let actionModels = actions.map { dto in
            AnalystAction(
                firmName: dto.firmName,
                actionType: AnalystActionType(rawValue: dto.actionType) ?? .maintain,
                date: dateFormatter.date(from: dto.date) ?? Date(),
                previousRating: dto.previousRating.flatMap { Self.mapRatingType($0) },
                newRating: Self.mapRatingType(dto.newRating) ?? .neutral,
                previousPriceTarget: dto.previousPriceTarget,
                newPriceTarget: dto.newPriceTarget
            )
        }

        return AnalystRatingsData(
            totalAnalysts: totalAnalysts,
            updatedDate: parsedDate,
            consensus: consensusEnum,
            targetPrice: targetPrice,
            targetUpside: targetUpside,
            distributions: distModels,
            priceTarget: pt,
            momentumData: momentum,
            netPositive: netPositive,
            netNegative: netNegative,
            actionsSummary: summary,
            actions: actionModels
        )
    }

    /// Map raw FMP grade string → AnalystRatingType enum.
    private static func mapRatingType(_ raw: String) -> AnalystRatingType? {
        let lower = raw.lowercased().trimmingCharacters(in: .whitespaces)
        switch lower {
        case "strong buy", "long term buy":
            return .strongBuy
        case "buy", "positive", "accumulate":
            return .buy
        case "outperform", "overweight", "market outperform", "sector outperform":
            return .overweight
        case "equal-weight", "equal weight":
            return .equalWeight
        case "neutral", "hold", "market perform", "sector perform",
             "peer perform", "in-line", "in line", "perform", "sector weight":
            return .neutral
        case "underperform", "underweight", "negative", "reduce":
            return .underperform
        case "sell":
            return .sell
        case "strong sell":
            return .strongSell
        default:
            return nil
        }
    }
}

// MARK: - Earnings DTOs

struct EarningsQuarterDTO: Codable {
    let quarter: String
    let actualValue: Double?
    let estimateValue: Double
    let surprisePercent: Double?

    enum CodingKeys: String, CodingKey {
        case quarter
        case actualValue = "actual_value"
        case estimateValue = "estimate_value"
        case surprisePercent = "surprise_percent"
    }
}

struct EarningsPricePointDTO: Codable {
    let quarter: String
    let price: Double
}

struct EarningsDailyPricePointDTO: Codable {
    let date: String
    let price: Double
}

struct NextEarningsDateDTO: Codable {
    let date: String
    let isConfirmed: Bool
    let timing: String

    enum CodingKeys: String, CodingKey {
        case date
        case isConfirmed = "is_confirmed"
        case timing
    }
}

struct EarningsDTO: Codable {
    let symbol: String
    let epsQuarters: [EarningsQuarterDTO]
    let revenueQuarters: [EarningsQuarterDTO]
    let priceHistory: [EarningsPricePointDTO]
    let dailyPriceHistory: [EarningsDailyPricePointDTO]?
    let nextEarningsDate: NextEarningsDateDTO?

    enum CodingKeys: String, CodingKey {
        case symbol
        case epsQuarters = "eps_quarters"
        case revenueQuarters = "revenue_quarters"
        case priceHistory = "price_history"
        case dailyPriceHistory = "daily_price_history"
        case nextEarningsDate = "next_earnings_date"
    }

    func toDisplayModel() -> EarningsData {
        let dateFormatter = DateFormatter()
        dateFormatter.dateFormat = "yyyy-MM-dd"
        dateFormatter.locale = Locale(identifier: "en_US_POSIX")

        let eps = epsQuarters.map { q in
            EarningsQuarterData(
                quarter: q.quarter,
                actualValue: q.actualValue,
                estimateValue: q.estimateValue,
                surprisePercent: q.surprisePercent
            )
        }
        let revenue = revenueQuarters.map { q in
            EarningsQuarterData(
                quarter: q.quarter,
                actualValue: q.actualValue,
                estimateValue: q.estimateValue,
                surprisePercent: q.surprisePercent
            )
        }
        let prices = priceHistory.map { p in
            EarningsPricePoint(quarter: p.quarter, price: p.price)
        }
        let dailyPrices = (dailyPriceHistory ?? []).map { dp in
            EarningsDailyPricePoint(date: dp.date, price: dp.price)
        }
        var nextDate: NextEarningsDate? = nil
        if let nd = nextEarningsDate, let d = dateFormatter.date(from: nd.date) {
            nextDate = NextEarningsDate(
                date: d,
                isConfirmed: nd.isConfirmed,
                timing: EarningsReportTiming(rawValue: nd.timing) ?? .unknown
            )
        }
        return EarningsData(
            epsQuarters: eps,
            revenueQuarters: revenue,
            priceHistory: prices,
            dailyPriceHistory: dailyPrices,
            nextEarningsDate: nextDate
        )
    }
}

// MARK: - Mock Repository for Previews

#if DEBUG
@MainActor
final class MockStockRepository: StockRepositoryProtocol {

    func searchStocks(query: String, limit: Int) async throws -> [StockSearchResult] {
        [
            StockSearchResult(ticker: "AAPL", companyName: "Apple Inc.", exchange: "NASDAQ", sector: "Technology", logoUrl: nil),
            StockSearchResult(ticker: "MSFT", companyName: "Microsoft Corp.", exchange: "NASDAQ", sector: "Technology", logoUrl: nil)
        ]
    }

    func getStockOverview(ticker: String, range: String, interval: String? = nil, extendedHours: Bool = false) async throws -> StockOverviewResponseDTO {
        // Mock: just throw so ViewModel falls back to sample data
        throw URLError(.badServerResponse)
    }

    func getStock(ticker: String) async throws -> StockDetail {
        StockDetail(
            ticker: ticker,
            companyName: "Apple Inc.",
            exchange: "NASDAQ",
            sector: "Technology",
            industry: "Consumer Electronics",
            description: "Apple Inc. designs, manufactures, and markets smartphones, personal computers...",
            website: "apple.com",
            logoUrl: nil,
            marketCap: 3_000_000_000_000,
            price: 175.50,
            change: 2.35,
            changePercent: 1.36,
            volume: 50_000_000,
            avgVolume: 55_000_000,
            high52Week: 199.62,
            low52Week: 124.17,
            beta: 1.25,
            lastDiv: 0.24,
            ceo: "Tim Cook",
            fullTimeEmployees: 161000,
            country: "US",
            city: "Cupertino",
            state: "CA",
            ipoDate: "1980-12-12",
            dcf: 185.00
        )
    }

    func getStockQuote(ticker: String) async throws -> StockQuote {
        StockQuote(
            ticker: ticker,
            price: 175.50,
            change: 2.35,
            changePercent: 1.36,
            open: 173.15,
            high: 176.20,
            low: 172.80,
            previousClose: 173.15,
            volume: 50_000_000,
            timestamp: Int(Date().timeIntervalSince1970),
            eps: 6.75,
            pe: 26.0,
            sharesOutstanding: 15_638_000_000,
            avgVolume: 55_000_000
        )
    }

    func getStockNews(ticker: String, limit: Int) async throws -> TickerNewsFeedResponse {
        TickerNewsFeedResponse(
            articles: [
                StockNewsArticle(
                    id: "1",
                    title: "Apple Reports Strong Q4 Earnings",
                    summary: "Apple exceeded analyst expectations...",
                    summaryBullets: [
                        "Apple beat Wall Street estimates with strong iPhone and Services revenue growth",
                        "Services segment reached all-time high, signaling successful diversification",
                        "Management guided for continued growth despite broader economic uncertainty"
                    ],
                    source: "Reuters",
                    publishedAt: ISO8601DateFormatter().string(from: Date()),
                    url: "https://example.com/news/1",
                    imageUrl: nil,
                    sentiment: "bullish",
                    sentimentConfidence: 85,
                    relatedTickers: ["AAPL"],
                    aiProcessed: true
                )
            ],
            ticker: ticker,
            cached: true,
            cacheAgeSeconds: 0
        )
    }

    func enrichStockNews(ticker: String, articleIds: [String]) async throws -> EnrichStockNewsResponse {
        EnrichStockNewsResponse(articles: [], ticker: ticker)
    }

    func getStockChart(ticker: String, range: String, interval: String? = nil, extendedHours: Bool = false) async throws -> StockChartResponse {
        StockChartResponse(
            symbol: ticker,
            prices: [
                StockPricePoint(date: "2024-01-01", close: 170.0, open: 168.0, high: 171.0, low: 167.0, volume: 50_000_000),
                StockPricePoint(date: "2024-01-02", close: 172.0, open: 170.0, high: 173.0, low: 169.0, volume: 48_000_000),
                StockPricePoint(date: "2024-01-03", close: 175.0, open: 172.0, high: 176.0, low: 171.0, volume: 52_000_000)
            ]
        )
    }

    func getAnalystAnalysis(ticker: String) async throws -> AnalystAnalysisDTO {
        throw URLError(.badServerResponse)
    }

    func getSentimentAnalysis(ticker: String) async throws -> SentimentAnalysisDTO {
        throw URLError(.badServerResponse)
    }

    func getTechnicalAnalysis(ticker: String) async throws -> TechnicalAnalysisDTO {
        throw URLError(.badServerResponse)
    }

    func getTechnicalAnalysisDetail(ticker: String) async throws -> TechnicalAnalysisDetailDTO {
        throw URLError(.badServerResponse)
    }

    func getChartEvents(ticker: String) async throws -> ChartEventDates {
        ChartEventDates(earningsDates: ["2024-01-25", "2024-04-25", "2024-07-25", "2024-10-31"],
                        dividendDates: ["2024-02-09", "2024-05-10", "2024-08-12", "2024-11-01"])
    }

    func getEarnings(ticker: String) async throws -> EarningsDTO {
        throw URLError(.badServerResponse)
    }
}
#endif
