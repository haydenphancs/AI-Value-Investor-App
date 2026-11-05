//
//  PortfolioStore.swift
//  ios
//
//  Server-backed observable store for the named portfolios that group tickers
//  on the Tracking screen. The list of portfolios, their ticker membership,
//  and per-portfolio holding values (shares / market_value) live on the
//  backend — INCLUDING which group is active (migration 126). That selection used
//  to be a device-local UserDefaults string, which meant the server could not build
//  Home's watchlist section or the Updates ticker chips from it, so those two
//  screens never followed the group the user picked and the choice did not sync
//  across devices. The UserDefaults key survives only as a cold-start hint for the
//  window before the first successful `GET /portfolios`.
//
//  Mutation strategy:
//    - Portfolio CRUD (create / rename / delete / reorder) talks to the server
//      first, then updates @Published state on success.
//    - Ticker membership mutations (add / remove / reorder) update @Published
//      state optimistically for instant feedback, then sync to the server. The
//      server payload is the full ordered ticker list (not a diff). A
//      portfolio's writes go out ONE AT A TIME (`enqueueMembershipWrite`), each
//      with the list its own edit produced; only the newest answer replaces
//      what is on screen, and a failure reverts to the last CONFIRMED list
//      only when no newer write is queued. The server preserves per-item
//      holdings for tickers that survive the swap.
//    - Per-portfolio holdings (shares / market_value) are pushed via
//      `setHoldings(_:in:)`, in the same per-portfolio queue; the response's
//      holdings are merged into the local items by ticker.
//

import Foundation
import Combine
import SwiftUI
import OSLog

nonisolated private let portfolioStoreLog = Logger(subsystem: "com.phan.caydex", category: "portfolio-store")

@MainActor
final class PortfolioStore: ObservableObject {
    // MARK: - Singleton

    static let shared = PortfolioStore()

    // MARK: - Published state

    @Published private(set) var portfolios: [Portfolio] = []
    @Published private(set) var activePortfolioId: String?
    @Published private(set) var isLoading: Bool = false
    /// "A load was ATTEMPTED for this session" — latched in `performLoad`'s defer even when the
    /// load failed, so it can never mean "the store holds usable data". `hasLiveData` does.
    @Published private(set) var hasLoadedOnce: Bool = false

    /// True only once a `GET /portfolios` answer was PUBLISHED for the current session in THIS
    /// process. The data-loss guard: every whole-list PUT (`purgeTickers` → `setTickers`) and
    /// every edit entry point on Tracking requires it, so nothing is ever written back from a
    /// list the server did not just hand us. Reset with the session (`reset()`), never by a
    /// later failed load (the published rows stay live).
    @Published private(set) var hasLiveData: Bool = false

    /// User-facing copy (mapped through `AppError`) for the LAST load's failure, nil on success,
    /// on a typed sign-in refusal (the Tracking feed's account gate speaks for the screen) and on
    /// a cancellation. Without it a failed `/portfolios` with a good feed rendered a silent
    /// "No tickers yet" — a false statement about the user's own holdings.
    @Published private(set) var loadErrorMessage: String?

    /// The exact bytes of the last `GET /portfolios` answer this store PUBLISHED, for the
    /// Tracking on-device snapshot (`TrackingSnapshot`). nil until a live load lands and after
    /// `reset()`. Read only right after the load that set it; never a source of rows.
    private(set) var lastLiveBody: Data?

    /// Bumped after EVERY server-confirmed write — membership (`syncTickers`, which the sheets'
    /// `setTickers` / `removeTicker` and the search star's `addTicker` go through), holdings,
    /// create, rename, delete, reorder and the active-group switch. `TrackingViewModel` purges
    /// the saved Holdings snapshot on it, so the next cold launch never paints a group, a
    /// membership or a score the user has since changed. A counter rather than a notification
    /// so a subscriber reads it like any other published state.
    @Published private(set) var confirmedMutationCount: Int = 0

    // MARK: - Private

    private let apiClient: APIClient
    private static let activeIdKey = "TrackingView.activePortfolioId"

    /// The cold-start hint: the group whose insights load before `GET /portfolios` answers.
    /// Only a UUID can be a group id (`portfolios.id` is `uuid`), so anything else is dropped
    /// here instead of sent. The server cast it and answered 500 (22P02).
    private static func readActiveIdHint() -> String? {
        guard let stored = UserDefaults.standard.string(forKey: activeIdKey) else { return nil }
        guard UUID(uuidString: stored) != nil else {
            portfolioStoreLog.warning("dropping a non-UUID active-group hint")
            UserDefaults.standard.removeObject(forKey: activeIdKey)
            return nil
        }
        return stored
    }

    /// Every write of the hint goes through here (nil removes it).
    private static func storeActiveIdHint(_ id: String?) {
        #if DEBUG
        // Store-screenshot mode answers GET /portfolios with a sample group whose id is
        // "store-shot-sample". Saving it put that id in the REAL hint, and the next normal
        // launch sent it to production: GET /portfolios/store-shot-sample/insights → 500
        // (Sentry, 2026-10-08). The mode never WRITES the hint; the session-end removal
        // (nil, `reset()`) still runs, since a session's device-global keys must not
        // outlive it (auth.md §7).
        if StoreScreenshotMode.isOn, id != nil { return }
        #endif
        if let id {
            UserDefaults.standard.set(id, forKey: activeIdKey)
        } else {
            UserDefaults.standard.removeObject(forKey: activeIdKey)
        }
    }

