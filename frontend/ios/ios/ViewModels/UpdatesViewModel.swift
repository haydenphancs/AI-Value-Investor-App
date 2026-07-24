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
    /// Errors from watchlist writes (Manage Assets). Kept SEPARATE from `error`:
    /// routing an "add ticker failed" through the feed's error state flipped a
    /// perfectly good timeline into a full-screen "Couldn't load the news".
    @Published var watchlistError: String?
    @Published var showFilterSheet: Bool = false

    /// Distinct source names present in the loaded feed — drives the filter
    /// sheet, instead of a hardcoded list that may match nothing.
    @Published private(set) var availableSources: [String] = []

    // MARK: - Private state

    /// Unfiltered articles for the selected scope.
    private var allNewsArticles: [NewsArticle] = []
    /// Per-scope cache so switching back to a tab is instant.
    /// Pagination state travels WITH the cached articles. Restoring the rows
    /// without the offset would make the next "load more" on a revisited tab
    /// re-request page 0 (duplicate rows) or skip ahead (a hole in the
    /// timeline), depending on which stale counter survived.
    private var feedCache: [String: (
        articles: [NewsArticle],
        insight: NewsInsightSummary?,
        offset: Int,
        hasMore: Bool
    )] = [:]

    private let apiClient: APIClient
    private var hasLoadedOnce = false
    /// Re-entrancy guard: `.task(id:)` can re-fire before the previous body ends.
    private var isLoadingInitial = false
    /// Guards against a stale in-flight response overwriting a newer tab's data.
    private var loadToken = UUID()
    /// Scope whose feed request is currently in flight, for duplicate-request dedup.
    private var inFlightScope: String?
    private var refreshPollTask: Task<Void, Never>?

    private let feedLimit = 50

    // MARK: - Pagination

    /// Whether the backend says more retained history exists for the current
    /// scope. Defaults false so a backend that predates pagination — or a scope
    /// with less than one page — never triggers a fetch loop.
    private var hasMorePages = false
    /// Guards against the scroll trigger firing repeatedly while a page is in
    /// flight. `onAppear` fires per row, so the last few rows would otherwise
    /// each launch their own request for the same offset.
    private var isLoadingMore = false
    /// Rows already requested. Kept separate from `allNewsArticles.count`, which
    /// shrinks when the backend drops unrenderable rows — paging off the
    /// rendered count would then re-request the ones that were dropped forever.
    private var loadedOffset = 0

    // MARK: - Enrichment

    /// Max un-enriched rows sent per background batch. Targets the reader's
    /// VISIBLE window (see `enrichVisibleWindow`), not the top of the list, so it
    /// is safe to be larger than before — every id is a row on/near the screen.
    /// The backend caps a request at 50.
    private let enrichBatchSize = 20
    /// How far AHEAD of the row that just appeared to reach for un-enriched rows,
    /// so summaries are ready a little before the reader scrolls onto them.
    private let enrichLookahead = 25
    /// Serialises the BACKGROUND window enrichment. `onAppear` fires per row, and
    /// without this the same un-enriched ids would be sent by overlapping batches.
    /// The on-TAP path (`summarizeArticle`) is intentionally NOT gated by this —
    /// a tap is high-intent and must respond immediately.
    private var isEnriching = false
    /// Rows from the end at which scrolling triggers the next page.
    private let prefetchThreshold = 5
    /// Debounce for scroll-driven work. `onAppear` fires in bursts — fast scroll,
    /// and again on every re-layout after an enrichment merge or a paginated
    /// append. Firing enrichment + pagination on EVERY callback spawned a storm of
    /// main-actor tasks, each running an O(n) regroup, which pegged the main thread
    /// (a UI FREEZE, worst on a cached feed whose pages return instantly). We
    /// coalesce a burst into ONE deferred pass keyed on the latest visible row.
    private var appearWorkTask: Task<Void, Never>?
    private var lastAppearedIndex = 0

    /// Articles the reader tapped that are being summarised on demand. Drives the
    /// per-card spinner. Per-article dedup so a double-tap fires one call.
    @Published var summarizingIDs: Set<String> = []

    // MARK: - Initialization

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        // Deliberately NO load here. `UpdatesView` is instantiated once at app
        // launch for all five tabs (see ContentView), so loading in init would
        // fire network calls for a screen the user may never open. The view
        // calls `loadIfNeeded()` when the tab first becomes active.
    }

    deinit { refreshPollTask?.cancel(); appearWorkTask?.cancel() }

    // MARK: - Lifecycle

    /// Called when the Updates tab becomes visible. Idempotent.
    func loadIfNeeded() async {
        guard !hasLoadedOnce, !isLoadingInitial else { return }
        isLoadingInitial = true
        defer { isLoadingInitial = false }
        await loadInitialData()
        // Latch on genuine completion — INCLUDING a legitimately empty scope —
        // but never on cancellation. `.task(id:)` cancels its body when the tab
        // switches away, which leaves `error == nil` and empty articles; the old
        // `!allNewsArticles.isEmpty` guard avoided latching there but also
        // re-fetched tabs+feed on every reactivation of a genuinely empty scope.
        // `Task.isCancelled` distinguishes "interrupted" from "empty result".
        if !Task.isCancelled && error == nil { hasLoadedOnce = true }
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
            self.watchlistError = appError.message
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
            self.watchlistError = appError.message
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

        // Dedup concurrent loads of the SAME scope. `loadTabs()` assigning
        // `selectedTab` fires the view's `.onChange` → `selectTab` → `loadFeed`,
        // which races the `loadFeed` in `loadInitialData`. The staleness token
        // below keeps the DATA correct, but without this guard the screen still
        // fires two identical requests on every cold open.
        if !force, inFlightScope == scope {
            print("⏭️ UpdatesVM: \(scope) already loading — skipping duplicate request")
            return
        }
        let token = UUID()
        loadToken = token
        inFlightScope = scope
        // Clear only if THIS load still owns the slot. On A→B→A, a stale A#1
        // response would otherwise clear the flag while A#2 is in flight, so the
        // dedup guard misses and A is fetched twice.
        defer { if loadToken == token { inFlightScope = nil } }
        refreshPollTask?.cancel()
        // Drop any pending scroll-driven work from the OUTGOING tab — its index
        // refers to the previous feed, and the token check would discard it anyway.
        appearWorkTask?.cancel()
        appearWorkTask = nil
        // Reset the enrichment throttle for the incoming tab. `isEnriching` is a
        // shared Bool; if the OUTGOING tab's enrich POST (seconds long) is still
        // in flight, the new tab's initial + debounced enrichment would both
        // early-return on `guard !isEnriching`, leaving its visible rows with no
        // sentiment/summary until a manual scroll. Any stale in-flight enrich is
        // still pinned to its own scope+token, so it can't mis-merge here.
        isEnriching = false
        // Clear per-card summarise spinners: `summarizingIDs` is keyed on bare
        // apiId, so a same-apiId card in the new scope would otherwise show a
        // phantom spinner.
        summarizingIDs.removeAll()

        if !force, let cached = feedCache[scope] {
            // Clear any error left by a PREVIOUSLY-failed scope. Without this a
            // stale `error` survives onto this valid cached scope, and the view
            // renders `errorState` (error != nil && groupedNews empty) instead of
            // this scope's own content or empty state. The non-cached path below
            // already clears it; the cached path must too.
            error = nil
            allNewsArticles = cached.articles
            insightSummary = cached.insight
            loadedOffset = cached.offset
            hasMorePages = cached.hasMore
            isLoadingMore = false
            applyFiltersAndGroup()
            // Must clear: returning to a tab that was cached EMPTY would
            // otherwise leave the shimmer up forever instead of showing the
            // empty state.
            isLoading = false
            print("✅ UpdatesVM: Served \(cached.articles.count) articles for \(scope) from memory")
            // Still enrich: a cached scope whose first enrich pass failed (or was
            // cut short) would otherwise keep its sentiment badges hidden until a
            // manual pull-to-refresh.
            if allNewsArticles.contains(where: { !$0.aiProcessed && $0.isEnrichable }) {
                await enrichVisibleWindow(around: 0, scope: scope, token: token)
            }
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
        // Reset pagination with the feed. Carrying the previous tab's offset
        // over would make the new scope's first "load more" skip its opening
        // rows — a silent hole in the timeline.
        loadedOffset = 0
        hasMorePages = false
        isLoadingMore = false

        do {
            let response: UpdatesFeedResponse = try await apiClient.request(
                endpoint: .getUpdatesFeed(scope: scope, limit: feedLimit),
                responseType: UpdatesFeedResponse.self
            )
            guard loadToken == token else {
                print("⏭️ UpdatesVM: Discarding stale feed response for \(scope)")
                // The newer load owns isLoading now — do NOT clear it here, or
                // this stale completion would hide the newer load's shimmer.
                return
            }

            let dtos = response.articles ?? []
            // Dedup by non-empty `apiId` so `stableID` is unique across the feed
            // (the timeline keys `ForEach` on it). Empty-`apiId` rows are all
            // kept — each has a unique UUID fallback.
            let articles = dedupedByApiID(dtos.compactMap { NewsArticle(dto: $0) })
            let dropped = dtos.count - articles.count
            if dropped > 0 {
                // Not silent: a spike here means the backend started emitting
                // rows iOS cannot render (missing headline / unparseable date)
                // or duplicate ids in one page.
                print("⚠️ UpdatesVM: Dropped \(dropped)/\(dtos.count) unrenderable/duplicate articles for \(scope)")
            }

            allNewsArticles = articles
            insightSummary = response.insight.flatMap { NewsInsightSummary(dto: $0) }
            applyFiltersAndGroup()
            // Page off what was REQUESTED, not what rendered: `dtos.count` may
            // exceed `articles.count` when rows are unrenderable, and paging off
            // the rendered count would re-request the dropped rows forever.
            loadedOffset = dtos.count
            hasMorePages = response.hasMore ?? false
            // Cache AFTER the pagination state is updated. Writing it above (with
            // the reset offset=0 / hasMore=false) meant every tab REVISIT restored
            // those dead values from cache → "load more" was permanently disabled
            // on any tab returned to (incl. Market after a single tab excursion).
            // `loadMoreIfNeeded` already writes the cache in this order.
            feedCache[scope] = (articles, insightSummary, loadedOffset, hasMorePages)
            isLoading = false

            print("""
            ✅ UpdatesVM: Loaded \(articles.count) articles for \(scope) \
            (cached: \(response.cached ?? false), \
            insight: \(insightSummary.map { $0.isAIGenerated ? "ai" : "fallback" } ?? "none"))
            """)

            await enrichVisibleWindow(around: 0, scope: scope, token: token)
            scheduleInsightPollIfNeeded(scope: scope, token: token)
        } catch is CancellationError {
            // Tab switched away mid-flight. Not a failure — show nothing.
            isLoading = false
            return
        } catch {
            guard loadToken == token else { return }
            if (error as? URLError)?.code == .cancelled {
                // Same as above: URLSession surfaces task cancellation as
                // URLError.cancelled, whose message is the literal "cancelled".
                // Rendering that as "Couldn't load the news / cancelled" blamed
                // the network for the user's own tab switch.
                isLoading = false
                return
            }
            let appError = AppError.from(error)
            self.error = appError.message
            isLoading = false
            // NO sample-data fallback. Fabricated headlines here would render as
            // real market news.
            print("⚠️ UpdatesVM: Failed to load feed for \(scope): \(appError.message)")
        }
    }

    // MARK: - Scroll-driven paging + enrichment

    /// Called from each timeline row's `onAppear`.
    ///
    /// Both the next page of history and the next enrichment batch hang off
    /// this: the reader approaching the end of the list is the only signal that
    /// they actually want more, and enrichment is a paid call that should not
    /// be spent on rows nobody scrolled to.
    func articleDidAppear(_ article: NewsArticle) {
        guard let index = newsArticles.firstIndex(where: { $0.id == article.id })
        else { return }
        lastAppearedIndex = index
        // DEBOUNCE: a burst of onAppear callbacks (fast scroll, or the reflow after
        // an enrichment merge / paginated append) collapses into ONE pass. Without
        // this, every callback spawned a task that ran a full regroup + maybe a
        // fetch, saturating the main actor → freeze. While a pass is scheduled,
        // later appears only update `lastAppearedIndex`.
        guard appearWorkTask == nil else { return }
        appearWorkTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 150_000_000)  // ~settle window
            guard let self, !Task.isCancelled else { return }
            self.appearWorkTask = nil               // allow the next burst to schedule
            guard let scope = self.selectedTab?.scope else { return }
            let idx = self.lastAppearedIndex
            let token = self.loadToken
            // Enrich the window the reader is ACTUALLY looking at (this row + a
            // lookahead), not the first N rows from the top.
            await self.enrichVisibleWindow(around: idx, scope: scope, token: token)
            // Paginate only near the end of the list.
            if idx >= self.newsArticles.count - self.prefetchThreshold {
                await self.loadMoreIfNeeded(scope: scope, token: token)
            }
        }
    }

    /// Fetch the next page of retained history and append it.
    private func loadMoreIfNeeded(scope: String, token: UUID) async {
        guard hasMorePages, !isLoadingMore, !isLoading else { return }
        isLoadingMore = true
        defer { isLoadingMore = false }

        do {
            let response: UpdatesFeedResponse = try await apiClient.request(
                endpoint: .getUpdatesFeed(
                    scope: scope, limit: feedLimit, offset: loadedOffset
                ),
                responseType: UpdatesFeedResponse.self
            )
            // The user switched tabs (or pulled to refresh) mid-flight —
            // appending now would splice this scope's history into another's.
            guard loadToken == token, selectedTab?.scope == scope else { return }

            let dtos = response.articles ?? []
            let page = dtos.compactMap { NewsArticle(dto: $0) }

            // De-dup by the backend id. `published_at` is not unique (FMP stamps
            // whole batches to the same minute) and a refresh between pages can
            // shift the window, so a non-empty id already on screen must never be
            // appended again — the timeline's `ForEach(id: \.stableID)` renders
            // garbled rows on a duplicate. Seed only NON-EMPTY ids: empty-`apiId`
            // rows carry a unique UUID and must all survive (the old
            // `Set(map { apiId })` lumped them under "" and dropped every
            // empty-id page row after the first).
            let seen = Set(allNewsArticles.map { $0.apiId }.filter { !$0.isEmpty })
            let fresh = dedupedByApiID(page, alreadySeen: seen)

            allNewsArticles.append(contentsOf: fresh)
            loadedOffset += dtos.count
            hasMorePages = response.hasMore ?? false
            applyFiltersAndGroup()
            feedCache[scope] = (allNewsArticles, insightSummary, loadedOffset, hasMorePages)

            print("""
            ✅ UpdatesVM: Page at offset \(response.offset ?? loadedOffset) for \(scope) \
            → +\(fresh.count) new (\(dtos.count - fresh.count) dupes), \
            total \(allNewsArticles.count), more: \(hasMorePages)
            """)
        } catch is CancellationError {
            return
        } catch {
            if (error as? URLError)?.code == .cancelled { return }
            // Deliberately NOT surfaced as `self.error`: the timeline already on
            // screen is valid, and replacing it with a full-screen failure
            // because page 3 did not load would destroy what the reader has.
            // Leave `hasMorePages` true so the next scroll retries.
            print("⚠️ UpdatesVM: Load-more failed for \(scope) at offset \(loadedOffset): \(AppError.from(error).message)")
        }
    }

    // MARK: - AI enrichment

    /// Enrich the un-enriched rows in the reader's current window (the row that
    /// just appeared, a couple behind it, and a lookahead ahead of it). This is
    /// the fix for "scrolled-to cards have no sentiment/summary": enrichment now
    /// follows the scroll position instead of always draining the top of the list.
    private func enrichVisibleWindow(around index: Int, scope: String, token: UUID) async {
        guard !isEnriching else { return }
        let list = newsArticles
        guard !list.isEmpty, index >= 0, index < list.count else { return }
        let lower = max(0, index - 2)
        let upper = min(list.count, index + enrichLookahead)
        let ids = list[lower..<upper]
            .filter { !$0.aiProcessed && $0.isEnrichable }
            .prefix(enrichBatchSize)
            .map { $0.apiId }
        guard !ids.isEmpty else { return }
        isEnriching = true
        defer { isEnriching = false }
        await requestEnrichment(ids: Array(ids), scope: scope, token: token, source: "window")
    }

    /// On-demand summary for a single tapped card. High-intent, so it bypasses the
    /// background `isEnriching` serialisation and shows a per-card spinner via
    /// `summarizingIDs`. This is what makes tapping an un-enriched card summarise
    /// it in-app instead of dumping the reader onto a (often paywalled) link.
    func summarizeArticle(_ article: NewsArticle) {
        guard article.isEnrichable, !article.aiProcessed else { return }
        guard !summarizingIDs.contains(article.apiId) else { return }
        guard let scope = selectedTab?.scope else { return }
        let token = loadToken
        summarizingIDs.insert(article.apiId)
        Task {
            defer { summarizingIDs.remove(article.apiId) }
            await requestEnrichment(
                ids: [article.apiId], scope: scope, token: token, source: "tap"
            )
        }
    }

    /// Shared POST /enrich + merge. Never surfaced as `self.error`: the timeline
    /// already on screen is valid, it just lacks AI bullets on some rows.
    private func requestEnrichment(
        ids: [String], scope: String, token: UUID, source: String
    ) async {
        guard !ids.isEmpty else { return }
        do {
            let response: EnrichUpdatesNewsResponse = try await apiClient.request(
                endpoint: .enrichUpdatesNews(scope: scope, articleIds: ids),
                responseType: EnrichUpdatesNewsResponse.self
            )
            // Two-part staleness check: the token alone can pass against a NEWER
            // tab, applying this scope's enrichment to the wrong feed. The scope
            // check pins it to the visible tab.
            guard loadToken == token, selectedTab?.scope == scope else { return }
            let merged = mergeEnrichment(response, scope: scope)
            print("✅ UpdatesVM: Enriched \(merged)/\(ids.count) articles for \(scope) (\(source))")
        } catch {
            print("⚠️ UpdatesVM: Enrichment (\(source)) failed for \(scope): \(AppError.from(error).message)")
        }
    }

    /// Merge enrichment DTOs into `allNewsArticles` by id. Returns how many rows
    /// changed. Only accepts an enrichment that actually produced something — the
    /// backend returns rows unchanged when Gemini degraded, and marking those
    /// `aiProcessed` would permanently hide the summary a later retry would supply.
    @discardableResult
    private func mergeEnrichment(_ response: EnrichUpdatesNewsResponse, scope: String) -> Int {
        let byId = Dictionary(
            (response.articles ?? []).map { ($0.id, $0) },
            uniquingKeysWith: { first, _ in first }
        )
        var merged = 0
        for i in allNewsArticles.indices {
            guard let dto = byId[allNewsArticles[i].apiId] else { continue }
            let bullets = dto.summaryBullets ?? []
            let processed = dto.aiProcessed ?? false
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
            feedCache[scope] = (allNewsArticles, insightSummary, loadedOffset, hasMorePages)
        }
        return merged
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
            feedCache[scope] = (allNewsArticles, card, loadedOffset, hasMorePages)
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

    // MARK: - Identity dedup

    /// Drop rows whose NON-EMPTY `apiId` already appeared, preserving order and
    /// keeping EVERY empty-`apiId` row (each carries a unique `UUID` fallback for
    /// identity). This guarantees `NewsArticle.stableID` is unique across the
    /// feed, so the timeline's `ForEach(id: \.stableID)` can never hit a
    /// duplicate key — a duplicate renders garbled rows and risks a crash. Seed
    /// `alreadySeen` with the non-empty ids already on screen (pagination); pass
    /// empty for a fresh load.
    private func dedupedByApiID(
        _ articles: [NewsArticle],
        alreadySeen: Set<String> = []
    ) -> [NewsArticle] {
        var seen = alreadySeen
        var result: [NewsArticle] = []
        result.reserveCapacity(articles.count)
        for article in articles {
            if article.apiId.isEmpty {
                result.append(article)                 // no server id → unique UUID identity
            } else if seen.insert(article.apiId).inserted {
                result.append(article)                 // first sighting of this server id
            }
        }
        return result
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
