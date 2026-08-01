//
//  AppDelegate.swift
//  ios
//
//  Minimal UIApplicationDelegate for APNs remote-notification callbacks. Wired via
//  @UIApplicationDelegateAdaptor in iosApp. All token handling is delegated to
//  PushNotificationManager (which registers the token with the backend once the
//  user is signed in).
//

import UIKit
import UserNotifications

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    // APNs handed us a device token → forward to the push manager.
    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        Task { @MainActor in
            PushNotificationManager.shared.didRegister(deviceToken: deviceToken)
        }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        // Expected in the Simulator / when the Push capability isn't provisioned.
        #if DEBUG
        print("⚠️ [Push] didFailToRegisterForRemoteNotifications: \(error.localizedDescription)")
        #endif
    }

    // Show alerts while the app is in the foreground.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .badge, .sound]
    }

    // The TAP. Without this, a notification that says "NVDA moved 8%" opened the app
    // to whatever tab was last used — the alert's whole promise, unfulfilled at the
    // moment of highest intent.
    //
    // The payload keys are set by the backend push dispatcher
    // (`push_dispatch_service` → `data={"kind": ..., "ticker": ...}`).
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let info = response.notification.request.content.userInfo
        guard let ticker = info["ticker"] as? String,
              !ticker.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        await MainActor.run {
            PushNotificationManager.shared.handleTap(ticker: ticker)
        }
    }
}
