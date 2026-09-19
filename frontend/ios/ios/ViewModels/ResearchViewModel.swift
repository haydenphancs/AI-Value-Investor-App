//
//  ResearchViewModel.swift
//  ios
//
//  ViewModel for Research screen - MVVM Architecture
//  Fetches real data from backend for reports, credits, and manages AI generation.
//

import Foundation
import Combine

@MainActor
class ResearchViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var selectedTab: ResearchTab = .research
    @Published var searchText: String = ""
    @Published var quickTickers: [QuickTicker] = QuickTicker.defaults
    @Published var personas: [AnalysisPersona] = AnalysisPersona.allCases
    /// The analyst the Research tab has pre-selected.
    ///
    /// `private(set)` on purpose: every user-driven change must go through `selectPersona(_:)`
    /// so it can mark `personaManuallyChosen`. The two picker surfaces
    /// (`PersonaSelectionSection`, `PersonasSheet`) used to write straight through
    /// `$viewModel.selectedPersona`, which would bypass that flag silently. `ContentView` now
    /// hands them a `Binding` whose setter calls the method, and this access level is what
    /// turns a future direct binding into a compile error instead of a quiet regression.
    @Published private(set) var selectedPersona: AnalysisPersona = AnalysisPersona.settingsDefault
    /// True once the user has tapped an analyst during THIS visit to the Research tab.
    /// Reset by `researchTabDidActivate()`, so a manual pick wins until they leave and return.
    private var personaManuallyChosen = false
    @Published var features: [AnalysisFeature] = AnalysisFeature.allFeatures
    /// Optional on purpose: nil = "not loaded yet / couldn't load". It used to
    /// default to a hardcoded 47-credit mock and STAY there when the fetch failed,
    /// so every user saw a fabricated balance that contradicted their real one.
    @Published var creditBalance: CreditBalance?
    @Published var trendingAnalyses: [TrendingAnalysis] = TrendingAnalysis.mockTrending
    @Published var analysisCost: AnalysisCost = .standard
    @Published var isLoading: Bool = false
    /// Backend ids of reports this session has launched that are still in
    /// flight (pending/processing). Bounded by `maxConcurrentGenerations` —
    /// drives the Generate button's enable/spinner state and gates new runs.
    @Published var inFlightReportIds: Set<String> = []
    /// Per-report live progress (percent + step) streamed from each
    /// generation's /status poll and mirrored onto its processing card.
    /// Keyed by backend report id.
    @Published var liveProgress: [String: (progress: Int, step: String)] = [:]
    @Published var error: String?

    /// Max reports one user may run at once. Mirrors the backend
    /// `MAX_CONCURRENT_REPORTS_PER_USER` cap (the server is the source of truth,
    /// enforcing it atomically pre-charge); this is the client-side gate.
    let maxConcurrentGenerations = 4

    // Reports Tab Properties
    @Published var reports: [AnalysisReport] = []
    @Published var reportSortOption: ReportSortOption = .dateNewest {
        didSet {
            sortReports()
        }
    }
    @Published var communityInsights: [CommunityInsight] = CommunityInsight.mockInsights

    // MARK: - Reports Tab: Search + Multi-Select
    /// Distinct from `searchText` (which drives the Research-tab stock target
    /// search + generateAnalysis). This one only filters the Reports list.
    @Published var reportSearchText: String = ""
    @Published var isReportSearchActive: Bool = false
    /// Persona keys selected as filter tags (empty = show all personas).
    @Published var selectedPersonaKeys: Set<String> = []
    @Published var isSelectingReports: Bool = false
    /// Keyed by `backendId` (NOT the per-load `AnalysisReport.id` UUID, which is
    /// reminted on every `loadReports()`), so a selection survives the 5s poll
    /// reload. Mock rows have no `backendId` and are therefore not selectable —
    /// which is fine, they can't be deleted either.
    @Published var selectedReportIds: Set<String> = []
    @Published var isDeletingReports: Bool = false
    @Published var showDeleteConfirm: Bool = false

    // Search results (as-you-type)
    @Published var searchResults: [StockSearchResult] = []
    /// True when the Reports tab has nothing to show because the user is signed out —
    /// distinct from "you have no reports yet", which needs different copy.
    ///
    /// ⚠️ This is a SNAPSHOT taken during `loadReports()`, not a live read of auth. It was
    /// previously written once from `init` — which runs at launch, while session restore is
    /// still in flight — and nothing ever recomputed it, so a signed-in user was told to sign
    /// in for the rest of the app run. Anything that changes auth MUST re-run `loadReports()`;
    /// `ResearchViewWithBinding` does that on `.task(id: isActiveTab)` and on an
    /// `auth.status` transition to `.authenticated`.
    @Published var requiresSignInForReports: Bool = false

    /// A credential is stored but not yet armed. Renders as "Reconnecting…", never as the
    /// sign-in prompt: this user is not signed out, and `AppState.requestSignIn` deliberately
    /// refuses to prompt in this window anyway, so the button would do nothing.
    @Published var isReconnectingReports: Bool = false

    /// When `loadBackendData()` last completed. Drives `loadIfStale()` so re-entering the tab
    /// does not refetch on every switch. Deliberately NOT set on the signed-out / reconnecting
    /// early-returns — those must stay eligible for an immediate reload.
    private var lastLoadedAt: Date?

    /// How long a completed load stays fresh.
    ///
    /// Mirrors the SHAPE of `HomeDashboardViewModel.stalenessWindow` but deliberately not its
    /// value: Home is pinned to 60s to match the server's `_CACHE_TTL_SECONDS`, while reports
    /// change on human timescales, so 300s. Do not "unify" them.
    ///
    /// `nonisolated` for the same reason as Home's — it is a default argument on
    /// `loadIfStale(maxAge:)`, and a default argument is checked as nonisolated at the call
    /// site. Safe: an immutable `Sendable` literal.
    nonisolated private static let stalenessWindow: TimeInterval = 300

    @Published var isSearching: Bool = false
    @Published var showSearchResults: Bool = false

    // Sheet presentation flags
    @Published var showCreditsSheet: Bool = false
    @Published var showPersonasSheet: Bool = false
    @Published var showProfileSheet: Bool = false
    @Published var showTargetSearchSheet: Bool = false

    /// Currently chosen company. Constraint: only one ticker at a time.
    /// Setting this also drives `searchText` so `generateAnalysis()` keeps working.
    @Published var selectedTarget: StockSearchResult?

    // MARK: - Dependencies
    private let apiClient: APIClient
    private let stockRepository: StockRepository
    private let pollingManager: TaskPollingManager
    private var searchTask: Task<Void, Never>?
    private var reportsPollTask: Task<Void, Never>?
    private var cancellables = Set<AnyCancellable>()

    /// Backend report IDs the client has locally given up on because they've
    /// been .processing past its timeout clock (see `startedTimeoutSeconds`). Survives across
    /// loadReports() calls so a stubborn backend "processing" row stays
    /// flipped to .failed in the UI. Cleared per-id when the backend reports
    /// a terminal status (.ready / .failed).
    private var locallyTimedOutReportIds: Set<String> = []

    /// Local age-out for a report the server has already given up on. Two clocks,
    /// because the server has two:
    ///
    /// - A STARTED report (`processingStartedAt` set) is killed and refunded by the
    ///   server's `RESEARCH_PIPELINE_TIMEOUT_SECONDS` (600 s) counted from work START —
    ///   after the agent semaphore. 660 s from that stamp is past the kill with margin.
    /// - A report still QUEUED (no stamp) is aged from `date` (`created_at`) against the
    ///   server's own queue-abandon window: `RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS` is
    ///   derived from the caps and is 11,400 s at current settings, and the sweep refunds a
    ///   queued row only past it. This clock must be AT LEAST that (pinned by
    ///   `test_research_list_timeout_contract.py`): at 1,800 s it flipped a healthy queued
    ///   report to "failed" 2.7 hours before the server would, the flip STOPPED the list
    ///   poll (nothing was `.processing` any more), the report then completed unseen, and
    ///   Retry deleted it — a completed row is a plain soft-delete with no refund — and
    ///   charged 20 credits again. Since followers of a deduplicated run now stamp
    ///   `processing_started_at` when their leader holds its slot, the only rows on this
    ///   clock are leaders genuinely waiting for an agent slot.
    ///
    /// This used to be one 600 s clock from `created_at` for every row, which flipped a
    /// report queued for two minutes to "failed" while the server was still generating it —
    /// and Retry then charged a second 20 credits (see `retryReport`).
    private let startedTimeoutSeconds: TimeInterval = 660
    private let queuedTimeoutSeconds: TimeInterval = 12000

    /// Where each of those clocks STARTS, per backend id — the instant THIS device first
    /// saw the row on that clock (`stamped` = it carried `processingStartedAt`; a row that
    /// gains its stamp moves from the queued clock to the started clock and re-anchors).
    ///
    /// Both clocks are aged on the DEVICE clock from that anchor, never by subtracting a
    /// server stamp (`processing_started_at`, `created_at` — the server's `now()`) from the
    /// device's `Date()`. Nothing in the client is anchored to server time, so a phone with
    /// "Set Automatically" off and its clock 15 min fast read `now - started` as 900 s on the
    /// first stamped list load — past the 660 s clock — and flipped every STARTED report to
    /// "failed" the moment it began: no Refunded chip, no explanation, and Retry then
    /// DELETED (refunded) the live run and started another that flipped the same way. The
    /// user could never see a report finish and every retry threw away a ~17-Gemini run.
    /// Aged from first observation, skew cancels: both ends are device time. The cost is
    /// an undercount of at most one poll interval (or a backgrounded gap), which only
    /// DELAYS the flip — safe, because the server's own sweep kills and refunds at 600 s
    /// and the re-read then shows that. Pruned to the rows the server still lists on every
    /// server-truth pass, so it cannot grow with deleted ids or survive an account change.
    private var timeoutClockAnchors: [String: (stamped: Bool, firstSeen: Date)] = [:]

    /// Backend report IDs the user has retried out of (or otherwise
    /// dismissed). The failed card disappears from the list immediately
    /// on retry tap; we then filter these out of every loadReports()
    /// result so it doesn't pop back when the backend still returns the
    /// stale failed row. In-memory only — app restart resets it.
    private var dismissedReportIds: Set<String> = []
    /// Backend ids whose Retry is in flight. Taken SYNCHRONOUSLY at the tap, before the
    /// Task's first await, so a second tap on the still-visible card during the credits
    /// pre-read cannot spawn a second DELETE + a second 20-credit generation (W2 E-1).
    private var retryInFlightIds: Set<String> = []

    // MARK: - Initialization
    init(prefilledTicker: String? = nil, apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        self.stockRepository = StockRepository(apiClient: apiClient)
        self.pollingManager = TaskPollingManager(apiClient: apiClient)
        if let ticker = prefilledTicker {
            _searchText = Published(initialValue: ticker)
        }
        // Start with static data immediately, then load real data
        quickTickers = QuickTicker.defaults
        personas = AnalysisPersona.allCases
        features = AnalysisFeature.allFeatures
        trendingAnalyses = TrendingAnalysis.mockTrending

        // Search is handled by the dedicated TargetSearchSheet — no debounce here.

        // Adopt a purchase the moment the backend records it.
        //
        // This ViewModel keeps its OWN `creditBalance` (it has no `AppState` reference), and it
        // was written only by `loadCredits()` from init / `refresh()` / a completed report. So
        // buying credits from this very tab left the balance at its pre-purchase value: the
        // Generate button stayed disabled and the "insufficient credits" copy stayed on screen
        // until a manual pull-to-refresh or a relaunch. The most likely reaction to paying and
        // seeing nothing change is a refund request, or a second purchase.
        //
        // `.caydexEntitlementChanged` is the single funnel `StoreKitService` posts from for BOTH
        // interactive purchases and `Transaction.updates` replays, so this also covers a pack
        // that lands while the app is backgrounded.
        entitlementObserver = NotificationCenter.default.addObserver(
            forName: .caydexEntitlementChanged, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in await self?.loadCredits() }
        }

        // Adopt a changed "Default Analyst" without waiting for a relaunch.
        //
        // TWO notifications, answering two different failure modes:
        //
        //  • `.caydexDefaultPersonaChanged` — the user just changed it in Settings. Settings is
        //    a `fullScreenCover` above the whole tree, so dismissing it rebuilds nothing and
        //    there is no view-update path that would otherwise reach this ViewModel.
        //
        //  • `.caydexSettingsHydrated` — the SERVER's value has just landed in UserDefaults.
        //    `SettingsSyncManager.hydrate()` runs from `AppState.onAuthenticated()`, i.e. AFTER
        //    this ViewModel is constructed, so on a fresh install or a new device the stored
        //    default arrives too late for the property initializer above and the tab would open
        //    on Buffett even on a cold launch.
        //
        // They differ in ONE way, and it is load-bearing: the explicit change FORCES, the
        // server hydrate does not.
        //
        // Settings is reached through `ProfileView`, a `.fullScreenCover` on this very screen —
        // and a cover does not change `\.isActiveTab`, so `researchTabDidActivate()` does NOT
        // fire on the way back. Without `force` the sequence "tap an analyst → open Settings →
        // change Default Analyst → return" would leave the tapped analyst in place, which is
        // the reported bug wearing a different hat. Deliberately changing the setting is the
        // more recent and more explicit statement of intent, so it wins over an earlier tap.
        //
        // A hydrate is the opposite: it is background sync the user did not ask for, and it
        // fires on every foreground and network-restore, so it must never yank a manual pick.
        defaultPersonaObservers = [
            (Notification.Name.caydexDefaultPersonaChanged, true),
            (Notification.Name.caydexSettingsHydrated, false),
        ].map { name, force in
            NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor [weak self] in self?.applyDefaultPersona(force: force) }
            }
        }

        // Deliberately NO load here — same rule as `UpdatesViewModel.init` and
        // `TrackingViewModel.init`.
        //
        // `ContentView` opacity-mounts all five tabs in one ZStack, so this initializer runs
        // at app launch for every user, including one who never opens the Research tab. That
        // made `loadBackendData()` — four requests (`/research/reports`, `/users/me/credits`,
        // `/research/trending`, `/research/personas`) — unconditional launch traffic. The
        // view's `.task(id: isActiveTab)` calls `loadIfStale()` when the tab first becomes
        // active, and `.reloadOnIdentityChange` covers signing in or out while already here.
    }

    /// Token for the `.caydexEntitlementChanged` observer, removed on deinit.
    private var entitlementObserver: NSObjectProtocol?
    /// Tokens for the two "Default Analyst" observers, removed on deinit.
    private var defaultPersonaObservers: [NSObjectProtocol] = []

    deinit {
        if let entitlementObserver {
            NotificationCenter.default.removeObserver(entitlementObserver)
        }
        for observer in defaultPersonaObservers {
            NotificationCenter.default.removeObserver(observer)
        }
    }

    // MARK: - Backend Data Loading

    /// Load real reports + credits + trending + personas from the backend.
    /// Falls back to mock/static defaults on failure.
    /// The load currently in flight, if any. Concurrent callers JOIN it.
    ///
    /// Tab activation and the identity-change reload land within milliseconds of each other
    /// on a signed-in launch, and each one fanned out four requests because nothing here
    /// consulted an in-flight flag — only `lastLoadedAt`, which is not yet set while the
    /// first load is still running.
    private var loadTask: Task<Void, Never>?

    private func loadBackendData() async {
        if let running = loadTask, !running.isCancelled {
            await running.value
            return
        }
        let task = Task { [weak self] in
            guard let self else { return }
            await self.performBackendLoad()
            // Cleared HERE, as the task's own last act, rather than after `await
            // task.value` below. Between a task finishing and its awaiting caller
            // being resumed, a THIRD caller can run and observe a completed-but-still
            // -registered task: it would "join" something already done and return
            // instantly without ever loading. That is a silently skipped refresh —
            // and for `handleIdentityChange` it would mean adopting a load that
            // completed under the PREVIOUS identity.
            self.loadTask = nil
        }
        loadTask = task
        await task.value
    }

    private func performBackendLoad() async {
        async let reportsTask: () = loadReports()
        async let creditsTask: () = loadCredits()
        async let trendingTask: () = loadTrending()
        async let personasTask: () = loadPersonas()
        _ = await (reportsTask, creditsTask, trendingTask, personasTask)

        // Only a load that actually saw the account counts as fresh. Marking the signed-out or
        // reconnecting pass as fresh would let the 5-minute window suppress the reload that
        // heals it — i.e. the staleness guard would re-create the bug it is sitting next to.
        if !requiresSignInForReports && !isReconnectingReports {
            lastLoadedAt = Date()
        }
    }

    /// Reload only if the last successful load has aged out.
    ///
    /// Mirrors `HomeDashboardViewModel.loadIfStale` so tab re-entry is cheap. A signed-out or
    /// mid-restore state is never "fresh" (see `loadBackendData`), so arriving on the tab after
    /// signing in always refetches.
    func loadIfStale(maxAge: TimeInterval = ResearchViewModel.stalenessWindow) async {
        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }
        await loadBackendData()
    }

    /// The signed-in identity changed — these reports belong to the previous one.
    ///
    /// Clearing first matters in the sign-OUT direction: nothing in
    /// `AppState.discardDataForEndedSession()` reaches into this ViewModel, so without it the
    /// previous account's analyses (ticker, score, fair value) stayed on screen for whoever
    /// used the device next.
    func handleIdentityChange(isActiveTab: Bool) async {
        // CLEAR FIRST, UNCONDITIONALLY — before the `isActiveTab` gate below. The reload is
        // deferred for a hidden tab, but the previous account's data must not survive in this
        // ViewModel waiting to be rendered (.claude/rules/auth.md §7).
        reports = []
        selectedReportIds = []
        isSelectingReports = false
        creditBalance = nil
        lastLoadedAt = nil
        error = nil
        // The previous account's in-flight bookkeeping must not gate or poll for the next
        // one: a non-empty `locallyTimedOutReportIds` keeps the 5 s list poll alive against
        // an account that never owned those ids, and `inFlightReportIds` would hold the
        // Generate button at the concurrency cap for reports the new account cannot see.
        stopReportsPolling()
        locallyTimedOutReportIds = []
        dismissedReportIds = []
        inFlightReportIds = []
        liveProgress = [:]
        // The analyst is per-ACCOUNT, so it must not survive a sign-out or an account switch
        // either. `SettingsSyncManager.clearLocalForEndedSession()` removes the stored key on
        // sign-out and the next account's `hydrate()` writes its own, so re-deriving here is
        // what keeps this ViewModel from rendering the previous user's choice. `force` because
        // their manual pick is exactly what must not carry over.
        applyDefaultPersona(force: true)

        // Fetch only if the user is actually looking at this tab. Clearing above nils the
        // freshness stamp, so `.task(id: isActiveTab)` re-loads on the next activation.
        guard isActiveTab else { return }
        await loadBackendData()
    }

    /// Fetch active personas from GET /research/personas.
    func loadPersonas() async {
        print("👤 ResearchVM: Loading personas from backend...")
        do {
            let backend: [BackendPersona] = try await apiClient.request(
                endpoint: .getPersonas,
                responseType: [BackendPersona].self
            )
            print("✅ ResearchVM: Loaded \(backend.count) personas")
            let mapped = backend.map(AnalysisPersona.from)
            guard !mapped.isEmpty else { return }
            self.personas = mapped
            // Re-derive against the list the backend actually serves.
            //
            // This deliberately does NOT assign `selectedPersona` itself — that would be a
            // third writer and defeat the single-writer design `private(set)` exists to
            // enforce. It also does not fall back to `mapped[0]`, as it used to: GET
            // /research/personas has no ORDER BY, so "first" is whatever row order Postgres
            // happened to return, and arbitrary row order must never decide a user-visible
            // default.
            //
            // `force` only when the current selection is gone. Otherwise a hand-picked analyst
            // that IS still served survives, while the non-forced call still lets a
            // `default_persona` synced from a newer build — a key with no hardcoded case, which
            // resolved to Buffett at launch — be adopted now that the real list is here.
            let stillServed = mapped.contains { $0.key == self.selectedPersona.key }
            self.applyDefaultPersona(force: !stillServed)
        } catch {
            print("⚠️ ResearchVM: Failed to load personas — \(error). Keeping fallbacks.")
        }
    }

    /// Fetch trending analyses from GET /research/trending.
    func loadTrending() async {
        print("📈 ResearchVM: Loading trending analyses from backend...")
        do {
            let backendTrending: [BackendTrendingAnalysis] = try await apiClient.request(
                endpoint: .getTrendingAnalyses,
                responseType: [BackendTrendingAnalysis].self
            )
            print("✅ ResearchVM: Loaded \(backendTrending.count) trending themes")
            let mapped = backendTrending.map(TrendingAnalysis.from)
            if !mapped.isEmpty {
                self.trendingAnalyses = mapped
            }
        } catch {
            print("⚠️ ResearchVM: Failed to load trending — \(error). Keeping current data.")
            // Keep existing (mock) value
        }
    }

    /// Detect reports stuck in .processing past their timeout clock,
    /// register their backend IDs, and flip them to `.failed` locally so the
    /// ReportCard's failed branch (with the Retry button) appears. Runs
    /// against `self.reports` in-place after every load attempt. Mock
    /// reports without a `backendId` are skipped — the timeout only applies
    /// to real backend-tracked generations.
    ///
    /// `serverTruth` says whether `reports` was JUST rebuilt from the server. Only then may a
    /// terminal row clear its flag: after a FAILED list GET the in-memory rows still carry
    /// this client's own flip (`withClientTimeout()` renders `.failed`), and reading that as
    /// "the server says failed" removed the id — so the next tick saw nothing in flight, the
    /// poll exited, and the report completed (or was swept and refunded) into a list nobody
    /// re-read. The card stayed "failed" with no Refunded chip until a manual refresh.
    private func applyClientSideTimeoutPass(serverTruth: Bool) {
        let now = Date()
        var inFlightIds = Set<String>()
        reports = reports.map { report in
            guard let backendId = report.backendId else { return report }
            // Backend gave us a terminal status — trust it, clear any prior flag.
            if report.status == .ready || report.status == .failed {
                if serverTruth {
                    locallyTimedOutReportIds.remove(backendId)
                    timeoutClockAnchors.removeValue(forKey: backendId)
                }
                return report
            }
            inFlightIds.insert(backendId)
            // Still .processing — age out against the clock the server actually uses, but
            // from the instant THIS device first saw the row on that clock (see
            // `timeoutClockAnchors`): the server stamps are read only to pick the clock,
            // never subtracted from the device's `Date()`.
            let stamped = report.processingStartedAt != nil
            let anchor: Date
            if let existing = timeoutClockAnchors[backendId], existing.stamped == stamped {
                anchor = existing.firstSeen
            } else {
                anchor = now
                timeoutClockAnchors[backendId] = (stamped: stamped, firstSeen: now)
            }
            let limit = stamped ? startedTimeoutSeconds : queuedTimeoutSeconds
            let timedOut = now.timeIntervalSince(anchor) > limit
            if timedOut {
                locallyTimedOutReportIds.insert(backendId)
            }
            if locallyTimedOutReportIds.contains(backendId) {
                return report.withClientTimeout()
            }
            return report
        }
        if serverTruth {
            // The list endpoint hides deleted rows, so an anchor for an id the server no
            // longer lists would otherwise live for the rest of the process — same leak the
            // flag set had (`formIntersection` in `loadReports`).
            timeoutClockAnchors = timeoutClockAnchors.filter { inFlightIds.contains($0.key) }
        }
    }

    /// Fetch user's research reports from GET /research/reports
    func loadReports() async {
        // Reports belong to an account now, so a signed-out user has none to load — say that,
        // rather than firing a request that will be refused.
        //
        // THREE outcomes, not two. "Not armed right now" is not the same as "signed out": at
        // launch this runs while session restore is still in flight, and a restore that keeps
        // failing backs off forever. Collapsing that into the sign-in prompt is what told a
        // signed-in user to sign in — with their own avatar loaded in the header above it.
        guard AppActions.shared.isSignedIn else {
            reports = []
            let reconnecting = AppActions.shared.isRestoringSession
            isReconnectingReports = reconnecting
            requiresSignInForReports = !reconnecting
            return
        }
        requiresSignInForReports = false
        isReconnectingReports = false

        print("📋 ResearchVM: Loading reports from backend...")
        do {
            let backendReports: [BackendReportListItem] = try await apiClient.request(
                endpoint: .getMyReports(limit: 50),
                responseType: [BackendReportListItem].self
            )
            print("✅ ResearchVM: Loaded \(backendReports.count) reports from backend")
            // DRAIN the local-timeout set to ids the server still lists — against the RAW
            // list, before the dismissed filter, so a retry's pre-check still sees a row it
            // has dismissed but not yet deleted. The list endpoint hides deleted rows, so an
            // id retired by Retry, by a bulk delete, or by a delete on another device never
            // came back through the pass and its flag never cleared: `startReportsPolling`
            // read the non-empty set as "something in flight" and issued the list GET every
            // 5 s for the rest of the process (~720/h), reminting every row's UUID each time.
            // The pass below re-inserts any row that is still genuinely stuck.
            locallyTimedOutReportIds.formIntersection(Set(backendReports.map(\.id)))
            self.reports = backendReports
                .filter { !dismissedReportIds.contains($0.id) }
                .map { AnalysisReport.from($0) }
            applyClientSideTimeoutPass(serverTruth: true)
            sortReports()
            applyLiveProgress()   // keep the in-flight row at the live stream %
        } catch {
            // NEVER fabricate. This used to fall back to `AnalysisReport.mockReports` when the
            // list was empty, which showed invented analyses — with tickers, scores and fair
            // values — as if they were the user's own. A failed load is not a set of reports.
            let appError = AppError.from(error)
            // A cancelled tick (tab switch mid-load) is nobody's failure: no sync-failed
            // analytics, and never a blank "cancelled" alert over the list.
            guard !appError.isCancellation else { return }
            Analytics.shared.track(.backgroundSyncFailed, [
                "op": .string("load_reports"),
                "code": .string(appError.analyticsCode),
            ])
            if reports.isEmpty {
                self.error = appError.message
            } else {
                // Network blip with rows already on screen — keep them but still age out the
                // stale ones, and don't overwrite what the user is looking at. NOT server
                // truth: a row this client flipped must keep its flag through the outage.
                applyClientSideTimeoutPass(serverTruth: false)
                sortReports()
            }
        }
    }

    /// Fetch user's credit balance from GET /users/me/credits
    func loadCredits() async {
        // A signed-out caller resolves to the shared guest sentinel, which is seeded with
        // 100,000 credits — so the Research tab told guests they had "99,999,530 credits
        // remaining" while the Generate button now asks them to sign in. That number was never
        // theirs and is not spendable; leaving the balance unknown hides the badge entirely,
        // which is the honest state.
        guard AppActions.shared.isSignedIn else {
            creditBalance = nil
            return
        }

        print("💳 ResearchVM: Loading credits from backend...")
        do {
            let backendCredits: BackendCreditsResponse = try await apiClient.request(
                endpoint: .getUserCredits,
                responseType: BackendCreditsResponse.self
            )
            print("✅ ResearchVM: Credits loaded — \(backendCredits.remaining) remaining of \(backendCredits.total)")
            self.creditBalance = CreditBalance.from(backendCredits)
        } catch {
            // Leave `creditBalance` nil rather than inventing a number — the UI hides
            // the badge/card instead of showing a balance the user doesn't have.
            print("⚠️ ResearchVM: Failed to load credits — \(error). Leaving balance unknown.")
        }
    }

    func refresh() async {
        guard !isDeletingReports else { return }   // don't race the delete fan-out
        isLoading = true
        await loadBackendData()
        isLoading = false
    }

    // MARK: - Reports Tab Live Polling

    /// Poll the reports list every 5s while any report is in-flight.
    /// Called when the user switches to the Reports tab. Self-terminates
    /// once no processing/pending reports remain — no need to cancel
    /// manually in that case.
    func startReportsPolling() {
        stopReportsPolling()
        reportsPollTask = Task { [weak self] in
            // 5s cadence balances "card animates" with FMP/Supabase load.
            // Each tick is a single Supabase query — no FMP cost.
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                if Task.isCancelled { return }
                guard let self = self else { return }
                // A card this client flipped to "failed" on its own clock is NOT terminal on
                // the server: it is still queued or running there, and it heals only by
                // re-reading. The poll used to exit the moment nothing was `.processing`,
                // which was exactly when the flipped card most needed it — the report then
                // completed into a list nobody re-read.
                let hasInflight = self.reports.contains { $0.status == .processing }
                    || !self.locallyTimedOutReportIds.isEmpty
                if !hasInflight {
                    return
                }
                // Don't churn the list mid-selection — a reload remints row
                // UUIDs and can reorder rows under the user. Skip this tick;
                // the task stays alive and resumes once selection ends.
                if self.isSelectingReports { continue }
                await self.loadReports()
            }
        }
    }

    func stopReportsPolling() {
        reportsPollTask?.cancel()
        reportsPollTask = nil
    }

    /// Mirror the live generation stream onto the in-flight report row so the
    /// processing card animates in real time. The GET /reports list query's
    /// stored progress can lag the per-report /status poll the stream uses, so
    /// without this the card freezes at the last list-refreshed value (e.g. 5%
    /// while the stream is already at 20%). No-op once the row goes ready/failed.
    private func applyLiveProgress() {
        guard !reports.isEmpty else { return }
        for (rid, lp) in liveProgress where lp.progress > 0 {
            guard let idx = reports.firstIndex(where: { $0.backendId == rid }),
                  reports[idx].status == .processing else { continue }
            reports[idx].progress = Double(lp.progress) / 100.0
            if !lp.step.isEmpty {
                reports[idx].currentStep = lp.step
            }
        }
    }

    // MARK: - Search

    private func setupSearchDebounce() {
        $searchText
            .debounce(for: .milliseconds(300), scheduler: RunLoop.main)
            .removeDuplicates()
            .sink { [weak self] query in
                guard let self else { return }
                let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
                if trimmed.isEmpty {
                    self.searchResults = []
                    self.showSearchResults = false
                    self.searchTask?.cancel()
                    return
                }
                self.performSearch(query: trimmed)
            }
            .store(in: &cancellables)
    }

    private func performSearch(query: String) {
        searchTask?.cancel()
        searchTask = Task { [weak self] in
            guard let self else { return }
            self.isSearching = true
            self.showSearchResults = true
            do {
                let results = try await self.stockRepository.searchStocks(query: query, limit: 8)
                if !Task.isCancelled {
                    self.searchResults = results
                    self.isSearching = false
                }
            } catch {
                if !Task.isCancelled {
                    self.searchResults = []
                    self.isSearching = false
                }
            }
        }
    }

    // `selectSearchResult(_:)` used to live here. It was DEAD — the only call site,
    // `SearchView.swift:72`, resolves to `SearchViewModel`'s method of the same name — and it
    // wrote `searchText` WITHOUT `selectedTarget`, which is precisely the shape that made the
    // chip and the charged ticker disagree. Deleted rather than left as a loaded gun; use
    // `applyPrefilledTicker(_:)` or `selectTarget(_:)`, both of which write the pair.

    func dismissSearchResults() {
        showSearchResults = false
    }

    // MARK: - Actions

    /// The single writer for a USER-driven analyst change.
    ///
    /// Both picker surfaces route here through the `Binding` built in `ContentView`, so this is
    /// the one place that can mark the selection as deliberate. Anything that sets
    /// `selectedPersona` without going through here is a default being applied, not a choice.
    func selectPersona(_ persona: AnalysisPersona) {
        selectedPersona = persona
        personaManuallyChosen = true
    }

    /// Re-read Settings → "Default Analyst" and adopt it.
    ///
    /// No-op once the user has picked an analyst by hand during this visit, unless `force` —
    /// which is for an identity change, where the previous account's choice must not carry over.
    func applyDefaultPersona(force: Bool = false) {
        guard force || !personaManuallyChosen else { return }
        if force { personaManuallyChosen = false }
        selectedPersona = AnalysisPersona.settingsDefault(in: personas)
    }

    /// The Research tab just became visible.
    ///
    /// Clearing the manual-override flag here is what makes the setting's own promise
    /// ("Pre-selected for new research") true: a one-off pick applies to the visit it was made
    /// in, and coming back to the tab starts from the user's stated default again.
    func researchTabDidActivate() {
        personaManuallyChosen = false
        applyDefaultPersona()
    }

    func selectQuickTicker(_ ticker: QuickTicker) {
        searchText = ticker.symbol
        searchResults = []
        showSearchResults = false
        selectedTarget = StockSearchResult(
            ticker: ticker.symbol,
            companyName: ticker.symbol,
            exchange: nil,
            sector: nil,
            logoUrl: nil,
            type: "stock"
        )
    }

    // MARK: - Target Selection

    func openTargetSearch() {
        showTargetSearchSheet = true
    }

    func selectTarget(_ result: StockSearchResult) {
        selectedTarget = result
        searchText = result.ticker
        showTargetSearchSheet = false
    }

    func clearTarget() {
        selectedTarget = nil
        searchText = ""
    }

    /// Adopt a ticker handed over by "AI Deep Research" on a detail screen.
    ///
    /// ⚠️ THIS MUST SET BOTH FIELDS, and that is not tidiness — they are read by different code.
    /// `TargetSelectionSection` renders `selectedTarget` and only falls back to `searchText` when
    /// it is nil, while `generateAnalysis()` takes the ticker from `searchText`. The handoff used
    /// to write `searchText` alone, so a target left over from an earlier `TargetSearchSheet`
    /// pick kept rendering — the user saw the OLD company in the chip and Generate spent 20
    /// credits on the NEW one. A displayed target that is not the target being charged for is a
    /// money bug, not a display bug.
    ///
    /// Mirrors `selectQuickTicker` above, including using the symbol as the display name: the
    /// route carries a ticker only, and the chip shows the ticker prominently either way.
    func applyPrefilledTicker(_ ticker: String) {
        let symbol = ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !symbol.isEmpty else { return }
        searchText = symbol
        searchResults = []
        showSearchResults = false
        selectedTarget = StockSearchResult(
            ticker: symbol,
            companyName: symbol,
            exchange: nil,
            sector: nil,
            logoUrl: nil,
            type: "stock"
        )
    }

    func generateAnalysis() {
        print("🔬 ResearchVM: generateAnalysis() tapped — searchText='\(searchText)', persona=\(selectedPersona.backendKey), credits=\(creditBalance?.credits.description ?? "unknown")")

        // Bounded concurrency: a user may run up to `maxConcurrentGenerations`
        // reports at once (e.g. 4 personas on one ticker, or 1 persona on 4
        // tickers). The backend enforces the same cap atomically pre-charge;
        // this is the client gate + a debounce against a tap double-firing.
        guard activeGenerationCount < maxConcurrentGenerations else {
            print("⚠️ ResearchVM: at concurrency cap (\(maxConcurrentGenerations)), ignoring tap.")
            error = "You can run up to \(maxConcurrentGenerations) analyses at once — wait for one to finish."
            return
        }

        // AI generation is account-only. Ask BEFORE spending anything — `APIClient` would refuse
        // the request anyway (`authPolicy == .signInRequired`), but reaching that as an error is
        // a worse experience than being invited to sign in.
        //
        // Note this is not the old `showSignInPrompt`, which was dead code that never fired and
        // was deleted: at the time, research really was guest-capable, so gating it would have
        // removed a working feature. What changed is the policy — the guest allowance keyed on a
        // client-supplied `X-Guest-Id`, so rotating that header bought unlimited ~17-Gemini-call
        // generations. Credits are FK-bound to a real account and can't be rotated; a free
        // account gets 50 credits against a 20-credit report, so 2/month vs the guest's 1.
        guard AppActions.shared.isSignedIn else {
            AppActions.shared.requestSignIn(for: "generate AI analysis")
            return
        }

        let ticker = searchText.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            print("⚠️ ResearchVM: bailed — ticker is empty after trimming. Surfacing error.")
            error = "Please select a ticker first."
            return
        }
        // Unknown balance (nil) does NOT block: the backend charges atomically and
        // returns 402 INSUFFICIENT_CREDITS, which is the real authority. Blocking on a
        // balance we failed to fetch would lock a paying user out of the feature.
        if let balance = creditBalance, balance.credits < analysisCost.credits {
            print("⚠️ ResearchVM: bailed — insufficient credits (\(balance.credits) < \(analysisCost.credits)).")
            error = "Insufficient credits"
            return
        }

        selectedTab = .reports
        error = nil

        let personaKey = selectedPersona.backendKey

        print("🔬 ResearchVM: Generating analysis for \(ticker) with persona \(personaKey)...")

        // Each generation runs its OWN monitor Task + stream so up to
        // `maxConcurrentGenerations` can run concurrently without cross-talk.
        // `startedId` is captured per-Task so terminal cleanup targets the
        // right report id. TaskPollingManager is an actor handing back an
        // independent stream per call, so concurrent monitors are safe.
        Task { [weak self] in
            guard let self = self else { return }
            var startedId: String?
            let tapTime = Date()

            do {
                let stream = await self.pollingManager.generateAndMonitorResearch(
                    stockId: ticker,
                    persona: personaKey
                )

                // Track the last percent we used to refresh the list,
                // so we don't hammer the backend on every tick.
                var lastListRefreshPercent = -1

                for try await progress in stream {
                    switch progress {
                    case .started(let taskId):
                        Analytics.shared.track(.reportRequested, [
                            "ticker": .string(ticker),
                            "persona": .string(personaKey),
                        ])
                        print("🔬 ResearchVM: Research started — report ID: \(taskId)")
                        startedId = taskId
                        self.inFlightReportIds.insert(taskId)
                        self.liveProgress[taskId] = (progress: 0, step: "Research initiated...")
                        // Surface the new pending row in the Reports list
                        // immediately so the processing card appears the
                        // moment the user switches tabs.
                        await self.loadReports()

                    case .progress(let percent, let step):
                        print("🔬 ResearchVM: Progress \(percent)% — \(step)")
                        if let id = startedId {
                            self.liveProgress[id] = (progress: percent, step: step)
                        }
                        self.applyLiveProgress()   // update this card now
                        // Refresh the list at 25% boundaries so the card
                        // animates without spamming Supabase. The poller
                        // in startReportsPolling() is the steady-state
                        // updater; this is a coarser belt-and-braces.
                        let bucket = (percent / 25) * 25
                        if bucket > lastListRefreshPercent {
                            lastListRefreshPercent = bucket
                            await self.loadReports()
                        }

                    case .completed(let report):
                        Analytics.shared.track(.reportCompleted, [
                            "ticker": .string(ticker),
                            "persona": .string(personaKey),
                        ])
                        print("✅ ResearchVM: Research complete for \(ticker) — \(report.title ?? "Untitled")")
                        if let id = startedId {
                            self.inFlightReportIds.remove(id)
                            self.liveProgress[id] = nil
                        }
                        // Reload reports and credits from backend to get fresh data
                        await self.loadReports()
                        await self.loadCredits()

                    case .failed(let appError):
                        if let id = startedId {
                            self.inFlightReportIds.remove(id)
                            self.liveProgress[id] = nil
                        }
                        // The user deleted this card while it was generating (or it was
                        // deleted from another device): the monitor's next poll reads
                        // `deleted`, which the polling manager reports as a terminal failure.
                        // That is the outcome the user asked for, not an error — it used to
                        // pop an "Error: This analysis is no longer available" alert over the
                        // list they had just cleaned, and log a `reportFailed`.
                        if let id = startedId, self.dismissedReportIds.contains(id) {
                            print("🗑️ ResearchVM: monitor for \(id) ended after the user deleted it — no alert")
                            continue
                        }
                        if case .apiError(let code, _) = appError, code == TaskPollingManager.deletedCode {
                            print("🗑️ ResearchVM: report was deleted elsewhere — no alert")
                            await self.loadReports()
                            // That delete refunded the charge on the other device; this
                            // one's balance was read before it.
                            await self.loadCredits()
                            continue
                        }
                        // The monitor was cancelled (account switch, app teardown): nobody
                        // is waiting on it, and the report itself is not failed.
                        if appError.isCancellation {
                            print("🛑 ResearchVM: monitor for \(ticker) cancelled — no alert")
                            continue
                        }
                        if case .timeout = appError, startedId != nil {
                            // CLIENT-side poll timeout only — NOT a real
                            // failure. The backend keeps generating; the
                            // report still resolves in the Reports list
                            // (startReportsPolling), and if it never delivers
                            // the server-side reconciliation sweep refunds the
                            // credits. Don't surface a hard error — keep the
                            // in-flight card and point the user at the Reports
                            // tab.
                            //
                            // `startedId != nil` is what makes this the POLL's timeout:
                            // with no `.started` yet, the only producer of `.timeout` is
                            // the POST itself (F09-10).
                            Analytics.shared.track(.reportFailed, [
                                "ticker": .string(ticker),
                                "reason": .string(appError.analyticsCode),
                            ])
                            print("⏳ ResearchVM: client poll timed out — report continues on the server")
                            await self.loadReports()
                            self.startReportsPolling()
                        } else if case .timeout = appError {
                            // The POST to /research/generate timed out BEFORE a report id
                            // came back. The server may have committed the row and charged
                            // 20 credits, or never received it — unknowable from here. This
                            // used to be classified as the benign poll timeout above: no
                            // error, no in-flight card, and the next tap could charge a
                            // second time. Reload the list and the wallet so a charge that
                            // landed is visible, adopt a fresh processing row for this
                            // (ticker, persona) silently if one appears, and otherwise say so.
                            Analytics.shared.track(.reportFailed, [
                                "ticker": .string(ticker),
                                "reason": .string("post_timeout"),
                            ])
                            print("⏳ ResearchVM: POST timed out before a report id — reconciling with the list")
                            await self.loadReports()
                            await self.loadCredits()
                            // A minute of slack on the row's stamp: the phone's clock and
                            // the server's need not agree to the second.
                            let adopted = self.reports.first {
                                $0.ticker.uppercased() == ticker.uppercased()
                                    && $0.persona.backendKey == personaKey
                                    && $0.status == .processing
                                    && $0.date >= tapTime.addingTimeInterval(-60)
                            }
                            if let adopted, let adoptedId = adopted.backendId {
                                // Adopt by LIST: the row is already a processing card, and
                                // the 5 s list poll carries it to completion. NOT into
                                // `inFlightReportIds` — no monitor owns that id, so nothing
                                // would ever remove it and one of the four concurrency slots
                                // stayed burned for the session (W2 E-2).
                                print("✅ ResearchVM: POST timed out but the report row exists — adopting \(adoptedId) via the list")
                                self.startReportsPolling()
                            } else {
                                self.error = "We couldn't confirm the request — check your Reports before retrying."
                                self.startReportsPolling()
                            }
                        } else {
                            // `code` only — never the message, which can carry backend text.
                            Analytics.shared.track(.reportFailed, [
                                "ticker": .string(ticker),
                                "reason": .string(appError.analyticsCode),
                            ])
                            print("❌ ResearchVM: Research failed — \(type(of: appError)): \(appError.message)")
                            self.error = appError.message
                            // Refresh so the failed card appears in the list — and adopt
                            // the server's refund, which `creditBalance` had no other way
                            // to learn about (it refreshes on init / completion only).
                            await self.loadReports()
                            await self.loadCredits()
                        }
                    }
                }
            } catch {
                print("❌ ResearchVM: Research stream error — \(type(of: error)): \(error)")
                if let id = startedId {
                    self.inFlightReportIds.remove(id)
                    self.liveProgress[id] = nil
                }
                // `AppError.from`, not `localizedDescription`. `APIError` does not conform to
                // `LocalizedError`, so this rendered "The operation couldn't be completed.
                // (ios.APIError error 4.)" on the app's most expensive action — and since report
                // generation became `.signInRequired`, the pre-flight `APIError.authRequired`
                // throw is now a ROUTINE outcome on this exact path, not a rare one.
                self.error = AppError.from(error).message
            }
        }
    }

    func addMoreCredits() {
        showCreditsSheet = true
    }

    func viewAllPersonas() {
        showPersonasSheet = true
    }

    func showProfile() {
        showProfileSheet = true
    }

    // MARK: - Reports Tab Actions
    func sortReports() {
        switch reportSortOption {
        case .dateNewest:
            reports.sort { $0.date > $1.date }
        case .dateOldest:
            reports.sort { $0.date < $1.date }
        case .ratingHigh:
            reports.sort { ($0.rating ?? 0) > ($1.rating ?? 0) }
        case .ratingLow:
            reports.sort { ($0.rating ?? 0) < ($1.rating ?? 0) }
        }
    }

    // MARK: - Reports Tab: Derived (search + grouping)

    /// `reports` filtered by the selected persona tags AND the search query.
    /// Persona tags (if any) restrict to those personas; the search then matches
    /// ticker, company name, persona full name ("Cathie Wood") or agent label
    /// ("Wood Agent"), case-insensitive. `reports` is already sorted in place by
    /// sortReports(), so this preserves the chosen sort order.
    var filteredReports: [AnalysisReport] {
        var result = reports

        // Persona filter tags (OR across selected personas; empty = all).
        if !selectedPersonaKeys.isEmpty {
            result = result.filter { selectedPersonaKeys.contains($0.persona.key) }
        }

        // Search query.
        let q = reportSearchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !q.isEmpty {
            result = result.filter {
                $0.ticker.lowercased().contains(q)
                    || $0.companyName.lowercased().contains(q)
                    || $0.persona.name.lowercased().contains(q)
                    || $0.persona.agentLabel.lowercased().contains(q)
            }
        }

        return result
    }

    /// Filtered reports grouped into time bands, ordered newest → oldest.
    /// Empty bands are omitted so no stray section header renders. The Sort
    /// option orders cards WITHIN each band (since `filteredReports` is sorted).
    var groupedReports: [ReportSectionGroup] {
        let buckets = Dictionary(grouping: filteredReports) { ReportTimeSection.bucket(for: $0.date) }
        return ReportTimeSection.allCases.compactMap { section in
            guard let rows = buckets[section], !rows.isEmpty else { return nil }
            return ReportSectionGroup(section: section, reports: rows)
        }
    }

    var selectedReportCount: Int { selectedReportIds.count }

    /// Toggle a persona filter tag on/off.
    func togglePersonaTag(_ persona: AnalysisPersona) {
        if selectedPersonaKeys.contains(persona.key) {
            selectedPersonaKeys.remove(persona.key)
        } else {
            selectedPersonaKeys.insert(persona.key)
        }
    }

    // MARK: - Reports Tab: Selection + Delete

    func toggleReportSelection(_ report: AnalysisReport) {
        guard let bid = report.backendId else { return }   // mock rows aren't selectable
        if selectedReportIds.contains(bid) {
            selectedReportIds.remove(bid)
        } else {
            selectedReportIds.insert(bid)
        }
    }

    func exitSelectionMode() {
        isSelectingReports = false
        selectedReportIds.removeAll()
    }

    /// Delete every selected report. Fans out parallel DELETEs against the
    /// existing per-report endpoint (soft-delete, idempotent → safe to parallel).
    /// Rows are removed optimistically and seeded into `dismissedReportIds` so a
    /// subsequent poll/loadReports() can't resurrect them. On partial failure the
    /// failed ids are un-dismissed and the list is reconciled via loadReports().
    func deleteSelectedReports() async {
        guard !selectedReportIds.isEmpty, !isDeletingReports else { return }
        isDeletingReports = true
        defer { isDeletingReports = false }

        let ids = Array(selectedReportIds)

        // Optimistic removal + dismiss-seed (mirrors retryReport's pattern).
        for id in ids { dismissedReportIds.insert(id) }
        reports.removeAll { report in
            guard let bid = report.backendId else { return false }
            return ids.contains(bid)
        }
        // Release the client-side concurrency slots too. `inFlightReportIds` gates the
        // Generate button (`isAtConcurrencyCap`), and deleting four still-generating
        // cards used to leave all four ids in it — so the button stayed spinning and
        // refused new work until the app was relaunched, for reports that no longer
        // existed. The monitoring stream stops on its own now that the poll loop has a
        // terminal branch for `deleted`.
        for id in ids {
            inFlightReportIds.remove(id)
            liveProgress[id] = nil
        }
        // Which of these did THIS client flip to "failed" on its own clock? Read before
        // the fan-out: a flipped card is failed only locally — the server may have finished
        // it — so its DELETE carries the retry intent, which the backend refuses with
        // `REPORT_ALREADY_COMPLETED` on a finished row instead of soft-deleting it
        // unrefunded. A deliberate delete of a card the SERVER marked completed stays a
        // plain delete. (Bulk-deleting a "failed, not refunded" card that had actually
        // completed forfeited 20 credits for a report the user never saw.)
        let clientFlipped = locallyTimedOutReportIds.intersection(ids)
        exitSelectionMode()

        // Parallel fan-out. A Sendable tri-state, so no `any Error` crosses the boundary.
        enum Outcome: Sendable { case deleted, failed, completedInstead }
        var failedIds: [String] = []
        var keptIds: [String] = []
        await withTaskGroup(of: (String, Outcome).self) { group in
            for rid in ids {
                let forRetry = clientFlipped.contains(rid)
                group.addTask { [apiClient] in
                    do {
                        try await apiClient.request(
                            endpoint: .deleteReport(reportId: rid, forRetry: forRetry)
                        )
                        return (rid, .deleted)
                    } catch {
                        if case .apiError(let code, _) = AppError.from(error),
                           code == "REPORT_ALREADY_COMPLETED" {
                            return (rid, .completedInstead)
                        }
                        return (rid, .failed)
                    }
                }
            }
            for await (rid, outcome) in group {
                switch outcome {
                case .deleted:
                    // The list endpoint hides deleted rows, so nothing would ever clear
                    // this flag — and a non-empty set keeps the 5 s poll alive forever.
                    locallyTimedOutReportIds.remove(rid)
                case .failed:
                    failedIds.append(rid)
                    dismissedReportIds.remove(rid)   // allow the failed row to come back
                case .completedInstead:
                    // The server finished it: nothing was deleted or charged. Show it.
                    keptIds.append(rid)
                    dismissedReportIds.remove(rid)
                    locallyTimedOutReportIds.remove(rid)
                }
            }
        }

        // Every in-flight DELETE refunded server-side, and `creditBalance` was read BEFORE
        // that. `generateAnalysis` gates on the local number, so deleting a generating card
        // left the Generate button refusing ("Insufficient credits") work the server would
        // accept until the next completion or a pull-to-refresh — and read as "deleting it
        // lost the 20 credits". Unknown-then-reload, as `retryReport` does: nil never blocks.
        creditBalance = nil
        await loadCredits()

        if !failedIds.isEmpty || !keptIds.isEmpty {
            await loadReports()   // reconcile: rows that failed to delete (or finished) reappear
        }
        if !failedIds.isEmpty {
            let n = failedIds.count
            self.error = "Couldn't delete \(n) report\(n == 1 ? "" : "s"). Please try again."
        } else if !keptIds.isEmpty {
            let n = keptIds.count
            self.error = n == 1
                ? "That analysis had actually finished — it was kept, not deleted."
                : "\(n) analyses had actually finished — they were kept, not deleted."
        }
    }

    /// Regenerate a report the user was shown as failed.
    ///
    /// ⚠️ THE DELETE IS NOT COSMETIC — it is what stops a double charge.
    ///
    /// A card reaches `.failed` two ways. The backend may genuinely have failed it
    /// (already refunded, via the `is_refunded` CAS). Or `applyClientSideTimeoutPass`
    /// flipped it locally (`startedTimeoutSeconds` from `processing_started_at`, or
    /// `queuedTimeoutSeconds` from `created_at` for a row that never started) — and the
    /// queued arm can still fire on a report the server is working on: a queued report
    /// waits behind the 8-slot agent semaphore, and the reconciliation sweep does not
    /// consider a never-started row abandoned until `RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS`,
    /// which is DERIVED from the caps and is ~11,400s at current settings. Retrying in
    /// that window charged a second 20 credits for a report that was still coming, and
    /// the original was added to `dismissedReportIds`, so it completed into a list the
    /// user never saw it in.
    ///
    /// `DELETE /research/reports/{id}` resolves that: it claims the row through the
    /// same at-most-once `is_refunded` compare-and-set the sweep uses and refunds an
    /// in-flight report, while a genuinely-failed (already refunded) row is a plain
    /// soft-delete. Either way the user is made whole BEFORE being charged again, and
    /// the abandoned row cannot later complete unseen.
    func retryReport(_ report: AnalysisReport) {
        guard report.status == .failed else { return }
        // Same cap as generateAnalysis() — only block the retry (and the
        // pre-emptive dismissal of the failed card) when already AT the
        // concurrency cap; otherwise the card would vanish with no replacement.
        guard activeGenerationCount < maxConcurrentGenerations else {
            print("⚠️ ResearchVM: at concurrency cap, ignoring retry tap.")
            error = "You can run up to \(maxConcurrentGenerations) analyses at once — wait for one to finish."
            return
        }
        // The refund-independent guards `generateAnalysis()` runs, BEFORE the card is
        // dismissed and the row deleted (F09-9): a signed-out tap used to delete the
        // failed card first and then bounce off the sign-in gate, leaving neither report.
        guard AppActions.shared.isSignedIn else {
            AppActions.shared.requestSignIn(for: "retry this analysis")
            return
        }
        // Re-entry guard, taken BEFORE the Task: the F09-9 reorder moved the card's dismissal
        // behind an await, so the enabled button stayed on screen during the credits read
        // and a double tap ran the whole path twice (two DELETEs, two charges).
        if let id = report.backendId {
            guard !retryInFlightIds.contains(id) else {
                print("🔄 ResearchVM: retry for \(id) already in flight — ignoring the second tap")
                return
            }
            retryInFlightIds.insert(id)
        }
        print("🔄 ResearchVM: Retrying report for \(report.ticker)...")
        let ticker = report.ticker
        let persona = report.persona
        let backendId = report.backendId

        Task { [weak self] in
            guard let self else { return }
            defer { if let backendId { self.retryInFlightIds.remove(backendId) } }
            if backendId != nil, report.isRefunded {
                // An already-refunded row's DELETE is a plain soft-delete — no refund is
                // coming — so the balance guard can run up front: a fresh read below the
                // cost means the retry cannot start, and the failed card must stay. For an
                // UNREFUNDED row the DELETE is what refunds, so a pre-DELETE balance would
                // refuse a retry the refund is about to fund; that case keeps the
                // post-DELETE reload below and lets the backend's 402 be the authority.
                await self.loadCredits()
                if let balance = self.creditBalance, balance.credits < self.analysisCost.credits {
                    print("⚠️ ResearchVM: retry refused before the delete — insufficient credits (\(balance.credits) < \(self.analysisCost.credits))")
                    self.error = "Insufficient credits"
                    return
                }
            }
            // Drop the failed card now — both from the in-memory list and from the
            // dismiss-set so the next loadReports() doesn't re-surface it. The new
            // processing card will appear when generateAnalysis() spawns the next report.
            if let backendId {
                self.dismissedReportIds.insert(backendId)
                self.reports.removeAll { $0.backendId == backendId }
            }
            if let backendId {
                // A card THIS client flipped is failed only on its own clock; the server may
                // have finished it since. Ask before deleting — a completed row would be a
                // plain, unrefundable soft-delete followed by a second 20-credit charge.
                if self.locallyTimedOutReportIds.contains(backendId),
                   await self.serverSaysCompleted(backendId) {
                    print("🔄 ResearchVM: \(backendId) completed on the server — showing it instead of retrying")
                    self.adoptCompletedInsteadOfRetrying(backendId)
                    return
                }
                do {
                    try await self.apiClient.request(
                        endpoint: .deleteReport(reportId: backendId, forRetry: true)
                    )
                    print("🔄 ResearchVM: released prior report \(backendId) before retrying")
                    // Deleted rows never come back through the list, so the flag would
                    // otherwise outlive the card and keep the 5 s poll running for good.
                    // AFTER the delete, not before: the `contains` check above is what
                    // gates the completed pre-check.
                    self.locallyTimedOutReportIds.remove(backendId)
                } catch {
                    let appError = AppError.from(error)
                    // The server's belt for the race the status check above can lose: the
                    // report completed between the check and the delete. Nothing was
                    // deleted or charged; show the finished report.
                    if case .apiError(let code, _) = appError, code == "REPORT_ALREADY_COMPLETED" {
                        print("🔄 ResearchVM: \(backendId) already completed — retry refused by the server, adopting it")
                        self.adoptCompletedInsteadOfRetrying(backendId)
                        return
                    }
                    // Surface and STOP. Charging again while the original may still be
                    // live is the exact outcome this method exists to prevent, and a
                    // silent revert is banned on a user-initiated mutation.
                    print("❌ ResearchVM: retry aborted — could not release \(backendId): \(appError.message)")
                    self.dismissedReportIds.remove(backendId)
                    self.error = "Couldn't retry that analysis just yet. Please try again in a moment."
                    await self.loadReports()   // put the card back
                    return
                }
                // The DELETE just refunded the original (or a server-side failure already
                // had). `creditBalance` last read BEFORE that refund, and `generateAnalysis`
                // gates on it — so with 10 local / 30 server the retry was refused as
                // "Insufficient credits" AFTER the failed card had been deleted, leaving
                // neither report. Unknown-then-reload: nil never blocks (the backend's 402
                // is the authority), and the reload adopts the refund when it succeeds.
                self.creditBalance = nil
                await self.loadCredits()
            }
            // Set the target as late as possible so an await above cannot let the
            // user's own selection be overwritten by a stale one.
            //
            // Through `applyPrefilledTicker`, not a bare `searchText` write: the chip renders
            // `selectedTarget`, so retrying TSLA while a stale AAPL target sat there showed
            // AAPL and charged TSLA.
            self.applyPrefilledTicker(ticker)
            // Through `selectPersona`, not a bare assignment: retrying a report is the user
            // asking for THAT analyst again, so it must count as a manual pick and survive a
            // subsequent `applyDefaultPersona()`.
            self.selectPersona(persona)
            self.generateAnalysis()
        }
    }

    /// Whether the server reports this report as finished. Conservative: any failure to
    /// read is `false`, so the retry proceeds through the DELETE — whose retry-intent
    /// refusal is the second, race-free line of defence.
    private func serverSaysCompleted(_ backendId: String) async -> Bool {
        struct Status: Decodable { let status: String }
        do {
            let s: Status = try await apiClient.request(
                endpoint: .getResearchStatus(reportId: backendId),
                responseType: Status.self
            )
            return s.status.lowercased() == "completed"
        } catch {
            print("⚠️ ResearchVM: status pre-check for \(backendId) failed — \(AppError.from(error).message)")
            return false
        }
    }

    /// The retry target turned out to be a finished report: un-dismiss it, drop the
    /// local timeout flag and reload so the completed card appears where the failed one
    /// was. Nothing was deleted and nothing was charged.
    private func adoptCompletedInsteadOfRetrying(_ backendId: String) {
        dismissedReportIds.remove(backendId)
        locallyTimedOutReportIds.remove(backendId)
        Task { [weak self] in
            guard let self else { return }
            await self.loadReports()
            await self.loadCredits()
        }
    }

    func joinDiscussion() {
        print("Join discussion tapped")
    }

    func likeInsight(_ insight: CommunityInsight) {
        print("Liked insight from: \(insight.userName)")
    }

    func commentOnInsight(_ insight: CommunityInsight) {
        print("Comment on insight from: \(insight.userName)")
    }

    func shareInsight(_ insight: CommunityInsight) {
        print("Share insight from: \(insight.userName)")
    }

    // MARK: - Computed Properties
    var canGenerateAnalysis: Bool {
        !searchText.isEmpty && (creditBalance?.credits ?? analysisCost.credits) >= analysisCost.credits
    }

    /// Number of reports this session currently has in flight.
    var activeGenerationCount: Int { inFlightReportIds.count }

    /// True once the user hits the concurrency cap — the Generate button shows
    /// a spinner and can't start another until one finishes.
    var isAtConcurrencyCap: Bool { activeGenerationCount >= maxConcurrentGenerations }

    /// Gate for STARTING a new generation: under the cap, a ticker chosen, and
    /// enough credits for one more run.
    var canStartNewGeneration: Bool {
        activeGenerationCount < maxConcurrentGenerations && canGenerateAnalysis
    }

    var selectedPersonaDescription: String {
        selectedPersona.description
    }

    /// "Quality Style Analysis" — the STYLE word, not the last word of the display
    /// name (which reads "Compounder" / "Seeker" since the personas were renamed off
    /// real surnames). Matches AnalysisDescriptionCard.styleTitle.
    var analysisStyleTitle: String {
        "\(selectedPersona.shortName) Style Analysis"
    }
}
