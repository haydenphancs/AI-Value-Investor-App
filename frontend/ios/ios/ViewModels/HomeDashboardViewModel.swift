//
//  HomeDashboardViewModel.swift
//  ios
//
//  ViewModel for the redesigned Home dashboard — MVVM + Repository.
//
//  Owns data fetching via `HomeRepositoryProtocol` and exposes view-ready state.
//  Defaults to `MockHomeRepository` (UI only, no backend) but accepts any
//  conforming repository via init for testing / a future live implementation.
//  Matches the codebase convention: `ObservableObject` + `@Published`, with
//  boolean loading / error flags (see HomeViewModel).
//

import Foundation
import Combine
import OSLog

@MainActor
final class HomeDashboardViewModel: ObservableObject {

    // MARK: - Published state
    @Published private(set) var data: HomeDashboardData?
    @Published private(set) var isLoading: Bool = false
    @Published private(set) var errorMessage: String?

    /// True once a load has been ATTEMPTED (successfully or not).
    ///
    /// Feeds `showsFirstLoadSkeleton`. It used to gate a full-screen `LoadingOverlay` that
    /// dimmed the screen and swallowed every touch, the tab bar included (removed: the
    /// TestFlight 1.0 (9) "it loads so slow" screenshot was that overlay). Before the first
    /// attempt the skeleton renders IN the scroll content, so the header and tab bar stay
    /// live; after it, a failure is carried by the error banner.
    @Published private(set) var hasAttemptedLoad: Bool = false

    /// When the dashboard on screen was SAVED, while it is the on-device snapshot rather than a
    /// live answer (`HomeDashboardSnapshotStore`). nil once a live load lands, and whenever
    /// `data` is nil. Drives the "Updated <time>" pulse header — see `pulseHeader(for:)`.
    @Published private(set) var snapshotSavedAt: Date?

    /// The dashboard is empty because there is no account, not because the load broke.
    ///
    /// ⚠️ A SNAPSHOT taken during `performLoad()`, never a live read of auth — the same shape
    /// (and the same trap) as `ResearchViewModel.requiresSignInForReports`. Anything that
    /// changes the identity MUST re-run the load; `handleIdentityChange` and the
    /// `.task(id: isActiveTab)` in `HomeDashboardView` are what do that.
    @Published private(set) var requiresSignIn: Bool = false

    /// A credential is stored but not armed yet. Renders as "Reconnecting…", NEVER as the
    /// sign-in prompt: this user is signed in as far as they are concerned, and
    /// `AppState.requestSignIn` declines to prompt in this window anyway (auth.md §5).
    @Published private(set) var isReconnecting: Bool = false

    // MARK: - Dependencies
    private let repository: HomeRepositoryProtocol
    private let snapshotStore: HomeDashboardSnapshotStore

    nonisolated private static let log = Logger(subsystem: "com.phan.caydex", category: "home")

    /// When the last SUCCESSFUL load landed. Nil after a failure, so a screen
    /// that never got data retries on the next trigger instead of staying blank
    /// for the whole process lifetime.
    private var lastLoadedAt: Date?

    /// Auto-refresh loop. Home is opacity-mounted (it never leaves the view
    /// hierarchy), so `.task` fires exactly once per process — without this the
    /// strip showed launch-time prices and a launch-time "Pre-Market" label for
    /// the rest of the session.
    private var refreshTask: Task<Void, Never>?

    /// Matches the backend's pulse cache (now 60s, was 300s): anything fresher
    /// than this is served from that cache anyway, so re-fetching sooner buys
    /// nothing — and anything OLDER than it is data the backend would happily
    /// refresh, so holding it back just makes the strip look frozen.
    ///
    /// Keep this equal to `_CACHE_TTL_SECONDS` in `home_dashboard_service.py`.
    /// At 300s against a 60s server cache, arriving on the tab could show prices
    /// up to five minutes old under a live "Markets Open" header.
    /// `nonisolated` so it can be a default argument on `loadIfStale(maxAge:)`: a default
    /// argument expression is evaluated at the CALL SITE, which the compiler checks as
    /// nonisolated, and this type is `@MainActor`. Safe — an immutable `Sendable` literal
    /// with no isolated initializer. Same lever as `APIConfig.researchPollInterval`.
    nonisolated static let stalenessWindow: TimeInterval = 60

