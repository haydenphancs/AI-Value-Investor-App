//
//  IndexDetailResponseModels.swift
//  ios
//
//  Codable DTOs for the GET /api/v1/indices/{symbol} endpoint.
//  These map 1:1 to the backend's snake_case JSON and are then
//  converted to the existing display models (IndexDetailData, etc.)
//  inside IndexDetailViewModel.
//

import Foundation

// MARK: - Shared ISO 8601 Parser

private enum IndexResponseFormatters {
    static func parseISO8601(_ string: String) -> Date? {
        let fmt = ISO8601DateFormatter()
        fmt.formatOptions = [.withInternetDateTime]
        if let d = fmt.date(from: string) { return d }
        fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = fmt.date(from: string) { return d }
        // Try date-only format (YYYY-MM-DD)
        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd"
        return df.date(from: string)
    }
}

// MARK: - Light Refresh Slice

/// `GET /api/v1/indices/{symbol}/quote` — the payload the 30-second loop and the range
/// picker actually need.
///
/// Every field name and type matches `IndexDetailResponse`, so this reuses the same nested
/// DTOs. What it drops is everything a 30-second refresh cannot change — and on this screen
/// that includes `snapshots_data`, a deep required graph of AI-written valuation, sector
/// and macro stories that made up most of the payload.
struct IndexQuoteResponse: Decodable {
    let symbol: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: MarketStatusDTO
    let chartData: [StockOverviewPricePointDTO]
    let keyStatisticsGroups: [IndexKeyStatisticsGroupDTO]

    enum CodingKeys: String, CodingKey {
        case symbol
        case currentPrice = "current_price"
        case priceChange = "price_change"
        case priceChangePercent = "price_change_percent"
        case marketStatus = "market_status"
        case chartData = "chart_data"
        case keyStatisticsGroups = "key_statistics_groups"
    }
}

// MARK: - Top-Level Response

struct IndexDetailResponse: Decodable {
    let symbol: String
    let indexName: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: MarketStatusDTO
    let chartData: [StockOverviewPricePointDTO]
    let keyStatisticsGroups: [IndexKeyStatisticsGroupDTO]
    let performancePeriods: [IndexPerformancePeriodDTO]
    let snapshotsData: IndexSnapshotsDataDTO
    let indexProfile: IndexProfileDTO
    let benchmarkSummary: IndexBenchmarkSummaryDTO?
    let newsArticles: [IndexNewsArticleDTO]

    enum CodingKeys: String, CodingKey {
        case symbol
        case indexName = "index_name"
        case currentPrice = "current_price"
        case priceChange = "price_change"
        case priceChangePercent = "price_change_percent"
        case marketStatus = "market_status"
        case chartData = "chart_data"
        case keyStatisticsGroups = "key_statistics_groups"
        case performancePeriods = "performance_periods"
        case snapshotsData = "snapshots_data"
        case indexProfile = "index_profile"
        case benchmarkSummary = "benchmark_summary"
        case newsArticles = "news_articles"
    }
}

// MARK: - Market Status

struct MarketStatusDTO: Decodable {
    let status: String
    let date: String?
    let time: String?
    let timezone: String?
}

// MARK: - Key Statistics

struct KeyStatisticItemDTO: Decodable {
    let label: String
    let value: String
    let isHighlighted: Bool
    let colorState: String?

    enum CodingKeys: String, CodingKey {
        case label, value
        case isHighlighted = "is_highlighted"
        case colorState = "color_state"
    }
}

struct IndexKeyStatisticsGroupDTO: Decodable {
    let statistics: [KeyStatisticItemDTO]
}

// MARK: - Performance

struct IndexPerformancePeriodDTO: Decodable {
    let label: String
    let changePercent: Double
    let vsMarketPercent: Double?

    enum CodingKeys: String, CodingKey {
        case label
        case changePercent = "change_percent"
        case vsMarketPercent = "vs_market_percent"
    }
}

struct IndexBenchmarkSummaryDTO: Decodable {
    let avgAnnualReturn: Double
    let spBenchmark: Double
    let alltimeAnnualReturn: Double?
    let alltimeBenchmark: Double?
    let alltimeSinceDate: String?

    enum CodingKeys: String, CodingKey {
        case avgAnnualReturn = "avg_annual_return"
        case spBenchmark = "sp_benchmark"
        case alltimeAnnualReturn = "alltime_annual_return"
        case alltimeBenchmark = "alltime_benchmark"
        case alltimeSinceDate = "alltime_since_date"
    }
}

