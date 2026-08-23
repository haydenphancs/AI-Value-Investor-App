//
//  AppState.swift
//  ios
//
//  Global Application State - Single Source of Truth
//
//  Architecture Decision:
//  Using @Observable (iOS 17+) for global state instead of:
//  - EnvironmentObject: Requires @Published boilerplate
//  - Singleton: Hard to test, implicit dependencies
//  - Full DI Container: Overkill for solo developer
//
//  Benefits:
//  - Simple, no boilerplate
//  - Automatic UI updates
//  - Easy to pass via Environment
//  - Works with existing @StateObject ViewModels
//

import SwiftUI
import Combine

// MARK: - Global App State

/// Central state container for data that needs to be shared across the app.
/// Injected via `.environment()` at the app root.
///
/// Usage in Views:
/// ```swift
/// struct MyView: View {
///     @Environment(AppState.self) private var appState
///     var body: some View {
///         Text("Credits: \(appState.user.credits)")
///     }
/// }
/// ```
///
/// Usage in ViewModels:
/// ```swift
/// @MainActor
/// class MyViewModel: ObservableObject {
///     private let appState: AppState
///     init(appState: AppState) {
///         self.appState = appState
///     }
/// }
/// ```
@Observable
@MainActor
final class AppState {

    // MARK: - Sub-States

    /// Authentication state
    var auth = AuthState()

    /// Current user state (profile, credits, tier)
    var user = UserState()

    /// Watchlist and tracked stocks
    var watchlist = WatchlistState()

    /// Research reports state
    var research = ResearchState()

    // MARK: - Global UI State

    /// Network connectivity status
    var isOnline: Bool = true

    /// Global loading indicator
    var isLoading: Bool = false

    /// Global error to display
    var currentError: AppError?

    /// Toast message to display
    var toastMessage: ToastMessage?

    /// Pending "this needs an account" prompt, presented once at the app root.
    ///
    /// Held on AppState rather than per-screen so that ANY call site — a ViewModel, a
    /// singleton service, a deeply nested row — can ask for sign-in without owning
    /// presentation state or a path back to the navigation tree.
    var signInPrompt: SignInPrompt?

    /// Whether the global Cay AI chat cover is up, presented once by `ContentView`.
    ///
    /// Here for the same reason as `signInPrompt` above: the chat door now lives in
    /// `GlobalHeaderView`, which four different tab headers embed, and none of them owns the
    /// presentation state or a path to the tab shell that does. A flag on AppState lets any of
    /// them ask without threading a binding through four header organisms.
    var isAIChatPresented: Bool = false

    /// Ticker a notification tap wants opened, consumed by the Home tab.
    ///
    /// A tapped push used to land wherever the user happened to be — the alert said
    /// "NVDA moved 8%" and then showed you the Wiser tab. Routed through AppState
    /// rather than a new navigation stack so it reuses the ticker presentation Home
    /// already owns. Cleared by whoever consumes it, so one tap opens one screen.
    var pendingPushTicker: String?

    /// Where a notification tap wants to land, resolved from the payload.
    ///
    /// Supersedes `pendingPushTicker`, which could only ever express "open a ticker" —
    /// and did so with a HARDCODED `.stock` type, so a crypto or ETF alert opened the
    /// wrong detail screen. `NotificationRoute` carries the asset type from the payload
    /// and can also express a report or the inbox.
    ///
    /// `pendingPushTicker` is kept alongside it, set in lockstep, so any existing reader
    /// keeps working through the transition.
    var pendingPushRoute: NotificationRoute?

    /// Which Tracking segment to open on arrival, consumed by `TrackingContentViewWithBinding`.
    ///
    /// The Tracking sub-tab lives in `TrackingViewModel`, which is a `@StateObject` private to
    /// that screen and therefore unreachable from a push handler. This parks the intent the
    /// same way `pendingPushRoute` does, and for the same reason: a tap that resolves to no
    /// detail screen must still land on the notification list rather than nowhere.
    ///
    /// Device-global with no user id, so it is cleared in `discardDataForEndedSession()`.
    var pendingTrackingTab: TrackingTab?

    /// Unread notification count, for the tab-bar badge.
    ///
    /// Device-global (no user id), so it MUST be reset in `discardDataForEndedSession()`
    /// — otherwise the next account to sign in on this phone inherits the previous
    /// user's badge. Same bug class as the four Learn stores and `WhaleService`.
    var unreadNotificationCount: Int = 0

    /// Broadcast an authoritative unread count from wherever it was last observed.
    ///
    /// Posted as a Notification rather than written directly because the inbox ViewModel
    /// is not `@Observable` and has no AppState reference; `iosApp` observes this and
    /// updates the badge. Keeps the ViewModel free of a global lookup (ios-swiftui.md:
    /// dependencies arrive via `init`, never a singleton reach-through).
    static func notificationUnreadDidChange(_ count: Int) {
        NotificationCenter.default.post(
            name: .caydexNotificationUnreadChanged,
            object: nil,
            userInfo: ["count": count]
        )
    }

    // MARK: - Services (Injected)

    private(set) var apiClient: APIClient!
    private(set) var authService: AuthService!

    // MARK: - Initialization

    init() {
        // Services will be set up in configure()
    }

    /// Adopt a newly granted entitlement without waiting for a relaunch.
    ///
    /// Re-reads BOTH the profile (for `tier`) and the credit balance. `refreshCredits()` alone
    /// is not enough: `user.tier` is only ever assigned in `applyProfile`, so after a purchase
    /// the credits would update while the tier badge, paywall highlighting, and
    /// `canGenerateResearch` all still said Free.
    func refreshEntitlement() async {
        do {
            applyProfile(try await apiClient.request(
                endpoint: .getCurrentUser, responseType: UserProfile.self
            ))
        } catch {
            // Release-visible. A stale tier after a purchase is a paid-for entitlement the user
            // cannot see, and a DEBUG-only print made that undiagnosable in production.
            Analytics.shared.track(.backgroundSyncFailed, [
                "op": .string("entitlement_refresh"),
                "code": .string(AppError.from(error).analyticsCode),
            ])
            #if DEBUG
            print("⚠️ [AppState] entitlement profile refresh failed: \(AppError.from(error).message)")
            #endif
        }
        await refreshCredits()
    }

    /// Configure services - called from App entry point
    func configure(apiClient: APIClient, authService: AuthService) {
        self.apiClient = apiClient
        self.authService = authService

        // Adopt a purchase as soon as the backend records it. StoreKitService posts this from
        // the single funnel that covers interactive purchases AND Transaction.updates replays;
        // it holds no AppState reference, and injecting one would mean editing iosApp.swift.
        NotificationCenter.default.addObserver(
            forName: .caydexEntitlementChanged, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in await self?.refreshEntitlement() }
        }

