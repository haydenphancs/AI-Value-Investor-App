//
//  PushNotificationManager.swift
//  ios
//
//  Coordinates APNs: requests notification permission, registers for remote
//  notifications, and hands the device token to the backend (POST /users/me/devices)
//  once the user is signed in. A token that arrives while signed-out is stashed and
//  flushed on the next sign-in (a push token is only useful bound to a real user).
//

import UIKit
import UserNotifications

@MainActor
final class PushNotificationManager {

    static let shared = PushNotificationManager()

    private let repository: AccountRepositoryProtocol
    private weak var appState: AppState?
    /// Token captured before sign-in. PERSISTED, not just in memory: a guest who
    /// grants permission during onboarding gets their token here, and an in-memory
    /// stash died with the process — so signing in a day later registered nothing.
    private var pendingToken: String? {
        get { UserDefaults.standard.string(forKey: Self.pendingTokenKey) }
        set {
            if let newValue {
                UserDefaults.standard.set(newValue, forKey: Self.pendingTokenKey)
            } else {
                UserDefaults.standard.removeObject(forKey: Self.pendingTokenKey)
            }
        }
    }
    private static let pendingTokenKey = "pending_apns_device_token"

    /// The token we last CONFIRMED with the backend. Persisted because sign-out has to name
    /// the token to detach it, and `pendingToken` is cleared the moment registration succeeds —
    /// so at sign-out time there was no record of what to unregister.
    private var registeredToken: String? {
        get { UserDefaults.standard.string(forKey: Self.registeredTokenKey) }
        set {
            if let newValue {
                UserDefaults.standard.set(newValue, forKey: Self.registeredTokenKey)
            } else {
                UserDefaults.standard.removeObject(forKey: Self.registeredTokenKey)
            }
        }
    }

    private static let registeredTokenKey = "registered_apns_device_token"

    /// True once a token has been CONFIRMED with the backend.
    ///
    /// Read by the Notifications screen so "iOS says authorized, but no device is registered"
    /// stops rendering as a healthy screen. That state is reachable — `didFailToRegister`
    /// records nothing — and it means every toggle on that screen is a control that cannot
    /// possibly work.
    var hasRegisteredToken: Bool { registeredToken != nil }