// MARK: - Snapshots

struct ValuationSnapshotDTO: Decodable {
    let peRatio: Double
    let forwardPe: Double
    let earningsYield: Double
    let historicalAvgPe: Double
    let historicalPeriod: String
    let storyTemplate: String

    enum CodingKeys: String, CodingKey {
        case peRatio = "pe_ratio"
        case forwardPe = "forward_pe"
        case earningsYield = "earnings_yield"
        case historicalAvgPe = "historical_avg_pe"
        case historicalPeriod = "historical_period"
        case storyTemplate = "story_template"
    }
}

struct SectorPerformanceEntryDTO: Decodable {
    let sector: String
    let changePercent: Double

    enum CodingKeys: String, CodingKey {
        case sector
        case changePercent = "change_percent"
    }
}

struct SectorPerformanceSnapshotDTO: Decodable {
    let sectors: [SectorPerformanceEntryDTO]
    let storyTemplate: String

    enum CodingKeys: String, CodingKey {
        case sectors
        case storyTemplate = "story_template"
    }
}

struct MacroForecastItemDTO: Decodable {
    let title: String
    let description: String
    let signal: String

    enum CodingKeys: String, CodingKey {
        case title, description, signal
    }
}

struct MacroForecastSnapshotDTO: Decodable {
    let indicators: [MacroForecastItemDTO]
    let storyTemplate: String

    enum CodingKeys: String, CodingKey {
        case indicators
        case storyTemplate = "story_template"
    }
}

struct IndexSnapshotsDataDTO: Decodable {
    let valuation: ValuationSnapshotDTO
    let sectorPerformance: SectorPerformanceSnapshotDTO
    let macroForecast: MacroForecastSnapshotDTO
    let generatedDate: String
    let generatedBy: String

    enum CodingKeys: String, CodingKey {
        case valuation
        case sectorPerformance = "sector_performance"
        case macroForecast = "macro_forecast"
        case generatedDate = "generated_date"
        case generatedBy = "generated_by"
    }
}

// MARK: - Profile

struct IndexProfileDTO: Decodable {
    let description: String
    let exchange: String
    let numberOfConstituents: Int
    let weightingMethodology: String
    let inceptionDate: String
    let indexProvider: String
    let website: String

    enum CodingKeys: String, CodingKey {
        case description, exchange, website
        case numberOfConstituents = "number_of_constituents"
        case weightingMethodology = "weighting_methodology"
        case inceptionDate = "inception_date"
        case indexProvider = "index_provider"
    }
}

// MARK: - News

struct IndexNewsArticleDTO: Decodable {
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
        case headline
        case sourceName = "source_name"
        case sourceIcon = "source_icon"
        case sentiment
        case publishedAt = "published_at"
        case thumbnailUrl = "thumbnail_url"
        case relatedTickers = "related_tickers"
        case summaryBullets = "summary_bullets"
        case articleUrl = "article_url"
    }

    // Tolerant decode: a null in `sentiment`/`published_at`/the arrays would
    // otherwise fail decoding of the ENTIRE index detail response. Absorb it.
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
}

// MARK: - ──────────────────────────────────────────────
// MARK:   DTO → Display Model Mapping
// MARK: - ──────────────────────────────────────────────

// MARK: - Light Slice → Display Merge

extension IndexQuoteResponse {
    /// Merge the volatile slice into an existing `IndexDetailData` IN PLACE.
    ///
    /// Only the fields this slice carries are written; `snapshotsData`, `indexProfile`,
    /// `performancePeriods` and `benchmarkSummary` are left untouched because a 30-second
    /// refresh cannot change them.
    ///
    /// `livePrice` WINS over the REST snapshot when the socket has ticked — a tick is now,
    /// a snapshot is up to 45 seconds old. See `ETFQuoteResponseDTO.merged`.
    func merged(
        into data: IndexDetailData,
        livePrice: Double?,
        liveChange: Double?,
        liveChangePercent: Double?,
        includeChart: Bool
    ) -> IndexDetailData {
        var out = data
        out.currentPrice = livePrice ?? currentPrice
        out.priceChange = liveChange ?? priceChange
        out.priceChangePercent = liveChangePercent ?? priceChangePercent
        out.marketStatus = marketStatus.resolvedMarketStatus
        out.keyStatisticsGroups = keyStatisticsGroups.map { group in
            KeyStatisticsGroup(statistics: group.statistics.map {
                KeyStatistic(label: $0.label, value: $0.value,
                             isHighlighted: $0.isHighlighted, colorState: $0.colorState)
            })
        }
        if includeChart, !chartData.isEmpty {
            out.chartPricePoints = chartData.map {
                StockPricePoint(date: $0.date ?? "", close: $0.close,
                                open: $0.open, high: $0.high, low: $0.low, volume: $0.volume)
            }
        }
        return out
    }
}

