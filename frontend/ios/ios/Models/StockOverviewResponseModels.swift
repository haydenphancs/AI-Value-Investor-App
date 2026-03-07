//
//  StockOverviewResponseModels.swift
//  ios
//
//  Codable DTOs for the GET /api/v1/stocks/{ticker}/overview endpoint.
//  Maps 1:1 to the backend's snake_case JSON and converts to existing
//  display models (TickerDetailData, etc.) via toDisplayModel().
//
//  Reuses shared DTOs:
//  - MarketStatusDTO, KeyStatisticItemDTO from IndexDetailResponseModels
//  - PerformancePeriodDTO, BenchmarkSummaryDTO from CryptoAPIModels
//  - RelatedTickerDTO from ETFDetailResponseModels
//

import Foundation

// MARK: - Top-Level Response

struct StockOverviewResponseDTO: Decodable {
    let symbol: String
    let companyName: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: MarketStatusDTO
    let chartData: [Double]
    let keyStatistics: [KeyStatisticItemDTO]
    let keyStatisticsGroups: [StockKeyStatisticsGroupDTO]
    let performancePeriods: [PerformancePeriodDTO]
    let snapshots: [SnapshotItemDTO]
    let sectorIndustry: SectorIndustryDTO
    let companyProfile: CompanyProfileDTO
    let relatedTickers: [RelatedTickerDTO]
    let benchmarkSummary: BenchmarkSummaryDTO?

    enum CodingKeys: String, CodingKey {
        case symbol
        case companyName = "company_name"
        case currentPrice = "current_price"
        case priceChange = "price_change"
        case priceChangePercent = "price_change_percent"
        case marketStatus = "market_status"
        case chartData = "chart_data"
        case keyStatistics = "key_statistics"
        case keyStatisticsGroups = "key_statistics_groups"
        case performancePeriods = "performance_periods"
        case snapshots
        case sectorIndustry = "sector_industry"
        case companyProfile = "company_profile"
        case relatedTickers = "related_tickers"
        case benchmarkSummary = "benchmark_summary"
    }
}

// MARK: - Key Statistics Group DTO

struct StockKeyStatisticsGroupDTO: Decodable {
    let statistics: [KeyStatisticItemDTO]
}

// MARK: - Snapshot DTOs

struct SnapshotMetricDTO: Decodable {
    let name: String
    let value: String
}

struct SnapshotItemDTO: Decodable {
    let category: String
    let rating: Int
    let metrics: [SnapshotMetricDTO]
    let fullReportAvailable: Bool

    enum CodingKeys: String, CodingKey {
        case category, rating, metrics
        case fullReportAvailable = "full_report_available"
    }
}

// MARK: - Sector & Industry DTO

struct SectorIndustryDTO: Decodable {
    let sector: String
    let industry: String
    let sectorPerformance: Double
    let industryRank: String

    enum CodingKeys: String, CodingKey {
        case sector, industry
        case sectorPerformance = "sector_performance"
        case industryRank = "industry_rank"
    }
}

// MARK: - Company Profile DTO

struct CompanyProfileDTO: Decodable {
    let description: String
    let ceo: String
    let founded: String
    let employees: Int
    let headquarters: String
    let website: String
}

// MARK: - DTO → Display Model Conversion

extension StockOverviewResponseDTO {

    func toDisplayModel() -> TickerDetailData {
        // Market status
        let mktStatus: MarketStatus = {
            switch marketStatus.status {
            case "open":
                return .open
            case "pre_market":
                return .preMarket
            case "after_hours":
                return .afterHours
            default:
                let date: Date = {
                    if let dateStr = marketStatus.date {
                        let fmt = ISO8601DateFormatter()
                        fmt.formatOptions = [.withInternetDateTime]
                        return fmt.date(from: dateStr) ?? Date()
                    }
                    return Date()
                }()
                return .closed(
                    date: date,
                    time: marketStatus.time ?? "4:00 PM",
                    timezone: marketStatus.timezone ?? "EST"
                )
            }
        }()

        // Key statistics
        let keyStats = keyStatistics.map {
            KeyStatistic(label: $0.label, value: $0.value, isHighlighted: $0.isHighlighted)
        }
        let keyStatsGroups = keyStatisticsGroups.map { group in
            KeyStatisticsGroup(statistics: group.statistics.map {
                KeyStatistic(label: $0.label, value: $0.value, isHighlighted: $0.isHighlighted)
            })
        }

        // Performance periods
        let perfPeriods = performancePeriods.map {
            PerformancePeriod(
                label: $0.label,
                changePercent: $0.changePercent,
                vsMarketPercent: $0.vsMarketPercent,
                benchmarkLabel: $0.benchmarkLabel ?? "S&P"
            )
        }

        // Snapshots
        let snapshotItems = snapshots.map { dto in
            let category = SnapshotCategory(rawValue: dto.category) ?? .profitability
            let rating = SnapshotRatingLevel(rawValue: dto.rating) ?? .average
            let metrics = dto.metrics.map { SnapshotMetric(name: $0.name, value: $0.value) }
            return SnapshotItem(
                category: category,
                rating: rating,
                metrics: metrics,
                fullReportAvailable: dto.fullReportAvailable
            )
        }

        // Sector & Industry
        let sectorInfo = SectorIndustryInfo(
            sector: sectorIndustry.sector,
            industry: sectorIndustry.industry,
            sectorPerformance: sectorIndustry.sectorPerformance,
            industryRank: sectorIndustry.industryRank
        )

        // Company Profile
        let profile = CompanyProfile(
            description: companyProfile.description,
            ceo: companyProfile.ceo,
            founded: companyProfile.founded,
            employees: companyProfile.employees,
            headquarters: companyProfile.headquarters,
            website: companyProfile.website
        )

        // Related Tickers
        let related = relatedTickers.map {
            RelatedTicker(
                symbol: $0.symbol,
                name: $0.name,
                price: $0.price,
                changePercent: $0.changePercent
            )
        }

        // Benchmark Summary
        let benchmark: PerformanceBenchmarkSummary? = benchmarkSummary.map {
            PerformanceBenchmarkSummary(
                avgAnnualReturn: $0.avgAnnualReturn,
                spBenchmark: $0.spBenchmark
            )
        }

        return TickerDetailData(
            symbol: symbol,
            companyName: companyName,
            currentPrice: currentPrice,
            priceChange: priceChange,
            priceChangePercent: priceChangePercent,
            marketStatus: mktStatus,
            chartData: chartData,
            keyStatistics: keyStats,
            keyStatisticsGroups: keyStatsGroups,
            performancePeriods: perfPeriods,
            snapshots: snapshotItems,
            sectorIndustry: sectorInfo,
            companyProfile: profile,
            relatedTickers: related,
            benchmarkSummary: benchmark
        )
    }
}
