//
//  WidgetRefreshService.swift
//  Caydex
//
//  Fetches the widget payloads and writes them into the shared App Group.
//
//  This exists because the WIDGET CANNOT FETCH HOLDINGS FOR ITSELF — see the long note in
//  `Shared/WidgetSnapshotStore.swift`. `.claude/rules/auth.md` §8 makes `APIClient` the
//  single token source, and a widget extension is a separate process that cannot reach
//  it. So the app, which does hold the token, does the fetching, and the widget renders
//  whatever it last found in the container. (The extension refreshes MARKET mode itself
//  with the market-scoped widget token this service mints — auth.md §8a.)
//
//  The freshness cost of that is real and is handled honestly rather than hidden: the
//  payload carries `as_of` and `market_session`, and the widget renders "At the close"
//  rather than implying live data. The insight sweeper is asleep 20:00–04:00 ET and all
//  weekend anyway, so on a Saturday there is nothing newer to show.
//
//  Triggers: the cold-launch seed and the sign-in force (`AppState`), every foreground and
//  background (`iosApp`), and a content change — the active group, its holdings or the
//  watchlist (`contentChanged()`). Every one of them is gated on `sessionOpen`.
//

import Foundation
import OSLog
import UIKit

@MainActor
final class WidgetRefreshService {
    static let shared = WidgetRefreshService()

    private let log = Logger(subsystem: "com.phan.caydex", category: "widget")

    /// Foregrounding twice in ten seconds should not cost two round trips.
    private var lastRefresh: Date?
    private static let minimumInterval: TimeInterval = 60

    /// `AppState.identityGeneration` the last refresh whose PORTFOLIO leg succeeded ran under.
    ///
    /// What makes `force: true` meaningful is that the previous fetch answered for SOMEBODY
    /// ELSE. On a real sign-in that is true. On a cold launch it is not: `AppState` arms the
    /// stored credential before it fires the seed refresh, so by the time `onAuthenticated`
    /// forces one, the completed run already used the right bearer — and forcing it spent two
    /// more requests re-fetching identical data on every single launch.
    ///
    /// Stamped only when the portfolio leg succeeded: that is the identity-bearing leg. A run
    /// whose market leg landed and whose portfolio leg failed (a 429, a timeout) used to stamp
    /// it anyway, and the sign-in force that followed was then judged redundant — so the
    /// Holdings tile kept the previous answer until a foreground a minute later.
    private var lastRefreshIdentity: Int?

    /// The identity the run in flight answers for — or, once a force has queued behind it, the
    /// identity of that queued run. Each run captures it when it STARTS (`startRefresh`) and
    /// stamps that capture: stamping this field at completion credited a run with the identity
    /// of the force queued behind it, so a later force for that identity was suppressed against
    /// a fetch that never ran under it.
    private var inFlightIdentity: Int?

    private var inFlight: Task<Void, Never>?

    /// Which run owns `inFlight`. Bumped by every `startRefresh()`; a finishing run may clear
    /// the handle (and drain `forcedRefreshPending`) only while this still names it.
    private var runID = 0

    /// A `force: true` request that arrived while a refresh was already running.
    ///
    /// It cannot simply join that run: the reason `onAuthenticated` forces a refresh is
    /// that the in-flight one is very likely fetching under the WRONG identity (see
    /// `refresh(force:)`), so its result is exactly what we need to replace. A content change
    /// queues here too — the running fetch may have read the holdings from before the change.
    private var forcedRefreshPending = false

    /// A forced refresh JOINED a run already fetching under its own identity, so the force was
    /// not queued. That is right when the run succeeds; if its PORTFOLIO leg fails, the failure
    /// must not be the last word for the sign-in that asked, so the run queues one more.
    private var retryIfPortfolioFails = false

    /// A content change (or a meaningful sign-in force) asked for a Holdings answer that no run
    /// has STORED yet.
    ///
    /// Such a run exists only for its portfolio leg, but a run whose market leg landed and whose
    /// portfolio leg failed (a 429 from the per-user bucket, a timeout, a degraded 200 refused
    /// over the old group's snapshot) still stamps the 60s throttle — so the swipe home that
    /// followed a group switch was throttled, and the tile kept the OLD group's name, count and
    /// movers until a foreground a minute later. While this is set, `refresh()` skips the
    /// throttle, so every later trigger retries until a run stores. Never drained through
    /// `forcedRefreshPending` in the completion handler: on a hard failure that would loop.
    private var portfolioPending = false

