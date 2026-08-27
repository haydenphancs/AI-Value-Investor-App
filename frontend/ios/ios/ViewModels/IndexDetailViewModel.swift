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
    /// Fast-core first paint. Populated by `/indices/{symbol}/core`, which answers in
    /// ~0.3s from the cheap cached sections, and rendered by the header/chart gate as
    /// `indexData ?? coreData`.
    ///
    /// Written ONLY while `indexData` is still nil (see `loadCore`), so a slow core
    /// response can never overwrite the full model. Mirrors `TickerDetailViewModel`.
    @Published var coreData: IndexCoreData?

    /// What the header + chart render: the full model when it has landed, the fast-core
    /// slice until then.
    ///
    /// Written as an `if let` rather than `indexData ?? coreData` on purpose — the two
    /// sides are different concrete types and only share the protocol, so the coalescing
    /// form depends on contextual coercion that is easy to break by accident.
    var headerData: (any IndexHeaderRenderable)? {
        if let indexData { return indexData }
        return coreData
    }
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

    /// External link to show in the in-app browser. Set via `openExternal(_:into:)`
    /// and presented by the Screen's `.inAppBrowser(link:)` — a ViewModel cannot
    /// present a view itself.
    @Published var browserLink: BrowserLink?

    // Analysis tab state
    @Published var isTechnicalLoaded: Bool = false
    /// Why the Analysis tab has no data, when it has none. Nil while loading or on
    /// success. The tab used to render literally NOTHING on failure — an empty tab is
    /// indistinguishable from a broken app, and `isTechnicalLoaded` was set to true on
    /// the failure path, making the state terminal for the life of the screen.
    @Published var technicalUnavailableMessage: String?
    /// False when the asset genuinely has no price history (the backend 404s), so a
    /// "Try Again" button would promise something that can never succeed.
    @Published var technicalIsRetryable: Bool = false
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
    /// Serialises news enrichment (tab-appear vs fetch/load-more completion).
    private var isEnrichingNews = false
    /// Re-entrancy guard for `loadMoreNews` (see TickerDetail) — the zero-height
    /// sentinel re-fires the instant more rows render.
    private var isLoadingMoreNews = false
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

        // Seed the interval from the range's own default BEFORE the sinks are wired.
        //
        // `selectedChartRange` defaults to 3M (daily) while `ChartSettings.selectedInterval`
        // defaults to `.fiveMin`, and the range sink is `.dropFirst()`-ed so it never fires
        // for the initial value. Every cold open therefore requested `?range=3M&interval=5min`
        // — a pair the picker cannot produce (`ChartTimeRange.allowedIntervals` excludes it),
        // so the backend silently fell back to `daily` and cached the response under a key no
        // later request would ever reuse. It also made `selectedInterval.isIntraday` true on a
        // daily chart, which un-gated the 30-second refresh timer.
        //
        // Assigning here fires nothing: both sinks are registered below and drop their first
        // value at subscribe time.
        chartSettings.selectedInterval = selectedChartRange.defaultInterval

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
            // One-time setup that must NOT be gated on the detail request token: a
            // range change during the initial fetch supersedes it, and the stale
            // response then skipped the connect, leaving the index with no live
            // updates / no 30s refresh until a manual refresh. connectLivePrice is
            // independent of the response, so start streaming here. The timer + the
            // WebSocket both self-gate on market hours, so this is a no-op when closed.
            self.connectLivePrice()
            self.startChartRefreshTimer()
            // Fast core, in parallel with the full detail: whichever lands first paints.
            async let coreTask: () = self.loadCore()
            async let fetchTask: () = self.fetchIndexDetail()
            async let newsTask: () = self.fetchIndexNews()
            async let watchlistTask: () = self.checkWatchlistStatus()
            async let technicalTask: () = self.fetchTechnicalAnalysis()
            _ = await (coreTask, fetchTask, newsTask, watchlistTask, technicalTask)
        }
    }

    func refresh() async {
        errorMessage = nil
        // Drop this asset's CLIENT-side cache first — otherwise the gesture does no
        // network work for anything served by StockRepository (news 60s, analyst /
        // sentiment / technical 30 min, ETF profile + holdings-risk + dividends 24h
        // against a process-lifetime singleton). Backend caches still absorb the
        // upstream cost; this only bypasses the on-device copy.
        StockRepository.shared.invalidate(symbol: indexSymbol)
        await fetchIndexDetail()
        // Pull-to-refresh must also retry the Analysis tab. Its failure path sets
        // `isTechnicalLoaded = true` with nil data, which is TERMINAL — without this
        // line one failed fetch left the tab blank for the entire life of the screen
        // and there was no gesture anywhere in the app that could recover it.
        await retryTechnicalAnalysis()
    }

    /// Re-run the technical fetch after a failure. Safe to call repeatedly.
    func retryTechnicalAnalysis() async {
        isTechnicalLoaded = false
        technicalUnavailableMessage = nil
        await fetchTechnicalAnalysis()
    }

    /// Bumped on every star tap. `checkWatchlistStatus()` captures it before its GET and
    /// re-checks it after, so a snapshot that was already in flight when the user tapped
    /// is discarded instead of reverting them. TickerDetailViewModel had this guard; all
    /// four sibling screens shipped without it, so a slow watchlist GET landing after the
    /// tap silently un-starred the asset with no error and no trace.
    ///
    /// A generation counter, NOT a sticky Bool: `checkWatchlistStatus()` re-runs from each
    /// screen's error-state Retry, and a sticky flag would pin the local value forever and
    /// ignore a genuine change made on another device.
    private var favoriteToggleGeneration: Int = 0

    func toggleFavorite() {
        // The user's intent now outranks any watchlist snapshot already in flight.
        favoriteToggleGeneration &+= 1
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
                // Revert AND tell the user. The revert was always right; the silence was the
                // bug — in a release build a star that fills in and empties again is
                // indistinguishable from the app deciding the tap never happened.
                isFavorite = wasInWatchlist
                AppActions.shared.reportMutationFailure(
                    error,
                    action: wasInWatchlist
                        ? "remove \(self.indexSymbol) from your watchlist"
                        : "add \(self.indexSymbol) to your watchlist",
                    signInFeature: "save this index"
                )
            }
        }
    }

    private func checkWatchlistStatus() async {
        let generation = favoriteToggleGeneration
        do {
            let watchlist: [WatchlistItemDTO] = try await APIClient.shared.request(
                endpoint: .getWatchlist,
                responseType: [WatchlistItemDTO].self
            )
            // Discard a snapshot that raced with a tap: it may predate the user's write
            // and would revert their star with no error shown.
            guard generation == self.favoriteToggleGeneration else {
                print("⏭️ [IndexDetailVM] Watchlist snapshot discarded — user toggled during the fetch")
                return
            }
            self.isFavorite = watchlist.contains { $0.ticker.uppercased() == indexSymbol.uppercased() }
        } catch {
            print("⚠️ [IndexDetailVM] Watchlist check failed: \(error)")
        }
    }

    private struct WatchlistItemDTO: Codable {
        let ticker: String
    }

    func handleWebsiteTap() {
        guard let website = indexData?.indexProfile.website,
              let url = URL(string: "https://\(website)") else { return }

        openExternal(url, into: &browserLink)
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
        // See TickerDetailViewModel.connectLivePrice for why this reads APIClient rather than
        // the Keychain. Note the old `guard let … else { return }` meant a guest got NO live
        // price at all and no fallback — the stream is public for these symbols, so connect
        // regardless and let the token be nil.
        Task { [weak self] in
            guard let self else { return }
            let token = await APIClient.shared.currentAuthToken()
            self.livePriceManager.connect(ticker: self.indexSymbol, authToken: token)
        }
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

                guard MarketHoursUtil.isMarketActive() else { continue }

                // The LIGHT slice, not the whole detail payload. Bars only when the chart
                // is intraday: on a daily chart a 30-second refresh cannot move a candle.
                // The level header refreshes either way — the old `isIntraday` guard froze
                // it entirely on a daily chart.
                await self.refreshLiveSlice(
                    includeChart: self.chartSettings.selectedInterval.isIntraday
                )
            }
        }
    }

    private func stopChartRefreshTimer() {
        chartRefreshTask?.cancel()
        chartRefreshTask = nil
    }

    // MARK: - News Pagination

    func loadMoreNews() {
        guard !isLoadingMoreNews, !isNewsLoading, hasMoreNews else { return }
        isLoadingMoreNews = true
        newsDisplayCount += newsPageSize
        newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
        hasMoreNews = newsDisplayCount < allNewsArticles.count

        // Reset AFTER enrichment so the sentinel can't cascade every page at once.
        Task {
            await enrichVisibleArticles()
            isLoadingMoreNews = false
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
            self.technicalUnavailableMessage = nil
            print("✅ [IndexDetailVM] Got technical analysis for \(indexSymbol) — gauge: \(dto.gaugeValue)")
        } catch {
            print("⚠️ [IndexDetailVM] Technical analysis failed: \(error)")
            // Do NOT fabricate a BUY gauge from sampleData — a hardcoded "Buy" signal
            // on a failed fetch is financial misinformation and leaks into Cay AI
            // context. Leave nil; the Analysis section renders its honest empty state.
            self.technicalAnalysisData = nil
            self.isTechnicalLoaded = true
            // Distinguish "this asset has no history" (permanent — the service raises a
            // 404 for a ticker with no OHLCV) from "the fetch failed" (retryable). The
            // tab previously rendered nothing at all for BOTH, so a transient blip
            // looked identical to an unsupported asset and neither offered a way back.
            if case APIError.notFound = error {
                self.technicalUnavailableMessage =
                    "Technical analysis isn\u{2019}t available for this asset."
                self.technicalIsRetryable = false
            } else {
                self.technicalUnavailableMessage = "Couldn\u{2019}t load technical analysis."
                self.technicalIsRetryable = true
            }
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
                // Do NOT fabricate Apple's pivots/levels for this index (misinformation).
                self.technicalAnalysisDetailData = nil
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

    /// Fetch the fast-core slice in parallel with the full detail and paint it the moment
    /// it lands.
    ///
    /// Why: the whole screen used to sit behind ONE aggregated response — `^GSPC`
    /// measured 5.63s cold against 0.14s warm — which is the "very slow at first time
    /// open it" TestFlight report this fixes. The stock screen never had that problem
    /// despite the slowest full build of the lot, because it paints a core slice first.
    ///
    /// `try?` on purpose: core is an accelerator, so a core failure must be invisible —
    /// the full response is already in flight and owns the error path. It also
    /// deliberately does NOT touch `errorMessage` or bump `chartRequestToken`, both of
    /// which belong to the full fetch.
    private func loadCore() async {
        guard let core = try? await StockRepository.shared.getIndexCore(
            symbol: indexSymbol,
            range: selectedChartRange.rawValue,
            interval: chartSettings.selectedInterval.rawValue
        ) else { return }
        // The race guard. A core response landing AFTER the full one must be dropped, or
        // the screen would visibly step backwards from the complete model to the
        // header-only one.
        guard indexData == nil else { return }
        coreData = core.toCoreData()
        chartDataVersion += 1
    }

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

            // Map DTOs → display models — but only if this is still the latest
            // request; a newer range change may have already painted fresher data,
            // and this (slower, earlier) response must not clobber it. The SAME
            // token gates errorMessage + streaming so a stale success can't clear a
            // newer request's error banner or start streaming on a superseded load.
            if token == self.chartRequestToken {
                self.errorMessage = nil
                self.indexData = response.toDisplayModel()
                self.chartDataVersion += 1
                // NOTE: live-price connect + refresh timer are started once in
                // loadIndexData (independent of this request token), so a range change
                // that supersedes this fetch can't leave the index unstreamed.
            }

            // News and technical analysis are fetched via separate concurrent tasks

            self.isLoading = false

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

    /// Light refresh: merge the volatile slice into `indexData` IN PLACE.
    ///
    /// Replaces a `loadChartData` call that re-requested the entire detail payload —
    /// including `snapshotsData`, a deep graph of AI-written valuation, sector and macro
    /// stories — and then did `self.indexData = response.toDisplayModel()`, a wholesale
    /// replacement that erased every WebSocket tick since the last refresh on a 30-second
    /// sawtooth.
    ///
    /// The socket WINS over the REST snapshot: a tick is now, a snapshot is up to 45s old.
    private func refreshLiveSlice(includeChart: Bool) async {
        chartRequestToken += 1
        let token = chartRequestToken
        let range = selectedChartRange
        do {
            let light = try await StockRepository.shared.getIndexQuote(
                symbol: indexSymbol,
                range: includeChart ? range.rawValue : nil,
                interval: includeChart ? chartSettings.selectedInterval.rawValue : nil
            )
            // Drop a stale response so rapid range switching can't clobber a newer range.
            guard token == self.chartRequestToken, let current = self.indexData else { return }

            self.indexData = light.merged(
                into: current,
                livePrice: self.livePriceManager.livePrice,
                liveChange: self.livePriceManager.livePriceChange,
                liveChangePercent: self.livePriceManager.livePriceChangePercent,
                includeChart: includeChart
            )
            if includeChart, !light.chartData.isEmpty {
                self.chartDataVersion += 1
            }
        } catch {
            print("⚠️ [IndexDetailVM] Live slice refresh failed: \(error)")
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

            // Convert API articles to UI models, dropping unrenderable rows
            // (no parseable date) — parity with the Updates screen.
            self.allNewsArticles = response.articles.compactMap { mapApiToUiArticle($0) }
            self.newsDisplayCount = newsPageSize
            self.hasMoreNews = allNewsArticles.count > newsDisplayCount

            // Show articles immediately with raw data
            self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            self.isNewsLoading = false

            // Enrich ONLY the VISIBLE batch, and ONLY if the News tab is being
            // viewed. This used to enrich ALL ~50 articles on every index open —
            // the most wasteful of the detail screens. See
            // TickerDetailViewModel.fetchStockNews.
            if selectedTab == .news {
                await enrichVisibleArticles()
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
                    // Only real AI bullets — no raw-summary pseudo-bullet (parity with Updates).
                    allNewsArticles[i].summaryBullets = enriched.summaryBullets ?? []
                    allNewsArticles[i].sentiment = mapSentiment(enriched.sentiment)
                    allNewsArticles[i].aiProcessed = true
                    actuallyEnriched += 1
                }
            }
        }
        print("📰 [IndexDetailVM] Merged \(actuallyEnriched)/\(enrichedArticles.count) enriched articles")
    }

    private func mapApiToUiArticle(_ article: StockNewsArticle) -> TickerNewsArticle? {
        // Drop rows with an unparseable/absent date instead of stamping "now" —
        // parity with the Updates screen.
        guard let published = article.publishedAt.flatMap({ parseDate($0) }) else {
            return nil
        }
        // Drop a title-less row, exactly as the Updates feed does — the same
        // shared cache row must not render as a blank card here while Updates
        // omits it.
        guard !article.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return nil }

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

    /// nil ⇒ no badge until AI-enriched (parity with Updates via NewsSentiment(backend:)).
    private func mapSentiment(_ sentiment: String?) -> NewsSentiment? {
        NewsSentiment(backend: sentiment)
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
        // UTC, not device-local: FMP's space-form timestamp is UTC. Omitting this
        // parsed it in the device zone, so the same shared row showed a time
        // shifted by the device's offset here vs Updates / the other detail tabs.
        fallback.timeZone = TimeZone(identifier: "UTC")
        return fallback.date(from: dateString)
    }

    // MARK: - Fallback

    private func loadFallbackData() {
        // Never paint S&P 500 sample figures onto a *different* index (e.g. Dow,
        // Nasdaq): showing ^GSPC's level / P/E / sectors under ^IXIC is a wrong-index
        // masquerade (and leaks into Cay AI context). Stay in the honest empty state —
        // errorMessage drives the banner + pull-to-refresh. Mirrors the Crypto VM.
        // Technical analysis is fetched separately (see fetchTechnicalAnalysis).
        print("🔄 [IndexDetailVM] Index load failed — honest empty state (no sample seed)")
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
            .map { a in
                // sentiment is nil until AI-enriched — omit the tag then.
                a.sentiment.map { "- \(a.headline) [\($0.rawValue)]" } ?? "- \(a.headline)"
            }
            .joined(separator: "\n")
        return "RECENT NEWS:\n\(headlines)"
    }

    private var analysisContext: String? {
        guard let tech = technicalAnalysisData else { return nil }
        return "TECHNICAL: Signal=\(tech.overallSignal.rawValue), Gauge=\(tech.gaugeValue)"
    }
}
