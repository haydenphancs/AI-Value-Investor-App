//
//  NotificationSettingsViewModel.swift
//  ios
//
//  Owns the Notifications screen's state: the toggle values, the iOS permission status,
//  and the quiet-hours window.
//
//  WHY A VIEWMODEL AT ALL — the screen used to bind `@AppStorage` straight into the
//  rows, which was fine for one toggle and breaks down at fourteen:
//
//    * Nothing knew the iOS permission status, so a user who had denied notifications
//      saw every toggle in the ON position, fully interactive, with no hint that iOS was
//      dropping the lot. That is the single most confusing state this screen can be in.
//    * The backend push happened only in `.onDisappear`, so flipping a toggle and force-
//      quitting lost the change silently.
//    * `@AppStorage` cannot express a GROUP master that disables its children.
//
//  THE ROW MANIFEST IS THE CONTRACT. `groups` below is read by
//  `backend/tests/test_push_preference_typing.py`, which asserts BOTH directions:
//  every visible toggle has a sender behind it, AND every registered sender has a
//  visible toggle. The first prevents a control that does nothing; the second prevents
//  the failure that actually shipped — users receiving alerts with no in-app way to turn
//  them off, only iOS Settings, which kills every type at once and never re-prompts.
//
//  KEEP THE MANIFEST DECLARATIVE. A key computed at runtime is invisible to that guard,
//  which would then pass vacuously.
//

import Combine
import Foundation
import SwiftUI
import UserNotifications

// MARK: - Row manifest

struct NotificationToggleSpec: Identifiable, Hashable {
    /// The `notify_*` preference key. Must match a `NotificationKind.preference_key`
    /// in `backend/app/services/notification_kinds.py`.
    let key: String
    let title: String
    let subtitle: String

    var id: String { key }
}

struct NotificationGroupSpec: Identifiable, Hashable {
    let id: String
    let title: String
    let subtitle: String
    let icon: String
    /// The group master. Turning it off silences every row below it — the backend ANDs
    /// the master into each child's decision, so this is not merely a UI convenience.
    let masterKey: String?
    let rows: [NotificationToggleSpec]
}

@MainActor
final class NotificationSettingsViewModel: ObservableObject {

    // MARK: The manifest

