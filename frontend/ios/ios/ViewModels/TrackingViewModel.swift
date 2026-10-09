//
//  TrackingViewModel.swift
//  ios
//
//  ViewModel for the Tracking screen
//

import Foundation
import SwiftUI
import Combine
import OSLog

@MainActor
class TrackingViewModel: ObservableObject {
    // MARK: - Private
    private var cancellables = Set<AnyCancellable>()
    private let apiClient: APIClient
    let portfolioStore: PortfolioStore
    /// This account's last live Holdings answer on THIS device (`TrackingSnapshot.swift`).
    /// Assigned once, in `init`, from the shared instance — memory-only in DEBUG screenshot mode.
    private let snapshotStore: TrackingSnapshotStore
    private var priceRefreshTask: Task<Void, Never>?

    nonisolated private static let log = Logger(subsystem: "com.phan.caydex", category: "tracking")

    // MARK: - Published Properties

    // Tab State
    @Published var selectedTab: TrackingTab = .assets
    @Published var searchText: String = ""

    // Assets Tab
    @Published var trackedAssets: [TrackedAsset] = []
    @Published var sortOption: AssetSortOption = .name
    @Published var sortAscending: Bool = true

    // Alerts & Events
    @Published var alerts: [AppAlert] = []

    /// Local UI preference for showing the Portfolio Insights section.
    /// Persisted in UserDefaults so it survives app restarts on this device.
    @Published var isInsightsEnabled: Bool {
        didSet {
            UserDefaults.standard.set(isInsightsEnabled, forKey: Self.insightsEnabledKey)
        }
    }
    /// Percent vs dollars for the Holdings rows' change column. One value for the whole
    /// list: tapping any row's price block switches every row, which is what makes the column
    /// comparable at a glance.
    ///
    /// Local-only, like `sortOption` and `isInsightsEnabled` next to it, and deliberately NOT
    /// in `SettingsSyncManager` — that allow-list costs a full-blob replace, a sign-out
    /// clearing path and a network round trip per write, and its own precedents (App Lock,
    /// chart type, this screen's sort) keep cosmetic display choices on the device.
    @Published var changeDisplayMode: ChangeDisplayMode = .percent {
        didSet {
            UserDefaults.standard.set(changeDisplayMode.rawValue, forKey: Self.changeDisplayModeKey)
        }
    }

    private static let insightsEnabledKey = "TrackingView.isInsightsEnabled"
    private static let changeDisplayModeKey = "TrackingView.changeDisplayMode"
    private static let sortOptionKey = "TrackingView.sortOption"
    private static let sortAscendingKey = "TrackingView.sortAscending"

    /// Server-computed diversification score (the source of truth). Nil until
    /// loaded, when the user has < 2 holdings, or when the call fails.
    @Published var portfolioInsights: DiversificationScore?
    /// True only when the insights call failed for connectivity reasons — used
    /// to decide whether to fall back to the on-device estimate.
    @Published var portfolioInsightsLoadFailed: Bool = false

    /// Where the Portfolio Insights answer stands. Three honest outcomes plus "not asked yet":
    /// unknown is NOT "too few holdings" (a known server `null`) and neither is a failure — the
    /// card used to show the first-run "Enter shares… Set up" call to action for all three.
    enum PortfolioInsightsPhase: Equatable {
        /// Nothing asked for this identity yet. The INITIAL value: a never-activated hidden tab
        /// must mount no spinner.
        case idle
        /// A request for the current token is on the wire — set when one starts, and always
        /// moved on by its own completion (published, or dropped back to `.idle`).
        case resolving
        /// The server answered for `portfolioInsightsPortfolioId`: a score, or nil.
        case known
        /// The request failed (see `portfolioInsightsLoadFailed` for the network case).
        case failed
    }
    @Published private(set) var portfolioInsightsPhase: PortfolioInsightsPhase = .idle {
        didSet {
            // The carried snapshot answer stands in ONLY while a request is on the wire.
            if portfolioInsightsPhase != .resolving { insightsCarriedFromSnapshot = nil }
        }
    }
    /// The group `portfolioInsights` / the phase describe. A score is only ever shown for the
    /// group it was computed for.
    private var portfolioInsightsPortfolioId: String?
    /// The snapshot's kept insights answer, carried across the snapshot → live swap while the
    /// live request for the SAME group is still on the wire. The rows usually go live a moment
    /// before the score: without this the card collapsed to its "Loading…" spinner and then
    /// re-expanded — two height changes below the rows on every such open. It is the rule a
    /// pull-to-refresh of one group already follows (`markPortfolioInsightsResolving`): the
    /// last answer stays up until the new one lands. Dropped once the phase leaves `.resolving`.
    private var insightsCarriedFromSnapshot: (portfolioId: String, score: DiversificationScore?)?
    /// Bumped by every insights request and by an identity change; an answer whose token is no
    /// longer current is dropped (a newer request, or another identity, owns the card now).
    private var insightsRequestToken = 0

    // MARK: Device snapshot (display-only)

    /// The last live Holdings answer read back from this device for this account, painted
    /// labelled "Updated <time>" until THIS process's own live answer lands.
    ///
    /// ⚠️ DISPLAY-ONLY. Never written into `PortfolioStore` (its whole-list PUTs would delete
    /// what the user added since), never `trackedAssets`, never the alerts or the search star,
    /// and it never latches `hasLoadedOnce`. Rows, group name and score come from it
    /// all-or-nothing (`presentedSnapshot`), so live prices never sit beside snapshot
    /// membership.
    @Published private(set) var snapshotSeed: TrackingSnapshot? {
        didSet {
            if snapshotSeed == nil {
                snapshotSeedSavedAt = nil
                seededEpoch = nil
                seedShownAt = nil
            }
        }
    }
    /// When the seeded snapshot was saved (set with the seed, cleared with it).
    private var snapshotSeedSavedAt: Date?
    /// The store's epoch when the seed was taken. A seed whose store has since been re-bound,
    /// cleared or purged belongs to a binding that no longer holds — dropped at the next prepare.
    private var seededEpoch: Int?
    /// When the seed reached the screen, for the snapshot → live log line.
    private var seedShownAt: Date?

    /// THIS identity's feed has answered live in this process (the 30 s poll keeps it true;
    /// only a refusal or an identity change clears it).
    @Published private(set) var hasLiveFeed = false
    /// The first load got past its phase 1 for this identity — before that, an empty list says
    /// "not loaded yet", never "No tickers yet".
    @Published private(set) var hasAttemptedLoad = false
    /// The exact bytes of the last live `GET /tracking/assets`, for the snapshot save.
    private var lastLiveFeedBody: Data?
    /// Bumped by every identity change. A load (or a feed answer) that started under the
    /// previous identity publishes nothing and latches nothing.
    private var loadGeneration = 0

    // Whales Tab
    @Published var selectedWhaleCategory: WhaleCategory = .investors
    @Published var whaleActivities: [WhaleActivity] = []
    @Published var trackedWhales: [TrendingWhale] = []
    @Published var popularWhales: [TrendingWhale] = []
    @Published var heroWhales: [TrendingWhale] = []
    @Published var allPopularWhales: [TrendingWhale] = []
    @Published var showAllWhales: Bool = false
    @Published var showAllTrades: Bool = false
    /// The Tracking screen's PREVIEW slice — at most `recentTradesPreviewLimit` trades.
    @Published var groupedWhaleTrades: [GroupedWhaleTrades] = []
    /// The complete feed, backing `AllRecentTradesView`. Never truncated.
    @Published var allWhaleTrades: [GroupedWhaleTrades] = []

    /// How many trades the Tracking screen's Recent Trades timeline shows before it
    /// defers to See All. Following 20 whales otherwise buries "Most Popular" under a
    /// timeline the user has to scroll past every visit.
    static let recentTradesPreviewLimit = 5

    /// Trades hidden behind the preview cap — drives the "+N more trades" tail row.
    /// 0 means the preview IS the whole feed, so no tail is drawn.
    var hiddenRecentTradeCount: Int {
        let total = allWhaleTrades.reduce(0) { $0 + $1.activities.count }
        return max(0, total - Self.recentTradesPreviewLimit)
    }

    /// Pending coalesced activity re-fetch — see `reloadForFollowChange()`.
    private var activityReloadTask: Task<Void, Never>?

    /// Pending coalesced reconcile after a watchlist change made elsewhere — see
    /// `handleWatchlistChange(_:)`.
    private var watchlistReloadTask: Task<Void, Never>?

    /// `recentlyAddedTickers` markers this ViewModel inserted for detail-screen ADDs and
    /// has not yet cleared. Cleared only after a load that could SEE the row: a marker
    /// removed before that load lands would let `performLoad`'s purge treat the new
    /// group member as an orphan.
    private var watchlistMarkerQueue: [(portfolioId: String, ticker: String)] = []

    /// A tapped LOCKED Follow button → the plan sheet. Owned here rather than passed down
    /// as a closure because the button sits three list layers deep (section → flat/category
    /// list → card) in two different screens.
    @Published var showWhalePaywall: Bool = false

    // Loading States
    @Published var isLoading: Bool = false
    /// Whale list + activity only. Deliberately SEPARATE from `isLoading`: those two
    /// calls are not on the Assets tab's critical path, and conflating them is what
    /// made the whole screen wait for them.
    @Published private(set) var isLoadingWhales: Bool = false
    @Published var isRefreshing: Bool = false

    /// User-facing copy when the assets feed could not be loaded, mapped through
    /// `AppError` (never a raw backend string). Without this an outage was
    /// pixel-identical to an empty portfolio — the list just rendered nothing,
    /// with no explanation and no retry affordance.
    @Published var assetsErrorMessage: String?

    /// User-facing copy when the WHALE roster could not be loaded, mapped through
    /// `AppError`. Without it a total failure left the Whales tab pixel-identical to
    /// "there are no investors to follow" — no explanation, no retry affordance. The
    /// assets tab has had `assetsErrorMessage` for exactly this reason; the whale half
    /// of the same screen simply printed to the console and gave up.
    @Published var whalesErrorMessage: String?

    /// The Assets half is empty because its load was REFUSED for want of an armed credential,
    /// not because it broke. Kept as a flag rather than folded into `assetsErrorMessage` for
    /// the reason this whole pass exists — once `AppError.signInRequired` is flattened to its
    /// `.message`, the view renders "Couldn't load your holdings" over a **Retry** button that
    /// re-fires a request `APIClient` refuses before it leaves the device.
    ///
    /// ⚠️ ONE PAIR PER HALF, not one shared pair. A shared pair was tried and the review
    /// caught it: `loadWhaleList` succeeding after the session healed cleared the flags the
    /// ASSETS load had set, and since switching sub-tab fetches nothing, Assets then fell
    /// through to "No tickers yet" about a portfolio that was never loaded. Each writer now
    /// touches only its own half.
    ///
    /// ⚠️ Written from the OUTCOME of a load, never from a pre-flight `auth.status` read — see
    /// `loadTrackingFeed` for why that read is wrong on every cold launch.
    @Published private(set) var assetsRequiresSignIn: Bool = false
    /// A credential is stored but not armed yet: "Reconnecting…", never the sign-in prompt
    /// (auth.md §5).
    @Published private(set) var assetsIsReconnecting: Bool = false

    /// The Whales half's own pair. See `assetsRequiresSignIn` for why it is not shared.
    @Published private(set) var whalesRequiresSignIn: Bool = false
    @Published private(set) var whalesIsReconnecting: Bool = false

    /// Either half is showing the account gate — what `TrackingView`'s session-healed trigger
    /// keys on, so a heal reloads the screen if ANY part of it is waiting on the session.
    var isAnySurfaceGated: Bool {
        assetsRequiresSignIn || assetsIsReconnecting || whalesRequiresSignIn || whalesIsReconnecting
    }

    // Sheet States
    @Published var showAddAssetSheet: Bool = false
    @Published var showSortSheet: Bool = false
    @Published var showPortfolioConfigSheet: Bool = false
    @Published var showNewPortfolioSheet: Bool = false
    @Published var showEditPortfolioSheet: Bool = false
    @Published var showManageTickersSheet: Bool = false

    /// Tickers the user just added via the in-sheet star button, keyed by
    /// the portfolio they were added to. Used to fill the star instantly
    /// while the server round-trip is in flight; cleared per-entry once the
    /// real `portfolioStore.activePortfolio.tickers` reflects the new row.
    /// Per-portfolio scoping prevents an optimistic add to "Holdings" from
    /// leaking into the star state when the user switches to "Tech".
    @Published var recentlyAddedTickers: [String: Set<String>] = [:]

