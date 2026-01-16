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
    func getStockQuote(ticker: String) async throws -> StockQuote
    func getStockNews(ticker: String, limit: Int) async throws -> [StockNewsArticle]
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

    func getStockNews(ticker: String, limit: Int = 10) async throws -> [StockNewsArticle] {
        let cacheKey = "news_\(ticker)"

        // Cache news for 5 minutes
        if let cached: [StockNewsArticle] = getCached(cacheKey, maxAge: 300) {
            return cached
        }

        let news = try await apiClient.request(
            endpoint: .getStockNews(ticker: ticker, limit: limit),
            responseType: [StockNewsArticle].self
        )

        setCache(cacheKey, value: news)
        return news
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

    enum CodingKeys: String, CodingKey {
        case ticker
        case companyName = "company_name"
        case exchange, sector
        case logoUrl = "logo_url"
    }
}

struct StockDetail: Codable, Identifiable {
    var id: String { ticker }
    let ticker: String
    let companyName: String
    let exchange: String
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

    enum CodingKeys: String, CodingKey {
        case ticker
        case companyName = "company_name"
        case exchange, sector, industry, description, website
        case logoUrl = "logo_url"
        case marketCap = "market_cap"
        case price, change
        case changePercent = "change_percent"
        case volume
        case avgVolume = "avg_volume"
        case high52Week = "high_52_week"
        case low52Week = "low_52_week"
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
    let price: Double
    let change: Double
    let changePercent: Double
    let open: Double?
    let high: Double?
    let low: Double?
    let previousClose: Double?
    let volume: Double?
    let timestamp: String

    enum CodingKeys: String, CodingKey {
        case ticker, price, change
        case changePercent = "change_percent"
        case open, high, low
        case previousClose = "previous_close"
        case volume, timestamp
    }
}

struct StockNewsArticle: Codable, Identifiable {
    let id: String
    let title: String
    let summary: String?
    let source: String
    let publishedAt: String
    let url: String
    let imageUrl: String?
    let sentiment: String?
    let relatedTickers: [String]?

    enum CodingKeys: String, CodingKey {
        case id, title, summary, source
        case publishedAt = "published_at"
        case url
        case imageUrl = "image_url"
        case sentiment
        case relatedTickers = "related_tickers"
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
            low52Week: 124.17
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
            timestamp: ISO8601DateFormatter().string(from: Date())
        )
    }

    func getStockNews(ticker: String, limit: Int) async throws -> [StockNewsArticle] {
        [
            StockNewsArticle(
                id: "1",
                title: "Apple Reports Strong Q4 Earnings",
                summary: "Apple exceeded analyst expectations...",
                source: "Reuters",
                publishedAt: ISO8601DateFormatter().string(from: Date()),
                url: "https://example.com/news/1",
                imageUrl: nil,
                sentiment: "positive",
                relatedTickers: ["AAPL"]
            )
        ]
    }
}
#endif