    /// Bumped by every request that sets `portfolioPending`. A run captures it before its first
    /// request and may settle the flag only while it is unchanged: a run that started BEFORE the
    /// latest content change may have read the holdings from before it, and the run queued
    /// behind it (`forcedRefreshPending`) is the one that answers.
    private var portfolioPendingGeneration = 0

    /// Whether the app has decided what credential (if any) to send.
    ///
    /// `UIApplication.didBecomeActiveNotification` is delivered BEFORE the root `.task` runs
    /// `AppState.configure`, so on every cold launch the foreground trigger in `iosApp` fires
    /// before the stored credential is armed. Measured in a launch log — the widget requests
    /// appeared above the appearance probe's `task-pre` line. Both widget routes are
    /// `.signInRequired`, so `APIClient` refuses that call before it leaves the device: a
    /// wasted run and a misleading "both modes failed" warning on every launch.
    ///
    /// Opened by `AppState` the instant the stored credential is armed, which is also where
    /// the cold-launch seed fires — so nothing is lost by dropping the early call.
    private var credentialReady = false

    /// Whether a signed-in session owns the widget right now.
    ///
    /// Opened by `AppState` through `openSession()`: at cold launch only when a stored
    /// credential exists, and in `onAuthenticated` once an identity has settled (AFTER the
    /// account-switch discard, which closes it) — and only while that identity is still the
    /// current one, so a sign-out landing during the sign-in's awaits is not undone
    /// (`AppState.settleWidgetSession`). Closed by `clearForEndedSession()`.
    ///
    /// ⚠️ `credentialReady` is NOT a session gate: it opens once per process and never closes.
    /// Sign-out runs `clearForEndedSession()` synchronously, but `AuthService.signOut` keeps
    /// the bearer ARMED until its two awaited calls (device unregister, `/auth/logout`) finish.
    /// A foreground inside that window — opening Control Center is enough — found the throttle
    /// reset and the gate free, fetched the ex-user's holdings with the still-armed bearer,
    /// wrote them back onto a signed-out Home Screen and minted a fresh 90-day widget token
    /// for a device with no session (auth.md §8a).
    private var sessionOpen = false

    /// Bumped whenever a session ends, so work already in flight refuses to publish.
    /// `inFlight?.cancel()` alone cannot close this: a request whose response has already
    /// been received still resumes its continuation and runs to completion.
    private var sessionEpoch = 0

    /// The pending debounce of a content change. See `contentChanged()`.
    private var contentDebounce: Task<Void, Never>?
    private static let contentDebounceNanoseconds: UInt64 = 1_500_000_000

    /// Called by `AppState` once the stored credential (if any) is on `APIClient`.
    func markCredentialReady() {
        guard !credentialReady else { return }
        credentialReady = true
    }

    /// Called by `AppState` when a signed-in session owns the widget. See `sessionOpen`.
    func openSession() {
        guard !sessionOpen else { return }
        sessionOpen = true
        log.info("widget session opened")
    }

    /// Called by `AppState.settleWidgetSession` when it declines to open the session: the
    /// identity moved (a sign-out, a dead credential, a newer sign-in) while the session it was
    /// settling was still being established. Logged here, beside the widget's other lifecycle
    /// lines, so a tile that stays signed-out after a sign-in race is explainable from logs.
    func noteSettleRefused() {
        log.warning("widget session NOT opened — the identity moved while the signed-in session was being established; the newer state owns the widget")
    }

    private init() {
        observeContentChanges()
    }

