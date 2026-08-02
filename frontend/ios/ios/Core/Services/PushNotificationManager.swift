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

    private init(repository: AccountRepositoryProtocol = AccountRepository.shared) {
        self.repository = repository
    }

    func configure(appState: AppState) {
        self.appState = appState
    }

    /// Ask for notification permission; register for remote notifications on grant.
    /// Safe to call repeatedly — iOS only prompts once.
    func requestAuthorization() {
        Task {
            do {
                let granted = try await UNUserNotificationCenter.current()
                    .requestAuthorization(options: [.alert, .badge, .sound])
                if granted {
                    UIApplication.shared.registerForRemoteNotifications()
                }
            } catch {
                #if DEBUG
                print("⚠️ [Push] authorization request failed: \(error.localizedDescription)")
                #endif
            }
        }
    }

    /// A notification was TAPPED. Hands the target to AppState; the Home tab
    /// consumes it and presents the ticker.
    func handleTap(ticker: String) {
        appState?.pendingPushTicker = ticker.uppercased()
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
        guard let token = registeredToken else { return }
        do {
            _ = try await repository.unregisterDevice(token: token)
            registeredToken = nil
        } catch {
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