        // Announce connectivity so a session can heal the moment the network comes back.
        NetworkMonitor.shared.start()
        NotificationCenter.default.addObserver(
            forName: NetworkMonitor.didRestoreNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.isOnline = true
                await self?.restoreSessionIfNeeded(trigger: "network-restored")
            }
        }

        // Restore auth state from keychain.
        //
        // ⚠️ THE TOKEN IS ARMED BEFORE ANYTHING ELSE IN THIS TASK, and that ordering is
        // load-bearing for correctness, not just for speed.
        //
        // `configure()` cannot await — it is called from the root `.task` — so it can only
        // SPAWN the restore. Meanwhile `ContentView` opacity-mounts all five tabs at once,
        // so their ViewModels and `.task` triggers fire immediately, and `restoreSession`
        // did not arm the credential until after it had already set `.restoring` and
        // suspended on the network. Every request issued in that window went out with NO
        // bearer — and most of them are `.guestAllowed`, which the backend ANSWERS by
        // resolving the caller to their per-install guest partition rather than refusing.
        //
        // So a signed-in user's launch fetched a stranger's empty data: an empty watchlist,
        // no portfolios, and — because `/widget/portfolio-mover` degrades an empty holdings
        // list to the market payload — market movers written into the "My Holdings" widget
        // slot. The identity-change reload then replaced it all a second later, which is
        // both the visible flash AND most of the duplicate traffic in a launch log.
        //
        // Arming first makes those same first requests authentic. A token that turns out to
        // be expired is not a new problem: it 401s and `APIClient`'s single-flight refresh
        // interceptor handles it, which is the designed path. `enterRestoringWindow` still
        // DISARMS deliberately on a transient failure — that divergence is intact.
        Task {
            await primeStoredCredential()

            // Seed the widget on COLD LAUNCH, from HERE rather than from `iosApp`.
            //
            // `didBecomeActive` covers returning to the foreground but races the first frame
            // on a cold start (verified: a launch-only run produced 20+ requests and ZERO
            // widget fetches), so a freshly installed widget sat on its placeholder until the
            // user backgrounded and returned. It therefore has to fire at launch — but from
            // `iosApp` it could only ever fire ALONGSIDE this task, never after the token was
            // armed, because `configure()` cannot await. That is a real ordering bug, not a
            // latency one: `/widget/portfolio-mover` is `.guestAllowed`, so a tokenless call
            // is answered for the per-install guest, whose holdings are empty, which the
            // backend degrades to the MARKET payload — and market movers were then written
            // into the "My Holdings" tile on every cold launch of a signed-in user.
            //
            // One line later here, that is simply sequenced. `onAuthenticated` still forces a
            // refresh once the identity fully settles, which now also survives landing
            // mid-flight (see `WidgetRefreshService.forcedRefreshPending`).
            //
            // `markCredentialReady()` also releases the gate that suppresses any EARLIER
            // refresh. `UIApplication.didBecomeActiveNotification` is delivered before the
            // root `.task` reaches `configure()`, so `iosApp`'s foreground trigger fires
            // first on every cold launch — and being `.guestAllowed`, it did not fail, it
            // succeeded as the guest.
            WidgetRefreshService.shared.markCredentialReady()
            WidgetRefreshService.shared.refresh(identity: identityGeneration)

            await restoreSession(trigger: "launch")
        }
    }

    /// Arm `APIClient` with the stored credential, if there is one, before any request goes out.
    ///
    /// Deliberately does NOT touch `auth.status`: this says nothing about whether the session
    /// is valid, only that we hold something worth sending. `restoreSession` immediately
    /// follows and owns the validation — and owns clearing it if the credential is dead.
    private func primeStoredCredential() async {
        guard let token = authService.getStoredToken() else { return }
        await apiClient.setAuthToken(token)
    }

    // MARK: - Session Healing

    /// Guards against concurrent restores. A foreground, a network-restore and a failed request
    /// can all fire within the same second; without this they would race three `/users/me`
    /// calls and three `onAuthenticated()` fan-outs.
    private var restoreTask: Task<Void, Never>?

    /// Backoff attempt counter, reset on any success or explicit sign-out.
    private var restoreAttempt: Int = 0

    /// Pending backoff retry, cancelled whenever a stronger trigger (foreground, network) fires.
    private var restoreBackoffTask: Task<Void, Never>?

    /// True when a credential is sitting in the Keychain but we are not signed in — i.e. the
    /// app is running tokenless when it shouldn't be. This is the condition the whole
    /// self-healing mechanism exists to resolve.
    var hasUnusedStoredCredential: Bool {
        guard let authService else { return false }
        return authService.hasStoredToken && auth.status != .authenticated
    }

    /// Re-attempt a restore, but only when there is actually something to restore.
    ///
    /// Every trigger routes through here so the "is this worth doing" test lives in one place:
    /// a deliberate guest (no stored token) is never disturbed, and an already-authenticated
    /// session is never re-fetched.
    func restoreSessionIfNeeded(trigger: String) async {
        guard hasUnusedStoredCredential else { return }
        await restoreSession(trigger: trigger)
    }

    /// Single-flight session restore. Concurrent callers await the in-flight attempt rather than
    /// starting their own — the same shape as `APIClient.refreshInFlight`. `AppState` is
    /// `@MainActor`, so this check-and-set cannot interleave.
    func restoreSession(trigger: String) async {
        if let inFlight = restoreTask {
            await inFlight.value
            return
        }
        let task = Task { @MainActor in
            await self.performRestore(trigger: trigger)
        }
        restoreTask = task
        await task.value
        restoreTask = nil
    }

    /// Schedule the next bounded backoff attempt after a TRANSIENT failure.
    ///
    /// Bounded and capped: 2s, 8s, 30s, 120s, then every 300s. The point is that a token-holding
    /// user is never stranded for a whole app run — which is exactly what happened before,
    /// because restore ran once at launch and nothing ever tried again.
    private func scheduleRestoreBackoff() {
        restoreBackoffTask?.cancel()
        let delays: [UInt64] = [2, 8, 30, 120, 300]
        let seconds = delays[min(restoreAttempt, delays.count - 1)]
        restoreAttempt += 1

        restoreBackoffTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: seconds * 1_000_000_000)
            guard !Task.isCancelled else { return }
            await self?.restoreSessionIfNeeded(trigger: "backoff")
        }
    }

    private func cancelRestoreBackoff() {
        restoreBackoffTask?.cancel()
        restoreBackoffTask = nil
        restoreAttempt = 0
    }

    /// React to an auth failure the network layer could not recover from.
    ///
    /// Wired to `APIClient.setAuthFailureHandler` at the app root. The two cases are opposite on
    /// purpose — see `AuthFailure`.
    func handleAuthFailure(_ failure: AuthFailure) async {
        switch failure {
        case .signInRequired:
            // A request went out tokenless on a route that needs an account. If we DO hold a
            // credential, the client is running without it — heal rather than prompt.
            await restoreSessionIfNeeded(trigger: "auth-required-response")

        case .credentialRejected:
            // Presented, rejected, refresh failed. The session is genuinely over.
            guard auth.status == .authenticated || authService?.hasStoredToken == true else { return }
            endSessionForDeadCredential()
        }
    }

    /// Tear down a session whose credential is definitively dead.
    ///
    /// Mirrors `signOut()` minus the backend call (there is no usable credential to log out
    /// with). Learn data is discarded for the same reason `restoreAuthState` discards it on a
    /// dead refresh token: the stores are device-global, so leaving them would merge the ended
    /// session's progress into whoever signs in next.
    private func endSessionForDeadCredential() {
        authService?.clearToken()
        invalidateIdentity(nil)
        user = UserState()
        // `signOut()` clears these two and this path did not, so a dead credential left the
        // ended account's tickers and reports rendered under the guest UI.
        watchlist = WatchlistState()
        research = ResearchState()
        auth.status = .unauthenticated
        cancelRestoreBackoff()
        // MUST be cleared alongside the discard below. `onAuthenticated` skips its fan-out when
        // the resolved user matches this, so leaving the previous id here would let the SAME
        // user sign back in and skip the re-hydrate — landing them in an app whose Learn stores
        // we just wiped, with nothing to refill them until the next cold launch.
        lastAuthenticatedUserId = nil
        discardDataForEndedSession()
        currentError = .sessionEnded(message: "")
    }

    // MARK: - Auth Actions

    /// Guest-first auth restore. Runs on launch:
    ///  - No stored token  → stay a guest (`.unauthenticated`); the app is fully
    ///    usable and requests fall back to the backend's GUEST_USER_ID.
    ///  - Stored token      → arm the API client, fetch `/users/me`, and go
    ///    `.authenticated`. On a 401 (expired), try ONE refresh then retry.
    ///  - Any hard failure  → drop to guest (never a login wall).
    private func performRestore(trigger: String) async {
        // Captured BEFORE the first await. Any interactive sign-in that lands while this is
        // suspended bumps it, and `enterRestoringWindow` then refuses to disarm a token that
        // is no longer the one this restore was validating.
        let generation = credentialGeneration
        guard let token = authService.getStoredToken() else {
            resolveIdentity(nil)
            auth.status = .unauthenticated   // guest — app still shown
            cancelRestoreBackoff()
            return
        }

        // Mark the honest state while we work: a credential exists, we just haven't validated it
        // yet. Previously this window was labelled `.unauthenticated`, indistinguishable from a
        // deliberate guest, so the UI offered "Sign In" to someone who was already signed in.
        if auth.status != .authenticated {
            auth.status = .restoring
        }

        await apiClient.setAuthToken(token)

        // Own the refresh decision here (allowAuthRetry:false bypasses the APIClient
        // interceptor) so a TRANSIENT failure is never mistaken for a dead session.
        do {
            let profile = try await fetchCurrentUserNoRetry()
            applyProfile(profile)
            await establishAuthenticatedSession(userId: profile.id)
            return
        } catch {
            // A non-auth error (offline / 5xx) → preserve the STORED token, run as guest.
            guard AppError.from(error).isAuthError else {
                // Disarm the CLIENT token to match what we are about to show.
                //
                // The Keychain copy is deliberately kept (this is transient — the next launch
                // must be able to restore), but leaving the token armed on APIClient made the
                // app's wire identity disagree with its UI: `auth.status` says guest and every
                // screen renders the signed-out affordances, while every request still carries
                // the Bearer token and the backend answers as the real account. The user sees
                // "Sign In" and a guest profile, yet their watchlist writes land on the real
                // account and report generation debits their real credits — and the guest
                // per-install partition they appear to be in is not the one being written.
                // `.restoring`, not `.unauthenticated`: the credential is still good as far as
                // we know, we just could not reach the server. Labelling this as signed-out is
                // what made a single flaky launch cost the user their whole session — the UI
                // said "Sign In" and nothing ever tried again.
                await enterRestoringWindow(generation: generation)
                return
            }
            // Access token rejected → fall through to an explicit refresh.
        }

        do {
            try await authService.refreshToken()
        } catch {
            // Clear ONLY on a genuine auth failure (refresh token itself dead). A
            // transient refresh outage (offline / 5xx) preserves the token so the
            // next launch can restore.
            if AppError.from(error).isAuthError {
                authService.clearToken()
                await apiClient.setAuthToken(nil)
                user = UserState()
                // The session is definitively over (the refresh token itself is dead), so this is
                // a sign-out in everything but name — drop the Learn data with it, exactly as
                // `signOut()` does. Without this, an expired session left the previous account's
                // completions and bookmarks on the device for the NEXT person to sign in, and
                // their `pushUnsynced` would write them into that account.
                //
                // Deliberately NOT done on the two transient branches above/below: those keep the
                // token because the same user is expected back, and wiping there would throw away
                // local progress that has not synced yet (an offline learner's work).
                discardDataForEndedSession()
                invalidateIdentity(nil)
                auth.status = .unauthenticated
                cancelRestoreBackoff()
                return
            }
            // Transient refresh outage: token preserved, keep trying.
            await enterRestoringWindow(generation: generation)
            return
        }

        // Refresh succeeded → retry the profile once.
        do {
            let profile = try await fetchCurrentUserNoRetry()
            applyProfile(profile)
            await establishAuthenticatedSession(userId: profile.id)
        } catch {
            // Refreshed OK but the profile read still failed → transient; preserve the STORED
            // token so a later attempt can restore, but disarm the client one so the wire
            // identity matches the guest-equivalent UI we are about to present (same reasoning
            // as above), and keep retrying.
            await enterRestoringWindow(generation: generation)
        }
    }

    /// Enter the `.restoring` window: disarm the CLIENT token, show the guest-equivalent UI
    /// honestly, and keep retrying. The Keychain copy is deliberately kept — this is transient.
    ///
    /// The flag is the point. `X-Guest-Id` goes out on EVERY request unconditionally
    /// (`APIClient.buildRequest`), so with the token disarmed every `.guestAllowed` route
    /// resolves through `guest_user_id_for` and writes to this install's GUEST partition. Once
    /// the session heals for the SAME user, `onAuthenticated` used to early-return before ever
    /// reaching the claim — so the tickers, portfolio edits, chats and Learn progress the user
    /// created while the app said "Reconnecting" silently vanished.
    ///
    /// Funnelled into one method so a future `.restoring` branch cannot forget to record it.
    private func enterRestoringWindow(generation: UInt64) async {
        guard generation == credentialGeneration else {
            // A newer credential was installed while this restore was in flight — the user
            // signed in. Disarming now would strip a good token off the client while the UI
            // says authenticated, and nothing would ever put it back.
            return
        }
        await apiClient.setAuthToken(nil)
        noteCredentialDisarmed()
        didWriteAsGuestWhileRestoring = true
        auth.status = .restoring
        scheduleRestoreBackoff()
    }

    /// Fetch `/users/me` WITHOUT the APIClient 401-refresh interceptor, so
    /// `restoreAuthState` can distinguish a transient failure from a dead session.
    private func fetchCurrentUserNoRetry() async throws -> UserProfile {
        try await apiClient.request(
            endpoint: .getCurrentUser,
            responseType: UserProfile.self,
            allowAuthRetry: false
        )
    }

    /// Store the fetched profile AND map its tier string into the `UserTier` enum.
    /// This is the ONLY place `user.tier` is assigned — it drives the tier badge,
    /// paywall highlighting, and `canGenerateResearch`.
    func applyProfile(_ profile: UserProfile) {
        user.profile = profile

        let previousTier = user.tier
        let incomingTier = UserTier(rawValue: profile.tier) ?? .free
        user.tier = incomingTier
        // A launch settling into the tier the user already had is not an unlock. See
        // `entitlementGeneration`.
        if hasHydratedProfileOnce, previousTier != incomingTier {
            entitlementGeneration &+= 1
        }
        hasHydratedProfileOnce = true

        // We now know whose credential we have been sending. On a cold launch this is a
        // discovery and bumps nothing; on a sign-in or an account switch it bumps and every
        // tab drops what it loaded for the previous identity.
        resolveIdentity(profile.id)
        // Push the tier to the audio engines. They are services, not views, so they cannot
        // read `@Environment(AppState.self)` — and the gate has to live at the engines
        // because Journey narrates from `.onAppear` with no button to guard. One assignment
        // point in, one push out.
        LearnAudioEntitlement.shared.update(tier: user.tier)
        // Book narration URLs are signed and fetched, not compiled in, so warm them here —
        // this is the first moment we know the plan. A locked account clears instead, so a
        // downgrade cannot leave a still-valid signed URL usable on the device.
        if user.tier == .pro || user.tier == .premium {
            BookAudioURLStore.shared.prefetch()
        } else {
            BookAudioURLStore.shared.reset()
        }
    }

    /// Post-authentication side effects: claim guest data, refresh credits, pull synced settings.
    /// Called from every path that transitions to `.authenticated`.
    /// Publish a confirmed identity and run its fan-out, in the one order that is safe.
    ///
    /// ⚠️ THE GUEST CLAIM COMPLETES BEFORE `.authenticated` IS PUBLISHED. That ordering is
    /// the entire reason this method exists, and it was wrong at all five call sites.
    ///
    /// `onAuthenticated` documents that the claim must run "FIRST — before any read of the
    /// user's data", but it lived INSIDE `onAuthenticated`, which is called one line AFTER
    /// `auth.status = .authenticated`. Publishing that status synchronously fires
    /// `ReloadOnIdentityChange` on every eagerly-mounted tab, and each of those launches an
    /// unstructured `Task` that reads the account partition — concurrently with the still
    /// in-flight `POST /users/me/claim-guest-data`. So the reads could and did land first,
    /// and the tickers being claimed were absent from the list the user was looking at until
    /// something else happened to refetch. The comment described a happens-before that
    /// nothing enforced.
    ///
    /// Callers pass a profile they have ALREADY applied, because `applyProfile` writes
    /// `user.tier`, which is itself observed.
    private func establishAuthenticatedSession(userId: String) async {
        await claimGuestDataForIncomingIdentity(userId: userId)
        auth.status = .authenticated
        cancelRestoreBackoff()
        await onAuthenticated(userId: userId)
    }

    /// The one piece of the fan-out that cannot wait until after the status is published.
    ///
    /// Deliberately mirrors `onAuthenticated`'s transition test rather than inventing a
    /// second rule: "does this identity need a claim" is the same question in both places,
    /// and answering it differently is how the two would drift.
    private func claimGuestDataForIncomingIdentity(userId: String) async {
        // Same account as last time — the claim is genuinely redundant UNLESS we spent time
        // in `.restoring`, where requests went out tokenless and anything the user created
        // landed in this install's guest partition.
        if userId == lastAuthenticatedUserId, !didWriteAsGuestWhileRestoring {
            return
        }
        didWriteAsGuestWhileRestoring = false
        await claimGuestDataIfNeeded()
    }

    private func onAuthenticated(userId: String? = nil) async {
        // Fire the fan-out only on a real identity TRANSITION.
        //
        // Restore is now re-runnable (launch, foreground, network-restore, backoff, a failed
        // request), so without this guard a user who bounced between Wi-Fi and cellular would
        // re-POST the guest claim, re-hydrate settings and re-hydrate all four Learn stores on
        // every reconnect. `claimGuestDataIfNeeded` guards concurrency but not repetition.
        //
        // Keyed on the user id, not a bool, so an account SWITCH still runs the fan-out.
        if let userId, userId == lastAuthenticatedUserId {
            // Same account, so the rest of the fan-out is genuinely redundant.
            //
            // The `.restoring`-window guest claim that used to sit here has moved UP into
            // `claimGuestDataForIncomingIdentity`, which runs before `.authenticated` is
            // published — see `establishAuthenticatedSession`. It is still conditional on
            // `didWriteAsGuestWhileRestoring` exactly as it was; only its timing changed.
            //
            // `SettingsSyncManager.push()` correctly DEFERS a change made
            // during `.restoring` into its pending-key set, and this early return is the only
            // reason nothing ever drained it: `hydrate()` sits at the bottom of this method,
            // past the `return`. A user who flipped a toggle on a flaky connection kept that
            // change local-only until they happened to open a settings screen again.
            //
            // Safe here precisely because it is NOT the fan-out: `resumeSyncIfNeeded` is
            // self-gating on "is anything actually pending", so a healthy reconnect with
            // nothing outstanding performs ZERO network calls — which is the property this
            // guard exists to protect. It deliberately does not touch the guest claim, which
            // stays conditional above: idempotence is not enough there, because a re-POSTed
            // claim is a real indexed write and the log stops being truthful.
            SettingsSyncManager.shared.resumeSyncIfNeeded(trigger: "session-healed")
            return
        }
        // A different account on the same device: drop the previous session's device-global
        // stores before hydrating, or `hydrate()` unions the old user's rows into the new one's
        // view and `pushUnsynced()` writes them into the new account.
        if let userId, let previous = lastAuthenticatedUserId, previous != userId {
            discardDataForEndedSession()
        }
        lastAuthenticatedUserId = userId

        // The guest claim that belongs FIRST — before any read of the user's data — has
        // already run, in `claimGuestDataForIncomingIdentity`, before `.authenticated` was
        // published. Migration 108 partitions guest watchlists per install, so a user who
        // added tickers during onboarding and then signed up owns nothing until those rows
        // are moved; claiming after a watchlist read shows them an empty list they have to
        // pull-to-refresh away. Doing it from HERE could not deliver that guarantee, because
        // publishing the status one line earlier had already started those reads.
        //
        // Apply any credit-pack purchase that Apple delivered while there was no account to
        // attach it to. `POST /billing/verify` is `.signInRequired`, so a transaction arriving
        // during a signed-out session is refused by `APIClient` before it goes out and stays
        // unfinished until something re-submits it — and `restorePurchases()` cannot, because
        // `Transaction.currentEntitlements` excludes consumables. Without this, the credits
        // land only on the NEXT cold launch. Runs before `refreshCredits` so the balance the
        // user sees already includes them; idempotent server-side, so a no-op is one cheap
        // call.
        await StoreKitService.shared.drainUnfinishedTransactions()
        await refreshCredits()
        SettingsSyncManager.shared.hydrate()
        PushNotificationManager.shared.flushPendingToken()
        hydrateLearnStores()

        // Rebuild the Home Screen widget for the identity that just settled.
        //
        // This is the RELIABLE trigger. The cold-launch call in `iosApp` races
        // `restoreSession`, and `didBecomeActive` only fires on re-entry — so without
        // this, a user who signed in (or switched accounts) kept a tile built for the
        // previous identity, or for the guest partition, for the whole app session. The
        // account-switch branch above has already cleared the portfolio snapshot, which
        // otherwise leaves that widget blank until the next foreground.
        //
        // `force` skips the 60s throttle: an identity change is exactly the case where
        // the throttle is wrong, because the previous fetch answered for someone else.
        // `identity:` is what keeps this from being two wasted requests on every launch:
        // the seed refresh in `configure()` already ran under this same armed credential, so
        // the force is only honoured when the identity actually moved (a real sign-in).
        WidgetRefreshService.shared.refresh(force: true, identity: identityGeneration)
    }

    /// Pull the user's Learn progress down at the auth transition.
    ///
    /// `BookProgressStore.hydrate()` was reachable from exactly ONE place — the Book Library
    /// screen's `.task`. Anything that shows book progress WITHOUT going through the Library
    /// first therefore rendered the empty UserDefaults set: on a fresh install, or a new device,
    /// the Learn tab's "continue reading" row showed nothing and a part-finished book offered to
    /// start at core 1, silently discarding progress the server already had. The four stores are
    /// all device-global and all hydrate the same way, so they belong here — the single funnel
    /// every path to `.authenticated` passes through, and the mirror of
    /// `discardDataForEndedSession()` on the way out.
    ///
    /// Fire-and-forget and individually isolated: each store swallows its own errors, and a
    /// Learn sync must never delay or fail sign-in.
    private func hydrateLearnStores() {
        Task {
            await BookProgressStore.shared.hydrate()
            await BookmarkStore.shared.hydrate()
            await JourneyProgressStore.shared.hydrate()
            await MoneyMovesProgressStore.shared.hydrate()
        }
    }

    /// Re-entrancy guard for `claimGuestDataIfNeeded`. `restoreAuthState()` on launch can race an
    /// explicit `signIn()`, and two concurrent claims both read the guest rows before either
    /// writes — harmless to the data (the second UPDATE just re-stamps rows already moved) but it
    /// double-counts, which makes the log lie about what happened. `AppState` is `@MainActor`, so
    /// this check-and-set cannot interleave.
    private var isClaimingGuestData = false

    /// The account whose post-auth fan-out has already run, so a repeated restore doesn't
    /// repeat it. Cleared on sign-out and on a dead credential.
    private var lastAuthenticatedUserId: String?

    /// True once we have operated tokenless while still holding a credential — see
    /// `enterRestoringWindow()`. Consumed by the next successful `onAuthenticated`.
    private var didWriteAsGuestWhileRestoring = false

    /// Bumped whenever a NEWER credential is installed — any interactive sign-in.
    ///
    /// `restoreSession` is single-flight only against ITSELF: an interactive `signIn` runs
    /// outside `restoreTask`, so a backoff retry can be suspended mid-flight while the user
    /// signs in on the very flaky network that scheduled it. Without this, the restore resumes,
    /// finds its own profile read failed, and calls `setAuthToken(nil)` — stripping the good
    /// token the sign-in just installed. The UI then says `.authenticated` with no token on the
    /// wire, and NOTHING recovers it: every healing trigger routes through
    /// `restoreSessionIfNeeded`, whose guard is `auth.status != .authenticated`. Terminal for
    /// the app run, with every `.guestAllowed` write silently going to the guest partition.
    private var credentialGeneration: UInt64 = 0

    // MARK: - Identity generation (what a loaded screen belongs to)

    /// Bumped when the identity behind the wire CHANGES — sign-in, sign-out, account switch,
    /// or the deliberate disarm during a transient restore. Surfaces stamp what they loaded
    /// under and skip a reload when it still matches.
    ///
    /// The subtle half is what does NOT bump it: **discovering** whose credential we already
    /// hold. A cold launch primes the stored token before any tab mounts, so the mount-time
    /// load is already answered for the right user; the `.authenticated` that lands a moment
    /// later is a STATUS change, not an identity change. Reloading for it fetched
    /// `/home/dashboard` four times per launch and re-ran the whole Tracking fan-out for tabs
    /// nobody was looking at.
    ///
    /// Distinct from `credentialGeneration` above, which is a restore-invalidation counter with
    /// a different lifetime — do not merge them.
    private(set) var identityGeneration: Int = 0

    /// `"u:<id>"` / `"guest"`, or nil for "we have not been told yet".
    private var resolvedIdentityKey: String?

    /// Record who the wire currently answers as. See `identityGeneration`.
    private func resolveIdentity(_ userId: String?) {
        let key = userId.map { "u:\($0)" } ?? "guest"
        defer { resolvedIdentityKey = key }
        // FIRST resolution of the process is a discovery, not a change — see above.
        guard let previous = resolvedIdentityKey, previous != key else { return }
        identityGeneration &+= 1
    }

    /// The identity behind the wire ENDED or was replaced — always a change, even when it is
    /// the first thing this process resolves.
    ///
    /// The distinction from `resolveIdentity` matters at exactly one moment, and getting it
    /// wrong strands a screen: a cold launch that primes a DEAD credential sends the
    /// mount-time loads with that token and has them rejected, then concludes "guest". Treated
    /// as a discovery, nothing would reload and the active tab would hold its 401 error until
    /// the user happened to switch tabs. It is a change: what we sent was not who we are.
    private func invalidateIdentity(_ userId: String?) {
        resolvedIdentityKey = userId.map { "u:\($0)" } ?? "guest"
        identityGeneration &+= 1
    }

    /// The client token was deliberately stripped while a stored credential is still held
    /// (`enterRestoringWindow`). Requests now answer as the per-install guest, so anything
    /// loaded from here belongs to a different identity than what came before — even though
    /// `auth.status` never reaches `.unauthenticated` and no observer fires. Without this bump
    /// a 60s auto-refresh tick during a flaky-network restore could load guest data and stamp
    /// it as the user's, and the heal would then decline to replace it.
    private func noteCredentialDisarmed() {
        guard resolvedIdentityKey != "guest" else { return }
        resolvedIdentityKey = "guest"
        identityGeneration &+= 1
    }

    /// Bumped ONLY by a tier change that happens after the profile has hydrated once.
    ///
    /// `user.tier` is declared `= .free` and `applyProfile` writes the real value during
    /// restore, so the first write is HYDRATION, not an upgrade — and an observer watching
    /// `user.tier` directly fired on every cold launch of every paying account. Views that
    /// unlock content on a purchase must observe THIS.
    private(set) var entitlementGeneration: Int = 0

    /// Distinguishes the hydration write from a real change. Checked inside `applyProfile`
    /// rather than from a view, because `onChange` delivery is deferred to the next update
    /// pass — by which time a view-side "was this the first write?" flag already reads true.
    private var hasHydratedProfileOnce = false

    /// Move this install's guest watchlist + portfolios + Learn progress onto the account that
    /// just signed in (Learn covers completions AND book bookmarks — one unified table).
    ///
    /// Deliberately NOT latched in `UserDefaults`: the endpoint is idempotent by construction
    /// (the rows are gone the second time, and it refuses the shared legacy bucket outright), and
    /// a persisted latch would permanently skip the claim for anyone who signed in on a build
    /// that shipped before this call existed — the exact users who need it. One cheap indexed
    /// POST per auth transition is the right trade.
    ///
    /// Never throws and never blocks sign-in: the user is already authenticated by the time this
    /// runs, so a failure here must not turn a successful sign-in into a visible error.
    private func claimGuestDataIfNeeded() async {
        guard !isClaimingGuestData else { return }
        isClaimingGuestData = true
        defer { isClaimingGuestData = false }

        do {
            // `AccountRepository.shared` rather than an injected dependency: adding one would mean
            // changing `configure(apiClient:authService:)`, whose only caller is iosApp.swift.
            // Matches how the two lines below reach SettingsSyncManager / PushNotificationManager.
            let result = try await AccountRepository.shared.claimGuestData()
            // No refresh signal is published on purpose: `TrackingViewModel.loadData()` has no
            // "already loaded" short-circuit, so the Tracking tab re-reads whenever it reappears
            // after the sign-in sheet dismisses. A revision counter here would be state nothing
            // observes.
            // ⚠️ A PARTIAL FAILURE ARRIVES AS HTTP 200, NOT AS A THROW.
            //
            // The backend deliberately never raises mid-claim: it catches, logs, and answers
            // 200 with `{"claimed": {all zeros}, "error": "<ExcType>"}`
            // (users.py:376-385, pinned by
            // tests/test_guest_data_claim.py::test_a_failure_PART_WAY_THROUGH_still_returns_rather_than_raises).
            // That is the right server behaviour — a half-migrated claim must not look like a
            // transport error — but on this side it lands in the SUCCESS branch.
            //
            // Inspecting it only inside `#if DEBUG` meant a release build treated the
            // designed failure signal as a success: no toast, no analytics, no log. The user
            // finishes sign-up, lands on an empty watchlist, and nothing anywhere records
            // that their data is still sitting in the guest partition. That is exactly the
            // shape `.claude/rules/auth.md` rule 6 bans ("banned on these paths: a bare
            // `try?`, a `catch` that only prints, and `#if DEBUG`-only reporting").
            //
            // Route it through the same two lines the `catch` below uses, so both failure
            // modes are reported identically.
            if let error = result.error {
                Analytics.shared.track(.backgroundSyncFailed, [
                    "op": .string("claim_guest_data"),
                    "code": .string("PARTIAL_\(error)"),
                ])
                showToast(
                    "We couldn't move your saved tickers to your new account. Pull to refresh, or sign in again.",
                    type: .warning
                )
            }

            #if DEBUG
            if let skipped = result.skipped {
                print("ℹ️ [AppState] guest claim skipped: \(skipped)")
            } else if let error = result.error {
                print("⚠️ [AppState] guest claim partial failure: \(error)")
            } else {
                print("✅ [AppState] guest claim: \(result.claimed.watchlistItems) watchlist, \(result.claimed.portfolios) portfolio(s)")
            }
            #endif
        } catch {
            // The most consequential of the silent ones: this is what moves a guest's watchlist,
            // portfolios and Learn progress onto the account they just created. Failing quietly
            // means the user finishes onboarding, signs up, and lands on an EMPTY watchlist with
            // no idea their work is still sitting in the guest partition. Surfaced, not just
            // logged — it is recoverable (the claim is idempotent, so a later sign-in retries).
            let appError = AppError.from(error)
            Analytics.shared.track(.backgroundSyncFailed, [
                "op": .string("claim_guest_data"),
                "code": .string(appError.analyticsCode),
            ])
            showToast(
                "We couldn't move your saved tickers to your new account. Pull to refresh, or sign in again.",
                type: .warning
            )
            #if DEBUG
            print("⚠️ [AppState] claimGuestData failed: \(appError.message)")
            #endif
        }
    }

    /// Refresh the credit balance into `user.credits` (single source of truth).
    /// Best-effort: failures are logged, not surfaced (the Profile screen shows
    /// the last-known balance rather than an error toast).
    /// The credits fetch currently in flight, if any. Concurrent callers JOIN it.
    ///
    /// Three independent paths call this on a signed-in launch — the `onAuthenticated`
    /// fan-out, `ResearchViewModel.loadCredits()`, and `refreshEntitlement()` via the
    /// `.caydexEntitlementChanged` observer that `drainUnfinishedTransactions()` can post —
    /// and they collided on `GET /users/me/credits`. Worse than wasteful: the two responses
    /// raced to assign `user.credits`, so the balance shown could be the OLDER of the two.
    private var creditsTask: Task<Void, Never>?

    func refreshCredits() async {
        if let running = creditsTask, !running.isCancelled {
            await running.value
            return
        }
        let task = Task { [weak self] in
            guard let self else { return }
            await self.performCreditsRefresh()
            // Cleared HERE, as the task's own last act, rather than after `await
            // task.value` below. Between a task finishing and its awaiting caller
            // being resumed, a THIRD caller can run and observe a completed-but-still
            // -registered task: it would "join" something already done and return
            // instantly without ever loading. That is a silently skipped refresh —
            // and for `handleIdentityChange` it would mean adopting a load that
            // completed under the PREVIOUS identity.
            self.creditsTask = nil
        }
        creditsTask = task
        await task.value
    }

    private func performCreditsRefresh() async {
        do {
            user.credits = try await apiClient.request(
                endpoint: .getUserCredits,
                responseType: CreditInfo.self
            )
        } catch {
            Analytics.shared.track(.backgroundSyncFailed, [
                "op": .string("credits_refresh"),
                "code": .string(AppError.from(error).analyticsCode),
            ])
            #if DEBUG
            print("⚠️ [AppState] refreshCredits failed: \(AppError.from(error).message)")
            #endif
        }
    }

    /// Sign out. Resets local state immediately for a snappy UI; the backend
    /// `/auth/logout` call + Keychain/APIClient token clear run in the background
    /// (they need the token to still be set, so they fire before it is cleared).
    func signOut() {
        Task { [authService] in await authService?.signOut() }
        invalidateIdentity(nil)
        auth.status = .unauthenticated
        user = UserState()
        watchlist = WatchlistState()
        research = ResearchState()
        // Stop healing: there is deliberately nothing to restore now, and a pending backoff
        // would otherwise fire mid-sign-out and try to resurrect the session.
        cancelRestoreBackoff()
        lastAuthenticatedUserId = nil

        // One funnel. The settings clear used to be a second call here, which is precisely why
        // the other two session-end paths missed it.
        discardDataForEndedSession()
    }

    /// Drop this device's Learn progress + bookmarks because the session that owned them ended.
    ///
    /// Learn state is DEVICE-GLOBAL — the four stores' UserDefaults keys carry no user id — so
    /// leaving it in place handed the next account to sign in on this device the previous user's
    /// data: each store's `hydrate()` unions the stale local set into the new account's view, and
    /// `pushUnsynced()` then POSTs those items INTO that account, durable and visible on all of
    /// its other devices.
    ///
    /// Costs a signed-in user nothing: their rows are on the server, so signing back in
    /// re-hydrates everything on the next Learn open.
    private func discardDataForEndedSession() {
        // Bump FIRST: a hydrate issued seconds ago is still in flight and would otherwise land
        // after these resets and refill the stores with the ended session's rows.
        LearnIdentityEpoch.bump()
        BookProgressStore.shared.reset()
        JourneyProgressStore.shared.reset()
        MoneyMovesProgressStore.shared.reset()
        BookmarkStore.shared.reset()
        // The saved Money Move topic is the same device-global bug class: one slug under a
        // defaults key with no user id in it. Left behind, the next account sees the previous
        // user's saved topic AND `pushUnsynced` writes it into their own rows.
        MoneyMoveBookmarkStore.shared.reset()
        // Followed whales belong here for exactly the same reason the four Learn stores do:
        // `WhaleService.followedWhaleIds` persists to a device-global UserDefaults key with no
        // user id in it, and nothing cleared it on sign-out. So account B, signing in on A's
        // phone, saw A's followed investors — and any list the server hadn't yet reconciled
        // stayed wrong. Same bug class, same fix, one funnel.
        WhaleService.shared.reset()
        // Search history is the same bug class one more time: tickers the user opened and
        // questions they asked Cay AI, on a device-global UserDefaults key with no user id in
        // it. Left behind, the next account to sign in on this phone reads the previous user's
        // searches — and re-taps them straight into their own session.
        SearchHistoryStore.shared.reset()
        // Narration entitlement is device-global state in the same sense: the ended session's
        // tier must not carry into the next account, and any Learn audio still playing is
        // now unentitled. `.free` is the safe direction — it locks, never unlocks.
        LearnAudioEntitlement.shared.update(tier: .free)
        // Same argument, one layer down: a signed book URL minted for the ended session is
        // still valid for hours, and this store is keyed by nothing but curriculum order.
        BookAudioURLStore.shared.reset()
        // The widget snapshot is the same bug class with the widest blast radius: it lives in
        // a device-global App Group container, and unlike everything else here it is visible
        // on the HOME SCREEN — the previous account's holdings and their biggest mover,
        // readable without unlocking into the app at all.
        WidgetRefreshService.shared.clearForEndedSession()

        // Everything below used to sit OUTSIDE this funnel, called only from `signOut()`. That
        // covered exactly one of the three ways a session ends — the other two (a dead access
        // token, a dead refresh token) left all of it behind. Those are not edge cases: an
        // expired or revoked session is the ordinary way a session dies.
        //
        // Synced preferences: 13 notify_* toggles plus persona, appearance, playback speed and
        // haptics, all on device-global keys. `hydrate()` only overwrites keys the server
        // actually returns, so the next account inherits the gaps — and its first `push()`
        // writes the previous user's preferences up as its own, across all of their devices.
        SettingsSyncManager.shared.clearLocalForEndedSession()
        // APNs: without this the phone keeps receiving the ended account's watchlist alerts,
        // and the only server-side detach requires the session that just died.
        PushNotificationManager.shared.clearLocalRegistrationForEndedSession()
        // The inbox badge and any parked tap are device-global with no user id, so they
        // survive a session end unless cleared here — handing the next account to sign in
        // on this phone the previous user's unread count, and possibly opening a screen
        // they never asked for. Same reasoning as the Learn stores above.
        unreadNotificationCount = 0
        pendingPushRoute = nil
        pendingPushTicker = nil
        pendingTrackingTab = nil
        PortfolioStore.shared.reset()
        // Consent is per person and must never be inherited — see the note on the method.
        AIConsentStore.shared.resetForEndedSession()

        // Abandon this install's GUEST partition, and forget that we ever wrote to it.
        //
        // A signed-in user's writes land there during a `.restoring` window (the client token
        // is disarmed while X-Guest-Id keeps going out). That is recoverable when the session
        // heals — `onAuthenticated` claims the rows. It is NOT recoverable when the session
        // ENDS: the device keeps pointing at the same bucket, so it reads the previous user's
        // tickers straight back, and the next person to sign up on this phone has those rows
        // CLAIMED onto their account by POST /users/me/claim-guest-data — which cannot tell an
        // ex-user's bucket from a legitimate pre-signup one.
        didWriteAsGuestWhileRestoring = false
        GuestIdentity.rotateForEndedSession()
    }

    /// Sign in. Throws on failure so the SignInView can render the error inline.
    func signIn(email: String, password: String) async throws {
        // A newer credential is about to be installed: invalidate any restore suspended
        // mid-flight, and stop a scheduled backoff firing into the middle of this.
        credentialGeneration &+= 1
        cancelRestoreBackoff()
        // NO `auth.status = .loading` here. `iosApp.swift` renders `SplashView()` for
        // `.loading`, so setting it swapped the ROOT view out mid-request — tearing down the
        // Account sheet, the SignInView presented on top of it, and the `errorMessage` the
        // catch below is about to set. Every failed sign-in therefore dumped the user back on
        // Home with no explanation, no matter how good the backend's error was. Found by
        // driving the Simulator; no test could see it.
        //
        // Nothing is lost: `SignInView` owns `isSubmitting`, which drives the in-button
        // spinner and disables the form. `.loading` is for app-launch/restore, not for a
        // request made from a screen that is already on top of a rendered app.
        do {
            // Bind the profile so the id can be passed on. Calling `onAuthenticated()` with no
            // id here left `lastAuthenticatedUserId` nil after every interactive sign-in, which
            // disarmed BOTH guards inside it: the account-switch discard never ran, and the
            // next restore re-ran the whole fan-out. See `onAuthenticated` for the leak.
            let profile = try await authService.signIn(email: email, password: password)
            applyProfile(profile)
            await establishAuthenticatedSession(userId: profile.id)
        } catch {
            // Status untouched: the caller was already `.unauthenticated` or `.restoring`, and
            // forcing `.unauthenticated` here would discard a `.restoring` credential that is
            // still perfectly good.
            throw error
        }
    }

    /// Sign up. Throws on failure so the SignInView can render the error inline.
    ///
    /// Returns the outcome because email confirmation is REQUIRED: the normal result is
    /// `.needsEmailConfirmation`, where the account exists but there is no session yet, so
    /// auth status must stay `.unauthenticated`. Treating that as a successful sign-in would
    /// drop the user into an app with no working token.
    @discardableResult
    func signUp(
        email: String, password: String, displayName: String
    ) async throws -> SignUpOutcome {
        // A newer credential is about to be installed: invalidate any restore suspended
        // mid-flight, and stop a scheduled backoff firing into the middle of this.
        credentialGeneration &+= 1
        cancelRestoreBackoff()
        // NO `auth.status = .loading` here. `iosApp.swift` renders `SplashView()` for
        // `.loading`, so setting it swapped the ROOT view out mid-request — tearing down the
        // Account sheet, the SignInView presented on top of it, and the `errorMessage` the
        // catch below is about to set. Every failed sign-in therefore dumped the user back on
        // Home with no explanation, no matter how good the backend's error was. Found by
        // driving the Simulator; no test could see it.
        //
        // Nothing is lost: `SignInView` owns `isSubmitting`, which drives the in-button
        // spinner and disables the form. `.loading` is for app-launch/restore, not for a
        // request made from a screen that is already on top of a rendered app.
        do {
            let outcome = try await authService.signUp(
                email: email, password: password, displayName: displayName
            )
            switch outcome {
            case .needsEmailConfirmation:
                break   // no session yet; leave the status exactly as it was
            case .signedIn(let profile):
                applyProfile(profile)
                await establishAuthenticatedSession(userId: profile.id)
            }
            return outcome
        } catch {
            throw error   // status untouched — see signIn
        }
    }

    /// Re-send the signup confirmation email.
    func resendConfirmation(email: String) async throws {
        try await authService.resendConfirmation(email: email)
    }

    /// Complete a social sign-in from a provider handshake.
    ///
    /// Both provider paths converge here. Not subject to the email-confirmation gate: Apple
    /// and Google supply an already-verified address.
    func completeSocialSignIn(_ result: SocialSignInResult) async throws {
        // A newer credential is about to be installed: invalidate any restore suspended
        // mid-flight, and stop a scheduled backoff firing into the middle of this.
        credentialGeneration &+= 1
        cancelRestoreBackoff()
        // NO `auth.status = .loading` here. `iosApp.swift` renders `SplashView()` for
        // `.loading`, so setting it swapped the ROOT view out mid-request — tearing down the
        // Account sheet, the SignInView presented on top of it, and the `errorMessage` the
        // catch below is about to set. Every failed sign-in therefore dumped the user back on
        // Home with no explanation, no matter how good the backend's error was. Found by
        // driving the Simulator; no test could see it.
        //
        // Nothing is lost: `SignInView` owns `isSubmitting`, which drives the in-button
        // spinner and disables the form. `.loading` is for app-launch/restore, not for a
        // request made from a screen that is already on top of a rendered app.
        do {
            let profile: UserProfile
            switch result {
            case let .identityToken(provider, token, nonce, displayName):
                profile = try await authService.signInWithProvider(
                    provider: provider, idToken: token, nonce: nonce, displayName: displayName
                )
            case let .supabaseSession(accessToken):
                profile = try await authService.exchangeSupabaseSession(accessToken: accessToken)
            }
            applyProfile(profile)
            await establishAuthenticatedSession(userId: profile.id)
        } catch {
            auth.status = .unauthenticated
            throw error
        }
    }

    // MARK: - Error Handling

    func handleError(_ error: Error) {
        let appError = AppError.from(error)

        // Nobody is waiting for this result any more (tab switch, view teardown, a
        // superseding request). Not a failure — and never a banner. See `AppError.cancelled`.
        guard !appError.isCancellation else { return }

        // Handle auth errors globally.
        //
        // This used to `signOut()` on ANY `.unauthorized`, which is far too blunt now that a
        // tokenless request produces a real auth error: a guest who never signed in would be
        // "signed out", wiping the device-global Learn stores and the synced settings with it.
        // Sign-out is reserved for a credential we KNOW is dead; a missing one asks for
        // sign-in, and a stored-but-unvalidated one heals.
        switch appError {
        case .sessionEnded:
            signOut()
            currentError = appError
            return
        case .unauthorized, .tokenExpired:
            if hasUnusedStoredCredential {
                Task { await restoreSessionIfNeeded(trigger: "handleError") }
            } else if auth.status == .authenticated {
                signOut()
            }
            currentError = appError
            return
        case .signInRequired:
            // Never a sign-out: there was no session to end.
            currentError = appError
            return
        default:
            break
        }

        currentError = appError
    }

    /// Ask for sign-in, app-wide, with copy specific to what the user was trying to do.
    ///
    /// Routes through `currentError` so it reuses machinery that already exists and works:
    /// the global overlay renders `ErrorToastView`, whose action button is driven by
    /// `suggestedAction` — `.signInRequired` returns `.signIn`, which `RootView` already maps to
    /// presenting `SignInView`. That path was fully built and fed from exactly one line in the
    /// entire app.
    ///
    /// Report a failed USER-INITIATED mutation, visibly.
    ///
    /// This is the single pattern that replaces ~20 hand-rolled `print`-and-revert blocks —
    /// whale follow, the five star toggles, portfolio edits, onboarding watchlist writes. Each
    /// of those reverted the optimistic UI correctly and then said nothing, so from the user's
    /// side the app simply undid what they just did.
    ///
    /// Routing, by kind of failure:
    ///  * needs an account  → the sign-in prompt (actionable)
    ///  * session died      → handled by `handleError`, which heals or ends the session
    ///  * anything else     → a toast naming what failed
    ///
    /// Always logs, in every build configuration.
    ///
    /// - Parameters:
    ///   - action: infinitive phrase completing "Couldn't …", e.g. "follow this investor".
    ///   - signInFeature: phrase for the sign-in prompt if this turns out to be an auth failure.
    func reportMutationFailure(_ error: Error, action: String, signInFeature: String? = nil) {
        let appError = AppError.from(error)

        // Unconditional, not `#if DEBUG`. A release build that says nothing is how this class of
        // bug survived: the revert looked like a UI glitch and there was no trace anywhere.
        Analytics.shared.track(.mutationFailed, [
            "action": .string(action),
            "code": .string(appError.analyticsCode),
        ])

        switch appError {
        case .signInRequired:
            requestSignIn(for: signInFeature ?? action)
        case .unauthorized, .tokenExpired, .sessionEnded:
            // Let the session logic decide between healing and ending, then still tell the user
            // their action didn't land.
            handleError(error)
        default:
            showToast("Couldn't \(action). \(appError.message)", type: .error)
        }
    }

    /// - Parameter feature: a verb phrase completing "Sign in to …", e.g. "follow investors".
    func requestSignIn(for feature: String?) {
        // A stored credential going unused is not a "please sign in" situation — it is a
        // restore that hasn't happened yet. Prompting there would ask an already-signed-in user
        // to sign in again, which is precisely the confusion this work exists to remove.
        if hasUnusedStoredCredential {
            Task { await restoreSessionIfNeeded(trigger: "sign-in-requested") }
            showToast("Reconnecting your account…", type: .info)
            return
        }
        signInPrompt = SignInPrompt(feature: feature)
    }

    /// Raise the global Cay AI chat, from the header bar of any tab.
    ///
    /// Deliberately NOT gated on sign-in: chat is `.guestAllowed` (a per-install identity, see
    /// auth.md §1a), so demanding an account here would delete a working feature for guests.
    /// The credit precharge on send is where a real account requirement, if any, belongs.
    func requestAIChat() {
        isAIChatPresented = true
    }

    func clearError() {
        currentError = nil
    }

    func showToast(_ message: String, type: ToastType = .info) {
        toastMessage = ToastMessage(message: message, type: type)

        // Auto-dismiss after 3 seconds
        Task {
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if toastMessage?.message == message {
                toastMessage = nil
            }
        }
    }
}