    /// Refresh both modes and hand the result to the widget.
    ///
    /// Best-effort by design: this is a background nicety, and a failure must never
    /// surface to the user or block anything. A stale widget is fine; an error alert
    /// because a Home Screen tile could not update is not.
    func refresh(force: Bool = false, identity: Int? = nil) {
        // Nothing may go out before we know who we are, nor for a session that has ended —
        // see `credentialReady` and `sessionOpen`. Dropped rather than queued: `AppState`
        // fires a refresh the moment it opens either gate, so a queued copy would just be a
        // duplicate of that.
        guard credentialReady, sessionOpen else {
            logSkip("refresh")
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
            // flight may have gone out under the PREVIOUS identity, so its result is exactly
            // what needs replacing. Returning here left that result as the final word for the
            // whole session: the Home Screen tile kept the previous answer until some
            // unrelated foreground 60s later. The observed launch only recovered because a
            // 9-second gap let the first run finish first; a fast auth restore would not have.
            //
            // Remember it and re-run on completion rather than starting a second task, so
            // `inFlight` stays a single cancellable handle and `clearForEndedSession()`
            // can still stop everything in one call (auth.md §7).
            if forceIsMeaningful(force, identity) {
                forcedRefreshPending = true
                // Carry the identity across to the drained run, or its completion would stamp
                // `lastRefreshIdentity` with the identity of the run it was queued BEHIND —
                // and the next force would then be suppressed against a stale answer.
                inFlightIdentity = identity
                markPortfolioPending()
            } else if force {
                retryIfPortfolioFails = true
            }
            return
        }

        let meaningful = forceIsMeaningful(force, identity)
        if !meaningful,
           let last = lastRefresh, Date().timeIntervalSince(last) < Self.minimumInterval {
            // A Holdings answer a content change or a sign-in asked for has not been stored
            // yet — see `portfolioPending`. Retry it rather than leave the old group on the
            // tile until a foreground a minute from now.
            guard portfolioPending else { return }
            log.info("widget Holdings answer still pending — refreshing inside the throttle window")
        }
        if meaningful {
            markPortfolioPending()
        }
        inFlightIdentity = identity
        startRefresh()
    }

    /// See `portfolioPending`.
    private func markPortfolioPending() {
        portfolioPending = true
        portfolioPendingGeneration &+= 1
    }

    /// A forced refresh only overrides the throttle when the run it would replace answered
    /// for a DIFFERENT identity. See `lastRefreshIdentity`.
    ///
    /// Both the completed run and the one in flight have to be consulted. Checking only
    /// `lastRefreshIdentity` fails open at exactly the moment that matters: at cold launch
    /// `onAuthenticated` forces a refresh while the seed run is still in flight, so nothing has
    /// been stamped yet, and the force was honoured every time — measured on the simulator as
    /// two widget runs (four requests) per launch even after the identity check was added.
    private func forceIsMeaningful(_ force: Bool, _ identity: Int?) -> Bool {
        guard force else { return false }
        // No identity supplied — the caller cannot vouch for the previous run, so honour it.
        guard let identity else { return true }
        // A run already COMPLETED under this identity; there is nothing to correct.
        if lastRefreshIdentity == identity { return false }
        // A run is IN FLIGHT under this identity; it will answer correctly on its own, so
        // joining it is enough and re-running afterwards would just repeat the same fetch.
        // A CANCELLED run does not count: it belonged to a session that has ended.
        if let running = inFlight, !running.isCancelled, inFlightIdentity == identity {
            return false
        }
        return true
    }

    /// Runs a refresh unconditionally — throttling and joining are decided by the callers.
    private func startRefresh() {
        runID &+= 1
        let id = runID
        let identity = inFlightIdentity
        inFlight = Task { [weak self] in
            await self?.performRefresh(identity: identity)
            guard let self else { return }

            // Only the run that still OWNS the handle may release it. `refresh()` treats a
            // CANCELLED handle as free (sign-out cancels, it does not wait), so a run
            // cancelled by `clearForEndedSession()` can finish after the next session's run
            // has started — and clearing unconditionally nilled THAT run's handle, leaving it
            // out of reach of the next sign-out's `inFlight?.cancel()`. The pending drain
            // belongs to the owner too: a stale run must not consume a request queued behind
            // the live one.
            guard self.runID == id else { return }
            self.inFlight = nil

            // Drain a forced request that landed mid-run. Skipped when this task was
            // cancelled or the session has closed: that means the session ended, and
            // re-running would re-publish the ended session's holdings — the leak the
            // fences in `performRefresh` exist to prevent.
            if self.forcedRefreshPending, !Task.isCancelled, self.sessionOpen {
                self.forcedRefreshPending = false
                self.startRefresh()
            }
        }
    }

