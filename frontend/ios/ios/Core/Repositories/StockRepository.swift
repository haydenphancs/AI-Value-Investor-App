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

// MARK: - Stock Repository Protocol

/// Protocol for stock data access
/// Allows mocking for tests and previews
@MainActor
protocol StockRepositoryProtocol {
    func searchStocks(query: String, limit: Int) async throws -> [StockSearchResult]
    func getStock(ticker: String) async throws -> StockDetail
    func getStockOverview(ticker: String, range: String, interval: String?) async throws -> StockOverviewResponseDTO
    func getStockQuote(ticker: String) async throws -> StockQuote
    func getStockNews(ticker: String, limit: Int) async throws -> TickerNewsFeedResponse
    func getStockChart(ticker: String, range: String, interval: String?) async throws -> StockChartResponse
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

    func getStockOverview(ticker: String, range: String = "3M", interval: String? = nil) async throws -> StockOverviewResponseDTO {
        let cacheKey = "overview_\(ticker)_\(range)_\(interval ?? "default")"

        if let cached: StockOverviewResponseDTO = getCached(cacheKey, maxAge: 300) {
            return cached
        }

        let response = try await apiClient.request(
            endpoint: .getStockOverview(ticker: ticker, range: range, interval: interval),
            responseType: StockOverviewResponseDTO.self
        )

        setCache(cacheKey, value: response)
        print("✅ StockRepository: Got overview for \(ticker), range=\(range), interval=\(interval ?? "default")")
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

    func getStockNews(ticker: String, limit: Int = 10) async throws -> TickerNewsFeedResponse {
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

    // MARK: - Chart

    func getStockChart(ticker: String, range: String, interval: String? = nil) async throws -> StockChartResponse {
        let cacheKey = "chart_\(ticker)_\(range)_\(interval ?? "default")"

        // Cache chart data for 5 minutes
        if let cached: StockChartResponse = getCached(cacheKey, maxAge: 300) {
            return cached
        }

        let chart = try await apiClient.request(
            endpoint: .getStockChart(ticker: ticker, range: range, interval: interval),
            responseType: StockChartResponse.self
        )

        setCache(cacheKey, value: chart)
        return chart
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

    func getStockOverview(ticker: String, range: String, interval: String? = nil) async throws -> StockOverviewResponseDTO {
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

    func getStockChart(ticker: String, range: String, interval: String? = nil) async throws -> StockChartResponse {
        StockChartResponse(
            symbol: ticker,
            prices: [
                StockPricePoint(date: "2024-01-01", close: 170.0, open: 168.0, high: 171.0, low: 167.0, volume: 50_000_000),
                StockPricePoint(date: "2024-01-02", close: 172.0, open: 170.0, high: 173.0, low: 169.0, volume: 48_000_000),
                StockPricePoint(date: "2024-01-03", close: 175.0, open: 172.0, high: 176.0, low: 171.0, volume: 52_000_000)
            ]
        )
    }
}
#endif