// MARK: - Auth State

@Observable
final class AuthState {
    var status: AuthStatus = .unknown
    var accessToken: String?

    var isAuthenticated: Bool {
        status == .authenticated
    }

    var isLoading: Bool {
        status == .loading
    }
}

/// A pending request to sign in, raised by whatever the user was trying to do.
/// `Identifiable` so it can drive a `.sheet(item:)` — a new prompt replaces an older one rather
/// than being dropped by a stale boolean.
struct SignInPrompt: Identifiable, Equatable {
    let id = UUID()
    /// Verb phrase completing "Sign in to …", e.g. "follow investors".
    let feature: String?
}

enum AuthStatus: Equatable {
    case unknown
    case loading
    case authenticated

    /// Deliberate guest: no stored credential. The app is fully usable; "Sign In" is the honest
    /// affordance.
    case unauthenticated

    /// A credential IS stored but hasn't been validated yet, and a restore is being retried.
    ///
    /// This state had no representation before, and its absence was a real bug: the two
    /// transient-failure branches of restore labelled themselves `.unauthenticated`, so a
    /// signed-in user who launched on a flaky network was shown the same UI as someone who had
    /// never made an account — offered "Sign In" while holding a perfectly good token, for the
    /// rest of the app run. On the wire this behaves exactly like a guest (the client token is
    /// deliberately disarmed to keep the wire identity honest); the difference is that the UI
    /// says "Reconnecting" and something is actively trying again.
    case restoring
}

