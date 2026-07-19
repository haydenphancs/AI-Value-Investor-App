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
    /// Fast price+chart snapshot from `/overview/core`, painted the instant it
    /// arrives so the screen isn't blank while the full `tickerData` loads. Once
    /// `tickerData` (full) lands it supersedes this (the view prefers `tickerData`).
    @Published var coreData: TickerCoreData?
    @Published var newsArticles: [TickerNewsArticle] = []  // displayed (paginated)
    @Published var isNewsLoading: Bool = false
    @Published var hasMoreNews: Bool = false
    @Published var isLoadingMoreNews: Bool = false
    @Published var analystRatingsData: AnalystRatingsData?
    @Published var sentimentAnalysisData: SentimentAnalysisData?
    @Published var technicalAnalysisData: TechnicalAnalysisData?
    @Published var isAnalystLoaded: Bool = false
    @Published var isSentimentLoaded: Bool = false
    @Published var isTechnicalLoaded: Bool = false
    @Published var earningsData: EarningsData?
    @Published var growthData: GrowthSectionData?
    @Published var profitPowerData: ProfitPowerSectionData?
    @Published var signalOfConfidenceData: SignalOfConfidenceSectionData?
    @Published var revenueBreakdownData: RevenueBreakdownData?
    @Published var healthCheckData: HealthCheckSectionData?
    @Published var holdersData: HoldersData?
    @Published var technicalAnalysisDetailData: TechnicalAnalysisDetailData?
    @Published var isTechnicalDetailLoading: Bool = false
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: TickerDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .oneDay
    @Published var isFavorite: Bool = false
    /// Set once the user taps the star. The initial (async) watchlist check must not
    /// clobber a user toggle with a snapshot taken before the add/remove landed.
    private var userToggledFavorite = false
    @Published var aiInputText: String = ""
    @Published var pendingAIQuery: String?
    @Published var pendingTickerNavigation: String?

    // Chart settings
    @Published var chartSettings = ChartSettings()
    @Published var chartDataVersion: Int = 0
    @Published var chartEventDates: ChartEventDates?

    // Analysis tab state
    @Published var selectedMomentumPeriod: AnalystMomentumPeriod = .sixMonths
    @Published var selectedSentimentTimeframe: SentimentTimeframe = .last24h

    // MARK: - API Data (live from backend)
    @Published var stockDetail: StockDetail?
    @Published var stockQuote: StockQuote?

    // MARK: - Live Price
    let livePriceManager = LivePriceWebSocketManager()

    // MARK: - Private Properties

    private let tickerSymbol: String
    private let stockRepository: StockRepository
    private var cancellables = Set<AnyCancellable>()
    private var allNewsArticles: [TickerNewsArticle] = []  // full set from API
    private var newsDisplayCount: Int = 10
    private let newsPageSize: Int = 10
    private var chartRefreshTask: Task<Void, Never>?
    private var quotePollTask: Task<Void, Never>?
    /// Monotonic token for chart fetches. Each fetchChartData captures the value
    /// before awaiting and only applies its result if still current — so a slow
    /// response for a no-longer-selected range can't clobber a newer one
    /// (last-write-wins during rapid range/interval switching).
    private var chartRequestGen = 0
    /// True while the range observer assigns the range's default interval, so the
    /// interval observer doesn't ALSO re-fetch (one range change would otherwise fire
    /// two chart requests when the range crosses an interval boundary).
    private var suppressIntervalReload = false

    // MARK: - Initialization

    init(tickerSymbol: String, stockRepository: StockRepository? = nil) {
        self.tickerSymbol = tickerSymbol
        self.stockRepository = stockRepository ?? .shared

        // Observe chart range changes: auto-set default interval and fetch new chart data
        $selectedChartRange
            .dropFirst() // Skip initial value
            .removeDuplicates()
            .sink { [weak self] range in
                guard let self = self else { return }
                print("📈 TickerDetailVM: Chart range changed to \(range.rawValue)")
                // Assigning the interval fires the interval observer SYNCHRONOUSLY;
                // suppress its re-fetch so a range change drives exactly one chart
                // request (not two when the range crosses an interval boundary).
                self.suppressIntervalReload = true
                self.chartSettings.selectedInterval = range.defaultInterval
                self.suppressIntervalReload = false

                // Restart or stop chart refresh timer based on new range
                if range.defaultInterval.isIntraday && self.livePriceManager.isConnected {
                    self.startChartRefreshTimer()
                } else {
                    self.stopChartRefreshTimer()
                }

                Task { [weak self] in
                    guard let self = self else { return }
                    await self.fetchChartData(self.tickerSymbol, range: range)
                }
            }
            .store(in: &cancellables)

        // Observe interval changes and re-fetch chart data (manual interval picker)
        chartSettings.$selectedInterval
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] _ in
                guard let self = self else { return }
                // Skip the re-fetch the range observer already owns (see suppress flag).
                guard !self.suppressIntervalReload else { return }
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

        // Observe live price updates from WebSocket and apply to tickerData + chart
        livePriceManager.$livePrice
            .compactMap { $0 }
            .sink { [weak self] newPrice in
                guard let self = self, var data = self.tickerData else { return }
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

                self.tickerData = data
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

            // Prefetch: Fire sentiment API call immediately so it warms the cache
            // while Phase 1 loads. Phase 2's fetchAnalystAnalysis will hit the cache.
            Task { [weak self] in
                guard let self else { return }
                _ = try? await self.stockRepository.getSentimentAnalysis(ticker: ticker)
            }

            // Fire-and-forget: warm the report's persona-neutral collection cache
            // for this ticker so a later Generate Analysis skips the FMP fan-out.
            // Best-effort — must never block or affect the detail view.
            Task { [weak self] in
                guard let self else { return }
                try? await self.stockRepository.prewarmReportCollection(ticker: ticker)
            }

            // Fast core (parallel with Phase 1): paint price + chart the instant it
            // lands — reuses only the quote + intraday chart + profile, so it returns
            // in ~0.5s instead of waiting for the full overview's slow historical
            // aggregation. Guarded so a late core never overwrites the full model.
            Task { [weak self] in
                guard let self else { return }
                let ext = self.chartSettings.showExtendedHours && self.chartSettings.selectedInterval.isIntraday
                guard let core = try? await self.stockRepository.getStockOverviewCore(
                    ticker: ticker, range: self.selectedChartRange.rawValue,
                    interval: self.chartSettings.selectedInterval.rawValue,
                    extendedHours: ext
                ) else { return }
                if self.tickerData == nil {
                    self.coreData = core.toCoreData()
                    self.chartDataVersion += 1
                }
            }

            // Phase 1: Get price/chart data — show UI as soon as this arrives
            do {
                let useExtendedHours = self.chartSettings.showExtendedHours && self.chartSettings.selectedInterval.isIntraday
                // Range + interval the overview is fetched for, captured now (before
                // the await) so we can detect a selection change during the slow load.
                let requestedRange = self.selectedChartRange
                let requestedInterval = self.chartSettings.selectedInterval
                let requestedExtended = useExtendedHours
                let response = try await self.stockRepository.getStockOverview(
                    ticker: ticker, range: requestedRange.rawValue,
                    interval: requestedInterval.rawValue,
                    extendedHours: useExtendedHours
                )
                self.tickerData = response.toDisplayModel()
                self.chartDataVersion += 1
                // If the user changed the range, the interval, OR the extended-hours
                // toggle while the (slow) overview was loading — now possible because
                // the fast-core chart is interactive — the overview's chart is for the
                // OLD selection. Re-fetch so the chart matches the current selection
                // instead of silently reverting (the tickerData write above is not
                // gen-guarded).
                let currentExtended = self.chartSettings.showExtendedHours && self.chartSettings.selectedInterval.isIntraday
                if self.selectedChartRange != requestedRange
                    || self.chartSettings.selectedInterval != requestedInterval
                    || currentExtended != requestedExtended {
                    await self.fetchChartData(ticker, range: self.selectedChartRange)
                }
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
                print("📊 Fallback data sources: stockDetail=\(self.stockDetail != nil), stockQuote=\(self.stockQuote != nil)")
                if let d = self.stockDetail {
                    print("  profile: price=\(d.price as Any), beta=\(d.beta as Any), marketCap=\(d.marketCap as Any), lastDiv=\(d.lastDiv as Any), 52wkH=\(d.high52Week as Any)")
                }
                if let q = self.stockQuote {
                    print("  quote: price=\(q.price as Any), pe=\(q.pe as Any), eps=\(q.eps as Any), open=\(q.open as Any), vol=\(q.volume as Any)")
                }
                // Fetch real chart data for the fallback path
                await self.fetchChartData(ticker, range: self.selectedChartRange)
            }

            // Show UI immediately — price/chart/overview are ready
            self.isLoading = false

            // Start live price streaming + chart refresh if market is active
            if let status = self.tickerData?.marketStatus,
               MarketHoursUtil.shouldStreamLivePrice(for: status) {
                self.connectLivePrice()
                self.startChartRefreshTimer()
            }

            // Phase 2: Fetch supplementary data in parallel (non-blocking)
            await withTaskGroup(of: Void.self) { group in
                group.addTask { await self.fetchStockNews(ticker) }
                group.addTask { await self.fetchAnalystAnalysis(ticker) }
                group.addTask { await self.fetchChartEvents(ticker) }
                group.addTask { await self.fetchEarnings(ticker) }
                group.addTask { await self.fetchGrowth(ticker) }
                group.addTask { await self.fetchProfitPower(ticker) }
                group.addTask { await self.fetchRevenueBreakdown(ticker) }
                group.addTask { await self.fetchHealthCheck(ticker) }
                group.addTask { await self.fetchSignalOfConfidence(ticker) }
                group.addTask { await self.fetchHolders(ticker) }
                group.addTask { await self.checkWatchlistStatus() }
            }

            // If the feed is empty/failed, stay empty (TickerNewsContent shows its
            // honest "No News Available" state). NEVER seed sampleDataForTicker — those
            // are hardcoded APPLE headlines attributed to THIS ticker (e.g. NVDA would
            // show "Apple announces record Q4 earnings"), and they leak into the Cay AI
            // news context. Matches the no-sample-fallback policy of the other 4 VMs
            // and fetchEarnings/fetchGrowth.
        }
    }

    // MARK: - Live Price

    func connectLivePrice() {
        let token = KeychainService.shared.get("access_token")
        if let token = token {
            livePriceManager.connect(ticker: tickerSymbol, authToken: token)
        }

        // Fallback: if WebSocket doesn't connect within 5 seconds, start REST polling
        startQuotePollFallback(hasToken: token != nil)
    }

    func disconnectLivePrice() {
        livePriceManager.disconnect()
        stopChartRefreshTimer()
        stopQuotePoll()
    }

    // MARK: - REST Quote Polling Fallback

    /// Starts REST polling as a fallback if WebSocket doesn't connect.
    private func startQuotePollFallback(hasToken: Bool) {
        quotePollTask?.cancel()
        quotePollTask = Task { [weak self] in
            // If we had a token, give WebSocket 5 seconds to connect first
            if hasToken {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                guard !Task.isCancelled else { return }
                guard let self = self else { return }
                if self.livePriceManager.isConnected {
                    print("📡 TickerDetailVM: WebSocket connected, skipping REST polling")
                    return
                }
            }

            print("📡 TickerDetailVM: WebSocket not connected — starting REST quote polling for \(self?.tickerSymbol ?? "")")

            // Poll every 15 seconds
            while !Task.isCancelled {
                guard let self = self else { break }
                guard MarketHoursUtil.isMarketActive() else {
                    try? await Task.sleep(nanoseconds: 60_000_000_000)
                    continue
                }

                // Stop polling if WebSocket connected in the meantime
                if self.livePriceManager.isConnected {
                    print("📡 TickerDetailVM: WebSocket connected, stopping REST polling")
                    return
                }

                await self.pollQuotePrice()
                try? await Task.sleep(nanoseconds: 15_000_000_000) // 15 seconds
            }
        }
    }

    private func stopQuotePoll() {
        quotePollTask?.cancel()
        quotePollTask = nil
    }

    /// Fetches the latest quote via REST and updates tickerData.
    private func pollQuotePrice() async {
        do {
            let quote = try await stockRepository.getStockQuote(ticker: tickerSymbol)
            // The WebSocket may have connected DURING this fetch. It's the
            // authoritative + fresher source, so drop this (now-stale) REST quote
            // rather than clobbering the live price during the connect handoff.
            guard !self.livePriceManager.isConnected else { return }
            guard var data = self.tickerData, let price = quote.price else { return }

            data.currentPrice = price
            if let change = quote.change {
                data.priceChange = change
            }
            if let changePct = quote.changePercent {
                data.priceChangePercent = changePct
            }

            // Update last chart candle for intraday ranges
            if self.chartSettings.selectedInterval.isIntraday,
               !data.chartPricePoints.isEmpty {
                let lastIndex = data.chartPricePoints.count - 1
                let last = data.chartPricePoints[lastIndex]
                let updatedPoint = StockPricePoint(
                    date: last.date,
                    close: price,
                    open: last.open,
                    high: max(last.high ?? price, price),
                    low: min(last.low ?? price, price),
                    volume: last.volume
                )
                data.chartPricePoints[lastIndex] = updatedPoint
            }

            self.tickerData = data
            print("📡 TickerDetailVM: REST poll updated price to \(price)")
        } catch {
            print("⚠️ TickerDetailVM: REST poll failed: \(error)")
        }
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

                await self.fetchChartData(self.tickerSymbol, range: self.selectedChartRange)
            }
        }
    }

    private func stopChartRefreshTimer() {
        chartRefreshTask?.cancel()
        chartRefreshTask = nil
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

            // Show articles IMMEDIATELY with raw data
            self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            self.isNewsLoading = false

            // Enrich in background, then update displayed articles
            let unenrichedIds = self.newsArticles
                .filter { !$0.aiProcessed }
                .map { $0.apiId }
                .filter { !$0.isEmpty && !$0.hasPrefix("temp_") && !$0.hasPrefix("raw_") }

            if !unenrichedIds.isEmpty {
                await attemptEnrichment(ticker: ticker, articleIds: unenrichedIds)
                // Refresh displayed articles with enriched data
                self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            }
        } catch {
            print("⚠️ TickerDetailVM: Failed to fetch news for \(ticker): \(error)")
        }
        self.isNewsLoading = false
    }

    private enum AnalysisPayload: Sendable {
        case ratings(AnalystRatingsData?)
        case sentiment(SentimentAnalysisData?)
        case technical(TechnicalAnalysisData?)
    }

    private func fetchChartEvents(_ ticker: String) async {
        do {
            let events = try await stockRepository.getChartEvents(ticker: ticker)
            self.chartEventDates = events
            print("✅ TickerDetailVM: Got chart events for \(ticker) — \(events.earningsDates.count) earnings, \(events.dividendDates.count) dividends")
        } catch {
            print("⚠️ TickerDetailVM: Chart events failed for \(ticker): \(error)")
        }
    }

    private func fetchEarnings(_ ticker: String) async {
        do {
            let dto = try await stockRepository.getEarnings(ticker: ticker)
            self.earningsData = dto.toDisplayModel()
            print("✅ TickerDetailVM: Got earnings for \(ticker) — \(dto.epsQuarters.count) EPS quarters")
        } catch {
            print("⚠️ TickerDetailVM: Earnings failed for \(ticker): \(error)")
            // Leave nil on failure — do NOT substitute .sampleData (hardcoded Apple
            // figures). Placeholder data would render as THIS ticker's real financials
            // and leak into the AI chat context. An honest empty section is correct.
            self.earningsData = nil
        }
    }

    private func fetchGrowth(_ ticker: String) async {
        do {
            let dto = try await stockRepository.getGrowth(ticker: ticker)
            self.growthData = dto.toDisplayModel()
            print("✅ TickerDetailVM: Got growth for \(ticker)")
        } catch {
            print("⚠️ TickerDetailVM: Growth failed for \(ticker): \(error)")
            // No .sampleData fallback — see fetchEarnings. Fabricated growth curves
            // must never masquerade as this ticker's data or feed financialsContext.
            self.growthData = nil
        }
    }

    private func fetchProfitPower(_ ticker: String) async {
        do {
            let dto = try await stockRepository.getProfitPower(ticker: ticker)
            self.profitPowerData = dto.toDisplayModel()
            print("✅ TickerDetailVM: Got profit power for \(ticker)")
        } catch {
            print("⚠️ TickerDetailVM: Profit power failed for \(ticker): \(error)")
            // No .sampleData fallback — see fetchEarnings.
            self.profitPowerData = nil
        }
    }

    private func fetchHealthCheck(_ ticker: String) async {
        do {
            let dto = try await stockRepository.getHealthCheck(ticker: ticker)
            self.healthCheckData = dto.toDisplayModel()
            print("✅ TickerDetailVM: Got health check for \(ticker)")
        } catch {
            print("⚠️ TickerDetailVM: Health check failed for \(ticker): \(error)")
            // No .sampleData fallback — see fetchEarnings.
            self.healthCheckData = nil
        }
    }

    private func fetchSignalOfConfidence(_ ticker: String) async {
        do {
            let dto = try await stockRepository.getSignalOfConfidence(ticker: ticker)
            self.signalOfConfidenceData = dto.toDisplayModel()
            print("✅ TickerDetailVM: Got signal of confidence for \(ticker)")
        } catch {
            print("⚠️ TickerDetailVM: Signal of confidence failed for \(ticker): \(error)")
            // No .sampleData fallback — see fetchEarnings.
            self.signalOfConfidenceData = nil
        }
    }

    private func fetchHolders(_ ticker: String) async {
        do {
            let dto = try await stockRepository.getHolders(ticker: ticker)
            self.holdersData = dto.toDisplayModel()
            print("✅ TickerDetailVM: Got holders for \(ticker)")
        } catch {
            print("⚠️ TickerDetailVM: Holders failed for \(ticker): \(error)")
            self.holdersData = nil
        }
    }

    private func fetchRevenueBreakdown(_ ticker: String) async {
        do {
            let dto = try await stockRepository.getRevenueBreakdown(ticker: ticker)
            self.revenueBreakdownData = dto.toDisplayModel()
            print("✅ TickerDetailVM: Got revenue breakdown for \(ticker)")
        } catch {
            print("⚠️ TickerDetailVM: Revenue breakdown failed for \(ticker): \(error)")
            // No sample fallback — section just won't show if nil
        }
    }

    private func fetchAnalystAnalysis(_ ticker: String) async {
        // Fetch analyst + sentiment + technical in parallel — each updates UI as it arrives
        await withTaskGroup(of: AnalysisPayload.self) { group in
            group.addTask { [self] in
                do {
                    let dto = try await stockRepository.getAnalystAnalysis(ticker: ticker)
                    print("✅ TickerDetailVM: Got analyst analysis for \(ticker) — \(dto.totalAnalysts) analysts, consensus: \(dto.consensus)")
                    return await .ratings(dto.toDisplayModel())
                } catch {
                    print("⚠️ TickerDetailVM: Analyst analysis failed for \(ticker): \(error)")
                    return .ratings(nil)
                }
            }
            group.addTask { [self] in
                do {
                    let dto = try await stockRepository.getSentimentAnalysis(ticker: ticker)
                    print("✅ TickerDetailVM: Got sentiment for \(ticker) — mood: \(dto.moodScore)")
                    return await .sentiment(dto.toDisplayModel())
                } catch {
                    print("⚠️ TickerDetailVM: Sentiment analysis failed for \(ticker): \(error)")
                    return .sentiment(nil)
                }
            }
            group.addTask { [self] in
                do {
                    let dto = try await stockRepository.getTechnicalAnalysis(ticker: ticker)
                    print("✅ TickerDetailVM: Got technical analysis for \(ticker) — gauge: \(dto.gaugeValue), daily: \(dto.dailySignal.matchingIndicators)/\(dto.dailySignal.totalIndicators), weekly: \(dto.weeklySignal.matchingIndicators)/\(dto.weeklySignal.totalIndicators)")
                    return await .technical(dto.toDisplayModel())
                } catch {
                    print("⚠️ TickerDetailVM: Technical analysis failed for \(ticker): \(error)")
                    return .technical(nil)
                }
            }

            // Set each property as it arrives — UI updates progressively
            for await result in group {
                switch result {
                case .ratings(let data):
                    self.analystRatingsData = data
                    self.isAnalystLoaded = true
                case .sentiment(let data):
                    self.sentimentAnalysisData = data
                    self.isSentimentLoaded = true
                case .technical(let data):
                    self.technicalAnalysisData = data
                    self.isTechnicalLoaded = true
                }
            }
        }
    }

    func fetchTechnicalAnalysisDetail() {
        guard technicalAnalysisDetailData == nil, !isTechnicalDetailLoading else { return }
        isTechnicalDetailLoading = true

        Task { [weak self] in
            guard let self = self else { return }
            do {
                let dto = try await self.stockRepository.getTechnicalAnalysisDetail(ticker: self.tickerSymbol)
                self.technicalAnalysisDetailData = dto.toDisplayModel()
                print("✅ TickerDetailVM: Got technical analysis detail for \(self.tickerSymbol)")
            } catch {
                print("⚠️ TickerDetailVM: Technical analysis detail failed for \(self.tickerSymbol): \(error)")
                // Fall back to sample data so the sheet still shows something
                self.technicalAnalysisDetailData = TechnicalAnalysisDetailData.sampleData
            }
            self.isTechnicalDetailLoading = false
        }
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
        print("⭐ TickerDetailVM: toggleFavorite called — isFavorite was \(isFavorite)")
        userToggledFavorite = true // the user's intent now wins over the initial check
        let wasInWatchlist = isFavorite
        isFavorite.toggle() // optimistic UI update
        print("⭐ TickerDetailVM: isFavorite is now \(isFavorite)")

        Task { @MainActor in
            do {
                if wasInWatchlist {
                    try await APIClient.shared.request(
                        endpoint: .removeFromWatchlist(stockId: tickerSymbol)
                    )
                    print("✅ TickerDetailVM: Removed \(tickerSymbol) from watchlist")
                } else {
                    try await APIClient.shared.request(
                        endpoint: .addToWatchlist(stockId: tickerSymbol)
                    )
                    print("✅ TickerDetailVM: Added \(tickerSymbol) to watchlist")
                }
            } catch {
                print("⚠️ TickerDetailVM: Watchlist toggle failed for \(tickerSymbol): \(error)")
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
            // If the user already toggled the star, their optimistic state + write is
            // authoritative — this snapshot may predate that write and would wrongly
            // revert it. Only apply the server snapshot when the user hasn't acted.
            guard !self.userToggledFavorite else {
                print("⏭️ TickerDetailVM: Watchlist check skipped — user already toggled favorite")
                return
            }
            self.isFavorite = watchlist.contains { $0.ticker.uppercased() == tickerSymbol.uppercased() }
            print("✅ TickerDetailVM: Watchlist check — \(tickerSymbol) isFavorite=\(isFavorite)")
        } catch {
            print("⚠️ TickerDetailVM: Watchlist check failed: \(error)")
            // Leave isFavorite as default false
        }
    }

    /// Lightweight DTO for decoding watchlist items (only need ticker field)
    private struct WatchlistItemDTO: Codable {
        let ticker: String
    }

    func handleNotificationTap() {
        print("Notification settings for \(tickerSymbol)")
    }

    func handleWebsiteTap() {
        // Prefer API data for website
        let website = stockDetail?.website ?? tickerData?.companyProfile.website
        guard let site = website,
              let url = URL(string: site.hasPrefix("http") ? site : "https://\(site)") else { return }
        UIApplication.shared.open(url)
    }

    func handleRelatedTickerTap(_ ticker: RelatedTicker) {
        pendingTickerNavigation = ticker.symbol
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

    /// Build context string from signal of confidence data for AI chat enrichment
    var signalOfConfidenceContext: String? {
        guard let data = signalOfConfidenceData else { return nil }
        var parts: [String] = []
        parts.append(data.summary.formattedSummary)
        parts.append(data.summary.shareCountDescription)
        if let info = data.dividendInfo {
            parts.append("Dividend Status: \(info.status.rawValue)")
            parts.append("Buyback Status: \(info.buybackStatus.rawValue)")
            parts.append("5Y Avg Yield: \(info.formattedYield)")
        }
        return parts.joined(separator: " ")
    }

    func updateChartRange(_ range: ChartTimeRange) {
        selectedChartRange = range
        // Chart data fetching is handled by the Combine $selectedChartRange observer
    }

    // MARK: - Chart Data Fetching

    private func fetchChartData(_ ticker: String, range: ChartTimeRange) async {
        let rangeString = range.rawValue  // e.g. "3M", "1Y", "1D"
        print("📈 TickerDetailVM: Fetching chart data for \(ticker), range=\(rangeString)")
        chartRequestGen += 1
        let gen = chartRequestGen
        do {
            let intervalString = chartSettings.selectedInterval.rawValue
            let useExtendedHours = chartSettings.showExtendedHours && chartSettings.selectedInterval.isIntraday
            let chartResponse = try await stockRepository.getStockChart(ticker: ticker, range: rangeString, interval: intervalString, extendedHours: useExtendedHours)
            // Drop a stale chart response so rapid range/interval switching can't
            // apply a chart for a no-longer-selected range (last-write-wins).
            guard gen == self.chartRequestGen else { return }
            let pricePoints = chartResponse.prices
            print("✅ TickerDetailVM: Got \(pricePoints.count) chart data points for \(ticker)")
            if !pricePoints.isEmpty {
                if var currentData = self.tickerData {
                    let previousCount = currentData.chartPricePoints.count
                    // Update chart prices in-place
                    currentData.chartPricePoints = pricePoints
                    self.tickerData = currentData
                    // Only reset viewport when new candles appeared (not just values updated)
                    if pricePoints.count != previousCount {
                        self.chartDataVersion += 1
                    }
                } else if var core = self.coreData {
                    // Still on the fast-core placeholder (full overview not in yet):
                    // keep its chart in sync with the selected range pill so a range
                    // change during this window isn't silently dropped.
                    core.chartPricePoints = pricePoints
                    self.coreData = core
                    self.chartDataVersion += 1
                }
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
            website: stockDetail?.website ?? "N/A",
            sector: stockDetail?.sector ?? "N/A",
            industry: stockDetail?.industry ?? "N/A",
            sectorPerformance: 0.0
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
            performancePeriods: [],   // Honest empty — never seed .sampleData (fabricated
            snapshots: [],            // data would render as THIS ticker's real figures on
            sectorIndustry: sectorIndustry,
            companyProfile: companyProfile,
            relatedTickers: [],       // the /overview-failed fallback path). Sections hide when empty.
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
            // FMP lastDiv is already annualized
            let divYield = (lastDiv / price) * 100
            stats.append(KeyStatistic(label: "Dividends", value: String(format: "%.2f (%.2f%%)", lastDiv, divYield)))
        } else {
            stats.append(KeyStatistic(label: "Dividends", value: "--"))
        }

        return stats
    }

    // MARK: - Build Key Statistics Groups

    private func buildKeyStatisticsGroups() -> [KeyStatisticsGroup] {
        // Column 1: Price & Volume (quote primary, profile fallback)
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
                value: {
                    if let avgVol = stockQuote?.avgVolume ?? stockDetail?.avgVolume {
                        return formatLargeNumber(avgVol)
                    }
                    return "--"
                }()
            ),
            KeyStatistic(
                label: "Market Cap",
                value: {
                    if let cap = stockDetail?.marketCap ?? stockQuote?.marketCap {
                        return formatMarketCap(cap)
                    }
                    return "--"
                }()
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
                value: {
                    if let h = stockDetail?.high52Week ?? stockQuote?.yearHigh {
                        return String(format: "%.2f", h)
                    }
                    return "--"
                }()
            ),
            KeyStatistic(
                label: "52-Week Low",
                value: {
                    if let l = stockDetail?.low52Week ?? stockQuote?.yearLow {
                        return String(format: "%.2f", l)
                    }
                    return "--"
                }()
            ),
            KeyStatistic(
                label: "52-Week % Range",
                value: {
                    let h = stockDetail?.high52Week ?? stockQuote?.yearHigh
                    let l = stockDetail?.low52Week ?? stockQuote?.yearLow
                    if let high = h, let low = l, low > 0 {
                        let pctRange = ((high - low) / low) * 100
                        return String(format: "%.2f%%", pctRange)
                    }
                    return "--"
                }()
            )
        ]

        // Column 3: Valuation (quote + profile)
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
                // FMP lastDiv is already annualized
                let divYield = (lastDiv / price) * 100
                return String(format: "%.2f (%.2f%%)", lastDiv, divYield)
            }
            return "--"
        }()
        let peFwdValue: String = {
            if let peFwd = stockDetail?.peForward, peFwd > 0 { return String(format: "%.2f", peFwd) }
            return "--"
        }()
        let valuationStats: [KeyStatistic] = [
            KeyStatistic(label: "P/E (TTM)", value: peValue),
            KeyStatistic(label: "P/E (FWD)", value: peFwdValue),
            KeyStatistic(label: "EPS (TTM)", value: epsValue),
            KeyStatistic(label: "Dividends", value: divValue),
            KeyStatistic(label: "Beta", value: betaValue)
        ]

        // Column 4: Shares & Ownership
        let sharesValue: String = {
            if let shares = stockQuote?.sharesOutstanding { return formatLargeNumber(shares) }
            return "--"
        }()
        // These three ownership fields arrive ALREADY percent-scaled (0–100) from
        // GET /stocks/{ticker}: short_percent_float = spf*100, percent_insiders =
        // 100 − freeFloat (freeFloat is 0–100, verified live: AAPL 99.83), and
        // percent_institutional = ownershipPercent (verified live: AAPL 63.49). The
        // old `value < 1 ? value*100 : value` heuristic assumed a FRACTION and thus
        // 100x-inflated every sub-1% value — AAPL's 0.17% insiders rendered as 17%,
        // a 0.83% short float as 83%. Format the percent directly, no scaling.
        let shortFloatValue: String = {
            if let sp = stockDetail?.shortPercentFloat {
                return String(format: "%.2f%%", sp)
            }
            return "N/A"
        }()
        let floatValue: String = {
            if let f = stockDetail?.floatShares { return formatLargeNumber(f) }
            return "--"
        }()
        let insiderValue: String = {
            if let ins = stockDetail?.percentInsiders {
                return String(format: "%.2f%%", ins)
            }
            return "--"
        }()
        let instValue: String = {
            if let inst = stockDetail?.percentInstitutional {
                return String(format: "%.2f%%", inst)
            }
            return "--"
        }()
        let ownershipStats: [KeyStatistic] = [
            KeyStatistic(label: "Short % of Float", value: shortFloatValue),
            KeyStatistic(label: "Shares Outstanding", value: sharesValue),
            KeyStatistic(label: "Float", value: floatValue),
            KeyStatistic(label: "% Held by Insiders", value: insiderValue),
            KeyStatistic(label: "% Held Inst.", value: instValue)
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
        switch selectedTab {
        case .financials:
            return [
                TickerAISuggestion(text: "Break down the revenue"),
                TickerAISuggestion(text: "Is margin improving?"),
                TickerAISuggestion(text: "How healthy is the balance sheet?"),
                TickerAISuggestion(text: "Is revenue growing?")
            ]
        case .analysis:
            return [
                TickerAISuggestion(text: "What do analysts say?"),
                TickerAISuggestion(text: "What's the price target?"),
                TickerAISuggestion(text: "Any recent upgrades?"),
                TickerAISuggestion(text: "Technical outlook?")
            ]
        case .news:
            return [
                TickerAISuggestion(text: "Summarize recent news"),
                TickerAISuggestion(text: "Any catalysts ahead?"),
                TickerAISuggestion(text: "What's the sentiment?"),
                TickerAISuggestion(text: "Key risks?")
            ]
        case .holders:
            return [
                TickerAISuggestion(text: "Who are the top holders?"),
                TickerAISuggestion(text: "Any insider buying recently?"),
                TickerAISuggestion(text: "Hedge funds vs institutions?"),
                TickerAISuggestion(text: "Smart money sentiment?")
            ]
        default:
            return TickerAISuggestion.defaultSuggestions
        }
    }

    // MARK: - AI Context Builders

    /// Core stock facts — always included regardless of tab
    private var baseStockContext: String? {
        guard let td = tickerData else { return nil }
        var lines: [String] = []
        lines.append("Stock: \(td.symbol) (\(td.companyName))")
        lines.append("Price: \(td.formattedPrice) \(td.formattedChange) \(td.formattedChangePercent)")

        // Key Statistics — include ALL stats grouped for AI context
        if !td.keyStatisticsGroups.isEmpty {
            lines.append("Key Statistics:")
            for group in td.keyStatisticsGroups {
                let groupStr = group.statistics.map { "\($0.label): \($0.value)" }.joined(separator: " | ")
                lines.append("  \(groupStr)")
            }
        } else if !td.keyStatistics.isEmpty {
            let statsSummary = td.keyStatistics.map { "\($0.label): \($0.value)" }.joined(separator: " | ")
            lines.append("Key Statistics: \(statsSummary)")
        }

        lines.append("Sector: \(td.sectorIndustry.sector) > \(td.sectorIndustry.industry)")

        // Performance periods
        if !td.performancePeriods.isEmpty {
            let perfStr = td.performancePeriods.map { p in
                let sign = p.changePercent >= 0 ? "+" : ""
                return "\(p.label): \(sign)\(String(format: "%.1f", p.changePercent))%"
            }.joined(separator: ", ")
            lines.append("Performance: \(perfStr)")
        }

        return lines.joined(separator: "\n")
    }

    /// Overview tab context — snapshots and benchmark
    private var overviewContext: String? {
        guard let td = tickerData else { return nil }
        var parts: [String] = []

        // Snapshot ratings
        for snap in td.snapshots {
            let metricsStr = snap.metrics.map { "\($0.name): \($0.value)" }.joined(separator: ", ")
            parts.append("\(snap.category.rawValue): \(snap.rating.displayName) (\(metricsStr))")
        }

        // Benchmark comparison
        if let bench = td.benchmarkSummary {
            let sign = bench.avgAnnualReturn >= 0 ? "+" : ""
            parts.append("Avg Annual Return: \(sign)\(String(format: "%.1f", bench.avgAnnualReturn))% vs \(bench.benchmarkName): \(String(format: "%.1f", bench.spBenchmark))%")
        }

        // Signal of confidence if available
        if let soc = signalOfConfidenceContext {
            parts.append(soc)
        }

        return parts.isEmpty ? nil : parts.joined(separator: ". ")
    }

    /// Analysis tab context — analyst ratings, price targets, technicals
    private var analysisContext: String? {
        var parts: [String] = []

        if let ar = analystRatingsData {
            parts.append("Analyst Consensus: \(ar.consensus.rawValue) (\(ar.totalAnalysts) analysts)")
            parts.append("Price Target: Low $\(String(format: "%.0f", ar.priceTarget.lowPrice)), Avg $\(String(format: "%.0f", ar.priceTarget.averagePrice)), High $\(String(format: "%.0f", ar.priceTarget.highPrice))")
            parts.append("Target Upside: \(ar.formattedUpside)")

            let distStr = ar.distributions.map { "\($0.label): \($0.count)" }.joined(separator: ", ")
            parts.append("Ratings: \(distStr)")

            let recentActions = ar.actions.prefix(3)
            if !recentActions.isEmpty {
                let actStr = recentActions.map { "\($0.firmName) \($0.actionType.rawValue) to \($0.newRating.rawValue)" }.joined(separator: "; ")
                parts.append("Recent: \(actStr)")
            }
        }

        if let ta = technicalAnalysisData {
            parts.append("Technical: Daily \(ta.dailySignal.signal.rawValue), Weekly \(ta.weeklySignal.signal.rawValue), Overall \(ta.overallSignal.rawValue)")
        }

        return parts.isEmpty ? nil : parts.joined(separator: ". ")
    }

    /// Financials tab context — growth, margins, health check, earnings
    private var financialsContext: String? {
        var parts: [String] = []

        // Growth data — skip a metric whose latest YoY is "not meaningful" (nil)
        // rather than grounding the AI on a fabricated 0%.
        if let gd = growthData {
            if let yoy = gd.revenueAnnual.last?.yoyChangePercent {
                let sign = yoy >= 0 ? "+" : ""
                parts.append("Revenue Growth (YoY): \(sign)\(String(format: "%.1f", yoy))%")
            }
            if let yoy = gd.epsAnnual.last?.yoyChangePercent {
                let sign = yoy >= 0 ? "+" : ""
                parts.append("EPS Growth (YoY): \(sign)\(String(format: "%.1f", yoy))%")
            }
            if let yoy = gd.freeCashFlowAnnual.last?.yoyChangePercent {
                let sign = yoy >= 0 ? "+" : ""
                parts.append("FCF Growth (YoY): \(sign)\(String(format: "%.1f", yoy))%")
            }
        }

        // Profit margins
        if let pp = profitPowerData, let latest = pp.annualData.last {
            parts.append("Margins — Gross: \(String(format: "%.1f", latest.grossMargin))%, Operating: \(String(format: "%.1f", latest.operatingMargin))%, Net: \(String(format: "%.1f", latest.netMargin))%, FCF: \(String(format: "%.1f", latest.fcfMargin))%")
            parts.append("Sector Avg Net Margin: \(String(format: "%.1f", latest.sectorAverageNetMargin))%")
        }

        // Health check
        if let hc = healthCheckData {
            parts.append("Health Check: \(hc.ratingBadgeText)")
            let metricsSummary = hc.metrics.map { m in
                let statusLabel: String
                switch m.status {
                case .positive: statusLabel = "Pass"
                case .neutral: statusLabel = "Mixed"
                case .negative: statusLabel = "Fail"
                }
                return "\(m.type.rawValue): \(statusLabel)"
            }.joined(separator: ", ")
            parts.append("Metrics: \(metricsSummary)")
        }

        // Earnings beat/miss streak
        if let ed = earningsData {
            let reported = ed.epsQuarters.filter { $0.actualValue != nil }
            let beats = reported.filter { $0.result == .beat }.count
            let misses = reported.filter { $0.result == .missed }.count
            if !reported.isEmpty {
                parts.append("Earnings: \(beats) beats, \(misses) misses in last \(reported.count) quarters")
            }
            if let next = ed.nextEarningsDate {
                let formatter = DateFormatter()
                formatter.dateStyle = .medium
                parts.append("Next Earnings: \(formatter.string(from: next.date)) (\(next.statusText))")
            }
        }

        return parts.isEmpty ? nil : parts.joined(separator: ". ")
    }

    /// News tab context — recent headlines
    private var newsContext: String? {
        let recent = newsArticles.prefix(3)
        guard !recent.isEmpty else { return nil }
        var parts: [String] = []
        parts.append("Recent Headlines:")
        for article in recent {
            let sentiment = article.sentiment.displayName
            parts.append("- [\(sentiment)] \(article.headline)")
        }
        return parts.joined(separator: "\n")
    }

    /// Holders tab context — ownership + smart money flows
    var holdersContext: String? {
        guard let data = holdersData else { return nil }
        var parts: [String] = []

        // Ownership breakdown
        let bd = data.shareholderBreakdown
        parts.append("Ownership: Insiders \(bd.formattedInsiders), Institutions \(bd.formattedInstitutions), Public \(bd.formattedPublicOther)")
        if let topHolder = bd.top10Owners.institutions.first {
            parts.append("Top holder: \(topHolder.name) (\(topHolder.formattedPercent))")
        }

        // Insider activity summary
        let summary = data.recentActivities.insiderActivities.summary
        if summary.numBuyers > 0 || summary.numSellers > 0 {
            parts.append("Insider activity (\(summary.periodDescription)): \(summary.buyersLabel), \(summary.sellersLabel)")
        }

        // Smart money flow summaries
        let insiderFlow = data.insiderData.summary
        parts.append("Insider 12M Net Flow: \(insiderFlow.formattedNetFlow) (\(insiderFlow.isPositive ? "Bullish" : "Bearish"))")

        // `hedgeFundsData` = FMP 13F institutional ownership; UI label "Institutions".
        let hfFlow = data.hedgeFundsData.summary
        parts.append("Institutional 12M Net Flow: \(hfFlow.formattedNetFlow) (\(hfFlow.isPositive ? "Bullish" : "Bearish"))")

        let congressFlow = data.congressData.summary
        parts.append("Congress 12M Net Flow: \(congressFlow.formattedNetFlow) (\(congressFlow.isPositive ? "Bullish" : "Bearish"))")

        return parts.joined(separator: ". ")
    }

    /// Build context string for the current tab to inject into AI chat
    var contextForCurrentTab: String? {
        var sections: [String] = []

        if let base = baseStockContext {
            sections.append(base)
        }

        switch selectedTab {
        case .overview:
            if let ctx = overviewContext { sections.append(ctx) }
        case .analysis:
            if let ctx = analysisContext { sections.append(ctx) }
        case .financials:
            if let ctx = financialsContext { sections.append(ctx) }
        case .news:
            if let ctx = newsContext { sections.append(ctx) }
        case .holders:
            if let ctx = holdersContext { sections.append(ctx) }
        }

        sections.append("User is viewing the \(selectedTab.rawValue) tab.")

        return sections.isEmpty ? nil : sections.joined(separator: "\n\n")
    }
}