    /// Poll cadence. Deliberately shorter than `stalenessWindow` so the
    /// market-status header (recomputed fresh on every backend request, never
    /// cached) flips within a minute of an open/close boundary, while the tile
    /// prices still cost at most one upstream fan-out per 5 minutes.
    private static let refreshInterval: UInt64 = 60_000_000_000  // 60s

    deinit {
        refreshTask?.cancel()
        firstLoadRetryTask?.cancel()
    }

    // MARK: - First-load fast retry (state)

    /// When a load with nothing live on screen fails TRANSIENTLY (a dropped roaming flow, a 5xx,
    /// AUTH_UNAVAILABLE), retry it after these delays instead of waiting for the 60 s tick.
    /// Bounded: two retries, then the error banner and the tick take over.
    nonisolated static let firstLoadRetryDelays: [Duration] = [.seconds(2), .seconds(5)]

    /// A fast retry is scheduled, or is the load running now. The first load is still under way
    /// as far as the screen is concerned (`showsFirstLoadSkeleton`).
    @Published private(set) var isFirstLoadRetryPending: Bool = false

    /// The scheduled retry. A SEPARATE task calling `load()`, never a sleep inside
    /// `performLoad`: callers joining `loadTask` are never held through a backoff, and a
    /// pull-to-refresh runs at once.
    private var firstLoadRetryTask: Task<Void, Never>?

    /// Retries spent since the last live success or identity change.
    private var firstLoadRetryAttempt = 0

    /// Home stopped being the visible tab (`stopAutoRefresh`) and has not been shown since
    /// (`loadIfStale` runs on every activation). A load that fails meanwhile — one that was in
    /// flight when the tab was hidden, or a hidden reload (active group, entitlement) — schedules
    /// no retry: a screen nobody is looking at never retries.
    private var firstLoadRetrySuspended = false

    // MARK: - Init
    // Optional + nil-coalesce (matches the codebase's repository-injection idiom,
    // e.g. SearchViewModel) so the default LIVE repository is built inside the
    // @MainActor init rather than in a nonisolated default-argument context.
    // Pass `MockHomeRepository()` for offline previews / tests, and
    // `HomeDashboardSnapshotStore.inMemory(seed:)` to preview the snapshot state.
    init(repository: HomeRepositoryProtocol? = nil, snapshotStore: HomeDashboardSnapshotStore? = nil) {
        self.repository = repository ?? HomeRepository()
        self.snapshotStore = snapshotStore ?? .shared
        // The first frame shows the last dashboard this account saw, if it is fresh enough.
        // `AppState.configure` primed the store before the tab tree mounted.
        seedFromSnapshot()
    }

    // MARK: - On-device snapshot

    /// Put the bound account's saved dashboard on screen, if there is one inside the display
    /// window and nothing is on screen yet.
    ///
    /// ⚠️ Never stamps `lastLoadedAt`. The seed is not a load: `loadIfStale` must still fetch
    /// at once, and the live answer replaces this.
    private func seedFromSnapshot() {
        guard data == nil, let snapshot = snapshotStore.snapshotForDisplay() else { return }
        data = Self.presentedSnapshot(snapshot.data, savedAt: snapshot.savedAt, now: Date()) ?? snapshot.data
        snapshotSavedAt = snapshot.savedAt
    }

