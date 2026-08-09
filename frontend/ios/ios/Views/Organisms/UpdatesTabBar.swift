//
//  UpdatesTabBar.swift
//  ios
//
//  Organism: Tab bar for filtering news by market or ticker
//

import SwiftUI

struct UpdatesTabBar: View {
    let tabs: [NewsFilterTab]
    @Binding var selectedTab: NewsFilterTab?
    /// How many of the user's group tickers their plan is hiding. 0 renders nothing —
    /// a user who can see their whole group must not be told they are missing something.
    var lockedCount: Int = 0
    var onManageAssets: (() -> Void)?
    var onLockedTap: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            // Scrollable Tabs
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.sm) {
                    ForEach(tabs) { tab in
                        UpdatesTabButton(
                            tab: tab,
                            isSelected: selectedTab?.id == tab.id
                        ) {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                selectedTab = tab
                            }
                        }
                    }

                    // Trailing, after the pills the user CAN open, so the strip reads as
                    // "here is your list, and here is the rest of it" rather than as a
                    // wall in front of the feature.
                    if lockedCount > 0 {
                        LockedTickersChip(count: lockedCount) { onLockedTap?() }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }

            // Manage Assets Button
            ManageAssetsButton {
                onManageAssets?()
            }
            .padding(.trailing, AppSpacing.lg)
        }
        .padding(.vertical, AppSpacing.sm)
    }
}

#Preview {
    VStack {
        UpdatesTabBar(
            tabs: [
                NewsFilterTab(title: "Market", ticker: nil, changePercent: nil, isMarketTab: true),
                NewsFilterTab(title: "AAPL", ticker: "AAPL", changePercent: 2.4, isMarketTab: false),
                NewsFilterTab(title: "TSLA", ticker: "TSLA", changePercent: -1.2, isMarketTab: false)
            ],
            selectedTab: .constant(NewsFilterTab(title: "Market", ticker: nil, changePercent: nil, isMarketTab: true)),
            onManageAssets: {}
        )
        Spacer()
    }
    .background(AppColors.background)
}
