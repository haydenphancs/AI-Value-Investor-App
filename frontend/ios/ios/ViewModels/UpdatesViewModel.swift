//
//  UpdatesViewModel.swift
//  ios
//
//  ViewModel for the Updates/News screen — MVVM Architecture
//
//  Data flow (all real, no mock data):
//    GET  /api/v1/updates/tabs            → filter pills (watchlist + Market)
//    GET  /api/v1/updates/feed?scope=…    → timeline + AI Insights card, one call
//    POST /api/v1/updates/news/enrich     → AI bullets + sentiment for visible rows
//
//  Two-phase render, mirroring TickerDetailViewModel's proven news pattern:
//  articles appear IMMEDIATELY from the cache-backed feed (shimmer ends there),
//  then AI enrichment merges in behind them. The user never waits on an LLM.
//
//  NOTE ON FABRICATION: this screen previously shipped 14 invented headlines and
//  3 invented "AI summaries" (Tesla delivery figures, Apple adoption rates) that
//  rendered as real market data. Nothing here may fall back to sample data on
//  failure — an honest empty state is the correct degraded behaviour.
//

import Foundation
import Combine

@MainActor
final class UpdatesViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var filterTabs: [NewsFilterTab] = []
    @Published var selectedTab: NewsFilterTab?
    @Published var insightSummary: NewsInsightSummary?
    @Published var newsArticles: [NewsArticle] = []
    @Published var groupedNews: [GroupedNews] = []
    @Published var filterOptions: NewsFilterOptions = .default {
        didSet { applyFiltersAndGroup() }
    }
    @Published var isLoading: Bool = false
    @Published var isRefreshing: Bool = false
    @Published var error: String?
    @Published var showFilterSheet: Bool = false

    /// Distinct source names present in the loaded feed — drives the filter
    /// sheet, instead of a hardcoded list that may match nothing.
    @Published private(set) var availableSources: [String] = []

    // MARK: - Private state

    /// Unfiltered articles for the selected scope.
    private var allNewsArticles: [NewsArticle] = []
    /// Per-scope cache so switching back to a tab is instant.
    private var feedCache: [String: (articles: [NewsArticle], insight: NewsInsightSummary?)] = [:]

    private let apiClient: APIClient
    private var hasLoadedOnce = false
    /// Guards against a stale in-flight response overwriting a newer tab's data.
    private var loadToken = UUID()
    private var refreshPollTask: Task<Void, Never>?

    private let feedLimit = 50

    // MARK: - Initialization

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        // Deliberately NO load here. `UpdatesView` is instantiated once at app
        // launch for all five tabs (see ContentView), so loading in init would
        // fire network calls for a screen the user may never open. The view
        // calls `loadIfNeeded()` when the tab first becomes active.
    }

    deinit { refreshPollTask?.cancel() }

    // MARK: - Lifecycle

    /// Called when the Updates tab becomes visible. Idempotent.
    func loadIfNeeded() async {
        guard !hasLoadedOnce else { return }
        hasLoadedOnce = true
        await loadInitialData()
    }

    private func loadInitialData() async {
        isLoading = true
        error = nil
        await loadTabs()
        if let tab = selectedTab {
            await loadFeed(for: tab, force: false)
        }
        isLoading = false
    }

    func refresh() async {
        isRefreshing = true
        error = nil
        // Drop the per-scope cache so pull-to-refresh actually re-fetches.
        feedCache.removeAll()
        await loadTabs()
        if let tab = selectedTab {
            await loadFeed(for: tab, force: true)
        }
        isRefreshing = false
    }

    func selectTab(_ tab: NewsFilterTab) {
        selectedTab = tab
        Task { await loadFeed(for: tab, force: false) }
    }

    func openFilterOptions() {
        showFilterSheet = true
    }

    // MARK: - Watchlist writes (Manage Assets sheet)

    /// Add a ticker to the real watchlist, then refresh the pills. Previously
    /// this only appended a local row, so the tab vanished on next launch.
    func addTicker(_ symbol: String) async {
        let ticker = symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else { return }
        guard !filterTabs.contains(where: { $0.scope == ticker }) else {
            print("⏭️ UpdatesVM: \(ticker) already in tabs")
            return
        }
        do {
            try await apiClient.request(endpoint: .addToWatchlist(stockId: ticker))
            print("✅ UpdatesVM: Added \(ticker) to watchlist")
            await loadTabs()
        } catch {
            let appError = AppError.from(error)
            self.error = appError.message
            print("⚠️ UpdatesVM: Failed to add \(ticker): \(appError.message)")
        }
    }

    func removeTicker(_ symbol: String) async {
        let ticker = symbol.uppercased()
        do {
            try await apiClient.request(endpoint: .removeFromWatchlist(stockId: ticker))
            print("✅ UpdatesVM: Removed \(ticker) from watchlist")
            feedCache.removeValue(forKey: ticker)
            await loadTabs()
            // The removed tab may have been the selected one.
            if let tab = selectedTab, !filterTabs.contains(where: { $0.scope == tab.scope }) {
                selectedTab = filterTabs.first
            }
            if let tab = selectedTab { await loadFeed(for: tab, force: false) }
        } catch {
            let appError = AppError.from(error)
            self.error = appError.message
            print("⚠️ UpdatesVM: Failed to remove \(ticker): \(appError.message)")
        }
    }

    // MARK: - Tabs

    private func loadTabs() async {
        // Remember the selection by SCOPE. The old code rebuilt tabs with fresh
        // UUIDs and reset selection to `.first`, so every refresh yanked the
        // user back to the Market tab.
        let previousScope = selectedTab?.scope

        do {
            let response: UpdatesTabsResponse = try await apiClient.request(
                endpoint: .getUpdatesTabs,
                responseType: UpdatesTabsResponse.self
            )
            let tabs = response.tabs.map { NewsFilterTab(dto: $0) }
            guard !tabs.isEmpty else {
                print("⚠️ UpdatesVM: /updates/tabs returned no tabs")
                if filterTabs.isEmpty { filterTabs = [Self.marketTabFallback] }
                selectedTab = selectedTab ?? filterTabs.first
                return
            }
            filterTabs = tabs
            selectedTab = tabs.first { $0.scope == previousScope } ?? tabs.first
            print("✅ UpdatesVM: Loaded \(tabs.count) tabs (selected: \(selectedTab?.scope ?? "none"))")
        } catch {
            let appError = AppError.from(error)
            print("⚠️ UpdatesVM: Failed to load tabs: \(appError.message)")
            // The Market feed is still usable without the watchlist pills, so
            // degrade to a single tab rather than showing an empty screen.
            if filterTabs.isEmpty {
                filterTabs = [Self.marketTabFallback]
                selectedTab = filterTabs.first
            }
        }
    }

    private static var marketTabFallback: NewsFilterTab {
        NewsFilterTab(
            title: "Market",
            ticker: nil,
            changePercent: nil,
            isMarketTab: true,
            scope: UpdatesScope.market
        )
    }

    // MARK: - Feed

    private func loadFeed(for tab: NewsFilterTab, force: Bool) async {
        let scope = tab.scope
        let token = UUID()
        loadToken = token
        refreshPollTask?.cancel()

        if !force, let cached = feedCache[scope] {
            allNewsArticles = cached.articles
            insightSummary = cached.insight
            applyFiltersAndGroup()
            print("✅ UpdatesVM: Served \(cached.articles.count) articles for \(scope) from memory")
            return
        }

        // Clear immediately so the previous tab's news never shows under the new
        // tab's title while the request is in flight.
        allNewsArticles = []
        newsArticles = []
        groupedNews = []
        insightSummary = nil
        isLoading = true
        error = nil

        do {
            let response: UpdatesFeedResponse = try await apiClient.request(
                endpoint: .getUpdatesFeed(scope: scope, limit: feedLimit),
                responseType: UpdatesFeedResponse.self
            )
            guard loadToken == token else {
                print("⏭️ UpdatesVM: Discarding stale feed response for \(scope)")
                return
            }

            let dtos = response.articles ?? []
            let articles = dtos.compactMap { NewsArticle(dto: $0) }
            let dropped = dtos.count - articles.count
            if dropped > 0 {
                // Not silent: a spike here means the backend started emitting
                // rows iOS cannot render (missing headline / unparseable date).
                print("⚠️ UpdatesVM: Dropped \(dropped)/\(dtos.count) unrenderable articles for \(scope)")
            }

            allNewsArticles = articles
            insightSummary = response.insight.flatMap { NewsInsightSummary(dto: $0) }
            applyFiltersAndGroup()
            feedCache[scope] = (articles, insightSummary)
            isLoading = false

            print("""
            ✅ UpdatesVM: Loaded \(articles.count) articles for \(scope) \
            (cached: \(response.cached ?? false), \
            insight: \(insightSummary.map { $0.isAIGenerated ? "ai" : "fallback" } ?? "none"))
            """)

            await enrichVisibleArticles(scope: scope, token: token)
            scheduleInsightPollIfNeeded(scope: scope, token: token)
        } catch {
            guard loadToken == token else { return }
            let appError = AppError.from(error)
            self.error = appError.message
            isLoading = false
            // NO sample-data fallback. Fabricated headlines here would render as
            // real market news.
            print("⚠️ UpdatesVM: Failed to load feed for \(scope): \(appError.message)")
        }
    }

    // MARK: - AI enrichment

    private func enrichVisibleArticles(scope: String, token: UUID) async {
        let ids = allNewsArticles
            .filter { !$0.aiProcessed && $0.isEnrichable }
            .prefix(20)                       // one batch; the rest enrich on refresh
            .map { $0.apiId }
        guard !ids.isEmpty else { return }

        do {
            let response: EnrichUpdatesNewsResponse = try await apiClient.request(
                endpoint: .enrichUpdatesNews(scope: scope, articleIds: Array(ids)),
                responseType: EnrichUpdatesNewsResponse.self
            )
            guard loadToken == token else { return }

            let byId = Dictionary(
                (response.articles ?? []).map { ($0.id, $0) },
                uniquingKeysWith: { first, _ in first }
            )
            var merged = 0
            for i in allNewsArticles.indices {
                guard let dto = byId[allNewsArticles[i].apiId] else { continue }
                let bullets = dto.summaryBullets ?? []
                let processed = dto.aiProcessed ?? false
                // Only accept an enrichment that actually produced something.
                // The backend returns rows unchanged when Gemini degraded, and
                // marking those `aiProcessed` would permanently hide the real
                // summary that a later retry would have supplied.
                guard processed || !bullets.isEmpty else { continue }
                allNewsArticles[i].summaryBullets = bullets
                allNewsArticles[i].aiProcessed = true
                if let s = NewsSentiment(backend: dto.sentiment) {
                    allNewsArticles[i].sentiment = s
                }
                merged += 1
            }
            if merged > 0 {
                applyFiltersAndGroup()
                feedCache[scope] = (allNewsArticles, insightSummary)
            }
            print("✅ UpdatesVM: Enriched \(merged)/\(ids.count) articles for \(scope)")
        } catch {
            // Non-fatal: the timeline still renders, just without AI bullets.
            print("⚠️ UpdatesVM: Enrichment failed for \(scope): \(AppError.from(error).message)")
        }
    }

    /// When the backend served the deterministic fallback card it also flags
    /// `refreshing` — the sweeper is producing a real one. Re-check a couple of
    /// times, then stop. Bounded on purpose: an unbounded poll would spin
    /// forever if the sweeper never gets to this scope.
    private func scheduleInsightPollIfNeeded(scope: String, token: UUID) {
        guard insightSummary?.isRefreshing == true else { return }
        refreshPollTask?.cancel()
        refreshPollTask = Task { [weak self] in
            for delay in [10.0, 45.0] {
                try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                if Task.isCancelled { return }
                guard let self, await self.loadToken == token else { return }
                let done = await self.repollInsight(scope: scope, token: token)
                if done { return }
            }
        }
    }

    /// Returns true when a real AI card arrived (stop polling).
    private func repollInsight(scope: String, token: UUID) async -> Bool {
        do {
            let response: UpdatesFeedResponse = try await apiClient.request(
                endpoint: .getUpdatesFeed(scope: scope, limit: feedLimit),
                responseType: UpdatesFeedResponse.self
            )
            guard loadToken == token,
                  let dto = response.insight,
                  let card = NewsInsightSummary(dto: dto) else { return false }
            insightSummary = card
            feedCache[scope] = (allNewsArticles, card)
            if card.isAIGenerated {
                print("✅ UpdatesVM: AI insight arrived for \(scope)")
                return true
            }
            return false
        } catch {
            print("⚠️ UpdatesVM: Insight re-poll failed for \(scope): \(AppError.from(error).message)")
            return true   // stop polling on error rather than hammering
        }
    }

    // MARK: - Filtering + grouping

    private func applyFiltersAndGroup() {
        newsArticles = allNewsArticles.filter { filterOptions.matches($0) }
        availableSources = Array(
            Set(allNewsArticles.map { $0.source.displayName })
        ).sorted()
        groupNewsArticles()
    }

    private func groupNewsArticles() {
        var groups: [String: [NewsArticle]] = [:]
        for article in newsArticles {
            groups[article.sectionTitle, default: []].append(article)
        }

        // Sort by the newest article in each group. The previous comparator
        // compared section TITLES lexicographically, so "Sep 3, 2026" sorted
        // above "Dec 28, 2026" — older news rendered above newer.
        groupedNews = groups
            .map { title, articles in
                (title: title, articles: articles.sorted { $0.publishedAt > $1.publishedAt })
            }
            .sorted { lhs, rhs in
                let l = lhs.articles.first?.publishedAt ?? .distantPast
                let r = rhs.articles.first?.publishedAt ?? .distantPast
                return l > r
            }
            .map { GroupedNews(sectionTitle: $0.title, articles: $0.articles) }
    }
}