extension IndexDetailResponse {

    /// Convert the API response DTO to the display model used by the view.
    func toDisplayModel() -> IndexDetailData {
        // Map market status
        let mktStatus: MarketStatus
        switch marketStatus.status {
        case "open":
            mktStatus = .open
        case "pre_market":
            mktStatus = .preMarket
        case "after_hours":
            mktStatus = .afterHours
        default:
            let date = IndexResponseFormatters.parseISO8601(marketStatus.date ?? "") ?? Date()
            mktStatus = .closed(
                date: date,
                time: marketStatus.time ?? "4:00 PM",
                timezone: marketStatus.timezone ?? "EST"
            )
        }

        // Map key statistics groups
        let keyStatsGroups = keyStatisticsGroups.map { group in
            KeyStatisticsGroup(statistics: group.statistics.map { item in
                KeyStatistic(label: item.label, value: item.value, isHighlighted: item.isHighlighted, colorState: item.colorState)
            })
        }

        // Map performance periods
        let perfPeriods = performancePeriods.map { p in
            PerformancePeriod(
                label: p.label,
                changePercent: p.changePercent,
                vsMarketPercent: p.vsMarketPercent
            )
        }

        // Map snapshots
        let valuation = IndexValuationSnapshot(
            peRatio: snapshotsData.valuation.peRatio,
            forwardPE: snapshotsData.valuation.forwardPe,
            earningsYield: snapshotsData.valuation.earningsYield,
            historicalAvgPE: snapshotsData.valuation.historicalAvgPe,
            historicalPeriod: snapshotsData.valuation.historicalPeriod,
            storyTemplate: snapshotsData.valuation.storyTemplate
        )

        let sectorEntries = snapshotsData.sectorPerformance.sectors.map { s in
            SectorPerformanceEntry(sector: s.sector, changePercent: s.changePercent)
        }
        let sectorSnapshot = IndexSectorPerformanceSnapshot(
            sectors: sectorEntries,
            storyTemplate: snapshotsData.sectorPerformance.storyTemplate
        )

        let macroItems = snapshotsData.macroForecast.indicators.map { item in
            let signal: MacroSignal
            switch item.signal.lowercased() {
            case "positive": signal = .positive
            case "cautious": signal = .cautious
            default: signal = .neutral
            }
            return MacroForecastItem(title: item.title, description: item.description, signal: signal)
        }
        let macroSnapshot = IndexMacroForecastSnapshot(
            indicators: macroItems,
            storyTemplate: snapshotsData.macroForecast.storyTemplate
        )

        let genDate = IndexResponseFormatters.parseISO8601(snapshotsData.generatedDate) ?? Date()
        let snapshotsCombined = IndexSnapshotsData(
            valuation: valuation,
            sectorPerformance: sectorSnapshot,
            macroForecast: macroSnapshot,
            generatedDate: genDate,
            generatedBy: snapshotsData.generatedBy
        )

        // Map profile
        let profile = IndexProfile(
            description: indexProfile.description,
            exchange: indexProfile.exchange,
            numberOfConstituents: indexProfile.numberOfConstituents,
            weightingMethodology: indexProfile.weightingMethodology,
            inceptionDate: indexProfile.inceptionDate,
            indexProvider: indexProfile.indexProvider,
            website: indexProfile.website
        )

        // Map benchmark
        let benchmark: PerformanceBenchmarkSummary?
        if let bs = benchmarkSummary {
            benchmark = PerformanceBenchmarkSummary(
                avgAnnualReturn: bs.avgAnnualReturn,
                spBenchmark: bs.spBenchmark,
                alltimeAnnualReturn: bs.alltimeAnnualReturn,
                alltimeBenchmark: bs.alltimeBenchmark,
                alltimeSinceDate: bs.alltimeSinceDate
            )
        } else {
            benchmark = nil
        }

        return IndexDetailData(
            symbol: symbol,
            indexName: indexName,
            currentPrice: currentPrice,
            priceChange: priceChange,
            priceChangePercent: priceChangePercent,
            marketStatus: mktStatus,
            chartPricePoints: chartData.map {
                StockPricePoint(date: $0.date ?? "", close: $0.close, open: $0.open, high: $0.high, low: $0.low, volume: $0.volume)
            },
            keyStatisticsGroups: keyStatsGroups,
            performancePeriods: perfPeriods,
            snapshotsData: snapshotsCombined,
            indexProfile: profile,
            benchmarkSummary: benchmark
        )
    }

