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
//  ⚠️ THE LOCK LIVES IN ITS OWN `UIWindow`, NOT IN THE VIEW TREE. THIS IS LOAD-BEARING.
//
//  It used to be an `.overlay { if appLock.isLocked { AppLockView() } }` on the root view in
//  `iosApp.swift`, with `zIndex(1000)`. A `.fullScreenCover` / `.sheet` is a separate presented
//  `UIViewController` drawn ABOVE the entire root hierarchy, so `zIndex` — which only orders
//  siblings inside one hierarchy — could not reach it. The lock rendered BEHIND every modal in
//  the app: Account, ticker detail, Cay AI chat, Buy Credits, Sign In.
//
//  So the privacy control failed open in the ordinary case: background the app with any modal
//  open, come back, and the content sat there fully readable — an account screen showing an
//  email address and a credit balance, or a chat transcript with the user's holdings pasted
//  into it. The lock screen was underneath it, waiting, invisible.
//
//  A window at `.alert + 1` is above every modal presentation unconditionally, needs no
//  cooperation from any screen, and covers presentations that do not exist yet. The alternative
//  considered — dismissing every presented cover on lock — throws the user out of whatever they
//  were reading and needs every presentation site to stay dismissible from one place.
//
//  Reconciliation is idempotent (`syncLockWindow()`), so the scene-connected path and the
//  `isLocked` path can both drive it without ordering rules.
//

import LocalAuthentication
import SwiftUI
import UIKit

@Observable
@MainActor
final class AppLockManager {

    static let shared = AppLockManager()

    private static let enabledKey = "app_lock_enabled"

    /// True while the lock screen is covering the app (awaiting unlock).
    ///
    /// Written only through `setLocked(_:)`, which also reconciles the window. A `didSet` would
    /// read better but `@Observable` transforms stored properties into computed ones, so a
    /// property observer here is not a reliable place to hang a side effect.
    private(set) var isLocked: Bool = false

    /// The window hosting `AppLockView`. Non-nil exactly while locked AND a scene exists.
    /// Not `@ObservationIgnored`-sensitive — nothing observes it; it is pure UIKit plumbing.
    private var lockWindow: UIWindow?

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
        // Require an unlock on cold launch when the feature is on. No `UIWindowScene` exists
        // yet during `AppLockManager.shared` initialisation, so the window cannot be built here;
        // `syncLockWindow()` from `iosApp`'s launch task raises it. That call is idempotent, so
        // the cold-launch and background-return paths need no ordering agreement.
        if isEnabled { isLocked = true }
    }

    // MARK: - The lock window

    /// The ONE writer of `isLocked`. Every state change reconciles the window in the same step,
    /// so the flag and what is actually on screen cannot disagree.
    private func setLocked(_ locked: Bool) {
        guard isLocked != locked else { return }
        isLocked = locked
        syncLockWindow()
    }

    /// Bring the window in line with `isLocked`. Idempotent and safe to call at any time,
    /// including before a scene exists (it becomes a no-op and the next call picks it up).
    ///
    /// Call sites: `setLocked(_:)`, `iosApp`'s launch `.task`, and `didBecomeActive` (which
    /// covers a scene that reconnected while the app was suspended — the same gap
    /// `AppearanceManager.applyStored()` is called there for).
    func syncLockWindow() {
        isLocked ? presentLockWindow() : dismissLockWindow()
    }

    private func presentLockWindow() {
        guard lockWindow == nil else { return }
        guard let scene = UIApplication.shared.connectedScenes
            .compactMap({ $0 as? UIWindowScene })
            .first(where: { $0.activationState != .background })
            ?? UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first
        else { return }   // no scene yet — `syncLockWindow()` runs again once there is one

        let controller = UIHostingController(rootView: AppLockView())
        // The lock is opaque by design: a transparent host would let the covered content show
        // through, which is the entire thing this exists to prevent.
        controller.view.backgroundColor = UIColor(AppColors.background)

        let window = UIWindow(windowScene: scene)
        window.rootViewController = controller
        // Above every modal presentation, including ones that do not exist yet. `.alert` is the
        // highest documented level UIKit itself uses; +1 keeps us above a system alert raised by
        // the app, while staying below the OS-owned status/keyguard layers we cannot occupy.
        window.windowLevel = .alert + 1
        // The user's Light/Dark/System choice, stamped at creation. `AppearanceManager.apply()`
        // walks `windowScene.windows` and will keep it in step afterwards, but it only touches
        // windows that exist AT CALL TIME — so without this the lock screen would render in the
        // OS style until the next appearance write.
        window.overrideUserInterfaceStyle = AppearanceManager.current.interfaceStyle
        // Key, so the unlock button and the biometric prompt receive touches.
        window.makeKeyAndVisible()

        lockWindow = window
    }

    private func dismissLockWindow() {
        guard let window = lockWindow else { return }
        lockWindow = nil
        window.isHidden = true
        // Hand key status back explicitly. Dropping the reference alone leaves the app with no
        // key window in some scene configurations, which silently breaks keyboard input on the
        // screen the user returns to.
        window.windowScene?.windows.first { $0 !== window && !$0.isHidden }?.makeKey()
        window.rootViewController = nil
        window.windowScene = nil
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
            setLocked(false)
            return false
        }
    }

    /// Lock the app if the feature is enabled (call when the app backgrounds).
    func lockIfEnabled() {
        if isEnabled { setLocked(true) }
    }

    /// Prompt for biometrics/passcode; clears `isLocked` on success.
    @discardableResult
    func authenticate(reason: String = "Unlock Caydex") async -> Bool {
        let ctx = LAContext()
        var err: NSError?
        guard ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: &err) else {
            // No biometrics/passcode configured → don't trap the user behind a lock
            // they can't clear.
            setLocked(false)
            return false
        }
        return await withCheckedContinuation { continuation in
            ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { success, _ in
                Task { @MainActor in
                    if success { self.setLocked(false) }
                    continuation.resume(returning: success)
                }
            }
        }
    }
}
