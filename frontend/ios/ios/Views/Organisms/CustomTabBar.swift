//
//  CustomTabBar.swift
//  ios
//
//  Organism: Custom bottom tab bar
//

import SwiftUI

struct CustomTabBar: View {
    @Binding var selectedTab: HomeTab
    /// Unread notifications, badged on the Updates tab — the closest thing the app has
    /// to a notification surface. Device-global state, so `AppState` resets it when a
    /// session ends; otherwise the next account inherits the previous user's count.
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
                    badgeCount: tab == .updates ? unreadNotifications : 0
                )
            }
        }
        .padding(.top, AppSpacing.md)
        .padding(.bottom, AppSpacing.xl)
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

#Preview {
    VStack {
        Spacer()
        CustomTabBar(selectedTab: .constant(.home))
    }
    .background(AppColors.background)
}
