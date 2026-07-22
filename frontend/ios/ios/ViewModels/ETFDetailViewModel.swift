//
//  ETFDetailViewModel.swift
//  ios
//
//  ViewModel for the ETF Detail screen.
//  Fetches real data from GET /api/v1/etfs/{symbol}?range=3M
//  and maps the response DTO to display models.
//

import Foundation
import SwiftUI
import Combine

@MainActor
class ETFDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var etfData: ETFDetailData?
    @Published var newsArticles: [TickerNewsArticle] = []
    @Published var isNewsLoading: Bool = false
    @Published var hasMoreNews: Bool = false
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: ETFDetailTab = .overview
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

    // MARK: - Live Price
    let livePriceManager = LivePriceWebSocketManager()
    private var chartRefreshTask: Task<Void, Never>?

    // MARK: - Private Properties

    let etfSymbol: String
    private let repository: StockRepository = .shared
    private var cancellables = Set<AnyCancellable>()

    // MARK: - News (shared cache + deferred enrichment)
    /// Full set from the shared news cache; `newsArticles` is the paged slice.
    private var allNewsArticles: [TickerNewsArticle] = []
    private var newsDisplayCount: Int = 10
    private let newsPageSize: Int = 10
    /// Serialises enrichment (News-tab-appear vs fetch/load-more completion).
    private var isEnrichingNews = false
    /// Re-entrancy guard for `loadMoreNews` — the zero-height load-more sentinel
    /// re-fires the instant more rows render, so without this it cascades every
    /// remaining page in one tick (TickerDetail has the same guard).
    private var isLoadingMoreNews = false
    /// Monotonic token for ETF-detail fetches. fetchETFDetail and fetchChartForRange
    /// hit the same endpoint and write the whole etfData snapshot; each captures the
    /// token before awaiting and applies its result only if still current, so a slow
    /// earlier range can't clobber the chart the user has since switched to.
    private var chartRequestToken = 0
    /// True while the range sink assigns the range's default interval, so the
    /// interval sink doesn't ALSO reload (one range change would otherwise fire two
    /// identical fetches when the range crosses an interval boundary).
    private var suppressIntervalReload = false

    // MARK: - Initialization

    init(etfSymbol: String) {
        self.etfSymbol = etfSymbol

        // Observe chart range changes: auto-set default interval and manage timer
        $selectedChartRange
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] newRange in
                guard let self = self else { return }
                // Setting the interval fires the interval sink SYNCHRONOUSLY; suppress
                // its reload so a range change drives exactly one fetch (not two when
                // the new range crosses an interval boundary).
                self.suppressIntervalReload = true
                self.chartSettings.selectedInterval = newRange.defaultInterval
                self.suppressIntervalReload = false

                // Restart or stop chart refresh timer based on new range
                if newRange.defaultInterval.isIntraday && self.livePriceManager.isConnected {
                    self.startChartRefreshTimer()
                } else {
                    self.stopChartRefreshTimer()
                }

                Task { await self.fetchChartForRange(newRange) }
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
                Task { await self.fetchChartForRange(self.selectedChartRange) }
            }
            .store(in: &cancellables)

        // Observe live price updates from WebSocket and apply to etfData + chart
        livePriceManager.$livePrice
            .compactMap { $0 }
            .sink { [weak self] newPrice in
                guard let self = self, var data = self.etfData else { return }
                data.currentPrice = newPrice
                data.priceChange = self.livePriceManager.livePriceChange ?? data.priceChange
                data.priceChangePercent = self.livePriceManager.livePriceChangePercent ?? data.priceChangePercent

                // Update last chart candle for intraday ranges
                if self.chartSettings.selectedInterval.isIntraday,
                   !data.chartPricePoints.isEmpty {
                    let lastIndex = data.chartPricePoints.count - 1
                    var lastPoint = data.chartPricePoints[lastIndex]
                    lastPoint = StockPricePoint(
                        date: lastPoint.date,
                        close: newPrice,
                        open: lastPoint.open,
                        high: max(lastPoint.high ?? newPrice, newPrice),
                        low: min(lastPoint.low ?? newPrice, newPrice),
                        volume: lastPoint.volume
                    )
                    data.chartPricePoints[lastIndex] = lastPoint
                }

                self.etfData = data
            }
            .store(in: &cancellables)
    }

    // MARK: - Data Loading

    func loadETFData() {
        isLoading = true
        errorMessage = nil

        Task { [weak self] in
            guard let self = self else { return }
            async let detailTask: () = self.fetchETFDetail()
            async let newsTask: () = self.fetchETFNews()
            async let watchlistTask: () = self.checkWatchlistStatus()
            _ = await (detailTask, newsTask, watchlistTask)
        }
    }

    func refresh() async {
        await fetchETFDetail()
        await fetchETFNews()
    }

    /// Fetches ETF detail from the backend and maps to display models.
    private func fetchETFDetail() async {
        let startTime = CFAbsoluteTimeGetCurrent()
        chartRequestToken += 1
        let token = chartRequestToken

        do {
            print("[ETFDetailVM] Fetching ETF detail for \(etfSymbol), range: \(selectedChartRange.rawValue)")

            let response = try await repository.getETFDetail(
                symbol: etfSymbol,
                range: selectedChartRange.rawValue,
                interval: chartSettings.selectedInterval.rawValue
            )

            let elapsed = CFAbsoluteTimeGetCurrent() - startTime
            print("[ETFDetailVM] ✅ ETF detail loaded in \(String(format: "%.2f", elapsed))s — \(response.symbol) @ $\(response.currentPrice)")

            // Apply only if still the latest request — a newer range change may have
            // already painted fresher data that this slower response must not clobber.
            // The SAME token gates errorMessage + streaming so a stale success can't
            // clear a newer request's error banner or start streaming on stale data.
            if token == self.chartRequestToken {
                self.errorMessage = nil
                self.etfData = response.toDisplayModel()
                self.chartDataVersion += 1
                // News now comes from the SHARED cache via `fetchETFNews()`, not
                // this detail payload — so ETF news matches the Updates feed and
                // gets AI enrichment. (The payload still carries `news_articles`;
                // we just don't use it here.)

                // Start live price streaming + chart refresh if market is active.
                self.maybeStartStreaming()
            }
            self.isLoading = false

        } catch {
            let elapsed = CFAbsoluteTimeGetCurrent() - startTime
            print("[ETFDetailVM] ❌ ETF detail failed after \(String(format: "%.2f", elapsed))s — \(error)")

            if let apiError = error as? APIError {
                print("[ETFDetailVM] API Error detail: \(apiError)")
            }

            self.isLoading = false

            // Load fallback data so the screen isn't empty — but only if this is
            // still the latest request, so a STALE fetch's failure can't clobber
            // data a newer range fetch already painted.
            if token == self.chartRequestToken {
                self.errorMessage = "Unable to load ETF data. Pull to refresh."
                loadFallbackData()
            }
        }
    }

    /// Reload chart data when user changes the time range.
    func updateChartRange(_ range: ChartTimeRange) {
        selectedChartRange = range
    }

    /// Called by the Combine observer when selectedChartRange changes.
    private func fetchChartForRange(_ range: ChartTimeRange) async {
        print("[ETFDetailVM] Updating chart range to \(range.rawValue)")
        chartRequestToken += 1
        let token = chartRequestToken

        do {
            let response = try await repository.getETFDetail(
                symbol: self.etfSymbol,
                range: range.rawValue,
                interval: chartSettings.selectedInterval.rawValue
            )

            print("[ETFDetailVM] ✅ Chart range updated — \(response.chartData.count) data points")

            // Drop a stale response so a slow earlier range can't overwrite the chart
            // the user has since switched to (last-write-wins).
            guard token == self.chartRequestToken else { return }
            self.etfData = response.toDisplayModel()
            self.chartDataVersion += 1
            // News is loaded once via `fetchETFNews()` (shared cache), not
            // re-pulled on every chart-range change.

            // If a range change superseded the initial fetchETFDetail before it could
            // connect, this is now the path that paints etfData — so ensure streaming
            // starts here too (idempotent; no-op if already connected or market closed).
            self.maybeStartStreaming()

        } catch {
            print("[ETFDetailVM] ⚠️ Chart range update failed, keeping existing data — \(error)")
        }
    }

    /// Start live-price streaming + the chart-refresh timer if the market is active
    /// and we aren't already streaming. Idempotent, so any path that paints etfData
    /// (initial fetch OR a range change that supersedes it) can call it without
    /// double-connecting — closing the race where a fast range tap during the initial
    /// load left the ETF with no live price.
    private func maybeStartStreaming() {
        guard !livePriceManager.isConnected,
              let status = etfData?.marketStatus,
              MarketHoursUtil.shouldStreamLivePrice(for: status) else { return }
        connectLivePrice()
        startChartRefreshTimer()
    }

    // MARK: - Fallback Data

    private func loadFallbackData() {
        // Only seed the SPY sample into an EMPTY screen (first-load failure) — never
        // clobber already-loaded real data. Mirrors IndexDetailViewModel.loadFallbackData.
        // A failed pull-to-refresh must keep the real ETF on screen (just show the
        // error banner), not flip the whole card to hard-coded SPY figures.
        // Never paint another fund's hard-coded figures (sampleSPY) onto a failed
        // load: showing SPY's price / expense ratio / dividend yield / holdings under
        // a *different* ETF (e.g. QQQ) is financial misinformation and leaks into the
        // Cay AI context. Stay in the honest empty/skeleton state — errorMessage drives
        // the banner + pull-to-refresh. Mirrors the Crypto/Commodity VMs.
        guard etfData == nil else { return }
        print("[ETFDetailVM] ETF load failed — honest empty state (no sample seed)")
    }

    // MARK: - Live Price

    func connectLivePrice() {
        let token = KeychainService.shared.get("access_token") ?? ""
        livePriceManager.connect(ticker: etfSymbol, authToken: token)
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

                // Only refresh if market is active and we're on an intraday interval
                guard self.chartSettings.selectedInterval.isIntraday,
                      MarketHoursUtil.isMarketActive() else { continue }

                await self.fetchChartForRange(self.selectedChartRange)
            }
        }
    }

    private func stopChartRefreshTimer() {
        chartRefreshTask?.cancel()
        chartRefreshTask = nil
    }

    // MARK: - User Actions

    func toggleFavorite() {
        let wasInWatchlist = isFavorite
        isFavorite.toggle()

        Task { @MainActor in
            do {
                if wasInWatchlist {
                    try await APIClient.shared.request(
                        endpoint: .removeFromWatchlist(stockId: etfSymbol)
                    )
                    print("✅ [ETFDetailVM] Removed \(etfSymbol) from watchlist")
                } else {
                    try await APIClient.shared.request(
                        endpoint: .addToWatchlist(stockId: etfSymbol)
                    )
                    print("✅ [ETFDetailVM] Added \(etfSymbol) to watchlist")
                }
            } catch {
                print("⚠️ [ETFDetailVM] Watchlist toggle failed: \(error)")
                isFavorite = wasInWatchlist
            }
        }
    }

    private func checkWatchlistStatus() async {
        do {
            let watchlist: [WatchlistItemDTO] = try await APIClient.shared.request(
                endpoint: .getWatchlist,
                responseType: [WatchlistItemDTO].self
            )
            self.isFavorite = watchlist.contains { $0.ticker.uppercased() == etfSymbol.uppercased() }
        } catch {
            print("⚠️ [ETFDetailVM] Watchlist check failed: \(error)")
        }
    }

    private struct WatchlistItemDTO: Codable {
        let ticker: String
    }

    func handleNotificationTap() {
        print("[ETFDetailVM] Notification settings for \(etfSymbol)")
    }

    func handleWebsiteTap() {
        guard let website = etfData?.etfProfile.website,
              !website.isEmpty,
              let url = URL(string: "https://\(website)") else { return }
        openExternal(url, into: &browserLink)
    }

    func handleRelatedETFTap(_ ticker: RelatedTicker) {
        pendingTickerNavigation = ticker.symbol
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

    // MARK: - News fetch + enrichment (shared cache; mirrors the other detail VMs)

    private func fetchETFNews() async {
        self.isNewsLoading = true
        do {
            let response = try await repository.getETFNews(symbol: etfSymbol, limit: 50)
            let apiNews = response.articles
            print("📰 [ETFDetail] Got \(apiNews.count) news articles for \(etfSymbol) (cached: \(response.cached ?? false))")

            // Drop unrenderable rows (no parseable date) instead of stamping
            // them "now" — parity with the Updates screen for the same feed.
            self.allNewsArticles = apiNews.compactMap { mapApiToUiArticle($0) }
            self.newsDisplayCount = newsPageSize
            self.hasMoreNews = allNewsArticles.count > newsDisplayCount
            self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            self.isNewsLoading = false

            // Enrich ONLY if the News tab is being viewed — the expensive `flash`
            // model must not fire on every ETF open. `newsTabAppeared()` covers
            // the case where news lands before the user switches to the tab.
            if selectedTab == .news {
                await enrichVisibleArticles()
            }
        } catch {
            print("⚠️ [ETFDetail] Failed to fetch news for \(etfSymbol): \(error)")
        }
        self.isNewsLoading = false
    }

    func loadMoreNews() {
        guard !isLoadingMoreNews, hasMoreNews else { return }
        isLoadingMoreNews = true
        newsDisplayCount += newsPageSize
        newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
        hasMoreNews = allNewsArticles.count > newsDisplayCount
        // Reset AFTER enrichment (not synchronously) so the load-more sentinel
        // can't cascade every remaining page in a single tick.
        Task {
            await enrichVisibleArticles()
            isLoadingMoreNews = false
        }
    }

    /// Called when the News tab becomes visible — defers AI enrichment to when
    /// news is actually read. See TickerDetailViewModel.newsTabAppeared.
    func newsTabAppeared() {
        Task { await enrichVisibleArticles() }
    }

    private func enrichVisibleArticles() async {
        guard !isEnrichingNews else { return }
        let unenriched = newsArticles.filter { !$0.aiProcessed }
        guard !unenriched.isEmpty else { return }
        let ids = unenriched.map { $0.apiId }
            .filter { !$0.isEmpty && !$0.hasPrefix("temp_") && !$0.hasPrefix("raw_") }
        guard !ids.isEmpty else { return }

        isEnrichingNews = true
        defer { isEnrichingNews = false }

        await attemptEnrichment(articleIds: ids)
        newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
    }

    private func attemptEnrichment(articleIds: [String], maxAttempts: Int = 2) async {
        for attempt in 1...maxAttempts {
            do {
                let enrichResponse = try await repository.enrichETFNews(
                    symbol: etfSymbol, articleIds: articleIds
                )
                mergeEnrichment(enrichResponse.articles)
                let enrichedCount = allNewsArticles.prefix(newsDisplayCount)
                    .filter { $0.aiProcessed }.count
                if enrichedCount > 0 {
                    print("✅ [ETFDetail] Attempt \(attempt) enriched \(enrichedCount) articles")
                    return
                } else if attempt < maxAttempts {
                    try await Task.sleep(nanoseconds: 3_000_000_000)
                }
            } catch {
                if attempt < maxAttempts {
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                } else {
                    print("⚠️ [ETFDetail] Enrichment failed after \(maxAttempts) attempts: \(error)")
                }
            }
        }
    }

    private func mergeEnrichment(_ enrichedArticles: [StockNewsArticle]) {
        let enrichedById = Dictionary(
            enrichedArticles.map { ($0.id, $0) },
            uniquingKeysWith: { first, _ in first }
        )
        for i in allNewsArticles.indices {
            guard let enriched = enrichedById[allNewsArticles[i].apiId] else { continue }
            let wasProcessed = enriched.aiProcessed ?? false
            let hasBullets = enriched.summaryBullets?.isEmpty == false
            guard wasProcessed || hasBullets else { continue }
            // Only real AI bullets — no raw-summary pseudo-bullet (parity with Updates).
            allNewsArticles[i].summaryBullets = enriched.summaryBullets ?? []
            allNewsArticles[i].sentiment = mapSentiment(enriched.sentiment)
            allNewsArticles[i].aiProcessed = true
        }
    }

    private func mapApiToUiArticle(_ article: StockNewsArticle) -> TickerNewsArticle? {
        // Drop rows with an unparseable/absent date instead of stamping "now" —
        // parity with the Updates screen.
        guard let published = article.publishedAt.flatMap({ parseNewsDate($0) }) else {
            return nil
        }
        return TickerNewsArticle(
            apiId: article.id,
            headline: article.title,
            source: NewsSource(
                name: article.source ?? "Unknown",
                iconName: nil,
                logoURL: article.sourceLogoUrl.flatMap { URL(string: $0) }
            ),
            sentiment: mapSentiment(article.sentiment),
            publishedAt: published,
            thumbnailName: nil,
            imageURL: article.imageUrl.flatMap { URL(string: $0) },
            relatedTickers: article.relatedTickers ?? [],
            // Only real AI bullets — no raw-summary pseudo-bullet (parity with Updates).
            summaryBullets: article.summaryBullets ?? [],
            articleURL: article.url.flatMap { URL(string: $0) },
            aiProcessed: article.aiProcessed ?? false
        )
    }

    /// nil ⇒ no badge until AI-enriched. Matches `NewsSentiment(backend:)` on the
    /// Updates side so the same row renders identically.
    private func mapSentiment(_ sentiment: String?) -> NewsSentiment? {
        NewsSentiment(backend: sentiment)
    }

    private func parseNewsDate(_ dateString: String) -> Date? {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = iso.date(from: dateString) { return d }
        iso.formatOptions = [.withInternetDateTime]
        if let d = iso.date(from: dateString) { return d }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        if let d = f.date(from: dateString) { return d }
        f.dateFormat = "yyyy-MM-dd"
        return f.date(from: dateString)
    }

    func handleSuggestionTap(_ suggestion: ETFAISuggestion) {
        aiInputText = suggestion.text
        handleAISend()
    }

    func handleAISend() {
        guard !aiInputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let query = aiInputText
        aiInputText = ""
        print("[ETFDetailVM] AI Query for \(etfSymbol): \(query)")
        pendingAIQuery = query
    }

    // MARK: - Computed Properties

    var formattedPrice: String {
        etfData?.formattedPrice ?? "--"
    }

    var formattedChange: String {
        etfData?.formattedChange ?? "--"
    }

    var formattedChangePercent: String {
        etfData?.formattedChangePercent ?? "--"
    }

    var isPositive: Bool {
        etfData?.isPositive ?? true
    }

    var chartData: [Double] {
        etfData?.chartData ?? []
    }

    var chartPricePoints: [StockPricePoint] {
        etfData?.chartPricePoints ?? []
    }

    var aiSuggestions: [ETFAISuggestion] {
        ETFAISuggestion.defaultSuggestions
    }

    // MARK: - AI Context Builders

    /// Core ETF facts — always included regardless of tab
    private var baseETFContext: String? {
        guard let data = etfData else { return nil }
        var lines: [String] = []
        lines.append("ETF: \(data.symbol) (\(data.name))")
        lines.append("Price: \(data.formattedPrice) \(data.formattedChange) \(data.formattedChangePercent)")

        if !data.keyStatisticsGroups.isEmpty {
            lines.append("Key Statistics:")
            for group in data.keyStatisticsGroups {
                let groupStr = group.statistics.map { "\($0.label): \($0.value)" }.joined(separator: " | ")
                lines.append("  \(groupStr)")
            }
        }

        if !data.performancePeriods.isEmpty {
            let perfStr = data.performancePeriods.map { p in
                let sign = p.changePercent >= 0 ? "+" : ""
                return "\(p.label): \(sign)\(String(format: "%.1f", p.changePercent))%"
            }.joined(separator: ", ")
            lines.append("Performance: \(perfStr)")
        }

        return lines.joined(separator: "\n")
    }

    /// Overview tab context — snapshot data, strategy, yield, holdings, benchmark
    private var overviewContext: String? {
        guard let data = etfData else { return nil }
        var parts: [String] = []

        parts.append("Identity Rating: \(data.identityRating.score)/\(data.identityRating.maxScore), \(data.identityRating.volatilityLabel)")
        parts.append("Strategy: \(data.strategy.hook) Tags: \(data.strategy.tags.joined(separator: ", "))")
        parts.append("Expense Ratio: \(data.netYield.formattedExpenseRatio), Dividend Yield: \(data.netYield.formattedDividendYield), \(data.netYield.payFrequency)")
        parts.append("Verdict: \(data.netYield.verdict)")

        let topHoldings = data.holdingsRisk.topHoldings.prefix(5).map { "\($0.symbol) \($0.formattedWeight)" }.joined(separator: ", ")
        parts.append("Top Holdings: \(topHoldings)")
        parts.append("Concentration: Top \(data.holdingsRisk.concentration.topN) = \(data.holdingsRisk.concentration.formattedWeight)")

        if let bench = data.benchmarkSummary {
            let sign = bench.avgAnnualReturn >= 0 ? "+" : ""
            parts.append("Avg Annual Return: \(sign)\(String(format: "%.1f", bench.avgAnnualReturn))% vs \(bench.benchmarkName): \(String(format: "%.1f", bench.spBenchmark))%")
        }

        return parts.isEmpty ? nil : parts.joined(separator: ". ")
    }

    /// News tab context — recent headlines
    private var newsContext: String? {
        let recent = newsArticles.prefix(3)
        guard !recent.isEmpty else { return nil }
        let headlines = recent.map { "- \($0.headline) (\($0.source.name))" }.joined(separator: "\n")
        return "Recent News:\n\(headlines)"
    }

    /// Build context string for the current tab to inject into AI chat
    var contextForCurrentTab: String? {
        var sections: [String] = []

        if let base = baseETFContext {
            sections.append(base)
        }

        switch selectedTab {
        case .overview:
            if let ctx = overviewContext { sections.append(ctx) }
        case .news:
            if let ctx = newsContext { sections.append(ctx) }
        }

        sections.append("User is viewing the \(selectedTab.rawValue) tab of ETF \(etfSymbol).")

        return sections.isEmpty ? nil : sections.joined(separator: "\n\n")
    }
}
