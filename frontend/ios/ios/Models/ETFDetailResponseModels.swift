//
//  ETFDetailResponseModels.swift
//  ios
//
//  Codable DTOs for the GET /api/v1/etfs/{symbol} endpoint.
//  These map 1:1 to the backend's snake_case JSON and are then
//  converted to the existing display models (ETFDetailData, etc.)
//  inside ETFDetailViewModel.
//
//  Reuses shared DTOs from IndexDetailResponseModels.swift:
//  MarketStatusDTO, KeyStatisticItemDTO, KeyStatisticsGroupDTO,
//  PerformancePeriodDTO, BenchmarkSummaryDTO
//

import Foundation

// MARK: - Shared ISO 8601 Parser

private enum ETFResponseFormatters {
    static func parseISO8601(_ string: String) -> Date? {
        let fmt = ISO8601DateFormatter()
        fmt.formatOptions = [.withInternetDateTime]
        if let d = fmt.date(from: string) { return d }
        fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = fmt.date(from: string) { return d }
        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd"
        return df.date(from: string)
    }
}

// MARK: - Top-Level Response

struct ETFDetailResponseDTO: Decodable {
    let symbol: String
    let name: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: MarketStatusDTO
    let chartData: [StockOverviewPricePointDTO]
    let keyStatistics: [KeyStatisticItemDTO]
    let keyStatisticsGroups: [KeyStatisticsGroupDTO]
    let performancePeriods: [PerformancePeriodDTO]
    let identityRating: ETFIdentityRatingDTO
    let strategy: ETFStrategyDTO
    let netYield: ETFNetYieldDTO
    let holdingsRisk: ETFHoldingsRiskDTO
    let etfProfile: ETFProfileDTO
    let relatedEtfs: [RelatedTickerDTO]
    let benchmarkSummary: BenchmarkSummaryDTO?
    let newsArticles: [ETFNewsArticleDTO]

    enum CodingKeys: String, CodingKey {
        case symbol, name
        case currentPrice = "current_price"
        case priceChange = "price_change"
        case priceChangePercent = "price_change_percent"
        case marketStatus = "market_status"
        case chartData = "chart_data"
        case keyStatistics = "key_statistics"
        case keyStatisticsGroups = "key_statistics_groups"
        case performancePeriods = "performance_periods"
        case identityRating = "identity_rating"
        case strategy
        case netYield = "net_yield"
        case holdingsRisk = "holdings_risk"
        case etfProfile = "etf_profile"
        case relatedEtfs = "related_etfs"
        case benchmarkSummary = "benchmark_summary"
        case newsArticles = "news_articles"
    }
}

// MARK: - ETF Identity & Rating

struct ETFIdentityRatingDTO: Decodable {
    let score: Int
    let maxScore: Int
    let esgRating: String
    let volatilityLabel: String

    enum CodingKeys: String, CodingKey {
        case score
        case maxScore = "max_score"
        case esgRating = "esg_rating"
        case volatilityLabel = "volatility_label"
    }
}

// MARK: - ETF Strategy

struct ETFStrategyDTO: Decodable {
    let hook: String
    let tags: [String]
}

// MARK: - ETF Dividend Payment

struct ETFDividendPaymentDTO: Decodable {
    let dividendPerShare: String
    let exDividendDate: String
    let payDate: String

    enum CodingKeys: String, CodingKey {
        case dividendPerShare = "dividend_per_share"
        case exDividendDate = "ex_dividend_date"
        case payDate = "pay_date"
    }
}

// MARK: - ETF Net Yield

struct ETFNetYieldDTO: Decodable {
    let expenseRatio: Double
    let feeContext: String
    let dividendYield: Double
    let payFrequency: String
    let yieldContext: String
    let verdict: String
    let lastDividendPayment: ETFDividendPaymentDTO
    let dividendHistory: [ETFDividendPaymentDTO]

    enum CodingKeys: String, CodingKey {
        case expenseRatio = "expense_ratio"
        case feeContext = "fee_context"
        case dividendYield = "dividend_yield"
        case payFrequency = "pay_frequency"
        case yieldContext = "yield_context"
        case verdict
        case lastDividendPayment = "last_dividend_payment"
        case dividendHistory = "dividend_history"
    }
}

// MARK: - ETF Asset Allocation

struct ETFAssetAllocationDTO: Decodable {
    let equities: Double
    let bonds: Double
    let crypto: Double
    let cash: Double
    let totalAssets: String

    enum CodingKeys: String, CodingKey {
        case equities, bonds, crypto, cash
        case totalAssets = "total_assets"
    }
}

// MARK: - ETF Sector Weight

struct ETFSectorWeightDTO: Decodable {
    let name: String
    let weight: Double
}

// MARK: - ETF Top Holding

struct ETFTopHoldingDTO: Decodable {
    let symbol: String
    let name: String
    let weight: Double
}

// MARK: - ETF Concentration

struct ETFConcentrationDTO: Decodable {
    let topN: Int
    let weight: Double
    let insight: String

    enum CodingKeys: String, CodingKey {
        case topN = "top_n"
        case weight, insight
    }
}

// MARK: - ETF Holdings & Risk

struct ETFHoldingsRiskDTO: Decodable {
    let assetAllocation: ETFAssetAllocationDTO
    let topSectors: [ETFSectorWeightDTO]
    let topHoldings: [ETFTopHoldingDTO]
    let concentration: ETFConcentrationDTO