    // Navigation States
    @Published var selectedAssetNavigation: SearchSelection?
    @Published var selectedSearchResult: SearchSelection?
    @Published var selectedWhaleId: String?
    @Published var selectedTradeGroup: TradeGroupNavigation?
    @Published var selectedAlert: AppAlert?

    // MARK: - Init

    init(apiClient: APIClient = .shared, portfolioStore: PortfolioStore? = nil) {
        self.apiClient = apiClient
        // `.shared` is @MainActor-isolated; defaulting the parameter to it
        // crosses the isolation boundary at the call site. Resolve here
        // instead — this initializer is itself @MainActor.
        self.portfolioStore = portfolioStore ?? PortfolioStore.shared
        // The one reference to the shared snapshot store. Nothing is read here: the file is
        // read lazily by `prepareSnapshot()` from the tab's `.task`, after Home has painted.
        self.snapshotStore = TrackingSnapshotStore.shared
        self.isInsightsEnabled = UserDefaults.standard.bool(forKey: Self.insightsEnabledKey)

        // Restore persisted sort preferences. Sort lives on the VM so it
        // applies to whichever portfolio is active — the menu in the new
        // PortfolioHeaderBar writes to the same keys.
        if let raw = UserDefaults.standard.string(forKey: Self.sortOptionKey),
           let restored = AssetSortOption(rawValue: raw) {
            self.sortOption = restored
        }
        if UserDefaults.standard.object(forKey: Self.sortAscendingKey) != nil {
            self.sortAscending = UserDefaults.standard.bool(forKey: Self.sortAscendingKey)
        }
        if let raw = UserDefaults.standard.string(forKey: Self.changeDisplayModeKey),
           let restored = ChangeDisplayMode(rawValue: raw) {
            self.changeDisplayMode = restored
        }
        

        NotificationCenter.default.publisher(for: .whaleFollowStateChanged)
            .receive(on: RunLoop.main)
            .sink { [weak self] notification in
                self?.handleFollowStateChange(notification)
            }
            .store(in: &cancellables)

        // Heal displayed follow state whenever the AUTHORITATIVE set changes —
        // a backend follow/unfollow that FAILED and reverted, a cross-device
        // change, or an optimistic toggle. Without this the row could show
        // "Following" while WhaleService/server disagreed, and since the request
        // direction is derived from `followedWhaleIds`, the next tap would send
        // the OPPOSITE request. Reconciling both display and request direction
        // to one source of truth closes that race.
        WhaleService.shared.$followedWhaleIds
            .receive(on: RunLoop.main)
            .sink { [weak self] ids in
                self?.reconcileFollowState(with: ids)
            }
            .store(in: &cancellables)

        // Recent Trades is a SERVER-derived feed keyed on the follow set, so unlike the
        // whale rows above it cannot be patched locally — it has to be re-fetched. Nothing
        // used to do that, and because ContentView opacity-mounts every tab this ViewModel
        // survives the whole process: the feed loaded in `init` was the only one the user
        // ever saw, so a newly-followed whale's trades appeared only after a force-quit.
        //
        // Observed HERE rather than in a view's `.onReceive` because WhaleProfileView is
        // pushed OVER this tab — a follow performed there must still land, and the whales
        // tab content is not the thing receiving events at that moment.
        NotificationCenter.default.publisher(for: WhaleService.followsDidChangeNotification)
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in
                self?.reloadForFollowChange()
            }
            .store(in: &cancellables)

        // A star tapped on a detail screen (or a toggle in Updates › Manage Assets) changed
        // the watchlist — and, through the backend's write-through, the active group.
        // Observed HERE for the same reason as the follow signal above: the detail screen
        // is pushed OVER this tab inside its own NavigationStack, `isActiveTab` never
        // flips, `loadIfNeeded` is latched, and the 30 s timer reloads only the feed
        // (never the portfolios) — so a removed row stayed until pull-to-refresh and an
        // added one never appeared at all.
        NotificationCenter.default.publisher(for: PortfolioStore.watchlistDidChangeNotification)
            .receive(on: RunLoop.main)
            .sink { [weak self] notification in
                guard let change = WatchlistChange(notification) else { return }
                self?.handleWatchlistChange(change)
            }
            .store(in: &cancellables)

