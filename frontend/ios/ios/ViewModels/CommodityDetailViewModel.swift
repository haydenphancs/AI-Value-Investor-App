//
//  CommodityDetailViewModel.swift
//  ios
//
//  ViewModel for the Commodity Detail screen.
//  Fetches real data from:
//    GET  /api/v1/commodities/{symbol}?range=3M&interval=daily
//    GET  /api/v1/commodities/{symbol}/news?limit=50
//    POST /api/v1/commodities/{symbol}/news/enrich
//    GET  /api/v1/stocks/{fmpSymbol}/technical-analysis
//    GET  /api/v1/stocks/{fmpSymbol}/technical-analysis/detail
//

import Foundation
import SwiftUI
import Combine

@MainActor
class CommodityDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var commodityData: CommodityDetailData?
    @Published var newsArticles: [TickerNewsArticle] = []
    @Published var technicalAnalysisData: TechnicalAnalysisData?
    @Published var technicalAnalysisDetailData: TechnicalAnalysisDetailData?
    @Published var isTechnicalDetailLoading: Bool = false
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: CommodityDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .threeMonths
    @Published var isFavorite: Bool = false
    @Published var aiInputText: String = ""
    @Published var pendingAIQuery: String?
    @Published var pendingTickerNavigation: String?

    /// External link to show in the in-app browser. Set via `openExternal(_:into:)`
    /// and presented by the Screen's `.inAppBrowser(link:)` — a ViewModel cannot
    /// present a view itself.
    @Published var browserLink: BrowserLink?
    @Published var chartSettings = ChartSettings()
    @Published var chartDataVersion: Int = 0

    // Analysis tab state
    @Published var isTechnicalLoaded: Bool = false

    // News state
    @Published var isNewsLoading: Bool = false
    @Published var hasMoreNews: Bool = false
    private var allNewsArticles: [TickerNewsArticle] = []
    private var newsDisplayCount: Int = 10
    private let newsPageSize: Int = 10

    // MARK: - Private Properties

    let commoditySymbol: String
    private let apiClient = APIClient.shared
    private var cancellables = Set<AnyCancellable>()
    private var chartRefreshTask: Task<Void, Never>?
    /// Monotonic token for detail/chart fetches. Each captures the value before
    /// awaiting and only applies its result if still current — so a slow
    /// out-of-order response can't clobber a newer one during rapid range switching.
    private var detailRequestGen = 0
    /// True while the range sink assigns the range's default interval, so the
    /// interval sink doesn't ALSO reload (one range change would otherwise fire two
    /// identical fetches when the range crosses an interval boundary).
    private var suppressIntervalReload = false

    // MARK: - Initialization

    init(commoditySymbol: String) {
        self.commoditySymbol = commoditySymbol

        $selectedChartRange
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] range in
                guard let self = self else { return }
                // Assigning the interval fires the interval sink SYNCHRONOUSLY;
                // suppress its reload so a range change drives exactly one fetch.
                self.suppressIntervalReload = true
                self.chartSettings.selectedInterval = range.defaultInterval
                self.suppressIntervalReload = false
                Task { await self.fetchChartForRange() }
            }
            .store(in: &cancellables)

        chartSettings.$selectedInterval
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] _ in
                guard let self = self else { return }
                guard !self.suppressIntervalReload else { return }
                Task { await self.fetchChartForRange() }
            }
            .store(in: &cancellables)
    }

    // MARK: - Data Loading

    func loadCommodityData() {
        isLoading = true
        errorMessage = nil

        Task { [weak self] in
            guard let self = self else { return }
            async let detailTask: () = self.fetchCommodityDetail()
            async let newsTask: () = self.fetchCommodityNews()
            async let technicalTask: () = self.fetchTechnicalAnalysis()
            async let watchlistTask: () = self.checkWatchlistStatus()
            _ = await (detailTask, newsTask, technicalTask, watchlistTask)
        }
    }

    func refresh() async {
        errorMessage = nil
        await fetchCommodityDetail()
        await fetchCommodityNews()
    }

    // MARK: - Detail Fetch

    private func fetchCommodityDetail() async {
        let range = selectedChartRange
        let startTime = CFAbsoluteTimeGetCurrent()
        detailRequestGen += 1
        let gen = detailRequestGen

        do {
            let response = try await apiClient.request(
                endpoint: .getCommodityDetail(
                    symbol: commoditySymbol,
                    range: range.rawValue,
                    interval: chartSettings.selectedInterval.rawValue
                ),
                responseType: CommodityDetailResponseDTO.self
            )

            // Drop a stale response (a newer load/refresh/range-change superseded it).
            guard gen == self.detailRequestGen else { return }
            let elapsed = String(format: "%.1f", CFAbsoluteTimeGetCurrent() - startTime)
            self.commodityData = response.toDisplayModel()
            self.chartDataVersion += 1
            self.isLoading = false
            self.errorMessage = nil

            print("✅ [CommodityDetailVM] Loaded \(response.name) in \(elapsed)s — \(response.chartData.count) chart points")

        } catch {
            print("❌ [CommodityDetailVM] Failed to load \(commoditySymbol): \(error)")
            // Drop a STALE failure: if a newer range fetch/refresh already superseded
            // this request (and may have painted correct data), don't stamp a false
            // error banner over it. Mirrors the success-path gen guard above.
            guard gen == self.detailRequestGen else { return }
            self.isLoading = false

            if let apiError = error as? APIError {
                switch apiError {
                case .networkError:
                    self.errorMessage = "Unable to connect. Check your internet connection."
                case .serverError(let code):
                    self.errorMessage = "Server error (\(code)). Please try again."
                case .notFound:
                    self.errorMessage = "Commodity data not found for \(commoditySymbol)."
                case .rateLimited:
                    self.errorMessage = "High demand right now — please try again in a moment."
                case .businessError(_, let message):
                    // Backend typed error (e.g. FMP_RATE_LIMITED) — surface its
                    // actionable user_message instead of a generic string.
                    self.errorMessage = message
                default:
                    self.errorMessage = "Something went wrong. Please try again."
                }
            } else {
                self.errorMessage = "Unexpected error. Please try again."
            }

            // Do NOT substitute Gold's data (previously sampleGold) for a
            // different commodity — that's financial misinformation. Keep real
            // data if a prior load succeeded; otherwise stay in the honest
            // skeleton/empty state. errorMessage drives any banner; the header
            // shows the real symbol and pull-to-refresh retries.
        }
    }

    // MARK: - Chart Range Change

    private func fetchChartForRange() async {
        let range = selectedChartRange
        detailRequestGen += 1
        let gen = detailRequestGen
        do {
            let response = try await apiClient.request(
                endpoint: .getCommodityDetail(
                    symbol: commoditySymbol,
                    range: range.rawValue,
                    interval: chartSettings.selectedInterval.rawValue
                ),
                responseType: CommodityDetailResponseDTO.self
            )
            // Drop a stale range response so rapid switching can't clobber a newer range.
            guard gen == self.detailRequestGen else { return }
            self.commodityData = response.toDisplayModel()
            self.chartDataVersion += 1
            print("✅ [CommodityDetailVM] Chart updated — \(response.chartData.count) data points")
        } catch {
            print("⚠️ [CommodityDetailVM] Chart update failed: \(error)")
        }
    }

    // MARK: - News Fetching & Enrichment

    private func fetchCommodityNews() async {
        self.isNewsLoading = true
        print("📡 [CommodityDetailVM] Fetching news for \(commoditySymbol)")

        do {
            let response = try await apiClient.request(
                endpoint: .getCommodityNews(symbol: commoditySymbol, limit: 50),
                responseType: TickerNewsFeedResponse.self
            )
            print("✅ [CommodityDetailVM] Got \(response.articles.count) news articles (cached: \(response.cached ?? false))")

            self.allNewsArticles = response.articles.map { mapApiToUiArticle($0) }
            self.newsDisplayCount = newsPageSize
            self.hasMoreNews = allNewsArticles.count > newsDisplayCount
            self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            self.isNewsLoading = false

            // Enrich unenriched articles in background
            let unenrichedIds = self.allNewsArticles
                .filter { !$0.aiProcessed }
                .map { $0.apiId }
                .filter { !$0.isEmpty && !$0.hasPrefix("temp_") && !$0.hasPrefix("raw_") }

            if !unenrichedIds.isEmpty {
                await attemptEnrichment(articleIds: unenrichedIds)
                self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            }
        } catch {
            print("❌ [CommodityDetailVM] Failed to fetch news: \(error)")
        }
        self.isNewsLoading = false
    }

    private func attemptEnrichment(articleIds: [String], maxAttempts: Int = 2) async {
        for attempt in 1...maxAttempts {
            do {
                let enrichResponse = try await apiClient.request(
                    endpoint: .enrichCommodityNews(symbol: commoditySymbol, articleIds: articleIds),
                    responseType: EnrichStockNewsResponse.self
                )
                mergeEnrichment(enrichResponse.articles)

                let enrichedCount = allNewsArticles.prefix(newsDisplayCount)
                    .filter { $0.aiProcessed }.count
                if enrichedCount > 0 {
                    print("✅ [CommodityDetailVM] Attempt \(attempt) enriched \(enrichedCount) articles")
                    return
                } else if attempt < maxAttempts {
                    print("⚠️ [CommodityDetailVM] Attempt \(attempt) returned 0 enriched, retrying in 3s...")
                    try await Task.sleep(nanoseconds: 3_000_000_000)
                } else {
                    print("⚠️ [CommodityDetailVM] Enrichment returned 0 enriched after \(maxAttempts) attempts")
                }
            } catch {
                if attempt < maxAttempts {
                    print("⚠️ [CommodityDetailVM] Enrichment attempt \(attempt) failed: \(error), retrying...")
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                } else {
                    print("⚠️ [CommodityDetailVM] Enrichment failed after \(maxAttempts) attempts: \(error)")
                }
            }
        }
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
        print("📰 [CommodityDetailVM] Merged \(actuallyEnriched)/\(enrichedArticles.count) enriched articles")
    }

    func loadMoreNews() {
        newsDisplayCount += newsPageSize
        newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
        hasMoreNews = newsDisplayCount < allNewsArticles.count

        // Enrich newly visible articles
        Task {
            let unenriched = newsArticles.filter { !$0.aiProcessed }
            guard !unenriched.isEmpty else { return }
            let ids = unenriched.map { $0.apiId }
                .filter { !$0.isEmpty && !$0.hasPrefix("temp_") && !$0.hasPrefix("raw_") }
            guard !ids.isEmpty else { return }
            await attemptEnrichment(articleIds: ids)
            newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
        }
    }

    // MARK: - Technical Analysis

    private func fetchTechnicalAnalysis() async {
        do {
            let dto = try await apiClient.request(
                endpoint: .getTechnicalAnalysis(ticker: commoditySymbol),
                responseType: TechnicalAnalysisDTO.self
            )
            self.technicalAnalysisData = dto.toDisplayModel()
            self.isTechnicalLoaded = true
            print("✅ [CommodityDetailVM] Got technical analysis — gauge: \(dto.gaugeValue)")
        } catch {
            print("⚠️ [CommodityDetailVM] Technical analysis failed: \(error)")
            // Do NOT fabricate a BUY gauge from sampleData — a hardcoded "Buy" signal
            // on a failed fetch is financial misinformation and leaks into the AI
            // context (contextForCurrentTab). Leave nil; the section stays empty.
            self.technicalAnalysisData = nil
            self.isTechnicalLoaded = true
        }
    }

    func fetchTechnicalAnalysisDetail() {
        guard technicalAnalysisDetailData == nil, !isTechnicalDetailLoading else { return }
        isTechnicalDetailLoading = true

        Task { [weak self] in
            guard let self = self else { return }
            do {
                let dto = try await self.apiClient.request(
                    endpoint: .getTechnicalAnalysisDetail(ticker: self.commoditySymbol),
                    responseType: TechnicalAnalysisDetailDTO.self
                )
                self.technicalAnalysisDetailData = dto.toDisplayModel()
                print("✅ [CommodityDetailVM] Got technical analysis detail for \(self.commoditySymbol)")
            } catch {
                print("⚠️ [CommodityDetailVM] Technical analysis detail failed: \(error)")
                // Do NOT fabricate Apple's pivots/levels for this commodity (misinformation).
                self.technicalAnalysisDetailData = nil
            }
            self.isTechnicalDetailLoading = false
        }
    }

    // MARK: - Watchlist

    func toggleFavorite() {
        let wasInWatchlist = isFavorite
        isFavorite.toggle()

        Task { @MainActor in
            do {
                if wasInWatchlist {
                    try await apiClient.request(
                        endpoint: .removeFromWatchlist(stockId: commoditySymbol)
                    )
                    print("✅ [CommodityDetailVM] Removed \(commoditySymbol) from watchlist")
                } else {
                    try await apiClient.request(
                        endpoint: .addToWatchlist(stockId: commoditySymbol)
                    )
                    print("✅ [CommodityDetailVM] Added \(commoditySymbol) to watchlist")
                }
            } catch {
                print("⚠️ [CommodityDetailVM] Watchlist toggle failed: \(error)")
                isFavorite = wasInWatchlist
            }
        }
    }

    private func checkWatchlistStatus() async {
        do {
            let watchlist: [WatchlistItemDTO] = try await apiClient.request(
                endpoint: .getWatchlist,
                responseType: [WatchlistItemDTO].self
            )
            self.isFavorite = watchlist.contains { $0.ticker.uppercased() == commoditySymbol.uppercased() }
        } catch {
            print("⚠️ [CommodityDetailVM] Watchlist check failed: \(error)")
        }
    }

    private struct WatchlistItemDTO: Codable {
        let ticker: String
    }

    // MARK: - User Actions

    func handleNotificationTap() {
        print("Notification settings for \(commoditySymbol)")
    }

    func handleRelatedCommodityTap(_ commodity: RelatedTicker) {
        pendingTickerNavigation = commodity.symbol
    }

    func handleNewsArticleTap(_ article: TickerNewsArticle) {
        guard let url = article.articleURL else { return }
        openExternal(url, into: &browserLink)
    }

    func handleNewsExternalLink(_ article: TickerNewsArticle) {
        guard let url = article.articleURL else { return }
        openExternal(url, into: &browserLink)
    }

    func handleNewsTickerTap(_ ticker: String) {
        pendingTickerNavigation = ticker
    }

    func handleSuggestionTap(_ suggestion: CommodityAISuggestion) {
        aiInputText = suggestion.text
    }

    func handleAISend() {
        guard !aiInputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        pendingAIQuery = aiInputText
        aiInputText = ""
    }

    // MARK: - AI Context

    var contextForCurrentTab: String? {
        guard let data = commodityData else { return nil }
        var parts: [String] = []
        parts.append("COMMODITY CONTEXT:")
        parts.append("Symbol: \(commoditySymbol)")
        parts.append("Name: \(data.name)")
        parts.append("Price: $\(String(format: "%.2f", data.currentPrice))")
        parts.append("Change: \(String(format: "%+.2f", data.priceChangePercent))%")

        let allStats = data.keyStatisticsGroups.flatMap { $0.statistics }
        if !allStats.isEmpty {
            let statsText = allStats.map { "\($0.label): \($0.value)" }.joined(separator: ", ")
            parts.append("KEY STATISTICS: \(statsText)")
        }

        let perfText = data.performancePeriods
            .map { "\($0.label): \(String(format: "%+.2f", $0.changePercent))%" }
            .joined(separator: ", ")
        if !perfText.isEmpty {
            parts.append("PERFORMANCE: \(perfText)")
        }

        switch selectedTab {
        case .overview:
            break
        case .news:
            if !newsArticles.isEmpty {
                let headlines = newsArticles.prefix(5)
                    .map { "- \($0.headline) [\($0.sentiment.rawValue)]" }
                    .joined(separator: "\n")
                parts.append("RECENT NEWS:\n\(headlines)")
            }
        case .analysis:
            if let tech = technicalAnalysisData {
                parts.append("TECHNICAL: Signal=\(tech.overallSignal.rawValue), Gauge=\(tech.gaugeValue)")
            }
        }

        parts.append("User is viewing the \(selectedTab.rawValue) tab.")
        return parts.joined(separator: "\n")
    }

    // MARK: - Computed Properties

    var formattedPrice: String {
        commodityData?.formattedPrice ?? "--"
    }

    var formattedChange: String {
        commodityData?.formattedChange ?? "--"
    }

    var formattedChangePercent: String {
        commodityData?.formattedChangePercent ?? "--"
    }

    var isPositive: Bool {
        commodityData?.isPositive ?? true
    }

    var chartData: [Double] {
        commodityData?.chartData ?? []
    }

    var chartPricePoints: [StockPricePoint] {
        commodityData?.chartPricePoints ?? []
    }

    var aiSuggestions: [CommodityAISuggestion] {
        CommodityAISuggestion.defaultSuggestions
    }

    // MARK: - News Helpers

    private func mapApiToUiArticle(_ article: StockNewsArticle) -> TickerNewsArticle {
        let bullets: [String] = {
            if let aiBullets = article.summaryBullets, !aiBullets.isEmpty { return aiBullets }
            if let summary = article.summary, !summary.isEmpty { return [summary] }
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
        let formatters: [DateFormatter] = {
            let iso = DateFormatter()
            iso.dateFormat = "yyyy-MM-dd'T'HH:mm:ssZ"
            let simple = DateFormatter()
            simple.dateFormat = "yyyy-MM-dd HH:mm:ss"
            let dateOnly = DateFormatter()
            dateOnly.dateFormat = "yyyy-MM-dd"
            return [iso, simple, dateOnly]
        }()
        for formatter in formatters {
            if let date = formatter.date(from: dateString) { return date }
        }
        return ISO8601DateFormatter().date(from: dateString)
    }
}
