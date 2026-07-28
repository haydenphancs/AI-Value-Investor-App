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
}