    /// Posted whenever the ACTIVE GROUP changes identity or name.
    ///
    /// Home and Updates now build their content server-side from `portfolios.is_active`,
    /// but both cache aggressively and neither observes this store: Updates latches
    /// `hasLoadedOnce` and never re-reads its tabs for the rest of the process, and Home's
    /// `loadIfStale` no-ops inside a 300s window. Without a signal, switching groups on
    /// Tracking left Home showing the OLD group's name over the OLD group's tiles and
    /// Updates showing the old group's chips — an affirmatively wrong label, not just
    /// stale prices, since the header stopped being a hardcoded literal.
    ///
    /// A notification rather than shared state because the two screens own independent
    /// ViewModels with their own lifecycles, and `PortfolioStore` is deliberately not in
    /// `AppState`.
    static let activeGroupDidChangeNotification = Notification.Name(
        "PortfolioStore.activeGroupDidChange"
    )

    private func announceActiveGroupChange() {
        NotificationCenter.default.post(
            name: Self.activeGroupDidChangeNotification, object: nil
        )
    }

    /// Posted once the SERVER has confirmed a membership write to the ACTIVE group — every
    /// add, remove, reorder and purge (`syncTickers`): the Manage Tickers drag and minus, Edit
    /// Portfolio's move / remove, the Holdings swipe, the orphan purge. None of those posts
    /// `watchlistDidChangeNotification` or `activeGroupDidChangeNotification`, so the Updates
    /// chips (whose Custom order IS the group's stored order) and Home's strip kept the old
    /// order and membership. `userInfo[membershipChangedKey]` says whether the set of tickers
    /// changed (a pure reorder leaves the diversification score alone). Post-confirm for the
    /// reason given below.
    static let activeGroupTickersDidChangeNotification = Notification.Name(
        "PortfolioStore.activeGroupTickersDidChange"
    )

    /// Posted once the SERVER has confirmed a watchlist add or remove — the star on the
    /// five detail screens, Updates › Manage Assets, and Tracking's own search star / swipe
    /// removal. Every subscriber that already reloads itself after its OWN mutation ignores
    /// its own `source` (Tracking ignores `.tracking`, Updates `.updates`).
    ///
    /// Why a post-CONFIRM signal and not one fired on the optimistic flip: the receiver's
    /// answer is a refetch, and a refetch keyed to the flip can overtake the POST/DELETE
    /// still in flight, read the PRE-toggle state and revert the very change it was
    /// meant to show (`WhaleService.followsDidChangeNotification` documents the same race
    /// for follows). So the five stars post inside the request task's success branch.
    ///
    /// The TestFlight report behind it: unstar NVDA on its detail screen, pop back, and
    /// the row was still in the Tracking list until a pull-to-refresh — nothing told
    /// `TrackingViewModel`, whose load is latched on `hasLoadedOnce`, and Home / Updates
    /// were stale for 60 s / the whole process respectively.
    static let watchlistDidChangeNotification = Notification.Name(
        "PortfolioStore.watchlistDidChange"
    )

    /// Post `watchlistDidChangeNotification` with a `WatchlistChange` payload.
    ///
    /// `ticker` must be the STORED spelling — the pair form (`BTCUSD`) for a coin, the
    /// raw symbol otherwise — because the receivers match it against feed rows and
    /// portfolio membership, both of which hold the stored form; a bare `BTC` would
    /// match nothing (and names a different asset, the Grayscale ETF).
    static func announceWatchlistChange(
        ticker: String, assetType: String, added: Bool, source: WatchlistChange.Source
    ) {
        let change = WatchlistChange(
            ticker: ticker.uppercased(), assetType: assetType, added: added, source: source
        )
        NotificationCenter.default.post(
            name: watchlistDidChangeNotification, object: nil,
            userInfo: [WatchlistChange.userInfoKey: change]
        )
    }

    var activePortfolio: Portfolio? {
        portfolios.first { $0.id == activePortfolioId }
    }

