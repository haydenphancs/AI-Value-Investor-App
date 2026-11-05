//
//  CustomTabBar.swift
//  ios
//
//  Organism: Custom bottom tab bar
//

import SwiftUI

struct CustomTabBar: View {
    /// The bar's own height, excluding the bottom safe area: `AppSpacing.md` top padding +
    /// a ~41pt item (a 20pt `iconLarge` glyph, `AppSpacing.xs`, an 11pt `caption` label) +
    /// `AppSpacing.xl` bottom padding.
    ///
    /// Published because `RootContainerView` parks the floating mini player above this bar and
    /// had been doing it with a bare `58`, which left a MEASURED 3pt gap — the capsule sat
    /// visually welded to the tab bar and was reported from TestFlight.
    ///
    /// Now only the FALLBACK for the frame before the bar has been measured: the root parks the
    /// player on `TabBarHeightKey`, the bar's real height. The item is text-sized (caption up to
    /// 1.4×, icon up to 1.25×), so this estimate left ~10pt of gap at the default size and ~1pt
    /// at the largest — the same welded look, back again for anyone with bigger text.
    static let approximateHeight: CGFloat = 73

    @Binding var selectedTab: HomeTab
    /// Unread notifications, badged on **Tracking**, whose "Alerts" segment shows them.
    ///
    /// It used to be badged on Updates — "the closest thing the app has to a notification
    /// surface" — and that approximation WAS the bug a TestFlight tester reported: Updates is
    /// a news feed that never reads `notification_events`, so tapping the badged tab could
    /// not clear the count, and the only surface that could was buried at
    /// `Profile → Notification History`. Tracking owns the data (7 of the 9 notification
    /// kinds fire on a watchlist ticker or a followed investor), and its Alerts segment marks
    /// everything read on sight.
    ///
    /// Device-global state, so `AppState` resets it when a session ends; otherwise the next
    /// account inherits the previous user's count.
    var unreadNotifications: Int = 0

    var body: some View {
        HStack(spacing: 0) {
            ForEach(HomeTab.allCases, id: \.self) { tab in
                TabBarItem(
                    tab: tab,
                    isSelected: selectedTab == tab,
                    onTap: {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            selectedTab = tab
                        }
                    },
                    badgeCount: tab == .tracking ? unreadNotifications : 0
                )
            }
        }
        .padding(.top, AppSpacing.md)
        .padding(.bottom, AppSpacing.xl)
        // The bar's real height, for `RootContainerView` to park the mini player on. Measured
        // here, after the paddings and before the background (which runs into the safe area).
        .background(
            GeometryReader { proxy in
                Color.clear
                    .preference(key: TabBarHeightKey.self, value: proxy.size.height)
            }
        )
        .background(AppColors.tabBarBackground)
        // Top hairline. `tabBarBackground`'s DARK arm (#171B26) is byte-identical to the
        // page background's, so the bar had NO boundary at all in dark — measured on a
        // screenshot, the left gutter is one unbroken #171B26 for 320px from the page
        // through the bar. A card scrolled to the edge was clipped mid-glyph against
        // nothing, which reads as truncation rather than as content passing under a plane.
        //
        // `border` (0.10 white in dark → 1.34 against the bar), NOT `cardEdge`, whose dark
        // arm is alpha 0 — that transparency is exactly the problem here — and not
        // `divider`/`borderSubtle` at 0.06, which is 1.18 and barely a value change.
        //
        // This is deliberately NOT the `toggleBackground` case, which is accepted at
        // 1.0000 in dark because its SELECTED SEGMENT separates and carries the meaning.
        // The tab bar's selected item is only an icon tint — no geometry changes — so
        // nothing about the bar itself delineates it. Light was already 1.09 and fine;
        // the hairline makes both modes agree.
        .overlay(alignment: .top) {
            Rectangle()
                .fill(AppColors.border)
                .frame(height: 1)
        }
    }
}

/// The tab bar's measured height (excluding the bottom safe area), read by `RootContainerView`.
///
/// `max`, because every tab draws its own bar (all five are mounted at once) and they are all the
/// same height. 0 means "not measured yet" — the root falls back to `approximateHeight`, so the
/// default must not be a plausible height that `max` could keep over a real, shorter one.
struct TabBarHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

#Preview {
    VStack {
        Spacer()
        CustomTabBar(selectedTab: .constant(.home))
    }
    .background(AppColors.background)
}
