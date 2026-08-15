//
//  WidgetRefreshService.swift
//  Caydex
//
//  Fetches the widget payloads and writes them into the shared App Group.
//
//  This exists because the WIDGET CANNOT FETCH FOR ITSELF — see the long note in
//  `Shared/WidgetSnapshotStore.swift`. `.claude/rules/auth.md` §8 makes `APIClient` the
//  single token source, and a widget extension is a separate process that cannot reach
//  it. So the app, which does hold the token, does the fetching, and the widget renders
//  whatever it last found in the container.
//
//  The freshness cost of that is real and is handled honestly rather than hidden: the
//  payload carries `as_of` and `market_session`, and the widget renders "At the close"
//  rather than implying live data. The insight sweeper is asleep 20:00–04:00 ET and all
//  weekend anyway, so on a Saturday there is nothing newer to show.
//

import Foundation
import OSLog

@MainActor
final class WidgetRefreshService {
    static let shared = WidgetRefreshService()

    private let log = Logger(subsystem: "com.phan.caydex", category: "widget")

    /// Foregrounding twice in ten seconds should not cost two round trips.
    private var lastRefresh: Date?
    private static let minimumInterval: TimeInterval = 60

    private var inFlight: Task<Void, Never>?

    /// A `force: true` request that arrived while a refresh was already running.
    ///
    /// It cannot simply join that run: the reason `onAuthenticated` forces a refresh is
    /// that the in-flight one is very likely fetching under the WRONG identity (see
    /// `refresh(force:)`), so its result is exactly what we need to replace.
    private var forcedRefreshPending = false

    /// Whether the app has decided what credential (if any) to send.
    ///
    /// ⚠️ A REFRESH BEFORE THIS IS POINTLESS AND ACTIVELY WRONG, which is not obvious
    /// because it does not fail — it succeeds with someone else's data.
    /// `/widget/portfolio-mover` is `.guestAllowed`, so a request carrying no bearer is
    /// answered for this install's per-install GUEST, who owns no portfolio; the backend's
    /// documented "degrade, never error" path then returns the MARKET payload, and market
    /// movers land in the "My Holdings" tile.
    ///
    /// It fires that early on every cold launch: `UIApplication.didBecomeActiveNotification`
    /// is delivered BEFORE the root `.task` runs `AppState.configure`, so the foreground
    /// trigger in `iosApp` beat the credential to the wire. Measured in a launch log — the
    /// widget requests appear above the appearance probe's `task-pre` line.
    ///
    /// Opened by `AppState` the instant the stored credential is armed, which is also where
    /// the cold-launch seed fires — so nothing is lost by dropping the early call.
    private var credentialReady = false

    /// Called by `AppState` once the stored credential (if any) is on `APIClient`.
    func markCredentialReady() {
        guard !credentialReady else { return }
        credentialReady = true
    }

    private init() {}

    /// Refresh both modes and hand the result to the widget.
    ///
    /// Best-effort by design: this is a background nicety, and a failure must never
    /// surface to the user or block anything. A stale widget is fine; an error alert
    /// because a Home Screen tile could not update is not.
    func refresh(force: Bool = false) {
        // Nothing may go out before we know who we are — see `credentialReady`. Dropped
        // rather than queued: `AppState` fires the seed the moment it opens this gate, so a
        // queued copy would just be a duplicate of that.
        guard credentialReady else {
            log.info("widget refresh skipped — credential not armed yet")
            return
        }

        // JOIN a running refresh, never cancel it.
        //
        // The obvious version — `inFlight?.cancel()` then start a new task — silently
        // does NOTHING when the two triggers land together, which is the common case:
        // cold launch fires one, `didBecomeActive` fires the other milliseconds later,
        // the first is cancelled mid-flight, and the second is then rejected by the
        // throttle the first already consumed. Verified on the simulator: the app made
        // 20+ requests on launch and zero widget fetches.
        if let running = inFlight, !running.isCancelled {
            // …but a FORCED request must not be dropped on the floor.
            //
            // `AppState.onAuthenticated` forces one precisely because the run already in
            // flight went out before the bearer was installed, so it is resolving the
            // per-install GUEST — who owns no portfolio — and the backend degrades that to
            // the market payload. Returning here left that guest result as the final word
            // for the whole session: the Home Screen "My Holdings" tile kept showing market
            // movers until some unrelated foreground 60s later. The observed launch only
            // recovered because a 9-second gap let the first run finish first; a fast auth
            // restore would not have.
            //
            // Remember it and re-run on completion rather than starting a second task, so
            // `inFlight` stays a single cancellable handle and `clearForEndedSession()`
            // can still stop everything in one call (auth.md §7).
            if force { forcedRefreshPending = true }
            return
        }

        if !force, let last = lastRefresh, Date().timeIntervalSince(last) < Self.minimumInterval {
            return
        }
        startRefresh()
    }