    private init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        self.activePortfolioId = Self.readActiveIdHint()
    }

    // MARK: - Loading

    /// The load currently in flight, if any. Concurrent callers JOIN it.
    ///
    /// This is a SINGLETON read by several unrelated triggers, and `isLoading` /
    /// `hasLoadedOnce` were published but never consulted here — so a launch that fired
    /// `TrackingViewModel.loadData()` and the identity-change reload issued two identical
    /// `GET /portfolios` calls whose responses then raced to assign `self.portfolios`.
    private var loadTask: Task<Bool, Never>?

    /// Load (or join the running load of) `GET /portfolios`.
    ///
    /// - Returns: true only when THIS load (or the one joined) published a live answer for the
    ///   current session. Callers that write anything back — the Tracking purge, the default
    ///   group the search star may create — act only on `true`.
    @discardableResult
    func loadPortfolios() async -> Bool {
        if let running = loadTask, !running.isCancelled {
            return await running.value
        }
        let task = Task { [weak self] () -> Bool in
            guard let self else { return false }
            let published = await self.performLoad()
            // Cleared HERE, as the task's own last act, rather than after `await
            // task.value` below. Between a task finishing and its awaiting caller
            // being resumed, a THIRD caller can run and observe a completed-but-still
            // -registered task: it would "join" something already done and return
            // instantly without ever loading. That is a silently skipped refresh —
            // and for `handleIdentityChange` it would mean adopting a load that
            // completed under the PREVIOUS identity.
            //
            // Not when cancelled: `reset()` cancels this task and clears the slot itself, and a
            // newer load may already be registered there by the time this one unwinds.
            if !Task.isCancelled { self.loadTask = nil }
            return published
        }
        loadTask = task
        return await task.value
    }

    /// Bumped by `reset()`. A load that started under the previous session refuses to
    /// publish, because cancellation cannot un-finish a request that already resolved.
    private var identityEpoch = 0

    private func performLoad() async -> Bool {
        // Captured BEFORE the request. `reset()` bumps it, so a load issued under the
        // previous session cannot publish that session's rows after sign-out.
        let epoch = identityEpoch
        // Also captured BEFORE the request: a membership write queued after this point may not
        // be in the answer (see `keepingLocalMembership`).
        let revisionsAtRequest = writeSequencer.revisionSnapshot()
        isLoading = true
        defer {
            isLoading = false
            // Only claim "loaded" for the session that actually asked. Otherwise a
            // superseded load marks the NEW session as loaded and its first real refresh
            // is skipped.
            if epoch == identityEpoch { hasLoadedOnce = true }
        }

        do {
            // `requestReturningBody`: the same transport, refusal gate, 401 refresh and GET-only
            // 5xx retry as `request<T>`, plus the exact bytes for the Tracking snapshot.
            let (response, body) = try await apiClient.requestReturningBody(
                endpoint: .getPortfolios,
                responseType: PortfolioListResponseDTO.self
            )
            // The sign-out may have landed while this was in flight. The request went out
            // with the still-armed bearer, so it returns the EX-USER's portfolios —
            // publishing them refills the store `reset()` just cleared and re-writes their
            // active id to the device-global UserDefaults key, which is precisely the bleed
            // `reset()` exists to stop.
            guard epoch == identityEpoch else {
                print("[PortfolioStore] ↩︎ discarding a load from an ended session")
                return false
            }
            let fetched = response.portfolios
                .map { $0.toPortfolio() }
                .sorted { $0.sortOrder < $1.sortOrder }
            let loaded = keepingLocalMembership(over: fetched, revisionsAtRequest: revisionsAtRequest)
            self.portfolios = loaded
            // The SERVER decides which group is active (migration 126). Home and the
            // Updates chips read it straight from the database, so if this device kept
            // its own opinion the three surfaces would disagree — which is the entire
            // class of bug this change exists to remove. The local key is now only a
            // cold-start hint for the window before the first successful load.
            if let serverActive = loaded.first(where: \.isActive) {
                activePortfolioId = serverActive.id
                Self.storeActiveIdHint(serverActive.id)
            } else {
                ensureActiveSelection()
            }
            // Only AFTER the rows above are published: this is the live signal every
            // write-back path keys on.
            lastLiveBody = body
            loadErrorMessage = nil
            hasLiveData = true
            print("[PortfolioStore] ✅ Loaded \(loaded.count) portfolios from API")
            return true
        } catch {
            guard epoch == identityEpoch else { return false }
            let appError = AppError.from(error)
            // Nobody is waiting for a cancelled load; it says nothing about the server.
            if appError.isCancellation { return false }
            if case .signInRequired = appError {
                // A refusal before any I/O: the Tracking feed's own refusal raises the account
                // gate for the screen, and a "Couldn't load" over it would offer a Retry the
                // client refuses before it leaves the device.
                loadErrorMessage = nil
                return false
            }
            // Never a silent empty store: the screen renders this instead of "No tickers yet".
            // The rows already published (if any) stay live — only the message changes.
            loadErrorMessage = appError.message
            let detail = "\(type(of: error)): \(error)"
            portfolioStoreLog.warning("GET /portfolios failed — \(detail, privacy: .public)")
            return false
        }
    }

    /// `fetched` with the LOCAL membership kept for every group that had a write queued, in
    /// flight, or finished while the load was out, and the confirmed membership re-seeded for
    /// every other group.
    ///
    /// A `GET /portfolios` issued before a `PUT /tickers` can answer after it. Publishing its
    /// items as-is put the OLD order back on screen — the very "my reorder did nothing" this
    /// screen was reported for — and the next whole-list write would then have sent that old
    /// list to the server. The local items of such a group are the newest confirmed or still
    /// optimistic state, and its own write settles them.
    private func keepingLocalMembership(
        over fetched: [Portfolio], revisionsAtRequest: [String: Int]
    ) -> [Portfolio] {
        let local: [String: Portfolio] = Dictionary(
            portfolios.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        var seeded: [String: [PortfolioItem]] = [:]
        let merged: [Portfolio] = fetched.map { server in
            let written = writeSequencer.hasPending(server.id)
                || writeSequencer.revision(server.id) != (revisionsAtRequest[server.id] ?? 0)
            guard written, let mine = local[server.id] else {
                seeded[server.id] = server.items
                return server
            }
            var kept = server
            kept.items = mine.items
            if let confirmed = confirmedItems[server.id] { seeded[server.id] = confirmed }
            return kept
        }
        // Groups the server no longer has take their confirmed lists with them.
        confirmedItems = seeded
        return merged
    }

    // MARK: - Active selection

    /// Switch the active group, optimistically and then on the server.
    ///
    /// The switch has to reach the backend: Home's watchlist section and the Updates
    /// ticker chips are built server-side from `portfolios.is_active`, so a local-only
    /// change would move the Tracking tab and leave the other two showing the previous
    /// group. It also makes the selection follow the user across devices.
    ///
    /// Optimistic in memory, reverted on failure, and never silent — a user-initiated
    /// mutation that fails must say so (auth.md §6). Note what is NOT done here: the
    /// UserDefaults write is deferred until the server confirms, because persisting an
    /// optimistic value would make a switch the server never received survive a kill.
    func setActivePortfolio(_ id: String) async {
        guard portfolios.contains(where: { $0.id == id }) else { return }
        guard id != activePortfolioId else { return }

        // Epoch, as in `load()`: a switch that resolves after `reset()` must not write the
        // ended session's id into UserDefaults (device-global, auth.md §7) or revert the
        // next account's active flag to it.
        let epoch = identityEpoch
        let previous = activePortfolioId
        activePortfolioId = id
        applyActiveFlag(id)

        do {
            _ = try await apiClient.request(
                endpoint: .activatePortfolio(id: id),
                responseType: PortfolioDTO.self
            )
            guard epoch == identityEpoch else { return }
            Self.storeActiveIdHint(id)
            confirmedMutationCount &+= 1
            // Only AFTER the server confirms. Announcing optimistically would make Home
            // and Updates re-fetch against the OLD server state and cache the old group
            // again, right before the revert below puts the UI back.
            announceActiveGroupChange()
        } catch {
            guard epoch == identityEpoch else { return }
            activePortfolioId = previous
            applyActiveFlag(previous)
            AppActions.shared.reportMutationFailure(
                error, action: "switch to that list"
            )
        }
    }

    /// Keep the in-memory `isActive` flags consistent with `activePortfolioId`.
    /// Exactly one row is active, so this clears every other one.
    private func applyActiveFlag(_ id: String?) {
        for index in portfolios.indices {
            portfolios[index].isActive = (portfolios[index].id == id)
        }
    }

    /// If the persisted active id is gone (deleted on another device, first
    /// launch, etc.) fall back to the first portfolio so the UI always has a
    /// valid selection.
    /// Drop the previous session's portfolios. Called from `AppState.discardDataForEndedSession`.
    ///
    /// `portfolios` is in-memory and `activePortfolioId` is a device-global UserDefaults key, so
    /// neither was cleared when a session ended. `loadPortfolios()` degrades by keeping whatever
    /// it already has, so the next account signing in on this phone — with the backend
    /// unreachable, or just before its first successful load — saw the PREVIOUS user's portfolio
    /// names in the picker, and their ticker membership driving the filtered asset and alert
    /// lists. Same class as the Learn stores and followed whales; same funnel.
    func reset() {
        // Bump FIRST and cancel, so an in-flight load fails its epoch check below rather
        // than refilling the store after this clears it. Cancellation alone is not enough:
        // a task already past its await still runs its completion branch.
        identityEpoch &+= 1
        loadTask?.cancel()
        loadTask = nil
        // The write chain belongs to the ended session. Its links check the epoch above and
        // resolve silently; the next session's first write must not wait behind them.
        writeSequencer.reset()
        writeTails = [:]
        confirmedItems = [:]
        unannouncedChanges = [:]
        portfolios = []
        activePortfolioId = nil
        hasLoadedOnce = false
        // The next session has proven nothing yet: no write-back until ITS first live load.
        hasLiveData = false
        loadErrorMessage = nil
        lastLiveBody = nil
        Self.storeActiveIdHint(nil)
    }

    private func ensureActiveSelection() {
        if let current = activePortfolioId,
           portfolios.contains(where: { $0.id == current }) {
            return
        }
        if let first = portfolios.first {
            activePortfolioId = first.id
            Self.storeActiveIdHint(first.id)
        } else {
            activePortfolioId = nil
        }
    }

    // MARK: - Portfolio CRUD

    @discardableResult
    func createPortfolio(named name: String) async throws -> Portfolio {
        let dto = try await apiClient.request(
            endpoint: .createPortfolio(name: name),
            responseType: PortfolioDTO.self
        )
        let new = dto.toPortfolio()
        portfolios.append(new)
        portfolios.sort { $0.sortOrder < $1.sortOrder }
        confirmedItems[new.id] = new.items
        confirmedMutationCount &+= 1
        // The server deliberately does NOT auto-activate a new group — doing it there
        // would move Home and Updates as a side effect of an action that only said "make
        // a new list". Switching is the CLIENT's call, and here the user just created it,
        // so switching is what they meant. `new.isActive` is already true when this was
        // their first group, and the guard makes that a no-op.
        await setActivePortfolio(new.id)
        return new
    }

    @discardableResult
    func renamePortfolio(id: String, to newName: String) async throws -> Portfolio {
        let dto = try await apiClient.request(
            endpoint: .renamePortfolio(id: id, name: newName),
            responseType: PortfolioDTO.self
        )
        let updated = dto.toPortfolio()
        // The NAME only. Replacing the whole row also wrote the response's `items` over a
        // ticker edit still queued (the rename field sits in the same editor as the drag).
        if let index = portfolios.firstIndex(where: { $0.id == id }) {
            portfolios[index].name = updated.name
        }
        confirmedMutationCount &+= 1
        // Renaming the ACTIVE group changes the header Home renders and the title of the
        // Updates manage sheet, both server-supplied. Without this they keep the old name
        // for up to 5 minutes / the rest of the session respectively.
        if id == activePortfolioId {
            announceActiveGroupChange()
        }
        return updated
    }

    func deletePortfolio(id: String) async throws {
        let wasActive = activePortfolioId == id
        try await apiClient.request(endpoint: .deletePortfolio(id: id))
        portfolios.removeAll { $0.id == id }
        confirmedItems.removeValue(forKey: id)
        unannouncedChanges.removeValue(forKey: id)
        writeSequencer.forget(id)
        writeTails.removeValue(forKey: id)
        confirmedMutationCount &+= 1
        if wasActive {
            // The SERVER already promoted a replacement (`ensure_active_portfolio`, called
            // from `delete_portfolio`). Re-read rather than guessing locally: the two used
            // to pick independently — the server orders by (sort_order, created_at, id),
            // this client only by sortOrder — so a tie left Tracking following one group
            // while Home and Updates rendered another, with nothing to reconcile them
            // until the next relaunch.
            await loadPortfolios()
            announceActiveGroupChange()
        }
    }

    func reorderPortfolios(_ newOrder: [Portfolio]) async throws {
        let ids = newOrder.map(\.id)
        // Only `sortOrder` moves, written onto the CURRENT values. Assigning the caller's copies
        // (as this used to) also wrote back their `items` and `name` — so a ticker edit still
        // in flight when the group list was dragged lost its optimistic state, and the revert
        // put back a whole array captured before it.
        let previousOrder: [String: Int] = Dictionary(
            portfolios.map { ($0.id, $0.sortOrder) }, uniquingKeysWith: { first, _ in first })
        applySortOrder(Dictionary(
            ids.enumerated().map { ($1, $0) }, uniquingKeysWith: { first, _ in first }))
        do {
            try await apiClient.request(endpoint: .reorderPortfolios(ids: ids))
        } catch {
            // Snapshot BEFORE the optimistic write and revert on throw, like every other
            // optimistic mutation in this file: a failed reorder used to leave the local list in
            // the new order and the server in the old one, and the next load silently undid it.
            applySortOrder(previousOrder)
            throw error
        }
        confirmedMutationCount &+= 1
    }

    /// Writes each listed group's `sortOrder` and re-sorts. Groups the map does not name keep
    /// theirs.
    private func applySortOrder(_ order: [String: Int]) {
        for index in portfolios.indices {
            if let position = order[portfolios[index].id] {
                portfolios[index].sortOrder = position
            }
        }
        portfolios.sort { $0.sortOrder < $1.sortOrder }
    }

    // MARK: - Per-portfolio write chain

    /// Which membership write is newest, what is still queued, and whether a load may predate a
    /// write. See `MembershipWriteSequencer` for why every write to a portfolio waits its turn.
    private var writeSequencer = MembershipWriteSequencer()

    /// The last write queued per portfolio. The next one awaits it before sending, so a
    /// portfolio's PUTs reach the server one at a time, in the order the user made them.
    private var writeTails: [String: Task<Void, Never>] = [:]

    /// The membership the SERVER last confirmed per portfolio — what a failed write reverts to.
    /// Seeded by a load (for groups with nothing queued), a create, and every confirmed write.
    private var confirmedItems: [String: [PortfolioItem]] = [:]

    /// `userInfo` key on `activeGroupTickersDidChangeNotification`: true when the confirmed
    /// writes since the last post changed WHICH tickers the group holds (an add or remove),
    /// false for pure reorders. The diversification score depends on membership only.
    static let membershipChangedKey = "membershipChanged"

    /// Confirmed writes not yet announced, per group: whether any of them changed membership.
    /// Only the NEWEST write's answer is announced (an older one would send Updates to re-read
    /// a list a newer write is about to replace, and its unordered reloads could land last), so
    /// the older answers' membership changes ride along here — sticky until that post.
    private var unannouncedChanges: [String: Bool] = [:]

    /// What one queued membership write came to.
    private enum MembershipLinkResult {
        /// The server confirmed this write; its answer is the confirmed ticker list.
        case confirmed([String])
        /// This write failed while a NEWER write was queued. That write sends the full list,
        /// which includes this edit, so ITS answer decides the outcome (`settledOutcome`).
        case superseded(Error)
        /// The session ended, or the group was deleted, before it could answer: no one to tell.
        case abandoned
    }

    /// Queues a whole-list `PUT /portfolios/{id}/tickers` of `tickers` behind every earlier
    /// write to the same portfolio.
    ///
    /// `tickers` is captured NOW, right after the caller's optimistic edit, so each gesture
    /// sends exactly the list it produced — never a later one that an unrelated load or holdings
    /// write may have replaced in the meantime.
    ///
    /// - Returns: the write. Its value is the server-confirmed ticker list, or nil when the
    ///   session ended before it could answer (a link from an ended session never throws: it
    ///   has no one left to tell).
    ///
    /// `satisfied` says whether a confirmed list holds THIS write's edit. It decides the outcome
    /// of a write that failed while a newer one was queued: the newer write carries this edit,
    /// so reporting the older failure as final told the user an edit had failed that the server
    /// then saved — and skipped the remove-fully step for a ticker that did leave the group.
    private func enqueueMembershipWrite(
        _ tickers: [String], for portfolioId: String,
        satisfied: @escaping @Sendable ([String]) -> Bool
    ) -> Task<[String]?, Error> {
        let epoch = identityEpoch
        let ticket = writeSequencer.enqueueMembership(portfolioId)
        let previous = writeTails[portfolioId]
        let link = Task { @MainActor [weak self] () async throws -> MembershipLinkResult in
            await previous?.value
            guard let self, epoch == self.identityEpoch else { return .abandoned }
            defer {
                if epoch == self.identityEpoch { self.writeSequencer.finish(portfolioId) }
            }
            // A group deleted while this waited takes no write: it would only 404, and its
            // caller would report a failure for a group the user just removed.
            guard self.portfolios.contains(where: { $0.id == portfolioId }) else { return .abandoned }
            return try await self.syncTickers(for: portfolioId, tickers: tickers, ticket: ticket, epoch: epoch)
        }
        writeTails[portfolioId] = Task { _ = try? await link.value }
        return Task { @MainActor [weak self] () async throws -> [String]? in
            switch try await link.value {
            case .confirmed(let confirmed):
                return confirmed
            case .abandoned:
                return nil
            case .superseded(let error):
                guard let self else { return nil }
                return try await self.settledOutcome(
                    for: portfolioId, epoch: epoch, satisfied: satisfied, error: error
                )
            }
        }
    }

    /// A superseded write's real outcome: once every write queued for the group has finished,
    /// the confirmed list either holds this edit (success — the list is returned) or not (the
    /// write's own error is thrown). nil when the session ended or the group is gone.
    private func settledOutcome(
        for portfolioId: String, epoch: Int,
        satisfied: ([String]) -> Bool, error: Error
    ) async throws -> [String]? {
        // Each tail is awaited once: a tail that is already finished while the count still
        // reads pending must not spin this loop.
        var lastAwaited: Task<Void, Never>?
        while epoch == identityEpoch, writeSequencer.hasPending(portfolioId),
              let tail = writeTails[portfolioId], tail != lastAwaited {
            lastAwaited = tail
            await tail.value
        }
        guard epoch == identityEpoch, portfolios.contains(where: { $0.id == portfolioId }) else {
            return nil
        }
        guard let confirmed = confirmedItems[portfolioId]?.map(\.ticker), satisfied(confirmed) else {
            throw error
        }
        return confirmed
    }

    /// Posts `activeGroupTickersDidChangeNotification` for the confirmed writes not announced
    /// yet, when the group is the active one.
    private func announceConfirmedChanges(for portfolioId: String) {
        guard let changed = unannouncedChanges.removeValue(forKey: portfolioId),
              portfolioId == activePortfolioId else { return }
        NotificationCenter.default.post(
            name: Self.activeGroupTickersDidChangeNotification,
            object: nil,
            userInfo: [Self.membershipChangedKey: changed]
        )
    }

    // MARK: - Ticker membership (optimistic)

    /// Append a ticker to the active portfolio (or `portfolioId` if given).
    /// No-op if the ticker is already there. The new item starts with no
    /// per-portfolio holding values; the user fills those in via the
    /// Insights config sheet.
    func addTicker(_ ticker: String, to portfolioId: String? = nil) async throws {
        let symbol = ticker.uppercased()
        guard let targetId = portfolioId ?? activePortfolioId,
              let index = portfolios.firstIndex(where: { $0.id == targetId }) else {
            return
        }
        if portfolios[index].items.contains(where: { $0.ticker == symbol }) { return }

        portfolios[index].items.append(
            PortfolioItem(ticker: symbol, shares: nil, marketValue: nil)
        )
        // A failure is reverted inside the write (to the last CONFIRMED membership, and only
        // when no newer edit is queued) — never here, from a copy captured before the await.
        _ = try await enqueueMembershipWrite(
            portfolios[index].tickers, for: targetId, satisfied: { $0.contains(symbol) }
        ).value
    }

    /// Drop a ticker from the active portfolio (or `portfolioId` if given).
    ///
    /// - Returns: the server-confirmed tickers after the write, or nil when nothing was sent
    ///   (the ticker was not there) or the session ended. Remove-fully reads it: a ticker may
    ///   leave the watchlist only once the server has confirmed it left this group.
    @discardableResult
    func removeTicker(_ ticker: String, from portfolioId: String? = nil) async throws -> [String]? {
        guard let targetId = portfolioId ?? activePortfolioId else { return nil }
        return try await startRemoveTicker(ticker, from: targetId)?.value
    }

    /// `removeTicker` for a caller that must see the row gone NOW (`List.onDelete`): the
    /// optimistic removal is applied before this returns.
    ///
    /// - Returns: the write (its value is the server-confirmed ticker list), or nil when the
    ///   ticker was not in the group.
    @discardableResult
    func startRemoveTicker(_ ticker: String, from portfolioId: String) -> Task<[String]?, Error>? {
        let symbol = ticker.uppercased()
        guard let index = portfolios.firstIndex(where: { $0.id == portfolioId }) else { return nil }
        let before = portfolios[index].items.count
        portfolios[index].items.removeAll { $0.ticker == symbol }
        guard portfolios[index].items.count != before else { return nil }
        return enqueueMembershipWrite(
            portfolios[index].tickers, for: portfolioId, satisfied: { !$0.contains(symbol) }
        )
    }

    /// Replace the ticker list for a portfolio (drag-to-reorder in Manage Tickers and Edit
    /// Portfolio, and the orphan purge). Preserves per-portfolio holdings for tickers already
    /// in the portfolio; new tickers come in with empty holdings.
    func setTickers(_ tickers: [String], in portfolioId: String) async throws {
        _ = try await startSetTickers(tickers, in: portfolioId)?.value
    }

    /// `setTickers` for a caller that must see the new order NOW: the optimistic order is
    /// applied before this returns, synchronously.
    ///
    /// `List.onMove` needs that. Writing the order from inside a `Task` landed one main-actor
    /// hop after the gesture ended, and the dragged row could snap back for a frame.
    ///
    /// - Returns: the write (its value is the server-confirmed ticker list), or nil when there
    ///   is nothing to send — no such portfolio, or the order is unchanged.
    @discardableResult
    func startSetTickers(_ tickers: [String], in portfolioId: String) -> Task<[String]?, Error>? {
        guard let index = portfolios.firstIndex(where: { $0.id == portfolioId }) else { return nil }
        let normalized = tickers.map { $0.uppercased() }
        // first-wins: a portfolio carrying the same ticker twice would otherwise trap
        // here while merely reordering its own tickers.
        let priorByTicker = Dictionary(
            portfolios[index].items.map { ($0.ticker, $0) }, uniquingKeysWith: { first, _ in first })
        let newItems = normalized.map { ticker in
            priorByTicker[ticker] ?? PortfolioItem(ticker: ticker, shares: nil, marketValue: nil)
        }
        // A drag dropped where it started: nothing changed, so nothing is sent.
        guard newItems != portfolios[index].items else { return nil }
        portfolios[index].items = newItems
        // Held when the confirmed list keeps these tickers in this relative order (the server
        // drops a ticker that left the watchlist, and a later write may add or remove others).
        let desired = newItems.map(\.ticker)
        return enqueueMembershipWrite(portfolios[index].tickers, for: portfolioId, satisfied: { confirmed in
            let kept = Set(confirmed)
            let wanted = Set(desired)
            return desired.filter { kept.contains($0) } == confirmed.filter { wanted.contains($0) }
        })
    }

    /// Remove a ticker from every portfolio (used by the long-press
    /// "Remove from all portfolios" action). Best-effort: any single sync
    /// failure is logged but the others still run.
    ///
    /// - Returns: the names of the groups whose write failed, so the caller can say so
    ///   (auth.md §6) — empty when every write succeeded.
    @discardableResult
    func removeTickerFromAllPortfolios(_ ticker: String) async -> [String] {
        let symbol = ticker.uppercased()
        var failed: [String] = []
        for portfolio in portfolios where portfolio.tickers.contains(symbol) {
            do {
                try await removeTicker(symbol, from: portfolio.id)
            } catch {
                let detail = "\(type(of: error)): \(error)"
                portfolioStoreLog.error(
                    "remove \(symbol, privacy: .public) from group \(portfolio.id, privacy: .public) failed — \(detail, privacy: .public)"
                )
                failed.append(portfolio.name)
            }
        }
        return failed
    }

    /// Drop tickers from every portfolio that no longer exist on the master
    /// watchlist (e.g. removed from another device). Idempotent — only fires
    /// the per-portfolio sync if something actually changed.
    ///
    /// - Returns: true when at least one per-portfolio `setTickers` was attempted (the
    ///   membership, and so the diversification score, may have changed).
    @discardableResult
    func purgeTickers(notIn allowed: Set<String>) async -> Bool {
        // NEVER purge from a list the server did not hand us in THIS session. `setTickers` is a
        // whole-list PUT built from `portfolios`; membership that is not live (nothing loaded
        // yet, or only a failed load) would delete what the user added since — hand-entered
        // shares included. The Tracking snapshot is display-only and never reaches this list,
        // and this guard is the store-side half of that rule.
        guard hasLiveData else {
            portfolioStoreLog.warning("purge refused — no live GET /portfolios answer in this session")
            return false
        }
        // NEVER purge against an empty allow-set. A total-wipe delta INFERRED from
        // a feed response is never trustworthy: the tracking feed returns an empty
        // asset list both when the watchlist is genuinely empty AND (historically)
        // when the datastore read failed, and an empty set makes every portfolio
        // differ → setTickers([]) → the backend deletes every portfolio_items row
        // with no reinsert, destroying hand-entered shares/market_value for good.
        // Genuine emptying always flows through the explicit removeTicker /
        // removeTickerFromAllPortfolios paths, which are per-ticker and intentional.
        guard !allowed.isEmpty else {
            print("[PortfolioStore] ⚠️ Skipping purge: empty allow-set (degraded or empty feed) — refusing to clear \(portfolios.count) portfolio(s)")
            return false
        }
        var attempted = false
        let upperAllowed = Set(allowed.map { $0.uppercased() })
        // Each group's list is built from the CURRENT `portfolios`, read after the previous
        // group's await. A copy taken before the loop would overwrite an edit the user made
        // while an earlier group's write was in flight.
        for portfolioId in portfolios.map(\.id) {
            guard let portfolio = portfolios.first(where: { $0.id == portfolioId }) else { continue }
            let filteredTickers = portfolio.tickers.filter { upperAllowed.contains($0) }
            if filteredTickers != portfolio.tickers {
                attempted = true
                do {
                    try await setTickers(filteredTickers, in: portfolioId)
                } catch {
                    print("[PortfolioStore] ❌ Failed to purge orphans in \(portfolio.name): \(error)")
                }
            }
        }
        return attempted
    }

    // MARK: - Per-portfolio holdings (shares / market_value)

    /// Push a bulk update of `shares` / `market_value` for tickers in the
    /// given portfolio. The server response is the full refreshed portfolio;
    /// its holdings are merged into local state by ticker — keeps display +
    /// diversification calc in sync without a separate refresh.
    ///
    /// Queued in the portfolio's write chain: `PUT /tickers` snapshots holdings and then deletes
    /// and reinserts every row, so a holdings write landing in between was overwritten.
    @discardableResult
    func setHoldings(_ items: [HoldingUpdateItem], in portfolioId: String) async throws -> Portfolio {
        let epoch = identityEpoch
        writeSequencer.enqueueOther(portfolioId)
        let previous = writeTails[portfolioId]
        let write = Task { @MainActor [weak self] () async throws -> PortfolioDTO? in
            await previous?.value
            guard let self, epoch == self.identityEpoch else { return nil }
            defer {
                if epoch == self.identityEpoch { self.writeSequencer.finish(portfolioId) }
            }
            return try await self.apiClient.request(
                endpoint: .setPortfolioHoldings(id: portfolioId, items: items),
                responseType: PortfolioDTO.self
            )
        }
        writeTails[portfolioId] = Task { _ = try? await write.value }
        guard let dto = try await write.value, epoch == identityEpoch else {
            // The session ended while the write waited or ran: nothing of it may land in the
            // next session's store.
            throw CancellationError()
        }
        let updated = dto.toPortfolio()
        // MERGE the holdings by ticker; never replace `items`. The response's membership is the
        // server's, and a membership edit queued after this write is already on screen.
        if let index = portfolios.firstIndex(where: { $0.id == portfolioId }) {
            portfolios[index].items = Self.mergingHoldings(from: updated.items, into: portfolios[index].items)
        }
        if let confirmed = confirmedItems[portfolioId] {
            confirmedItems[portfolioId] = Self.mergingHoldings(from: updated.items, into: confirmed)
        }
        confirmedMutationCount &+= 1
        return updated
    }

    /// `target` with each ticker's `shares` / `marketValue` taken from `source` when `source`
    /// holds that ticker. Membership and order stay `target`'s.
    private static func mergingHoldings(
        from source: [PortfolioItem], into target: [PortfolioItem]
    ) -> [PortfolioItem] {
        let byTicker = Dictionary(source.map { ($0.ticker, $0) }, uniquingKeysWith: { first, _ in first })
        return target.map { item in
            guard let server = byTicker[item.ticker] else { return item }
            var merged = item
            merged.shares = server.shares
            merged.marketValue = server.marketValue
            return merged
        }
    }

    // MARK: - Server sync helper

    /// One queued membership write: the PUT, then what its answer may change.
    ///
    /// - Every answer becomes the CONFIRMED membership (the revert target) and is announced.
    /// - Only the NEWEST write's answer replaces what is on screen; an older one would roll a
    ///   newer optimistic edit back.
    /// - A failure reverts to the confirmed membership only when no newer write is queued: that
    ///   write carries the full list and settles the state itself.
    private func syncTickers(for portfolioId: String, tickers: [String], ticket: Int, epoch: Int) async throws -> MembershipLinkResult {
        let dto: PortfolioDTO
        do {
            dto = try await apiClient.request(
                endpoint: .setPortfolioTickers(id: portfolioId, tickers: tickers),
                responseType: PortfolioDTO.self
            )
        } catch {
            guard epoch == identityEpoch else { return .abandoned }
            guard let index = portfolios.firstIndex(where: { $0.id == portfolioId }) else {
                return .abandoned
            }
            // A newer write is queued: it sends the full list, this edit included, and its
            // answer settles both. Neither a revert nor a final failure belongs to this one.
            guard writeSequencer.isNewest(portfolioId, ticket: ticket) else {
                return .superseded(error)
            }
            if let confirmed = confirmedItems[portfolioId] {
                portfolios[index].items = confirmed
            } else {
                // Nothing confirmed to fall back on (no live load seeded it). Leaving the
                // optimistic list up would show a state the server refused, so re-read it.
                portfolioStoreLog.warning(
                    "membership write failed with no confirmed list for group \(portfolioId, privacy: .public) — reloading"
                )
                Task { await self.loadPortfolios() }
            }
            // Older writes of this burst may have been confirmed (and not announced, waiting
            // for this newest one): the server did change, so say so now.
            announceConfirmedChanges(for: portfolioId)
            throw error
        }
        guard epoch == identityEpoch else { return .abandoned }
        let serverItems = dto.toPortfolio().items
        let previousTickers = Set((confirmedItems[portfolioId] ?? []).map(\.ticker))
        let membershipChanged = Set(serverItems.map(\.ticker)) != previousTickers
        confirmedItems[portfolioId] = serverItems
        unannouncedChanges[portfolioId] = (unannouncedChanges[portfolioId] ?? false) || membershipChanged
        // Server is the source of truth — accept its filtered/normalized items (it drops tickers
        // not on the master watchlist and returns the canonical per-portfolio holdings). Only
        // for the newest write: an older answer would undo a newer edit already on screen.
        let isNewest = writeSequencer.isNewest(portfolioId, ticket: ticket)
        if isNewest, let index = portfolios.firstIndex(where: { $0.id == portfolioId }) {
            portfolios[index].items = serverItems
        }
        // Every membership write lands here (add / remove / reorder / purge, and the Edit and
        // Manage Tickers sheets that call the store directly), so one bump covers them all.
        confirmedMutationCount &+= 1
        // The Home Screen Holdings tile is built server-side from the ACTIVE group, and an
        // add / remove / reorder posts neither `activeGroupDidChange` nor (from Tracking's
        // Edit sheet) a watchlist change — so without this the tile kept the old holdings
        // until the next app visit. Only after the server confirmed, and only for the active
        // group; the service debounces and is session-gated, so a sign-out mid-request (which
        // nils `activePortfolioId` in `reset()`) publishes nothing.
        //
        // The same confirmed change is announced to Updates, Home and Tracking's insights —
        // every add, remove, reorder and purge, not only `setTickers` (the one poster before,
        // so a removed ticker stayed on the Updates chips) — once per burst, on the NEWEST
        // answer, carrying whether any write in it changed membership.
        if portfolioId == activePortfolioId {
            WidgetRefreshService.shared.contentChanged()
            if isNewest {
                announceConfirmedChanges(for: portfolioId)
            }
        }
        return .confirmed(serverItems.map(\.ticker))
    }
}

