//
//  WidgetRefreshService.swift
//  Caydex
//
//  Fetches the widget payloads and writes them into the shared App Group, and looks after the
//  two credentials the widget extension holds.
//
//  `.claude/rules/auth.md` §8 makes `APIClient` the single token source, and a widget extension
//  is a separate process that cannot reach it — so the extension holds NO session. The app,
//  which does, fetches both modes on every trigger below and writes the slots; the extension
//  renders whatever it finds in the container, and refreshes on its own with two NARROW keys
//  this service obtains:
//    • MARKET mode with the market-scoped widget token (`renewWidgetTokenIfNeeded`, auth.md §8a);
//    • HOLDINGS mode with the per-device Holdings grant (migration 216, auth.md §8c): a 30-day
//      opaque key the server mints ONLY inside a refresh-token rotation (`POST /auth/refresh`
//      with `widget_holdings_grant: true`), revocable server-side, kept in a ThisDeviceOnly
//      Keychain item in the App Group access group (`WidgetHoldingsGrant`). It is wanted only
//      while a Movers widget is installed; this service sets the refresh flag when the grant is
//      missing, refused, someone else's, from another session or due for renewal, forces a
//      refresh for it in the foreground when it is urgent, adopts what the refresh hands back,
//      and revokes every key the device should no longer hold. On iOS 26+ it also uploads the
//      WidgetKit push token so the Insights sweeper can wake the tile.
//
//  The freshness cost of the extension's schedule is handled honestly rather than hidden: the
//  payload carries `as_of` and `market_session`, and the widget renders "At the close" / "As of"
//  rather than implying live data.
//
//  Triggers: the cold-launch seed and the sign-in force (`AppState`), every foreground and
//  background (`iosApp`), and a content change — the active group, its holdings or the
//  watchlist (`contentChanged()`), or an Insights card newer than the tile
//  (`insightCardArrived`). Every one of them is gated on `sessionOpen`.
//