    enum CodingKeys: String, CodingKey {
        case assetAllocation = "asset_allocation"
        case topSectors = "top_sectors"
        case topHoldings = "top_holdings"
        case concentration
    }
}

// MARK: - ETF Profile

struct ETFProfileDTO: Decodable {
    let description: String
    let symbol: String
    let etfCompany: String
    let assetClass: String
    let expenseRatio: String
    let inceptionDate: String
    let domicile: String
    let indexTracked: String
    let website: String

    enum CodingKeys: String, CodingKey {
        case description, symbol, website
        case etfCompany = "etf_company"
        case assetClass = "asset_class"
        case expenseRatio = "expense_ratio"
        case inceptionDate = "inception_date"
        case domicile
        case indexTracked = "index_tracked"
    }
}

// MARK: - Related Ticker

struct RelatedTickerDTO: Decodable {
    let symbol: String
    let name: String
    let price: Double
    let changePercent: Double

    enum CodingKeys: String, CodingKey {
        case symbol, name, price
        case changePercent = "change_percent"
    }
}

// MARK: - ETF News Article

struct ETFNewsArticleDTO: Decodable {
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

extension ETFDetailResponseDTO {

    /// Convert the API response DTO to the display model used by the view.
    func toDisplayModel() -> ETFDetailData {
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
            let date = ETFResponseFormatters.parseISO8601(marketStatus.date ?? "") ?? Date()
            mktStatus = .closed(
                date: date,
                time: marketStatus.time ?? "4:00 PM",
                timezone: marketStatus.timezone ?? "EST"
            )
        }

        // Map flat key statistics
        let keyStats = keyStatistics.map { item in
            KeyStatistic(label: item.label, value: item.value, isHighlighted: item.isHighlighted)
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

        // Map identity rating
        let identity = ETFIdentityRating(
            score: identityRating.score,
            maxScore: identityRating.maxScore,
            esgRating: identityRating.esgRating,
            volatilityLabel: identityRating.volatilityLabel
        )

        // Map strategy
        let strat = ETFStrategy(
            hook: strategy.hook,
            tags: strategy.tags
        )

        // Map dividend history
        let divHistory = netYield.dividendHistory.map { d in
            ETFDividendPayment(
                dividendPerShare: d.dividendPerShare,
                exDividendDate: d.exDividendDate,
                payDate: d.payDate
            )
        }

        let lastDiv = ETFDividendPayment(
            dividendPerShare: netYield.lastDividendPayment.dividendPerShare,
            exDividendDate: netYield.lastDividendPayment.exDividendDate,
            payDate: netYield.lastDividendPayment.payDate
        )

        // Map net yield
        let yield_ = ETFNetYield(
            expenseRatio: netYield.expenseRatio,
            feeContext: netYield.feeContext,
            dividendYield: netYield.dividendYield,
            payFrequency: netYield.payFrequency,
            yieldContext: netYield.yieldContext,
            verdict: netYield.verdict,
            lastDividendPayment: lastDiv,
            dividendHistory: divHistory
        )

        // Map asset allocation
        let alloc = ETFAssetAllocation(
            equities: holdingsRisk.assetAllocation.equities,
            bonds: holdingsRisk.assetAllocation.bonds,
            crypto: holdingsRisk.assetAllocation.crypto,
            cash: holdingsRisk.assetAllocation.cash,
            totalAssets: holdingsRisk.assetAllocation.totalAssets
        )

        // Map sectors
        let sectors = holdingsRisk.topSectors.map { s in
            ETFSectorWeight(name: s.name, weight: s.weight)
        }

        // Map holdings
        let holdings = holdingsRisk.topHoldings.map { h in
            ETFTopHolding(symbol: h.symbol, name: h.name, weight: h.weight)
        }

        // Map concentration
        let conc = ETFConcentration(
            topN: holdingsRisk.concentration.topN,
            weight: holdingsRisk.concentration.weight,
            insight: holdingsRisk.concentration.insight
        )

        let holdRisk = ETFHoldingsRisk(
            assetAllocation: alloc,
            topSectors: sectors,
            topHoldings: holdings,
            concentration: conc
        )

        // Map ETF profile
        let profile = ETFProfile(
            description: etfProfile.description,
            symbol: etfProfile.symbol,
            etfCompany: etfProfile.etfCompany,
            assetClass: etfProfile.assetClass,
            expenseRatio: etfProfile.expenseRatio,
            inceptionDate: etfProfile.inceptionDate,
            domicile: etfProfile.domicile,
            indexTracked: etfProfile.indexTracked,
            website: etfProfile.website
        )

        // Map related ETFs
        let related = relatedEtfs.map { r in
            RelatedTicker(symbol: r.symbol, name: r.name, price: r.price, changePercent: r.changePercent)
        }

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

        return ETFDetailData(
            symbol: symbol,
            name: name,
            currentPrice: currentPrice,
            priceChange: priceChange,
            priceChangePercent: priceChangePercent,
            marketStatus: mktStatus,
            chartPricePoints: chartData.map {
                StockPricePoint(date: $0.date ?? "", close: $0.close, open: $0.open, high: $0.high, low: $0.low, volume: $0.volume)
            },
            keyStatistics: keyStats,
            keyStatisticsGroups: keyStatsGroups,
            performancePeriods: perfPeriods,
            identityRating: identity,
            strategy: strat,
            netYield: yield_,
            holdingsRisk: holdRisk,
            etfProfile: profile,
            relatedETFs: related,
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

            let publishedDate = ETFResponseFormatters.parseISO8601(dto.publishedAt) ?? Date()

            return TickerNewsArticle(
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
