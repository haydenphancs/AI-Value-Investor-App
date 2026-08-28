//
//  PriceAlertStore.swift
//  ios
//
//  The ONE copy of the user's price alerts. Every surface reads this.
//
//  WHY THIS EXISTS
//  ---------------
//  TestFlight: *"Price Rules must be match with the notification icon in the ticker when they
//  set it up."* — a screenshot of ORCL holding an ACTIVE price rule in Tracking → Alerts while
//  the bell in the ORCL detail header sat as an unchanged grey outline.
//
//  It could not have been anything else: nothing in the app knew a ticker had alerts until the
//  bell was tapped. Two view models sat over one endpoint and never spoke —
//  `PriceAlertsViewModel` (per-ticker, owns the create form) and `PriceAlertRulesViewModel`
//  (the cross-ticker list, now folded into this file). `AppState` held no alert state,
//  `NotificationRepository` caches nothing, and the view model that DID know was constructed
//  inside the sheet, i.e. only after the bell was tapped.
//
//  Three defects came out of that one shape, and all three are fixed by having one array:
//    1. The header bell had no state to render.
//    2. A rule created in the bell sheet was invisible in Tracking → Alerts for up to five
//       minutes, because that tab's `loadIfStale` no-opped inside its window. The reverse
//       direction DID update (the sheet refetches on present), so it read as flaky, not stale.
//    3. The tab's caption counted ALL rules while the server quota counts only ACTIVE ones, so
//       "20 of 20" showed while a 21st was still creatable.
//
//  WHY ONE UNFILTERED FETCH IS ENOUGH
//  ----------------------------------
//  `GET /api/v1/alerts/price` returns every alert the user has and takes an OPTIONAL `ticker`
//  filter — the two surfaces were always calling the same route with one different query
//  param. And the response is small by construction: `price_alert_service.py` limits the query
//  to `PRICE_ALERT_MAX_PER_USER * 2` = 40 rows. So one unfiltered load answers "does THIS
//  ticker have alerts" for every ticker, in memory, for free. That is what makes a shared store
//  cheaper than the per-ticker fetch it replaces, rather than an extra cost.
//
//  Modelled on `PortfolioStore` — the same problem (one server-backed collection, several
//  screens that must agree) solved the same way. Deliberately NOT modelled on
//  `AppState.WatchlistState`, which is dead code with zero readers.
//
//  Mutation strategy, per operation and per `PortfolioStore`'s documented split:
//    - create   → SERVER-FIRST. The server mints the id, seeds `last_price` from a live quote
//                 and decides `armed`. A bell badging for an alert that was never created is a
//                 lie about a notification that will never arrive.
//    - toggle   → OPTIMISTIC, then replaced by the server row. Turning the last active alert
//                 off has to un-badge the bell immediately; the round trip stays because
//                 `armed` moves server-side too.
//    - delete   → OPTIMISTIC with a snapshot revert.
//  Nothing is written to disk, so auth.md §6's "never persist an optimistic value" holds
//  structurally rather than by discipline.
//

import Combine
import Foundation
import os

@MainActor
final class PriceAlertStore: ObservableObject {

    // MARK: - Singleton

    static let shared = PriceAlertStore()

    // MARK: - State

    enum State: Equatable {
        case loading
        case loaded
        case error(String)
        /// A stored credential that has not been validated yet. NOT `signedOut` — this user is
        /// signed in as far as they are concerned, and `AppState.requestSignIn` declines to
        /// prompt during a restore, so a Sign In button here would be inert. (auth.md §5)
        case reconnecting
        case signedOut
    }

    @Published private(set) var state: State = .loading
    /// EVERY alert, across every ticker. The per-ticker views are computed from this.
    @Published private(set) var alerts: [PriceAlertDTO] = []
    @Published private(set) var maxPerUser: Int = 20
    @Published private(set) var maxPerTicker: Int = 3

    private let repository: NotificationRepositoryProtocol
    private let log = Logger(subsystem: "com.phan.caydex", category: "price-alerts")

    /// When the last load that actually SAW the account completed. Drives `loadIfStale` so
    /// re-entering a tab does not refetch on every switch.
    private var lastLoadedAt: Date?

