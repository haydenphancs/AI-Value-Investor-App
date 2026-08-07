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
    /// Mirrors `AppState.discardDataForEndedSession()`, and costs a signed-in user
    /// nothing — their real values are on the server and `hydrate()` restores them at the next
    /// sign-in. Removing the keys (rather than writing defaults) lets each screen fall back to
    /// its own declared default.
    func clearLocalForEndedSession() {
        for key in Self.boolKeys + Self.stringKeys + Self.doubleKeys {
            defaults.removeObject(forKey: key)
        }
        // Reset the sync state machine too, not just the values. Leaving `hasHydrated` true
        // would let the next account `push()` before its own hydrate had run, and stale
        // pending keys would replay the ended session's edits into it.
        hasHydrated = false
        pendingKeys = []
        deferredPushPending = false
        lastServerBlob = [:]
        // `isHydrating` must reset too, and it did not.
        //
        // It is an in-flight guard, but it is not identity-scoped: if A's fetch was still in
        // flight at sign-out, it stayed true, and B's hydrate then hit `guard !isHydrating`
        // and returned having done NOTHING. A's response is correctly discarded by the epoch
        // check, so nothing ever set `hasHydrated` for B — leaving every one of B's pushes
        // gated for the whole session. The in-flight response cannot hurt us (the epoch guard
        // owns that), so clearing the flag here is safe and is what lets B hydrate at all.
        isHydrating = false
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

    /// We hold a credential we could not validate yet (`.restoring`). NOT a guest.
    ///
    /// `push()` used to fall through `guard isAuthenticated` and RETURN here, silently
    /// discarding the change — which is the same class of loss the `hasHydrated` gate was
    /// rewritten to avoid, on a path that is common rather than rare: `.restoring` is
    /// entered on every transient network failure and every launch on a flaky connection.
    private var isRestoring: Bool {
        appState?.auth.status == .restoring
    }

    // MARK: - Durable sync state
    //
    // These three survive a relaunch on purpose. Everything the sync machine knows used to
    // live in memory, so a kill (or a jetsam) between "user changed a setting while offline"
    // and "a hydrate finally succeeded" lost the change AND let the stale server value
    // overwrite it — the exact symptom `push()`'s deferral comment says was fixed.

    private static let pendingKeysDefaultsKey = "settings_pending_dirty_keys"
    private static let lastServerBlobDefaultsKey = "settings_last_server_blob"

    /// Keys this device changed locally that the server has not accepted yet.
    private var pendingKeys: Set<String> {
        get { Set(defaults.stringArray(forKey: Self.pendingKeysDefaultsKey) ?? []) }
        set {
            if newValue.isEmpty { defaults.removeObject(forKey: Self.pendingKeysDefaultsKey) }
            else { defaults.set(Array(newValue).sorted(), forKey: Self.pendingKeysDefaultsKey) }
        }
    }

    /// The last blob the server confirmed, verbatim — INCLUDING keys this build does not know.
    ///
    /// Load-bearing for the full-replace contract: the PUT is built by overlaying local values
    /// onto this, so a key introduced by a newer app version survives a push from an older one.
    /// Without it, `currentBlob()` (which iterates only the three static key lists) defines the
    /// whole row, and every key this build has never heard of is DELETED for every device.
    private var lastServerBlob: [String: PreferenceValue] {
        get {
            guard let data = defaults.data(forKey: Self.lastServerBlobDefaultsKey),
                  let decoded = try? JSONDecoder().decode([String: PreferenceValue].self, from: data)
            else { return [:] }
            return decoded
        }
        set {
            guard let data = try? JSONEncoder().encode(newValue) else { return }
            defaults.set(data, forKey: Self.lastServerBlobDefaultsKey)
        }
    }

    /// Pull the backend preference blob into UserDefaults (authed only).
    /// Blocks `push()` until it completes so a partial local snapshot can't clobber
    /// richer server settings on a fresh install / new session.
    func hydrate() {
        guard isAuthenticated else { return }
        guard !isHydrating else { return }   // a bounce between settings screens must not storm
        isHydrating = true
        hasHydrated = false   // re-gate for this (possibly new) session
        // Snapshot the Learn identity epoch — it is bumped by
        // `AppState.discardDataForEndedSession()`, i.e. on every session end and account switch.
        //
        // `isHydrating` alone was actively harmful across an identity change. A's fetch is in
        // flight when A signs out and B signs in; B's hydrate hits the `isHydrating` guard and
        // returns having done NOTHING, then A's response lands and `apply(prefs)` writes A's
        // persona, appearance, playback speed and 13 notification toggles onto B's device —
        // and B's next change calls `push()`, which is now ungated, sending A's blob up as B's
        // own to every one of B's devices. The four Learn stores solve this exact race with the
        // epoch; this one had no equivalent.
        let epoch = LearnIdentityEpoch.current
        Task {
            defer { isHydrating = false }
            do {
                let prefs = try await repository.fetchSettings()
                guard epoch == LearnIdentityEpoch.current else {
                    // These are the ENDED session's preferences. Drop them, and leave
                    // `hasHydrated` false so the new identity's own hydrate still has to run.
                    //
                    // Re-drive for the CURRENT identity rather than just returning: this
                    // branch used to leave the new account with `hasHydrated == false` and
                    // nothing scheduled, so its push stayed gated until something else
                    // happened to call hydrate() again.
                    #if DEBUG
                    print("[SettingsSync] discarded a hydrate from a previous identity")
                    #endif
                    isHydrating = false
                    hydrate()
                    return
                }
                // Record the server's blob VERBATIM before applying it, so the next PUT can
                // preserve keys this build does not know about (see `lastServerBlob`).
                lastServerBlob = prefs
                apply(prefs)
                // Re-assert only the keys the user actually changed while the hydrate was
                // gated — NOT the whole local blob.
                //
                // The old code snapshotted every key at defer time and replayed all of them,
                // so one appearance tap made on an un-hydrated launch also re-asserted 17
                // stale toggles over the server's newer values. Replaying a diff means an
                // edit on device A can no longer resurrect device B's superseded settings.
                let dirty = pendingKeys
                if !dirty.isEmpty {
                    pendingKeys = []
                    applyLocalOverrides(currentBlob().filter { dirty.contains($0.key) })
                }
                hasHydrated = true   // safe to push now (server state is known)
                if deferredPushPending || !dirty.isEmpty {
                    deferredPushPending = false
                    push()
                }
            } catch {
                // Leave hasHydrated false so push() stays gated — we don't know the
                // server state, so pushing would risk a clobber. A later hydrate retries.
                // Release-visible: a failed hydrate leaves `hasHydrated` false, which GATES
                // every subsequent push — so one silent failure here quietly stops the user's
                // settings from syncing at all for the rest of the session.
                Analytics.shared.track(.backgroundSyncFailed, [
                    "op": .string("settings_hydrate"),
                    "code": .string(AppError.from(error).analyticsCode),
                ])
                #if DEBUG
                print("⚠️ [Settings] hydrate failed: \(AppError.from(error).message)")
                #endif
            }
        }
    }

    /// A push was requested while gated; fire it once the hydrate lands.
    /// (The VALUES live in `pendingKeys` + UserDefaults, which survive a relaunch — this flag
    /// only says "there is something to send", and re-deriving it from a non-empty
    /// `pendingKeys` on the next hydrate is what makes a killed app recover.)
    private var deferredPushPending = false
    /// In-flight guard so repeated `push()` calls can't launch a hydrate storm while offline.
    private var isHydrating = false

    /// Push current UserDefaults values to the backend (best-effort, authed only).
    /// No-ops until the first successful `hydrate()` (see `hasHydrated`).
    func push() {
        // `.restoring` means we HOLD a credential we could not validate — the user is not a
        // guest, and their change must not be thrown away just because the token is in the
        // middle of healing. Defer it like an un-hydrated push; the session heals on its own
        // (AppState.restoreSession) and the pending keys replay then.
        if !isAuthenticated {
            guard isRestoring else { return }
            deferLocalChange()
            return
        }
        guard hasHydrated else {
            // DEFER, don't discard. This gate exists so a partial local snapshot can't clobber
            // richer server settings — correct — but it used to `return` and lose the change
            // entirely. On a launch where the hydrate failed (offline, 5xx), every settings
            // change the user made for the rest of that session was silently dropped: nothing
            // was PUT, and the next successful hydrate then overwrote their local values with
            // the stale server blob. The toggle they flipped simply flipped back.
            //
            // Hold the changed keys, retry the hydrate, and re-assert them over the response.
            deferLocalChange()
            #if DEBUG
            print("ℹ️ [Settings] push deferred (not hydrated) — retrying hydrate")
            #endif
            hydrate()
            return
        }
        // Overlay this build's known keys onto the last confirmed server blob, rather than
        // letting a partial local snapshot define the whole row. The endpoint is a FULL
        // REPLACE, so any key missing from this dictionary is deleted for every device —
        // including keys a NEWER app version added and this one has never heard of.
        var blob = lastServerBlob
        for (key, value) in currentBlob() { blob[key] = value }
        // Same identity race the hydrate path guards: capture the epoch before the Task, so a
        // sign-out + sign-in that lands between here and the token being attached cannot PUT
        // account A's blob with account B's credential.
        let epoch = LearnIdentityEpoch.current
        Task {
            guard epoch == LearnIdentityEpoch.current else {
                #if DEBUG
                print("[SettingsSync] dropped a push from a previous identity")
                #endif
                return
            }
            do {
                _ = try await repository.updateSettings(blob)
                guard epoch == LearnIdentityEpoch.current else { return }
                // The server has now confirmed exactly this blob.
                lastServerBlob = blob
                pendingKeys = []
            } catch {
                // Release-visible: the toggle the user just flipped is now local-only and will
                // be overwritten by the next hydrate.
                Analytics.shared.track(.backgroundSyncFailed, [
                    "op": .string("settings_push"),
                    "code": .string(AppError.from(error).analyticsCode),
                ])
                #if DEBUG
                print("⚠️ [Settings] push failed: \(AppError.from(error).message)")
                #endif
            }
        }
    }

    /// Remember that the local store has moved ahead of the server, and which keys moved.
    ///
    /// Diffed against the last confirmed server blob rather than snapshotting everything, so a
    /// replay re-asserts only what this device actually changed. When no server blob is known
    /// yet (fresh install, never hydrated) every present key counts as dirty — which is the
    /// correct reading there: nothing on the server can be superseded by them.
    private func deferLocalChange() {
        let local = currentBlob()
        let server = lastServerBlob
        let changed = local.filter { key, value in server[key] != value }.keys
        pendingKeys = pendingKeys.union(changed)
        deferredPushPending = true
    }

    /// Keys this build owns. Anything else the server sends is NOT written to UserDefaults.
    private static var syncedKeys: Set<String> {
        Set(boolKeys).union(stringKeys).union(doubleKeys)
    }

    // MARK: - Blob <-> UserDefaults

    private func currentBlob() -> [String: PreferenceValue] {
        var blob: [String: PreferenceValue] = [:]
        for key in Self.boolKeys where defaults.object(forKey: key) != nil {
            blob[key] = .bool(defaults.bool(forKey: key))
        }
        for key in Self.stringKeys {
            guard var value = defaults.string(forKey: key) else { continue }
            // Heal a foreign/legacy value on the way OUT too, so one hydrate plus one
            // push permanently repairs the server row instead of re-uploading junk
            // forever. Unparseable values are left alone — `apply(_:)` already
            // refused to store one, so anything unrecognised here came from outside
            // this key's contract and is not ours to rewrite.
            if key == AppearanceManager.storageKey,
               let mode = AppearanceMode(tolerantRawValue: value) {
                value = mode.rawValue
            }
            blob[key] = .string(value)
        }
        for key in Self.doubleKeys where defaults.object(forKey: key) != nil {
            blob[key] = .double(defaults.double(forKey: key))
        }
        return blob
    }

    private func apply(_ prefs: [String: PreferenceValue]) {
        for (key, value) in prefs {
            // Only keys this build declares are written. `apply()` used to write ANY key the
            // server returned straight into UserDefaults, so a row containing (say)
            // `app_lock_enabled` — documented above as deliberately NOT synced — would set it
            // on this device, and `clearLocalForEndedSession` (which only clears the three
            // declared lists) would then leave it behind for the next account.
            guard Self.syncedKeys.contains(key) else {
                #if DEBUG
                print("⚠️ [Settings] ignored unknown synced key \"\(key)\"")
                #endif
                continue
            }

            // `appearance_mode` is the one synced key this layer has SEMANTICS for, and a bad
            // value is silently destructive: both readers coalesce an unparseable value to
            // `.dark`, turning a "System" user into a "Dark" user with nothing to say why.
            //
            // Validated by KEY, before the value switch — not inside the `.string` arm.
            // `PreferenceValue` decodes Bool → Int → Double → String, so a JSON number would
            // arrive as `.int`, skip a string-only guard entirely, and write an NSNumber. The
            // two readers then disagree (`@AppStorage<String>` falls back to its default while
            // `AppearanceManager` sees the coerced "1"), and because the picker's ViewModel is
            // seeded from the coerced value, the mode the user needs to tap becomes a no-op.
            if key == AppearanceManager.storageKey {
                guard case .string(let s) = value,
                      let mode = AppearanceMode(tolerantRawValue: s) else {
                    Analytics.shared.track(.backgroundSyncFailed, [
                        "op": .string("settings_hydrate_appearance"),
                        "code": .string("invalid_appearance_mode"),
                    ])
                    #if DEBUG
                    print("⚠️ [Settings] rejected unusable appearance_mode \(value) — "
                          + "keeping local \(AppearanceManager.current.rawValue)")
                    #endif
                    continue   // keep the user's local choice; do NOT write junk
                }
                // Write the CANONICAL casing, so a foreign value (lowercase from another
                // client, a hand-edited row) heals locally and the next push repairs the
                // server row — see `currentBlob()`.
                defaults.set(mode.rawValue, forKey: key)
                continue
            }

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