        // Republish whenever the portfolio store changes so filteredAssets,
        // filteredAlerts, and portfolioDiversificationScore re-render.
        self.portfolioStore.objectWillChange
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)

        // A server-confirmed portfolio write (here, in a sheet, or the search star) makes the
        // saved Holdings snapshot describe something the user no longer has — another group's
        // rows and score, a removed ticker. Drop it; the next live load saves a fresh one.
        // Synchronous (no `receive(on:)`): the purge's epoch bump must land before any save
        // that could still be pending in this run-loop turn.
        self.portfolioStore.$confirmedMutationCount
            .dropFirst()
            .sink { [weak self] _ in self?.discardSnapshotAfterConfirmedEdit() }
            .store(in: &cancellables)

        // Deliberately NO load here — same rule as `UpdatesViewModel.init`.
        //
        // `ContentView` opacity-mounts all five tabs in one ZStack, so this initializer runs
        // at app launch for every user, including one who never opens the Assets tab. That
        // made `loadData()` — which is five requests (`/tracking/assets`, `/portfolios`,
        // `/whales`, `/whales/activity`, `/portfolios/{id}/insights`) — unconditional launch
        // traffic, and it started the 30s price-refresh timer for a screen nobody was
        // looking at. The view calls `loadIfNeeded()` when the tab first becomes active.
    }

    /// Called when the Assets tab becomes visible. Idempotent.
    ///
    /// Pairs with `.reloadOnIdentityChange`, which covers signing in or out while already on
    /// the tab; this covers the first visit. `loadData()`'s own single-flight guard means an
    /// overlap between the two costs one request, not two.
    func loadIfNeeded() async {
        guard !hasLoadedOnce else {
            // RE-ACTIVATION. `TrackingView`'s `.task(id: isActiveTab)` teardown now calls
            // `stopPriceRefreshTimer()` when the tab goes away, and `ContentView` keeps
            // every tab's `@StateObject` alive — so `hasLoadedOnce` is still true when the
            // user comes back and this guard returns before ever reaching the
            // `startPriceRefreshTimer()` below. The stop had a caller and the start did
            // not: after one tab switch, Holdings prices and P/L froze for the rest of the
            // process, silently. Restarting here is safe — the timer cancels any prior
            // task before arming a new one.
            startPriceRefreshTimer()
            // A first load that FAILED (feed or /portfolios) is retried once per activation —
            // never from the timer, never while the session is unarmed.
            retryFailedLoadIfNeeded()
            return
        }
        let generation = loadGeneration
        await loadData()
        // Latch on the LOAD finishing for this identity, not on the awaiting `.task` surviving.
        // The load runs in a ViewModel-owned task that a tab-away does not cancel, so the old
        // `Task.isCancelled` latch threw a COMPLETED load away and the next activation re-ran
        // all five requests. An identity change in between owns the latch instead.
        guard generation == loadGeneration else { return }
        hasLoadedOnce = true
        // …but the poll is never armed behind a tab that is no longer on screen: the `.task`
        // teardown has already stopped it, and nothing would stop it again.
        guard !Task.isCancelled else { return }
        startPriceRefreshTimer()
    }

    /// Re-run a first load that failed, on a later activation. Keyed on what the screen is
    /// MISSING: a feed that is live but had one failed 30 s poll is the poll's job, not a reason
    /// to re-issue five requests.
    private func retryFailedLoadIfNeeded() {
        guard !hasLiveHoldings else { return }
        // A refusal is deterministic until the session heals (the root's auth-status heal
        // reloads then), and a load already running will settle the question itself.
        guard !assetsRequiresSignIn, !assetsIsReconnecting, loadTask == nil else { return }
        let feedFailed = assetsErrorMessage != nil
        let portfoliosFailed = portfolioStore.loadErrorMessage != nil && !portfolioStore.hasLiveData
        guard feedFailed || portfoliosFailed else { return }
        Self.log.info("tracking: retrying a failed first load on activation")
        Task { [weak self] in await self?.loadData() }
    }

    // MARK: - Device snapshot

    /// Read this account's saved Holdings from disk (once per binding) and, if nothing live is
    /// on screen yet, show it. Disk only — never a request — so the hidden tab may run it.
    /// Called from both branches of the tab's `.task(id: isActiveTab)` and from
    /// `handleIdentityChange` (after its clears, above its active-tab gate).
    func prepareSnapshot() async {
        expireSnapshotIfStale()
        await snapshotStore.prepare(apiClient: apiClient)
        dropSeedIfEpochMoved()
        seedFromSnapshot()
    }

    /// Show the saved snapshot — display state ONLY. The precondition is the first statement:
    /// nothing live for this identity yet, not gated, and the snapshot is about the group this
    /// device has active (another group's rows under this group's header would be false).
    private func seedFromSnapshot() {
        guard snapshotSeed == nil, !hasLiveHoldings, trackedAssets.isEmpty,
              !assetsRequiresSignIn, !assetsIsReconnecting,
              let snapshot = snapshotStore.snapshotForDisplay(),
              snapshot.payload.activePortfolioId == portfolioStore.activePortfolioId else { return }
        snapshotSeedSavedAt = snapshot.savedAt
        seededEpoch = snapshotStore.epoch
        seedShownAt = Date()
        snapshotSeed = snapshot.payload
        let age: TimeInterval = Date().timeIntervalSince(snapshot.savedAt)
        let ageSeconds: Int = age.isFinite ? Int(max(0, min(age, 31_536_000))) : 0
        let rowCount: Int = snapshot.payload.holdingsRows.count
        Self.log.info("tracking: showing the saved snapshot — \(ageSeconds, privacy: .public) s old, \(rowCount, privacy: .public) holdings")
    }

    /// A seed taken under a store binding that has since moved (another account bound, the
    /// session ended, a purge) is not this binding's answer any more.
    private func dropSeedIfEpochMoved() {
        guard snapshotSeed != nil, let seeded = seededEpoch, seeded != snapshotStore.epoch else { return }
        snapshotSeed = nil
        Self.log.info("tracking: dropped a snapshot seed from a previous store binding")
    }

    /// Drop an on-screen snapshot that aged past the 96 h display window (the app sat in the
    /// background, or every live load since launch failed). Live data is never touched.
    func expireSnapshotIfStale(now: Date = Date()) {
        guard snapshotSeed != nil, let savedAt = snapshotSeedSavedAt,
              !AccountSnapshotPolicy.isDisplayable(savedAt: savedAt, now: now) else { return }
        snapshotSeed = nil
        Self.log.info("tracking: the on-screen snapshot aged out of its display window")
    }

    /// A server-confirmed portfolio write: the saved file AND the seed on screen describe what
    /// the user no longer has. The seed goes too — it can be on screen while the write lands
    /// (feed failed, `/portfolios` live, so the edit entry points are open), and a removed
    /// ticker left in Holdings beside the "Updated <time>" label reads as a failed removal. The
    /// screen falls back to the live list (or the feed's own error with its Retry).
    private func discardSnapshotAfterConfirmedEdit() {
        snapshotStore.purgeCache()
        // The snapshot's score, carried across the swap, was computed for the pre-edit group.
        insightsCarriedFromSnapshot = nil
        guard snapshotSeed != nil else { return }
        snapshotSeed = nil
        Self.log.info("tracking: a confirmed portfolio edit dropped the on-screen snapshot")
    }

    /// A removal the server confirmed ELSEWHERE (a detail screen, Updates › Manage Assets): the
    /// saved file still lists the ticker, so it goes — the next cold launch must not paint it
    /// back (the next live load saves a fresh one). The seed on screen was patched by the
    /// caller and stays: this purge is the one epoch move it is excused from, and only when
    /// the seed was current for the binding just before it.
    private func purgeSnapshotAfterRemovalElsewhere() {
        let seedWasCurrent: Bool = snapshotSeed != nil && seededEpoch == snapshotStore.epoch
        snapshotStore.purgeCache()
        if seedWasCurrent { seededEpoch = snapshotStore.epoch }
    }

    deinit {
        priceRefreshTask?.cancel()
        activityReloadTask?.cancel()
        watchlistReloadTask?.cancel()
    }

    // MARK: - Computed Properties

    /// Tickers in the LIVE active portfolio, uppercased Set for O(1) membership. Alerts read
    /// this and stay live-only: a snapshot never scopes them.
    private var activeTickerSet: Set<String> {
        Set(portfolioStore.activePortfolio?.tickers.map { $0.uppercased() } ?? [])
    }

    // MARK: Live or snapshot — all or nothing

    /// Both halves of THIS identity's Holdings answered live: the feed and `/portfolios`.
    var hasLiveHoldings: Bool { hasLiveFeed && portfolioStore.hasLiveData }

    /// The saved snapshot, when it is what the screen should draw: nothing live yet, not gated,
    /// inside the display window, and about the group this device has active. A group switch
    /// (or a live `/portfolios` naming another active group) hides it at once.
    var presentedSnapshot: TrackingSnapshot? {
        guard let seed = snapshotSeed, let savedAt = snapshotSeedSavedAt,
              !hasLiveHoldings, !assetsRequiresSignIn, !assetsIsReconnecting,
              seed.activePortfolioId == portfolioStore.activePortfolioId,
              AccountSnapshotPolicy.isDisplayable(savedAt: savedAt, now: Date()) else { return nil }
        return seed
    }

    var isShowingSnapshot: Bool { presentedSnapshot != nil }

    /// When the presented snapshot was saved; nil whenever live data (or nothing) is shown.
    var snapshotSavedAt: Date? { presentedSnapshot == nil ? nil : snapshotSeedSavedAt }

    /// "Updated 4:02 PM" / "Updated Sep 28, 4:02 PM" — the one shared wording source.
    var snapshotUpdatedLabel: String? {
        guard let savedAt = snapshotSavedAt else { return nil }
        return AccountSnapshotPolicy.updatedLabel(savedAt: savedAt)
    }

    /// The group the screen describes: the snapshot's while it is presented, else the live one.
    var presentedPortfolio: Portfolio? {
        if let seed = presentedSnapshot { return seed.activePortfolio }
        return portfolioStore.activePortfolio
    }

    /// Group and holdings edits need a LIVE `/portfolios` list (every write is built from it).
    var canEditPortfolio: Bool { portfolioStore.hasLiveData }

    /// Row actions (swipe / long-press remove) need live rows too: a snapshot row's membership
    /// is not the store's.
    var canEditHoldings: Bool { portfolioStore.hasLiveData && !isShowingSnapshot }

    /// The Holdings failure to show: the feed's, or — with no live list at all — `/portfolios`'.
    var holdingsErrorMessage: String? {
        if let feedError = assetsErrorMessage { return feedError }
        return portfolioStore.hasLiveData ? nil : portfolioStore.loadErrorMessage
    }

    /// A load (or pull-to-refresh) is on the wire for the Holdings list.
    var isRefreshingHoldings: Bool { isLoading || isRefreshing || portfolioStore.isLoading }

    /// The snapshot is on screen and the refresh behind it has finished without replacing it.
    var snapshotRefreshFailed: Bool {
        isShowingSnapshot && !isRefreshingHoldings && holdingsErrorMessage != nil
    }

    /// The copy a blocked edit reports (auth.md §6: nothing is ever silent).
    static let portfolioStillLoadingMessage = "Your portfolio is still loading — try again in a moment."

    private func reportPortfolioStillLoading(action: String) {
        AppActions.shared.reportMutationFailure(
            APIError.unknown(message: Self.portfolioStillLoadingMessage), action: action
        )
    }

    var filteredAssets: [TrackedAsset] {
        let seed: TrackingSnapshot? = presentedSnapshot
        let portfolio: Portfolio? = seed != nil ? seed?.activePortfolio : portfolioStore.activePortfolio
        // The group's tickers in their stored order — `.dateAdded` means "order in the
        // portfolio", the closest analogue to the old "added at" concept.
        let order: [String] = (portfolio?.tickers ?? []).map { $0.uppercased() }
        let active = Set(order)
        let source: [TrackedAsset] = seed?.assets ?? trackedAssets
        var assets = source.filter { active.contains($0.ticker.uppercased()) }

        // Apply search filter
        if !searchText.isEmpty {
            assets = assets.filter { asset in
                asset.ticker.localizedCaseInsensitiveContains(searchText) ||
                asset.companyName.localizedCaseInsensitiveContains(searchText)
            }
        }

        // Apply sorting
        switch sortOption {
        case .name:
            assets.sort { sortAscending ? $0.ticker < $1.ticker : $0.ticker > $1.ticker }
        case .price:
            assets.sort { sortAscending ? $0.price < $1.price : $0.price > $1.price }
        case .change:
            assets.sort { sortAscending ? $0.changePercent < $1.changePercent : $0.changePercent > $1.changePercent }
        case .marketCap:
            // Assets without a market cap (e.g. crypto, indices) sort to the
            // bottom in ascending order, top in descending — matches how the
            // Watchlist screen handles missing fundamentals.
            assets.sort { lhs, rhs in
                switch (lhs.marketCap, rhs.marketCap) {
                case let (l?, r?):
                    return sortAscending ? l < r : l > r
                case (nil, _?):
                    return !sortAscending
                case (_?, nil):
                    return sortAscending
                case (nil, nil):
                    return sortAscending ? lhs.ticker < rhs.ticker : lhs.ticker > rhs.ticker
                }
            }
        case .dateAdded:
            // "Date added" now means position in the active portfolio (`order` above).
            // first-wins rather than `uniqueKeysWithValues:`, which TRAPS on a duplicate
            // key. A repeated ticker in `order` would crash the sort; the earliest
            // position is the right one to keep.
            let positions = Dictionary(
                order.enumerated().map { ($1, $0) },
                uniquingKeysWith: { first, _ in first })
            assets.sort { lhs, rhs in
                let l = positions[lhs.ticker.uppercased()] ?? Int.max
                let r = positions[rhs.ticker.uppercased()] ?? Int.max
                return sortAscending ? l < r : l > r
            }
        }

        return assets
    }

    /// Alerts scoped strictly to the active portfolio. Multi-ticker rollups
    /// are trimmed to only their portfolio members, and dollar totals are
    /// re-aggregated from per-item raw amounts so the displayed total always
    /// matches the displayed ticker list. `.market` events have no ticker
    /// and are always shown (macro relevance).
    var filteredAlerts: [AppAlert] {
        let active = activeTickerSet
        return alerts.compactMap { alert -> AppAlert? in
            switch alert {
            case .market:
                return alert
            case .earnings(let data):
                return active.contains(data.ticker.uppercased()) ? alert : nil
            case .whaleTrade(let data):
                let trimmed = data.items.filter { active.contains($0.ticker.uppercased()) }
                guard !trimmed.isEmpty else { return nil }
                return .whaleTrade(AppAlert.WhaleTradeAlertData(
                    action: data.action,
                    totalAmount: Self.recomputedWhaleTotal(
                        trimmed, fallback: data.totalAmount
                    ),
                    timeWindowLabel: data.timeWindowLabel,
                    items: trimmed
                ))
            case .analystRating(let data):
                let trimmed = data.items.filter { active.contains($0.ticker.uppercased()) }
                guard !trimmed.isEmpty else { return nil }
                return .analystRating(AppAlert.AnalystRatingAlertData(
                    timeWindowLabel: data.timeWindowLabel,
                    items: trimmed
                ))
            case .insiderTransaction(let data):
                let trimmed = data.items.filter { active.contains($0.ticker.uppercased()) }
                guard !trimmed.isEmpty else { return nil }
                return .insiderTransaction(AppAlert.InsiderTransactionAlertData(
                    action: data.action,
                    totalAmount: Self.recomputedTotal(
                        trimmed.compactMap(\.rawAmount),
                        fallback: data.totalAmount,
                        expectedCount: trimmed.count
                    ),
                    timeWindowLabel: data.timeWindowLabel,
                    items: trimmed
                ))
            }
        }
    }

    /// Sum trimmed items' raw amounts and format. If any item is missing
    /// `rawAmount` (older backend), fall back to the server-supplied label
    /// rather than print a misleading $0.
    private static func recomputedTotal(
        _ amounts: [Double], fallback: String, expectedCount: Int
    ) -> String {
        guard amounts.count == expectedCount else { return fallback }
        return formatDollars(amounts.reduce(0, +))
    }

    /// Re-aggregate a trimmed whale-trade rollup honestly. Congress items carry
    /// STOCK Act bounds (`rawAmountLow`/`High`); if any trimmed item is a range
    /// (or open-ended) the total is a summed RANGE — never a fabricated precise
    /// dollar. A 13F-only trimmed set (all exact points) collapses to one figure.
    /// Falls back to the server label if any item lacks bounds (older backend).
    private static func recomputedWhaleTotal(
        _ items: [AppAlert.WhaleTradeItem], fallback: String
    ) -> String {
        guard items.allSatisfy({ $0.rawAmountLow != nil }) else { return fallback }
        let low = items.reduce(0.0) { $0 + ($1.rawAmountLow ?? 0) }
        // If ANY trimmed item is congressional, the total is a STOCK Act
        // range/estimate — never a bare precise dollar (mirrors backend
        // _format_amount_or_range). 13F-only sets collapse to one exact figure.
        if items.contains(where: { $0.isCongress }) {
            let hasOpenEnded = items.contains { $0.rawAmountHigh == nil }
            let high: Double? = hasOpenEnded
                ? nil
                : items.reduce(0.0) { $0 + ($1.rawAmountHigh ?? 0) }
            if let high, abs(high - low) < 1 {
                // Bounds collapsed (malformed/single-value) — mark as estimate.
                return low >= 1 ? "~\(formatDollars(low))" : "—"
            }
            return formatAmountRange(low: low, high: high)
        }
        return formatDollars(low)  // all exact points (13F)
    }

    /// Mirrors backend `format_amount_range` in _whale_common.py.
    private static func formatAmountRange(low: Double, high: Double?) -> String {
        guard let high else { return "\(formatDollars(low))+" }
        if abs(high - low) < 1 { return formatDollars(low) }
        return "\(formatDollars(low)) – \(formatDollars(high))"
    }

    /// Mirrors backend `_format_amount` in tracking_service.py so re-aggregated
    /// totals look identical to alerts that come straight from the server.
    private static func formatDollars(_ value: Double) -> String {
        let amt = abs(value)
        // Roll up to the next unit when rounding would render a 4-digit
        // mantissa in the lower unit (mirrors backend _format_amount):
        // 999_600 → "$1.0M" (not "$1000K"), 999_960_000 → "$1.00B".
        if amt >= 999_950_000 { return String(format: "$%.2fB", amt / 1_000_000_000) }
        if amt >= 999_500     { return String(format: "$%.1fM", amt / 1_000_000) }
        if amt >= 1_000       { return String(format: "$%.0fK", amt / 1_000) }
        return String(format: "$%.0f", amt)
    }

    /// Diversification score computed locally from the active portfolio's
    /// per-portfolio holdings (`shares` / `marketValue` on each
    /// `PortfolioItem`). Joined with `trackedAssets` for the live price (so
    /// share-count entries can be converted to dollars) and the price-feed
    /// metadata (sector, asset_type, country, company name) that the
    /// calculator needs but the per-portfolio item doesn't carry.
    var portfolioDiversificationScore: DiversificationScore? {
        guard let active = portfolioStore.activePortfolio else { return nil }
        // first-wins: `.uppercased()` COLLAPSES case-differing rows ("brk.b" and
        // "BRK.B") onto one key, and `uniqueKeysWithValues:` traps on that.
        let assetsByTicker = Dictionary(
            trackedAssets.map { ($0.ticker.uppercased(), $0) },
            uniquingKeysWith: { first, _ in first })

        let holdings: [PortfolioHolding] = active.items.compactMap { item in
            guard item.isHolding else { return nil }
            let asset = assetsByTicker[item.ticker.uppercased()]

            // The user enters EITHER shares OR a dollar amount per ticker.
            // For shares-only entries we multiply by the live price so the
            // calculator gets a non-zero dollar weight — without this the
            // score collapses to nil whenever every holding was entered as
            // shares (the storage column for market_value stays null).
            let effectiveMarketValue: Double
            if let mv = item.marketValue, mv > 0 {
                effectiveMarketValue = mv
            } else if let shares = item.shares, shares > 0,
                      let price = asset?.price, price > 0 {
                effectiveMarketValue = shares * price
            } else {
                effectiveMarketValue = 0
            }

            let assetTypeLower = (asset?.assetType ?? "stock").lowercased()
            let mappedAssetType: AssetType
            switch assetTypeLower {
            case "etf":     mappedAssetType = .etf
            case "bond":    mappedAssetType = .bond
            case "crypto":  mappedAssetType = .crypto
            case "cash":    mappedAssetType = .cash
            default:
                mappedAssetType = (asset?.country ?? "US") == "US" ? .stock : .internationalStock
            }
            return PortfolioHolding(
                ticker: item.ticker.uppercased(),
                companyName: asset?.companyName ?? item.ticker,
                marketValue: effectiveMarketValue,
                shares: item.shares,
                sector: asset?.sector,
                assetType: mappedAssetType,
                country: asset?.country ?? "US",
                marketCap: asset?.marketCap
            )
        }
        return DiversificationCalculator.calculate(holdings: holdings)
    }

    /// Caption shown next to the diversification score telling the user how
    /// many of the active portfolio's tickers actually contributed to it
    /// (i.e. have shares or a dollar amount). Nil when there's no active
    /// portfolio or it has no tickers — the score itself is also nil in
    /// those cases, so the caption simply hides with the card.
    var portfolioInsightsCoverageNote: String? {
        // The group the screen describes — the snapshot's while it is presented, so the
        // caption counts the same group the (snapshot's) score was computed for.
        guard let active = presentedPortfolio,
              !active.items.isEmpty else { return nil }
        // Prefer what was SCORED. The client-side `isHolding` count includes a shares-only
        // row the server could not price (stored value 0 → dropped before weighting), so
        // "Based on 3 of 3" could sit over a score computed from two. Clamped to the local
        // total: the score is the LAST server answer, and an in-tab removal shrinks
        // `active.items` before the insights reload lands — "3 of 2" otherwise.
        let total = active.items.count
        let used = min(
            displayedDiversificationScore?.holdingsCount
                ?? active.items.filter { $0.isHolding }.count,
            total
        )
        let noun = total == 1 ? "ticker" : "tickers"
        return "Based on \(used) of \(total) \(noun)"
    }

    /// The one-line hint under the Diversification verdict, or nil. Keyed on the SCORED
    /// count (server online, calculator offline) so it can never disagree with the
    /// coverage note above it; a score that carries no count says nothing.
    var portfolioInsightsHint: String? {
        guard let score = displayedDiversificationScore,
              let scored = score.holdingsCount,
              let active = presentedPortfolio else { return nil }
        // Same clamp as the coverage note: the scored count is the last server answer.
        return DiversificationHint.make(
            scoredHoldings: min(scored, active.items.count),
            enteredTickers: enteredHoldingsCount,
            totalTickers: active.items.count
        )
    }

    /// How many of the active portfolio's tickers have shares or a dollar
    /// amount entered (i.e. count toward the score). Drives the "add at least
    /// N holdings" hint: when the user has entered some holdings but fewer than
    /// `DiversificationThresholds.minimumHoldings`, the score is nil and we want
    /// to tell them why instead of showing the blank first-run empty state.
    var enteredHoldingsCount: Int {
        presentedPortfolio?.items.filter { $0.isHolding }.count ?? 0
    }

    var filteredWhaleActivities: [WhaleActivity] {
        // Filter by category if needed
        // For now, return all activities
        whaleActivities
    }

    // MARK: - Data Loading (Real API)

    /// Whether a genuine load has completed. Reset by `handleIdentityChange`.
    private var hasLoadedOnce = false

    /// The load currently in flight, if any. Concurrent callers JOIN it.
    ///
    /// `isLoading` was published but never consulted, so every trigger became a real
    /// request: tab activation and the identity-change reload land within milliseconds of
    /// each other on a signed-in launch, and each one fanned out five calls.
    private var loadTask: Task<Void, Never>?

    func loadData() async {
        // Join a running load rather than starting a second one. Awaiting the SAME task
        // (rather than returning early) matters: callers use completion to decide what to
        // render, so an early return would report "done" while the data was still arriving.
        if let running = loadTask, !running.isCancelled {
            await running.value
            return
        }
        let task = Task { [weak self] in
            guard let self else { return }
            await self.performLoad()
            // Cleared HERE, as the task's own last act, rather than after `await
            // task.value` below. Between a task finishing and its awaiting caller
            // being resumed, a THIRD caller can run and observe a completed-but-still
            // -registered task: it would "join" something already done and return
            // instantly without ever loading. That is a silently skipped refresh —
            // and for `handleIdentityChange` it would mean adopting a load that
            // completed under the PREVIOUS identity.
            //
            // Not when cancelled: `handleIdentityChange` cancels this task and clears the slot
            // itself, and the next identity's load may already be registered there.
            if !Task.isCancelled { self.loadTask = nil }
        }
        loadTask = task
        await task.value
    }

    private func performLoad() async {
        let generation = loadGeneration
        isLoading = true
        defer { if generation == loadGeneration { isLoading = false } }

        // Captured BEFORE the first request (an `async let` starts its request): a purge after a
        // confirmed edit, a re-bind or a session end while this load is in flight voids the save.
        let snapshotEpoch = snapshotStore.epoch

        // INSIGHTS start beside phase 1, for the group this device last had active (the
        // UserDefaults hint) — and are never awaited before the gate below closes. A stale hint
        // is fenced by request token and group id, and refetched for the live group.
        insightsRequestToken &+= 1
        let earlyInsightsToken = insightsRequestToken
        let hintedPortfolioId = portfolioStore.activePortfolioId
        markPortfolioInsightsResolving(for: hintedPortfolioId)
        async let earlyInsights: InsightsFetch = fetchPortfolioInsights(for: hintedPortfolioId)

        // PHASE 1 — only what the Assets tab actually draws.
        //
        // Whale list + whale activity used to be awaited here too, and `isLoading` gates the
        // whole screen, so the visible tab sat behind a spinner waiting for two responses it
        // never renders (`AssetsTabContent` references no whale state at all). Measured warm
        // against production: feed 0.21s, portfolios 0.42s, whales 0.13s + 0.10s, then a
        // SEQUENTIAL insights hop on top — ~0.6–1.0s of gate for ~0.4s of needed data.
        async let feedTask: Bool = loadTrackingFeed()
        async let portfoliosTask: Bool = portfolioStore.loadPortfolios()

        let (feedSucceeded, portfoliosSucceeded) = await (feedTask, portfoliosTask)
        // BOTH halves live, for THIS identity. Anything less writes nothing back and saves
        // nothing: a purge from a list that is not live deletes what the user added since.
        let bothLive = feedSucceeded && portfoliosSucceeded && generation == loadGeneration

        // The snapshot's inputs, captured NOW — every one from THIS load, before the purge or any
        // other suspension lets a poll, a swipe or a reconcile move them.
        let capturedAt = Date()
        let capturedFeedBody = lastLiveFeedBody
        let capturedPortfoliosBody = portfolioStore.lastLiveBody
        let capturedActiveId = portfolioStore.activePortfolioId
        let capturedAssets = trackedAssets
        let capturedPortfolios = portfolioStore.portfolios

        // Drop tickers from any portfolio that no longer exist on the master
        // watchlist (e.g. removed on another device). Only when BOTH halves of this load
        // succeeded live — otherwise we'd wipe real tickers off portfolios on a transient
        // network failure, or rewrite membership the server never sent this session.
        //
        // The allow-set is unioned with anything the user just added: the feed is
        // cached server-side for 30s, so a refresh fired immediately after an add
        // can still return the PRE-ADD list, and the brand-new ticker would look
        // like an orphan and be deleted again — the add silently undoing itself.
        // (The backend now invalidates that cache on write too; this is the
        // client-side belt to that braces.) `purgeTickers` additionally refuses an
        // empty allow-set and a store with no live answer outright — see PortfolioStore.
        var purged = false
        if bothLive {
            var allowed = Set(trackedAssets.map(\.ticker))
            for pending in recentlyAddedTickers.values {
                allowed.formUnion(pending)
            }
            purged = await portfolioStore.purgeTickers(notIn: allowed)
        }

        // A load that left under the previous identity publishes nothing more.
        guard generation == loadGeneration else { return }
        if bothLive { replaceSnapshotWithLiveHoldings() }
        hasAttemptedLoad = true

        // The Assets tab is renderable from here. Closing the gate now is the whole point:
        // everything below is drawn by other surfaces and must not hold this one.
        //
        // The `defer` above stays as the cancellation safety net — setting it twice is
        // idempotent, and on a cancelled load the defer is the only thing that runs.
        isLoading = false

        // PHASE 2 — off the critical path, but still AWAITED.
        //
        // Not detached: `loadIfNeeded()` latches `hasLoadedOnce` on this function returning,
        // and `refresh()` drives pull-to-refresh's spinner from it. Fire-and-forget here would
        // latch "loaded" before the data existed and end the refresh gesture early.
        async let whalesTask: () = loadWhaleData()

        // The early insights answer counts only for the group that is active NOW; a hint that
        // named another group (or a purge that changed membership) refetches for the live one.
        let early = await earlyInsights
        var settled: InsightsFetch? = publishPortfolioInsights(early, token: earlyInsightsToken) ? early : nil
        if purged || (settled == nil && earlyInsightsToken == insightsRequestToken) {
            settled = await requestPortfolioInsights()
        }

        // ONE save, after a LIVE answer from both halves of this load, never from a failure
        // path and never from the poll. A purge that wrote means the bodies are already stale.
        if bothLive, !purged, generation == loadGeneration,
           let feedBody = capturedFeedBody, let portfoliosBody = capturedPortfoliosBody {
            var insightsBody: Data?
            var insights: TrackingSnapshot.Insights = .unknown
            if case .answered(let portfolioId, let score, let body)? = settled, portfolioId == capturedActiveId {
                insightsBody = body
                insights = .known(score)
            }
            var parts: [String: Data] = [:]
            parts[TrackingSnapshot.assetsPart] = feedBody
            parts[TrackingSnapshot.portfoliosPart] = portfoliosBody
            parts[TrackingSnapshot.activePart] = TrackingSnapshot.activePartBody(capturedActiveId)
            if let insightsBody { parts[TrackingSnapshot.insightsPart] = insightsBody }
            let payload = TrackingSnapshot(
                assets: capturedAssets, portfolios: capturedPortfolios,
                activePortfolioId: capturedActiveId, insights: insights
            )
            snapshotStore.save(parts: parts, payload: payload, savedAt: capturedAt, epoch: snapshotEpoch)
        }

        _ = await whalesTask
    }

    /// Both live halves landed: the labelled snapshot steps aside for them.
    private func replaceSnapshotWithLiveHoldings() {
        guard snapshotSeed != nil else { return }
        var shownMillis = 0
        if let shownAt = seedShownAt {
            let elapsed: TimeInterval = Date().timeIntervalSince(shownAt) * 1000
            shownMillis = elapsed.isFinite ? Int(max(0, min(elapsed, 86_400_000))) : 0
        }
        // The score for this group is usually still on the wire: its kept answer stays on the
        // card until the live one lands (the phase's didSet drops it then).
        if portfolioInsightsPhase == .resolving, let kept = keptSeedInsightsForLiveGroup {
            insightsCarriedFromSnapshot = kept
        }
        snapshotSeed = nil
        Self.log.info("tracking: live holdings replaced the snapshot after \(shownMillis, privacy: .public) ms")
    }

    // MARK: - Portfolio Insights

    /// One insights request's outcome, before it is allowed onto the card.
    private enum InsightsFetch {
        case answered(portfolioId: String, score: DiversificationScore?, body: Data)
        case failed(portfolioId: String, network: Bool, cancelled: Bool, detail: String)
        case noPortfolio
    }

    /// The request alone — it publishes nothing. `requestReturningBody` is `request<T>` plus
    /// the exact bytes, so the answer can ride in the Holdings snapshot.
    private func fetchPortfolioInsights(for portfolioId: String?) async -> InsightsFetch {
        guard let portfolioId else { return .noPortfolio }
        do {
            let (dto, body) = try await apiClient.requestReturningBody(
                endpoint: .getPortfolioInsightsForPortfolio(id: portfolioId),
                responseType: PortfolioInsightsDTO?.self
            )
            return .answered(portfolioId: portfolioId, score: dto?.toDiversificationScore(), body: body)
        } catch {
            var network = false
            if let apiError = error as? APIError, case .networkError = apiError {
                network = true
            }
            let cancelled = AppError.from(error).isCancellation
            return .failed(
                portfolioId: portfolioId, network: network, cancelled: cancelled,
                detail: "\(type(of: error)): \(error)"
            )
        }
    }

    /// Put an answer on the card — only if its request is still the current one AND it is
    /// about the group that is active now. Returns whether it was published.
    private func publishPortfolioInsights(_ fetch: InsightsFetch, token: Int) -> Bool {
        guard token == insightsRequestToken else { return false }
        switch fetch {
        case .noPortfolio:
            guard portfolioStore.activePortfolioId == nil else { return false }
            portfolioInsights = nil
            portfolioInsightsLoadFailed = false
            portfolioInsightsPortfolioId = nil
            // "No group" is an answer only when a LIVE /portfolios said so; with no live list it
            // is still unknown (the card then shows the /portfolios failure, never "Set up").
            portfolioInsightsPhase = portfolioStore.hasLiveData ? .known : .idle
            return true
        case .answered(let portfolioId, let score, _):
            guard portfolioId == portfolioStore.activePortfolioId else { return false }
            portfolioInsights = score
            portfolioInsightsLoadFailed = false
            portfolioInsightsPortfolioId = portfolioId
            portfolioInsightsPhase = .known
            return true
        case .failed(let portfolioId, let network, let cancelled, let detail):
            guard !cancelled, portfolioId == portfolioStore.activePortfolioId else { return false }
            portfolioInsights = nil
            portfolioInsightsLoadFailed = network
            portfolioInsightsPortfolioId = portfolioId
            portfolioInsightsPhase = .failed
            Self.log.warning("tracking: portfolio insights failed — \(detail, privacy: .public)")
            return true
        }
    }

    /// Ask for the active group's score now. Every caller of `loadPortfolioInsights()` lands
    /// here. Returns the outcome when it was published.
    @discardableResult
    private func requestPortfolioInsights() async -> InsightsFetch? {
        insightsRequestToken &+= 1
        let token = insightsRequestToken
        let portfolioId = portfolioStore.activePortfolioId
        markPortfolioInsightsResolving(for: portfolioId)
        let fetch = await fetchPortfolioInsights(for: portfolioId)
        if publishPortfolioInsights(fetch, token: token) { return fetch }
        // Still the current request but not publishable (the group moved under it, or it was
        // cancelled): nothing is in flight for the card any more, so it is not "resolving".
        if token == insightsRequestToken, portfolioInsightsPhase == .resolving {
            portfolioInsightsPhase = .idle
        }
        return nil
    }

    /// Fetch the server-computed diversification health score for the active
    /// portfolio. On a genuine connectivity failure we flag it so the UI can
    /// fall back to the on-device estimate; a `null` body (fewer than the
    /// minimum holdings) is a KNOWN answer, never shown as a failure.
    func loadPortfolioInsights() async {
        _ = await requestPortfolioInsights()
    }

    /// A request is starting for `portfolioId`. Another group's score never stays on screen
    /// for this one; a refresh of the SAME group keeps its answer up until the new one lands
    /// (no spinner flash on every pull-to-refresh).
    private func markPortfolioInsightsResolving(for portfolioId: String?) {
        // A request about another group retires the carried snapshot answer for good.
        if let carried = insightsCarriedFromSnapshot, carried.portfolioId != portfolioId {
            insightsCarriedFromSnapshot = nil
        }
        if portfolioId == portfolioInsightsPortfolioId, portfolioInsightsPhase == .known { return }
        if portfolioId != portfolioInsightsPortfolioId {
            portfolioInsights = nil
            portfolioInsightsLoadFailed = false
        }
        portfolioInsightsPhase = .resolving
    }

    /// The insights card's Retry. Reloads `/portfolios` first when the list itself never
    /// arrived — a score needs to know which group it is for.
    func retryPortfolioInsights() {
        guard !portfolioInsightsIsGated else { return }
        Task { [weak self] in
            guard let self else { return }
            if !self.portfolioStore.hasLiveData {
                _ = await self.portfolioStore.loadPortfolios()
            }
            await self.loadPortfolioInsights()
        }
    }

    /// The account gate is up for Holdings: the card shows a neutral line, no spinner, no
    /// Retry (a retry would re-send a request the client refuses before it leaves the device).
    var portfolioInsightsIsGated: Bool { assetsRequiresSignIn || assetsIsReconnecting }

    /// What the card may treat as ANSWERED: whether the answer is known, and the score.
    /// While the snapshot is presented, a live score counts only for the snapshot's own group;
    /// otherwise the snapshot's kept answer (if it kept one) stands in.
    private var presentedInsightsAnswer: (known: Bool, score: DiversificationScore?) {
        if portfolioInsightsIsGated { return (false, nil) }
        if let seed = presentedSnapshot {
            let sameGroup = portfolioInsightsPortfolioId == seed.activePortfolioId
            if portfolioInsightsPhase == .known, sameGroup {
                return (true, portfolioInsights)
            }
            // This session asked about the snapshot's group and FAILED: say so (with a Retry)
            // rather than keep presenting the kept score as if the refresh had worked.
            if portfolioInsightsPhase == .failed, sameGroup {
                return (false, nil)
            }
            return (seed.insightsKnown, seed.insightsScore)
        }
        let liveGroup = portfolioStore.activePortfolioId
        if portfolioInsightsPhase == .known, portfolioInsightsPortfolioId == liveGroup {
            return (true, portfolioInsights)
        }
        // The snapshot → live swap: the rows went live a moment before the score. The kept
        // answer for the SAME group stays up while that group's request is on the wire — from
        // the seed itself in the turns before `performLoad` replaces it, then from the carry.
        if portfolioInsightsPhase == .resolving,
           let kept = keptSeedInsightsForLiveGroup ?? insightsCarriedFromSnapshot,
           kept.portfolioId == liveGroup {
            return (true, kept.score)
        }
        // A CONNECTIVITY failure falls back to the on-device estimate (live data only).
        if portfolioInsightsPhase == .failed, portfolioInsightsLoadFailed,
           portfolioInsightsPortfolioId == liveGroup,
           let estimate = portfolioDiversificationScore {
            return (true, estimate)
        }
        return (false, nil)
    }

    /// The seed's kept insights answer, when the seed is about the LIVE active group, inside its
    /// display window, and kept a known answer — and only while a load is running, i.e. the one
    /// about to replace the seed. (A seed left behind by a live list that arrived some other way,
    /// such as the insights card's Retry, must not stand in for a later request.)
    private var keptSeedInsightsForLiveGroup: (portfolioId: String, score: DiversificationScore?)? {
        guard loadTask != nil, let seed = snapshotSeed, seed.insightsKnown,
              let groupId = seed.activePortfolioId, groupId == portfolioStore.activePortfolioId,
              let savedAt = snapshotSeedSavedAt,
              AccountSnapshotPolicy.isDisplayable(savedAt: savedAt, now: Date()) else { return nil }
        return (portfolioId: groupId, score: seed.insightsScore)
    }

    /// What the Portfolio Insights card renders: the server score when present,
    /// the on-device estimate only when the server call failed for connectivity.
    var displayedDiversificationScore: DiversificationScore? {
        presentedInsightsAnswer.score
    }

    /// The answer could not be had — the insights request failed, or there is no live group
    /// list to ask about. Never the first-run "Set up" card.
    var portfolioInsightsDidFail: Bool {
        guard !portfolioInsightsIsGated, !presentedInsightsAnswer.known else { return false }
        if portfolioInsightsPhase == .failed { return true }
        return presentedPortfolio == nil && !portfolioStore.hasLiveData && portfolioStore.loadErrorMessage != nil
    }

    /// Not known yet and not failed: the card says so (no call to action).
    var portfolioInsightsIsResolving: Bool {
        guard !portfolioInsightsIsGated, !presentedInsightsAnswer.known else { return false }
        return !portfolioInsightsDidFail
    }

    /// The spinner, only while something is actually on the wire — a hidden tab that never
    /// loaded mounts no ProgressView.
    var portfolioInsightsShowsProgress: Bool {
        guard portfolioInsightsIsResolving else { return false }
        return portfolioInsightsPhase == .resolving || isLoading || portfolioStore.isLoading
    }

    /// Opening the toggle auto-opens the config sheet only over a KNOWN empty answer — never
    /// while the answer is unknown, failed or gated — and only when edits can land.
    var shouldAutoOpenPortfolioConfig: Bool {
        presentedInsightsAnswer.known && displayedDiversificationScore == nil && canEditPortfolio
    }

    @discardableResult
    private func loadTrackingFeed() async -> Bool {
        // THREE outcomes, not two — decided from the OUTCOME, never from a pre-flight read of
        // `auth.status`.
        //
        // `GET /tracking/assets` is `.signInRequired`, so an unarmed caller is refused by
        // `APIClient.buildRequest` BEFORE any network I/O and the refusal arrives typed, as
        // `AppError.signInRequired`. That refusal is the one reliable signal, because it is
        // APIClient's own answer to "is a token armed?". A pre-flight
        // `guard AppActions.shared.isSignedIn` looks equivalent and is not: `isSignedIn` is
        // `status == .authenticated`, and on EVERY signed-in cold launch `primeStoredCredential`
        // arms the token while the status still reads `.restoring` (AppState documents that
        // ordering as load-bearing). That guard therefore refused a request that would have
        // succeeded, and showed "Reconnecting…" to a user whose token was already on the wire.
        // Calling and classifying costs nothing extra — the refusal never leaves the device.
        //
        // Fenced by identity generation: an answer to a request that left under the previous
        // identity publishes nothing (and so can never reach the snapshot save either).
        let generation = loadGeneration
        do {
            let (feed, body) = try await apiClient.requestReturningBody(
                endpoint: .getTrackingAssets,
                responseType: TrackingFeedResponse.self
            )
            guard generation == loadGeneration else { return false }
            self.trackedAssets = feed.assets.map { $0.toTrackedAsset() }
            self.alerts = feed.alerts.map { $0.toAppAlert() }
            self.lastLiveFeedBody = body
            self.hasLiveFeed = true
            self.assetsErrorMessage = nil
            self.assetsRequiresSignIn = false
            self.assetsIsReconnecting = false
            print("[TrackingVM] ✅ Loaded \(feed.assets.count) assets, \(feed.alerts.count) alerts from API")
            return true
        } catch {
            guard generation == loadGeneration else { return false }
            let appError = AppError.from(error)
            // A cancelled request is nobody's failure: it used to land below as
            // `assetsErrorMessage = ""` (a cancelled poll after a tab-away).
            if appError.isCancellation { return false }
            print("[TrackingVM] ❌ Tracking feed failed: \(appError.title): \(error)")
            // Surface it. A silent empty list reads as "you own nothing", which is
            // a different (and wrong) statement about the user's own money.
            // The backend now answers 503 WATCHLIST_UNAVAILABLE rather than a
            // successful empty feed when the datastore is unreadable, so this
            // branch is reached instead of a false success.
            //
            // Signed out and broken must not look alike: one wants a Sign In button, the other
            // a Retry. A REFUSED load also empties the list — nothing on screen can be
            // refreshed while the token is unarmed, and what is there may belong to an identity
            // that is no longer the armed one.
            if case .signInRequired = appError {
                self.trackedAssets = []
                self.alerts = []
                self.assetsErrorMessage = nil
                // The saved snapshot comes down too, and stays down: stored holdings never sit
                // over an account gate (the seed's precondition refuses while it is up). The
                // FILE is kept — a refusal also happens in a restore race; real session ends
                // delete it through `clearForEndedSession`.
                self.snapshotSeed = nil
                self.hasLiveFeed = false
                self.lastLiveFeedBody = nil
                let reconnecting = AppActions.shared.isRestoringSession
                self.assetsIsReconnecting = reconnecting
                self.assetsRequiresSignIn = !reconnecting
            } else {
                self.assetsErrorMessage = appError.message
                self.assetsRequiresSignIn = false
                self.assetsIsReconnecting = false
            }
            // Do NOT seed fabricated sample prices/alerts here. Rendering a
            // fake $178.42 quote or a "$2.4B Warren Buffett bought" rollup as
            // if it were the user's real holdings/alerts is worse than an
            // honest empty state. Leave the lists as-is (empty on a first-load
            // failure); the 30s timer and pull-to-refresh retry. `sampleData`
            // stays preview-only.
            return false
        }
    }

    // MARK: - Whale Data Loading (Real API)

    private func loadWhaleData() async {
        // The Whales sub-tab had no loading state of its own — it relied on the global
        // LoadingOverlay, which is gone. Without this it would render its empty sections as
        // though the user follows nobody and no whale has traded.
        isLoadingWhales = true
        defer { isLoadingWhales = false }

        async let listTask: () = loadWhaleList()
        async let activityTask: () = loadWhaleActivityFeed()
        _ = await (listTask, activityTask)
    }

    private func loadWhaleList(retryCount: Int = 3) async {
        // Same three outcomes as the assets half, decided the same way — from the typed
        // refusal, not from a pre-flight `auth.status` read (see `loadTrackingFeed`).
        //
        // Captured BEFORE the request. `WhaleService.reset()` bumps this on sign-out, so a
        // response that lands afterwards is refused rather than re-persisted into the
        // device-global follows key (auth.md §7).
        let whaleSyncEpoch = WhaleService.shared.currentIdentityEpoch
        var lastError: Error?
        for attempt in 1...retryCount {
            do {
                // Lenient: one malformed row must not empty the whole roster. See
                // `LenientArray` — the synthesised `[T]` decode is all-or-nothing.
                let decoded = try await apiClient.request(
                    endpoint: .getWhaleList(category: nil),
                    responseType: LenientArray<TrendingWhaleDTO>.self
                )
                if decoded.droppedCount > 0 {
                    print("[TrackingVM] ⚠️ Dropped \(decoded.droppedCount) malformed whale row(s)")
                }
                let allWhales = decoded.elements.map { $0.toTrendingWhale() }

                // Sync follow state from API, under the epoch this request STARTED in —
                // a response that lands after sign-out must not re-persist the ended
                // session's follows into the device-global key.
                WhaleService.shared.syncFromAPIResponse(allWhales, asOf: whaleSyncEpoch)

                // Split into followed vs not-followed
                self.trackedWhales = allWhales.filter { $0.isFollowing }
                self.allPopularWhales = allWhales

                // Hero whales: top 5 with descriptions (fallback: top 5 overall)
                let whalesWithDesc = allWhales.filter { !$0.description.isEmpty }
                self.heroWhales = Array((whalesWithDesc.isEmpty ? allWhales : whalesWithDesc).prefix(5))

                // Popular row: next 5 unfollowed whales after the hero (no dup with hero)
                let heroIds = Set(self.heroWhales.map(\.id))
                self.popularWhales = Array(
                    allWhales
                        .filter { !$0.isFollowing && !heroIds.contains($0.id) }
                        .prefix(5)
                )

                self.whalesErrorMessage = nil
                self.whalesRequiresSignIn = false
                self.whalesIsReconnecting = false
                print("[TrackingVM] ✅ Loaded \(allWhales.count) whales from API (\(trackedWhales.count) followed)")
                return // success — exit loop
            } catch {
                lastError = error
                print("[TrackingVM] ❌ Whale list attempt \(attempt)/\(retryCount) failed: \(error)")
                // A refusal for want of an armed token is DETERMINISTIC — the next attempt is
                // refused identically, so retrying only spends the 1 s + 2 s sleeps below.
                // That mattered: `TrackingView.onAppear` calls `retryWhaleListIfNeeded()`, so
                // while the session was unarmed every appearance of the Whales sub-tab paid
                // for three refusals and two sleeps.
                if case .signInRequired = AppError.from(error) { break }
                if attempt < retryCount {
                    // 1s then 2s. The old 2s+4s ran INSIDE the parallel load, so a whale
                    // outage stalled the entire Assets tab for six seconds before it
                    // rendered anything at all.
                    let delay = UInt64(attempt) * 1_000_000_000
                    try? await Task.sleep(nanoseconds: delay)
                }
            }
        }
        // All retries exhausted — leave lists empty so UI shows empty state.
        // Never fall back to sample data (sample UUIDs cause 404s on profile fetch).
        // But SAY SO: an unexplained empty roster reads as "we track nobody".
        if let lastError {
            let appError = AppError.from(lastError)
            // An identity change cancels the load this ran in: nobody is waiting, and the
            // cancellation is not a roster failure to put on screen (it used to set "").
            if appError.isCancellation { return }
            if case .signInRequired = appError {
                // ALL FOUR roster arrays, as one unit — the four the success path writes. The
                // first version cleared only `trackedWhales` and `allPopularWhales` (the one the
                // gate is keyed on), so "Reconnecting…" rendered directly above the previous
                // load's hero carousel and Most Popular cards, Follow buttons live. Three
                // independent reviewers found that; clearing two of four is the bug.
                self.trackedWhales = []
                self.allPopularWhales = []
                self.heroWhales = []
                self.popularWhales = []
                self.whalesErrorMessage = nil
                let reconnecting = AppActions.shared.isRestoringSession
                self.whalesIsReconnecting = reconnecting
                self.whalesRequiresSignIn = !reconnecting
            } else {
                self.whalesErrorMessage = appError.message
                self.whalesRequiresSignIn = false
                self.whalesIsReconnecting = false
            }
        }
        print("[TrackingVM] ⚠️ Whale list unavailable after \(retryCount) attempts. Pull to refresh to retry.")
    }

    private func loadWhaleActivityFeed() async {
        do {
            let decoded = try await apiClient.request(
                endpoint: .getWhaleActivity,
                responseType: LenientArray<WhaleTradeGroupActivityDTO>.self
            )
            if decoded.droppedCount > 0 {
                print("[TrackingVM] ⚠️ Dropped \(decoded.droppedCount) malformed activity row(s)")
            }
            let activities = decoded.elements.map { $0.toWhaleTradeGroupActivity() }

            // See All keeps everything; the Tracking screen shows only the newest few.
            // The slice is taken on the FLAT list before bucketing on purpose —
            // `grouped.prefix(n)` would take n DATE BUCKETS, which is an unbounded
            // number of trades and not what "5 most recent" means.
            //
            // Truncated HERE and stored, never derived in a view's computed property:
            // `GroupedWhaleTrades.id` is a UUID minted at init and the timeline's ForEach
            // keys on it, so rebuilding the slice each body pass would hand SwiftUI fresh
            // identities on every render.
            self.allWhaleTrades = Self.bucketByDate(activities)
            self.groupedWhaleTrades = Self.bucketByDate(
                Array(activities.prefix(Self.recentTradesPreviewLimit))
            )

            print("[TrackingVM] ✅ Loaded \(activities.count) whale activity items from API")
        } catch {
            print("[TrackingVM] ❌ Whale activity failed: \(error)")
            // No sample fallback — leave empty so UI shows empty state.
            //
            // A REFUSED load (no armed token) does clear, though: this runs alongside
            // `loadWhaleList`, and a stale Recent Trades timeline left in place rendered ABOVE
            // that roster's account gate — "Reconnecting…" sandwiched under trades the screen
            // can no longer refresh. The gate itself is owned by the roster load.
            if case .signInRequired = AppError.from(error) {
                self.allWhaleTrades = []
                self.groupedWhaleTrades = []
            }
        }
    }

    /// Bucket consecutive same-date activities into a single section so a date header
    /// isn't repeated for every whale who traded that day. The feed arrives already
    /// sorted desc by date from the backend, so a single pass over neighbours is enough.
    private static func bucketByDate(
        _ activities: [WhaleTradeGroupActivity]
    ) -> [GroupedWhaleTrades] {
        var grouped: [GroupedWhaleTrades] = []
        for activity in activities {
            if let last = grouped.last, last.sectionTitle == activity.formattedDate {
                grouped[grouped.count - 1] = GroupedWhaleTrades(
                    sectionTitle: last.sectionTitle,
                    activities: last.activities + [activity]
                )
            } else {
                grouped.append(GroupedWhaleTrades(
                    sectionTitle: activity.formattedDate,
                    activities: [activity]
                ))
            }
        }
        return grouped
    }

    /// Re-fetch the roster AND the activity feed after the SERVER confirmed a follow change.
    ///
    /// Coalesced: following three whales in a row posts three notifications, and each
    /// would otherwise cost a round trip whose result the next one immediately discards.
    /// Cancelling the pending task collapses a burst into one fetch of the final state.
    ///
    /// ⚠️ The roster reload is not optional. `is_locked` and `is_following_inactive` are
    /// WHOLE-SET verdicts — the server computes them from the complete follow set
    /// (`at_cap = len(followed) >= limit`, and the `> limit` truncation), so no per-row client
    /// update can maintain them. `reconcileFollowState` only rebuilds the rows whose
    /// `isFollowing` actually changed and returns every other row byte-identical, which left
    /// both flags stale in both directions:
    ///   • Pro at 10 follows stamps `is_locked` on the whole unfollowed roster; unfollow two and
    ///     those rows still refuse at 8/10, showing an upgrade sheet for a slot the server would
    ///     have accepted.
    ///   • Drop back under the limit and a previously-truncated follow keeps
    ///     `isFollowingInactive`, rendering dimmed with a lock and the word "Upgrade" directly
    ///     above the very trades the feed has resumed serving.
    /// `toggle_follow` clears `_whale_list_cache` server-side, so this re-fetch returns freshly
    /// computed flags rather than the pre-change ones.
    func reloadForFollowChange() {
        activityReloadTask?.cancel()
        activityReloadTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled else { return }
            await self?.loadWhaleList()
            guard !Task.isCancelled else { return }
            await self?.loadWhaleActivityFeed()
        }
    }

    /// A watchlist add/remove confirmed by the server somewhere other than this tab.
    ///
    /// Two halves, in this order:
    ///   1. Patch locally so the list is right on THIS run-loop turn. A removal drops the
    ///      row from `trackedAssets` (`filteredAssets` derives from it — the same line
    ///      `removeAssetFromAll` uses). An add cannot be drawn yet (the feed row needs a
    ///      quote and the group membership comes from `GET /portfolios`), so it marks the
    ///      ticker in `recentlyAddedTickers` instead — which also fills the search sheet's
    ///      star and, load-bearingly, keeps `performLoad`'s purge from deleting the freshly
    ///      mirrored group member if the reconcile below reads a feed that predates the
    ///      write (a build already in flight when the POST landed can do exactly that).
    ///   2. Reconcile from the server, coalesced (300 ms, like `reloadForFollowChange`)
    ///      and ordered STRICTLY BEHIND the write: any load already running may predate
    ///      it, so it is awaited to completion first and a fresh one issued after —
    ///      joining it would adopt the pre-toggle state. `loadData()` rather than
    ///      `refresh()`: no pull-to-refresh spinner for a change the user did not make
    ///      here. Markers are cleared only for the adds this particular load could see.
    ///
    /// Nothing to do before the first load — `loadIfNeeded` fetches fresh on activation,
    /// and the server invalidated its feed cache on the write — UNLESS that first load is
    /// in flight right now: it may have read the pre-write list (a star tapped on a
    /// Home-pushed detail while Tracking's first load runs), so it is reconciled behind
    /// like any other running load.
    func handleWatchlistChange(_ change: WatchlistChange) {
        // Own writes already reload this tab (`addTickerFromSearch` → `refresh()`,
        // `removeAssetFromAll` patches + reloads); they post only for Home and Updates.
        guard change.source != .tracking else { return }
        // A removal made elsewhere comes off a snapshot on screen at once too — display state
        // only (the seed is never written anywhere), and even before this tab's first load.
        if !change.added, let seed = snapshotSeed {
            snapshotSeed = Self.removingRow(of: change.ticker, from: seed)
        }
        // …and off the saved FILE, even in a session where this tab was never opened: the next
        // cold launch would otherwise paint the removed ticker back as a holding. An add is not
        // purged — the snapshot cannot draw it, and a file missing a newer ticker is the
        // ordinary, labelled staleness the next live load replaces.
        if !change.added {
            purgeSnapshotAfterRemovalElsewhere()
        }
        guard hasLoadedOnce || loadTask != nil else { return }
        let ticker = change.ticker.uppercased()

        if change.added {
            if let activeId = portfolioStore.activePortfolioId {
                recentlyAddedTickers[activeId, default: []].insert(ticker)
                watchlistMarkerQueue.append((portfolioId: activeId, ticker: ticker))
            }
        } else {
            trackedAssets.removeAll { $0.ticker.uppercased() == ticker }
            // Add-then-remove inside one debounce window: the pending add marker would
            // otherwise keep `isOnWatchlist` true for a ticker that is gone.
            for (portfolioId, _) in watchlistMarkerQueue where recentlyAddedTickers[portfolioId]?.contains(ticker) == true {
                recentlyAddedTickers[portfolioId]?.remove(ticker)
                if recentlyAddedTickers[portfolioId]?.isEmpty == true {
                    recentlyAddedTickers.removeValue(forKey: portfolioId)
                }
            }
            watchlistMarkerQueue.removeAll { $0.ticker == ticker }
        }

        watchlistReloadTask?.cancel()
        watchlistReloadTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled, let self else { return }
            if let running = self.loadTask, !running.isCancelled {
                await running.value
            }
            guard !Task.isCancelled else { return }
            let mine = self.watchlistMarkerQueue
            self.watchlistMarkerQueue.removeAll()
            await self.loadData()
            for (portfolioId, pending) in mine {
                self.recentlyAddedTickers[portfolioId]?.remove(pending)
                if self.recentlyAddedTickers[portfolioId]?.isEmpty == true {
                    self.recentlyAddedTickers.removeValue(forKey: portfolioId)
                }
            }
        }
    }

    /// `seed` without `ticker`'s row (uppercased match, like Holdings' membership rule).
    private static func removingRow(of ticker: String, from seed: TrackingSnapshot) -> TrackingSnapshot {
        let removed = ticker.uppercased()
        let kept: [TrackedAsset] = seed.assets.filter { $0.ticker.uppercased() != removed }
        return TrackingSnapshot(
            assets: kept, portfolios: seed.portfolios,
            activePortfolioId: seed.activePortfolioId, insights: seed.insights
        )
    }

    func refresh() async {
        isRefreshing = true
        await loadData()
        isRefreshing = false
    }

    /// The signed-in identity changed — these tracked assets belong to the previous one.
    ///
    /// This tab is the most exposed of the four: watchlist and portfolios are `.guestAllowed`
    /// and partitioned PER INSTALL, so a guest and an account legitimately hold DIFFERENT rows
    /// under the same install. It also had no reload trigger at all — it reads `isActiveTab`
    /// nowhere, so nothing refetched on tab activation either. Signing in or out left the
    /// previous identity's holdings on screen until pull-to-refresh.
    ///
    /// Cleared before the fetch so the previous account's positions are never on screen while
    /// the new load is in flight.
    func handleIdentityChange(isActiveTab: Bool) async {
        // The previous identity's load is CANCELLED and fenced, never joined: its answers were
        // asked for by someone else, and a joined old load would publish nothing (the feed is
        // generation-fenced) and then latch an empty tab as loaded.
        loadGeneration &+= 1
        let generation = loadGeneration
        loadTask?.cancel()
        loadTask = nil
        isLoading = false
        // CLEAR FIRST, UNCONDITIONALLY — before the `isActiveTab` gate below. The reload is
        // deferred for a hidden tab, but the previous account's data must not survive in this
        // ViewModel waiting to be rendered (.claude/rules/auth.md §7).
        trackedAssets = []
        alerts = []
        hasLiveFeed = false
        lastLiveFeedBody = nil
        snapshotSeed = nil
        hasAttemptedLoad = false
        // The insights card is identity-scoped too: the previous account's score, and any
        // answer still in flight for it (the token bump drops that on arrival).
        insightsRequestToken &+= 1
        portfolioInsights = nil
        portfolioInsightsLoadFailed = false
        portfolioInsightsPortfolioId = nil
        portfolioInsightsPhase = .idle
        assetsErrorMessage = nil
        // Cleared with the rest, ABOVE the gate: a latched "sign in" from a load that raced
        // session restore is exactly what this reload exists to heal.
        assetsRequiresSignIn = false
        assetsIsReconnecting = false
        whalesRequiresSignIn = false
        whalesIsReconnecting = false
        // The whale surfaces are follow-derived and therefore IDENTITY-SCOPED. Nothing
        // cleared them, so after A signed out and B signed in on the same device, B saw
        // A's followed investors and A's Recent Trades timeline until the new load
        // landed — and if that load failed, indefinitely. Same rule as
        // `AppState.discardDataForEndedSession` applies to the Learn stores
        // (.claude/rules/auth.md §7).
        trackedWhales = []
        allWhaleTrades = []
        groupedWhaleTrades = []
        whaleActivities = []
        // The Most Popular roster too. It is market-wide, but every row carries the previous
        // identity's `isFollowing`, so it rendered their Follow state for a hidden tab until
        // the next activation reloaded it.
        allPopularWhales = []
        heroWhales = []
        popularWhales = []
        // Cleared so a later tab activation re-loads for the NEW identity rather than
        // treating the previous account's completed load as this one's.
        hasLoadedOnce = false
        // A watchlist reconcile still debouncing would reload against the NEW identity;
        // its markers (and any older ones — nothing cleared `recentlyAddedTickers` here
        // before, so a marker could keep `isOnWatchlist` true across a sign-out) belong
        // to the previous one.
        watchlistReloadTask?.cancel()
        watchlistMarkerQueue.removeAll()
        recentlyAddedTickers.removeAll()

        // Re-seed for the NEW identity, still above the gate (a disk read, never a request).
        // AppState re-bound or cleared the snapshot store synchronously before publishing the
        // identity, so another account's file can never paint here — and the view renders
        // snapshot rows only while this tab is on screen.
        await prepareSnapshot()

        // Fetch only if the user is actually looking at this tab. Clearing above resets
        // `hasLoadedOnce`, so `.task(id: isActiveTab)` re-loads on the next activation.
        //
        // This tab is the reason the gate exists: `loadData()` fans out FIVE requests
        // (/tracking/assets, /portfolios, /whales, /whales/activity, /portfolios/{id}/insights),
        // and it fired all of them on every sign-in regardless of which tab was on screen.
        guard isActiveTab else { return }

        await loadData()
        // Latched on THIS identity's load completing — a newer identity change owns it otherwise.
        if generation == loadGeneration { hasLoadedOnce = true }
        // The timer is keyed to whoever is signed in now; restart it against their assets.
        startPriceRefreshTimer()
    }

    // MARK: - Live Price Refresh

    /// Periodically re-fetches asset prices while there is something that can move.
    ///
    /// The gate is deliberately NOT a bare `isMarketActive()`: that is the US
    /// equity session, so a crypto holding (24/7) or a commodity future froze
    /// every evening and all weekend — exactly when a crypto holder checks. It
    /// now also refreshes whenever the tracked set contains a round-the-clock
    /// asset, mirroring the backend's `asset_class.trades_extended_hours`.
    func startPriceRefreshTimer() {
        priceRefreshTask?.cancel()
        priceRefreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30_000_000_000) // 30 seconds
                guard !Task.isCancelled else { break }
                guard let self = self else { break }
                let assets = self.trackedAssets
                guard MarketHoursUtil.shouldRefreshPrices(
                    assetTypes: assets.map(\.assetType),
                    symbols: assets.map(\.ticker)
                ) else { continue }
                await self.loadTrackingFeed()
            }
        }
    }

    /// Called from `TrackingView`'s `.task(id: isActiveTab)` teardown and on background.
    /// It had NO caller at all: the 30-second poll started on the tab's first load and ran
    /// for the life of the process, re-fetching `/tracking/assets` from every other tab and
    /// after sign-out — on the single uvicorn worker, for every installed app.
    func stopPriceRefreshTimer() {
        priceRefreshTask?.cancel()
        priceRefreshTask = nil
    }

    /// Called when the Whales tab appears — retries loading if the list is still empty.
    ///
    /// ⚠️ The gate check is load-bearing, not tidiness. This fires on EVERY appearance of the
    /// sub-tab, and its only other condition — "the roster is empty" — is permanently true
    /// while the session is unarmed, so every appearance used to spend three refused requests
    /// and two backoff sleeps. An empty roster BECAUSE it is gated has nothing a retry can fix;
    /// `TrackingView`'s `onChange(of: appState.auth.status)` reloads it when the session heals.
    ///
    /// Keyed on the gate the last load RECORDED, not on `auth.status`: see `loadTrackingFeed`
    /// for why a status read is wrong on every cold launch.
    func retryWhaleListIfNeeded() {
        guard !whalesRequiresSignIn, !whalesIsReconnecting else { return }
        guard allPopularWhales.isEmpty, !isLoading else { return }
        retryWhaleList()
    }

    /// Explicit user-driven retry from the roster error state.
    func retryWhaleList() {
        whalesErrorMessage = nil
        Task { [weak self] in
            await self?.loadWhaleList()
        }
    }

    // MARK: - Asset Actions

    func addNewAsset() {
        showAddAssetSheet = true
    }

    func openSortOptions() {
        showSortSheet = true
    }

    func toggleSort() {
        sortAscending.toggle()
        UserDefaults.standard.set(sortAscending, forKey: Self.sortAscendingKey)
    }

    func selectSortOption(_ option: AssetSortOption) {
        sortOption = option
        UserDefaults.standard.set(option.rawValue, forKey: Self.sortOptionKey)
        showSortSheet = false
    }

    /// Swipe-to-delete: removes the ticker from the active portfolio only.
    /// The master watchlist (and any other portfolio that contains it) is
    /// untouched. Use `removeAssetFromAll` for the long-press path that
    /// fully removes the ticker.
    func removeAsset(_ asset: TrackedAsset) {
        // A snapshot row (or a store with no live list) has no membership this device may
        // write: the store would no-op silently. Say so instead (auth.md §6).
        guard canEditHoldings else {
            reportPortfolioStillLoading(action: "remove \(asset.ticker) from this portfolio")
            return
        }
        Task {
            do {
                try await portfolioStore.removeTicker(asset.ticker)
            } catch {
                // The swipe already removed the row optimistically, so silence here left the
                // ticker gone from the UI and still on the server — it reappeared on the next
                // refresh with no explanation. Refresh to restore truth, and say what happened.
                AppActions.shared.reportMutationFailure(
                    error, action: "remove \(asset.ticker) from this portfolio"
                )
                await refresh()
            }
        }
    }

    /// Whether the ticker is in the *active portfolio* (or was just added to
    /// it in this session). Used by the search sheet's star icon — filled
    /// means "already in this portfolio", empty means "tap to add here".
    /// Master-watchlist membership is intentionally NOT what this checks:
    /// the user's mental model is portfolio-scoped, not watchlist-scoped.
    func isOnWatchlist(_ ticker: String) -> Bool {
        let upper = ticker.uppercased()
        guard let activeId = portfolioStore.activePortfolioId else { return false }
        if recentlyAddedTickers[activeId]?.contains(upper) == true { return true }
        return portfolioStore.activePortfolio?.tickers.contains(upper) ?? false
    }

    /// Add a ticker to the master watchlist + active portfolio from the in-sheet
    /// search star button. Idempotent on the server (UNIQUE constraint), so it's
    /// safe to call repeatedly. Tickers already on the watchlist still get
    /// pushed into the active portfolio — that's the whole point of tapping
    /// the star while looking at this portfolio.
    func addTickerFromSearch(_ result: StockSearchResult) {
        // The spelling the PORTFOLIO must carry is the one the watchlist stores: a coin is
        // persisted as the pair (`BTCUSD`, migration 160) while search hands us the bare
        // `BTC`. `PUT /portfolios/{id}/tickers` resolves a bare spelling raw-first, so a
        // user who also holds the same-ticker security (the BTC ETF) would have had the
        // security chosen and the coin they just starred DROPPED from the portfolio.
        let symbol = CryptoSymbol.storedSymbol(for: result)

        Task { @MainActor in
            // Self-heal: if the user taps the star before portfolios have
            // loaded (or the initial load failed — e.g. backend
            // missing the new endpoint), try reloading once and create a
            // default "Holdings" portfolio if the list is still empty.
            // Without this the tap looks like a dead button.
            //
            // Keyed on a LIVE list, not only on a nil active id: after a FAILED `/portfolios` the
            // id still holds this device's remembered hint, a group the store does not hold —
            // `addTicker` then returned without a word and the star emptied again.
            if !portfolioStore.hasLiveData || portfolioStore.activePortfolioId == nil {
                print("[TrackingVM] ⚠️ No live active portfolio for \(symbol); attempting recovery…")
                let loaded = await portfolioStore.loadPortfolios()
                // Only a LIVE answer that says "no groups" may mint the default one. After a
                // FAILED load an empty list proves nothing, and creating "Holdings" then would
                // add a duplicate beside the group the server already has.
                if loaded && portfolioStore.portfolios.isEmpty {
                    do {
                        _ = try await portfolioStore.createPortfolio(named: "Holdings")
                        print("[TrackingVM] ✅ Created default Holdings portfolio")
                    } catch {
                        // Without this the tap is simply a dead button: no portfolio is created,
                        // nothing is added, and nothing is said.
                        AppActions.shared.reportMutationFailure(
                            error, action: "create a portfolio"
                        )
                        return
                    }
                }
            }

            // Before the optimistic marker and before any request: a group no LIVE list holds
            // takes no write (`addTicker` would no-op silently), so the star must not fill for it.
            guard portfolioStore.hasLiveData, let portfolioId = portfolioStore.activePortfolioId else {
                print("[TrackingVM] ❌ Still no live active portfolio after recovery; aborting add for \(symbol)")
                // The star was tapped; a silent return reads as a dead button (auth.md §6).
                AppActions.shared.reportMutationFailure(
                    APIError.unknown(message: Self.portfolioStillLoadingMessage), action: "add \(symbol)"
                )
                return
            }
            // Capture the portfolio at this point — if the user switches mid-
            // flight, we still target the one they were looking at when they
            // tapped (and the optimistic star fills only on that portfolio).
            recentlyAddedTickers[portfolioId, default: []].insert(symbol)

            do {
                try await apiClient.request(
                    endpoint: .addToWatchlist(stockId: result.ticker, assetType: result.type)
                )
                print("[TrackingVM] ✅ Added \(symbol) to watchlist via search star")
                // Home's watchlist section and Updates' chips are built from the active
                // group server-side and observe nothing here; `symbol` is already the
                // stored spelling. This tab ignores its own source.
                PortfolioStore.announceWatchlistChange(
                    ticker: symbol, assetType: result.type ?? "stock",
                    added: true, source: .tracking
                )
            } catch {
                // Most common reason this fails is that the ticker is already
                // on the master watchlist (409). That's fine — we still want
                // it in the active portfolio.
                print("[TrackingVM] ⚠️ Watchlist add failed for \(symbol) (likely already present): \(error)")
            }
            do {
                try await portfolioStore.addTicker(symbol, to: portfolioId)
            } catch {
                // Was a bare `try?`. The star had already filled in optimistically, so a failure
                // here meant the user watched it light up for a ticker that never joined the
                // portfolio.
                AppActions.shared.reportMutationFailure(
                    error, action: "add \(symbol) to this portfolio"
                )
            }
            await refresh()
            // Real portfolio.tickers now carries the truth — drop the
            // optimistic marker so future state reads from authoritative data.
            recentlyAddedTickers[portfolioId]?.remove(symbol)
            if recentlyAddedTickers[portfolioId]?.isEmpty == true {
                recentlyAddedTickers.removeValue(forKey: portfolioId)
            }
        }
    }

    /// Long-press "Remove from all portfolios": removes the ticker from every
    /// portfolio it belongs to AND from the master watchlist.
    func removeAssetFromAll(_ asset: TrackedAsset) {
        guard canEditHoldings else {
            reportPortfolioStillLoading(action: "remove \(asset.ticker) from your watchlist")
            return
        }
        // Optimistic UI removal from the underlying asset list — the swipe
        // animation looks broken if the row sticks around while the network
        // request flies.
        trackedAssets.removeAll { $0.id == asset.id }

        Task {
            await portfolioStore.removeTickerFromAllPortfolios(asset.ticker)
            do {
                try await apiClient.request(
                    endpoint: .removeFromWatchlist(stockId: asset.ticker)
                )
                print("[TrackingVM] ✅ Removed \(asset.ticker) from watchlist + all portfolios")
                PortfolioStore.announceWatchlistChange(
                    ticker: asset.ticker, assetType: asset.assetType,
                    added: false, source: .tracking
                )
            } catch {
                // The row was removed optimistically before this ran; without a signal the
                // ticker silently returns on the next refresh.
                AppActions.shared.reportMutationFailure(
                    error, action: "remove \(asset.ticker) from your watchlist"
                )
                await refresh()
            }
        }
    }

    // MARK: - Portfolio Actions

    func setActivePortfolio(_ id: String) {
        Task {
            // The switch now round-trips to the server, because Home's watchlist section
            // and the Updates ticker chips are built from `portfolios.is_active` — a
            // local-only change would move this tab and leave those two behind.
            await portfolioStore.setActivePortfolio(id)
            // The diversification score is per-portfolio — re-fetch it for the
            // newly active portfolio so the card doesn't show a stale score. Awaited
            // AFTER the switch so it cannot score the group we just navigated away from.
            await loadPortfolioInsights()
        }
    }

    func openNewPortfolioSheet() {
        // Group edits are built from the live list; without one the sheet would act on nothing.
        guard canEditPortfolio else {
            reportPortfolioStillLoading(action: "create a portfolio")
            return
        }
        showNewPortfolioSheet = true
    }

    func openEditPortfolioSheet() {
        guard canEditPortfolio else {
            reportPortfolioStillLoading(action: "edit your portfolios")
            return
        }
        showEditPortfolioSheet = true
    }

    func openManageTickersSheet() {
        guard canEditPortfolio else {
            reportPortfolioStillLoading(action: "manage this portfolio's tickers")
            return
        }
        showManageTickersSheet = true
    }

    @discardableResult
    func createPortfolio(named name: String) async throws -> Portfolio {
        let portfolio = try await portfolioStore.createPortfolio(named: name)
        // New portfolio becomes active (and starts empty) — refresh the score.
        await loadPortfolioInsights()
        return portfolio
    }

    func renamePortfolio(id: String, to newName: String) async throws {
        _ = try await portfolioStore.renamePortfolio(id: id, to: newName)
    }

    func deletePortfolio(id: String) async throws {
        try await portfolioStore.deletePortfolio(id: id)
        // Active portfolio may have been reassigned — refresh the score.
        await loadPortfolioInsights()
    }

    // MARK: - Portfolio Insights Actions

    func openPortfolioConfigSheet() {
        // The config sheet edits the LIVE active group's holdings; with no live list it would
        // open on nothing (or on a snapshot group the store does not hold).
        guard canEditPortfolio else {
            reportPortfolioStillLoading(action: "edit your holdings")
            return
        }
        showPortfolioConfigSheet = true
    }

    /// Push every row's `shares` / `marketValue` from the config sheet to
    /// the backend in a single bulk PUT, scoped to the active portfolio, then
    /// re-fetch the server-computed diversification score so the card reflects
    /// the new holdings immediately.
    ///
    /// `null` for both fields on a row clears that ticker's holding values —
    /// the row stays in the portfolio but stops counting toward the
    /// diversification score.
    func savePortfolioHoldings(_ items: [HoldingUpdateItem]) async throws {
        guard canEditPortfolio else {
            throw APIError.unknown(message: Self.portfolioStillLoadingMessage)
        }
        guard let portfolioId = portfolioStore.activePortfolioId else {
            throw APIError.unknown(message: "No active portfolio selected.")
        }
        try await portfolioStore.setHoldings(items, in: portfolioId)
        await loadPortfolioInsights()
    }

    // MARK: - Navigation

    /// Tapping any holdings row's price block flips the whole column.
    func toggleChangeDisplayMode() {
        changeDisplayMode = changeDisplayMode.toggled
        Haptics.selection()
    }

    func viewAssetDetail(_ asset: TrackedAsset) {
        selectedAssetNavigation = SearchSelection(symbol: asset.ticker, type: asset.assetType)
    }

    func viewAlertDetail(_ alert: AppAlert) {
        selectedAlert = alert
    }

    // MARK: - Whale Actions

    func selectWhaleCategory(_ category: WhaleCategory) {
        selectedWhaleCategory = category
    }

    /// Tapped a followed whale the current plan doesn't surface. Same plan sheet as a locked
    /// Follow pill: the follow row exists, but the feed truncates to the covered subset, so
    /// upgrading — not buying credits — is what makes it visible again.
    func viewInactiveWhale(_ whale: TrendingWhale) {
        showWhalePaywall = true
    }

    func toggleFollowWhale(_ whale: TrendingWhale) {
        // PLAN gate before anything else. The server would refuse this follow with
        // WHALE_FOLLOW_LOCKED anyway, but letting the request go out would spend a round
        // trip to arrive at a toast — and it would run the optimistic rebuilds below first,
        // reproducing the exact "row animates in, then snaps back" symptom the sign-in gate
        // underneath was added to fix. Same reasoning, one rung up.
        guard !whale.isLocked else {
            showWhalePaywall = true
            return
        }

        // Ask the service FIRST. It owns the "does this need an account?" decision (and raises
        // the sign-in prompt), and it returns false when the mutation was never started — so a
        // signed-out tap must not reach the four optimistic list rebuilds below. Doing this
        // after the rebuilds is what produced the reported symptom: the row animated into
        // "Tracked Whales" and then vanished again with nothing said.
        guard WhaleService.shared.toggleFollow(whale.id) else { return }

        let newFollowing = !whale.isFollowing
        // `withFollowing` rather than a hand-written rebuild: every TrendingWhale field has
        // to be threaded through or it silently disappears from the row (firmName vanished
        // here once; `isLocked` is the newest field that would). The helper is the one
        // place that knows the full field list.
        let updatedWhale = whale.withFollowing(newFollowing)

        // Update isFollowing in-place across all lists
        if let index = popularWhales.firstIndex(where: { $0.id == whale.id }) {
            popularWhales[index] = updatedWhale
        }
        if let index = allPopularWhales.firstIndex(where: { $0.id == whale.id }) {
            allPopularWhales[index] = updatedWhale
        }
        if let index = heroWhales.firstIndex(where: { $0.id == whale.id }) {
            heroWhales[index] = updatedWhale
        }

        if newFollowing {
            if !trackedWhales.contains(where: { $0.id == whale.id }) {
                trackedWhales.append(updatedWhale)
            }
        } else {
            trackedWhales.removeAll { $0.id == whale.id }
        }

    }

    // MARK: - Follow State Sync

    /// Align every whale list to the authoritative followed-id set (the
    /// backend-reconciled `WhaleService.followedWhaleIds`). Runs whenever that
    /// set changes, so a reverted or cross-device follow heals the tab and the
    /// displayed `isFollowing` can never contradict the request direction.
    private func reconcileFollowState(with ids: Set<String>) {
        func synced(_ list: [TrendingWhale]) -> [TrendingWhale] {
            list.map { whale in
                let shouldFollow = ids.contains(whale.id)
                return whale.isFollowing == shouldFollow
                    ? whale
                    : whale.withFollowing(shouldFollow)
            }
        }
        popularWhales = synced(popularWhales)
        allPopularWhales = synced(allPopularWhales)
        heroWhales = synced(heroWhales)

        // Rebuild trackedWhales = the followed whales. Keep already-tracked ones
        // that are still followed (incl. profile-opened whales absent from the
        // loaded lists), then add any newly-followed whale discoverable in the
        // loaded lists so a freshly-followed row appears in the tracked section.
        var tracked = trackedWhales
            .filter { ids.contains($0.id) }
            .map { $0.isFollowing ? $0 : $0.withFollowing(true) }
        let trackedIds = Set(tracked.map(\.id))
        for whale in allPopularWhales where ids.contains(whale.id) && !trackedIds.contains(whale.id) {
            tracked.append(whale)
        }
        trackedWhales = tracked
    }

    private func handleFollowStateChange(_ notification: Notification) {
        guard let userInfo = notification.userInfo,
              let isFollowing = userInfo["isFollowing"] as? Bool else { return }

        let whaleId = userInfo["whaleId"] as? String ?? ""
        let whaleName = userInfo["whaleName"] as? String ?? ""

        func matches(_ whale: TrendingWhale) -> Bool {
            whale.id == whaleId || whale.name == whaleName
        }

        // Same reasoning as `toggleFollowWhale`: one helper owns the full field list, so a
        // field added to TrendingWhale cannot silently vanish at this site.
        func makeUpdated(_ whale: TrendingWhale, following: Bool) -> TrendingWhale {
            whale.withFollowing(following)
        }

        if let index = popularWhales.firstIndex(where: { matches($0) }) {
            popularWhales[index] = makeUpdated(popularWhales[index], following: isFollowing)
        }
        if let index = allPopularWhales.firstIndex(where: { matches($0) }) {
            allPopularWhales[index] = makeUpdated(allPopularWhales[index], following: isFollowing)
        }
        if let index = heroWhales.firstIndex(where: { matches($0) }) {
            heroWhales[index] = makeUpdated(heroWhales[index], following: isFollowing)
        }

        if isFollowing {
            guard !trackedWhales.contains(where: { matches($0) }) else { return }

            if let whale = allPopularWhales.first(where: { matches($0) }) {
                trackedWhales.append(makeUpdated(whale, following: true))
            } else if let whale = heroWhales.first(where: { matches($0) }) {
                trackedWhales.append(makeUpdated(whale, following: true))
            } else {
                let title = userInfo["whaleTitle"] as? String ?? ""
                let firm = (userInfo["whaleFirmName"] as? String).flatMap {
                    $0.isEmpty ? nil : $0
                }
                let newWhale = TrendingWhale(
                    id: whaleId,
                    name: whaleName,
                    category: .investors,
                    avatarName: "",
                    followersCount: 0,
                    isFollowing: true,
                    title: title,
                    firmName: firm
                )
                trackedWhales.append(newWhale)
            }
        } else {
            trackedWhales.removeAll { matches($0) }
        }
    }

    func viewMorePopularWhales() {
        showAllWhales = true
    }

    func viewMoreRecentTrades() {
        showAllTrades = true
    }

    func viewWhaleDetail(_ activity: WhaleActivity) {
        if let whale = allPopularWhales.first(where: { $0.name == activity.entityName }) {
            selectedWhaleId = whale.id
        }
    }

    func viewWhaleProfile(_ whale: TrendingWhale) {
        selectedWhaleId = whale.id
    }

    func viewTradeGroupDetail(_ activity: WhaleTradeGroupActivity) {
        // The destination view fetches the real trades + insights from
        // GET /whales/{whaleId}/trade-groups/{groupId} on appear. We just
        // hand it the activity so the header can render while the fetch
        // is in flight.
        selectedTradeGroup = TradeGroupNavigation(activity: activity)
    }

}
