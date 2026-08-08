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
                    .overlay(alignment: .topTrailing) {
                        if badgeCount > 0 {
                            Text(badgeCount > 99 ? "99+" : "\(badgeCount)")
                                .font(AppTypography.captionSmallEmphasis)
                                // Ink and fill change TOGETHER. `lossFill` was the
                                // obvious "badges are red" choice and it is WRONG here:
                                // its dark arm is a light red (#F87171), which puts
                                // `textOnAccent` at 2.77:1 — the theme guard caught it.
                                // `primaryFill` is frozen across both appearances, so
                                // white-on-it holds in each.
                                .foregroundColor(AppColors.textOnAccent)
                                .padding(.horizontal, 5)
                                .padding(.vertical, 1)
                                .background(
                                    Capsule().fill(AppColors.primaryFill)
                                )
                                .offset(x: 10, y: -6)
                                .accessibilityHidden(true)
                        }
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
