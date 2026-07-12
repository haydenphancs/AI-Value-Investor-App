//
//  IndexDetailViewModel.swift
//  ios
//
//  ViewModel for the Index Detail screen
//
//  Fetches aggregated index data from GET /api/v1/indices/{symbol}.
//  Falls back to local sample data when the backend is unreachable.
//

import Foundation
import SwiftUI
import Combine

@MainActor
class IndexDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var indexData: IndexDetailData?
    @Published var newsArticles: [TickerNewsArticle] = []
    @Published var technicalAnalysisData: TechnicalAnalysisData?
    @Published var technicalAnalysisDetailData: TechnicalAnalysisDetailData?
    @Published var isTechnicalDetailLoading: Bool = false
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: IndexDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .threeMonths
    @Published var isFavorite: Bool = false
    @Published var aiInputText: String = ""
    @Published var pendingAIQuery: String?
    @Published var pendingTickerNavigation: String?

    // Analysis tab state
    @Published var isTechnicalLoaded: Bool = false
    @Published var chartSettings = ChartSettings()
    @Published var chartDataVersion: Int = 0
    @Published var chartEventDates: ChartEventDates?

    // Live Price
    let livePriceManager = LivePriceWebSocketManager()

    // News pagination
    @Published var isNewsLoading: Bool = false
    @Published var hasMoreNews: Bool = false
    private var allNewsArticles: [TickerNewsArticle] = []
    private var newsDisplayCount: Int = 10
    private let newsPageSize: Int = 10

    // MARK: - Private Properties

    private let indexSymbol: String
    private var cancellables = Set<AnyCancellable>()
    private var chartRefreshTask: Task<Void, Never>?
    /// Monotonic token for index-detail fetches. Both fetchIndexDetail and
    /// loadChartData hit the same endpoint and write the whole indexData snapshot;
    /// each captures the token before awaiting and only applies its result if still
    /// current, so a slow earlier response can't clobber a newer range's chart.
    private var chartRequestToken = 0
    /// True while the range sink is assigning the range's default interval, so the
    /// interval sink doesn't ALSO reload (a single range change would otherwise fire
    /// two identical fetches when the range crosses an interval boundary).
    private var suppressIntervalReload = false

    // MARK: - Initialization

    init(indexSymbol: String) {
        self.indexSymbol = indexSymbol

        // Observe chart range changes and reload chart data
        $selectedChartRange
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] newRange in
                guard let self = self else { return }
                // Setting the interval fires the interval sink SYNCHRONOUSLY; suppress
                // its reload so the range change drives exactly one fetch (not two when
                // the new range crosses an interval boundary, e.g. 3M→5Y daily→weekly).
                self.suppressIntervalReload = true
                self.chartSettings.selectedInterval = newRange.defaultInterval
                self.suppressIntervalReload = false

                if newRange.defaultInterval.isIntraday && self.livePriceManager.isConnected {
                    self.startChartRefreshTimer()
                } else {
                    self.stopChartRefreshTimer()
                }

                Task {
                    await self.loadChartData(range: newRange)
                }
            }
            .store(in: &cancellables)

        // Observe interval changes and re-fetch chart data (manual interval picker)
        chartSettings.$selectedInterval
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] _ in
                guard let self = self else { return }
                // Skip the reload the range sink already owns (see suppress flag).
                guard !self.suppressIntervalReload else { return }
                Task {
                    await self.loadChartData(range: self.selectedChartRange)
                }
            }
            .store(in: &cancellables)

        // Observe live price updates → update indexData in real-time
        livePriceManager.$livePrice
            .compactMap { $0 }
            .sink { [weak self] newPrice in
                guard let self = self, var data = self.indexData else { return }
                data.currentPrice = newPrice
                data.priceChange = self.livePriceManager.livePriceChange ?? data.priceChange
                data.priceChangePercent = self.livePriceManager.livePriceChangePercent ?? data.priceChangePercent

                // Update last chart candle for intraday ranges
                if self.chartSettings.selectedInterval.isIntraday,
                   !data.chartPricePoints.isEmpty {
                    let lastIndex = data.chartPricePoints.count - 1
                    let last = data.chartPricePoints[lastIndex]
                    let updatedPoint = StockPricePoint(
                        date: last.date,
                        close: newPrice,
                        open: last.open,
                        high: max(last.high ?? newPrice, newPrice),
                        low: min(last.low ?? newPrice, newPrice),
                        volume: last.volume
                    )
                    data.chartPricePoints[lastIndex] = updatedPoint
                }

                self.indexData = data
            }
            .store(in: &cancellables)
    }

    // MARK: - Public Methods

    func loadIndexData() {
        isLoading = true
        errorMessage = nil

        Task { [weak self] in
            guard let self = self else { return }
            async let fetchTask: () = self.fetchIndexDetail()
            async let newsTask: () = self.fetchIndexNews()
            async let watchlistTask: () = self.checkWatchlistStatus()
            async let technicalTask: () = self.fetchTechnicalAnalysis()
            _ = await (fetchTask, newsTask, watchlistTask, technicalTask)
        }
    }

    func refresh() async {
        errorMessage = nil
        await fetchIndexDetail()
    }

    func toggleFavorite() {
        let wasInWatchlist = isFavorite
        isFavorite.toggle() // optimistic UI update

        Task { @MainActor in
            do {
                if wasInWatchlist {
                    try await APIClient.shared.request(
                        endpoint: .removeFromWatchlist(stockId: indexSymbol)
                    )
                    print("✅ [IndexDetailVM] Removed \(indexSymbol) from watchlist")
                } else {
                    try await APIClient.shared.request(
                        endpoint: .addToWatchlist(stockId: indexSymbol)
                    )
                    print("✅ [IndexDetailVM] Added \(indexSymbol) to watchlist")
                }
            } catch {
                print("⚠️ [IndexDetailVM] Watchlist toggle failed for \(indexSymbol): \(error)")
                isFavorite = wasInWatchlist // revert on failure
            }
        }
    }

    private func checkWatchlistStatus() async {
        do {
            let watchlist: [WatchlistItemDTO] = try await APIClient.shared.request(
                endpoint: .getWatchlist,
                responseType: [WatchlistItemDTO].self
            )
            self.isFavorite = watchlist.contains { $0.ticker.uppercased() == indexSymbol.uppercased() }
        } catch {
            print("⚠️ [IndexDetailVM] Watchlist check failed: \(error)")
        }
    }

    private struct WatchlistItemDTO: Codable {
        let ticker: String
    }

    func handleNotificationTap() {
        print("🔔 [IndexDetailVM] Notification settings for \(indexSymbol)")
    }

    func handleWebsiteTap() {
        guard let website = indexData?.indexProfile.website,
              let url = URL(string: "https://\(website)") else { return }

        UIApplication.shared.open(url)
    }

    func handleNewsArticleTap(_ article: TickerNewsArticle) {
        guard let url = article.articleURL else { return }
        UIApplication.shared.open(url)
    }

    func handleNewsExternalLink(_ article: TickerNewsArticle) {
        guard let url = article.articleURL else { return }
        UIApplication.shared.open(url)
    }

    func handleNewsTickerTap(_ ticker: String) {
        pendingTickerNavigation = ticker
    }

    func handleSuggestionTap(_ suggestion: IndexAISuggestion) {
        aiInputText = suggestion.text
        handleAISend()
    }

    func handleAISend() {
        guard !aiInputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let query = aiInputText
        aiInputText = ""
        print("🤖 [IndexDetailVM] AI Query for \(indexSymbol): \(query)")
        pendingAIQuery = query
    }

    func updateChartRange(_ range: ChartTimeRange) {
        selectedChartRange = range
    }

    // MARK: - Live Price

    func connectLivePrice() {
        guard let token = KeychainService.shared.get("access_token") else { return }
        livePriceManager.connect(ticker: indexSymbol, authToken: token)
    }

    func disconnectLivePrice() {
        livePriceManager.disconnect()
        stopChartRefreshTimer()
    }

    // MARK: - Chart Refresh Timer

    private func startChartRefreshTimer() {
        chartRefreshTask?.cancel()
        chartRefreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30_000_000_000) // 30 seconds
                guard !Task.isCancelled else { break }
                guard let self = self else { break }

                guard self.chartSettings.selectedInterval.isIntraday,
                      MarketHoursUtil.isMarketActive() else { continue }

                await self.loadChartData(range: self.selectedChartRange)
            }
        }
    }

    private func stopChartRefreshTimer() {
        chartRefreshTask?.cancel()
        chartRefreshTask = nil
    }

    // MARK: - News Pagination

    func loadMoreNews() {
        guard !isNewsLoading, hasMoreNews else { return }
        newsDisplayCount += newsPageSize
        newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
        hasMoreNews = newsDisplayCount < allNewsArticles.count

        // Enrich newly visible articles in the background
        Task {
            await enrichVisibleArticles()
        }
    }

    // MARK: - Technical Analysis

    private func fetchTechnicalAnalysis() async {
        do {
            let dto = try await APIClient.shared.request(
                endpoint: .getTechnicalAnalysis(ticker: indexSymbol),
                responseType: TechnicalAnalysisDTO.self
            )
            self.technicalAnalysisData = dto.toDisplayModel()
            self.isTechnicalLoaded = true
            print("✅ [IndexDetailVM] Got technical analysis for \(indexSymbol) — gauge: \(dto.gaugeValue)")
        } catch {
            print("⚠️ [IndexDetailVM] Technical analysis failed: \(error)")
            self.technicalAnalysisData = TechnicalAnalysisData.sampleData
            self.isTechnicalLoaded = true
        }
    }

    func fetchTechnicalAnalysisDetail() {
        guard technicalAnalysisDetailData == nil, !isTechnicalDetailLoading else { return }
        isTechnicalDetailLoading = true

        Task { [weak self] in
            guard let self = self else { return }
            do {
                let dto = try await APIClient.shared.request(
                    endpoint: .getTechnicalAnalysisDetail(ticker: self.indexSymbol),
                    responseType: TechnicalAnalysisDetailDTO.self
                )
                self.technicalAnalysisDetailData = dto.toDisplayModel()
                print("✅ [IndexDetailVM] Got technical analysis detail for \(self.indexSymbol)")
            } catch {
                print("⚠️ [IndexDetailVM] Technical analysis detail failed: \(error)")
                self.technicalAnalysisDetailData = TechnicalAnalysisDetailData.sampleData
            }
            self.isTechnicalDetailLoading = false
        }
    }

    // MARK: - Computed Properties

    var formattedPrice: String {
        indexData?.formattedPrice ?? "--"
    }

    var formattedChange: String {
        indexData?.formattedChange ?? "--"
    }

    var formattedChangePercent: String {
        indexData?.formattedChangePercent ?? "--"
    }

    var isPositive: Bool {
        indexData?.isPositive ?? true
    }

    var chartData: [Double] {
        indexData?.chartData ?? []
    }

    var chartPricePoints: [StockPricePoint] {
        indexData?.chartPricePoints ?? []
    }

    var aiSuggestions: [IndexAISuggestion] {
        IndexAISuggestion.defaultSuggestions
    }

    // MARK: - Network

    private func fetchIndexDetail() async {
        let startTime = CFAbsoluteTimeGetCurrent()
        let range = selectedChartRange.rawValue
        chartRequestToken += 1
        let token = chartRequestToken
        let endpoint = APIEndpoint.getIndexDetail(symbol: indexSymbol, range: range, interval: chartSettings.selectedInterval.rawValue)

        print("📡 [IndexDetailVM] Fetching index detail for \(indexSymbol) (range: \(range)) from \(APIConfig.baseURL.absoluteString)\(endpoint.path) ...")

        do {
            let response = try await APIClient.shared.request(
                endpoint: endpoint,
                responseType: IndexDetailResponse.self
            )
            let elapsed = String(format: "%.2f", CFAbsoluteTimeGetCurrent() - startTime)

            // Clear previous error on success
            self.errorMessage = nil

            // Map DTOs → display models — but only if this is still the latest
            // request; a newer range change may have already painted fresher data,
            // and this (slower, earlier) response must not clobber it.
            if token == self.chartRequestToken {
                self.indexData = response.toDisplayModel()
                self.chartDataVersion += 1
            }

            // News and technical analysis are fetched via separate concurrent tasks

            self.isLoading = false

            // Connect live price after successful data load
            self.connectLivePrice()
            self.startChartRefreshTimer()

            print("✅ [IndexDetailVM] Index detail loaded in \(elapsed)s")
            print("   💰 Price: \(response.currentPrice) | Change: \(response.priceChange) (\(response.priceChangePercent)%)")
            print("   📊 Chart points: \(response.chartData.count)")
            print("   🏢 Profile: \(response.indexName) (\(response.indexProfile.numberOfConstituents) constituents)")
            if let snap = indexData?.snapshotsData {
                print("   📈 Valuation: P/E \(snap.valuation.peRatio)x | Level: \(snap.valuation.level.rawValue)")
                print("   🌍 Sectors: \(snap.sectorPerformance.sectors.count) sectors loaded")
                print("   🏛️ Macro: \(snap.macroForecast.indicators.count) indicators")
            }

        } catch {
            let elapsed = String(format: "%.2f", CFAbsoluteTimeGetCurrent() - startTime)
            print("❌ [IndexDetailVM] Fetch failed after \(elapsed)s: \(error)")
            if let apiError = error as? APIError {
                print("   🔍 API Error detail: \(apiError)")
            }

            // Don't let a STALE request's failure clobber data a newer range fetch
            // already painted (or is about to) — only surface the error/fallback if
            // this is still the latest request.
            if token == self.chartRequestToken {
                self.errorMessage = "Unable to load index data. Pull to refresh."
                loadFallbackData()
            }
            self.isLoading = false
        }
    }

    /// Reload only the chart data when the user changes time range.
    private func loadChartData(range: ChartTimeRange) async {
        let startTime = CFAbsoluteTimeGetCurrent()
        print("📡 [IndexDetailVM] Reloading chart for \(indexSymbol) range: \(range.rawValue)")
        chartRequestToken += 1
        let token = chartRequestToken

        do {
            let response = try await APIClient.shared.request(
                endpoint: .getIndexDetail(symbol: indexSymbol, range: range.rawValue, interval: chartSettings.selectedInterval.rawValue),
                responseType: IndexDetailResponse.self
            )

            let elapsed = String(format: "%.2f", CFAbsoluteTimeGetCurrent() - startTime)

            // Drop a stale response so a slow earlier range can't overwrite the chart
            // the user has since switched to (last-write-wins).
            guard token == self.chartRequestToken else { return }
            // Update all data — the backend returns a fresh snapshot
            self.indexData = response.toDisplayModel()
            self.chartDataVersion += 1

            print("✅ [IndexDetailVM] Chart reloaded in \(elapsed)s — \(response.chartData.count) data points")

        } catch {
            print("❌ [IndexDetailVM] Chart reload failed: \(error)")
            // Keep existing data — don't wipe the screen on a chart range failure
        }
    }

    // MARK: - News Fetching & Enrichment

    private func fetchIndexNews() async {
        self.isNewsLoading = true
        print("📡 [IndexDetailVM] fetchIndexNews() CALLED for \(indexSymbol) — requesting GET /indices/\(indexSymbol)/news")
        do {
            let response = try await APIClient.shared.request(
                endpoint: .getIndexNews(symbol: indexSymbol, limit: 50),
                responseType: TickerNewsFeedResponse.self
            )
            let cached = response.cached ?? false
            print("✅ [IndexDetailVM] Got \(response.articles.count) news articles for \(indexSymbol) (cached: \(cached))")

            // Convert API articles to UI models
            self.allNewsArticles = response.articles.map { mapApiToUiArticle($0) }
            self.newsDisplayCount = newsPageSize
            self.hasMoreNews = allNewsArticles.count > newsDisplayCount

            // Show articles immediately with raw data
            self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            self.isNewsLoading = false

            // Enrich ALL articles in background (not just visible ones)
            let unenrichedIds = self.allNewsArticles
                .filter { !$0.aiProcessed }
                .map { $0.apiId }
                .filter { !$0.isEmpty && !$0.hasPrefix("temp_") && !$0.hasPrefix("raw_") }

            if !unenrichedIds.isEmpty {
                await attemptEnrichment(articleIds: unenrichedIds)
                self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            }
        } catch {
            print("❌ [IndexDetailVM] Failed to fetch news for \(indexSymbol): \(error)")
            if let apiError = error as? APIError {
                print("   🔍 API Error: \(apiError)")
            }
        }
        self.isNewsLoading = false
    }

    private func attemptEnrichment(articleIds: [String], maxAttempts: Int = 2) async {
        for attempt in 1...maxAttempts {
            do {
                let enrichResponse = try await APIClient.shared.request(
                    endpoint: .enrichIndexNews(symbol: indexSymbol, articleIds: articleIds),
                    responseType: EnrichStockNewsResponse.self
                )
                mergeEnrichment(enrichResponse.articles)

                let enrichedCount = allNewsArticles.prefix(newsDisplayCount)
                    .filter { $0.aiProcessed }.count
                if enrichedCount > 0 {
                    print("✅ [IndexDetailVM] Attempt \(attempt) enriched \(enrichedCount) articles")
                    return
                } else if attempt < maxAttempts {
                    print("⚠️ [IndexDetailVM] Attempt \(attempt) returned 0 enriched, retrying in 3s...")
                    try await Task.sleep(nanoseconds: 3_000_000_000)
                } else {
                    print("⚠️ [IndexDetailVM] Enrichment returned 0 enriched after \(maxAttempts) attempts")
                }
            } catch {
                if attempt < maxAttempts {
                    print("⚠️ [IndexDetailVM] Enrichment attempt \(attempt) failed: \(error), retrying...")
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                } else {
                    print("⚠️ [IndexDetailVM] Enrichment failed after \(maxAttempts) attempts: \(error)")
                }
            }
        }
    }

    private func enrichVisibleArticles() async {
        let unenriched = newsArticles.filter { !$0.aiProcessed }
        guard !unenriched.isEmpty else { return }

        let ids = unenriched.map { $0.apiId }
            .filter { !$0.isEmpty && !$0.hasPrefix("temp_") && !$0.hasPrefix("raw_") }
        guard !ids.isEmpty else { return }

        await attemptEnrichment(articleIds: ids)
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
            }
        }
        print("📰 [IndexDetailVM] Merged \(actuallyEnriched)/\(enrichedArticles.count) enriched articles")
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
        let isoFormatter = ISO8601DateFormatter()
        isoFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = isoFormatter.date(from: dateString) { return date }

        isoFormatter.formatOptions = [.withInternetDateTime]
        if let date = isoFormatter.date(from: dateString) { return date }

        let fallback = DateFormatter()
        fallback.dateFormat = "yyyy-MM-dd HH:mm:ss"
        fallback.locale = Locale(identifier: "en_US_POSIX")
        return fallback.date(from: dateString)
    }

    // MARK: - Fallback

    private func loadFallbackData() {
        if indexData == nil {
            indexData = IndexDetailData.sampleSP500
            print("🔄 [IndexDetailVM] Using fallback sample data for index")
        }
        // Technical analysis is fetched separately — fallback handled in fetchTechnicalAnalysis()
    }

    // MARK: - AI Context Builders

    /// Contextual information injected into "Ask Cay AI" chat sessions.
    var contextForCurrentTab: String? {
        var sections: [String] = []

        if let base = baseIndexContext {
            sections.append(base)
        }

        switch selectedTab {
        case .overview:
            if let ctx = overviewContext { sections.append(ctx) }
        case .news:
            if let ctx = newsContext { sections.append(ctx) }
        case .analysis:
            if let ctx = analysisContext { sections.append(ctx) }
        }

        sections.append("User is viewing the \(selectedTab.rawValue) tab of the index detail screen.")

        return sections.isEmpty ? nil : sections.joined(separator: "\n\n")
    }

    private var baseIndexContext: String? {
        guard let data = indexData else { return nil }
        return """
        INDEX CONTEXT:
        Symbol: \(data.symbol)
        Name: \(data.indexName)
        Current Price: \(data.formattedPrice)
        Change: \(data.formattedChange) \(data.formattedChangePercent)
        Constituents: \(data.indexProfile.numberOfConstituents)
        Weighting: \(data.indexProfile.weightingMethodology)
        Provider: \(data.indexProfile.indexProvider)
        """
    }

    private var overviewContext: String? {
        guard let data = indexData else { return nil }
        let snap = data.snapshotsData

        var parts: [String] = []

        // Key stats summary
        let allStats = data.keyStatisticsGroups.flatMap { $0.statistics }
        let statsText = allStats.map { "\($0.label): \($0.value)" }.joined(separator: ", ")
        parts.append("KEY STATISTICS: \(statsText)")

        // Valuation
        let val = snap.valuation
        parts.append(
            "VALUATION: P/E(TTM)=\(String(format: "%.1f", val.peRatio))x, "
            + "Forward P/E=\(String(format: "%.1f", val.forwardPE))x, "
            + "Earnings Yield=\(String(format: "%.2f", val.earningsYield))%, "
            + "Level=\(val.level.rawValue), "
            + "Historical Avg P/E (\(val.historicalPeriod))=\(String(format: "%.0f", val.historicalAvgPE))x"
        )

        // ALL sector performance (not just top 5)
        let allSectors = snap.sectorPerformance.sectors
            .map { "\($0.sector): \($0.formattedChange)" }
            .joined(separator: ", ")
        parts.append("SECTOR PERFORMANCE (\(snap.sectorPerformance.advancingSectors) advancing, \(snap.sectorPerformance.decliningSectors) declining): \(allSectors)")

        // Macro forecast indicators
        let macroText = snap.macroForecast.indicators
            .map { "\($0.title) [\($0.signal.rawValue)]" }
            .joined(separator: ", ")
        parts.append("MACRO FORECAST: \(macroText)")

        // Performance periods
        let perfText = data.performancePeriods
            .map { "\($0.label): \(String(format: "%+.2f", $0.changePercent))%" }
            .joined(separator: ", ")
        parts.append("PERFORMANCE: \(perfText)")

        return parts.joined(separator: "\n")
    }

    private var newsContext: String? {
        guard !newsArticles.isEmpty else { return nil }
        let headlines = newsArticles.prefix(5)
            .map { "- \($0.headline) [\($0.sentiment.rawValue)]" }
            .joined(separator: "\n")
        return "RECENT NEWS:\n\(headlines)"
    }

    private var analysisContext: String? {
        guard let tech = technicalAnalysisData else { return nil }
        return "TECHNICAL: Signal=\(tech.overallSignal.rawValue), Gauge=\(tech.gaugeValue)"
    }
}