// MARK: - User State

@Observable
final class UserState {
    var profile: UserProfile?
    var credits: CreditInfo?
    var tier: UserTier = .free

    var displayName: String {
        profile?.displayName ?? "Guest"
    }

    var remainingCredits: Int {
        credits?.remaining ?? 0
    }

    var canGenerateResearch: Bool {
        remainingCredits > 0
    }
}

struct UserProfile: Codable, Identifiable, Sendable {
    let id: String
    let email: String
    let displayName: String?
    let avatarUrl: String?
    let tier: String
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, email, tier
        case displayName = "display_name"
        case avatarUrl = "avatar_url"
        case createdAt = "created_at"
    }
}

/// Credit balance across BOTH pools.
///
/// `total` / `used` / `remaining` are the COMBINED position — the backend aggregates the
/// monthly (granted) pool and the purchased (consumable IAP) pool before sending them, so
/// every existing reader of `remaining` keeps working and a user holding purchased credits is
/// not shown 0 with a disabled Generate button.
///
/// ⚠️ `resetsAt` describes the GRANTED pool ONLY. App Store Guideline 3.1.1 forbids purchased
/// credits expiring, so anything that renders a reset date next to `remaining` must qualify it
/// with `purchasedRemaining` — otherwise the app is telling the user their paid credits
/// expire.
struct CreditInfo: Codable, Sendable {
    let total: Int
    let used: Int
    let remaining: Int
    let resetsAt: String?
    /// Optional because the app and the backend deploy independently — a build carrying this
    /// type can hit a Railway instance that predates the two-pool split.
    let grantedRemaining: Int?
    let purchasedRemaining: Int?
    /// The GRANTED pool's own totals. A `used / total` fraction is only meaningful within one
    /// pool — `total`/`used` above are lifetime-inclusive of every pack ever bought, so a
    /// monthly quota bar drawn from them never fills and cannot be read as "40 of your 50 this
    /// month".
    let grantedTotal: Int?
    let grantedUsed: Int?