    /// A snapshot whose numbers describe an earlier US-market SESSION than a live answer would
    /// now, relabelled so nothing on it claims to be today's; nil when `data` needs no change
    /// (same session, or already relabelled).
    ///
    /// The movers card is mapped as "Today's Top Movers" (`HomeRepository.mapScanners`), so
    /// Friday's 4:02 PM snapshot painted on Monday at 10:00 ranked Friday's moves under
    /// "Today's" — for 1–2 s normally, for hours while `/home/dashboard` keeps failing. It
    /// becomes "Top Movers · Sep 25", dated by the session its numbers describe
    /// (`earlierMarketDay`), and the card drops its "#1 today". The other cards make no
    /// "today" claim. Live data is mapped afresh, so it restores the normal title on its own.
    private static func presentedSnapshot(
        _ data: HomeDashboardData, savedAt: Date, now: Date
    ) -> HomeDashboardData? {
        guard let day = earlierMarketDay(savedAt: savedAt, now: now) else { return nil }
        let title = earlierDayMoversTitle(day)
        guard data.scanners.contains(where: { $0.kind == .movers && $0.title != title }) else { return nil }
        // A COPY with only the scanners replaced, never a field-by-field rebuild: a rebuild had
        // to list every section, and dropping one (the watchlist; the defaulted Trillion Club)
        // still compiled and silently emptied it on every dated snapshot.
        var presented: HomeDashboardData = data
        presented.scanners = data.scanners.map { scanner in
            scanner.kind == .movers ? scanner.relabelled(title: title, asOfDayLabel: day) : scanner
        }
        return presented
    }

    /// "Sep 25" — the trading session the snapshot's numbers describe — when that differs from
    /// the session a live answer would describe `now`; nil when they are the same.
    ///
    /// The backend's rule, not the calendar's: the movers' day change rolls at the 09:30 ET
    /// OPEN, not at midnight (`MarketHoursUtil.numbersSessionDay`, the copy of
    /// `home_dashboard_service._numbers_session`). So a Monday 07:00 save holds Friday's moves
    /// and is dated "Sep 25" once Monday's session opens, and a Sunday save is dated by Friday's
    /// session, not by Sunday. Both instants are shifted back by the backend's grace
    /// (`_same_numbers_session`): an answer in the first minutes after the open may still
    /// carry pre-open numbers. ET, not the device's zone: the rankings belong to the US
    /// session, whatever zone the reader is roaming in. The month and day are joined by a
    /// narrow no-break space, so the date never splits across lines.
    nonisolated static func earlierMarketDay(savedAt: Date, now: Date = Date()) -> String? {
        let grace: TimeInterval = MarketHoursUtil.numbersSessionGraceSeconds
        let savedSession: Date = MarketHoursUtil.numbersSessionDay(at: savedAt.addingTimeInterval(-grace))
        let currentSession: Date = MarketHoursUtil.numbersSessionDay(at: now.addingTimeInterval(-grace))
        guard savedSession != currentSession else { return nil }
        let eastern = TimeZone(identifier: "America/New_York") ?? .current
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = eastern
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = calendar
        formatter.timeZone = eastern
        formatter.dateFormat = "MMM\u{202F}d"
        return formatter.string(from: savedSession)
    }

    /// "Top Movers · Sep 28". Never wider than "Today's Top Movers", the title `ScannerCard`'s
    /// two-line header is sized for (`lineLimit(2)`, the `fixedSize` toggle beside it): the
    /// narrow spaces around the dot are what keep the widest date (May 30) inside it. The
    /// break opportunity sits AFTER the dot (a thin space; the one before it is no-break), so a
    /// two-line title reads "Top Movers ·" / "Sep 28". Pinned by
    /// `test_ios_home_instant_paint_guards.py`, which measures every date.
    nonisolated static func earlierDayMoversTitle(_ day: String) -> String {
        "Top Movers\u{202F}\u{00B7}\u{2009}\(day)"
    }

    /// A new US-market session opened while a snapshot from an earlier one is still on screen
    /// (the app sat in the background or on another tab, or every live load since has failed):
    /// date its movers card now. Run on foreground, on tab activation (`loadIfStale`) and after
    /// a failed load. A no-op for live data, and for a snapshot already dated.
    func relabelSnapshotIfDayRolledOver(now: Date = Date()) {
        guard let savedAt = snapshotSavedAt, let shown = data,
              let relabelled = Self.presentedSnapshot(shown, savedAt: savedAt, now: now) else { return }
        data = relabelled
    }

