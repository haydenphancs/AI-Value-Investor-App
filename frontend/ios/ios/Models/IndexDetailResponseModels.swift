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

    enum CodingKeys: String, CodingKey {
        case label, value
        case isHighlighted = "is_highlighted"
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

    enum CodingKeys: String, CodingKey {
        case avgAnnualReturn = "avg_annual_return"
        case spBenchmark = "sp_benchmark"
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
}

// MARK: - ──────────────────────────────────────────────
// MARK:   DTO → Display Model Mapping
// MARK: - ──────────────────────────────────────────────

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
                KeyStatistic(label: item.label, value: item.value, isHighlighted: item.isHighlighted)
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
                spBenchmark: bs.spBenchmark
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
                headline: dto.headline,
                source: NewsSource(name: dto.sourceName, iconName: dto.sourceIcon),
                sentiment: sentiment,
                publishedAt: publishedDate,
                thumbnailName: nil,
                relatedTickers: dto.relatedTickers,
                summaryBullets: dto.summaryBullets,
                articleURL: dto.articleUrl.flatMap { URL(string: $0) }
            )
        }
    }
}
