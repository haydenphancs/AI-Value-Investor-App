//
//  TabBarItem.swift
//  ios
//
//  Molecule: Individual tab bar item
//

import SwiftUI

struct TabBarItem: View {
    let tab: HomeTab
    let isSelected: Bool
    var onTap: (() -> Void)?
    /// Unread count for this tab. 0 = no badge. Optional-by-default so the four tabs
    /// that never badge keep their exact call site.
    var badgeCount: Int = 0

    private var iconColor: Color {
        isSelected ? AppColors.tabBarSelected : AppColors.tabBarUnselected
    }

    private var textColor: Color {
        isSelected ? AppColors.tabBarSelected : AppColors.tabBarUnselected
    }

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(spacing: AppSpacing.xs) {
                Image(systemName: tab.systemIconName)
                    .font(AppTypography.iconLarge).fontWeight(isSelected ? .semibold : .regular)
                    .foregroundColor(iconColor)
                    // The badge recipe lives in `UnreadCountBadge` — the Tracking → Alerts
                    // segment shows the SAME count and is on screen at the same time, so one
                    // atom is what stops one number rendering in two colours.
                    //
                    // It is RED now (`lossFill` + `textOnFill`), not blue. The old comment here
                    // rejected "red badges" on the strength of `lossFill` + `textOnAccent` being
                    // 2.77:1 in dark — true, and `textOnFill` is the ink that solves it. The
                    // reasoning is kept in full on the atom.
                    //
                    // An OVERLAY, never a layout sibling: the label below already needs
                    // `minimumScaleFactor` to survive five columns, and a badge that took layout
                    // width would shrink every one of them.
                    .overlay(alignment: .topTrailing) {
                        UnreadCountBadge(count: badgeCount)
                            .offset(x: 10, y: -6)
                    }

                // Five equal columns of ~78pt on a 393pt phone, and there is no `TabView`
                // here (this bar is hand-rolled) so no framework behaviour rescues it.
                // Without these three the only remedy SwiftUI has is to wrap, and
                // "Research"/"Tracking" have no inter-word break — they split mid-word
                // ("Researc/h"). Scaling down is the correct trade for a tab label.
                Text(tab.rawValue)
                    .font(AppTypography.caption)
                    .foregroundColor(textColor)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                    .allowsTightening(true)
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(PlainButtonStyle())
        // The badge itself is `accessibilityHidden`, so the count has to be said HERE or it is
        // said nowhere — which is what happened before: a purely visual unread indicator.
        .accessibilityLabel(
            badgeCount > 0 ? "\(tab.rawValue), \(badgeCount) new" : tab.rawValue
        )
    }
}

#Preview {
    HStack {
        ForEach(HomeTab.allCases, id: \.self) { tab in
            TabBarItem(tab: tab, isSelected: tab == .home)
        }
    }
    .padding()
    .background(AppColors.tabBarBackground)
}