    /// Single-flight, as `PortfolioStore` does it: five detail screens plus the Tracking tab
    /// can all call `loadIfStale` in the same run loop.
    private var loadTask: Task<Void, Never>?

    /// Long enough that tab-flipping is free, short enough that a rule created on another
    /// device shows up without a manual pull. `nonisolated` because it is used as a DEFAULT
    /// ARGUMENT below, and a default argument is evaluated at the call site under nonisolated
    /// checking — the same rule that forces the repository to be nil-coalesced inside `init`.
    nonisolated static let stalenessWindow: TimeInterval = 5 * 60

    /// Optional + nil-coalesce: `NotificationRepository.init` is MainActor-isolated and a
    /// default argument is checked as nonisolated at the CALL SITE, so the live default is
    /// constructed here, inside this `@MainActor` init.
    init(repository: NotificationRepositoryProtocol? = nil) {
        self.repository = repository ?? NotificationRepository()
    }

    var isEmpty: Bool { alerts.isEmpty }

    // MARK: - Per-ticker reads (the bell's questions)

    /// Alerts on one ticker.
    ///
    /// Crypto is matched on the BARE symbol because the app carries both forms — Home hands
    /// over `BTCUSD`, search hands over `BTC`, and `CryptoDetailView` normalizes to bare. A row
    /// written by an older client as `BTCUSD` must still light the bell on `BTC`. The bare form
    /// is applied ONLY to rows whose `assetType` is crypto, so a stock whose symbol happens to
    /// end in USD is never mangled.
    func alerts(for ticker: String) -> [PriceAlertDTO] {
        let wanted = Self.normalized(ticker)
        let wantedBare = CryptoSymbol.bare(wanted)
        return alerts.filter { alert in
            let symbol = Self.normalized(alert.ticker)
            if alert.assetType.lowercased() == "crypto" {
                return CryptoSymbol.bare(symbol) == wantedBare
            }
            return symbol == wanted
        }
    }

    /// What the detail-header bell renders off. ACTIVE only: a rule the user has toggled off
    /// will not fire, so badging for it would promise a notification that is not coming.
    func hasActiveAlerts(ticker: String) -> Bool {
        alerts(for: ticker).contains(where: \.isActive)
    }

    /// Active count — for one ticker, or for the whole account when `ticker` is nil.
    ///
    /// ACTIVE is the only correct basis, and it is what the server counts
    /// (`price_alert_service._count_for_user` filters `is_active = True`). Counting every row
    /// is what made the Tracking caption read "20 of 20" while a 21st was still creatable.
    func activeCount(ticker: String? = nil) -> Int {
        let pool = ticker.map { alerts(for: $0) } ?? alerts
        return pool.filter(\.isActive).count
    }

    private static func normalized(_ symbol: String) -> String {
        symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
    }

    // MARK: - Loading

    func load() async {
        if let running = loadTask, !running.isCancelled {
            await running.value
            return
        }
        let task = Task { await self.performLoad() }
        loadTask = task
        await task.value
        loadTask = nil
    }

    private func performLoad() async {
        // THREE outcomes, not two. "Not armed right now" is not the same as "signed out": at
        // launch this runs while session restore is still in flight, and a restore that keeps
        // failing backs off forever. Collapsing that into the sign-in prompt is what once told
        // a signed-in user to sign in, with their own avatar loaded above it.
        guard AppActions.shared.isSignedIn else {
            alerts = []
            state = AppActions.shared.isRestoringSession ? .reconnecting : .signedOut
            // Neither pass saw the account, so neither is ever "fresh" — otherwise the
            // staleness window would suppress the reload that heals it on sign-in.
            lastLoadedAt = nil
            return
        }
        do {
            let page = try await repository.fetchPriceAlerts(ticker: nil)
            alerts = page.items
            maxPerUser = page.maxPerUser
            maxPerTicker = page.maxPerTicker
            state = .loaded
            lastLoadedAt = Date()
        } catch {
            let appError = AppError.from(error)
            // A failure that is never logged is diagnosed from nothing. Type + operation, so
            // it is greppable without a repro.
            log.error("load price alerts failed: \(String(describing: type(of: error))): \(appError.message, privacy: .public)")
            // Backstop for a credential that dies MID-FLIGHT — the guard above cannot catch
            // that. Signed out and broken must not look alike: one wants a Sign In button, the
            // other Try Again, and "you have no price alerts" over an auth failure is a lie.
            if case .signInRequired = appError {
                state = .signedOut
            } else {
                // NEVER an empty string. `AppError.message` passes some backend messages
                // through verbatim (`.apiError`, `.validationFailed`), and an empty one
                // rendered as a bare warning triangle over a "Try Again" button with no
                // sentence at all — observed live on this screen.
                let text = appError.message.trimmingCharacters(in: .whitespacesAndNewlines)
                state = .error(text.isEmpty ? "We couldn't load your price alerts." : text)
            }
            alerts = []
            // A pass that never saw the account is NEVER fresh — otherwise the staleness
            // window suppresses the very reload that heals it once the user signs in.
            lastLoadedAt = nil
        }
    }

