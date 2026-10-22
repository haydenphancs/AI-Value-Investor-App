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
    /// `UpdatesView.marketFocusSeq` (a Market widget tap). A change brings the Market chip into
    /// view even when it was ALREADY selected — then the selection does not change, so the
    /// `.onChange(of: selectedTab?.scope)` below never fires and a strip left scrolled stays put.
    var marketRevealSeq: Int = 0

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            // Scrollable Tabs
            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: AppSpacing.sm) {
                        ForEach(tabs) { tab in
                            // Equality is by SCOPE: the UUID `id` is re-minted whenever the tabs
                            // are rebuilt, so a selection held from before a rebuild would match
                            // no chip by id.
                            UpdatesTabButton(
                                tab: tab,
                                isSelected: selectedTab == tab
                            ) {
                                withAnimation(.easeInOut(duration: 0.2)) {
                                    selectedTab = tab
                                }
                            }
                            // The scroll target, by scope for the same reason.
                            .id(tab.scope)
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
                // A selection made from code — a Market widget tap, or /tabs re-selecting — can
                // sit off-screen when the strip was left scrolled. No anchor: SwiftUI scrolls only
                // as far as it needs to, so a chip already in view does not move.
                .onChange(of: selectedTab?.scope) { _, scope in
                    guard let scope else { return }
                    withAnimation(.easeInOut(duration: 0.2)) {
                        proxy.scrollTo(scope)
                    }
                }
                .onChange(of: marketRevealSeq) { _, _ in
                    withAnimation(.easeInOut(duration: 0.2)) {
                        proxy.scrollTo(UpdatesScope.market)
                    }
                }
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
                // A scope each: `==` compares scope, and the default is the Market scope.
                NewsFilterTab(title: "AAPL", ticker: "AAPL", changePercent: 2.4, isMarketTab: false, scope: "AAPL"),
                NewsFilterTab(title: "TSLA", ticker: "TSLA", changePercent: -1.2, isMarketTab: false, scope: "TSLA")
            ],
            selectedTab: .constant(NewsFilterTab(title: "Market", ticker: nil, changePercent: nil, isMarketTab: true)),
            onManageAssets: {}
        )
        Spacer()
    }
    .background(AppColors.background)
}