// MARK: - Environment

private struct PortfolioStoreKey: EnvironmentKey {
    @MainActor static var defaultValue: PortfolioStore { PortfolioStore.shared }
}

extension EnvironmentValues {
    var portfolioStore: PortfolioStore {
        get { self[PortfolioStoreKey.self] }
        set { self[PortfolioStoreKey.self] = newValue }
    }
}

// MARK: - Watchlist change payload

/// Payload of `PortfolioStore.watchlistDidChangeNotification`. File scope (not nested in
/// the `@MainActor` store) so the `userInfo` value carries no actor isolation and every
/// subscriber can read it from whatever context the notification lands on.
struct WatchlistChange: Sendable, Equatable {
    /// Where the mutation was made. Subscribers that already reload themselves after
    /// their OWN mutation ignore their own source, or every toggle would fetch twice.
    enum Source: String, Sendable {
        case detailStar
        case updates
        case tracking
    }

    /// Stored spelling, uppercased (see `PortfolioStore.announceWatchlistChange`).
    let ticker: String
    let assetType: String
    let added: Bool
    let source: Source

    static let userInfoKey = "change"

    init(ticker: String, assetType: String, added: Bool, source: Source) {
        self.ticker = ticker.uppercased()
        self.assetType = assetType
        self.added = added
        self.source = source
    }

    /// The payload carried by a `watchlistDidChangeNotification`, or nil for any other
    /// notification (or a malformed one).
    init?(_ notification: Notification) {
        guard let change = notification.userInfo?[Self.userInfoKey] as? WatchlistChange else {
            return nil
        }
        self = change
    }
}