    /// Credits that will survive the next monthly reset. 0 (not nil) when the backend has not
    /// told us, so callers can render honestly without special-casing.
    var purchasedCredits: Int { purchasedRemaining ?? 0 }
    /// Whether any part of this balance actually expires at `resetsAt`.
    var hasExpiringCredits: Bool { (grantedRemaining ?? remaining) > 0 }

    /// The MONTHLY quota only — what `resetsAt` actually describes. Falls back to the combined
    /// figures when talking to a backend that predates the split, which is exactly right there:
    /// before credit packs existed the combined balance WAS the monthly one.
    var monthlyTotal: Int { grantedTotal ?? total }
    var monthlyUsed: Int { grantedUsed ?? used }
    var monthlyRemaining: Int { grantedRemaining ?? remaining }

    /// Fraction of the MONTHLY allowance consumed, clamped to 0...1.
    ///
    /// Computed from the granted pool alone. Drawn from the combined `used / total` it is
    /// nonsense: buy a 1,200-credit pack on the Free tier and the bar reads 0/1250 forever,
    /// implying a monthly quota 25x the real one.
    var monthlyUsageFraction: Double {
        guard monthlyTotal > 0 else { return 0 }
        return min(max(Double(monthlyUsed) / Double(monthlyTotal), 0), 1)
    }

