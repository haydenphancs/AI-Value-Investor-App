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
        // FMP publishedDate is space-separated with no timezone ("2024-01-15 09:30:00").
        // The ISO8601 parsers above require a 'T' + offset and fail on it, so EVERY ETF
        // news article fell back to Date() and rendered as "just now" (and mis-sorted).
        // Treat the naive timestamp as UTC (matches TickerDetailViewModel.parseDate).
        let dt = DateFormatter()
        dt.locale = Locale(identifier: "en_US_POSIX")
        dt.timeZone = TimeZone(identifier: "UTC")
        dt.dateFormat = "yyyy-MM-dd HH:mm:ss"
        if let d = dt.date(from: string) { return d }
        let df = DateFormatter()
        df.locale = Locale(identifier: "en_US_POSIX")
        df.dateFormat = "yyyy-MM-dd"
        return df.date(from: string)
    }
}

// MARK: - Light Refresh Slice

/// `GET /api/v1/etfs/{symbol}/quote` — the payload the 30-second loop and the range picker
/// actually need.
///
/// Every field name and type matches `ETFDetailResponseDTO`, so this reuses the same nested
/// DTOs. What it drops is everything a 30-second refresh cannot change: performance
/// periods, benchmark, profile, identity rating, strategy, net yield, holdings and news.
///
/// The loop used to call `getETFDetail` and then assign the WHOLE view model, which erased
/// every WebSocket tick that had landed since the last refresh — a 30-second price sawtooth.
struct ETFQuoteResponseDTO: Decodable {
    let symbol: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: MarketStatusDTO
    let chartData: [StockOverviewPricePointDTO]
    let keyStatistics: [KeyStatisticItemDTO]
    let keyStatisticsGroups: [KeyStatisticsGroupDTO]
    let relatedEtfs: [RelatedTickerDTO]

    enum CodingKeys: String, CodingKey {
        case symbol
        case currentPrice = "current_price"
        case priceChange = "price_change"
        case priceChangePercent = "price_change_percent"
        case marketStatus = "market_status"
        case chartData = "chart_data"
        case keyStatistics = "key_statistics"
        case keyStatisticsGroups = "key_statistics_groups"
        case relatedEtfs = "related_etfs"
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
    let volatilityLabel: String

    enum CodingKeys: String, CodingKey {
        case score
        case maxScore = "max_score"
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
    /// Optional: absent on pre-existing cached ETF payloads. Gold/commodity funds
    /// route here instead of being mislabeled as equities (or 100% cash).
    let commodities: Double?
    let cash: Double
    let totalAssets: String

    enum CodingKeys: String, CodingKey {
        case equities, bonds, crypto, commodities, cash
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
    let inceptionDate: String
    let domicile: String
    let indexTracked: String
    let website: String

    enum CodingKeys: String, CodingKey {
        case description, symbol, website
        case etfCompany = "etf_company"
        case assetClass = "asset_class"
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

    // Tolerant decode: `sentiment`, `published_at`, and the arrays are the only
    // REQUIRED-typed fields on this embedded shape. A single null in any of them
    // (a future `_build_news` change) would fail decoding of the ENTIRE ETF
    // detail response, not just news. Absorb null/missing here instead.
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

// MARK: - ETF Dividend History Response (dedicated endpoint)

struct ETFDividendHistoryResponseDTO: Decodable {
    let symbol: String
    let payFrequency: String
    let totalDividends: Int
    let dividends: [ETFDividendPaymentDTO]

    enum CodingKeys: String, CodingKey {
        case symbol
        case payFrequency = "pay_frequency"
        case totalDividends = "total_dividends"
        case dividends
    }