    /// EVERY notification group the app can send, and nothing else.
    ///
    /// ⚠️ Scanned by `test_push_preference_typing.py` inside a brace-bounded window with
    /// comments stripped. Adding a row here without a backend `NotificationKind` fails
    /// the build, and vice versa — deliberately, in both directions.
    static let groups: [NotificationGroupSpec] = [
        NotificationGroupSpec(
            id: "watchlist",
            title: "Watchlist",
            subtitle: "Unusual moves and price targets",
            icon: "chart.line.uptrend.xyaxis",
            masterKey: nil,
            rows: [
                NotificationToggleSpec(
                    key: "notify_watchlist_changes",
                    title: "Unusual Price Moves",
                    subtitle: "When a stock you track moves far more than it normally does"
                ),
                NotificationToggleSpec(
                    key: "notify_price_alerts",
                    title: "My Price Alerts",
                    subtitle: "Targets you set yourself, from the bell on any ticker"
                ),
            ]
        ),
        NotificationGroupSpec(
            id: "earnings",
            title: "Earnings",
            subtitle: "Upcoming reports and results",
            icon: "calendar.badge.clock",
            masterKey: "notify_earnings_alerts",
            rows: [
                NotificationToggleSpec(
                    key: "notify_earnings_upcoming",
                    title: "Reporting Tomorrow",
                    subtitle: "A heads-up the day before a company you track reports"
                ),
                NotificationToggleSpec(
                    key: "notify_earnings_surprises",
                    title: "Results & Surprises",
                    subtitle: "When EPS lands well above or below consensus"
                ),
            ]
        ),
        NotificationGroupSpec(
            id: "smart_money",
            title: "Smart Money",
            subtitle: "Insiders, institutions and Congress",
            icon: "building.columns.fill",
            masterKey: "notify_smart_money",
            rows: [
                NotificationToggleSpec(
                    key: "notify_smart_money_insider",
                    title: "Insider Trades",
                    subtitle: "Form 4 buys and sells above $100K"
                ),
                NotificationToggleSpec(
                    key: "notify_smart_money_whale",
                    title: "Congressional Trades",
                    subtitle: "Disclosed trades by members of Congress"
                ),
                NotificationToggleSpec(
                    key: "notify_smart_money_institutional",
                    title: "Institutional Filings",
                    subtitle: "13F filings — disclosed up to 45 days after the trade"
                ),
            ]
        ),
        NotificationGroupSpec(
            id: "match",
            title: "Topics You Follow",
            subtitle: "Based on the interests you picked",
            icon: AppSymbols.ai,
            masterKey: nil,
            rows: [
                NotificationToggleSpec(
                    key: "notify_profile_topics",
                    title: "Signals in Your Topics",
                    // Informational, never directive (SYSTEM_DESIGN_GUIDELINES §11.7):
                    // describes what happened in an area they follow, and makes no claim
                    // that anything is worth owning.
                    subtitle: "When institutions or Congress are active in an area you follow"
                ),
            ]
        ),
        NotificationGroupSpec(
            id: "app",
            title: "App Activity",
            subtitle: "Things you asked for",
            icon: "app.badge.fill",
            masterKey: nil,
            rows: [
                NotificationToggleSpec(
                    key: "notify_research_complete",
                    title: "Report Ready",
                    subtitle: "When an AI analysis you started finishes"
                ),
                // The mirror of the row above, and the one the user needs MORE. A failed
                // report is refunded silently: no report, no explanation, and a balance
                // that quietly went back up. This toggle exists because the registry
                // requires one — a kind with no visible switch is an alert with no in-app
                // opt-out, which shipped once for a week.
                NotificationToggleSpec(
                    key: "notify_research_failed",
                    title: "Report Didn't Finish",
                    subtitle: "When an analysis fails and your credits are returned"
                ),
            ]
        ),
    ]

    // MARK: Published state

    @Published var toggles: [String: Bool] = [:]
    @Published var permission: UNAuthorizationStatus = .notDetermined
    @Published var quietHoursEnabled: Bool = false
    @Published var quietStart: Date = NotificationSettingsViewModel.time(from: "22:00")
    @Published var quietEnd: Date = NotificationSettingsViewModel.time(from: "07:00")

    /// True when iOS will drop everything regardless of what this screen says. Drives the
    /// banner and dims the rows — a toggle that cannot possibly take effect must not look
    /// like one that can.
    var systemNotificationsBlocked: Bool {
        permission == .denied
    }

    /// iOS granted permission, but this device has never been registered with the backend —
    /// so nothing can be delivered, and every toggle below is inert.
    ///
    /// A REACHABLE state that used to render as perfectly healthy. `didFailToRegister` records
    /// nothing, and `systemNotificationsBlocked` only tests `.denied`, so a user whose APNs
    /// registration failed saw full-opacity rows and a screen with no banner. That is the one
    /// axis `NotificationPermissionBanner` was written to cover and did not.
    ///
    /// Recomputed by `refreshPermission()` rather than being a computed property over the
    /// manager, so a `@Published` change actually redraws the view.
    @Published private(set) var deviceUnregistered: Bool = false

    // MARK: Lifecycle

