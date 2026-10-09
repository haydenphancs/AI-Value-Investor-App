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
//  INSTANT FIRST PAINT (2026-10-08). Two things put rows on the first visible frame:
//    1. The on-device snapshot (`UpdatesFeedSnapshot`, `AccountSnapshotStore`): the last LIVE
//       Market feed, first page, of THIS account, at most 96 h old. `prepareSnapshot()` reads
//       it from disk at tab mount (never a request) and seeds DISPLAY state only, labelled
//       "News · Updated <time>"; the live load replaces it in place. It is not live data:
//       it never enters `feedCache`, never latches `hasLoadedOnce`, never selects a chip and
//       is never enriched (a paid call) — and the view renders it only on the active tab.
//    2. The first open fetches the Market feed BESIDE /updates/tabs, not after it: the Market
//       scope needs nothing from /tabs.
//  The first load belongs to the ViewModel (`initialLoadTask`), so leaving the tab mid-load no
//  longer cancels it, and AI enrichment runs detached (`enrichTask`), so a load or a
//  pull-to-refresh ends when the rows paint, not after the model answers. A load that finishes
//  behind another tab starts no paid work until the tab is on screen (`deferredPostPaint`).
//

import Foundation
import Combine
import OSLog

@MainActor
final class UpdatesViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var filterTabs: [NewsFilterTab] = []
    @Published var selectedTab: NewsFilterTab?
    @Published var insightSummary: NewsInsightSummary?
    @Published var newsArticles: [NewsArticle] = []
    @Published var groupedNews: [GroupedNews] = []
    /// The News filter the user SAVED — written only by the filter sheet's Apply (or Reset),
    /// restored in `init` (TestFlight 1.0 (9): "set up once and permanently keep them") and
    /// re-read from the store in `handleIdentityChange` (a session end clears it — see there).
    ///
    /// Not what the timeline applies: that is `effectiveFilterOptions`, cut down to the
    /// publishers in the loaded feed. Keep the two apart — writing the narrowed copy back here
    /// would silently delete a publisher the user picked on another ticker's feed.
    @Published var filterOptions: NewsFilterOptions = .default {
        didSet {
            // Not for the identity handler's re-read: a restore never writes the store back.
            if !isRereadingSavedFilter { filterOptions.save() }
            applyFiltersAndGroup()
        }
    }
    /// True only while `handleIdentityChange` re-reads the saved filter into `filterOptions`.
    private var isRereadingSavedFilter = false

    /// The filter the timeline, the chip and the empty-state copy actually use: the saved one,
    /// with sources this feed does not carry dropped (`NewsFilterOptions.restrictingSources`).
    /// Re-derived on every load, page and filter change, so a source drops in once a page
    /// that carries it arrives — the chip and the sheet's checkmarks always describe exactly
    /// what is narrowing the list, never a filter the user cannot see.
    var effectiveFilterOptions: NewsFilterOptions {
        filterOptions.restrictingSources(to: availableSources)
    }
    /// Starts TRUE: until the first load decides otherwise, the honest first frame is the
    /// skeleton (or the snapshot), never the empty state's "No recent stories".
    @Published var isLoading: Bool = true
    @Published var isRefreshing: Bool = false
    @Published var error: String?
    /// Errors from watchlist writes (Manage Assets). Kept SEPARATE from `error`:
    /// routing an "add ticker failed" through the feed's error state flipped a
    /// perfectly good timeline into a full-screen "Couldn't load the news".
    @Published var watchlistError: String?
    @Published var showFilterSheet: Bool = false

    /// The feed is empty because there is no account, not because the load broke. Kept as a
    /// FLAG rather than folded into `error`, for the reason the whole pass exists: the moment
    /// `AppError.signInRequired` is flattened to its `.message` the view can no longer tell
    /// "you need an account" from "the network died", and renders the wrong affordance — here,
    /// a "Try Again" button that re-fires a request `APIClient` refuses before it leaves the
    /// device, forever.
    ///
    /// ⚠️ A SNAPSHOT taken during `loadFeed`, not a live read of auth. Anything that changes
    /// the identity must re-run the load — `handleIdentityChange` does.
    @Published private(set) var requiresSignIn: Bool = false

    /// A credential is stored but not armed yet: "Reconnecting…", never the sign-in prompt
    /// (auth.md §5).
    @Published private(set) var isReconnecting: Bool = false

    // MARK: - On-device snapshot

    /// When the rows on screen were SAVED, while they are the on-device snapshot rather than a
    /// live answer. nil once any live (or in-memory cached) feed paints, and whenever the
    /// snapshot is dropped. The header reads "News · Updated <time>" while it is set.
    @Published private(set) var snapshotSavedAt: Date?
    /// A live or cached feed of THIS identity has painted. Until then a snapshot may be seeded;
    /// after it, never again (a seed would overwrite a live answer).
    @Published private(set) var hasShownFeed = false

    /// "Updated 4:02 PM" / "Updated Sep 28, 4:02 PM" while a snapshot is on screen. One wording
    /// source for every tab (`AccountSnapshotPolicy.updatedLabel`).
    var snapshotUpdatedLabel: String? {
        snapshotSavedAt.map { AccountSnapshotPolicy.updatedLabel(savedAt: $0) }
    }

    /// The live refresh of an on-screen snapshot failed (not a refusal): keep the stories, say
    /// so beside them. The gate flags are checked HERE so the view's `body` never names them
    /// (the account-gate tests read the branch order from their first mention there), and a
    /// saved filter that hides every snapshot row gets the full error state instead.
    var showsSnapshotRefreshFailure: Bool {
        snapshotSavedAt != nil && error != nil && !requiresSignIn && !isReconnecting && !groupedNews.isEmpty
    }

    /// True for an Insights card that came from the snapshot. "Ask Cay AI" is withheld from it
    /// (on the card and in its detail sheet): the chat is grounded on the server's CURRENT card,
    /// which may not hold the bullet the user is reading, and the first send costs a credit.
    func isSnapshotInsight(_ summary: NewsInsightSummary) -> Bool {
        guard let seeded = snapshotInsightID else { return false }
        return summary.id == seeded
    }

    // MARK: - Active group + plan gate

    /// Name of the active group the pills came from, so the Manage sheet can title itself
    /// with the same word Home and Tracking use. Nil when the user has no active group.
    @Published private(set) var groupName: String?
    /// How many of the group's tickers the user's plan is hiding. Drives the single
    /// "+N more" upsell chip; 0 means render nothing at all.
    @Published private(set) var lockedTickerCount: Int = 0
    /// The plan that would unlock them, straight from the server so the client never
    /// carries a second copy of the tier ladder.
    @Published private(set) var tierRequiredForMoreTickers: String?
    /// Presents the plan paywall. A PLAN gate, so `PaywallView` — not the credits-driven
    /// `BuyCreditsView` route, which answers a 402 with a one-tap top-up.
    @Published var showPaywall: Bool = false

    /// Copy for the upsell chip. "Max" is the display name for the `premium` tier key.
    var lockedTickerLabel: String {
        "+\(lockedTickerCount) more"
    }

    /// Tabs the chip strip renders — everything the plan lets the user open.
    ///
    /// `filterTabs` carries the WHOLE group, locked entries included, because the
    /// Manage-Assets sheet is fed from it and managing your own watchlist is free. Only
    /// the strip filters; conflating the two is what made a Free user's sheet show one
    /// ticker out of twenty with no way to remove the rest.
    var openableTabs: [NewsFilterTab] {
        filterTabs.filter { !$0.isLocked }
    }

    /// Every ticker in the group, locked or not — the Manage-Assets sheet's source, and
    /// the set the add-duplicate guard has to test against.
    var manageableTickers: [NewsFilterTab] {
        filterTabs.filter { !$0.isMarketTab }
    }

    /// Whether an upgrade would actually reveal anything. `lockedTickerCount > 0` alone is
    /// NOT the test: a top-tier subscriber whose group exceeds the ceiling has a positive
    /// locked count and `tier_required == nil`, and showing them a lock chip opens a
    /// paywall whose only tappable option is a DOWNGRADE.
    var showsUpgradeChip: Bool {
        lockedTickerCount > 0 && tierRequiredForMoreTickers != nil
    }

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
    /// This account's on-device snapshot. Always `UpdatesFeedSnapshotStore.shared`, assigned
    /// once in `init`; memory-only in the DEBUG screenshot mode (the store's own init).
    private let snapshotStore: UpdatesFeedSnapshotStore
    /// The store epoch the snapshot on screen was seeded under. A seed whose epoch has moved
    /// (another owner bound, a session end, Clear Cache) is dropped at the next prepare —
    /// the first identity resolution of a launch fires no `handleIdentityChange`.
    private var seededEpoch: Int?
    /// When the snapshot was put on screen, for the snapshot → live log line only.
    private var seededAt: Date?
    /// The id of the Insights card built from the snapshot (see `isSnapshotInsight`).
    private var snapshotInsightID: UUID?
    private var hasLoadedOnce = false
    /// The first load, owned HERE rather than by the view's `.task`: a tab-away cancels only
    /// the view's wait, never the request on the wire, and the next activation joins it.
    /// Cancelled only by `handleIdentityChange`.
    private var initialLoadTask: Task<Void, Never>?
    /// Which `initialLoadTask` is current, so a finished task never clears a newer one.
    private var initialLoadID: UUID?
    /// The post-paint AI enrichment of the visible rows. Detached so a load (and a
    /// pull-to-refresh) ends when the rows paint, not when the model answers. Cancelled on a
    /// scope change and on an identity change; the merge keeps its own scope + token check.
    private var enrichTask: Task<Void, Never>?
    /// Bumped by every identity change. /updates/tabs answers carry no load token, so a late
    /// one that left under the previous identity is dropped on this instead.
    private var identityGeneration = 0
    /// The chips came from a live /updates/tabs answer for this identity. Until then the strip
    /// holds at most the Market fallback chip, which a failed /tabs may replace.
    private var tabsAreLive = false
    /// The chip that was selected when the identity changed. A same-account session heal
    /// clears the selection with everything else; this lets `loadTabs` select it again.
    private var pendingScope: String?
    /// The snapshot store's epoch when the selected scope's feed load started, i.e. which
    /// account made the selection. The store bumps its epoch on every sign-out, account switch
    /// and purge, so `handleIdentityChange` keeps the chip only while this still matches — the
    /// ended session's chip never decides the next account's first screen (auth.md §7).
    private var selectionEpoch: Int?
    /// Post-paint work (the paid enrichment, the Insights re-poll) of a load that finished while
    /// the tab was HIDDEN — the first load now outlives a tab-away. Started by
    /// `setTabActive(true)` if that load is still the one on screen; never for a hidden tab.
    private var deferredPostPaint: (scope: String, token: UUID, pollInsight: Bool)?
    /// Guards against a stale in-flight response overwriting a newer tab's data.
    private var loadToken = UUID()
    /// Scope whose feed request is currently in flight, for duplicate-request dedup.
    private var inFlightScope: String?
    private var refreshPollTask: Task<Void, Never>?

    nonisolated private static let log = Logger(subsystem: "com.phan.caydex", category: "updates")

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

    // MARK: - News-tone trend (GET /updates/sentiment-trend)

    /// The chart under the Insights card, for the SELECTED scope: its 90-day answer cut to
    /// `trendWindow`. nil hides it — not loaded yet, failed, or signed out. Never a placeholder
    /// series: a fabricated trend line on a finance screen is exactly what this screen was
    /// rebuilt to stop showing.
    @Published private(set) var sentimentTrend: SentimentTrend?
    /// The window the toggle shows. Always drawn from `trendSource`, so a switch redraws at
    /// once — no request, no spinner, no previous window's numbers (TestFlight 1.0 (11)).
    @Published private(set) var trendWindow: SentimentTrendWindow = .month
    /// The SELECTED scope's newest 90-day answer (`SentimentTrendWindow.widest`): the one
    /// series every window is cut from. Kept apart from the TTL'd cache below, so a window
    /// switch never waits on the network even when that entry has gone stale — the request a
    /// stale entry starts only refreshes what is already on screen.
    private var trendSource: SentimentTrend?
    /// The 90-day answer per scope. Short-lived: the backend's own memory tier is 5 minutes and
    /// a new day's bar appears as articles are scored. A "building" answer is never cached.
    private var trendCache: [String: (fetchedAt: Date, trend: SentimentTrend)] = [:]
    private var trendTask: Task<Void, Never>?
    private let trendCacheTTL: TimeInterval = 300
    /// False until the user taps the toggle. Until then the window is chosen per scope from
    /// its history — 7D while it has under a week, else 30D. Either way the request is the
    /// 90-day answer, and every window is a free cut of it.
    ///
    /// A tap is SAVED on this device (`SentimentTrendWindow.savedPickKey`) and restored as a
    /// pick by `restoreTrendWindowPreference()` — in `init` and again on identity change — so
    /// the choice now outlives the session (TestFlight 1.0 (9): "set up once and permanently
    /// keep them"). With nothing saved, auto mode is unchanged.
    private var userPickedWindow = false
    /// Re-checks a scope whose 90-day history is still being built, backing off, until it is
    /// ready or the delays run out. Cancelled on scope change, identity change and deinit.
    private var trendPollTask: Task<Void, Never>?
    private var trendPollAttempt = 0
    private let trendPollDelays: [UInt64] = [30, 45, 60, 90, 120, 180, 300]
    /// True once the re-checks ran out while the history was still building (the backend can
    /// hold a scope for hours when its daily budget is spent). The card then stops spinning
    /// and says "still building" instead of promising "a minute or two" indefinitely.
    @Published private(set) var trendPollExhausted = false
    /// Scopes whose re-checks ran out while still building. "Stalled" belongs to the SCOPE,
    /// not to one visit: a tab return, a chip switch or the pull the stalled card asks for
    /// restart the re-checks (and should), but must not bring back "a minute or two".
    /// Cleared by an answer that is no longer building, and on identity change.
    private var stalledScopes: Set<String> = []
    /// Whether the Updates tab is on screen. Re-checks pause while it is not.
    private var isTabActive = false

    // MARK: - Initialization

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        self.snapshotStore = UpdatesFeedSnapshotStore.shared
        // The saved News filter, through the wrapper so `didSet` neither re-saves it nor
        // regroups an empty feed. Applied when the first feed load calls `applyFiltersAndGroup`.
        _filterOptions = Published(initialValue: NewsFilterOptions.loadSaved())
        restoreTrendWindowPreference()
        // Deliberately NO load here. `UpdatesView` is instantiated once at app
        // launch for all five tabs (see ContentView), so loading in init would
        // fire network calls for a screen the user may never open. The view
        // calls `loadIfNeeded()` when the tab first becomes active.
        // No snapshot seed either: the store has not read its file yet (that is
        // `prepareSnapshot()`, from the view's `.task`, after Home has painted).
    }

    /// Adopt the device's saved news-tone window as the user's pick, or auto mode when none is
    /// saved. READ-only: nothing here writes the store, so a garbage value falls back to auto
    /// without being "repaired" over.
    private func restoreTrendWindowPreference() {
        let saved = SentimentTrendWindow.savedPick()
        userPickedWindow = saved != nil
        trendWindow = saved ?? .month
    }

    deinit {
        refreshPollTask?.cancel(); appearWorkTask?.cancel()
        trendTask?.cancel(); trendPollTask?.cancel()
        enrichTask?.cancel(); initialLoadTask?.cancel()
    }

    // MARK: - Snapshot lifecycle

    /// Read this account's snapshot from disk (once per binding; never a request) and paint it
    /// if nothing live is on screen yet. Called from the view's `.task(id: isActiveTab)` on BOTH
    /// the hidden and the active run, and from `handleIdentityChange` after its clears.
    func prepareSnapshot() async {
        expireSnapshotIfStale()
        await snapshotStore.prepare(apiClient: apiClient)
        dropSeedIfEpochMoved()
        seedFromSnapshot()
    }

    /// Put the snapshot on screen — DISPLAY state only. Never `feedCache` (its cached branch
    /// skips the network, so the live load would never run), never the pagination state, the
    /// load latch, the token or the in-flight scope, never the selection (its `.onChange` would
    /// fetch for a hidden tab), never the plan state, and never a task.
    private func seedFromSnapshot() {
        guard !hasShownFeed, allNewsArticles.isEmpty, snapshotSavedAt == nil,
              !requiresSignIn, !isReconnecting,
              (selectedTab?.scope ?? pendingScope ?? UpdatesScope.market) == UpdatesScope.market,
              let snapshot = snapshotStore.snapshotForDisplay() else { return }
        let feed = snapshot.payload.feed
        let articles = dedupedByApiID((feed.articles ?? []).compactMap { NewsArticle(dto: $0) })
        guard !articles.isEmpty else { return }
        allNewsArticles = articles
        let card = Self.snapshotInsight(feed.insight)
        insightSummary = card
        snapshotInsightID = card?.id
        // The Market chip only — the snapshot keeps no /tabs body (a stale `is_locked` could
        // open a feed the plan now locks). The live /tabs answer replaces it.
        if filterTabs.isEmpty && !tabsAreLive {
            filterTabs = [Self.marketTabFallback]
        }
        snapshotSavedAt = snapshot.savedAt
        seededEpoch = snapshotStore.epoch
        seededAt = Date()
        applyFiltersAndGroup()
        let ageSeconds = Self.wholeSeconds(Date().timeIntervalSince(snapshot.savedAt))
        let rowCount = articles.count
        let hasCard = card != nil
        Self.log.info("updates: snapshot painted — \(ageSeconds, privacy: .public) s old, \(rowCount, privacy: .public) stories, insight card: \(hasCard, privacy: .public)")
    }

    /// The snapshot's Insights card, or nil. A card whose `generated_at` does not parse would
    /// read "Updated just now" on a days-old summary, so it is dropped; the rest are forced
    /// stale ("· checking for updates") because "· up to date" was the server's claim at save
    /// time, not now.
    private static func snapshotInsight(_ dto: AIInsightCardDTO?) -> NewsInsightSummary? {
        guard let dto, UpdatesDateParser.parse(dto.generatedAt) != nil,
              var card = NewsInsightSummary(dto: dto) else { return nil }
        card.isStale = true
        card.isRefreshing = false
        return card
    }

    /// Take the snapshot off screen. A no-op while live rows are showing.
    private func dropSnapshot() {
        guard snapshotSavedAt != nil else { return }
        snapshotSavedAt = nil
        seededEpoch = nil
        seededAt = nil
        allNewsArticles = []
        insightSummary = nil
        applyFiltersAndGroup()
    }

    /// The store changed hands (or was purged) since the seed: what is on screen may be another
    /// account's, so it goes before anything is re-seeded.
    private func dropSeedIfEpochMoved() {
        guard snapshotSavedAt != nil, let seeded = seededEpoch, seeded != snapshotStore.epoch else { return }
        Self.log.info("updates: snapshot dropped — the account snapshot store changed since it was painted")
        dropSnapshot()
    }

    /// Drop an on-screen snapshot that has aged past the 96 h display window (the app sat in
    /// the background, or every live load failed). A no-op for live rows.
    func expireSnapshotIfStale(now: Date = Date()) {
        guard let savedAt = snapshotSavedAt,
              !AccountSnapshotPolicy.isDisplayable(savedAt: savedAt, now: now) else { return }
        Self.log.info("updates: snapshot dropped — past its display window")
        dropSnapshot()
    }

    /// Log values only: whole seconds / milliseconds, clamped so a wild clock cannot trap.
    nonisolated private static func wholeSeconds(_ interval: TimeInterval) -> Int {
        guard interval.isFinite else { return 0 }
        return Int(max(min(interval, 31_536_000), -31_536_000))
    }

    nonisolated private static func wholeMillis(since start: Date) -> Int {
        let millis = Date().timeIntervalSince(start) * 1000
        guard millis.isFinite, millis > 0 else { return 0 }
        return Int(min(millis, 86_400_000))
    }

    // MARK: - Lifecycle

    /// Called when the Updates tab becomes visible. Idempotent, and JOINS a first load already
    /// running instead of starting another.
    func loadIfNeeded() async {
        expireSnapshotIfStale()
        guard !hasLoadedOnce else { return }
        if let running = initialLoadTask {
            await running.value
            return
        }
        let id = UUID()
        initialLoadID = id
        // Owned by the ViewModel: the view's `.task` is cancelled when the tab switches away,
        // and that must not cancel the request on the wire (the return visit used to pay
        // /tabs + /feed again). So `Task.isCancelled` inside here is true ONLY for an identity
        // change, which cancels this task itself.
        let task = Task { [weak self] in
            guard let self else { return }
            await self.loadInitialData()
            // Latch on a genuine LIVE completion — including a legitimately empty scope — but
            // never on an identity cancellation, an error, or a snapshot still on screen (its
            // live load failed, so the next activation must try again).
            if !Task.isCancelled, self.error == nil, self.snapshotSavedAt == nil {
                self.hasLoadedOnce = true
            }
            if self.initialLoadID == id {
                self.initialLoadTask = nil
                self.initialLoadID = nil
            }
        }
        initialLoadTask = task
        await task.value
    }

    private func loadInitialData() async {
        let generation = identityGeneration
        isLoading = true
        error = nil
        let effectiveScope = selectedTab?.scope ?? pendingScope ?? UpdatesScope.market
        if effectiveScope == UpdatesScope.market {
            // The Market feed needs nothing from /tabs, so both leave together. All three lines
            // run in ONE main-actor turn: `loadFeed` claims `inFlightScope` before its first
            // suspension, so the `.onChange(of: selectedTab)` → `selectTab` this assignment
            // triggers finds the load in flight and skips. When /tabs lands it re-selects an
            // EQUAL-scope tab, which fires no `.onChange`.
            let market = selectedTab ?? filterTabs.first(where: { $0.isMarketTab }) ?? Self.marketTabFallback
            selectedTab = market
            async let tabs: Void = loadTabs()
            await loadFeed(for: market, force: false)
            await tabs
        } else {
            // A ticker scope is never fetched before /tabs confirms it: /updates/feed has no
            // server-side plan gate, only /tabs says which tickers this plan may open.
            await loadTabs()
            if let tab = selectedTab {
                await loadFeed(for: tab, force: false)
            }
        }
        // Not over another load that owns the skeleton now (a chip tap, a refresh), and not
        // after an identity change (its handler set `isLoading = true` for the next identity).
        if generation == identityGeneration, inFlightScope == nil {
            isLoading = false
        }
    }

    func refresh() async {
        isRefreshing = true
        error = nil
        // Drop the per-scope cache so pull-to-refresh actually re-fetches.
        feedCache.removeAll()
        trendCache.removeAll()
        await loadTabs()
        if let tab = selectedTab {
            await loadFeed(for: tab, force: true)
        }
        isRefreshing = false
    }

    /// Re-read the tab strip because the ACTIVE GROUP changed on another tab.
    ///
    /// Distinct from `refresh()` on purpose: that one is pull-to-refresh and shows the
    /// spinner, which would be a lie here — the user is not on this screen. It re-reads
    /// tabs and re-selects, but keeps the per-scope article cache, since the ARTICLES for
    /// a scope are unaffected by which group it belongs to. Only membership changed.
    /// The signed-in identity changed — the feed and its per-scope caches belong to the
    /// previous one.
    ///
    /// Must reset `hasLoadedOnce`. It is the hardest latch of the four tab ViewModels: it is
    /// set on the first successful load and never cleared, and this screen is opacity-mounted,
    /// so `loadIfNeeded()` early-returns for the ENTIRE process. Without this reset, signing in
    /// left guest-era news and insight scopes on screen until the app was killed.
    func handleIdentityChange(isActiveTab: Bool) async {
        // CLEAR FIRST, UNCONDITIONALLY — before the `isActiveTab` gate below. The reload is
        // deferred for a hidden tab, but the previous account's data must not survive in this
        // ViewModel waiting to be rendered (.claude/rules/auth.md §7).
        hasLoadedOnce = false
        // Cleared with it, ABOVE the gate: a latched "sign in" from a load that raced session
        // restore is exactly what this reload exists to heal.
        requiresSignIn = false
        isReconnecting = false
        // The news-tone chart belongs to the previous identity's feed too.
        trendTask?.cancel()
        trendTask = nil
        trendPollTask?.cancel()
        trendPollTask = nil
        trendCache.removeAll()
        trendSource = nil
        sentimentTrend = nil
        // Back to the DEVICE's saved window (or auto mode when there is none). The pick is a
        // display preference of this phone, not the previous account's data, so it carries
        // over — only the in-memory chart state above is the old identity's.
        restoreTrendWindowPreference()
        trendPollAttempt = 0
        trendPollExhausted = false
        stalledScopes.removeAll()

        // The feed itself, the chips, the plan state and every in-flight load belong to the
        // previous identity too — all cleared HERE, above the gate, so a hidden tab holds none
        // of them (with a snapshot store that would be a cross-account leak, not untidy state).
        //
        // The selected chip first: a same-account session heal clears the selection with the
        // rest, and `loadTabs` selects it again from this (re-checked against the new answer).
        // Only for the SAME account: AppState bumps the store's epoch before it publishes a
        // sign-out or an account switch, so a moved epoch drops the stash — the ended session's
        // chip must not pick the next account's first feed. The stash wins over the selection:
        // while it is set no live /tabs has answered, so the selection can only be the Market
        // fallback a refused /tabs picked during the restoring window, never the user's choice.
        pendingScope = selectionEpoch == snapshotStore.epoch ? (pendingScope ?? selectedTab?.scope) : nil
        // Fences every answer of the previous identity: the bumped generation drops a late
        // /tabs, the new token drops every feed, page, enrich and insight re-poll answer, and
        // `inFlightScope` is cleared by hand because the old load's `defer` no longer can.
        identityGeneration &+= 1
        initialLoadTask?.cancel()
        initialLoadTask = nil
        initialLoadID = nil
        loadToken = UUID()
        inFlightScope = nil
        enrichTask?.cancel()
        enrichTask = nil
        deferredPostPaint = nil
        refreshPollTask?.cancel()
        refreshPollTask = nil
        appearWorkTask?.cancel()
        appearWorkTask = nil
        isEnriching = false
        summarizingIDs.removeAll()
        feedCache.removeAll()
        allNewsArticles = []
        newsArticles = []
        groupedNews = []
        insightSummary = nil
        loadedOffset = 0
        hasMorePages = false
        isLoadingMore = false
        // `selectedTab = nil` is the one selection reset that fires no load (its `.onChange`
        // ignores nil); selecting Market here would fetch for a HIDDEN tab.
        filterTabs = []
        selectedTab = nil
        tabsAreLive = false
        groupName = nil
        lockedTickerCount = 0
        tierRequiredForMoreTickers = nil
        snapshotSavedAt = nil
        seededEpoch = nil
        seededAt = nil
        snapshotInsightID = nil
        hasShownFeed = false
        error = nil
        // The next identity's first frame is its skeleton (or its snapshot), never "No recent
        // stories" from a cleared list.
        isLoading = true

        // The News filter is NOT a device preference like the window: a filter on a device-global
        // key would open the next account's feed already narrowed (auth.md §7), so
        // `AppState.discardDataForEndedSession()` removes it from the store when a session ends.
        // Re-reading the store drops it from this long-lived ViewModel too. A RE-READ, never a
        // reset to `.default`: a transient-restore heal of the SAME account also lands here, and
        // a reset would erase that user's filter. The flag keeps the re-read from saving.
        isRereadingSavedFilter = true
        filterOptions = NewsFilterOptions.loadSaved()
        isRereadingSavedFilter = false

        // Then the NEW identity's own snapshot, above the gate too, so a hidden tab is ready.
        // AppState re-binds or clears the store before it publishes the identity change, so a
        // different account finds nothing here and a same-account heal repaints its own.
        await prepareSnapshot()

        // Fetch only if the user is actually looking at this tab. Clearing above nils the
        // freshness stamp, so `.task(id: isActiveTab)` re-loads on the next activation.
        guard isActiveTab else { return }
        // The same first load as a first open (parallel Market start, the success-only latch);
        // the cache was cleared above, so nothing of the previous identity is served from it.
        await loadIfNeeded()
    }

    /// A watchlist row was added or removed elsewhere. Only when this tab has already
    /// loaded — `loadIfNeeded` fetches fresh on the first activation, and the chips are not
    /// worth a tabs+feed fetch for a tab nobody has opened.
    func reloadForWatchlistChange() async {
        guard hasLoadedOnce else { return }
        await reloadForActiveGroupChange()
    }

    func reloadForActiveGroupChange() async {
        await loadTabs()
        // `loadTabs` re-points `selectedTab` at a scope the new group actually contains
        // (falling back to Market), so the feed below can never be the old group's.
        if let tab = selectedTab {
            await loadFeed(for: tab, force: false)
        }
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
    ///
    /// `assetType` is the search result's wire class and is REQUIRED, not defaulted:
    /// `POST /watchlist` resolves an undeclared bare coin symbol toward the coin by
    /// design (`canonical_stored_symbol("LTC", nil)` → "LTCUSD"), so sending nil for the
    /// equity the user tapped stored LTC Properties as Litecoin, Banco de Chile (BCH) as
    /// Bitcoin Cash, Atomera (ATOM) as Cosmos, Interlink (LINK) as Chainlink and Emeren
    /// (SOL) as Solana — the strip and Tracking then showed the coin, and the REIT they
    /// chose was tracked nowhere.
    func addTicker(_ symbol: String, assetType: String?) async {
        let ticker = symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else { return }
        guard !filterTabs.contains(where: { $0.scope == ticker }) else {
            print("⏭️ UpdatesVM: \(ticker) already in tabs")
            return
        }
        do {
            try await apiClient.request(endpoint: .addToWatchlist(stockId: ticker, assetType: assetType))
            print("✅ UpdatesVM: Added \(ticker) to watchlist")
            // Tracking and Home are stale otherwise (the backend also mirrored the ticker
            // into the active group). Post-confirm, with the STORED spelling — a coin is
            // stored as the pair. This screen ignores its own source below.
            let wireClass = (assetType ?? "").trimmingCharacters(in: .whitespaces).lowercased()
            PortfolioStore.announceWatchlistChange(
                ticker: wireClass == "crypto" ? CryptoSymbol.pair(ticker) : ticker,
                assetType: wireClass.isEmpty ? MarketTickerType.resolve(nil, symbol: ticker).rawValue : wireClass,
                added: true, source: .updates
            )
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
            // The tab scope IS the stored spelling (it came from the watchlist), so it is
            // the right key for Tracking's row match. Post-confirm; own source ignored.
            PortfolioStore.announceWatchlistChange(
                ticker: ticker, assetType: MarketTickerType.resolve(nil, symbol: ticker).rawValue,
                added: false, source: .updates
            )
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
        // /tabs carries no load token, so an answer that left under the previous identity is
        // recognised by this instead (it would put that account's chips on screen).
        let generation = identityGeneration

        do {
            let response: UpdatesTabsResponse = try await apiClient.request(
                endpoint: .getUpdatesTabs,
                responseType: UpdatesTabsResponse.self
            )
            guard generation == identityGeneration else {
                print("⏭️ UpdatesVM: Discarding /updates/tabs from a previous identity")
                return
            }
            // Remember the selection by SCOPE, read NOW rather than when the request left: the
            // Market chip is tappable while /tabs is in flight (it starts beside the feed), and a
            // chip picked meanwhile must not be yanked back when this lands. `pendingScope` is
            // the chip selected before an identity change cleared the selection (a same-account
            // heal keeps it; for another account `openableTabs` re-checks it below).
            let previousScope = selectedTab?.scope ?? pendingScope
            pendingScope = nil
            let tabs = response.tabs.map { NewsFilterTab(dto: $0) }
            // Group + plan state travels with the pills, so it is applied even on the
            // empty-tabs path below — otherwise a Free user whose group is entirely
            // locked would see no chips AND no way to find out why.
            groupName = response.groupName
            lockedTickerCount = max(0, response.lockedCount)
            tierRequiredForMoreTickers = response.tierRequired
            guard !tabs.isEmpty else {
                print("⚠️ UpdatesVM: /updates/tabs returned no tabs")
                if filterTabs.isEmpty || !tabsAreLive { filterTabs = [Self.marketTabFallback] }
                selectedTab = selectedTab ?? filterTabs.first
                return
            }
            filterTabs = tabs
            tabsAreLive = true
            // Selection can only land on a tab whose feed the plan actually allows —
            // otherwise a downgrade would leave the user parked on a locked scope with no
            // chip to navigate away from.
            selectedTab = openableTabs.first { $0.scope == previousScope } ?? openableTabs.first
            print("✅ UpdatesVM: Loaded \(tabs.count) tabs (selected: \(selectedTab?.scope ?? "none"), locked: \(lockedTickerCount))")
        } catch {
            guard generation == identityGeneration else { return }
            let appError = AppError.from(error)
            // Nobody is waiting for a cancelled request; it says nothing about the chips.
            guard !appError.isCancellation else { return }
            print("⚠️ UpdatesVM: Failed to load tabs: \(appError.message)")
            // The Market feed is still usable without the watchlist pills, so
            // degrade to a single tab rather than showing an empty screen. Keyed on the
            // SELECTION and on live chips, not on an empty strip: a strip holding only the
            // seeded Market chip (no live /tabs yet) must still get a selection here, or no
            // feed load would ever start.
            if selectedTab == nil || !tabsAreLive {
                if !tabsAreLive { filterTabs = [Self.marketTabFallback] }
                selectedTab = filterTabs.first(where: { $0.isMarketTab }) ?? Self.marketTabFallback
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

        // THREE outcomes, not two — decided in the `catch` below from the TYPED refusal, not
        // from a pre-flight read of `auth.status`. Every Updates endpoint is
        // `.signInRequired`, so an unarmed caller is refused by `APIClient.buildRequest`
        // before any network I/O and arrives as `AppError.signInRequired`; this ViewModel used
        // to flatten that into `error`, rendered by `errorState` as "Couldn't load the news"
        // over a **Try Again** button that can never succeed.
        //
        // Deliberately NOT an up-front `guard AppActions.shared.isSignedIn`: on every signed-in
        // cold launch the token is armed while `status` still reads `.restoring`, so that
        // guard refuses a request that would have succeeded (see `HomeDashboardViewModel`).
        // Routing the refusal through the normal path also means it goes through the
        // `loadToken` bump below — an early return before that bump let a response already on
        // the wire land afterwards and repopulate the list the gate had just cleared.

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
        // Which account this selection belongs to (see `selectionEpoch`).
        selectionEpoch = snapshotStore.epoch
        // The news-tone chart loads beside the feed, never in front of it: its own task,
        // its own staleness check, and a failure only hides the chart.
        startTrendLoad(scope: scope, force: force)
        // Clear only if THIS load still owns the slot. On A→B→A, a stale A#1
        // response would otherwise clear the flag while A#2 is in flight, so the
        // dedup guard misses and A is fetched twice.
        defer { if loadToken == token { inFlightScope = nil } }
        refreshPollTask?.cancel()
        // Drop any pending scroll-driven work from the OUTGOING tab — its index
        // refers to the previous feed, and the token check would discard it anyway.
        appearWorkTask?.cancel()
        appearWorkTask = nil
        // Same for the outgoing feed's detached enrichment (state hygiene: the server still
        // finishes a request it already received; the merge would be dropped on the token).
        enrichTask?.cancel()
        enrichTask = nil
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
            // Same for the account gate: this scope has real rows to show.
            requiresSignIn = false
            isReconnecting = false
            allNewsArticles = cached.articles
            insightSummary = cached.insight
            // A cached feed is this session's live answer, never the snapshot (the snapshot
            // never enters `feedCache`), so the label goes with it.
            snapshotSavedAt = nil
            hasShownFeed = true
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
            // manual pull-to-refresh. (Deferred to the next activation on a hidden tab.)
            if allNewsArticles.contains(where: { !$0.aiProcessed && $0.isEnrichable }) {
                startPostPaintWork(scope: scope, token: token, pollInsight: false)
            }
            return
        }

        // Clear immediately so the previous tab's news never shows under the new
        // tab's title while the request is in flight — EXCEPT the Market snapshot under a
        // Market load: it stays on screen (no skeleton flash) and the answer replaces it in
        // one turn below. The snapshot is always Market, so any other scope clears it.
        if snapshotSavedAt == nil || scope != UpdatesScope.market {
            snapshotSavedAt = nil
            allNewsArticles = []
            newsArticles = []
            groupedNews = []
            insightSummary = nil
        }
        isLoading = true
        error = nil
        // Reset pagination with the feed. Carrying the previous tab's offset
        // over would make the new scope's first "load more" skip its opening
        // rows — a silent hole in the timeline.
        loadedOffset = 0
        hasMorePages = false
        isLoadingMore = false

        // Captured BEFORE the request: a store that changed hands while it was in flight
        // refuses the save, so one account's answer is never filed under the next.
        let snapshotEpoch = snapshotStore.epoch
        do {
            // The exact response bytes come back too: the snapshot keeps them verbatim and
            // re-decodes them through this same DTO at the next launch.
            let (response, body) = try await apiClient.requestReturningBody(
                endpoint: .getUpdatesFeed(scope: scope, limit: feedLimit),
                responseType: UpdatesFeedResponse.self
            )
            let fetchedAt = Date()
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

            // ONE turn: the snapshot rows (if any) are replaced by the live ones with no
            // skeleton between, and the rows keep their identity (`stableID` = the server id).
            allNewsArticles = articles
            insightSummary = response.insight.flatMap { NewsInsightSummary(dto: $0) }
            let replacedSnapshotSavedAt = snapshotSavedAt
            let replacedSnapshotSeededAt = seededAt
            snapshotSavedAt = nil
            seededAt = nil
            hasShownFeed = true
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
            requiresSignIn = false
            isReconnecting = false

            // The snapshot for the next cold launch: a LIVE Market first page with stories,
            // the bytes exactly as received, dated when they were received. The store refuses
            // it if the account changed while the request was in flight (the epoch).
            if scope == UpdatesScope.market, !articles.isEmpty {
                snapshotStore.save(
                    parts: [UpdatesFeedSnapshot.feedPart: body],
                    payload: UpdatesFeedSnapshot(feed: response),
                    savedAt: fetchedAt,
                    epoch: snapshotEpoch
                )
            }

            print("""
            ✅ UpdatesVM: Loaded \(articles.count) articles for \(scope) \
            (cached: \(response.cached ?? false), \
            insight: \(insightSummary.map { $0.isAIGenerated ? "ai" : "fallback" } ?? "none"))
            """)
            if let replacedSnapshotSavedAt, let replacedSnapshotSeededAt {
                let onScreenMillis = Self.wholeMillis(since: replacedSnapshotSeededAt)
                let snapshotAgeSeconds = Self.wholeSeconds(fetchedAt.timeIntervalSince(replacedSnapshotSavedAt))
                Self.log.info("updates: live feed replaced the snapshot after \(onScreenMillis, privacy: .public) ms on screen (snapshot was \(snapshotAgeSeconds, privacy: .public) s old)")
            }

            // Detached: this load (and a pull-to-refresh) ends HERE, when the rows paint. A load
            // that finished behind another tab waits for the next activation instead.
            startPostPaintWork(scope: scope, token: token, pollInsight: true)
        } catch is CancellationError {
            // Not a failure — show nothing. Only for THIS load: a load the identity handler
            // cancelled must not clear the next identity's skeleton.
            if loadToken != token { return }
            isLoading = false
            return
        } catch {
            guard loadToken == token else { return }
            let appError = AppError.from(error)
            if appError.isCancellation {
                // URLSession surfaces task cancellation as URLError.cancelled (often nested in
                // the API error), whose message is the literal "cancelled". Rendering that as
                // "Couldn't load the news / cancelled" blamed the network for a tab switch.
                isLoading = false
                return
            }
            // The account gate is decided HERE, from the typed refusal — see the note at the
            // top of this function for why not from `auth.status`.
            if case .signInRequired = appError {
                let reconnecting = AppActions.shared.isRestoringSession
                isReconnecting = reconnecting
                requiresSignIn = !reconnecting
                self.error = nil
                // No stored stories under the account gate (owner decision 1).
                dropSnapshot()
            } else {
                self.error = appError.message
                requiresSignIn = false
                isReconnecting = false
                // A snapshot on screen STAYS (the view says the refresh failed beside it),
                // still bounded by its display window.
                expireSnapshotIfStale()
            }
            isLoading = false
            // NO sample-data fallback. Fabricated headlines here would render as
            // real market news.
            print("⚠️ UpdatesVM: Failed to load feed for \(scope): \(appError.message)")
        }
    }

    // MARK: - News-tone trend

    /// The tab's visibility. A "Building…" re-check has no reader while the tab is hidden, so
    /// it pauses there, and coming back checks again at once with a fresh set of re-checks.
    func setTabActive(_ active: Bool) {
        guard active != isTabActive else { return }
        isTabActive = active
        if !active {
            trendPollTask?.cancel()
            trendPollTask = nil
        } else if let trend = sentimentTrend, trend.isBuildingHistory,
                  trend.scope == selectedTab?.scope {
            startTrendLoad(scope: trend.scope, force: true)
        }
        // A load that painted while the tab was hidden starts its post-paint work now — only if
        // it is still the load on screen (a newer load, or an identity change, owns the token).
        if active, let pending = deferredPostPaint {
            deferredPostPaint = nil
            if pending.token == loadToken, pending.scope == selectedTab?.scope {
                startPostPaintWork(scope: pending.scope, token: pending.token, pollInsight: pending.pollInsight)
            }
        }
    }

    func setTrendWindow(_ window: SentimentTrendWindow) {
        // A tap on the window already shown is a no-op only once it is the user's own pick. In
        // auto mode it is still a choice: unrecorded, the next scope or a relaunch auto-picks
        // another window — the "it changed back" of TestFlight 1.0 (9).
        guard window != trendWindow || !userPickedWindow else { return }
        // The user's choice wins on every scope, and is saved so it survives a relaunch. Saved
        // HERE only — the auto pick in `show` and the failure snap-back in `loadTrend` assign
        // `trendWindow` too, and storing either would overwrite the user's real choice.
        userPickedWindow = true
        trendWindow = window
        window.saveAsPick()
        guard let scope = selectedTab?.scope else { return }
        // Redraws at once from the scope's 90-day answer (`startTrendLoad` → `show`); a
        // request goes out only when that answer is stale, and only to refresh it.
        startTrendLoad(scope: scope, force: false)
    }

    /// `fromPoll` keeps the building re-check counter running; every other caller restarts it.
    private func startTrendLoad(scope: String, force: Bool, fromPoll: Bool = false) {
        trendTask?.cancel()
        trendPollTask?.cancel()
        trendPollTask = nil
        if !fromPoll {
            trendPollAttempt = 0
            trendPollExhausted = stalledScopes.contains(scope)
        }
        let isNewScope = sentimentTrend?.scope != scope
        // Auto mode opens a NEW scope on 30D; the answer then decides whether to show 7D.
        if !userPickedWindow && isNewScope { trendWindow = .month }
        if !force, let hit = trendCache[scope], Date().timeIntervalSince(hit.fetchedAt) < trendCacheTTL {
            present(hit.trend)
            trendTask = nil
            return
        }
        // The SAME scope redraws at once from the 90-day answer it already holds — any window,
        // no spinner, no previous window's numbers — and the request below only refreshes it.
        // Another scope's chart must never sit under this scope's card while it is in flight.
        if let source = trendSource, source.scope == scope {
            show(source)
        } else {
            sentimentTrend = nil
            trendSource = nil
        }
        trendTask = Task { [weak self] in
            await self?.loadTrend(scope: scope)
        }
    }

    /// Fetch the scope's 90-day answer — the one series every window is cut from.
    private func loadTrend(scope: String) async {
        let fetchWindow = SentimentTrendWindow.widest
        do {
            let response: SentimentTrendResponse = try await apiClient.request(
                endpoint: .getSentimentTrend(scope: scope, days: fetchWindow.days),
                responseType: SentimentTrendResponse.self
            )
            // The SCOPE is checked, never the window: this answer serves every window, so a
            // toggle tapped while it was in flight must not throw it away (dropping it, and
            // re-fetching per window, was the lag of TestFlight 1.0 (11)).
            guard !Task.isCancelled, selectedTab?.scope == scope else { return }
            let trend = SentimentTrend(dto: response, window: fetchWindow)
            // A building answer is never cached: the next re-check must see the next state.
            if !trend.isBuildingHistory { trendCache[scope] = (Date(), trend) }
            present(trend)
            print("✅ UpdatesVM: news-tone trend \(scope) \(fetchWindow.rawValue) (showing \(trendWindow.rawValue)): \(trend.days.count) day(s), history=\(trend.historyStatus?.rawValue ?? "n/a")")
        } catch {
            if error is CancellationError || (error as? URLError)?.code == .cancelled { return }
            guard !Task.isCancelled, selectedTab?.scope == scope else { return }
            // Not an error state: the chart is secondary to the feed, and a 503
            // SENTIMENT_TREND_UNAVAILABLE (a blip, or migration 180 not yet applied) must not
            // put an error banner over a feed that loaded fine. Logged so it is not silent.
            //
            // A failed REFRESH keeps this scope's chart (every window is still cut from the
            // answer it holds) and the toggle on the window it draws — nil-ing it removed the
            // card and the only toggle that could switch back. Only with nothing of this scope
            // on screen is it hidden.
            if let shown = sentimentTrend, shown.scope == scope {
                trendWindow = shown.window
                // A failed re-check of a history still being built must not end the
                // re-checks: the card would spin with nothing scheduled. The delay list still
                // bounds them (`trendPollAttempt` survives a poll's own fetch).
                if shown.isBuildingHistory { scheduleTrendPollIfBuilding(scope: scope) }
            } else {
                sentimentTrend = nil
                trendSource = nil
            }
            print("⚠️ UpdatesVM: news-tone trend unavailable for \(scope) \(fetchWindow.rawValue): \(AppError.from(error).message)")
        }
    }

    /// Show a fetched or cached 90-day answer, and keep re-checking one still being built.
    private func present(_ source: SentimentTrend) {
        show(source)
        scheduleTrendPollIfBuilding(scope: source.scope)
    }

    /// Draw `trendWindow` cut from the scope's 90-day answer — never a request. In auto mode a
    /// scope with under a week of history opens on 7D (a mostly empty month reads as broken),
    /// and on 30D once it has more. A window the user picked is shown as picked.
    private func show(_ source: SentimentTrend) {
        trendSource = source
        if !userPickedWindow {
            trendWindow = source.trackedDays() < SentimentTrend.shortHistoryDays ? .week : .month
        }
        sentimentTrend = source.trimmed(to: trendWindow)
    }

    /// While a scope's 90-day history is being built, re-check with a backoff so the
    /// "Building…" card turns into the chart without a pull-to-refresh. Stops when the
    /// history is ready, the scope changes, or the delays run out.
    private func scheduleTrendPollIfBuilding(scope: String) {
        guard sentimentTrend?.isBuildingHistory == true else {
            stalledScopes.remove(scope)
            trendPollExhausted = false
            return
        }
        guard trendPollAttempt < trendPollDelays.count else {
            stalledScopes.insert(scope)
            trendPollExhausted = true
            return
        }
        // Hidden tab: nothing is scheduled; `setTabActive(true)` re-checks on return.
        guard isTabActive else { return }
        let delay = trendPollDelays[trendPollAttempt]
        trendPollAttempt += 1
        trendPollTask?.cancel()
        trendPollTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: delay * 1_000_000_000)
            guard !Task.isCancelled, let self, self.selectedTab?.scope == scope else { return }
            self.startTrendLoad(scope: scope, force: true, fromPoll: true)
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
        // A row built behind another tab (this tab is opacity-mounted, so a load that finished
        // after a tab-away still builds its rows) is not a reader: no paid enrichment, no page.
        // The next activation enriches the top of the feed (`deferredPostPaint`).
        guard isTabActive else { return }
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
            // Accepted trade-off: a saved publisher first seen on this page starts filtering mid-scroll.
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
        // Never a paid enrich for snapshot rows: the live page replaces them within a few
        // hundred ms. (The on-tap `summarizeArticle` stays allowed — a tap is intent.)
        guard snapshotSavedAt == nil else { return }
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

    /// Everything a load starts once its rows have painted: the enrichment of the top of the
    /// feed (a paid call) and, for a network answer, the Insights re-poll. On a HIDDEN tab
    /// nothing starts — the first load now finishes after a tab-away, and enriching 20 rows
    /// nobody sees (plus two /feed re-polls) is exactly the spend `articleDidAppear` exists to
    /// avoid. It is recorded instead, and `setTabActive(true)` starts it if still current.
    private func startPostPaintWork(scope: String, token: UUID, pollInsight: Bool) {
        guard isTabActive else {
            deferredPostPaint = (scope: scope, token: token, pollInsight: pollInsight)
            return
        }
        deferredPostPaint = nil
        startEnrichment(scope: scope, token: token)
        if pollInsight {
            scheduleInsightPollIfNeeded(scope: scope, token: token)
        }
    }

    /// The post-paint enrichment of the top of the feed, as a tracked task instead of an
    /// `await` inside the load: the load returns at paint. Replaces any previous one.
    private func startEnrichment(scope: String, token: UUID) {
        enrichTask?.cancel()
        enrichTask = Task { [weak self] in
            await self?.enrichVisibleWindow(around: 0, scope: scope, token: token)
        }
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
            let appError = AppError.from(error)
            // A cancelled enrich (scope or identity change) is not a failure worth a log line.
            if appError.isCancellation { return }
            print("⚠️ UpdatesVM: Enrichment (\(source)) failed for \(scope): \(appError.message)")
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
            // Snapshot rows (a tap-summarised one) never enter the cache: its cached branch
            // would serve them later as this session's live feed.
            if snapshotSavedAt == nil {
                feedCache[scope] = (allNewsArticles, insightSummary, loadedOffset, hasMorePages)
            }
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
                // No `await` on `loadToken`: this Task inherits the enclosing @MainActor
                // isolation, so the read is synchronous. (`repollInsight` below is genuinely
                // async and keeps its `await`.)
                guard let self, self.loadToken == token else { return }
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

    /// True when the source/sentiment sheet is narrowing the feed. Drives the empty state's
    /// copy ("No stories match your filters" vs "No recent stories").
    var hasActiveFeedFilter: Bool {
        effectiveFilterOptions.hasActiveFilters
    }

    private func applyFiltersAndGroup() {
        // Sources for the filter sheet come from the UNFILTERED feed on purpose —
        // narrowing them by the active keyword would hide togglable publishers.
        //
        // FIRST, before filtering: `effectiveFilterOptions` intersects the saved sources with
        // this list, so filtering ahead of it would apply the previous scope's publishers.
        availableSources = Array(
            Set(allNewsArticles.map { $0.source.displayName })
        ).sorted()
        let effective = effectiveFilterOptions
        newsArticles = allNewsArticles.filter { effective.matches($0) }
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
