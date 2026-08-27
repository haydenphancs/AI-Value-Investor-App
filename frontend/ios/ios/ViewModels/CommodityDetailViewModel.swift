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
    /// Fast-core first paint. Populated by `/commodities/{symbol}/core`, which answers in
    /// ~0.3s from the cheap cached sections, and rendered by the header/chart gate as
    /// `commodityData ?? coreData`.
    ///
    /// Written ONLY while `commodityData` is still nil (see `loadCore`), so a slow core
    /// response can never overwrite the full model. Mirrors `TickerDetailViewModel`.
    @Published var coreData: CommodityCoreData?

    /// What the header + chart render: the full model when it has landed, the fast-core
    /// slice until then.
    ///
    /// Written as an `if let` rather than `commodityData ?? coreData` on purpose — the two
    /// sides are different concrete types and only share the protocol, so the coalescing
    /// form depends on contextual coercion that is easy to break by accident.
    var headerData: (any CommodityHeaderRenderable)? {
        if let commodityData { return commodityData }
        return coreData
    }
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
    /// Where a tap wants to navigate, WITH the asset type.
    ///
    /// This was a bare `String?` and the screen hardcoded its own asset type for every
    /// value, so a related-ticker chip on a NEWS card — which carries US-listed EQUITY
    /// symbols, put there by the shared enrichment prompt — opened the wrong screen
    /// entirely: tapping "NVDA" on the BTC screen pushed CryptoDetailView("NVDA").
    /// Carrying the type with the symbol makes that unrepresentable.
    @Published var pendingTickerNavigation: SearchSelection?

    /// External link to show in the in-app browser. Set via `openExternal(_:into:)`
    /// and presented by the Screen's `.inAppBrowser(link:)` — a ViewModel cannot
    /// present a view itself.
    @Published var browserLink: BrowserLink?
    @Published var chartSettings = ChartSettings()
    @Published var chartDataVersion: Int = 0

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

    // News state
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

    let commoditySymbol: String
    private let apiClient = APIClient.shared
    private var cancellables = Set<AnyCancellable>()
    /// Mirrors IndexDetailViewModel. The commodity screen previously had NO live price
    /// path at all — `chartRefreshTask` below was declared and never assigned, so the
    /// quote was frozen for the entire life of the screen while the field implied a
    /// refresh that never ran.
    let livePriceManager = LivePriceWebSocketManager()
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
                Task { await self.refreshLiveSlice(includeChart: true) }
            }
            .store(in: &cancellables)

        chartSettings.$selectedInterval
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] _ in
                guard let self = self else { return }
                guard !self.suppressIntervalReload else { return }
                Task { await self.refreshLiveSlice(includeChart: true) }
            }
            .store(in: &cancellables)

        observeLivePrice()
    }

    // MARK: - Data Loading

    func loadCommodityData() {
        isLoading = true
        errorMessage = nil

        Task { [weak self] in
            guard let self = self else { return }
            // One-time setup, deliberately NOT gated on the detail request token: a
            // range change during the initial fetch supersedes it, and a stale response
            // would then skip the connect and leave the screen with no live updates and
            // no 30s refresh. Both the socket and the timer self-gate, so this is cheap
            // when the market for this symbol is shut.
            self.connectLivePrice()
            // Fast core, in parallel with the full detail: whichever lands first paints.
            async let coreTask: () = self.loadCore()
            async let detailTask: () = self.fetchCommodityDetail()
            async let newsTask: () = self.fetchCommodityNews()
            async let technicalTask: () = self.fetchTechnicalAnalysis()
            async let watchlistTask: () = self.checkWatchlistStatus()
            _ = await (coreTask, detailTask, newsTask, technicalTask, watchlistTask)
        }
    }

    func refresh() async {
        errorMessage = nil
        // Drop this asset's CLIENT-side cache first — otherwise the gesture does no
        // network work for anything served by StockRepository (news 60s, analyst /
        // sentiment / technical 30 min, ETF profile + holdings-risk + dividends 24h
        // against a process-lifetime singleton). Backend caches still absorb the
        // upstream cost; this only bypasses the on-device copy.
        // Both spellings: Home's Market Pulse pushes "GCUSD" while a search selection can
        // push the bare root, and `invalidate` matches whole `_`-separated key components.
        StockRepository.shared.invalidate(
            symbol: commoditySymbol,
            aliases: [commoditySymbol.uppercased().hasSuffix("USD")
                        ? String(commoditySymbol.dropLast(3))
                        : commoditySymbol + "USD"]
        )
        await fetchCommodityDetail()
        await fetchCommodityNews()
        // See IndexDetailViewModel.refresh — the technical failure path is terminal, so
        // refresh is the only thing that can un-stick a blank Analysis tab.
        await retryTechnicalAnalysis()
    }


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
    /// deliberately does NOT touch `errorMessage` or bump any request token, both of
    /// which belong to the full fetch.
    private func loadCore() async {
        guard let core = try? await StockRepository.shared.getCommodityCore(
            symbol: commoditySymbol,
            range: selectedChartRange.rawValue,
            interval: chartSettings.selectedInterval.rawValue
        ) else { return }
        // The race guard. A core response landing AFTER the full one must be dropped, or
        // the screen would visibly step backwards from the complete model to the
        // header-only one.
        guard commodityData == nil else { return }
        coreData = core.toCoreData()
        chartDataVersion += 1
    }

    // MARK: - Detail Fetch

    private func fetchCommodityDetail() async {
        let range = selectedChartRange
        let startTime = CFAbsoluteTimeGetCurrent()
        detailRequestGen += 1
        let gen = detailRequestGen

        do {
            let response = try await StockRepository.shared.getCommodityDetail(
                symbol: commoditySymbol,
                range: range.rawValue,
                interval: chartSettings.selectedInterval.rawValue
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

    // MARK: - Live Price

    /// Merge socket ticks into `commodityData`. Every field falls back to what the REST
    /// load produced, so if the upstream feed never ticks for this symbol the screen
    /// still shows the 30s-refreshed values rather than blanking.
    private func observeLivePrice() {
        livePriceManager.$livePrice
            .compactMap { $0 }
            .sink { [weak self] newPrice in
                guard let self = self, var data = self.commodityData else { return }
                data.currentPrice = newPrice
                data.priceChange = self.livePriceManager.livePriceChange ?? data.priceChange
                data.priceChangePercent = self.livePriceManager.livePriceChangePercent ?? data.priceChangePercent
                self.commodityData = data
            }
            .store(in: &cancellables)
    }

    func connectLivePrice() {
        // Reads APIClient rather than the Keychain — the two deliberately diverge during
        // session restore, and the stream is public for these symbols, so connect even
        // when the token is nil rather than leaving a guest with no live price.
        Task { [weak self] in
            guard let self else { return }
            let token = await APIClient.shared.currentAuthToken()
            self.livePriceManager.connect(ticker: self.commoditySymbol, authToken: token)
        }
        startChartRefreshTimer()
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

                // NOT `MarketHoursUtil.isMarketActive()`, which the index copy uses:
                // that is the US EQUITY session, and these are continuously-quoted
                // futures (~23h/day). Gating on equity hours would leave the commodity
                // screen frozen for most of the day — the very bug being fixed.
                // NO `isIntraday` gate any more. It used to skip the whole refresh on a
                // daily chart, which froze the PRICE HEADER for the life of the screen —
                // the light slice costs ~1.2 KB, so there is no reason to skip it. Bars
                // are still only requested when the chart is actually intraday, because
                // on a daily chart 30 seconds cannot move one.
                guard MarketHoursUtil.symbolTradesAroundTheClock(self.commoditySymbol)
                        || MarketHoursUtil.isMarketActive() else { continue }

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

    // MARK: - Chart Range Change

    /// Light refresh: merge the volatile slice into `commodityData` IN PLACE.
    ///
    /// Replaces a `fetchChartForRange` that, despite its name, re-requested the entire
    /// ~11.9 KB detail payload and then did `self.commodityData = response.toDisplayModel()`
    /// — a wholesale replacement. Two consequences, both fixed here:
    ///
    ///  1. Every WebSocket tick that had landed since the last refresh was ERASED, on a
    ///     30-second sawtooth, on a screen whose whole point is a live price.
    ///  2. A range tap re-fetched news, profile, related quotes, performance and the
    ///     benchmark — none of which depend on the range.
    ///
    /// The socket WINS over the REST snapshot: a tick is now, a snapshot is up to 45s old.
    /// Falling back to the REST values keeps the screen alive for symbols whose upstream
    /// feed never ticks, which is the reason this loop exists at all.
    private func refreshLiveSlice(includeChart: Bool) async {
        detailRequestGen += 1
        let gen = detailRequestGen
        let range = selectedChartRange
        do {
            let light = try await StockRepository.shared.getCommodityQuote(
                symbol: commoditySymbol,
                range: includeChart ? range.rawValue : nil,
                interval: includeChart ? chartSettings.selectedInterval.rawValue : nil
            )
            // Drop a stale response so rapid range switching can't clobber a newer range.
            guard gen == self.detailRequestGen, var data = self.commodityData else { return }

            data.currentPrice = livePriceManager.livePrice ?? light.currentPrice
            data.priceChange = livePriceManager.livePriceChange ?? light.priceChange
            data.priceChangePercent =
                livePriceManager.livePriceChangePercent ?? light.priceChangePercent
            data.marketStatus = CommodityMarketStatus(backend: light.marketStatus)
            data.keyStatisticsGroups = light.keyStatisticsGroups.map { $0.toModel() }
            // Keep the previous list when the refresh returns none — a 60s cache miss on
            // the related quotes must not blank a populated "People Also Check" row.
            if let related = light.relatedCommodities, !related.isEmpty {
                data.relatedCommodities = related.map { $0.toModel() }
            }
            if includeChart, !light.chartData.isEmpty {
                data.chartPricePoints = light.chartData.map {
                    StockPricePoint(
                        date: $0.date, close: $0.close, open: $0.open,
                        high: $0.high, low: $0.low, volume: $0.volume
                    )
                }
                self.chartDataVersion += 1
            }
            // performancePeriods / commodityProfile / benchmarkSummary are untouched:
            // they are range-independent and cannot change between refreshes.
            self.commodityData = data
        } catch {
            print("⚠️ [CommodityDetailVM] Live slice refresh failed: \(error)")
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

            // Drop unrenderable rows (no parseable date) — parity with Updates.
            self.allNewsArticles = response.articles.compactMap { mapApiToUiArticle($0) }
            self.newsDisplayCount = newsPageSize
            self.hasMoreNews = allNewsArticles.count > newsDisplayCount
            self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            self.isNewsLoading = false

            // Enrich ONLY the visible batch, ONLY when the News tab is viewed.
            // This used to enrich ALL articles on every commodity open. See
            // TickerDetailViewModel.fetchStockNews.
            if selectedTab == .news {
                await enrichVisibleArticles()
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
                    // Only real AI bullets — no raw-summary pseudo-bullet (parity with Updates).
                    allNewsArticles[i].summaryBullets = enriched.summaryBullets ?? []
                    allNewsArticles[i].sentiment = mapSentiment(enriched.sentiment)
                    allNewsArticles[i].aiProcessed = true
                    actuallyEnriched += 1
                }
            }
        }
        print("📰 [CommodityDetailVM] Merged \(actuallyEnriched)/\(enrichedArticles.count) enriched articles")
    }

    func loadMoreNews() {
        guard !isLoadingMoreNews, hasMoreNews else { return }
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

    // MARK: - Technical Analysis

    /// Re-run the technical fetch after a failure. Safe to call repeatedly.
    func retryTechnicalAnalysis() async {
        isTechnicalLoaded = false
        technicalUnavailableMessage = nil
        await fetchTechnicalAnalysis()
    }

    private func fetchTechnicalAnalysis() async {
        do {
            let dto = try await apiClient.request(
                endpoint: .getTechnicalAnalysis(ticker: commoditySymbol),
                responseType: TechnicalAnalysisDTO.self
            )
            self.technicalAnalysisData = dto.toDisplayModel()
            self.isTechnicalLoaded = true
            self.technicalUnavailableMessage = nil
            print("✅ [CommodityDetailVM] Got technical analysis — gauge: \(dto.gaugeValue)")
        } catch {
            print("⚠️ [CommodityDetailVM] Technical analysis failed: \(error)")
            // Do NOT fabricate a BUY gauge from sampleData — a hardcoded "Buy" signal
            // on a failed fetch is financial misinformation and leaks into the AI
            // context (contextForCurrentTab). Leave nil; the section stays empty.
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
                // Revert AND tell the user. The revert was always right; the silence was the
                // bug — in a release build a star that fills in and empties again is
                // indistinguishable from the app deciding the tap never happened.
                isFavorite = wasInWatchlist
                AppActions.shared.reportMutationFailure(
                    error,
                    action: wasInWatchlist
                        ? "remove \(self.commoditySymbol) from your watchlist"
                        : "add \(self.commoditySymbol) to your watchlist",
                    signInFeature: "save this commodity"
                )
            }
        }
    }

    private func checkWatchlistStatus() async {
        let generation = favoriteToggleGeneration
        do {
            let watchlist: [WatchlistItemDTO] = try await apiClient.request(
                endpoint: .getWatchlist,
                responseType: [WatchlistItemDTO].self
            )
            // Discard a snapshot that raced with a tap: it may predate the user's write
            // and would revert their star with no error shown.
            guard generation == self.favoriteToggleGeneration else {
                print("⏭️ [CommodityDetailVM] Watchlist snapshot discarded — user toggled during the fetch")
                return
            }
            self.isFavorite = watchlist.contains { $0.ticker.uppercased() == commoditySymbol.uppercased() }
        } catch {
            print("⚠️ [CommodityDetailVM] Watchlist check failed: \(error)")
        }
    }

    private struct WatchlistItemDTO: Codable {
        let ticker: String
    }

    // MARK: - User Actions

    func handleRelatedCommodityTap(_ commodity: RelatedTicker) {
        pendingTickerNavigation = SearchSelection(symbol: commodity.symbol, type: "commodity")
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
        // A news chip is an EQUITY symbol, not another commodity.
        pendingTickerNavigation = SearchSelection(symbol: ticker, type: "stock")
    }

    func handleSuggestionTap(_ suggestion: CommodityAISuggestion) {
        aiInputText = suggestion.text
        // …and SEND it. Without this the chip was a dead control: the text
        // appeared in the input box and nothing happened, while the same tap on
        // the Ticker / ETF / Index screens opens the chat. Matches
        // TickerDetailViewModel.handleSuggestionTap.
        handleAISend()
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
                    .map { a in
                        // sentiment is nil until AI-enriched — omit the tag then.
                        a.sentiment.map { "- \(a.headline) [\($0.rawValue)]" } ?? "- \(a.headline)"
                    }
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
        let formatters: [DateFormatter] = {
            // Fixed POSIX locale + UTC on every formatter. FMP's space-separated
            // "yyyy-MM-dd HH:mm:ss" is UTC; without an explicit timeZone it parses
            // in the DEVICE's zone, so the same shared cache row shows a different
            // time (and "Xh ago") here than on Updates / the other detail tabs —
            // a cross-screen news-parity break. Matches UpdatesDateParser.fmpSpaced.
            let posix = Locale(identifier: "en_US_POSIX")
            let utc = TimeZone(identifier: "UTC")
            let iso = DateFormatter()
            iso.locale = posix; iso.timeZone = utc
            iso.dateFormat = "yyyy-MM-dd'T'HH:mm:ssZ"
            let simple = DateFormatter()
            simple.locale = posix; simple.timeZone = utc
            simple.dateFormat = "yyyy-MM-dd HH:mm:ss"
            let dateOnly = DateFormatter()
            dateOnly.locale = posix; dateOnly.timeZone = utc
            dateOnly.dateFormat = "yyyy-MM-dd"
            return [iso, simple, dateOnly]
        }()
        for formatter in formatters {
            if let date = formatter.date(from: dateString) { return date }
        }
        return ISO8601DateFormatter().date(from: dateString)
    }
}