import CryptoKit
import Foundation
import OSLog
import UIKit
import WidgetKit

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
    /// Sign-out runs `clearForEndedSession()` synchronously, and it used to keep the bearer
    /// ARMED until its two awaited calls (device unregister, `/auth/logout`) finished. A
    /// foreground inside that window — opening Control Center is enough — found the throttle
    /// reset and the gate free, fetched the ex-user's holdings with the still-armed bearer,
    /// wrote them back onto a signed-out Home Screen and minted a fresh 90-day widget token
    /// for a device with no session (auth.md §8a). `AuthService.beginSignOut()` now disarms
    /// synchronously; this gate stays, because a refresh in flight can re-arm the ended token
    /// until sign-out's backstop clears it.
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

        // The Holdings grant, last: only after a run whose HOLDINGS leg answered (the session is
        // known good for exactly the data the grant serves), with the market token in place (no
        // token, the extension renders signed-out in both modes and needs no grant), and behind
        // the epoch fence just above. Never delays the stamp; never fails the run.
        if p != nil {
            await assessHoldingsGrant(client: client, epoch: epoch)
        }
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

    // MARK: - Insights cards

    /// The Updates screen just received an Insights card from the network.
    ///
    /// The Holdings tile's head line IS that card's headline when the card agrees with the
    /// move (the backend's news lean, owner 2026-10-30), and the Market tile leads with the
    /// market card's. But the extension never fetches Holdings, so a new run of the card
    /// reached the tile only at the next foreground or swipe home — and not even then inside
    /// the 60 s throttle. A card written after the stored snapshot was BUILT (`asOf`) is one
    /// the tile cannot be showing yet: refresh, as a content change. Self-limiting: once the
    /// refresh stores, the snapshot is newer than the card.
    func insightCardArrived(scope: String, generatedAt: Date?, isAIGenerated: Bool) {
        // Only an AI card can become the tile's line (`_session_news_lean`), and an
        // unparseable stamp says nothing about which is newer.
        guard isAIGenerated, let generatedAt, let stored = WidgetSnapshotStore.read() else { return }
        let snapshot: WidgetMoverSnapshot?
        if scope == UpdatesScope.market {
            snapshot = stored.market
        } else if let head = stored.portfolio?.headlineMover?.ticker,
                  head.caseInsensitiveCompare(scope) == .orderedSame {
            // Only the head mover's line is a card headline; the other rows are numbers.
            snapshot = stored.portfolio
        } else {
            snapshot = nil
        }
        guard let snapshot, generatedAt > snapshot.asOf else { return }
        log.info("widget: an Insights card newer than the tile arrived — refreshing")
        contentChanged()
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

    // MARK: - The Holdings grant (migration 216, auth.md §8c)

    /// Renew when fewer than this many seconds of the grant's 30 days remain: about one renewal
    /// a day for an app opened daily, so the tile keeps refreshing 29-30 days past the last open.
    private static let grantRenewWindow: TimeInterval = 29 * 24 * 60 * 60

    /// When a forced grant refresh retries, measured from its FIRST attempt. Both inside the
    /// server's 120 s refresh grace window: a refresh whose answer was lost has already rotated
    /// the token server-side, and only a replay inside that window gets the session back (a
    /// sibling). Outside it the replay is REUSE, and the server ends the session (auth.md §8b).
    /// While the access token is still good nothing else would refresh for up to 60 minutes —
    /// which is why these retries exist, and why they are not gated on the foreground.
    private static let forcedGrantRetryOffsets: [TimeInterval] = [20, 90]

    /// How long forced grant refreshes wait after flagged refreshes came back without a grant
    /// (the switch is off, the migration is not applied, the mint limiter tripped, a grace
    /// replay): 10 min → 1 h → 6 h → 24 h. Reset by an adoption. Natural refreshes keep
    /// carrying the flag regardless — they cost nothing extra.
    private static let grantBackoffSteps: [TimeInterval] = [10 * 60, 60 * 60, 6 * 60 * 60, 24 * 60 * 60]
    private static let grantBackoffStepKey = "widget.holdings.grant.backoff.step.v1"
    private static let grantBackoffUntilKey = "widget.holdings.grant.backoff.until.v1"

    /// The last push-token upload that STORED: sha256(grant id | token | environment), and when.
    /// Re-sent when either changes, and at least daily: the daily re-send keeps the server's
    /// `push_token_updated_at` fresh (its APNs-410 fence) and heals a server-side clear.
    private static let pushFingerprintKey = "widget.push.fingerprint.v1"
    private static let pushFingerprintAtKey = "widget.push.fingerprint.at.v1"
    private static let pushTokenResendInterval: TimeInterval = 24 * 60 * 60

    /// A push-token answer calling a grant adopted less than this long ago unusable is a server
    /// anomaly, not a revocation: back off instead of re-minting at once (a re-mint loop).
    private static let freshGrantUnusableWindow: TimeInterval = 10 * 60

    /// The fingerprint this process knows to be the LIVE marker: set by an adoption and whenever
    /// `assessHoldingsGrant` reads the live grant back; cleared with the session. Never swept.
    private var liveGrantFingerprint: String?

    /// Grants between their Keychain write and their marker. A sweep that enumerated the Keychain
    /// in that window would otherwise take them for leftovers and revoke them.
    private var adoptingGrantFingerprints: Set<String> = []

    /// A grant the server called unusable on a push-token upload (`grant_usable: false`).
    private var unusableGrantFingerprint: String?

    /// When this process last adopted a grant, for `freshGrantUnusableWindow`.
    private var lastGrantAdoption: Date?

    /// The forced refresh for an urgent grant, with its retries — one at a time, owned by id so a
    /// run cancelled at sign-out cannot clear the next session's handle.
    private var forcedGrantRefresh: Task<Void, Never>?
    private var forcedGrantRefreshID = 0

    /// What the device's grant needs. Every case but `.current` and `.renewal` is URGENT.
    private enum HoldingsGrantNeed: String {
        case current
        case renewal
        case missing
        case otherAccount = "another account's"
        case refused = "refused by the server"
        case expired
        case otherSession = "minted for another session"
        case unusable = "no longer accepted by the server"

        var isUrgent: Bool { self != .current && self != .renewal }
    }

    /// Decide what the device's Holdings grant needs, after a run whose Holdings leg answered.
    ///
    /// • No Movers widget installed — a SUCCESSFUL WidgetKit answer with no Movers entry: no grant
    ///   is wanted, so the marker goes and every key is revoked as `widget_removed`. A THROWN
    ///   query keeps everything as it is: a transient WidgetKit error must never kill a live grant.
    /// • URGENT — none, another account's, refused, expired, minted for another session (a
    ///   password change starts a new family) or called unusable by the server: flag the next
    ///   refresh AND force one now, foreground only.
    /// • RENEWAL — fewer than 29 days left: flag the next refresh; it rides the next natural one.
    /// • Live (or renewing): retry any revocation still pending, keep the push token in sync.
    private func assessHoldingsGrant(client: APIClient, epoch: Int) async {
        let installed: Bool
        do {
            let configurations = try await WidgetCenter.shared.currentConfigurations()
            installed = configurations.contains { $0.kind == WidgetSharedConfig.moversKind }
        } catch {
            log.warning("widget holdings grant: WidgetKit could not list the installed widgets — grant state kept as is: \(String(describing: error), privacy: .public)")
            return
        }
        guard epoch == sessionEpoch, sessionOpen, !Task.isCancelled else { return }
        guard installed else {
            retireHoldingsGrantForRemovedWidget()
            return
        }

        let bearer = await client.currentAuthToken()
        guard epoch == sessionEpoch, sessionOpen, !Task.isCancelled else { return }
        guard let bearer, let owner = Self.subject(of: bearer), let sid = Self.sessionId(of: bearer) else {
            log.info("widget holdings grant: the armed bearer names no account or no session — not assessed")
            return
        }

        // OFF main: the grant is a Keychain item (securityd never on the main actor).
        let read = await Task.detached(priority: .utility) { () -> (grant: WidgetHoldingsGrant.Grant?, refused: Bool) in
            let grant = WidgetHoldingsGrant.current()
            return (grant, grant.map { WidgetHoldingsGrant.isRefused($0.fp) } ?? false)
        }.value
        guard epoch == sessionEpoch, sessionOpen, !Task.isCancelled else { return }

        let need = Self.holdingsGrantNeed(
            grant: read.grant, refused: read.refused, unusableFingerprint: unusableGrantFingerprint,
            owner: owner, sessionId: sid, now: Date()
        )
        guard let grant = read.grant, !need.isUrgent else {
            if need == .otherAccount {
                // An account switch the session teardown never saw (a sign-in from `.restoring`):
                // the previous account's key must not stay presented to the extension, nor live.
                WidgetHoldingsGrant.removeMarker()
                liveGrantFingerprint = nil
                unusableGrantFingerprint = nil
                Self.clearPushFingerprint()
                sweepHoldingsGrantKeys(keeping: nil, reason: "app_revoked")
            } else if let grant = read.grant {
                liveGrantFingerprint = grant.fp
            }
            WidgetHoldingsGrant.requestOnNextRefresh = true
            startForcedGrantRefreshIfAllowed(why: need.rawValue)
            return
        }
        liveGrantFingerprint = grant.fp
        if need == .renewal {
            if !WidgetHoldingsGrant.requestOnNextRefresh {
                log.info("widget holdings grant \(grant.grantId, privacy: .public): under 29 days left — the next refresh asks for a renewal")
            }
            WidgetHoldingsGrant.requestOnNextRefresh = true
        } else {
            WidgetHoldingsGrant.requestOnNextRefresh = false
        }
        // Revocations a previous pass could not confirm (offline sign-out, a dropped answer).
        sweepHoldingsGrantKeys(keeping: grant.fp, reason: "app_revoked")
        await syncWidgetPushTokenIfNeeded(client: client, epoch: epoch, grant: grant)
    }

    /// The pure decision behind `assessHoldingsGrant`. Accounts and sessions are UUIDs, compared
    /// without regard to case.
    private static func holdingsGrantNeed(
        grant: WidgetHoldingsGrant.Grant?, refused: Bool, unusableFingerprint: String?,
        owner: String, sessionId: String, now: Date
    ) -> HoldingsGrantNeed {
        guard let grant else { return .missing }
        if grant.userId.lowercased() != owner.lowercased() { return .otherAccount }
        if refused { return .refused }
        if grant.expiresAt <= now { return .expired }
        if grant.sessionId.lowercased() != sessionId.lowercased() { return .otherSession }
        if let unusableFingerprint, grant.fp == unusableFingerprint { return .unusable }
        if grant.expiresAt.timeIntervalSince(now) < grantRenewWindow { return .renewal }
        return .current
    }

    /// No Movers widget is installed: no grant is wanted (D10). The marker goes synchronously,
    /// then every key is revoked as `widget_removed` and deleted once the server confirms.
    private func retireHoldingsGrantForRemovedWidget() {
        let removed = WidgetHoldingsGrant.removeMarker()
        liveGrantFingerprint = nil
        unusableGrantFingerprint = nil
        WidgetHoldingsGrant.requestOnNextRefresh = false
        Self.clearPushFingerprint()
        if let removed {
            log.info("widget holdings grant retired — no Movers widget is installed (marker \(String(removed.prefix(8)), privacy: .public)…)")
        }
        sweepHoldingsGrantKeys(keeping: nil, reason: "widget_removed")
    }

    // MARK: Forced refresh for an urgent grant

    /// Force a refresh that asks for a grant — ONLY in the foreground (never from a background
    /// trigger: the refresh rotates the session, and a lost answer must be replayable inside the
    /// grace window), one at a time, and not while backing off.
    private func startForcedGrantRefreshIfAllowed(why: String) {
        guard sessionOpen else { return }
        guard forcedGrantRefresh == nil else { return }
        guard UIApplication.shared.applicationState == .active else {
            log.info("widget holdings grant needed (\(why, privacy: .public)) — no forced refresh outside the foreground; the next refresh asks for it")
            return
        }
        if let until = Self.grantBackoffUntil(), until > Date() {
            log.info("widget holdings grant needed (\(why, privacy: .public)) — forced refresh backing off until \(until, privacy: .public)")
            return
        }
        forcedGrantRefreshID &+= 1
        let id = forcedGrantRefreshID
        let epoch = sessionEpoch
        log.info("widget holdings grant needed (\(why, privacy: .public)) — forcing a refresh that asks for one")
        forcedGrantRefresh = Task { [weak self] in
            await self?.runForcedGrantRefresh(epoch: epoch)
            guard let self, self.forcedGrantRefreshID == id else { return }
            self.forcedGrantRefresh = nil
        }
    }

    /// Its own background-task assertion, ended on completion AND on expiration (the same shape
    /// as `holdBackgroundAssertionUntilSettled`): a retry that a swipe home would freeze is the
    /// one replaying a lost answer.
    private func runForcedGrantRefresh(epoch: Int) async {
        let app = UIApplication.shared
        let log = self.log
        var assertion: UIBackgroundTaskIdentifier = .invalid
        assertion = app.beginBackgroundTask(withName: "caydex.widget.holdings-grant") {
            log.warning("widget holdings grant: the forced refresh ran out of background time — ending the assertion")
            guard assertion != .invalid else { return }
            app.endBackgroundTask(assertion)
            assertion = .invalid
        }
        await attemptForcedGrantRefreshes(epoch: epoch)
        guard assertion != .invalid else { return }
        app.endBackgroundTask(assertion)
        assertion = .invalid
    }

    /// One attempt, then retries at +20 s and +90 s on `.transientFailure` only. The outcome is
    /// only ever matched here: a rejected refresh never ends the session from this path — the
    /// 401 interceptor alone does that (auth.md §3/§5).
    private func attemptForcedGrantRefreshes(epoch: Int) async {
        let client = APIClient.shared
        let started = Date()
        let bearerAtStart = await client.currentAuthToken()
        for attempt in 0...Self.forcedGrantRetryOffsets.count {
            if attempt > 0 {
                let wait = Self.forcedGrantRetryOffsets[attempt - 1] - Date().timeIntervalSince(started)
                if wait > 0 {
                    try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
                }
                // A retry exists only to replay a lost answer. A bearer that moved means a refresh
                // landed meanwhile (another request's, a restore): nothing is left to replay.
                guard await client.currentAuthToken() == bearerAtStart else {
                    log.info("widget holdings grant: a refresh landed during the retry wait — no retry needed")
                    return
                }
            }
            guard !Task.isCancelled, epoch == sessionEpoch, sessionOpen else {
                log.info("widget holdings grant: forced refresh stopped — the session ended")
                return
            }
            let outcome = await client.refreshForWidgetGrant()
            guard !Task.isCancelled, epoch == sessionEpoch else { return }
            switch outcome {
            case .refreshed:
                // `AuthService.refreshToken()` handed a minted grant to `adoptHoldingsGrant`, or
                // reported none (`noteHoldingsGrantNotIssued`). Either way this refresh is done.
                return
            case .credentialRejected:
                log.warning("widget holdings grant: the forced refresh was rejected — no grant; the next request's 401 path decides the session")
                stepGrantBackoff()
                return
            case .transientFailure:
                log.info("widget holdings grant: forced refresh attempt \(attempt + 1, privacy: .public) did not complete")
            }
        }
        stepGrantBackoff()
    }

    private func stepGrantBackoff() {
        let defaults = UserDefaults.standard
        let step = min(max(0, defaults.integer(forKey: Self.grantBackoffStepKey)), Self.grantBackoffSteps.count - 1)
        let delay = Self.grantBackoffSteps[step]
        defaults.set(step + 1, forKey: Self.grantBackoffStepKey)
        defaults.set(Date().addingTimeInterval(delay).timeIntervalSince1970, forKey: Self.grantBackoffUntilKey)
        log.info("widget holdings grant: forced refreshes back off \(Int(delay / 60), privacy: .public) min")
    }

    private static func resetGrantBackoff() {
        UserDefaults.standard.removeObject(forKey: grantBackoffStepKey)
        UserDefaults.standard.removeObject(forKey: grantBackoffUntilKey)
    }

    /// nil when no back-off is set — or when the stored instant is further ahead than the longest
    /// step allows (a device clock moved backwards must not freeze the grant for days).
    private static func grantBackoffUntil() -> Date? {
        let raw = UserDefaults.standard.double(forKey: grantBackoffUntilKey)
        guard raw > 0 else { return nil }
        let until = Date(timeIntervalSince1970: raw)
        guard let longest = grantBackoffSteps.last,
              until.timeIntervalSinceNow <= longest + 60 else { return nil }
        return until
    }

    // MARK: Adoption

    /// A refresh that asked for a grant came back without one. Called by `AuthService`.
    func noteHoldingsGrantNotIssued() {
        guard sessionOpen else { return }
        log.info("widget holdings grant: the refresh asked for one and none was issued (switch off, migration not applied, limiter, or not a rotation)")
        stepGrantBackoff()
    }

    /// The grant `POST /auth/refresh` just minted, handed over by `AuthService.refreshToken()`
    /// AFTER its epoch fence, `saveTokens` and `setAuthToken`. Synchronous and non-throwing: the
    /// refresh never waits on the widget. Fenced again before anything is stored — the widget
    /// session must be open, under the same epoch, and the ARMED bearer's subject and session
    /// must be the grant's. A fenced-out key is LIVE on the server, so it is revoked, not dropped.
    func adoptHoldingsGrant(_ payload: WidgetHoldingsGrantPayload, owner: String) {
        let epoch = sessionEpoch
        guard WidgetHoldingsGrant.isWellFormedToken(payload.grantToken) else {
            log.error("widget holdings grant \(payload.grantId, privacy: .public): the refresh answer carried a malformed key — not adopted")
            stepGrantBackoff()
            return
        }
        let expiresAt = Self.parseGrantExpiry(payload.expiresAt)
        let grant = Self.grant(from: payload, owner: owner, expiresAt: expiresAt ?? Date())
        guard let expiresAt, expiresAt > Date(), !payload.grantId.isEmpty, !payload.sessionId.isEmpty, !owner.isEmpty else {
            log.error("widget holdings grant \(payload.grantId, privacy: .public): unreadable expiry, id, session or owner (expires_at \(payload.expiresAt, privacy: .public)) — not adopted")
            discardHoldingsGrant(grant, why: "its fields are malformed")
            stepGrantBackoff()
            return
        }
        guard sessionOpen else {
            discardHoldingsGrant(grant, why: "no widget session is open")
            return
        }
        adoptingGrantFingerprints.insert(grant.fp)
        Task { [weak self] in
            guard let self else { return }
            await self.publishHoldingsGrant(grant, epoch: epoch)
            self.adoptingGrantFingerprints.remove(grant.fp)
        }
    }

    /// Keychain item first, then the marker, then a reload — the order the extension relies on:
    /// a marker never names a key that is not there yet. Then every OTHER key is revoked by key
    /// (the predecessor of a renewal, an earlier account's, a leftover), and the push token
    /// follows the new grant.
    private func publishHoldingsGrant(_ grant: WidgetHoldingsGrant.Grant, epoch: Int) async {
        let bearer = await APIClient.shared.currentAuthToken()
        guard epoch == sessionEpoch, sessionOpen else {
            discardHoldingsGrant(grant, why: "the widget session ended before adoption")
            return
        }
        guard let bearer, Self.subject(of: bearer)?.lowercased() == grant.userId.lowercased() else {
            discardHoldingsGrant(grant, why: "it names another account than the armed session")
            return
        }
        guard let sid = Self.sessionId(of: bearer), sid.lowercased() == grant.sessionId.lowercased() else {
            discardHoldingsGrant(grant, why: "it names another session than the armed one")
            stepGrantBackoff()
            return
        }

        let failure = await Task.detached(priority: .utility) { () -> String? in
            do {
                try WidgetHoldingsGrant.store(grant)
                return nil
            } catch {
                return String(describing: error)
            }
        }.value
        if let failure {
            log.error("widget holdings grant \(grant.grantId, privacy: .public): the Keychain refused the key — not adopted: \(failure, privacy: .public)")
            discardHoldingsGrant(grant, why: "it could not be stored")
            stepGrantBackoff()
            return
        }
        // Re-fenced after the Keychain await: a session that ended meanwhile gets no marker, and
        // the key it stored is revoked and deleted (the compensating half of the write above).
        guard epoch == sessionEpoch, sessionOpen else {
            log.info("widget holdings grant \(grant.grantId, privacy: .public): the session ended during the Keychain write — revoking it")
            revokeThenDeleteDetached([grant], reason: "app_revoked")
            return
        }
        WidgetHoldingsGrant.setMarker(grant.fp)
        liveGrantFingerprint = grant.fp
        unusableGrantFingerprint = nil
        lastGrantAdoption = Date()
        WidgetHoldingsGrant.requestOnNextRefresh = false
        Self.resetGrantBackoff()
        WidgetSnapshotStore.reloadTimelines()
        log.info("widget holdings grant \(grant.grantId, privacy: .public) adopted — expires \(grant.expiresAt, privacy: .public)")
        sweepHoldingsGrantKeys(keeping: grant.fp, reason: "app_revoked")
        await syncWidgetPushTokenIfNeeded(client: APIClient.shared, epoch: epoch, grant: grant)
    }

    /// A grant the refresh carried for a session that changed while it was in flight. Called by
    /// `AuthService.refreshToken()` after its epoch fence refused the answer.
    func discardHoldingsGrant(_ payload: WidgetHoldingsGrantPayload, owner: String) {
        guard WidgetHoldingsGrant.isWellFormedToken(payload.grantToken) else {
            log.error("widget holdings grant \(payload.grantId, privacy: .public): a malformed key arrived for an ended session — nothing to revoke")
            return
        }
        let grant = Self.grant(from: payload, owner: owner, expiresAt: Self.parseGrantExpiry(payload.expiresAt) ?? Date())
        discardHoldingsGrant(grant, why: "the session changed while the refresh was in flight")
    }

    /// Revoke a key this device will not use. It is stored first (no marker: the extension never
    /// sees it), so a revoke that is not confirmed leaves it for a later sweep instead of
    /// stranding a live key nobody can revoke.
    private func discardHoldingsGrant(_ grant: WidgetHoldingsGrant.Grant, why: String) {
        log.warning("widget holdings grant \(grant.grantId, privacy: .public) not adopted — \(why, privacy: .public); revoking it by key")
        let log = self.log
        Task.detached(priority: .utility) {
            do {
                try WidgetHoldingsGrant.store(grant)
            } catch {
                log.warning("widget holdings grant \(grant.grantId, privacy: .public): not kept for a revoke retry: \(String(describing: error), privacy: .public)")
            }
            await Self.revokeThenDelete([grant], reason: "app_revoked", log: log)
        }
    }

    // MARK: Revocation

    /// Revoke by key, off main, every grant item but `keep`, the live one and any being adopted.
    /// The enumeration is detached; the decision is made back on the main actor, after it, so
    /// an adoption that stored its key meanwhile is seen (`adoptingGrantFingerprints`).
    private func sweepHoldingsGrantKeys(keeping keep: String?, reason: String) {
        let log = self.log
        Task { [weak self] in
            let keys = await Task.detached(priority: .utility) {
                WidgetHoldingsGrant.pendingKeys(excluding: keep)
            }.value
            guard let self else { return }
            let doomed = keys.filter { key in
                key.fp != keep && key.fp != self.liveGrantFingerprint
                    && !self.adoptingGrantFingerprints.contains(key.fp)
            }
            guard !doomed.isEmpty else { return }
            log.info("widget holdings grant: revoking \(doomed.count, privacy: .public) key(s) this device no longer uses (\(reason, privacy: .public))")
            self.revokeThenDeleteDetached(doomed, reason: reason)
        }
    }

    /// `revokeThenDelete` on a detached task: the delete is a Keychain write, never on the main actor.
    private func revokeThenDeleteDetached(_ grants: [WidgetHoldingsGrant.Grant], reason: String) {
        let log = self.log
        Task.detached(priority: .utility) {
            await Self.revokeThenDelete(grants, reason: reason, log: log)
        }
    }

    /// Revoke THEN delete (D16): the item stays until the server answers 2xx or a refusal code
    /// (`WidgetHoldingsFetcher.revoke` → true), so an offline sign-out keeps the key for the next
    /// retry instead of stranding a live server row for 30 days. Call it only from a detached
    /// task: the delete is a Keychain write.
    nonisolated private static func revokeThenDelete(
        _ grants: [WidgetHoldingsGrant.Grant], reason: String, log: Logger
    ) async {
        for grant in grants {
            if await WidgetHoldingsFetcher.revoke(grant, reason: reason) {
                WidgetHoldingsGrant.delete(fingerprint: grant.fp)
                log.info("widget holdings grant \(grant.grantId, privacy: .public) revoked (\(reason, privacy: .public)) and deleted")
            } else {
                log.warning("widget holdings grant \(grant.grantId, privacy: .public): revoke not confirmed — key kept for a retry at a later refresh")
            }
        }
    }

    /// `AuthService.purgeSessionLeftByAnEarlierInstall()`'s `.fresh` branch: Keychain items outlive
    /// the app, so an earlier install's grant keys are still here — inert (the App Group, with its
    /// marker, went with the app) but live on the server. Revoked by key, then deleted, off main.
    nonisolated static func revokeHoldingsGrantKeysLeftByAnEarlierInstall() {
        let log = Logger(subsystem: "com.phan.caydex", category: "widget")
        Task.detached(priority: .utility) {
            let keys = WidgetHoldingsGrant.pendingKeys(excluding: nil)
            guard !keys.isEmpty else { return }
            log.notice("fresh install: revoking \(keys.count, privacy: .public) Holdings widget grant key(s) an earlier install left in the Keychain")
            await Self.revokeThenDelete(keys, reason: "app_revoked", log: log)
        }
    }

    /// `clearForEndedSession`'s half for the grant, AFTER the marker is gone and the slots are
    /// cleared: drop every per-session flag (auth.md §7 — device-global keys die with the
    /// session), stop a forced refresh, and revoke every key — none belongs to a live session.
    private func endHoldingsGrantForEndedSession(endedFingerprint: String?) {
        forcedGrantRefresh?.cancel()
        forcedGrantRefresh = nil
        forcedGrantRefreshID &+= 1
        liveGrantFingerprint = nil
        unusableGrantFingerprint = nil
        lastGrantAdoption = nil
        WidgetHoldingsGrant.requestOnNextRefresh = false
        Self.resetGrantBackoff()
        Self.clearPushFingerprint()
        if let endedFingerprint {
            log.info("widget holdings grant marker removed (\(String(endedFingerprint.prefix(8)), privacy: .public)…) — revoking the ended session's keys")
        }
        sweepHoldingsGrantKeys(keeping: nil, reason: "app_revoked")
    }

    // MARK: Push token (iOS 26+)

    /// Upload the WidgetKit push token onto the grant's row (`PUT /widget/push-token`), when the
    /// grant, the token or the environment changed, or a day has passed. The APP uploads it; the
    /// extension's push handler only logs (it holds no session). Best-effort: logged, never
    /// surfaced — nobody tapped anything (auth.md §6).
    private func syncWidgetPushTokenIfNeeded(client: APIClient, epoch: Int, grant: WidgetHoldingsGrant.Grant) async {
        guard #available(iOS 26.0, *) else { return }
        let info = await WidgetCenter.shared.currentPushInfo
        guard epoch == sessionEpoch, sessionOpen, !Task.isCancelled else { return }
        let token: String? = info.flatMap { info in
            info.token.isEmpty ? nil : info.token.map { String(format: "%02x", $0) }.joined()
        }
        // The server stores 32-400 hex characters; anything else would be refused (422) on every
        // refresh. Not sent, and never silent.
        if let token, !(32...400).contains(token.count) {
            log.error("widget push token has \(token.count, privacy: .public) hex characters — outside what the server stores; not uploaded")
            return
        }
        let environment = PushNotificationManager.apnsEnvironment
        let fingerprint = Self.pushFingerprint(grantId: grant.grantId, token: token, environment: environment)
        let defaults = UserDefaults.standard
        if defaults.string(forKey: Self.pushFingerprintKey) == fingerprint {
            let at = defaults.double(forKey: Self.pushFingerprintAtKey)
            let age = Date().timeIntervalSince1970 - at
            if at > 0, age >= 0, age < Self.pushTokenResendInterval { return }
        }
        do {
            let response = try await client.request(
                endpoint: .registerWidgetPushToken(grantId: grant.grantId, token: token, environment: environment),
                responseType: WidgetPushTokenResponse.self
            )
            guard epoch == sessionEpoch, sessionOpen, !Task.isCancelled else {
                log.info("widget push token answer discarded — the session ended mid-request")
                return
            }
            if response.stored {
                defaults.set(fingerprint, forKey: Self.pushFingerprintKey)
                defaults.set(Date().timeIntervalSince1970, forKey: Self.pushFingerprintAtKey)
                log.info("widget push token \(token == nil ? "cleared" : "stored", privacy: .public) for grant \(grant.grantId, privacy: .public)")
            } else {
                log.warning("widget push token not stored for grant \(grant.grantId, privacy: .public) — retried at the next refresh")
            }
            if response.grantUsable == false {
                grantCalledUnusable(grant)
            }
        } catch {
            guard !Task.isCancelled else { return }
            log.warning("widget push token upload failed for grant \(grant.grantId, privacy: .public): \(String(describing: error), privacy: .public)")
        }
    }

    /// The server's register answered `grant_usable: false` — the grant is revoked, expired,
    /// evicted or superseded there. Urgent, unless this process adopted it moments ago: a fresh
    /// grant the server already rejects is an anomaly, and re-minting at once would loop.
    private func grantCalledUnusable(_ grant: WidgetHoldingsGrant.Grant) {
        unusableGrantFingerprint = grant.fp
        WidgetHoldingsGrant.requestOnNextRefresh = true
        if let adopted = lastGrantAdoption, Date().timeIntervalSince(adopted) < Self.freshGrantUnusableWindow {
            log.error("widget holdings grant \(grant.grantId, privacy: .public): the server rejects a grant adopted under 10 minutes ago — backing off instead of re-minting")
            stepGrantBackoff()
            return
        }
        log.warning("widget holdings grant \(grant.grantId, privacy: .public): the server no longer accepts it — asking for a new one")
        startForcedGrantRefreshIfAllowed(why: HoldingsGrantNeed.unusable.rawValue)
    }

    private static func clearPushFingerprint() {
        UserDefaults.standard.removeObject(forKey: pushFingerprintKey)
        UserDefaults.standard.removeObject(forKey: pushFingerprintAtKey)
    }

    private static func pushFingerprint(grantId: String, token: String?, environment: String) -> String {
        let material = "\(grantId)|\(token ?? "none")|\(environment)"
        return SHA256.hash(data: Data(material.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    // MARK: Grant helpers

    /// The `sid` claim (the 214 session family) of a bearer, or nil. Reads only; verifies nothing.
    private static func sessionId(of bearer: String) -> String? {
        WidgetAPIConfig.jwtClaims(of: bearer)?["sid"] as? String
    }

    /// `expires_at` is text ("YYYY-MM-DDTHH:MM:SSZ"), parsed here — never by the client's
    /// `.iso8601` date strategy, so a format drift drops the grant, never the refresh. A
    /// fractional-seconds stamp is accepted too: one serializer change on the server must not
    /// turn every mint into a revoke.
    private static func parseGrantExpiry(_ raw: String) -> Date? {
        if let date = ISO8601DateFormatter().date(from: raw) { return date }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: raw)
    }

    private static func grant(
        from payload: WidgetHoldingsGrantPayload, owner: String, expiresAt: Date
    ) -> WidgetHoldingsGrant.Grant {
        WidgetHoldingsGrant.Grant(
            token: payload.grantToken,
            grantId: payload.grantId,
            userId: owner,
            sessionId: payload.sessionId,
            expiresAt: expiresAt,
            fp: WidgetHoldingsGrant.fingerprint(of: payload.grantToken)
        )
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
    ///
    /// ⚠️ The Holdings grant MARKER goes first, synchronously, right after the epoch bump and
    /// BEFORE the token and the snapshots: the extension uses a key only while the App Group's
    /// marker names it, so from this line on no reload can fetch the ended account's holdings
    /// (the slot clear below reloads the timelines). The KEY itself is revoked by key and only
    /// then deleted, off main — a revoke that is not confirmed keeps it for the next retry,
    /// because an offline sign-out (where the logout, and so the server's trigger, never ran)
    /// would otherwise strand a live 30-day key no device can revoke.
    func clearForEndedSession() {
        sessionEpoch &+= 1
        let endedGrantFingerprint = WidgetHoldingsGrant.removeMarker()
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
        endHoldingsGrantForEndedSession(endedFingerprint: endedGrantFingerprint)
        log.info("widget session closed — snapshots, token and holdings grant marker cleared")
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

/// The Holdings widget grant inside `POST /auth/refresh`'s answer (`TokenResponse
/// .widget_holdings_grant`) — mirrors `WidgetHoldingsGrantOut` in `backend/app/schemas/auth.py`
/// (migration 216). Decoded only by the APP, LOSSILY, inside `AuthResponse`, and handed to
/// `WidgetRefreshService.adoptHoldingsGrant`. `nonisolated`: `AuthResponse`'s decoder runs on
/// `APIClient`'s actor. `grantToken` is the key itself — never logged; `description` omits it.
nonisolated struct WidgetHoldingsGrantPayload: Decodable, Sendable, CustomStringConvertible {
    let grantToken: String
    let grantId: String
    /// "YYYY-MM-DDTHH:MM:SSZ", kept as text and parsed by `ISO8601DateFormatter` in adoption —
    /// never by the client's `.iso8601` date strategy.
    let expiresAt: String
    /// The 214 session family the grant was minted in (the bearer's `sid`).
    let sessionId: String

    enum CodingKeys: String, CodingKey {
        case grantToken = "grant_token"
        case grantId = "grant_id"
        case expiresAt = "expires_at"
        case sessionId = "session_id"
    }

    var description: String {
        "WidgetHoldingsGrantPayload(grant_id: \(grantId), expires_at: \(expiresAt), grant_token: <redacted>)"
    }
}

/// `PUT /widget/push-token` — mirrors `WidgetPushTokenResponse` in `backend/app/schemas/widget.py`.
nonisolated struct WidgetPushTokenResponse: Decodable, Sendable {
    /// The token is on the grant row now. false: a store error — retried at the next refresh.
    let stored: Bool
    /// false: the grant is no longer live server-side (revoked, expired, evicted, superseded) —
    /// the app asks for a new one. Optional on purpose: an absent field reads as usable, so a
    /// backend that ever omits it can never push every device into a re-mint.
    let grantUsable: Bool?

    enum CodingKeys: String, CodingKey {
        case stored
        case grantUsable = "grant_usable"
    }
}
