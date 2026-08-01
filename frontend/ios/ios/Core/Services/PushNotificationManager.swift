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
    private var pendingToken: String?

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