    enum CodingKeys: String, CodingKey {
        case total, used, remaining
        case resetsAt = "resets_at"
        case grantedRemaining = "granted_remaining"
        case purchasedRemaining = "purchased_remaining"
        case grantedTotal = "granted_total"
        case grantedUsed = "granted_used"
    }
}

enum UserTier: String, Codable, Sendable {
    case free
    case pro
    case premium
}

// MARK: - Watchlist State

@Observable
final class WatchlistState {
    var stocks: [WatchlistStock] = []
    var isLoading: Bool = false

    func contains(_ ticker: String) -> Bool {
        stocks.contains { $0.ticker.uppercased() == ticker.uppercased() }
    }

    func toggle(_ stock: WatchlistStock) {
        if let index = stocks.firstIndex(where: { $0.ticker == stock.ticker }) {
            stocks.remove(at: index)
        } else {
            stocks.insert(stock, at: 0)
        }
    }
}

struct WatchlistStock: Codable, Identifiable, Equatable, Sendable {
    var id: String { ticker }
    let ticker: String
    let companyName: String
    let logoUrl: String?
    var price: Double?
    var changePercent: Double?

    enum CodingKeys: String, CodingKey {
        case ticker
        case companyName = "company_name"
        case logoUrl = "logo_url"
        case price
        case changePercent = "change_percent"
    }
}

