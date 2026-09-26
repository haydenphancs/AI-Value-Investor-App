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
    /// `false` when neither the quote nor the profile carried a day change (a
    /// halted/OTC listing's `/stable/profile` answers `change: null`): `priceChange`
    /// / `priceChangePercent` are then the 0.0 wire placeholder, NOT a flat day.
    /// Optional so a payload from an older backend still decodes (`?? true`).
    let changeKnown: Bool?
    let marketStatus: MarketStatusDTO
    let chartData: [StockOverviewPricePointDTO]
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
        case changeKnown = "change_known"
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

// MARK: - Price Point DTO (OHLCV)

struct StockOverviewPricePointDTO: Decodable {
    let date: String?
    let open: Double?
    let high: Double?
    let low: Double?
    let close: Double
    let volume: Double?
}

// MARK: - Fast-core response (GET /stocks/{ticker}/overview/core)

/// The fast subset the stock detail screen paints first (price + chart + name),
/// before the full `/overview` supersedes it. Field names mirror
/// `StockOverviewResponseDTO`, reusing `StockOverviewPricePointDTO` + `MarketStatusDTO`.
struct StockOverviewCoreResponseDTO: Decodable {
    let symbol: String
    let companyName: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    /// See `StockOverviewResponseDTO.changeKnown`.
    let changeKnown: Bool?
    let marketStatus: MarketStatusDTO
    let chartData: [StockOverviewPricePointDTO]

    enum CodingKeys: String, CodingKey {
        case symbol
        case companyName = "company_name"
        case currentPrice = "current_price"
        case priceChange = "price_change"
        case priceChangePercent = "price_change_percent"
        case changeKnown = "change_known"
        case marketStatus = "market_status"
        case chartData = "chart_data"
    }

    /// Map to the lightweight price+chart side model the ViewModel paints until
    /// the full `tickerData` lands.
    func toCoreData() -> TickerCoreData {
        TickerCoreData(
            symbol: symbol,
            companyName: companyName,
            currentPrice: currentPrice,
            priceChange: priceChange,
            priceChangePercent: priceChangePercent,
            changeKnown: changeKnown ?? true,
            marketStatus: marketStatus.resolvedMarketStatus,
            chartPricePoints: chartData.map {
                StockPricePoint(date: $0.date ?? "", close: $0.close,
                                open: $0.open, high: $0.high, low: $0.low, volume: $0.volume)
            }
        )
    }
}

extension MarketStatusDTO {
    /// Shared status→`MarketStatus` mapping (identical to the switch in
    /// `StockOverviewResponseDTO.toDisplayModel`), so the fast-core header renders
    /// exactly like the full one.
    var resolvedMarketStatus: MarketStatus {
        switch status {
        case "open":
            return .open
        case "pre_market":
            return .preMarket
        case "after_hours":
            return .afterHours
        default:
            let resolvedDate: Date = {
                if let dateStr = date {
                    let fmt = ISO8601DateFormatter()
                    fmt.formatOptions = [.withInternetDateTime]
                    return fmt.date(from: dateStr) ?? Date()
                }
                return Date()
            }()
            return .closed(date: resolvedDate, time: time ?? "4:00 PM", timezone: timezone ?? "EST")
        }
    }
}

/// Lightweight price+chart snapshot for the instant first paint. Formatted
/// computeds mirror `TickerDetailData` exactly so core → full is visually seamless.
struct TickerCoreData {
    let symbol: String
    let companyName: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    /// `false` = the backend had no day change for this listing (`change_known`), so
    /// `priceChange` is a 0.0 placeholder. Same rule as `ETFDetailData.changeKnown`:
    /// never a direction, never a sign, "—" for the text. The equity screen was the
    /// fifth asset class and the only one that painted the placeholder as
    /// "▲ +0.00 (+0.00%)" in green with a bullish flash. Pass it to
    /// `TickerPriceHeader(changeKnown:)` — the header hides the arrow and the flash.
    var changeKnown: Bool = true
    let marketStatus: MarketStatus
    /// `var` so the ViewModel can keep the fast-core chart in sync with the selected
    /// range pill while only coreData is shown (the pill is interactive before the
    /// full overview lands). See TickerDetailViewModel.fetchChartData.
    var chartPricePoints: [StockPricePoint]

