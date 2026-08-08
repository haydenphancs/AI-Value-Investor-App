//
//  AppDelegate.swift
//  ios
//
//  UIApplicationDelegate for APNs. Wired via @UIApplicationDelegateAdaptor in iosApp.
//  Token handling is delegated to PushNotificationManager; ROUTING is delegated to
//  NotificationRouter, so the decision "where does this tap land" is testable without
//  launching the app.
//

import UIKit
import UserNotifications

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {

    /// `UNNotificationCategory` identifiers. These MUST match the `thread_id` the backend
    /// registry ships in `aps.category` (`notification_kinds.NotificationKind.thread_id`),
    /// or iOS finds no category and silently renders the notification with no action
    /// buttons — no error, no log, just a banner that does less than it should.
    private enum Category {
        static let watchlist = "watchlist"
        static let earnings = "earnings"
        static let smartMoney = "smart_money"
        static let priceAlert = "price_alert"
        static let app = "app"

        static let all = [watchlist, earnings, smartMoney, priceAlert, app]
    }

    private enum Action {
        static let view = "CAYDEX_VIEW"
        static let markRead = "CAYDEX_MARK_READ"
    }

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        registerCategories()

        // Re-register on EVERY launch when already authorized. Apple requires this —
        // the device token can change (restore from backup, reinstall, OS update) and
        // is only re-delivered in response to a registration call.
        //
        // Without it, `registerForRemoteNotifications()` ran in exactly one place: the
        // grant branch of `requestAuthorization()` during onboarding. A guest who
        // granted there had their token parked in memory, and it died with the
        // process — so iOS reported notifications as authorized while the backend
        // never held a token, forever. Adversarial review caught this; it made push
        // silently unreachable for the app's own default (guest-first) path.
        Task { @MainActor in
            let settings = await UNUserNotificationCenter.current().notificationSettings()
            if settings.authorizationStatus == .authorized
                || settings.authorizationStatus == .provisional {
                UIApplication.shared.registerForRemoteNotifications()
            }
        }
        return true
    }

    /// Give every notification family a "View" and a "Mark as Read" button.
    ///
    /// Registered at launch rather than lazily: iOS matches `aps.category` against the
    /// set registered at the time the notification ARRIVES, so a category registered
    /// later is too late for the notification that needed it.
    private func registerCategories() {
        let view = UNNotificationAction(
            identifier: Action.view,
            title: "View",
            options: [.foreground]
        )
        let markRead = UNNotificationAction(
            identifier: Action.markRead,
            title: "Mark as Read",
            // NOT `.foreground`: marking read should not yank the user into the app.
            options: []
        )

        let categories = Category.all.map { identifier in
            UNNotificationCategory(
                identifier: identifier,
                actions: [view, markRead],
                intentIdentifiers: [],
                options: []
            )
        }
        UNUserNotificationCenter.current().setNotificationCategories(Set(categories))
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
        Analytics.shared.track(.pushRegisterFailed, ["reason": "apns"])
    }

    // Show alerts while the app is in the foreground.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        let info = notification.request.content.userInfo
        await MainActor.run {
            syncBadge(from: info)
            Analytics.shared.track(.pushReceived, ["kind": .string(kindDimension(info))])
        }
        return [.banner, .badge, .sound]
    }

    // The TAP. Without this, a notification that says "NVDA moved 8%" opened the app
    // to whatever tab was last used — the alert's whole promise, unfulfilled at the
    // moment of highest intent.
    //
    // Payload keys are set by the backend push dispatcher: `kind` names the notification
    // type, `route`/`ticker`/`asset_type`/`report_id` name the destination.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let info = response.notification.request.content.userInfo

        // "Mark as Read" is a background action: acknowledge it and stay out of the way.
        if response.actionIdentifier == Action.markRead {
            await MainActor.run {
                Analytics.shared.track(.pushOpened, ["action": "mark_read"])
            }
            return
        }

        let route = NotificationRoute(payload: info)
        await MainActor.run {
            syncBadge(from: info)
            Analytics.shared.track(.pushOpened, [
                "kind": .string(kindDimension(info)),
                "route": .string(route.analyticsName),
            ])
            // EVERY payload routes now, including one with no ticker — it lands in the
            // inbox. Previously a payload without a `ticker` key was a silent no-op: the
            // banner was tappable, the tap did nothing, and nothing was logged.
            PushNotificationManager.shared.handleTap(route: route)
        }
    }

    /// Adopt the badge the server computed, so the app icon agrees with the backend even
    /// if the user never opens the notification.
    ///
    /// Server-computed on purpose: incrementing client-side drifts the moment a
    /// notification is delivered and not opened, and the badge is the one piece of
    /// notification state visible without launching the app.
    @MainActor
    private func syncBadge(from info: [AnyHashable: Any]) {
        guard let aps = info["aps"] as? [String: Any],
              let badge = aps["badge"] as? Int, badge >= 0 else { return }
        UNUserNotificationCenter.current().setBadgeCount(badge) { error in
            guard let error else { return }
            #if DEBUG
            print("⚠️ [Push] setBadgeCount failed: \(error.localizedDescription)")
            #endif
        }
        AppState.notificationUnreadDidChange(badge)
    }

    /// Low-cardinality analytics dimension. `kind` is a fixed registry key, never a
    /// ticker — `props` is for dimensions, not user-specific values.
    private func kindDimension(_ info: [AnyHashable: Any]) -> String {
        (info["kind"] as? String) ?? "unknown"
    }
}