// MARK: - Research State

@Observable
final class ResearchState {
    var reports: [ResearchReportSummary] = []
    var generatingReports: Set<String> = [] // Report IDs currently generating
    var selectedPersona: String = "buffett"

    func isGenerating(_ reportId: String) -> Bool {
        generatingReports.contains(reportId)
    }

    var hasActiveGeneration: Bool {
        !generatingReports.isEmpty
    }
}

struct ResearchReportSummary: Codable, Identifiable, Sendable {
    let id: String
    let stockId: String
    let ticker: String
    let companyName: String
    let investorPersona: String
    let status: String
    let title: String?
    let executiveSummary: String?
    let createdAt: String
    let completedAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case stockId = "stock_id"
        case ticker
        case companyName = "company_name"
        case investorPersona = "investor_persona"
        case status, title
        case executiveSummary = "executive_summary"
        case createdAt = "created_at"
        case completedAt = "completed_at"
    }

    var isCompleted: Bool { status == "completed" }
    var isFailed: Bool { status == "failed" }
    var isPending: Bool { status == "pending" || status == "processing" }
}

// MARK: - Toast Message

struct ToastMessage: Equatable, Sendable {
    let message: String
    let type: ToastType
}

enum ToastType: Sendable {
    case success
    case error
    case info
    case warning
}

// MARK: - Environment Key

extension EnvironmentValues {
    @Entry var appState: AppState = AppState()
}