    /// The dashboard on screen is the saved snapshot, not a live answer.
    var isShowingSnapshot: Bool { data != nil && snapshotSavedAt != nil }

    /// A live load has landed and is what is on screen.
    var hasLiveData: Bool { data != nil && snapshotSavedAt == nil }

    /// Show the first-load skeleton in the content (never over it).
    ///
    /// Only with nothing to show, never under the account gate (`requiresSignIn` /
    /// `isReconnecting` render their own state), and only during the FIRST load: before the
    /// first attempt (`handleIdentityChange` resets it for a new account), and while a fast
    /// retry is scheduled or running. Deliberately NOT on a bare `isLoading`: during an outage
    /// with nothing on screen, every 60 s poll would re-raise the skeleton over the error banner
    /// — once the retries are spent, the banner is the honest state and stays put.
    var showsFirstLoadSkeleton: Bool {
        data == nil && !requiresSignIn && !isReconnecting && (!hasAttemptedLoad || isFirstLoadRetryPending)
    }

    /// The Market Pulse header. While a snapshot is on screen the server's "Markets Open" is
    /// NOT shown — it described the moment of the save, not now — and the dot goes muted
    /// (`isOpen: false`); "Updated <time>" says what the numbers are instead. Live data gets the
    /// server's status back.
    func pulseHeader(for data: HomeDashboardData) -> (text: String, isOpen: Bool) {
        if let savedAt = snapshotSavedAt {
            return (Self.snapshotStatusText(savedAt: savedAt), false)
        }
        return (data.marketStatusText, data.marketIsOpen)
    }

    /// "Updated 4:02 PM" for a save today, "Updated Sep 28, 4:02 PM" otherwise — an absolute
    /// time, so the label never goes stale on screen. Same "Updated …" wording as the Updates
    /// tab's insight cards.
    nonisolated static func snapshotStatusText(
        savedAt: Date, now: Date = Date(), calendar: Calendar = .current
    ) -> String {
        let time = savedAt.formatted(date: .omitted, time: .shortened)
        if calendar.isDate(savedAt, inSameDayAs: now) {
            return "Updated \(time)"
        }
        let day = savedAt.formatted(.dateTime.month(.abbreviated).day())
        return "Updated \(day), \(time)"
    }

    /// Drop an on-screen snapshot that has aged past the display window (the app sat in the
    /// background, or every live load since launch failed). Live data is never touched.
    func expireSnapshotIfStale(now: Date = Date()) {
        guard let savedAt = snapshotSavedAt, data != nil,
              !HomeDashboardSnapshotStore.isDisplayable(savedAt: savedAt, now: now) else { return }
        data = nil
        snapshotSavedAt = nil
    }

    // MARK: - Loading

    // NOTE: the old `loadIfNeeded()` (guard on `data == nil`) was deliberately
    // REMOVED rather than kept alongside `loadIfStale`. "Load once and never
    // again" is the exact bug this screen had — Home is opacity-mounted, so that
    // guard froze the prices and the market-status header for the whole process.
    // Don't reintroduce it; use `loadIfStale` from the tab/foreground triggers.

