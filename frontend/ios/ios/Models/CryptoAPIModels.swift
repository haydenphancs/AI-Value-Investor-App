//
//  CryptoAPIModels.swift
//  ios
//
//  Codable response DTOs for the /api/v1/crypto/{symbol} endpoint.
//  These decode the backend's snake_case JSON and map to UI models.
//

import Foundation

// MARK: - Top-Level Response

struct CryptoDetailResponse: Decodable {
    let symbol: String
    let name: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: String
    let chartData: [StockOverviewPricePointDTO]
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
    let colorState: String?

    enum CodingKeys: String, CodingKey {
        case label, value
        case isHighlighted = "is_highlighted"
        case colorState = "color_state"
    }

    func toModel() -> KeyStatistic {
        KeyStatistic(label: label, value: value, isHighlighted: isHighlighted, colorState: colorState)
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
    let spReturnPercent: Double?

    enum CodingKeys: String, CodingKey {
        case label
        case changePercent = "change_percent"
        case vsMarketPercent = "vs_market_percent"
        case benchmarkLabel = "benchmark_label"
        case spReturnPercent = "sp_return_percent"
    }

    func toModel() -> PerformancePeriod {
        PerformancePeriod(
            label: label,
            changePercent: changePercent,
            vsMarketPercent: vsMarketPercent,
            benchmarkLabel: benchmarkLabel ?? "BTC",
            spReturnPercent: spReturnPercent
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

/// The ONE benchmark decoder. Stock, ETF, commodity AND crypto all decode this same
/// struct (only the index screen has its own, and it never sends a summary), so a field
/// added here reaches all four screens at once — and so does a mistake.
///
/// `benchmarkSinceDate` was REMOVED: it was either identical to `sinceDate` (stock) or
/// nil (everything else) at every call site, and rendering it is what put two identical
/// "Since Aug 2021" labels side by side on the card. Both sides of a row now share one
/// window by construction — see `benchmark_math` on the backend.
struct BenchmarkSummaryDTO: Codable {
    let avgAnnualReturn: Double
    let spBenchmark: Double
    let benchmarkName: String?
    let sinceDate: String?
    let badgeThreshold: Double?
    /// "5-year" | "All-time" — names the window BOTH columns cover. Optional so an older
    /// backend simply yields no row label rather than failing to decode.
    let windowLabel: String?
    /// `false` when the backend could not measure the benchmark. Absent on an older
    /// backend, where `true` reproduces the previous behaviour exactly.
    let benchmarkAvailable: Bool?
    let alltimeAnnualReturn: Double?
    let alltimeBenchmark: Double?
    let alltimeSinceDate: String?

    enum CodingKeys: String, CodingKey {
        case avgAnnualReturn = "avg_annual_return"
        case spBenchmark = "sp_benchmark"
        case benchmarkName = "benchmark_name"
        case sinceDate = "since_date"
        case badgeThreshold = "badge_threshold"
        case windowLabel = "window_label"
        case benchmarkAvailable = "benchmark_available"
        case alltimeAnnualReturn = "alltime_annual_return"
        case alltimeBenchmark = "alltime_benchmark"
        case alltimeSinceDate = "alltime_since_date"
    }

    func toModel() -> PerformanceBenchmarkSummary {
        PerformanceBenchmarkSummary(
            avgAnnualReturn: avgAnnualReturn,
            spBenchmark: spBenchmark,
            benchmarkName: benchmarkName ?? "Bitcoin (BTC)",
            sinceDate: sinceDate,
            badgeThreshold: badgeThreshold ?? 5.0,
            windowLabel: windowLabel,
            benchmarkAvailable: benchmarkAvailable ?? true,
            alltimeAnnualReturn: alltimeAnnualReturn,
            alltimeBenchmark: alltimeBenchmark,
            alltimeSinceDate: alltimeSinceDate
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

    // Tolerant decode: a null in `sentiment`/`published_at`/the arrays would
    // otherwise fail decoding of the ENTIRE crypto detail response. Absorb it.
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

// MARK: - CryptoDetailResponse → CryptoDetailData Mapping

extension CryptoDetailResponse {
    func toModel() -> CryptoDetailData {
        // One mapping, shared with the fast-core path — see CryptoMarketStatus(backend:).
        let resolvedMarketStatus = CryptoMarketStatus(backend: self.marketStatus)

        return CryptoDetailData(
            symbol: symbol,
            name: name,
            currentPrice: currentPrice,
            priceChange: priceChange,
            priceChangePercent: priceChangePercent,
            marketStatus: resolvedMarketStatus,
            chartPricePoints: chartData.map {
                StockPricePoint(date: $0.date ?? "", close: $0.close, open: $0.open, high: $0.high, low: $0.low, volume: $0.volume)
            },
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

extension CryptoMarketStatus {
    /// The backend's `market_status` string -> the view model's enum.
    ///
    /// Extracted from `CryptoDetailResponse.toDisplayModel`, where it was written inline,
    /// so the full and fast-core paths cannot disagree about what one string means — two
    /// copies of a mapping is how the same value ends up rendering two different states
    /// on one screen. Same reasoning as `CommodityMarketStatus(backend:)`.
    init(backend: String) {
        if backend.hasPrefix("Maintenance") {
            let resumeTime = backend.replacingOccurrences(
                of: "Maintenance - Resumes ", with: "")
            self = .maintenance(resumeTime: resumeTime)
        } else {
            self = .trading
        }
    }
}

// MARK: - ──────────────────────────────────────────────
// MARK:   FAST-CORE FIRST PAINT
// MARK: - ──────────────────────────────────────────────
//
// `GET /api/v1/crypto/{{symbol}}/core` — the header line, and the chart when it was
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

/// Fast-core payload for the crypto detail screen. Field names mirror the full detail
/// DTO and reuse its nested types, so `full ?? core` is a drop-in swap in the view.
struct CryptoCoreResponseDTO: Decodable {
    let symbol: String
    let name: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: String
    /// Empty when the server could only have produced bars by pulling the multi-thousand
    /// row daily history. The full response fills them in a moment later.
    let chartData: [StockOverviewPricePointDTO]

    enum CodingKeys: String, CodingKey {
        case symbol
        case name = "name"
        case currentPrice = "current_price"
        case priceChange = "price_change"
        case priceChangePercent = "price_change_percent"
        case marketStatus = "market_status"
        case chartData = "chart_data"
    }

    func toCoreData() -> CryptoCoreData {
        CryptoCoreData(
            symbol: symbol,
            name: name,
            currentPrice: currentPrice,
            priceChange: priceChange,
            priceChangePercent: priceChangePercent,
            marketStatus: CryptoMarketStatus(backend: marketStatus),
            chartPricePoints: chartData.map {
                StockPricePoint(date: $0.date ?? "", close: $0.close,
                                open: $0.open, high: $0.high, low: $0.low,
                                volume: $0.volume)
            }
        )
    }
}

/// The header-only side model the screen paints until the full response lands.
///
/// Every formatted string delegates to `CryptoHeaderFormat`, the SAME helper the full display model
/// uses — so nothing visibly reformats when core is superseded.
struct CryptoCoreData {
    let symbol: String
    let name: String
    var currentPrice: Double
    var priceChange: Double
    var priceChangePercent: Double
    var marketStatus: CryptoMarketStatus
    /// `var`: the range pill is interactive before the full response lands, and the live
    /// socket merges ticks into the core header the same way it merges into the full one.
    var chartPricePoints: [StockPricePoint]

    var chartData: [Double] { chartPricePoints.map { $0.close } }
    var isPositive: Bool { priceChange >= 0 }
    /// Prior close, for the chart's dashed baseline — derived exactly as the full display
    /// model derives it, not shipped by the server, so there is one source for it.
    var previousClose: Double { currentPrice - priceChange }

    var formattedPrice: String { CryptoHeaderFormat.price(currentPrice) }
    var formattedChange: String { CryptoHeaderFormat.change(priceChange) }
    var formattedChangePercent: String {
        CryptoHeaderFormat.changePercent(priceChangePercent)
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
protocol CryptoHeaderRenderable {
    var symbol: String { get }
    var name: String { get }
    var formattedPrice: String { get }
    var formattedChange: String { get }
    var formattedChangePercent: String { get }
    var isPositive: Bool { get }
    var marketStatus: CryptoMarketStatus { get }
    var chartPricePoints: [StockPricePoint] { get }
    var previousClose: Double { get }
}

extension CryptoDetailData: CryptoHeaderRenderable {}
extension CryptoCoreData: CryptoHeaderRenderable {}
