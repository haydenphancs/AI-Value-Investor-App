//
//  StoreScreenshotMode.swift
//  ios
//
//  DEBUG-ONLY. App Store screenshots with labelled SAMPLE market data.
//
//  Why: the market-data licence permits no public display of prices, % moves or price
//  charts, and the App Store listing is public. A screen that shows them is captured with
//  sample values and a visible "Sample data" label (.claude/rules/marketing.md §1,
//  "Screenshots"; OWNER_TASKS §2.3 step 8). 1.0 shipped real prices in three of its five
//  screenshots — this mode exists so the next capture never has to.
//
//  The whole file sits inside `#if DEBUG`, as do its fixtures and every call site, so no
//  App Store build can contain any of it (`tests/test_ios_store_screenshot_mode_debug_only.py`).
//
//  On for one simulator launch:
//      SIMCTL_CHILD_CAYDEX_STORE_SHOT=1 SIMCTL_CHILD_CAYDEX_STORE_SHOT_TAB=tracking \
//        xcrun simctl launch <device> com.phan.caydex
//  `frontend/ios/scripts/capture-store-screenshots.sh` does that once per shot.
//
//  ⚠️ Only the tab ROOTS the capture script shoots are sample: Home, Tracking (and the Updates
//  chips). The label window sits over EVERY screen, so a cover opened by a tap (a ticker screen)
//  shows LIVE prices under "Sample data" — never tap during a capture. Creating a list in this
//  mode echoes the sample group's id (a DEBUG-only quirk; relaunch without the mode afterwards).
//
//  What changes while it is on (everything else still talks to the real backend with the
//  signed-in session):
//   • Home renders `StoreShotHomeRepository` through an in-memory snapshot store, so the
//     real saved dashboard never shows and nothing is written to disk.
//   • `StoreShotURLProtocol` answers the Tracking and Updates-chip reads with fixtures and
//     swallows the portfolio / watchlist / holdings writes they can trigger.
//   • A non-interactive window draws "Sample data" above every screen, cover and sheet.
//

#if DEBUG
import SwiftUI
import UIKit

nonisolated enum StoreScreenshotMode {
    /// `CAYDEX_STORE_SHOT=1` in the launch environment.
    static let isOn: Bool = ProcessInfo.processInfo.environment["CAYDEX_STORE_SHOT"] == "1"

    /// `CAYDEX_STORE_SHOT_TAB` = home | updates | research | tracking | wiser.
    static let startTabName: String? =
        ProcessInfo.processInfo.environment["CAYDEX_STORE_SHOT_TAB"]?.lowercased()
}

extension StoreScreenshotMode {
    /// The tab the shell starts on, or nil for the normal default. Honoured with or without
    /// `CAYDEX_STORE_SHOT`: the screens that show no market data are captured with the mode
    /// OFF (no fixtures, no "Sample data" label) but still need their tab picked without taps.
    @MainActor static var startTab: HomeTab? {
        guard let name = startTabName else { return nil }
        return HomeTab.allCases.first { $0.rawValue.lowercased() == name }
    }

    /// Registers the fixture protocol on `URLSession.shared`. Call once, at launch.
    @MainActor static func installIfEnabled() {
        guard isOn else { return }
        URLProtocol.registerClass(StoreShotURLProtocol.self)
        print("📸 [StoreScreenshotMode] ON — sample market data, tab=\(startTabName ?? "default")")
    }

    /// Kept alive here: a `UIWindow` nobody holds is torn down at once.
    @MainActor private static var labelWindow: UIWindow?

    /// Draws the "Sample data" label in its own window. A window at `.alert + 1` sits above
    /// every full-screen cover and sheet, which an overlay on the root view cannot do, and
    /// with touches off it never intercepts a tap.
    @MainActor static func showLabelIfEnabled() {
        guard isOn, labelWindow == nil else { return }
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let scene = scenes.first(where: { $0.activationState == .foregroundActive }) ?? scenes.first else {
            print("📸 [StoreScreenshotMode] no window scene yet — label not shown")
            return
        }
        let host = UIHostingController(rootView: StoreShotSampleDataLabel())
        host.view.backgroundColor = .clear
        let window = UIWindow(windowScene: scene)
        window.windowLevel = .alert + 1
        window.isUserInteractionEnabled = false
        window.backgroundColor = .clear
        // A new window follows the SYSTEM style, not the app's chosen Dark/Light, so copy the
        // app window's override — else the label could render light over a dark app.
        let appWindow = scene.windows.first(where: { $0.isKeyWindow }) ?? scene.windows.first
        window.overrideUserInterfaceStyle = appWindow?.overrideUserInterfaceStyle ?? .unspecified
        window.rootViewController = host
        window.isHidden = false
        labelWindow = window
    }
}

/// The label itself: a small capsule just above the tab bar.
private struct StoreShotSampleDataLabel: View {
    /// The custom tab bar is 73 pt tall (`CustomTabBar`); the label clears it.
    private let tabBarClearance: CGFloat = 81

    var body: some View {
        VStack {
            Spacer()
            Text("Sample data")
                .font(AppTypography.labelEmphasis)
                .foregroundColor(AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.xs)
                .background(AppColors.cardBackground, in: Capsule())
                .overlay(Capsule().stroke(AppColors.divider, lineWidth: 1))
                .padding(.bottom, tabBarClearance)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .allowsHitTesting(false)
    }
}
#endif