    var isPositive: Bool { changeKnown && priceChange >= 0 }
    /// Chart tint with an unknown change: from the series, never the placeholder.
    var chartIsPositive: Bool {
        if changeKnown { return isPositive }
        guard let first = chartPricePoints.first?.close, let last = chartPricePoints.last?.close,
              first.isFinite, last.isFinite else { return true }
        return last >= first
    }
    var formattedPrice: String { String(format: "$%.2f", currentPrice) }
    var formattedChange: String {
        guard changeKnown else { return "—" }
        let sign = priceChange >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", priceChange))"
    }
    var formattedChangePercent: String {
        guard changeKnown else { return "" }
        let sign = priceChangePercent >= 0 ? "+" : ""
        return "(\(sign)\(String(format: "%.2f", priceChangePercent))%)"
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

/// FMP's discounted-cash-flow value, carried on the valuation ("Price") snapshot only.
/// Optional end to end: the other four snapshots never carry it and a backend predating
/// it still decodes. `value` is absent for `negative_cash_flow`.
struct DcfEstimateDTO: Decodable {
    let status: String
    let value: Double?
    let asOf: String?

    enum CodingKeys: String, CodingKey {
        case status, value
        case asOf = "as_of"
    }
}

struct SnapshotItemDTO: Decodable {
    let category: String
    let rating: Int
    let metrics: [SnapshotMetricDTO]
    let fullReportAvailable: Bool
    let dcf: DcfEstimateDTO?
    /// The Caydex Fair Value Estimate (valuation snapshot only, when the backend enables
    /// it — FMP's `dcf` is then absent). Optional: older backends never send it.
    let caydexEstimate: CaydexFairValueDTO?

    enum CodingKeys: String, CodingKey {
        case category, rating, metrics, dcf
        case fullReportAvailable = "full_report_available"
        case caydexEstimate = "caydex_estimate"
    }
}

// MARK: - Sector & Industry DTO

struct SectorIndustryDTO: Decodable {
    let sector: String
    let industry: String
    let sectorPerformance: Double
    let industryRank: String
    /// `pe_known` pattern: `sectorPerformance` is a plain Double on the wire (a shipped
    /// build cannot decode null), so this says whether it is a measurement or the 0.0
    /// placeholder. `nil` (older backend) reads as known, matching prior behaviour.
    let sectorPerformanceKnown: Bool?

    enum CodingKeys: String, CodingKey {
        case sector, industry
        case sectorPerformance = "sector_performance"
        case industryRank = "industry_rank"
        case sectorPerformanceKnown = "sector_performance_known"
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
    let sector: String
    let industry: String
    let sectorPerformance: Double
    let sectorPerformanceKnown: Bool?

    enum CodingKeys: String, CodingKey {
        case description, ceo, founded, employees, headquarters, website
        case sector, industry
        case sectorPerformance = "sector_performance"
        case sectorPerformanceKnown = "sector_performance_known"
    }
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
            KeyStatistic(label: $0.label, value: $0.value, isHighlighted: $0.isHighlighted, colorState: $0.colorState)
        }
        let keyStatsGroups = keyStatisticsGroups.map { group in
            KeyStatisticsGroup(statistics: group.statistics.map {
                KeyStatistic(label: $0.label, value: $0.value, isHighlighted: $0.isHighlighted, colorState: $0.colorState)
            })
        }

        // Performance periods
        let perfPeriods = performancePeriods.map {
            PerformancePeriod(
                label: $0.label,
                changePercent: $0.changePercent,
                vsMarketPercent: $0.vsMarketPercent,
                benchmarkLabel: $0.benchmarkLabel ?? "S&P",
                spReturnPercent: $0.spReturnPercent
            )
        }

        // Snapshots
        let snapshotItems = snapshots.map { dto in
            let category = SnapshotCategory(rawValue: dto.category) ?? .profitability
            let rating = SnapshotRatingLevel(rawValue: dto.rating) ?? .unavailable
            let metrics = dto.metrics.map { SnapshotMetric(name: $0.name, value: $0.value) }
            return SnapshotItem(
                category: category,
                rating: rating,
                metrics: metrics,
                fullReportAvailable: dto.fullReportAvailable,
                dcf: dto.dcf.flatMap { DcfEstimate(dto: $0) },
                caydexEstimate: dto.caydexEstimate.flatMap { CaydexFairValue(dto: $0) }
            )
        }

        // Sector & Industry
        let sectorInfo = SectorIndustryInfo(
            sector: sectorIndustry.sector,
            industry: sectorIndustry.industry,
            sectorPerformance: sectorIndustry.sectorPerformance,
            industryRank: sectorIndustry.industryRank,
            sectorPerformanceKnown: sectorIndustry.sectorPerformanceKnown ?? true
        )

        // Company Profile (includes sector & industry)
        let profile = CompanyProfile(
            description: companyProfile.description,
            ceo: companyProfile.ceo,
            founded: companyProfile.founded,
            employees: companyProfile.employees,
            headquarters: companyProfile.headquarters,
            website: companyProfile.website,
            sector: companyProfile.sector,
            industry: companyProfile.industry,
            sectorPerformance: companyProfile.sectorPerformance,
            sectorPerformanceKnown: companyProfile.sectorPerformanceKnown
                ?? sectorIndustry.sectorPerformanceKnown ?? true
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
                spBenchmark: $0.spBenchmark,
                sinceDate: $0.sinceDate,
                windowLabel: $0.windowLabel,
                benchmarkAvailable: $0.benchmarkAvailable ?? true,
                alltimeAnnualReturn: $0.alltimeAnnualReturn,
                alltimeBenchmark: $0.alltimeBenchmark,
                alltimeSinceDate: $0.alltimeSinceDate
            )
        }

        return TickerDetailData(
            symbol: symbol,
            companyName: companyName,
            currentPrice: currentPrice,
            priceChange: priceChange,
            priceChangePercent: priceChangePercent,
            changeKnown: changeKnown ?? true,
            marketStatus: mktStatus,
            chartPricePoints: chartData.map {
                StockPricePoint(
                    date: $0.date ?? "",
                    close: $0.close,
                    open: $0.open,
                    high: $0.high,
                    low: $0.low,
                    volume: $0.volume
                )
            },
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
