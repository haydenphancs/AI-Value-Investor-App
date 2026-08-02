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

    /// Drop this device's synced preferences because the session that owned them ended.
    ///
    /// These keys are DEVICE-GLOBAL — none carries a user id — and `hydrate()` only ever
    /// overwrites a key the server actually returns. So after user A signed out, every
    /// preference A had set stayed in UserDefaults, and user B signing in on the same device
    /// inherited them: A's default persona, A's playback speed, A's appearance choice, and A's
    /// notification opt-ins. B never chose any of it, and the notification toggles are the
    /// sharp edge — B could be silently opted INTO alerts, or out of ones they expected.
    ///
    /// Worse, it was durable rather than cosmetic: the next `push()` writes those values up as
    /// B's own preferences, so A's settings become B's on every one of B's devices.
    ///
    /// Mirrors `AppState.discardLearnDataForEndedSession()`, and costs a signed-in user
    /// nothing — their real values are on the server and `hydrate()` restores them at the next
    /// sign-in. Removing the keys (rather than writing defaults) lets each screen fall back to
    /// its own declared default.
    func clearLocalForEndedSession() {
        for key in Self.boolKeys + Self.stringKeys + Self.doubleKeys {
            defaults.removeObject(forKey: key)
        }
        // The appearance override is applied to the window, not just stored, so re-apply the
        // now-default value or the previous user's Light/Dark choice stays on screen.
        AppearanceManager.applyStored()
        // Same signal the hydrate path posts, so any open settings screen re-reads its
        // @AppStorage-backed rows instead of showing the previous account's values.
        NotificationCenter.default.post(name: .caydexSettingsHydrated, object: nil)
    }

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
        guard !isHydrating else { return }   // a bounce between settings screens must not storm
        isHydrating = true
        hasHydrated = false   // re-gate for this (possibly new) session
        Task {
            defer { isHydrating = false }
            do {
                let prefs = try await repository.fetchSettings()
                apply(prefs)
                // Re-assert anything the user changed WHILE the hydrate was gated, so the
                // server blob does not silently revert their choice. Local wins here because
                // it is strictly newer than the response now being applied.
                if let deferred = pendingBlob {
                    pendingBlob = nil
                    applyLocalOverrides(deferred)
                }
                hasHydrated = true   // safe to push now (server state is known)
                if deferredPushPending {
                    deferredPushPending = false
                    push()
                }
            } catch {
                // Leave hasHydrated false so push() stays gated — we don't know the
                // server state, so pushing would risk a clobber. A later hydrate retries.
                #if DEBUG
                print("⚠️ [Settings] hydrate failed: \(AppError.from(error).message)")
                #endif
            }
        }
    }

    /// Local snapshot captured while `push()` was gated on an un-hydrated session. Held so the
    /// change survives to the next successful hydrate instead of being dropped.
    private var pendingBlob: [String: PreferenceValue]?
    /// A push was requested while gated; fire it once the hydrate lands.
    private var deferredPushPending = false
    /// In-flight guard so repeated `push()` calls can't launch a hydrate storm while offline.
    private var isHydrating = false

    /// Push current UserDefaults values to the backend (best-effort, authed only).
    /// No-ops until the first successful `hydrate()` (see `hasHydrated`).
    func push() {
        guard isAuthenticated else { return }
        guard hasHydrated else {
            // DEFER, don't discard. This gate exists so a partial local snapshot can't clobber
            // richer server settings — correct — but it used to `return` and lose the change
            // entirely. On a launch where the hydrate failed (offline, 5xx), every settings
            // change the user made for the rest of that session was silently dropped: nothing
            // was PUT, and the next successful hydrate then overwrote their local values with
            // the stale server blob. The toggle they flipped simply flipped back.
            //
            // Hold the snapshot, retry the hydrate, and re-assert these keys over the response.
            pendingBlob = currentBlob()
            deferredPushPending = true
            #if DEBUG
            print("ℹ️ [Settings] push deferred (not hydrated) — retrying hydrate")
            #endif
            hydrate()
            return
        }
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

    /// Re-assert values the user changed while `push()` was gated, on top of a just-applied
    /// server blob. Same write path as `apply`, but semantically the opposite direction: these
    /// are strictly NEWER than the response, so local wins.
    private func applyLocalOverrides(_ blob: [String: PreferenceValue]) {
        guard !blob.isEmpty else { return }
        apply(blob)
    }
}
