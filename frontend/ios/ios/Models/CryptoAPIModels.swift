//
//  CryptoAPIModels.swift
//  ios
//
//  Codable response DTOs for the /api/v1/crypto/{symbol} endpoint.
//  These decode the backend's snake_case JSON and map to UI models.
//

import Foundation

// MARK: - Top-Level Response

struct CryptoDetailResponse: Codable {
    let symbol: String
    let name: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: String
    let chartData: [Double]
    let keyStatisticsGroups: [KeyStatisticsGroupDTO]
    let performancePeriods: [PerformancePeriodDTO]
    let snapshots: [CryptoSnapshotDTO]
    let cryptoProfile: CryptoProfileDTO
    let relatedCryptos: [RelatedCryptoDTO]
    let benchmarkSummary: BenchmarkSummaryDTO?
    let newsArticles: [CryptoNewsArticleDTO]

    enum CodingKeys: String, CodingKey {
        case symbol, name
        case currentPrice = "current_price"
        case priceChange = "price_change"
        case priceChangePercent = "price_change_percent"
        case marketStatus = "market_status"
        case chartData = "chart_data"
        case keyStatisticsGroups = "key_statistics_groups"
        case performancePeriods = "performance_periods"
        case snapshots
        case cryptoProfile = "crypto_profile"
        case relatedCryptos = "related_cryptos"
        case benchmarkSummary = "benchmark_summary"
        case newsArticles = "news_articles"
    }
}

// MARK: - Key Statistics DTOs

struct KeyStatisticDTO: Codable {
    let label: String
    let value: String
    let isHighlighted: Bool

    enum CodingKeys: String, CodingKey {
        case label, value
        case isHighlighted = "is_highlighted"
    }

    func toModel() -> KeyStatistic {
        KeyStatistic(label: label, value: value, isHighlighted: isHighlighted)
    }
}

struct KeyStatisticsGroupDTO: Codable {
    let statistics: [KeyStatisticDTO]

    func toModel() -> KeyStatisticsGroup {
        KeyStatisticsGroup(statistics: statistics.map { $0.toModel() })
    }
}

// MARK: - Performance Period DTO

struct PerformancePeriodDTO: Codable {
    let label: String
    let changePercent: Double
    let vsMarketPercent: Double?
    let benchmarkLabel: String?

    enum CodingKeys: String, CodingKey {
        case label
        case changePercent = "change_percent"
        case vsMarketPercent = "vs_market_percent"
        case benchmarkLabel = "benchmark_label"
    }

    func toModel() -> PerformancePeriod {
        PerformancePeriod(
            label: label,
            changePercent: changePercent,
            vsMarketPercent: vsMarketPercent,
            benchmarkLabel: benchmarkLabel ?? "BTC"
        )
    }
}

// MARK: - Crypto Snapshot DTO

struct CryptoSnapshotDTO: Codable {
    let category: String
    let paragraphs: [String]

    func toModel() -> CryptoSnapshotItem? {
        guard let cat = CryptoSnapshotCategory(rawValue: category) else { return nil }
        return CryptoSnapshotItem(category: cat, paragraphs: paragraphs)
    }
}

// MARK: - Crypto Profile DTO

struct CryptoProfileDTO: Codable {
    let description: String
    let symbol: String
    let launchDate: String
    let consensusMechanism: String
    let blockchain: String
    let website: String
    let whitepaper: String?

    enum CodingKeys: String, CodingKey {
        case description, symbol, blockchain, website, whitepaper
        case launchDate = "launch_date"
        case consensusMechanism = "consensus_mechanism"
    }

    func toModel() -> CryptoProfile {
        CryptoProfile(
            description: description,
            symbol: symbol,
            launchDate: launchDate,
            consensusMechanism: consensusMechanism,
            blockchain: blockchain,
            website: website,
            whitepaper: whitepaper
        )
    }
}

// MARK: - Related Crypto DTO

struct RelatedCryptoDTO: Codable {
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

// MARK: - Benchmark Summary DTO

struct BenchmarkSummaryDTO: Codable {
    let avgAnnualReturn: Double
    let spBenchmark: Double
    let benchmarkName: String?
    let sinceDate: String?
    let benchmarkSinceDate: String?
    let badgeThreshold: Double?

    enum CodingKeys: String, CodingKey {
        case avgAnnualReturn = "avg_annual_return"
        case spBenchmark = "sp_benchmark"
        case benchmarkName = "benchmark_name"
        case sinceDate = "since_date"
        case benchmarkSinceDate = "benchmark_since_date"
        case badgeThreshold = "badge_threshold"
    }

    func toModel() -> PerformanceBenchmarkSummary {
        PerformanceBenchmarkSummary(
            avgAnnualReturn: avgAnnualReturn,
            spBenchmark: spBenchmark,
            benchmarkName: benchmarkName ?? "Bitcoin (BTC)",
            sinceDate: sinceDate,
            benchmarkSinceDate: benchmarkSinceDate,
            badgeThreshold: badgeThreshold ?? 5.0
        )
    }
}

// MARK: - News Article DTO

struct CryptoNewsArticleDTO: Codable {
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
        // Parse the ISO date string
        let date: Date
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let parsed = formatter.date(from: publishedAt) {
            date = parsed
        } else {
            // Try without fractional seconds
            formatter.formatOptions = [.withInternetDateTime]
            if let parsed = formatter.date(from: publishedAt) {
                date = parsed
            } else {
                // Try a simpler format "2024-01-15 10:30:00"
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
            headline: headline,
            source: newsSource,
            sentiment: newsSentiment,
            publishedAt: date,
            thumbnailName: thumbnailUrl,
            relatedTickers: relatedTickers,
            summaryBullets: summaryBullets,
            articleURL: articleUrl.flatMap { URL(string: $0) }
        )
    }
}

// MARK: - CryptoDetailResponse → CryptoDetailData Mapping

extension CryptoDetailResponse {
    func toModel() -> CryptoDetailData {
        let resolvedMarketStatus: CryptoMarketStatus
        if self.marketStatus.starts(with: "Maintenance") {
            let resumeTime = self.marketStatus.replacingOccurrences(of: "Maintenance - Resumes ", with: "")
            resolvedMarketStatus = .maintenance(resumeTime: resumeTime)
        } else {
            resolvedMarketStatus = .trading
        }

        return CryptoDetailData(
            symbol: symbol,
            name: name,
            currentPrice: currentPrice,
            priceChange: priceChange,
            priceChangePercent: priceChangePercent,
            marketStatus: resolvedMarketStatus,
            chartData: chartData,
            keyStatistics: keyStatisticsGroups.flatMap { $0.statistics.map { $0.toModel() } },
            keyStatisticsGroups: keyStatisticsGroups.map { $0.toModel() },
            performancePeriods: performancePeriods.map { $0.toModel() },
            snapshots: snapshots.compactMap { $0.toModel() },
            cryptoProfile: cryptoProfile.toModel(),
            relatedCryptos: relatedCryptos.map { $0.toModel() },
            benchmarkSummary: benchmarkSummary?.toModel()
        )
    }
}
