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
    @Published var analystRatingsData: AnalystRatingsData?
    @Published var sentimentAnalysisData: SentimentAnalysisData?
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

    // Analysis tab state
    @Published var selectedMomentumPeriod: AnalystMomentumPeriod = .sixMonths
    @Published var selectedSentimentTimeframe: SentimentTimeframe = .last24h
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

    // MARK: - Initialization

    init(indexSymbol: String) {
        self.indexSymbol = indexSymbol

        // Observe chart range changes and reload chart data
        $selectedChartRange
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] newRange in
                guard let self = self else { return }
                self.chartSettings.selectedInterval = newRange.defaultInterval

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

        // Observe interval changes and re-fetch chart data
        chartSettings.$selectedInterval
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] _ in
                guard let self = self else { return }
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
            await self.fetchIndexDetail()
        }
    }

    func refresh() async {
        errorMessage = nil
        await fetchIndexDetail()
    }

    func toggleFavorite() {
        isFavorite.toggle()
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
        print("📰 [IndexDetailVM] Open news article: \(article.headline)")
    }

    func handleNewsExternalLink(_ article: TickerNewsArticle) {
        guard let url = article.articleURL else { return }
        UIApplication.shared.open(url)
    }

    func handleNewsTickerTap(_ ticker: String) {
        print("🔗 [IndexDetailVM] Navigate to ticker: \(ticker)")
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
    }

    // MARK: - Analysis Tab Handlers

    func handleAnalystRatingsMore() {
        print("📊 [IndexDetailVM] Analyst ratings more options for \(indexSymbol)")
    }

    func handleSentimentMore() {
        print("💬 [IndexDetailVM] Sentiment analysis more options for \(indexSymbol)")
    }

    func handleTechnicalDetail() {
        print("📈 [IndexDetailVM] Technical analysis detail for \(indexSymbol)")
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

            // Map DTOs → display models
            self.indexData = response.toDisplayModel()
            self.chartDataVersion += 1

            // News with pagination
            self.allNewsArticles = response.toNewsArticles()
            self.newsDisplayCount = newsPageSize
            self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            self.hasMoreNews = allNewsArticles.count > newsDisplayCount

            // Analysis data is not yet served by the backend — use sample
            self.analystRatingsData = AnalystRatingsData.sampleData
            self.sentimentAnalysisData = SentimentAnalysisData.sampleData
            self.technicalAnalysisData = TechnicalAnalysisData.sampleData

            self.isLoading = false

            // Connect live price after successful data load
            self.connectLivePrice()
            self.startChartRefreshTimer()

            print("✅ [IndexDetailVM] Index detail loaded in \(elapsed)s")
            print("   💰 Price: \(response.currentPrice) | Change: \(response.priceChange) (\(response.priceChangePercent)%)")
            print("   📊 Chart points: \(response.chartData.count)")
            print("   📰 News: \(response.newsArticles.count) articles")
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

            self.errorMessage = "Unable to load index data. Pull to refresh."
            loadFallbackData()
            self.isLoading = false
        }
    }

    /// Reload only the chart data when the user changes time range.
    private func loadChartData(range: ChartTimeRange) async {
        let startTime = CFAbsoluteTimeGetCurrent()
        print("📡 [IndexDetailVM] Reloading chart for \(indexSymbol) range: \(range.rawValue)")

        do {
            let response = try await APIClient.shared.request(
                endpoint: .getIndexDetail(symbol: indexSymbol, range: range.rawValue, interval: chartSettings.selectedInterval.rawValue),
                responseType: IndexDetailResponse.self
            )

            let elapsed = String(format: "%.2f", CFAbsoluteTimeGetCurrent() - startTime)

            // Update all data — the backend returns a fresh snapshot
            self.indexData = response.toDisplayModel()
            self.chartDataVersion += 1
            if !response.newsArticles.isEmpty {
                self.newsArticles = response.toNewsArticles()
            }

            print("✅ [IndexDetailVM] Chart reloaded in \(elapsed)s — \(response.chartData.count) data points")

        } catch {
            print("❌ [IndexDetailVM] Chart reload failed: \(error)")
            // Keep existing data — don't wipe the screen on a chart range failure
        }
    }

    // MARK: - Fallback

    private func loadFallbackData() {
        if indexData == nil {
            indexData = IndexDetailData.sampleSP500
            print("🔄 [IndexDetailVM] Using fallback sample data for index")
        }
        if newsArticles.isEmpty {
            newsArticles = TickerNewsArticle.sampleDataForTicker(indexSymbol)
            print("🔄 [IndexDetailVM] Using fallback sample news")
        }
        if analystRatingsData == nil {
            analystRatingsData = AnalystRatingsData.sampleData
            sentimentAnalysisData = SentimentAnalysisData.sampleData
            technicalAnalysisData = TechnicalAnalysisData.sampleData
            print("🔄 [IndexDetailVM] Using fallback sample analysis")
        }
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

        // Sector performance
        let topSectors = snap.sectorPerformance.sectors.prefix(5)
            .map { "\($0.sector): \($0.formattedChange)" }
            .joined(separator: ", ")
        parts.append("TOP SECTORS: \(topSectors)")

        // Performance
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
        var parts: [String] = []
        if let ratings = analystRatingsData {
            parts.append("ANALYST CONSENSUS: \(ratings.consensus.rawValue), Target: \(ratings.formattedTargetPrice) (\(ratings.formattedUpside))")
        }
        if let sentiment = sentimentAnalysisData {
            parts.append("SENTIMENT: Mood=\(sentiment.moodScore)/100 (\(sentiment.last24hMood.rawValue))")
        }
        if let tech = technicalAnalysisData {
            parts.append("TECHNICAL: Signal=\(tech.overallSignal.rawValue)")
        }
        return parts.isEmpty ? nil : parts.joined(separator: "\n")
    }
}