    private func performRefresh(identity: Int?) async {
        // Captured BEFORE the first await, and re-checked before every write below.
        // `clearForEndedSession()` bumps it, and cancellation alone cannot fence a sign-out:
        // a response that already arrived still resumes this function after the wipe.
        let epoch = sessionEpoch
        // Captured with the epoch, before the first request: only a fetch that went out AFTER
        // the latest pending request may settle it — see `portfolioPendingGeneration`.
        let pendingGeneration = portfolioPendingGeneration
        let client = APIClient.shared

        // Whose holdings these are: the account the bearer this run sends belongs to. Stamped
        // onto the portfolio slot so `AppState.onAuthenticated` can tell a snapshot left by a
        // DIFFERENT account from this one's (a switch its in-memory guard cannot see).
        let bearer = await client.currentAuthToken()
        let owner = Self.subject(of: bearer)
        log.info("widget refresh starting")

        // Both modes, concurrently — they hit different caches and neither blocks the
        // other. Market is fetched even when signed in: the widget's default
        // configuration is Market, and a user may have one of each on their screen.
        async let market: WidgetMoverSnapshot? = fetch(.getWidgetMarketMover, client: client)
        async let portfolio: WidgetMoverSnapshot? = fetch(.getWidgetPortfolioMover, client: client)

        let (m, fetchedPortfolio) = await (market, portfolio)

        // The bearer can be replaced under a running fetch (a sign-in landing mid-run), and
        // then nobody can say whose holdings the answer holds. Drop it rather than stamp it
        // with the wrong owner; the sign-in's own forced refresh is queued behind this run.
        var ownerChanged = false
        if fetchedPortfolio != nil {
            let bearerNow = await client.currentAuthToken()
            ownerChanged = Self.subject(of: bearerNow) != owner
            if ownerChanged {
                log.warning("widget portfolio answer discarded — the signed-in account changed mid-run")
            }
        }
        let p: WidgetMoverSnapshot? = ownerChanged ? nil : fetchedPortfolio

        // ⚠️ CHECK CANCELLATION AND THE EPOCH BEFORE WRITING.
        //
        // `clearForEndedSession()` cancels this task and wipes the App Group — but
        // cancelling cannot un-finish a child task that already resolved. Without these
        // checks, a response that landed just before sign-out resumed here afterwards and
        // re-published the ended session's holdings to the Home Screen, where they are
        // visible without even unlocking. That is precisely what `.claude/rules/auth.md`
        // §7 exists to prevent. Both are synchronous with the writes below (main actor, no
        // await in between), so nothing can end the session between check and write.
        if Task.isCancelled {
            log.warning("widget refresh cancelled — discarding fetched snapshots")
            return
        }
        guard epoch == sessionEpoch else {
            log.warning("widget refresh outlived its session — discarding fetched snapshots")
            return
        }

        // `write` itself refuses to replace a good snapshot with a degraded one; see the
        // note there. A degraded backend 200 must not blank a working tile.
        if let m { WidgetSnapshotStore.write(mode: .market, snapshot: m) }
        let portfolioStored: Bool
        if let p {
            portfolioStored = WidgetSnapshotStore.write(mode: .portfolio, snapshot: p, owner: owner)
        } else {
            portfolioStored = false
        }

        // A pending Holdings request is settled only by an answer that was actually STORED —
        // not by `p != nil`: a degraded 200 refused over the old group's snapshot is non-nil,
        // and settling on it left the switched group's tile on the old group.
        if portfolioStored, pendingGeneration == portfolioPendingGeneration {
            portfolioPending = false
        }

        // One more run, once, when a sign-in force joined this one and its portfolio leg
        // failed — see `retryIfPortfolioFails`. Reset either way, so it can never loop.
        if p == nil, retryIfPortfolioFails {
            log.info("widget portfolio leg failed under a joined sign-in refresh — re-running once")
            forcedRefreshPending = true
        }
        retryIfPortfolioFails = false

        if m == nil && p == nil {
            // Never silent: without this, a widget that never updates looks like a bug
            // in the widget rather than a failed fetch in the app.
            log.warning("widget refresh produced nothing — both modes failed")
            // Do NOT consume the throttle window on a total failure; the next
            // foreground should be allowed to try again immediately.
            return
        }
        log.info("widget refresh fetched market=\(m != nil) portfolio=\(p != nil), holdings stored=\(portfolioStored)")
        await renewWidgetTokenIfNeeded(client: client, epoch: epoch)

        // Stamped on COMPLETION, not on entry: a run that is cancelled or fails must not burn
        // the next minute's allowance. Re-fenced, because the token renewal awaited — a run
        // whose session ended meanwhile must not throttle the next session's first refresh.
        guard epoch == sessionEpoch, !Task.isCancelled else {
            log.info("widget refresh not stamped — the session ended during the token renewal")
            return
        }
        // The identity stamp does NOT wait for the token below: it records that the
        // identity-bearing leg answered under this identity, which is true either way. The
        // sign-in force stays correct because a missing token leaves the THROTTLE unstamped
        // (and only `clearForEndedSession` removes a token, nilling `lastRefresh` with it), so a
        // force judged redundant still runs instead of being throttled.
        if p != nil {
            lastRefreshIdentity = identity
        }
        // ⚠️ NO WIDGET TOKEN, NO THROTTLE STAMP.
        //
        // The extension renders "Sign in to Caydex" in both modes whenever the App Group holds
        // no widget token (it cannot tell a failed mint from a signed-out device, and must not
        // guess). A mint that failed here — a timeout, a 503 from the strict session lookup —
        // used to be followed by the stamp anyway, so the swipe home a moment later was
        // throttled and a signed-in user's tile said "Sign in" over the fresh snapshots this run
        // just wrote, until a foreground a minute later. Left unstamped, the next trigger runs
        // and retries the mint.
        guard WidgetAPIConfig.widgetToken != nil else {
            log.warning("widget refresh not stamped — no widget token after the renewal; the next trigger retries the mint")
            return
        }
        lastRefresh = Date()
    }

