//
//  SettingsSyncManager.swift
//  ios
//
//  Syncs the app's device preferences (the @AppStorage keys in
//  NotificationsSettingsView + AppSettingsView + the appearance choice) with the
//  backend blob at /users/me/settings, so a signed-in user's settings follow them
//  across devices/reinstalls.
//
//  Strategy (last-write-wins, low-friction):
//   • hydrate()  — on sign-in / launch: pull the backend blob → UserDefaults.
//   • push()     — best-effort: read the known keys → PUT the blob. Called when
//                  the settings screens close and when appearance changes.
//  Both self-gate on authentication (guests stay purely local — no backend row).
//

import Foundation

@MainActor
final class SettingsSyncManager {

    static let shared = SettingsSyncManager()

    private let repository: AccountRepositoryProtocol
    private weak var appState: AppState?
    private let defaults = UserDefaults.standard

    // Boolean toggles (NotificationsSettingsView + AppSettingsView).
    static let boolKeys: [String] = [
        "notify_earnings_alerts", "notify_earnings_surprises", "notify_earnings_upcoming",
        "notify_market_alerts", "notify_market_macro", "notify_market_volatility", "notify_market_sector",
        "notify_smart_money", "notify_smart_money_whale", "notify_smart_money_insider",
        "notify_smart_money_institutional",
        "notify_research_complete", "notify_watchlist_changes",
        "auto_refresh_quotes", "show_premarket", "compact_numbers", "haptic_feedback",
    ]

    // String preferences (currency, persona, appearance).
    static let stringKeys: [String] = [
        "default_currency", "default_persona", AppearanceManager.storageKey,
    ]

    private init(repository: AccountRepositoryProtocol = AccountRepository.shared) {
        self.repository = repository
    }

    func configure(appState: AppState) {
        self.appState = appState
    }

    private var isAuthenticated: Bool {
        appState?.auth.isAuthenticated ?? false
    }

    /// Pull the backend preference blob into UserDefaults (authed only).
    func hydrate() {
        guard isAuthenticated else { return }
        Task {
            do {
                let prefs = try await repository.fetchSettings()
                apply(prefs)
            } catch {
                #if DEBUG
                print("⚠️ [Settings] hydrate failed: \(AppError.from(error).message)")
                #endif
            }
        }
    }

    /// Push current UserDefaults values to the backend (best-effort, authed only).
    func push() {
        guard isAuthenticated else { return }
        let blob = currentBlob()
        Task {
            do {
                _ = try await repository.updateSettings(blob)
            } catch {
                #if DEBUG
                print("⚠️ [Settings] push failed: \(AppError.from(error).message)")
                #endif
            }
        }
    }

    // MARK: - Blob <-> UserDefaults

    private func currentBlob() -> [String: PreferenceValue] {
        var blob: [String: PreferenceValue] = [:]
        for key in Self.boolKeys where defaults.object(forKey: key) != nil {
            blob[key] = .bool(defaults.bool(forKey: key))
        }
        for key in Self.stringKeys {
            if let value = defaults.string(forKey: key) {
                blob[key] = .string(value)
            }
        }
        return blob
    }

    private func apply(_ prefs: [String: PreferenceValue]) {
        for (key, value) in prefs {
            switch value {
            case .bool(let b): defaults.set(b, forKey: key)
            case .string(let s): defaults.set(s, forKey: key)
            case .int(let i): defaults.set(i, forKey: key)
            case .double(let d): defaults.set(d, forKey: key)
            }
        }
        // Re-apply appearance in case the synced blob changed it.
        AppearanceManager.applyStored()
    }
}