    /// Convert news DTOs to display models.
    func toNewsArticles() -> [TickerNewsArticle] {
        newsArticles.map { dto in
            let sentiment: NewsSentiment
            switch dto.sentiment.lowercased() {
            case "positive": sentiment = .positive
            case "negative": sentiment = .negative
            default: sentiment = .neutral
            }

            let publishedDate = IndexResponseFormatters.parseISO8601(dto.publishedAt) ?? Date()

            return TickerNewsArticle(
                apiId: dto.articleUrl ?? UUID().uuidString,
                headline: dto.headline,
                source: NewsSource(name: dto.sourceName, iconName: dto.sourceIcon),
                sentiment: sentiment,
                publishedAt: publishedDate,
                thumbnailName: nil,
                imageURL: dto.thumbnailUrl.flatMap { URL(string: $0) },
                relatedTickers: dto.relatedTickers,
                summaryBullets: dto.summaryBullets,
                articleURL: dto.articleUrl.flatMap { URL(string: $0) },
                aiProcessed: !dto.summaryBullets.isEmpty
            )
        }
    }
}

// MARK: - ──────────────────────────────────────────────
// MARK:   FAST-CORE FIRST PAINT
// MARK: - ──────────────────────────────────────────────
//
// `GET /api/v1/indices/{{symbol}}/core` — the header line, and the chart when it was
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

/// Fast-core payload for the indices detail screen. Field names mirror the full detail
/// DTO and reuse its nested types, so `full ?? core` is a drop-in swap in the view.
struct IndexCoreResponseDTO: Decodable {
    let symbol: String
    let indexName: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: MarketStatusDTO
    /// Empty when the server could only have produced bars by pulling the multi-thousand
    /// row daily history. The full response fills them in a moment later.
    let chartData: [StockOverviewPricePointDTO]

    enum CodingKeys: String, CodingKey {
        case symbol
        case indexName = "index_name"
        case currentPrice = "current_price"
        case priceChange = "price_change"
        case priceChangePercent = "price_change_percent"
        case marketStatus = "market_status"
        case chartData = "chart_data"
    }

    func toCoreData() -> IndexCoreData {
        IndexCoreData(
            symbol: symbol,
            indexName: indexName,
            currentPrice: currentPrice,
            priceChange: priceChange,
            priceChangePercent: priceChangePercent,
            marketStatus: marketStatus.resolvedMarketStatus,
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
/// Every formatted string delegates to `IndexHeaderFormat`, the SAME helper the full display model
/// uses — so nothing visibly reformats when core is superseded.
struct IndexCoreData {
    let symbol: String
    let indexName: String
    var currentPrice: Double
    var priceChange: Double
    var priceChangePercent: Double
    var marketStatus: MarketStatus
    /// `var`: the range pill is interactive before the full response lands, and the live
    /// socket merges ticks into the core header the same way it merges into the full one.
    var chartPricePoints: [StockPricePoint]

    var chartData: [Double] { chartPricePoints.map { $0.close } }
    var isPositive: Bool { priceChange >= 0 }
    /// Prior close, for the chart's dashed baseline — derived exactly as the full display
    /// model derives it, not shipped by the server, so there is one source for it.
    var previousClose: Double { currentPrice - priceChange }

    var formattedPrice: String { IndexHeaderFormat.price(currentPrice) }
    var formattedChange: String { IndexHeaderFormat.change(priceChange) }
    var formattedChangePercent: String {
        IndexHeaderFormat.changePercent(priceChangePercent)
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
protocol IndexHeaderRenderable {
    var symbol: String { get }
    var indexName: String { get }
    var formattedPrice: String { get }
    var formattedChange: String { get }
    var formattedChangePercent: String { get }
    var isPositive: Bool { get }
    var marketStatus: MarketStatus { get }
    var chartPricePoints: [StockPricePoint] { get }
    var previousClose: Double { get }
}

extension IndexDetailData: IndexHeaderRenderable {}
extension IndexCoreData: IndexHeaderRenderable {}