    /// Read every declared key out of UserDefaults, applying each key's declared default
    /// when it has never been set.
    ///
    /// The default matters more than it looks: `SettingsSyncManager.currentBlob()` OMITS
    /// any key with no UserDefaults entry, so for a user who has never opened this screen
    /// the BACKEND default is what the toggle actually means. These two maps mirror
    /// `notification_kinds.preference_defaults()` and a test pins them together.
    func load() {
        let defaults = UserDefaults.standard
        var next: [String: Bool] = [:]
        for group in Self.groups {
            if let master = group.masterKey {
                next[master] = defaults.object(forKey: master) as? Bool
                    ?? NotificationsSettingsView.preferenceDefaults[master] ?? true
            }
            for row in group.rows {
                next[row.key] = defaults.object(forKey: row.key) as? Bool
                    ?? NotificationsSettingsView.preferenceDefaults[row.key] ?? true
            }
        }
        toggles = next

        quietHoursEnabled = defaults.object(forKey: "notify_quiet_hours_enabled") as? Bool
            ?? NotificationsSettingsView.preferenceDefaults["notify_quiet_hours_enabled"] ?? false
        quietStart = Self.time(from: defaults.string(forKey: "notify_quiet_start")
            ?? NotificationsSettingsView.preferenceStringDefaults["notify_quiet_start"] ?? "22:00")
        quietEnd = Self.time(from: defaults.string(forKey: "notify_quiet_end")
            ?? NotificationsSettingsView.preferenceStringDefaults["notify_quiet_end"] ?? "07:00")
    }

