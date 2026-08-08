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
import UIKit   // willEnterForegroundNotification — see `configure(appState:)`

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
        "notify_price_alerts",
        "notify_quiet_hours_enabled",
        "haptic_feedback", "autoplay_next",
    ]

    // String preferences (persona, appearance, quiet hours, timezone).
    // Currency is USD-only (no picker).
    //
    // The quiet-hours pair is `"HH:MM"` (24-hour, zero-padded) and the timezone is an
    // IANA identifier from `TimeZone.current.identifier`. Both are read by the BACKEND
    // — `quiet_hours.py` parses them to decide whether a notification buzzes now or is
    // parked until the window ends — so the formats are a contract, not a display
    // choice. The backend rejects anything else and degrades to "not quiet" rather
    // than guessing, so a malformed value costs a notification's timing, never its
    // delivery.
    static let stringKeys: [String] = [
        "default_persona", AppearanceManager.storageKey,
        "notify_quiet_start", "notify_quiet_end", "notify_timezone",
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
        // Same reasoning for the in-flight PUT and the retry ladder: a rung firing into the
        // NEXT account, or a stale push confirming and stamping `lastServerBlob`, is the same
        // "state from the ended session leaks forward" class the isHydrating reset fixed.
        // Bumping `localVersion` also neutralises any push already awaiting its response.
        pushTask?.cancel()
        pushTask = nil
        cancelSyncRetry()
        redriveDepth = 0
        bumpLocalVersion()
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

        guard !hasInstalledObservers else { return }
        hasInstalledObservers = true

        // These live HERE rather than in `AppState.configure`, and that is load-bearing.
        //
        // AppState observes both of these already, but its handler calls
        // `restoreSessionIfNeeded`, which early-returns unless there is an unused stored
        // credential — i.e. exactly NOT the case this exists for: a perfectly healthy
        // session where one push happened to fail on a network blip. Driving the settings
        // retry from there would mean it never fires when it is needed.
        //
        // Never removed: this is a `@MainActor` singleton with process lifetime, the same
        // arrangement as AppState's own two observers.
        NotificationCenter.default.addObserver(
            forName: NetworkMonitor.didRestoreNotification, object: nil, queue: .main
        ) { _ in
            Task { @MainActor in
                SettingsSyncManager.shared.resumeSyncIfNeeded(trigger: "network-restored")
            }
        }
        NotificationCenter.default.addObserver(
            forName: UIApplication.willEnterForegroundNotification, object: nil, queue: .main
        ) { _ in
            Task { @MainActor in
                SettingsSyncManager.shared.resumeSyncIfNeeded(trigger: "foreground")
            }
        }
    }

    /// "Something changed — do we owe the server anything?"
    ///
    /// Self-gating and idempotent, so every trigger can call it unconditionally and none of
    /// them needs to know the state machine. Costs ZERO network calls when nothing is
    /// pending, which is what lets `AppState`'s same-user reconnect path call it without
    /// reintroducing the per-reconnect fan-out that guard exists to prevent.
    func resumeSyncIfNeeded(trigger: String) {
        guard isAuthenticated else { return }   // guests and `.restoring` keep holding their keys
        if !hasHydrated {
            hydrate()                            // re-drives the push via `deferredPushPending`
        } else if deferredPushPending || !pendingKeys.isEmpty {
            #if DEBUG
            print("ℹ️ [Settings] resuming deferred push (\(trigger))")
            #endif
            push()
        }
        // Deliberately does NOT reset the retry ladder — see `scheduleSyncRetry`.
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
        // A hydrate is about to REWRITE local state, so any push already on the wire is
        // describing a snapshot that is going away. Bump before the fetch so its confirmation
        // cannot stamp `lastServerBlob`, and cancel it so it does not linger.
        bumpLocalVersion()
        pushTask?.cancel()
        Task {
            // NOT a bare `defer { isHydrating = false }`. The epoch-mismatch branch below
            // re-drives hydrate() from INSIDE this task, so the defer ran AFTER the new
            // hydrate had set the flag and spawned its own Task — clearing it and leaving
            // the `guard !isHydrating` storm guard at the top of hydrate() wide open for the
            // rest of the session. Clear the flag FIRST, re-drive AFTER.
            var redrive = false
            defer {
                isHydrating = false
                if redrive { hydrate() }
            }
            do {
                let prefs = try await repository.fetchSettings()
                guard epoch == LearnIdentityEpoch.current else {
                    // These are the ENDED session's preferences. Drop them, and leave
                    // `hasHydrated` false so the new identity's own hydrate still has to run.
                    //
                    // Re-drive for the CURRENT identity rather than just returning: this
                    // branch used to leave the new account with `hasHydrated == false` and
                    // nothing scheduled, so its push stayed gated until something else
                    // happened to call hydrate() again. Bounded, so a burst of account
                    // switches cannot recurse one GET per bounce.
                    #if DEBUG
                    print("[SettingsSync] discarded a hydrate from a previous identity")
                    #endif
                    redrive = redriveDepth < 2
                    redriveDepth += 1
                    return
                }
                redriveDepth = 0
                // Record the server's blob VERBATIM before applying it, so the next PUT can
                // preserve keys this build does not know about (see `lastServerBlob`).
                lastServerBlob = prefs
                let rejected = apply(prefs)
                if !rejected.isEmpty { healRejectedKeys(rejected) }
                // Re-assert only the keys the user actually changed while the hydrate was
                // gated — NOT the whole local blob.
                //
                // The old code snapshotted every key at defer time and replayed all of them,
                // so one appearance tap made on an un-hydrated launch also re-asserted 17
                // stale toggles over the server's newer values. Replaying a diff means an
                // edit on device A can no longer resurrect device B's superseded settings.
                let dirty = pendingKeys
                if !dirty.isEmpty {
                    // `pendingKeys = []` used to live here, and it was the same silent lost
                    // edit as the one in push(): if the push below then failed, those keys
                    // were gone and the NEXT hydrate overwrote the user's values with the
                    // stale server blob. Only a CONFIRMED push clears a key now.
                    applyLocalOverrides(currentBlob().filter { dirty.contains($0.key) })
                }
                hasHydrated = true   // safe to push now (server state is known)
                cancelSyncRetry()    // a success, and the only thing that resets the ladder
                if deferredPushPending || !dirty.isEmpty {
                    deferredPushPending = false
                    push()
                }
            } catch {
                if wasSuperseded { return }
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
                scheduleSyncRetry(after: error)
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

    /// Installed once, from `configure(appState:)`.
    private var hasInstalledObservers = false

    // MARK: - Ordering, single-flight, and retry

    /// Monotonic counter of LOCAL writes — a push capturing its snapshot, a hydrate about to
    /// overwrite the store, a session ending. Every request snapshots it before awaiting; a
    /// response carrying a stale snapshot describes state that has since moved on and must
    /// not be committed.
    ///
    /// TWO guards, answering different questions — the same arrangement `BookmarkStore` and
    /// `MoneyMovesProgressStore` already use. The EPOCH catches the account changing
    /// underneath; it cannot see a second local edit, because one identity keeps one epoch
    /// for its whole session. This manager had only the epoch half, so two pushes 200ms apart
    /// raced: `lastServerBlob = blob` landed in arbitrary completion order and poisoned the
    /// overlay that builds the NEXT push.
    private var localVersion: UInt64 = 0
    private func bumpLocalVersion() { localVersion &+= 1 }

    /// The single in-flight PUT. Cancel-and-replace is safe here specifically because the
    /// endpoint is a FULL REPLACE and a newer blob is built by overlaying onto the same
    /// `lastServerBlob` — so it strictly supersedes an older one. A cancelled push loses
    /// nothing: only a CONFIRMED push clears `pendingKeys`.
    private var pushTask: Task<Void, Never>?

    /// Bounded backoff after a TRANSIENT failure. Same ladder as
    /// `AppState.scheduleRestoreBackoff`, for the same reason: a blip must not strand the
    /// user's settings for a whole app run. The `catch` here used to be analytics and a
    /// DEBUG print, with no driver anywhere — a failed hydrate left `hasHydrated` false,
    /// which gates every later push, so one 5xx stopped settings syncing for the session.
    private static let retryDelays: [UInt64] = [2, 8, 30, 120, 300]
    private var retryAttempt = 0
    private var retryTask: Task<Void, Never>?

    /// Depth cap on the epoch-mismatch re-drive, so rapid account switching cannot recurse
    /// one GET per bounce.
    private var redriveDepth = 0

    private func scheduleSyncRetry(after error: Error) {
        // A dead credential is AppState's problem — it ends the session. Retrying it burns a
        // request per rung and hides a session that has actually ended.
        guard !AppError.from(error).isAuthError else { return }
        retryTask?.cancel()
        let seconds = Self.retryDelays[min(retryAttempt, Self.retryDelays.count - 1)]
        retryAttempt += 1
        retryTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: seconds * 1_000_000_000)
            guard !Task.isCancelled else { return }
            self?.resumeSyncIfNeeded(trigger: "backoff")
        }
    }

    /// Only a SUCCESS resets the ladder. A rung firing must not, or the delay would restart
    /// at 2s every time and never escalate.
    private func cancelSyncRetry() {
        retryTask?.cancel()
        retryTask = nil
        retryAttempt = 0
    }

    /// Was this request superseded rather than genuinely failing?
    ///
    /// `APIClient` wraps anything it does not recognise into `APIError.networkError(_)`, so a
    /// cancellation arrives NESTED and `catch is CancellationError` never fires.
    /// `Task.isCancelled` is true regardless of how the underlying error got wrapped — without
    /// this check every superseded push would report `backgroundSyncFailed` and arm the ladder.
    private var wasSuperseded: Bool { Task.isCancelled }

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
        // A push IS a local write from the sync machine's point of view: the snapshot it is
        // about to send is newer than anything already on the wire.
        bumpLocalVersion()
        let token = localVersion

        // Cancel-and-replace. Two rapid pushes — the settings screen's `.onDisappear` and the
        // appearance picker's `didSet` are ~200ms apart in practice — each used to get a bare
        // `Task`, so `lastServerBlob = blob` landed in arbitrary completion order and left the
        // cached "confirmed" blob describing a row the server does not hold, poisoning the
        // overlay that builds the next push. Cancelling loses nothing: only a CONFIRMED push
        // clears `pendingKeys`, so at worst this costs one redundant PUT, and the endpoint is
        // idempotent by construction (full replace).
        pushTask?.cancel()
        pushTask = Task { @MainActor [weak self] in
            guard let self else { return }
            guard epoch == LearnIdentityEpoch.current else {
                #if DEBUG
                print("[SettingsSync] dropped a push from a previous identity")
                #endif
                return
            }
            do {
                _ = try await self.repository.updateSettings(blob)
                // TWO guards, answering different questions. `epoch` catches the ACCOUNT
                // changing underneath — `localVersion` cannot see that, because each identity
                // starts from its own local state. `token` catches a newer local edit or a
                // newer push having superseded this blob; without it a stale response stamps
                // `lastServerBlob` with a row the server no longer holds.
                guard epoch == LearnIdentityEpoch.current, token == self.localVersion else { return }

                // The server has now confirmed exactly this blob.
                self.lastServerBlob = blob
                // `pendingKeys = []` was a silent lost edit: a key dirtied AFTER this blob was
                // captured got cleared without ever being sent. Retain a key iff its CURRENT
                // local value still differs from what the server just confirmed — the same
                // "diff against the last confirmed blob" definition `deferLocalChange()` uses,
                // so there is exactly one meaning of "dirty" in this file.
                let local = self.currentBlob()
                self.pendingKeys = self.pendingKeys.filter { key in
                    guard let localValue = local[key] else { return false }
                    return blob[key] != localValue
                }
                self.deferredPushPending = false
                self.cancelSyncRetry()
            } catch {
                if self.wasSuperseded { return }   // superseded; its keys are still pending
                // Release-visible: the toggle the user just flipped is now local-only and will
                // be overwritten by the next hydrate.
                Analytics.shared.track(.backgroundSyncFailed, [
                    "op": .string("settings_push"),
                    "code": .string(AppError.from(error).analyticsCode),
                ])
                #if DEBUG
                print("⚠️ [Settings] push failed: \(AppError.from(error).message)")
                #endif
                self.scheduleSyncRetry(after: error)
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

    /// The declared type of every synced key.
    private enum PreferenceKind { case bool, string, double }

    private static func kind(of key: String) -> PreferenceKind? {
        if boolKeys.contains(key) { return .bool }
        if stringKeys.contains(key) { return .string }
        if doubleKeys.contains(key) { return .double }
        return nil
    }

    /// Write the server's blob into UserDefaults. Returns the keys it REFUSED, so the caller
    /// can heal them (see `healRejectedKeys`).
    @discardableResult
    private func apply(_ prefs: [String: PreferenceValue]) -> Set<String> {
        var rejected: Set<String> = []
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
                    rejected.insert(key)
                    continue   // keep the user's local choice; do NOT write junk
                }
                // Write the CANONICAL casing, so a foreign value (lowercase from another
                // client, a hand-edited row) heals locally and the next push repairs the
                // server row — see `currentBlob()`.
                defaults.set(mode.rawValue, forKey: key)
                continue
            }

            // Match the value's type against the KEY's declared type. Only `appearance_mode`
            // was checked before, and a mismatch elsewhere is DURABLY destructive rather than
            // cosmetic: a `boolKeys` member arriving as `.string("off")` was written to
            // UserDefaults as a String, so `@AppStorage<Bool>` fell back to its declared
            // default while `currentBlob()`'s `defaults.bool(forKey:)` read FALSE — and the
            // next push() rewrote the server row to false, on every one of the user's devices.
            // A user's notification toggle turned itself off permanently, from one bad row.
            switch (Self.kind(of: key), value) {
            case (.bool, .bool(let b)):
                defaults.set(b, forKey: key)
            // 0/1 is a legitimate JSON wire form for a bool and `PreferenceValue` decodes a
            // bare number as `.int`, so accepting exactly those two is lossless. Anything else
            // numeric is not a bool and must not be coerced into one.
            case (.bool, .int(let i)) where i == 0 || i == 1:
                defaults.set(i == 1, forKey: key)
            case (.string, .string(let s)):
                defaults.set(s, forKey: key)
            case (.double, .double(let d)):
                defaults.set(d, forKey: key)
            case (.double, .int(let i)):
                defaults.set(Double(i), forKey: key)
            default:
                rejected.insert(key)
                Analytics.shared.track(.backgroundSyncFailed, [
                    "op": .string("settings_hydrate_type"),
                    "code": .string("type_mismatch"),
                ])
                #if DEBUG
                print("⚠️ [Settings] rejected \(value) for \"\(key)\" — keeping the local value")
                #endif
                continue
            }
        }
        // Re-apply appearance in case the synced blob changed it.
        AppearanceManager.applyStored()
        // Let open screens refresh from the store (e.g. the appearance picker).
        NotificationCenter.default.post(name: .caydexSettingsHydrated, object: nil)
        return rejected
    }

    /// A value this build OWNS but could not use must not survive in `lastServerBlob`.
    ///
    /// `push()` builds its blob as `lastServerBlob` overlaid with `currentBlob()`, and
    /// `currentBlob()` SKIPS a key with no UserDefaults entry — so a rejected value for a
    /// toggle the user has never touched would sit in the cached blob and be re-PUT verbatim
    /// for the life of the row. Drop it and mark the key dirty so the next push heals the
    /// server, mirroring what `currentBlob()` already does for a foreign `appearance_mode`.
    ///
    /// Only keys in `syncedKeys` can be rejected, so this never touches the unknown keys
    /// `lastServerBlob` exists to preserve.
    private func healRejectedKeys(_ rejected: Set<String>) {
        guard !rejected.isEmpty else { return }
        lastServerBlob = lastServerBlob.filter { !rejected.contains($0.key) }
        pendingKeys.formUnion(rejected)
        deferredPushPending = true
        bumpLocalVersion()
    }

    /// Re-assert values the user changed while `push()` was gated, on top of a just-applied
    /// server blob. Same write path as `apply`, but semantically the opposite direction: these
    /// are strictly NEWER than the response, so local wins.
    private func applyLocalOverrides(_ blob: [String: PreferenceValue]) {
        guard !blob.isEmpty else { return }
        apply(blob)
    }
}
