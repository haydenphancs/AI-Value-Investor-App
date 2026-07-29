//
//  AppLockManager.swift
//  ios
//
//  Optional biometric/passcode App Lock (fintech trust feature). When enabled, the
//  app is covered by AppLockView on cold launch and after backgrounding until the
//  user passes Face ID / Touch ID / passcode. Device-local (never synced).
//
//  Requires NSFaceIDUsageDescription in Info.plist for Face ID.
//

import LocalAuthentication
import SwiftUI

@Observable
@MainActor
final class AppLockManager {

    static let shared = AppLockManager()

    private static let enabledKey = "app_lock_enabled"

    /// True while the lock screen is covering the app (awaiting unlock).
    var isLocked: Bool = false

    var isEnabled: Bool { UserDefaults.standard.bool(forKey: Self.enabledKey) }

    /// Whether the device can do biometrics OR passcode (else App Lock can't be offered).
    var isAvailable: Bool {
        LAContext().canEvaluatePolicy(.deviceOwnerAuthentication, error: nil)
    }

    /// "Face ID" / "Touch ID" / "passcode" for UI copy.
    var biometryLabel: String {
        let ctx = LAContext()
        _ = ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: nil)
        switch ctx.biometryType {
        case .faceID: return "Face ID"
        case .touchID: return "Touch ID"
        default: return "passcode"
        }
    }

    private init() {
        // Require an unlock on cold launch when the feature is on.
        if isEnabled { isLocked = true }
    }

    /// Turn App Lock on (requires a successful auth) or off (immediate).
    /// Returns the resulting enabled state.
    @discardableResult
    func setEnabled(_ enabled: Bool) async -> Bool {
        if enabled {
            let ok = await authenticate(reason: "Enable App Lock")
            if ok { UserDefaults.standard.set(true, forKey: Self.enabledKey) }
            return ok
        } else {
            UserDefaults.standard.set(false, forKey: Self.enabledKey)
            isLocked = false
            return false
        }
    }

    /// Lock the app if the feature is enabled (call when the app backgrounds).
    func lockIfEnabled() {
        if isEnabled { isLocked = true }
    }

    /// Prompt for biometrics/passcode; clears `isLocked` on success.
    @discardableResult
    func authenticate(reason: String = "Unlock Caydex") async -> Bool {
        let ctx = LAContext()
        var err: NSError?
        guard ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: &err) else {
            // No biometrics/passcode configured → don't trap the user behind a lock
            // they can't clear.
            isLocked = false
            return false
        }
        return await withCheckedContinuation { continuation in
            ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { success, _ in
                Task { @MainActor in
                    if success { self.isLocked = false }
                    continuation.resume(returning: success)
                }
            }
        }
    }
}