    func toDisplayModels() -> [ETFDividendPayment] {
        dividends.map { d in
            ETFDividendPayment(
                dividendPerShare: d.dividendPerShare,
                exDividendDate: d.exDividendDate,
                payDate: d.payDate
            )
        }
    }
}

// MARK: - ETF Holdings & Risk Response (dedicated endpoint)

/// Reuses the same nested DTOs as the embedded response.
/// The dedicated endpoint returns this at the top level.
extension ETFHoldingsRiskDTO {
    func toDisplayModel() -> ETFHoldingsRisk {
        let alloc = ETFAssetAllocation(
            equities: assetAllocation.equities,
            bonds: assetAllocation.bonds,
            crypto: assetAllocation.crypto,
            commodities: assetAllocation.commodities ?? 0,
            cash: assetAllocation.cash,
            totalAssets: assetAllocation.totalAssets
        )

        let sectors = topSectors.map { s in
            ETFSectorWeight(name: s.name, weight: s.weight)
        }

        let holdings = topHoldings.map { h in
            ETFTopHolding(symbol: h.symbol, name: h.name, weight: h.weight)
        }

        let conc = ETFConcentration(
            topN: concentration.topN,
            weight: concentration.weight,
            insight: concentration.insight
        )

        return ETFHoldingsRisk(
            assetAllocation: alloc,
            topSectors: sectors,
            topHoldings: holdings,
            concentration: conc
        )
    }
}

// MARK: - ETF Profile (dedicated endpoint)

extension ETFProfileDTO {
    func toDisplayModel() -> ETFProfile {
        ETFProfile(
            description: description,
            symbol: symbol,
            etfCompany: etfCompany,
            assetClass: assetClass,
            inceptionDate: inceptionDate,
            domicile: domicile,
            indexTracked: indexTracked,
            website: website
        )
    }
}

// MARK: - ──────────────────────────────────────────────
// MARK:   DTO → Display Model Mapping
// MARK: - ──────────────────────────────────────────────

// MARK: - Light Slice → Display Merge

extension ETFQuoteResponseDTO {
    /// Merge the volatile slice into an existing `ETFDetailData` IN PLACE.
    ///
    /// Only the fields this slice actually carries are written. The close-cadence sections
    /// — performance periods, benchmark, profile, identity rating, strategy, net yield,
    /// holdings — are left exactly as they were, because they are range-independent and
    /// cannot change between two 30-second refreshes.
    ///
    /// `livePrice` WINS over the REST snapshot when the socket has ticked: a tick is now, a
    /// snapshot is up to 45 seconds old. Falling back to REST keeps the header alive for
    /// symbols whose feed never ticks, which is the reason the poll exists at all.
    func merged(
        into data: ETFDetailData,
        livePrice: Double?,
        liveChange: Double?,
        liveChangePercent: Double?,
        includeChart: Bool
    ) -> ETFDetailData {
        var out = data
        out.currentPrice = livePrice ?? currentPrice
        out.priceChange = liveChange ?? priceChange
        out.priceChangePercent = liveChangePercent ?? priceChangePercent
        out.marketStatus = marketStatus.resolvedMarketStatus
        out.keyStatistics = keyStatistics.map {
            KeyStatistic(label: $0.label, value: $0.value,
                         isHighlighted: $0.isHighlighted, colorState: $0.colorState)
        }
        out.keyStatisticsGroups = keyStatisticsGroups.map { group in
            KeyStatisticsGroup(statistics: group.statistics.map {
                KeyStatistic(label: $0.label, value: $0.value,
                             isHighlighted: $0.isHighlighted, colorState: $0.colorState)
            })
        }
        // Keep the previous list when the refresh returns none — a cache miss on the
        // related quotes must not blank a populated "Related ETFs" row.
        if !relatedEtfs.isEmpty {
            out.relatedETFs = relatedEtfs.map {
                RelatedTicker(symbol: $0.symbol, name: $0.name,
                              price: $0.price, changePercent: $0.changePercent)
            }
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

extension ETFDetailResponseDTO {
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
            KeyStatistic(label: item.label, value: item.value, isHighlighted: item.isHighlighted, colorState: item.colorState)
        }

        // Map key statistics groups
        let keyStatsGroups = keyStatisticsGroups.map { group in
            KeyStatisticsGroup(statistics: group.statistics.map { item in
                KeyStatistic(label: item.label, value: item.value, isHighlighted: item.isHighlighted, colorState: item.colorState)
            })
        }

        // Map performance periods (with S&P 500 comparison)
        let perfPeriods = performancePeriods.map { p in
            PerformancePeriod(
                label: p.label,
                changePercent: p.changePercent,
                vsMarketPercent: p.vsMarketPercent,
                spReturnPercent: p.spReturnPercent
            )
        }

        // Map identity rating
        let identity = ETFIdentityRating(
            score: identityRating.score,
            maxScore: identityRating.maxScore,
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
            commodities: holdingsRisk.assetAllocation.commodities ?? 0,
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
            inceptionDate: etfProfile.inceptionDate,
            domicile: etfProfile.domicile,
            indexTracked: etfProfile.indexTracked,
            website: etfProfile.website
        )

        // Map related ETFs
        let related = relatedEtfs.map { r in
            RelatedTicker(symbol: r.symbol, name: r.name, price: r.price, changePercent: r.changePercent)
        }

        // Map benchmark (with dynamic S&P CAGR)
        let benchmark: PerformanceBenchmarkSummary?
        if let bs = benchmarkSummary {
            benchmark = PerformanceBenchmarkSummary(
                avgAnnualReturn: bs.avgAnnualReturn,
                spBenchmark: bs.spBenchmark,
                benchmarkName: bs.benchmarkName ?? "S&P 500",
                sinceDate: bs.sinceDate ?? "",
                windowLabel: bs.windowLabel,
                benchmarkAvailable: bs.benchmarkAvailable ?? true,
                alltimeAnnualReturn: bs.alltimeAnnualReturn,
                alltimeBenchmark: bs.alltimeBenchmark,
                alltimeSinceDate: bs.alltimeSinceDate
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
// `GET /api/v1/etfs/{{symbol}}/core` — the header line, and the chart when it was
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

/// Fast-core payload for the etfs detail screen. Field names mirror the full detail
/// DTO and reuse its nested types, so `full ?? core` is a drop-in swap in the view.
struct ETFCoreResponseDTO: Decodable {
    let symbol: String
    let name: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: MarketStatusDTO
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

    func toCoreData() -> ETFCoreData {
        ETFCoreData(
            symbol: symbol,
            name: name,
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
/// Every formatted string delegates to `ETFHeaderFormat`, the SAME helper the full display model
/// uses — so nothing visibly reformats when core is superseded.
struct ETFCoreData {
    let symbol: String
    let name: String
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

    var formattedPrice: String { ETFHeaderFormat.price(currentPrice) }
    var formattedChange: String { ETFHeaderFormat.change(priceChange) }
    var formattedChangePercent: String {
        ETFHeaderFormat.changePercent(priceChangePercent)
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
protocol ETFHeaderRenderable {
    var symbol: String { get }
    var name: String { get }
    var formattedPrice: String { get }
    var formattedChange: String { get }
    var formattedChangePercent: String { get }
    var isPositive: Bool { get }
    var marketStatus: MarketStatus { get }
    var chartPricePoints: [StockPricePoint] { get }
    var previousClose: Double { get }
}

extension ETFDetailData: ETFHeaderRenderable {}
extension ETFCoreData: ETFHeaderRenderable {}