    /// Re-assert the APNs registration when iOS already granted permission.
    ///
    /// Idempotent: iOS returns the cached token, so calling it on every foreground costs
    /// nothing. It has to run there, not just at launch, because **iOS only delivers a token
    /// in response to a registration call**. The recovery flow the permission banner is built
    /// to drive — denied → Open Settings → enable → come back — otherwise ends with the banner
    /// gone, every toggle live, and NO token sent to the backend until the user fully quits
    /// and relaunches. Nothing on screen said so.
    func registerIfAuthorized() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        guard settings.authorizationStatus == .authorized
                || settings.authorizationStatus == .provisional else { return }
        UIApplication.shared.registerForRemoteNotifications()
    }

    /// Optional + nil-coalesce, matching the codebase's repository-injection idiom (see
    /// `HomeDashboardViewModel.init` / `SearchViewModel.init`): `AccountRepository.shared` is
    /// MainActor-isolated, and a default-argument expression is evaluated at the CALL SITE,
    /// which the compiler checks as nonisolated. Resolving it inside this `@MainActor` init
    /// keeps the isolation honest. Injection for tests/previews is unchanged.
    private init(repository: AccountRepositoryProtocol? = nil) {
        self.repository = repository ?? AccountRepository.shared
    }

    func configure(appState: AppState) {
        self.appState = appState
        // Deliver a tap that arrived before this ran. See `pendingRoute`.
        if let parked = pendingRoute {
            pendingRoute = nil
            deliver(parked)
        }
    }

    /// A tap captured BEFORE `configure(appState:)` had run.
    ///
    /// This is the cold-launch path and it silently lost every tap. `configure` is called from
    /// `iosApp`'s `.task`, which runs after first render AND after an `await` on
    /// `ServerEnvironmentManager.resolve()` — while `userNotificationCenter(_:didReceive:)`
    /// fires as soon as the app is launched BY the tap. So `appState` was routinely still nil,
    /// `appState?.pendingPushRoute = route` wrote to nothing, and the app opened on Home with
    /// no error anywhere: the exact "tappable banner, tap does nothing" symptom that
    /// `NotificationRoute` was introduced to kill, just moved one layer up.
    ///
    /// In memory only, unlike `pendingToken`: a tap is meaningful for this launch, not a day
    /// later. If the process dies before AppState exists there is nothing worth restoring.
    private var pendingRoute: NotificationRoute?

    /// Ask for notification permission; register for remote notifications on grant.
    /// Safe to call repeatedly — iOS only prompts once.
    ///
    /// Fire-and-forget. A caller that needs to redraw based on the ANSWER must use
    /// `requestAuthorizationResult()` instead — see the note there.
    func requestAuthorization() {
        Task { _ = await requestAuthorizationResult() }
    }

    /// The same request, AWAITING the user's actual answer.
    ///
    /// ⚠️ The fire-and-forget form cannot tell a caller what happened, and the two callers
    /// that need to know were guessing: they slept 500ms and re-read `authorizationStatus`.
    /// The system prompt is MODAL and a person takes seconds to read it, so that re-read
    /// almost always observed `.notDetermined` and the screen never updated. Measured on the
    /// simulator: tapping "Don't Allow" left the price-alert sheet showing no warning at all,
    /// which is the exact state the warning exists for.
    ///
    /// - Returns: whether permission was granted.
    @discardableResult
    func requestAuthorizationResult() async -> Bool {
        do {
            let granted = try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .badge, .sound])
            // The single most important number in the push funnel: iOS prompts ONCE,
            // so the grant rate is a one-shot measurement and a denial is permanent
            // until the user goes to Settings themselves. Without it there is no way
            // to tell "our copy is bad" from "nobody is being asked".
            //
            // The outcome is computed OUTSIDE the props literal on purpose: an inline
            // ternary puts `"granted" :` inside the dictionary, which the prop-key
            // source scan in `test_analytics_ingest.py` reads as a second key.
            let outcome = granted ? "granted" : "denied"
            Analytics.shared.track(.pushPermissionResult, ["reason": .string(outcome)])
            if granted {
                UIApplication.shared.registerForRemoteNotifications()
            }
            return granted
        } catch {
            #if DEBUG
            print("⚠️ [Push] authorization request failed: \(error.localizedDescription)")
            #endif
            Analytics.shared.track(.pushPermissionResult, ["reason": "error"])
            return false
        }
    }

    /// A notification was TAPPED. Hands the resolved destination to AppState; the Home
    /// tab consumes it and presents the right screen.
    ///
    /// Both properties are set in lockstep: `pendingPushRoute` is the one that carries the
    /// asset type (so a crypto alert opens the crypto screen), and `pendingPushTicker` is
    /// kept so any existing reader keeps working.
    func handleTap(route: NotificationRoute) {
        // Park it if AppState is not wired yet — a cold launch FROM a tap gets here first.
        guard appState != nil else {
            pendingRoute = route
            return
        }
        deliver(route)
    }

    private func deliver(_ route: NotificationRoute) {
        appState?.pendingPushRoute = route
        appState?.pendingPushTicker = route.symbol
    }

    /// Legacy entry point, kept so a caller that only has a symbol still works. Resolves
    /// through the same router, which defaults the asset type to `.stock`.
    func handleTap(ticker: String) {
        handleTap(route: .ticker(
            symbol: ticker.uppercased(), assetType: .stock, destination: .default
        ))
    }

    /// Called by the AppDelegate with the raw APNs token.
    func didRegister(deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        registerOrStash(token)
    }

    /// Detach this device from the account that is signing out.
    ///
    /// `device_tokens.token` is UNIQUE and the binding only ever moves when a NEW registration
    /// re-binds it — but a signed-out client has no session, so it never registers again. The
    /// row therefore kept pointing at the previous account and the sweeper kept delivering ITS
    /// watchlist alerts to this device: a phone showing the signed-out guest UI buzzing with
    /// someone else's tickers, which also discloses what they follow.
    ///
    /// Awaited by `AuthService.signOut()` BEFORE the token is cleared, because the call needs
    /// the session that is being ended. Best-effort: a failure must never block sign-out.
    func unregisterCurrentDevice() async {
        // `?? pendingToken` is what makes this work at all on the deliberate sign-out path,
        // and it is not defensive padding — it fixes a deterministic ordering bug.
        //
        // `AppState.signOut()` DEFERS the backend logout into a `Task` and then runs the rest
        // of itself synchronously, reaching `discardDataForEndedSession()` →
        // `clearLocalRegistrationForEndedSession()`, which nils `registeredToken` and stashes
        // it in `pendingToken`. So by the time this runs, the guard it used to have had
        // already failed and `DELETE /users/me/devices` was NEVER SENT on a normal sign-out.
        // The server kept the token bound to the account, and a signed-out phone carried on
        // receiving that account's alerts.
        //
        // Reading the stash instead of racing the ordering means this is correct whichever
        // side wins. `pendingToken` is intentionally left in place: it is the device's APNs
        // token, still valid, and re-registration on the next sign-in wants it.
        guard let token = registeredToken ?? pendingToken else { return }
        do {
            _ = try await repository.unregisterDevice(token: token)
            registeredToken = nil
        } catch {
            // Release-visible: a token that fails to detach stays bound to the account that
            // registered it, so a signed-out phone keeps receiving the PREVIOUS account's alerts.
            Analytics.shared.track(.backgroundSyncFailed, [
                "op": .string("push_deregister"),
                "code": .string(AppError.from(error).analyticsCode),
            ])
            #if DEBUG
            print("⚠️ [Push] device de-registration failed: \(AppError.from(error).message)")
            #endif
        }
    }

    /// Flush a token captured before sign-in (call after auth succeeds).
    func flushPendingToken() {
        if let token = pendingToken {
            registerOrStash(token)
        }
    }

    /// Release this device's binding LOCALLY, for the session-end paths that have no usable
    /// credential to de-register with.
    ///
    /// `unregisterCurrentDevice()` needs a live session, so it can only run from a deliberate
    /// sign-out. The other two endings — a dead access token and a dead refresh token — cannot
    /// call the server at all, and they used to do nothing: `device_tokens` kept mapping this
    /// phone to the ended account, and because the only detach endpoint requires that account's
    /// session, nothing could ever undo it. The sweeper kept delivering the previous user's
    /// watchlist alerts to a phone now showing the guest UI, disclosing what they follow.
    ///
    /// Re-stashing the token as pending is what actually heals it: `flushPendingToken()` runs on
    /// the next successful auth, and `device_tokens.token` is UNIQUE, so re-registering MOVES
    /// the row to the new account rather than duplicating it.
    func clearLocalRegistrationForEndedSession() {
        guard let token = registeredToken else { return }
        registeredToken = nil
        pendingToken = token
    }

    // MARK: - Private

    private func registerOrStash(_ token: String) {
        guard appState?.auth.isAuthenticated == true else {
            pendingToken = token   // register once the user signs in
            return
        }
        let environment = apnsEnvironment
        Task {
            do {
                _ = try await repository.registerDevice(token: token, environment: environment)
                pendingToken = nil   // clear ONLY on confirmed success
                registeredToken = token
            } catch {
                // Keep the token so flushPendingToken() can retry — a transient
                // offline/5xx must not permanently drop the device registration.
                pendingToken = token
                // Release-visible: silently means no push notifications, ever, with nothing
                // anywhere to explain why.
                Analytics.shared.track(.backgroundSyncFailed, [
                    "op": .string("push_register"),
                    "code": .string(AppError.from(error).analyticsCode),
                ])
                #if DEBUG
                print("⚠️ [Push] device registration failed: \(AppError.from(error).message)")
                #endif
            }
        }
    }

    private var apnsEnvironment: String {
        #if DEBUG
        return "sandbox"
        #else
        return "production"
        #endif
    }
}