    /// Reload only if the last successful load has aged out, so tab re-entry and opening a
    /// second detail screen are both free.
    func loadIfStale(maxAge: TimeInterval = PriceAlertStore.stalenessWindow) async {
        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }
        await load()
    }

    // MARK: - Mutate

    /// SERVER-FIRST. Returns true on success so the caller can clear its draft.
    @discardableResult
    func create(
        ticker: String,
        kind: PriceAlertKind,
        threshold: Double,
        assetType: String,
        repeatMode: PriceAlertRepeat
    ) async -> Bool {
        do {
            let created = try await repository.createPriceAlert(
                ticker: ticker,
                kind: kind,
                threshold: threshold,
                assetType: assetType,
                repeatMode: repeatMode
            )
            alerts.insert(created, at: 0)
            state = .loaded
            return true
        } catch {
            // Never a bare `try?` and never a DEBUG-only print: a user-initiated mutation
            // that fails silently looks like a UI glitch and leaves no trace anywhere.
            AppActions.shared.reportMutationFailure(
                AppError.from(error), action: "create that price alert"
            )
            return false
        }
    }

    /// OPTIMISTIC. The bell has to stop badging the instant the last active rule is switched
    /// off — waiting for the round trip leaves the toggle and the bell disagreeing on screen.
    func toggleActive(_ alert: PriceAlertDTO) async {
        let snapshot = alerts
        applyIsActive(!alert.isActive, to: alert.id)
        do {
            let updated = try await repository.updatePriceAlert(
                id: alert.id, threshold: nil, isActive: !alert.isActive, repeatMode: nil
            )
            // The server row, not our guess: `armed` also changes server-side when a rule is
            // re-enabled, and showing a stale one is how "my alert never fires" becomes
            // unexplainable.
            replace(updated)
        } catch {
            alerts = snapshot
            AppActions.shared.reportMutationFailure(
                AppError.from(error),
                action: alert.isActive ? "turn off that alert" : "turn on that alert"
            )
        }
    }

    func delete(_ alert: PriceAlertDTO) async {
        // Optimistic in MEMORY only — nothing is persisted before the server confirms, so a
        // kill mid-request cannot make a deletion the server never received durable.
        let snapshot = alerts
        alerts.removeAll { $0.id == alert.id }
        do {
            _ = try await repository.deletePriceAlert(id: alert.id)
        } catch {
            alerts = snapshot
            AppActions.shared.reportMutationFailure(
                AppError.from(error), action: "delete that price alert"
            )
        }
    }

    /// auth.md §7 — this list is the caller's own data and must not survive a session end.
    /// Called from `AppState.discardDataForEndedSession()`, beside `PortfolioStore.shared.reset()`.
    func reset() {
        loadTask?.cancel()
        loadTask = nil
        alerts = []
        maxPerUser = 20
        maxPerTicker = 3
        lastLoadedAt = nil
        state = .loading
    }

    // MARK: - Private

    private func replace(_ updated: PriceAlertDTO) {
        if let index = alerts.firstIndex(where: { $0.id == updated.id }) {
            alerts[index] = updated
        }
    }

    private func applyIsActive(_ isActive: Bool, to id: String) {
        guard let index = alerts.firstIndex(where: { $0.id == id }) else { return }
        alerts[index] = alerts[index].withIsActive(isActive)
    }
}