    /// Runs a refresh unconditionally — throttling and joining are decided by `refresh(force:)`.
    private func startRefresh() {
        inFlight = Task { [weak self] in
            await self?.performRefresh()
            guard let self else { return }
            self.inFlight = nil

            // Drain a forced request that landed mid-run. Skipped when this task was
            // cancelled: that means the session ended, and re-running would re-publish
            // the ended session's holdings — the leak the cancellation check in
            // `performRefresh` exists to prevent.
            if self.forcedRefreshPending, !Task.isCancelled {
                self.forcedRefreshPending = false
                self.startRefresh()
            }
        }
    }

    private func performRefresh() async {
        let client = APIClient.shared
        log.info("widget refresh starting")

        // Both modes, concurrently — they hit different caches and neither blocks the
        // other. Market is fetched even when signed in: the widget's default
        // configuration is Market, and a user may have one of each on their screen.
        async let market: WidgetMoverSnapshot? = fetch(.getWidgetMarketMover, client: client)
        async let portfolio: WidgetMoverSnapshot? = fetch(.getWidgetPortfolioMover, client: client)

        let (m, p) = await (market, portfolio)

        // ⚠️ CHECK CANCELLATION BEFORE WRITING.
        //
        // `clearForEndedSession()` cancels this task and wipes the App Group — but
        // cancelling cannot un-finish a child task that already resolved. Without this
        // check, a response that landed just before sign-out resumed here afterwards and
        // re-published the ended session's holdings to the Home Screen, where they are
        // visible without even unlocking. That is precisely what `.claude/rules/auth.md`
        // §7 exists to prevent.
        if Task.isCancelled {
            log.warning("widget refresh cancelled — discarding fetched snapshots")
            return
        }

        // `write` itself refuses to replace a good snapshot with an empty one; see the
        // note there. A degraded backend 200 must not blank a working tile.
        if let m { WidgetSnapshotStore.write(mode: .market, snapshot: m) }
        if let p { WidgetSnapshotStore.write(mode: .portfolio, snapshot: p) }

        if m == nil && p == nil {
            // Never silent: without this, a widget that never updates looks like a bug
            // in the widget rather than a failed fetch in the app.
            log.warning("widget refresh produced nothing — both modes failed")
            // Do NOT consume the throttle window on a total failure; the next
            // foreground should be allowed to try again immediately.
            return
        }
        log.info("widget refresh wrote market=\(m != nil) portfolio=\(p != nil)")
        // Stamped on COMPLETION, not on entry. Stamping first means a run that is
        // cancelled or fails still burns the next minute's allowance.
        lastRefresh = Date()
    }

    private func fetch(_ endpoint: APIEndpoint, client: APIClient) async -> WidgetMoverSnapshot? {
        do {
            // `APIClient`'s decoder uses `.iso8601`, which is exactly why the backend
            // emits `as_of` without fractional seconds (`schemas/widget.py`).
            return try await client.request(
                endpoint: endpoint, responseType: WidgetMoverSnapshot.self
            )
        } catch {
            log.warning("widget \(String(describing: endpoint)) failed: \(String(describing: error))")
            return nil
        }
    }

    /// Called when a session ends. See `.claude/rules/auth.md` §7: a device-global store
    /// that survives sign-out hands the next account the previous user's data — and a
    /// portfolio snapshot is visible on the Home Screen without even unlocking the app.
    func clearForEndedSession() {
        inFlight?.cancel()
        // Drop any queued forced refresh too — otherwise the ended session's pending
        // request re-runs after cancellation and re-publishes their holdings.
        forcedRefreshPending = false
        lastRefresh = nil
        WidgetSnapshotStore.clear()
    }
}