    func refreshPermission() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        permission = settings.authorizationStatus
        // Only meaningful once iOS has said yes: before that, "no token" is the expected
        // state and saying so would be noise.
        deviceUnregistered = (settings.authorizationStatus == .authorized
                              || settings.authorizationStatus == .provisional)
            && !PushNotificationManager.shared.hasRegisteredToken
    }

    /// Ask for permission when the user has never been asked.
    ///
    /// Only in `.notDetermined`: iOS prompts exactly once, so calling this in `.denied`
    /// is a silent no-op that makes the screen look identical to the granted case. The
    /// banner sends denied users to iOS Settings instead.
    func requestPermissionIfNeeded() {
        guard permission == .notDetermined else { return }
        Task {
            // AWAIT the answer rather than sleeping a guess. The prompt is modal and a person
            // takes seconds to read it, so a fixed 500ms re-read observed `.notDetermined` and
            // left the banner and row styling stale until the screen was re-entered.
            await PushNotificationManager.shared.requestAuthorizationResult()
            await refreshPermission()
        }
    }

    // MARK: Mutation

    func binding(for key: String) -> Binding<Bool> {
        Binding(
            get: { self.toggles[key] ?? true },
            set: { self.setToggle(key, $0) }
        )
    }

    /// Whether a row is disabled because its group master is off.
    ///
    /// Shown rather than hidden: a row that vanishes when the master flips reads as a
    /// bug, and the user loses the ability to see what the group contains.
    func isSuppressedByMaster(_ group: NotificationGroupSpec) -> Bool {
        guard let master = group.masterKey else { return false }
        return !(toggles[master] ?? true)
    }

    func setToggle(_ key: String, _ value: Bool) {
        toggles[key] = value
        UserDefaults.standard.set(value, forKey: key)
        pushNow()
    }

    /// Each quiet-hours setter writes ONLY the key it changed.
    ///
    /// They used to share one `writeQuietTimes()` that wrote BOTH ends from this ViewModel's
    /// copy. That copy is loaded once and can be stale (a hydrate landed after `load()`, or the
    /// other device moved the other end), so changing "From" also rewrote "Until" with the old
    /// value and pushed it — silently reverting the account's other end on every device.
    func setQuietHoursEnabled(_ value: Bool) {
        quietHoursEnabled = value
        let defaults = UserDefaults.standard
        defaults.set(value, forKey: "notify_quiet_hours_enabled")
        // Record the window the screen is SHOWING, but only an end that has never been stored —
        // the first time quiet hours are switched on, so the backend evaluates the hours the
        // user sees. A stored end is left alone: rewriting it from this copy is the revert above.
        if defaults.object(forKey: "notify_quiet_start") == nil {
            writeQuietTime(quietStart, forKey: "notify_quiet_start")
        }
        if defaults.object(forKey: "notify_quiet_end") == nil {
            writeQuietTime(quietEnd, forKey: "notify_quiet_end")
        }
        SettingsSyncManager.shared.refreshDeviceTimezone()
        pushNow()
    }

    func setQuietStart(_ date: Date) {
        quietStart = date
        writeQuietTime(date, forKey: "notify_quiet_start")
        pushNow()
    }

    func setQuietEnd(_ date: Date) {
        quietEnd = date
        writeQuietTime(date, forKey: "notify_quiet_end")
        pushNow()
    }

    /// True when the user has set both ends of the window to the same time.
    ///
    /// The backend treats that as DISABLED rather than as "always quiet", because
    /// silencing the app forever with no error is the worse reading. Surfacing it here
    /// means the user finds out on this screen instead of by never getting a notification.
    var quietWindowIsDegenerate: Bool {
        quietHoursEnabled && Self.hhmm(from: quietStart) == Self.hhmm(from: quietEnd)
    }

    /// Write ONE end of the window. Never both — see the note on the setters above.
    private func writeQuietTime(_ date: Date, forKey key: String) {
        UserDefaults.standard.set(Self.hhmm(from: date), forKey: key)
        // The backend needs the DEVICE's zone to know when 22:00 is.
        //
        // This is no longer the ONLY writer, and it could not be: it runs only when the user
        // touches a quiet-hours control, so a user who never opened this card never sent a
        // timezone at all and the backend fell back to ET — for their quiet window AND for the
        // daily-cap roll, which every notification is judged against whether or not quiet
        // hours are on. `SettingsSyncManager.refreshDeviceTimezone()` now owns the lifecycle
        // (launch, foreground, `NSSystemTimeZoneDidChange`); this call stays because writing it
        // in the same step as the times it qualifies is what makes the pair consistent.
        SettingsSyncManager.shared.refreshDeviceTimezone()
    }

    // MARK: Sync

    /// Push NOW. Every setter above ends here, synchronously, AFTER its UserDefaults write.
    ///
    /// The old screen synced ONLY in `.onDisappear`, so a user who flipped a toggle and
    /// force-quit lost the change with no trace. Its replacement was a ~500ms debounce, and
    /// the debounce was the next hole: a change is recorded as UNSAVED only inside `push()` —
    /// `pendingKeys`, durable, written before any request goes out (or by `deferLocalChange()`
    /// while the session is un-hydrated or `.restoring`). For those 500ms the new value sat in
    /// UserDefaults marked as nothing, so a launch hydrate landing inside them overwrote it with
    /// the server's older value (the row visibly flipped back), and a kill inside them let the
    /// next launch's hydrate do the same. Calling `push()` in the same step as the write closes
    /// both. Never put a delay back between the write and this call.
    ///
    /// Cost: while un-hydrated, none (a deferral sends nothing). Once hydrated, one PUT per
    /// change — the same per-change policy as `AppSettingsView`'s rows — and `push()` is
    /// cancel-and-replace, so only the newest blob of a burst is ever committed as confirmed.
    ///
    /// Also the screen's `.onDisappear` backstop. One PUT there, not free: `push()` sends the
    /// whole blob whenever the session is authenticated and hydrated, dirty or not (it is a
    /// no-op only for a guest).
    func pushNow() {
        SettingsSyncManager.shared.push()
    }

    // MARK: Time helpers

    /// `"HH:MM"` → a `Date` on an arbitrary day (only the time-of-day is meaningful).
    static func time(from hhmm: String) -> Date {
        let parts = hhmm.split(separator: ":")
        let hour = parts.count == 2 ? Int(parts[0]) ?? 22 : 22
        let minute = parts.count == 2 ? Int(parts[1]) ?? 0 : 0
        var components = DateComponents()
        components.hour = min(max(hour, 0), 23)
        components.minute = min(max(minute, 0), 59)
        return Calendar.current.date(from: components) ?? Date()
    }

    /// `Date` → zero-padded 24-hour `"HH:MM"`.
    ///
    /// The FORMAT is a contract, not a display choice: `quiet_hours.parse_hhmm` on the
    /// backend rejects anything else and degrades to "not quiet". A locale-aware
    /// formatter would emit "10:00 PM" for a US user and break exactly one region's
    /// quiet hours — so this is built from components, never from a DateFormatter.
    static func hhmm(from date: Date) -> String {
        let parts = Calendar.current.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", parts.hour ?? 0, parts.minute ?? 0)
    }
}