    /// Re-fetch only when the data is older than `maxAge`.
    ///
    /// Used by the tab-activation and foreground triggers, which can fire far
    /// more often than the data actually changes. A failed load leaves
    /// `lastLoadedAt` nil, so a screen that is still blank always retries.
    func loadIfStale(maxAge: TimeInterval = HomeDashboardViewModel.stalenessWindow) async {
        // Both callers are VISIBLE triggers (tab activation; foreground while Home is the tab),
        // so this is where a tab hidden by `stopAutoRefresh` may fast-retry again.
        firstLoadRetrySuspended = false
        // Switching back to Home after the open (no background trip, so no `didBecomeActive`):
        // a snapshot still on screen is bounded and dated now, not only once this load fails.
        expireSnapshotIfStale()
        relabelSnapshotIfDayRolledOver()
        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }
        await load()
    }

    /// The signed-in identity changed — drop this dashboard and fetch the new one.
    ///
    /// `loadIfStale()` cannot serve this. It is keyed on AGE, and the data is not old, it
    /// belongs to somebody else: a load made as a guest stamps `lastLoadedAt` like any other,
    /// so signing in left the guest's watchlist on screen for up to 5 minutes. In the sign-out
    /// direction it is worse — the next person to open the app would see the previous
    /// account's watchlist and holdings.
    ///
    /// Clearing before the fetch is deliberate: the reload can be slow or fail, and stale
    /// account data must not sit on screen while it does.
    func handleIdentityChange(isActiveTab: Bool) async {
        // CLEAR FIRST, UNCONDITIONALLY — before the `isActiveTab` gate below. The reload is
        // deferred for a hidden tab, but the previous account's data must not survive in this
        // ViewModel waiting to be rendered (.claude/rules/auth.md §7).
        data = nil
        snapshotSavedAt = nil
        lastLoadedAt = nil
        errorMessage = nil
        // The previous identity's fast retry must not fire into the new one, and the new
        // identity gets its own first load: a full retry budget and, with no snapshot, the
        // skeleton rather than a blank page while it loads.
        cancelFirstLoadRetry(resetBudget: true)
        hasAttemptedLoad = false
        // Cleared with the rest, ABOVE the gate: a latched "sign in" left over from a load
        // that raced session restore is exactly the state this reload exists to heal.
        requiresSignIn = false
        isReconnecting = false
        // Then the NEW identity's own snapshot, above the gate too, so a hidden tab is ready
        // when it is opened. Only ever the current owner's: AppState re-binds or clears the
        // store (`applyProfile`, `discardDataForEndedSession`) before it publishes the status
        // change that runs this. On a heal from `.restoring` that bumped the generation, Home
        // keeps a labelled dashboard instead of blanking a second time.
        seedFromSnapshot()

        // Fetch only if the user is actually looking at this tab. Clearing above nils the
        // freshness stamp, so `.task(id: isActiveTab)` re-loads on the next activation.
        guard isActiveTab else { return }
        await load()
    }

    /// The load currently in flight, if any. Concurrent callers JOIN it.
    ///
    /// Five triggers point at this one ViewModel — tab activation, `didBecomeActive`, the
    /// tier `onChange`, the identity-change reload and the active-group notification — and
    /// on a signed-in cold launch at least three of them fire within the same instant. The
    /// `lastLoadedAt` window cannot collapse them, because it is only stamped once a load
    /// COMPLETES, so every trigger that arrives while the first is still in flight saw a nil
    /// timestamp and issued its own request. That is why one launch fetched
    /// `/home/dashboard` three times.
    private var loadTask: Task<Void, Never>?

    /// A watchlist row was added or removed elsewhere (a detail-screen star, Tracking,
    /// Updates). Visible tab → reload ORDERED BEHIND any load already running: `load()`
    /// joins a running load, and one started before the write (the 60 s tick keeps them
    /// going while a detail cover sits over Home) would hand back the pre-toggle list.
    /// Hidden tab → just void the freshness stamp so `loadIfStale` fetches on the next
    /// activation, instead of spending a dashboard fetch nobody is looking at.
    func reloadForWatchlistChange(isActiveTab: Bool) async {
        guard isActiveTab else {
            lastLoadedAt = nil
            return
        }
        if let running = loadTask, !running.isCancelled {
            await running.value
        }
        await load()
    }

    func load() async {
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
            self.loadTask = nil
        }
        loadTask = task
        await task.value
    }

    private func performLoad() async {
        isLoading = true
        errorMessage = nil
        // Captured BEFORE the request. If the account changes while it is in flight, the store
        // has moved on and refuses to save this answer under the next owner.
        let snapshotEpoch = snapshotStore.epoch

        // THREE outcomes, not two — decided from the OUTCOME of the request, never from a
        // pre-flight read of `auth.status`.
        //
        // `GET /home/dashboard` is `.signInRequired`, so an unarmed caller is refused by
        // `APIClient.buildRequest` before any network I/O, and the refusal arrives TYPED as
        // `AppError.signInRequired`. This ViewModel used to flatten that to its `.message` —
        // the generic "Sign in to use this feature." — and paste it into the NETWORK-error
        // banner. That is the TestFlight screenshot: an orange wifi-exclamation line over a
        // blank page, with nothing to tap, shown to a signed-in user mid-restore.
        //
        // ⚠️ Why not `guard AppActions.shared.isSignedIn` up front, as `ResearchViewModel`
        // does: `isSignedIn` is `status == .authenticated`, and on EVERY signed-in cold launch
        // `primeStoredCredential` arms the token while the status still reads `.restoring`
        // (AppState documents that ordering as load-bearing). Home is the tab on screen at
        // launch, so that guard refused a request that would have succeeded and showed
        // "Reconnecting…" instead of the dashboard — the adversarial review measured it, and
        // the session-healed reload in `HomeDashboardView` was only hiding it. The refusal is
        // APIClient's own answer to "is a token armed?"; asking anything else is a guess.
        do {
            let fetched = try await repository.fetchHomeDashboard()
            data = fetched.data
            snapshotSavedAt = nil
            lastLoadedAt = Date()
            requiresSignIn = false
            isReconnecting = false
            // Only a SUCCESSFUL live load is ever persisted, fenced on the epoch above; the
            // store also refuses a degraded dashboard (`isWorthPersisting`).
            if let body = fetched.body {
                snapshotStore.save(body: body, data: fetched.data, epoch: snapshotEpoch)
            }
            // Live data is on screen: any pending fast retry is moot, and the next first load
            // (after an identity change) gets the full budget again.
            cancelFirstLoadRetry(resetBudget: true)
        } catch {
            // Route through AppError like every other surface — a single
            // hardcoded string can't tell "you're offline" from "you're signed
            // out" from "we're rate-limited", and the user gets no actionable
            // hint. Existing data is deliberately kept on screen (stale beats
            // blank); only the banner changes.
            let appError = AppError.from(error)
            // Signed out and broken must not look alike: one wants a Sign In button, the other
            // a retry, and a network-error banner over an auth refusal is the defect this whole
            // pass is about. A refused load also drops `data` — nothing on screen can be
            // refreshed while the token is unarmed. `lastLoadedAt` is deliberately NOT stamped
            // here, or the staleness window would suppress the reload that heals the gate.
            if case .signInRequired = appError {
                data = nil
                snapshotSavedAt = nil
                let reconnecting = AppActions.shared.isRestoringSession
                isReconnecting = reconnecting
                requiresSignIn = !reconnecting
                // The designed path while no credential is armed — info, not a failure.
                Self.log.info("home: dashboard load refused before sending — no armed credential (reconnecting: \(reconnecting, privacy: .public))")
                // Never fast-retried: a refusal heals through the session (the auth-status and
                // identity triggers in HomeDashboardView), not through the clock.
                cancelFirstLoadRetry(resetBudget: false)
            } else {
                errorMessage = appError.message
                requiresSignIn = false
                isReconnecting = false
                // A kept snapshot is still bounded by its display window, and is dated once a
                // later US-market session than the one its numbers describe has opened.
                expireSnapshotIfStale()
                relabelSnapshotIfDayRolledOver()
                // Release-visible: a load that fails on users' phones must be diagnosable from
                // logs alone.
                let detail = "\(type(of: error)): \(error)"
                Self.log.warning("home: dashboard load failed — \(detail, privacy: .public)")
                // The ONLY place a fast retry is scheduled: a non-auth failure.
                scheduleFirstLoadRetryIfNeeded(after: appError, underlying: error)
            }
        }
        isLoading = false
        hasAttemptedLoad = true
    }

    /// Pull-to-refresh — always re-fetches, regardless of staleness.
    func refresh() async {
        await load()
    }

    // MARK: - Auto refresh

    /// Start (or restart) the polling loop. Safe to call repeatedly; the previous
    /// loop is cancelled first. Call `stopAutoRefresh()` when Home stops being the
    /// active tab so a hidden screen isn't polling in the background.
    func startAutoRefresh() {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: Self.refreshInterval)
                guard !Task.isCancelled, let self else { break }
                // Cheap when nothing has aged out: the backend's 5-minute pulse
                // cache absorbs it, and the market-status header is recomputed
                // fresh server-side on every request.
                await self.load()
            }
        }
    }

    func stopAutoRefresh() {
        refreshTask?.cancel()
        refreshTask = nil
        // Hidden: no fast retry fires, and none is scheduled until Home is shown again
        // (`loadIfStale`). The budget is kept — it belongs to this first load, not to a tab visit.
        firstLoadRetrySuspended = true
        cancelFirstLoadRetry(resetBudget: false)
    }

    // MARK: - First-load fast retry

    /// A failure the next attempt, seconds from now, can plausibly heal. Mirrors
    /// `TaskPollingManager.isTransientPollFailure`, with two deliberate differences:
    ///   • `.rateLimited` is NOT retried — a 429 carries Retry-After, and the 60 s tick honours
    ///     it; a fast retry would spend the caller's budget against the limiter;
    ///   • `.signInRequired` is NOT retried — a refusal heals through the session, never the
    ///     clock (and never reaches here: `performLoad` handles it first).
    /// Never: cancellation, any auth failure, a 4xx business outcome or a decode drift (all of
    /// them fail identically on every attempt).
    ///
    /// `.unknown` is BOTH a transport failure `URLError` could not name (TLS, DNS, roaming —
    /// `APIError.networkError`) and a 4xx / unreadable body, so the raw error decides.
    nonisolated static func isTransientFirstLoadFailure(_ appError: AppError, underlying error: Error) -> Bool {
        switch appError {
        case .noConnection, .timeout, .serverError, .authUnavailable:
            return true
        case .unknown:
            if let apiError = error as? APIError, case .networkError = apiError {
                return true
            }
            return false
        default:
            return false
        }
    }

    /// Schedule the next fast retry, if this failure and this state warrant one. Otherwise
    /// the first-load sequence is over: the error banner (or what is on screen) is the honest
    /// state, and the 60 s tick takes it from here.
    private func scheduleFirstLoadRetryIfNeeded(after appError: AppError, underlying error: Error) {
        guard !firstLoadRetrySuspended,
              !hasLiveData,
              Self.isTransientFirstLoadFailure(appError, underlying: error),
              firstLoadRetryAttempt < Self.firstLoadRetryDelays.count else {
            // Also drops a retry still sleeping from an earlier failure: this answer is what it
            // would have retried into.
            cancelFirstLoadRetry(resetBudget: false)
            return
        }
        let delay = Self.firstLoadRetryDelays[firstLoadRetryAttempt]
        firstLoadRetryAttempt += 1
        let attempt = firstLoadRetryAttempt
        let budget = Self.firstLoadRetryDelays.count
        let code = appError.analyticsCode
        firstLoadRetryTask?.cancel()
        isFirstLoadRetryPending = true
        Self.log.info("home: first load failed transiently (\(code, privacy: .public)) — fast retry \(attempt, privacy: .public)/\(budget, privacy: .public) in \(delay.components.seconds, privacy: .public)s")
        firstLoadRetryTask = Task { [weak self] in
            try? await Task.sleep(for: delay)
            guard !Task.isCancelled, let self else { return }
            // `isFirstLoadRetryPending` stays set through this load; its outcome settles it.
            await self.load()
        }
    }

    /// Drop a scheduled retry. `resetBudget` only where a NEW first load begins (a live
    /// success, an identity change) — hiding the tab or a refusal keeps what was spent.
    private func cancelFirstLoadRetry(resetBudget: Bool) {
        firstLoadRetryTask?.cancel()
        firstLoadRetryTask = nil
        isFirstLoadRetryPending = false
        if resetBudget { firstLoadRetryAttempt = 0 }
    }
}