    /// The JWT subject (the user id) of a bearer, or nil when there is none.
    private static func subject(of bearer: String?) -> String? {
        guard let bearer else { return nil }
        return WidgetAPIConfig.jwtSubject(of: bearer)
    }

    // MARK: - Content changes

    /// Holdings, the active group and its name are what the Holdings tile shows, and the
    /// extension never fetches that mode itself — so without these, a switch or an edit
    /// reached the Home Screen only on the next app visit, and a visit inside the 60s
    /// throttle did not refresh it either. `syncTickers` in `PortfolioStore` calls
    /// `contentChanged()` directly; it posts neither notification.
    private func observeContentChanges() {
        let names: [Notification.Name] = [
            PortfolioStore.activeGroupDidChangeNotification,
            PortfolioStore.watchlistDidChangeNotification,
        ]
        for name in names {
            NotificationCenter.default.addObserver(
                forName: name, object: nil, queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in self?.contentChanged() }
            }
        }
    }

    /// What the Holdings tile shows changed: refresh, debounced.
    ///
    /// Forced past the throttle AND past the identity suppression in `forceIsMeaningful` — a
    /// content change is not an identity change, so a completed run under the same identity
    /// says nothing about it. Deliberately NOT routed through `refresh(force: true,
    /// identity: nil)`: joining a run that way overwrites `inFlightIdentity` with nil, the
    /// drained run then stamps nil, and every launch's sign-in force would be honoured again
    /// (two wasted requests). Debounced because one edit can post several signals (a star
    /// posts the watchlist change, then the group sync lands).
    func contentChanged() {
        guard credentialReady, sessionOpen else {
            logSkip("content refresh")
            return
        }
        contentDebounce?.cancel()
        let epoch = sessionEpoch
        contentDebounce = Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.contentDebounceNanoseconds)
            guard !Task.isCancelled, let self, epoch == self.sessionEpoch else { return }
            self.contentDebounce = nil
            self.runContentRefresh()
        }
    }

    /// The debounced half of `contentChanged()`. Joins a running refresh the way a force does
    /// — queued behind it, never cancelling it — but leaves `inFlightIdentity` alone.
    private func runContentRefresh() {
        guard credentialReady, sessionOpen else {
            logSkip("content refresh")
            return
        }
        // Before both branches: whether it queues or starts, this request is settled only by a
        // run that STORES a Holdings answer — see `portfolioPending`.
        markPortfolioPending()
        if let running = inFlight, !running.isCancelled {
            forcedRefreshPending = true
            return
        }
        log.info("widget content changed — refreshing")
        startRefresh()
    }

    // MARK: - Background

    /// The app is leaving the foreground: bring the tile up to date with the session the user
    /// is leaving, under a background-task assertion so the run is not frozen mid-request.
    ///
    /// The moment the user swipes home is exactly when they look at the tile. Throttled like
    /// a foreground (a run completed inside the last minute is current enough); a content
    /// change still waiting out its debounce runs NOW instead, because the process may be
    /// suspended before the debounce would have fired.
    func refreshOnBackground() {
        guard credentialReady, sessionOpen else {
            logSkip("background refresh")
            return
        }
        if let pending = contentDebounce {
            pending.cancel()
            contentDebounce = nil
            runContentRefresh()
        } else {
            refresh()
        }
        // Nothing running means the throttle answered: the tile is already current.
        guard let running = inFlight, !running.isCancelled else { return }
        holdBackgroundAssertionUntilSettled()
    }

    /// Same shape as `Analytics.flushNow`: ended on completion AND on expiration, so the
    /// assertion can never leak (iOS terminates an app that holds one past its allowance).
    private func holdBackgroundAssertionUntilSettled() {
        Task { @MainActor [weak self] in
            let app = UIApplication.shared
            let log = self?.log
            var assertion: UIBackgroundTaskIdentifier = .invalid
            assertion = app.beginBackgroundTask(withName: "caydex.widget.refresh") {
                // Out of time. The run is left alone — it resumes with the process, and its
                // own fences still apply — but the assertion must end now.
                log?.warning("widget background refresh ran out of time — ending the assertion")
                guard assertion != .invalid else { return }
                app.endBackgroundTask(assertion)
                assertion = .invalid
            }
            await self?.waitForRunsToSettle()
            guard assertion != .invalid else { return }
            app.endBackgroundTask(assertion)
            assertion = .invalid
        }
    }

    /// Waits out the run in flight AND one drained behind it (a queued force or content
    /// change). Bounded: each pass needs a newer run to have started.
    private func waitForRunsToSettle() async {
        for _ in 0..<3 {
            guard let running = inFlight, !running.isCancelled else { return }
            await running.value
        }
    }

    private func logSkip(_ trigger: String) {
        let reason = credentialReady ? "no open session" : "credential not armed yet"
        log.info("widget \(trigger, privacy: .public) skipped — \(reason, privacy: .public)")
    }

    // MARK: - The extension's credential

    /// When the published widget token expires, as far as this process knows. A renewal hint
    /// only: the token itself in the App Group is the source of truth, and on a cold launch
    /// this is backfilled from that token's own `exp` (`WidgetAPIConfig.widgetTokenExpiry`).
    private var widgetTokenExpiry: Date?

    /// Renew inside this much of expiry. The token lives 90 days, so a user who opens the app
    /// even once a month never falls off; someone who does not open it for three months loses
    /// the tile, which is the correct outcome rather than a bug.
    private static let widgetTokenRenewWindow: TimeInterval = 30 * 24 * 60 * 60

    /// Mint or renew the token the `CaydexWidgets` extension authenticates with.
    ///
    /// Runs only after a SUCCESSFUL refresh, which means the session is known good — minting off
    /// a failed run would ask for a credential with one we just saw rejected.
    ///
    /// Best-effort by design: a failed RENEWAL leaves the extension on its existing token (still
    /// valid for up to 30 more days). A failed first MINT leaves no token at all, and the tile
    /// then renders signed-out in both modes — so `performRefresh` does not stamp the throttle
    /// while the token is missing, and the next trigger retries. Either way it is logged rather
    /// than surfaced — `auth.md` §6 governs user-INITIATED actions, and nobody tapped anything
    /// to get here.
    private func renewWidgetTokenIfNeeded(client: APIClient, epoch: Int) async {
        let hasToken = WidgetAPIConfig.widgetToken != nil
        // The in-memory hint is empty on every cold launch, and treating "unknown" as "due"
        // minted a fresh, un-revocable 90-day token on EVERY launch — against the backend's
        // stated contract of once per sign-in and once per renewal window.
        if hasToken, widgetTokenExpiry == nil {
            widgetTokenExpiry = WidgetAPIConfig.widgetTokenExpiry
        }
        if hasToken, let expiry = widgetTokenExpiry,
           expiry.timeIntervalSinceNow > Self.widgetTokenRenewWindow {
            return
        }

        do {
            let response = try await client.request(
                endpoint: .getWidgetToken, responseType: WidgetTokenResponse.self
            )
            // If the session ended while this was in flight, publishing now would hand a
            // SIGNED-OUT device a fresh 90-day `scope="widget:market"` credential — one
            // that `clearForEndedSession` had just wiped, and that serves FMP data which
            // End-User Display Rights permit only through an authenticated platform
            // (auth.md §8a). Revocation for this token kind is expiry plus this local
            // clear, so re-publishing it defeats the only mechanism there is. The epoch is
            // the RUN's, captured before its first request — not this call's.
            guard epoch == sessionEpoch else {
                log.info("widget token discarded — the session ended mid-request")
                return
            }
            WidgetAPIConfig.publishWidgetToken(response.token)
            widgetTokenExpiry = ISO8601DateFormatter().date(from: response.expiresAt)
            log.info("widget token published, expires \(response.expiresAt, privacy: .public)")
        } catch {
            // Not fatal, but never silent: if this keeps failing the tile quietly stops
            // self-refreshing 90 days later, and nothing else would say why.
            log.warning("widget token fetch failed: \(String(describing: error))")
        }
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

    // MARK: - Session end

    /// Called when a session ends. See `.claude/rules/auth.md` §7: a device-global store
    /// that survives sign-out hands the next account the previous user's data — and a
    /// portfolio snapshot is visible on the Home Screen without even unlocking the app.
    ///
    /// ⚠️ `clearAll()`, not `clear()`. `clear()` keeps a non-empty MARKET snapshot, which was
    /// right while market data was public and is wrong now: End-User Display Rights permit FMP
    /// data only through an authenticated platform, so a signed-out device must not keep showing
    /// prices. The widget token goes with it — leave it behind and the extension keeps
    /// successfully refreshing FMP data onto a phone with no session.
    ///
    /// Closes the session gate too: until `AppState` opens it again for a settled identity,
    /// no trigger — foreground, background, content change, a queued force — can fetch.
    func clearForEndedSession() {
        sessionEpoch &+= 1
        sessionOpen = false
        inFlight?.cancel()
        // Drop any queued forced refresh and any pending content change too — otherwise the
        // ended session's request re-runs after cancellation and re-publishes their holdings.
        contentDebounce?.cancel()
        contentDebounce = nil
        forcedRefreshPending = false
        retryIfPortfolioFails = false
        // A pending Holdings retry belongs to the session that asked for it.
        portfolioPending = false
        lastRefresh = nil
        widgetTokenExpiry = nil
        WidgetAPIConfig.clearWidgetToken()
        WidgetSnapshotStore.clearAll()
        log.info("widget session closed — snapshots and token cleared")
    }

    /// Called by `AppState` when a launch finds widget state but NO stored session: a restore
    /// to a new phone brings the App Group (widget token + snapshots) along, while the
    /// `...ThisDeviceOnly` session token stays behind. No session "ended" on this device, so
    /// nothing else would ever clear it.
    func clearOrphanedState() {
        log.warning("widget state found with no stored session — clearing it (backup restore or device migration?)")
        clearForEndedSession()
    }
}

/// `GET /api/v1/widget/token` — the extension's market-scoped credential.
///
/// Decoded only by the APP. The extension never sees this type; it reads the raw string the app
/// published into the App Group. Mirrors `WidgetTokenResponse` in `backend/app/schemas/widget.py`.
struct WidgetTokenResponse: Codable, Sendable {
    let token: String
    let expiresAt: String

    enum CodingKeys: String, CodingKey {
        case token
        case expiresAt = "expires_at"
    }
}
