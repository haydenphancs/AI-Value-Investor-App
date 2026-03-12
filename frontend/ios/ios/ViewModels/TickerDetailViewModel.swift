//
//  TickerDetailViewModel.swift
//  ios
//
//  ViewModel for the Ticker Detail screen
//

import Foundation
import SwiftUI
import Combine

@MainActor
class TickerDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var tickerData: TickerDetailData?
    @Published var newsArticles: [TickerNewsArticle] = []  // displayed (paginated)
    @Published var isNewsLoading: Bool = false
    @Published var hasMoreNews: Bool = false
    @Published var isLoadingMoreNews: Bool = false
    @Published var analysisData: TickerAnalysisData?
    @Published var earningsData: EarningsData?
    @Published var growthData: GrowthSectionData?
    @Published var profitPowerData: ProfitPowerSectionData?
    @Published var signalOfConfidenceData: SignalOfConfidenceSectionData?
    @Published var revenueBreakdownData: RevenueBreakdownData?
    @Published var healthCheckData: HealthCheckSectionData?
    @Published var holdersData: HoldersData?
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: TickerDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .oneDay
    @Published var isFavorite: Bool = false
    @Published var aiInputText: String = ""
    @Published var pendingAIQuery: String?

    // Chart settings
    @Published var chartSettings = ChartSettings()
    @Published var chartDataVersion: Int = 0

    // Analysis tab state
    @Published var selectedMomentumPeriod: AnalystMomentumPeriod = .sixMonths
    @Published var selectedSentimentTimeframe: SentimentTimeframe = .last24h

    // MARK: - API Data (live from backend)
    @Published var stockDetail: StockDetail?
    @Published var stockQuote: StockQuote?

    // MARK: - Private Properties

    private let tickerSymbol: String
    private let stockRepository: StockRepository
    private var cancellables = Set<AnyCancellable>()
    private var allNewsArticles: [TickerNewsArticle] = []  // full set from API
    private var newsDisplayCount: Int = 10
    private let newsPageSize: Int = 10

    // MARK: - Initialization

    init(tickerSymbol: String, stockRepository: StockRepository? = nil) {
        self.tickerSymbol = tickerSymbol
        self.stockRepository = stockRepository ?? StockRepository()

        // Observe chart range changes: auto-set default interval and fetch new chart data
        $selectedChartRange
            .dropFirst() // Skip initial value
            .removeDuplicates()
            .sink { [weak self] range in
                guard let self = self else { return }
                print("📈 TickerDetailVM: Chart range changed to \(range.rawValue)")
                self.chartSettings.selectedInterval = range.defaultInterval
                Task { [weak self] in
                    guard let self = self else { return }
                    await self.fetchChartData(self.tickerSymbol, range: range)
                }
            }
            .store(in: &cancellables)

        // Observe interval changes and re-fetch chart data
        chartSettings.$selectedInterval
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] _ in
                guard let self = self else { return }
                Task { [weak self] in
                    guard let self = self else { return }
                    await self.fetchChartData(self.tickerSymbol, range: self.selectedChartRange)
                }
            }
            .store(in: &cancellables)

        // Observe extended hours toggle and re-fetch chart data
        chartSettings.$showExtendedHours
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] _ in
                guard let self = self else { return }
                Task { [weak self] in
                    guard let self = self else { return }
                    await self.fetchChartData(self.tickerSymbol, range: self.selectedChartRange)
                }
            }
            .store(in: &cancellables)
    }

    // MARK: - Public Methods

    func loadTickerData() {
        isLoading = true
        errorMessage = nil

        Task { [weak self] in
            guard let self = self else { return }

            let ticker = self.tickerSymbol
            print("📊 TickerDetailVM: Loading data for \(ticker) from API...")

            // Try the aggregated overview endpoint first (all Overview tab data in one call)
            do {
                let response = try await self.stockRepository.getStockOverview(
                    ticker: ticker, range: self.selectedChartRange.rawValue,
                    interval: self.chartSettings.selectedInterval.rawValue
                )
                self.tickerData = response.toDisplayModel()
                self.chartDataVersion += 1
                print("✅ TickerDetailVM: Overview loaded for \(ticker) — price: \(self.tickerData?.currentPrice ?? 0)")
            } catch {
                print("⚠️ TickerDetailVM: Overview failed, falling back to separate calls: \(error)")
                // Fallback: use existing separate API calls + sample data for missing sections
                await withTaskGroup(of: Void.self) { group in
                    group.addTask { await self.fetchStockDetail(ticker) }
                    group.addTask { await self.fetchStockQuote(ticker) }
                }
                self.tickerData = self.buildTickerDetailData()
                print("📊 TickerDetailVM: Built fallback TickerDetailData for \(ticker)")
                // Fetch real chart data for the fallback path
                await self.fetchChartData(ticker, range: self.selectedChartRange)
            }

            // Fetch news in parallel (not included in overview endpoint)
            await self.fetchStockNews(ticker)

            await self.fetchAnalystAnalysis(ticker)
            self.earningsData = EarningsData.sampleData
            self.growthData = GrowthSectionData.sampleData
            self.profitPowerData = ProfitPowerSectionData.sampleData
            self.signalOfConfidenceData = SignalOfConfidenceSectionData.sampleData
            self.revenueBreakdownData = RevenueBreakdownData.sampleApple
            self.healthCheckData = HealthCheckSectionData.sampleData
            self.holdersData = HoldersData.sampleData

            // If we don't have API news, load sample news
            if self.newsArticles.isEmpty {
                self.newsArticles = TickerNewsArticle.sampleDataForTicker(ticker)
            }

            self.isLoading = false
        }
    }

    // MARK: - API Fetching

    private func fetchStockDetail(_ ticker: String) async {
        do {
            let detail = try await stockRepository.getStock(ticker: ticker)
            self.stockDetail = detail
            print("✅ TickerDetailVM: Got stock detail for \(ticker) — price: \(detail.price ?? 0)")
        } catch {
            print("⚠️ TickerDetailVM: Failed to fetch stock detail for \(ticker): \(error)")
            // Non-fatal: we'll use sample data
        }
    }

    private func fetchStockQuote(_ ticker: String) async {
        do {
            let quote = try await stockRepository.getStockQuote(ticker: ticker)
            self.stockQuote = quote
            print("✅ TickerDetailVM: Got quote for \(ticker) — price: \(quote.price ?? 0)")
        } catch {
            print("⚠️ TickerDetailVM: Failed to fetch quote for \(ticker): \(error)")
        }
    }

    private func fetchStockNews(_ ticker: String) async {
        self.isNewsLoading = true
        do {
            let response = try await stockRepository.getStockNews(ticker: ticker, limit: 50)
            let apiNews = response.articles
            let cached = response.cached ?? false
            print("✅ TickerDetailVM: Got \(apiNews.count) news articles for \(ticker) (cached: \(cached))")

            // Convert all API news to UI models
            self.allNewsArticles = apiNews.map { mapApiToUiArticle($0) }
            self.newsDisplayCount = newsPageSize
            self.hasMoreNews = allNewsArticles.count > newsDisplayCount

            // Enrich the first batch BEFORE showing articles
            let firstBatch = Array(allNewsArticles.prefix(newsDisplayCount))
            let unenrichedIds = firstBatch
                .filter { !$0.aiProcessed }
                .map { $0.apiId }
                .filter { !$0.isEmpty && !$0.hasPrefix("temp_") && !$0.hasPrefix("raw_") }

            if !unenrichedIds.isEmpty {
                await attemptEnrichment(ticker: ticker, articleIds: unenrichedIds)
            }

            // NOW show articles (enriched or raw fallback)
            self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
        } catch {
            print("⚠️ TickerDetailVM: Failed to fetch news for \(ticker): \(error)")
        }
        self.isNewsLoading = false
    }

    private enum AnalysisPayload: Sendable {
        case ratings(AnalystRatingsData)
        case sentiment(SentimentAnalysisData)
        case technical(TechnicalAnalysisData)
    }

    private func fetchAnalystAnalysis(_ ticker: String) async {
        // Fetch analyst + sentiment + technical in parallel — no sample-data fallback
        var ratingsData: AnalystRatingsData?
        var sentimentData: SentimentAnalysisData?
        var techData: TechnicalAnalysisData?

        let results = await withTaskGroup(of: AnalysisPayload?.self) { group -> [AnalysisPayload] in
            group.addTask { [self] in
                do {
                    let dto = try await stockRepository.getAnalystAnalysis(ticker: ticker)
                    print("✅ TickerDetailVM: Got analyst analysis for \(ticker) — \(dto.totalAnalysts) analysts, consensus: \(dto.consensus)")
                    return await .ratings(dto.toDisplayModel())
                } catch {
                    print("⚠️ TickerDetailVM: Analyst analysis failed for \(ticker): \(error)")
                    return nil
                }
            }
            group.addTask { [self] in
                do {
                    let dto = try await stockRepository.getSentimentAnalysis(ticker: ticker)
                    print("✅ TickerDetailVM: Got sentiment for \(ticker) — mood: \(dto.moodScore)")
                    return await .sentiment(dto.toDisplayModel())
                } catch {
                    print("⚠️ TickerDetailVM: Sentiment analysis failed for \(ticker): \(error)")
                    return nil
                }
            }
            group.addTask { [self] in
                do {
                    let dto = try await stockRepository.getTechnicalAnalysis(ticker: ticker)
                    print("✅ TickerDetailVM: Got technical analysis for \(ticker) — gauge: \(dto.gaugeValue), daily: \(dto.dailySignal.matchingIndicators)/\(dto.dailySignal.totalIndicators), weekly: \(dto.weeklySignal.matchingIndicators)/\(dto.weeklySignal.totalIndicators)")
                    return await .technical(dto.toDisplayModel())
                } catch {
                    print("⚠️ TickerDetailVM: Technical analysis failed for \(ticker): \(error)")
                    return nil
                }
            }

            var collected: [AnalysisPayload] = []
            for await result in group {
                if let result { collected.append(result) }
            }
            return collected
        }

        // Unpack results — only real API data, no sample fallback
        for payload in results {
            switch payload {
            case .ratings(let data): ratingsData = data
            case .sentiment(let data): sentimentData = data
            case .technical(let data): techData = data
            }
        }

        // Only set analysisData if we got at least one real response
        guard ratingsData != nil || sentimentData != nil || techData != nil else {
            print("⚠️ TickerDetailVM: All analysis calls failed for \(ticker) — no data to show")
            return
        }

        self.analysisData = TickerAnalysisData(
            analystRatings: ratingsData ?? AnalystRatingsData.sampleData,
            sentimentAnalysis: sentimentData ?? SentimentAnalysisData.sampleData,
            technicalAnalysis: techData ?? TechnicalAnalysisData.sampleData
        )
    }

    func loadMoreNews() {
        guard !isLoadingMoreNews, hasMoreNews else { return }
        isLoadingMoreNews = true

        newsDisplayCount += newsPageSize
        newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
        hasMoreNews = allNewsArticles.count > newsDisplayCount
        isLoadingMoreNews = false

        // Enrich newly visible articles in the background
        Task {
            await enrichVisibleArticles()
        }
    }

    /// Attempt enrichment with one retry on failure (3s delay between attempts)
    private func attemptEnrichment(ticker: String, articleIds: [String], maxAttempts: Int = 2) async {
        for attempt in 1...maxAttempts {
            do {
                let enrichResponse = try await stockRepository.enrichStockNews(
                    ticker: ticker,
                    articleIds: articleIds
                )
                mergeEnrichment(enrichResponse.articles)

                // Check if any articles were actually enriched
                let enrichedCount = allNewsArticles.prefix(newsDisplayCount)
                    .filter { $0.aiProcessed }.count
                if enrichedCount > 0 {
                    print("✅ TickerDetailVM: Attempt \(attempt) enriched \(enrichedCount) articles")
                    return
                } else if attempt < maxAttempts {
                    print("⚠️ TickerDetailVM: Attempt \(attempt) returned 0 enriched, retrying in 3s...")
                    try await Task.sleep(nanoseconds: 3_000_000_000)
                } else {
                    print("⚠️ TickerDetailVM: Enrichment returned 0 enriched after \(maxAttempts) attempts")
                }
            } catch {
                if attempt < maxAttempts {
                    print("⚠️ TickerDetailVM: Enrichment attempt \(attempt) failed: \(error), retrying...")
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                } else {
                    print("⚠️ TickerDetailVM: Enrichment failed after \(maxAttempts) attempts: \(error)")
                }
            }
        }
    }

    private func enrichVisibleArticles() async {
        // Find un-enriched articles in the visible set
        let unenriched = newsArticles.filter { !$0.aiProcessed }
        guard !unenriched.isEmpty else { return }

        let ids = unenriched.map { $0.apiId }.filter { !$0.isEmpty && !$0.hasPrefix("temp_") && !$0.hasPrefix("raw_") }
        guard !ids.isEmpty else { return }

        await attemptEnrichment(ticker: tickerSymbol, articleIds: ids)
        newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
    }

    private func mergeEnrichment(_ enrichedArticles: [StockNewsArticle]) {
        let enrichedById = Dictionary(
            enrichedArticles.map { ($0.id, $0) },
            uniquingKeysWith: { first, _ in first }
        )

        var actuallyEnriched = 0
        for i in allNewsArticles.indices {
            if let enriched = enrichedById[allNewsArticles[i].apiId] {
                // Only mark as processed if the backend actually enriched it
                let wasProcessed = enriched.aiProcessed ?? false
                let hasBullets = enriched.summaryBullets?.isEmpty == false

                if wasProcessed || hasBullets {
                    let bullets: [String] = {
                        if let b = enriched.summaryBullets, !b.isEmpty { return b }
                        if let s = enriched.summary, !s.isEmpty { return [s] }
                        return allNewsArticles[i].summaryBullets
                    }()
                    allNewsArticles[i].summaryBullets = bullets
                    allNewsArticles[i].sentiment = mapSentiment(enriched.sentiment)
                    allNewsArticles[i].aiProcessed = true
                    actuallyEnriched += 1
                }
                // If not processed, leave aiProcessed = false so we can retry
            }
        }
        print("📰 TickerDetailVM: Merged \(actuallyEnriched)/\(enrichedArticles.count) truly enriched articles")
    }

    private func mapApiToUiArticle(_ article: StockNewsArticle) -> TickerNewsArticle {
        let bullets: [String] = {
            if let aiBullets = article.summaryBullets, !aiBullets.isEmpty {
                return aiBullets
            }
            if let summary = article.summary, !summary.isEmpty {
                return [summary]
            }
            return []
        }()

        return TickerNewsArticle(
            apiId: article.id,
            headline: article.title,
            source: NewsSource(name: article.source ?? "Unknown", iconName: nil),
            sentiment: mapSentiment(article.sentiment),
            publishedAt: article.publishedAt.flatMap { parseDate($0) } ?? Date(),
            thumbnailName: nil,
            imageURL: article.imageUrl.flatMap { URL(string: $0) },
            relatedTickers: article.relatedTickers ?? [],
            summaryBullets: bullets,
            articleURL: article.url.flatMap { URL(string: $0) },
            aiProcessed: article.aiProcessed ?? false
        )
    }

    private func mapSentiment(_ sentiment: String?) -> NewsSentiment {
        switch sentiment?.lowercased() {
        case "positive", "bullish": return .positive
        case "negative", "bearish": return .negative
        default: return .neutral
        }
    }

    private func parseDate(_ dateString: String) -> Date? {
        // Try ISO 8601 first (e.g. "2026-03-08T12:06:00+00:00")
        let isoFormatter = ISO8601DateFormatter()
        isoFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = isoFormatter.date(from: dateString) { return date }
        isoFormatter.formatOptions = [.withInternetDateTime]
        if let date = isoFormatter.date(from: dateString) { return date }

        // Try "yyyy-MM-dd HH:mm:ss" (FMP raw format)
        let dateFormatter = DateFormatter()
        dateFormatter.locale = Locale(identifier: "en_US_POSIX")
        dateFormatter.timeZone = TimeZone(identifier: "UTC")
        dateFormatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        if let date = dateFormatter.date(from: dateString) { return date }

        // Try "yyyy-MM-dd" only
        dateFormatter.dateFormat = "yyyy-MM-dd"
        return dateFormatter.date(from: dateString)
    }

    func refresh() async {
        loadTickerData()
    }

    func toggleFavorite() {
        isFavorite.toggle()
    }

    func handleNotificationTap() {
        print("Notification settings for \(tickerSymbol)")
    }

    func handleMoreOptions() {
        print("More options for \(tickerSymbol)")
    }

    func handleDeepResearch() {
        print("AI Deep Research for \(tickerSymbol)")
    }

    func handleWebsiteTap() {
        // Prefer API data for website
        let website = stockDetail?.website ?? tickerData?.companyProfile.website
        guard let site = website,
              let url = URL(string: site.hasPrefix("http") ? site : "https://\(site)") else { return }
        UIApplication.shared.open(url)
    }

    func handleRelatedTickerTap(_ ticker: RelatedTicker) {
        print("Navigate to \(ticker.symbol)")
    }

    func handleNewsArticleTap(_ article: TickerNewsArticle) {
        print("Open news article: \(article.headline)")
    }

    func handleNewsExternalLink(_ article: TickerNewsArticle) {
        guard let url = article.articleURL else { return }
        UIApplication.shared.open(url)
    }

    func handleNewsTickerTap(_ ticker: String) {
        print("Navigate to ticker: \(ticker)")
    }

    func handleSuggestionTap(_ suggestion: TickerAISuggestion) {
        aiInputText = suggestion.text
        handleAISend()
    }

    func handleAISend() {
        guard !aiInputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let query = aiInputText
        aiInputText = ""
        print("🤖 AI Query for \(tickerSymbol): \(query)")
        pendingAIQuery = query
    }

    func updateChartRange(_ range: ChartTimeRange) {
        selectedChartRange = range
        // Chart data fetching is handled by the Combine $selectedChartRange observer
    }

    // MARK: - Chart Data Fetching

    private func fetchChartData(_ ticker: String, range: ChartTimeRange) async {
        let rangeString = range.rawValue  // e.g. "3M", "1Y", "1D"
        print("📈 TickerDetailVM: Fetching chart data for \(ticker), range=\(rangeString)")
        do {
            let intervalString = chartSettings.selectedInterval.rawValue
            let useExtendedHours = chartSettings.showExtendedHours && chartSettings.selectedInterval.isIntraday
            let chartResponse = try await stockRepository.getStockChart(ticker: ticker, range: rangeString, interval: intervalString, extendedHours: useExtendedHours)
            let pricePoints = chartResponse.prices
            print("✅ TickerDetailVM: Got \(pricePoints.count) chart data points for \(ticker)")
            if !pricePoints.isEmpty, let currentData = self.tickerData {
                // Rebuild tickerData with new chart prices
                self.tickerData = TickerDetailData(
                    symbol: currentData.symbol,
                    companyName: currentData.companyName,
                    currentPrice: currentData.currentPrice,
                    priceChange: currentData.priceChange,
                    priceChangePercent: currentData.priceChangePercent,
                    marketStatus: currentData.marketStatus,
                    chartPricePoints: pricePoints,
                    keyStatistics: currentData.keyStatistics,
                    keyStatisticsGroups: currentData.keyStatisticsGroups,
                    performancePeriods: currentData.performancePeriods,
                    snapshots: currentData.snapshots,
                    sectorIndustry: currentData.sectorIndustry,
                    companyProfile: currentData.companyProfile,
                    relatedTickers: currentData.relatedTickers,
                    benchmarkSummary: currentData.benchmarkSummary
                )
                self.chartDataVersion += 1
            }
        } catch {
            print("⚠️ TickerDetailVM: Failed to fetch chart data for \(ticker): \(error)")
            // Non-fatal: keep existing chart data
        }
    }

    // MARK: - Build TickerDetailData from API

    private func buildTickerDetailData() -> TickerDetailData {
        let price = stockQuote?.price ?? stockDetail?.price ?? 0
        let change = stockQuote?.change ?? stockDetail?.change ?? 0
        let changePercent = stockQuote?.changePercent ?? stockDetail?.changePercent ?? 0
        let companyName = stockDetail?.companyName ?? tickerSymbol

        print("🔧 buildTickerDetailData: symbol=\(tickerSymbol), companyName=\(companyName), price=\(price), change=\(change)")

        // Build key statistics from real data
        let keyStats = buildKeyStatistics()
        let keyStatsGroups = buildKeyStatisticsGroups()

        // Build sector/industry from API data
        let sectorIndustry = SectorIndustryInfo(
            sector: stockDetail?.sector ?? "N/A",
            industry: stockDetail?.industry ?? "N/A",
            sectorPerformance: 0.0,   // Not available from current API
            industryRank: "--"        // Not available from current API
        )

        // Build company profile from API data
        let hq: String = {
            if let city = stockDetail?.city, let state = stockDetail?.state {
                return "\(city), \(state)"
            }
            return stockDetail?.country ?? "N/A"
        }()
        let companyProfile = CompanyProfile(
            description: stockDetail?.description ?? "No description available.",
            ceo: stockDetail?.ceo ?? "N/A",
            founded: stockDetail?.ipoDate ?? "N/A",
            employees: stockDetail?.fullTimeEmployees ?? 0,
            headquarters: hq,
            website: stockDetail?.website ?? "N/A"
        )

        // Determine market status based on current time
        let marketStatus = determineMarketStatus()

        // Use existing chart data if available, otherwise empty (will be fetched separately)
        let existingChart = self.tickerData?.chartPricePoints ?? []

        return TickerDetailData(
            symbol: tickerSymbol,
            companyName: companyName,
            currentPrice: price,
            priceChange: change,
            priceChangePercent: changePercent,
            marketStatus: marketStatus,
            chartPricePoints: existingChart,
            keyStatistics: keyStats,
            keyStatisticsGroups: keyStatsGroups,
            performancePeriods: PerformancePeriod.sampleData,   // No API backing yet
            snapshots: SnapshotItem.sampleData,                 // No API backing yet
            sectorIndustry: sectorIndustry,
            companyProfile: companyProfile,
            relatedTickers: RelatedTicker.sampleData,           // No API backing yet
            benchmarkSummary: nil                               // No API backing yet
        )
    }

    // MARK: - Build Key Statistics

    private func buildKeyStatistics() -> [KeyStatistic] {
        var stats: [KeyStatistic] = []

        // Open
        if let open = stockQuote?.open {
            stats.append(KeyStatistic(label: "Open", value: String(format: "%.2f", open)))
        } else {
            stats.append(KeyStatistic(label: "Open", value: "--"))
        }

        // Previous Close
        if let prevClose = stockQuote?.previousClose {
            stats.append(KeyStatistic(label: "Previous Close", value: String(format: "%.2f", prevClose)))
        } else {
            stats.append(KeyStatistic(label: "Previous Close", value: "--"))
        }

        // Day High
        if let high = stockQuote?.high {
            stats.append(KeyStatistic(label: "Day High", value: String(format: "%.2f", high)))
        } else {
            stats.append(KeyStatistic(label: "Day High", value: "--"))
        }

        // Day Low
        if let low = stockQuote?.low {
            stats.append(KeyStatistic(label: "Day Low", value: String(format: "%.2f", low)))
        } else {
            stats.append(KeyStatistic(label: "Day Low", value: "--"))
        }

        // Volume
        if let volume = stockQuote?.volume ?? stockDetail?.volume {
            stats.append(KeyStatistic(label: "Volume", value: formatLargeNumber(volume)))
        } else {
            stats.append(KeyStatistic(label: "Volume", value: "--"))
        }

        // Avg Volume
        if let avgVol = stockDetail?.avgVolume {
            stats.append(KeyStatistic(label: "Avg. Volume (3M)", value: formatLargeNumber(avgVol)))
        } else {
            stats.append(KeyStatistic(label: "Avg. Volume (3M)", value: "--"))
        }

        // Market Cap
        if let marketCap = stockDetail?.marketCap {
            stats.append(KeyStatistic(label: "Market Cap", value: formatMarketCap(marketCap)))
        } else {
            stats.append(KeyStatistic(label: "Market Cap", value: "--"))
        }

        // 52-Week High
        if let high52 = stockDetail?.high52Week {
            stats.append(KeyStatistic(label: "52-Week High", value: String(format: "%.2f", high52)))
        } else {
            stats.append(KeyStatistic(label: "52-Week High", value: "--"))
        }

        // 52-Week Low
        if let low52 = stockDetail?.low52Week {
            stats.append(KeyStatistic(label: "52-Week Low", value: String(format: "%.2f", low52)))
        } else {
            stats.append(KeyStatistic(label: "52-Week Low", value: "--"))
        }

        // P/E from quote
        if let pe = stockQuote?.pe, pe > 0 {
            stats.append(KeyStatistic(label: "P/E (TTM)", value: String(format: "%.2f", pe)))
        } else {
            stats.append(KeyStatistic(label: "P/E (TTM)", value: "--"))
        }

        // EPS from quote
        if let eps = stockQuote?.eps {
            stats.append(KeyStatistic(label: "EPS (TTM)", value: String(format: "%.2f", eps)))
        } else {
            stats.append(KeyStatistic(label: "EPS (TTM)", value: "--"))
        }

        // Beta from profile
        if let beta = stockDetail?.beta {
            stats.append(KeyStatistic(label: "Beta", value: String(format: "%.2f", beta)))
        } else {
            stats.append(KeyStatistic(label: "Beta", value: "--"))
        }

        // Dividend from profile
        if let lastDiv = stockDetail?.lastDiv, lastDiv > 0, let price = stockQuote?.price ?? stockDetail?.price, price > 0 {
            let yield = (lastDiv * 4 / price) * 100  // Annualized yield estimate
            stats.append(KeyStatistic(label: "Dividend & Yield", value: String(format: "%.2f (%.2f%%)", lastDiv, yield)))
        } else {
            stats.append(KeyStatistic(label: "Dividend & Yield", value: "--"))
        }

        return stats
    }

    // MARK: - Build Key Statistics Groups

    private func buildKeyStatisticsGroups() -> [KeyStatisticsGroup] {
        // Column 1: Price & Volume
        let priceVolumeStats: [KeyStatistic] = [
            KeyStatistic(
                label: "Open",
                value: stockQuote?.open != nil ? String(format: "%.2f", stockQuote!.open!) : "--"
            ),
            KeyStatistic(
                label: "Previous Close",
                value: stockQuote?.previousClose != nil ? String(format: "%.2f", stockQuote!.previousClose!) : "--"
            ),
            KeyStatistic(
                label: "Volume",
                value: {
                    if let vol = stockQuote?.volume ?? stockDetail?.volume {
                        return formatLargeNumber(vol)
                    }
                    return "--"
                }()
            ),
            KeyStatistic(
                label: "Avg. Volume (3M)",
                value: stockDetail?.avgVolume != nil ? formatLargeNumber(stockDetail!.avgVolume!) : "--"
            ),
            KeyStatistic(
                label: "Market Cap",
                value: stockDetail?.marketCap != nil ? formatMarketCap(stockDetail!.marketCap!) : "--"
            )
        ]

        // Column 2: Day Range & 52-Week
        let rangeStats: [KeyStatistic] = [
            KeyStatistic(
                label: "Day High",
                value: stockQuote?.high != nil ? String(format: "%.2f", stockQuote!.high!) : "--"
            ),
            KeyStatistic(
                label: "Day Low",
                value: stockQuote?.low != nil ? String(format: "%.2f", stockQuote!.low!) : "--"
            ),
            KeyStatistic(
                label: "52-Week High",
                value: stockDetail?.high52Week != nil ? String(format: "%.2f", stockDetail!.high52Week!) : "--"
            ),
            KeyStatistic(
                label: "52-Week Low",
                value: stockDetail?.low52Week != nil ? String(format: "%.2f", stockDetail!.low52Week!) : "--"
            )
        ]

        // Column 3: Valuation (from quote + profile)
        let peValue: String = {
            if let pe = stockQuote?.pe, pe > 0 { return String(format: "%.2f", pe) }
            return "--"
        }()
        let epsValue: String = {
            if let eps = stockQuote?.eps { return String(format: "%.2f", eps) }
            return "--"
        }()
        let betaValue: String = {
            if let beta = stockDetail?.beta { return String(format: "%.2f", beta) }
            return "--"
        }()
        let divValue: String = {
            if let lastDiv = stockDetail?.lastDiv, lastDiv > 0,
               let price = stockQuote?.price ?? stockDetail?.price, price > 0 {
                let yield = (lastDiv * 4 / price) * 100
                return String(format: "%.2f (%.2f%%)", lastDiv, yield)
            }
            return "--"
        }()
        let valuationStats: [KeyStatistic] = [
            KeyStatistic(label: "P/E (TTM)", value: peValue),
            KeyStatistic(label: "P/E (FWD)", value: "--"),
            KeyStatistic(label: "EPS (TTM)", value: epsValue),
            KeyStatistic(label: "Dividend & Yield", value: divValue),
            KeyStatistic(label: "Beta", value: betaValue)
        ]

        // Column 4: Shares & Ownership (from quote)
        let sharesValue: String = {
            if let shares = stockQuote?.sharesOutstanding { return formatLargeNumber(shares) }
            return "--"
        }()
        let ownershipStats: [KeyStatistic] = [
            KeyStatistic(label: "Short % of Float", value: "--"),
            KeyStatistic(label: "Shares Outstanding", value: sharesValue),
            KeyStatistic(label: "Float", value: "--"),
            KeyStatistic(label: "% Held by Insiders", value: "--"),
            KeyStatistic(label: "% Held Inst.", value: "--")
        ]

        return [
            KeyStatisticsGroup(statistics: priceVolumeStats),
            KeyStatisticsGroup(statistics: rangeStats),
            KeyStatisticsGroup(statistics: valuationStats),
            KeyStatisticsGroup(statistics: ownershipStats)
        ]
    }

    // MARK: - Market Status

    private func determineMarketStatus() -> MarketStatus {
        let now = Date()
        let nyTimeZone = TimeZone(identifier: "America/New_York")!

        var nyCalendar = Calendar.current
        nyCalendar.timeZone = nyTimeZone

        let weekday = nyCalendar.component(.weekday, from: now)
        let hour = nyCalendar.component(.hour, from: now)
        let minute = nyCalendar.component(.minute, from: now)
        let totalMinutes = hour * 60 + minute

        // Weekend
        if weekday == 1 || weekday == 7 {
            return .closed(date: now, time: "4:00 PM", timezone: "EST")
        }

        let marketOpen = 9 * 60 + 30   // 9:30 AM ET
        let marketClose = 16 * 60       // 4:00 PM ET
        let preMarketStart = 4 * 60     // 4:00 AM ET
        let afterHoursEnd = 20 * 60     // 8:00 PM ET

        if totalMinutes >= marketOpen && totalMinutes < marketClose {
            return .open
        } else if totalMinutes >= preMarketStart && totalMinutes < marketOpen {
            return .preMarket
        } else if totalMinutes >= marketClose && totalMinutes < afterHoursEnd {
            return .afterHours
        } else {
            return .closed(date: now, time: "4:00 PM", timezone: "EST")
        }
    }

    // MARK: - Number Formatting Helpers

    private func formatLargeNumber(_ value: Double) -> String {
        if value >= 1_000_000_000 {
            return String(format: "%.2fB", value / 1_000_000_000)
        } else if value >= 1_000_000 {
            return String(format: "%.2fM", value / 1_000_000)
        } else if value >= 1_000 {
            return String(format: "%.1fK", value / 1_000)
        }
        return String(format: "%.0f", value)
    }

    private func formatMarketCap(_ value: Double) -> String {
        if value >= 1_000_000_000_000 {
            return String(format: "$%.2fT", value / 1_000_000_000_000)
        } else if value >= 1_000_000_000 {
            return String(format: "$%.2fB", value / 1_000_000_000)
        } else if value >= 1_000_000 {
            return String(format: "$%.2fM", value / 1_000_000)
        }
        return String(format: "$%.0f", value)
    }

    // MARK: - Analysis Tab Handlers

    func handleAnalystRatingsMore() {
        print("Analyst ratings more options for \(tickerSymbol)")
    }

    func handleSentimentMore() {
        print("Sentiment analysis more options for \(tickerSymbol)")
    }

    func handleTechnicalDetail() {
        print("Technical analysis detail for \(tickerSymbol)")
    }

    // MARK: - Financials Tab Handlers

    func handleEarningsDetail() {
        print("Earnings detail for \(tickerSymbol)")
    }

    func handleGrowthDetail() {
        print("Growth detail for \(tickerSymbol)")
    }

    func handleProfitPowerDetail() {
        print("Profit power detail for \(tickerSymbol)")
    }

    func handleSignalOfConfidenceDetail() {
        print("Signal of confidence detail for \(tickerSymbol)")
    }

    func handleRevenueBreakdownDetail() {
        print("Revenue breakdown detail for \(tickerSymbol)")
    }

    func handleHealthCheckDetail() {
        print("Health check detail for \(tickerSymbol)")
    }

    // MARK: - Computed Properties (prefer live API data over sample data)

    var formattedPrice: String {
        // Prefer tickerData (from overview endpoint), then fallback to separate API data
        if let data = tickerData, data.currentPrice > 0 {
            return data.formattedPrice
        }
        if let price = stockQuote?.price ?? stockDetail?.price {
            return String(format: "$%.2f", price)
        }
        return "--"
    }

    var formattedChange: String {
        if let data = tickerData {
            return data.formattedChange
        }
        if let change = stockQuote?.change ?? stockDetail?.change {
            let sign = change >= 0 ? "+" : ""
            return "\(sign)\(String(format: "%.2f", change))"
        }
        return "--"
    }

    var formattedChangePercent: String {
        if let data = tickerData {
            return data.formattedChangePercent
        }
        if let percent = stockQuote?.changePercent {
            let sign = percent >= 0 ? "+" : ""
            return "(\(sign)\(String(format: "%.2f", percent))%)"
        }
        return "--"
    }

    var isPositive: Bool {
        if let data = tickerData {
            return data.isPositive
        }
        if let change = stockQuote?.change ?? stockDetail?.change {
            return change >= 0
        }
        return true
    }

    var chartData: [Double] {
        tickerData?.chartData ?? []
    }

    var chartPricePoints: [StockPricePoint] {
        tickerData?.chartPricePoints ?? []
    }

    var aiSuggestions: [TickerAISuggestion] {
        TickerAISuggestion.defaultSuggestions
    }
}
