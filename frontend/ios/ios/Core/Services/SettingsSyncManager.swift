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

extension Notification.Name {
    /// Posted after the backend settings blob is applied to UserDefaults, so open
    /// screens (e.g. the Profile appearance picker) can refresh from the store.
    static let caydexSettingsHydrated = Notification.Name("caydexSettingsHydrated")
}

@MainActor
final class SettingsSyncManager {

    static let shared = SettingsSyncManager()

    private let repository: AccountRepositoryProtocol
    private weak var appState: AppState?
    private let defaults = UserDefaults.standard

    /// True once the current session's settings have been pulled from the server.
    /// `push()` no-ops until this is set, so a partial local snapshot can't
    /// full-replace (clobber) richer server settings before we've read them.
    private var hasHydrated = false

    // Boolean toggles (NotificationsSettingsView + AppSettingsView). App Lock is
    // deliberately NOT synced — it's device-local security.
    static let boolKeys: [String] = [
        "notify_earnings_alerts", "notify_earnings_surprises", "notify_earnings_upcoming",
        "notify_market_alerts", "notify_market_macro", "notify_market_volatility", "notify_market_sector",
        "notify_smart_money", "notify_smart_money_whale", "notify_smart_money_insider",
        "notify_smart_money_institutional",
        "notify_research_complete", "notify_watchlist_changes",
        "haptic_feedback", "autoplay_next",
    ]

    // String preferences (persona, appearance). Currency is USD-only (no picker).
    static let stringKeys: [String] = [
        "default_persona", AppearanceManager.storageKey,
    ]

    // Numeric preferences (playback speed).
    static let doubleKeys: [String] = [
        "playback_speed",
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
    /// Blocks `push()` until it completes so a partial local snapshot can't clobber
    /// richer server settings on a fresh install / new session.
    func hydrate() {
        guard isAuthenticated else { return }
        hasHydrated = false   // re-gate for this (possibly new) session
        Task {
            do {
                let prefs = try await repository.fetchSettings()
                apply(prefs)
                hasHydrated = true   // safe to push now (server state is known)
            } catch {
                // Leave hasHydrated false so push() stays gated — we don't know the
                // server state, so pushing would risk a clobber. A later hydrate retries.
                #if DEBUG
                print("⚠️ [Settings] hydrate failed: \(AppError.from(error).message)")
                #endif
            }
        }
    }

    /// Push current UserDefaults values to the backend (best-effort, authed only).
    /// No-ops until the first successful `hydrate()` (see `hasHydrated`).
    func push() {
        guard isAuthenticated, hasHydrated else { return }
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
        for key in Self.doubleKeys where defaults.object(forKey: key) != nil {
            blob[key] = .double(defaults.double(forKey: key))
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
        // Let open screens refresh from the store (e.g. the appearance picker).
        NotificationCenter.default.post(name: .caydexSettingsHydrated, object: nil)
    }
}
