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
                    // The whole strip, inset included, so `reveal` can take it back to rest.
                    .id(Self.stripID)
                }
                // A selection made from code — a Market widget tap, or /tabs re-selecting — can
                // sit off-screen when the strip was left scrolled.
                .onChange(of: selectedTab?.scope) { _, scope in
                    guard let scope else { return }
                    withAnimation(.easeInOut(duration: 0.2)) {
                        reveal(scope, with: proxy)
                    }
                }
                .onChange(of: marketRevealSeq) { _, _ in
                    withAnimation(.easeInOut(duration: 0.2)) {
                        reveal(UpdatesScope.market, with: proxy)
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

    private static let stripID = "updates.tabbar.strip"

    /// Brings a chip into view. The Market chip, which the server always lists first, takes the
    /// strip back to its rest position: `scrollTo` a chip makes only the CHIP visible, so it
    /// stopped flush against the screen edge with the strip's leading inset scrolled away. Any
    /// other chip gets the least scroll that shows it (no anchor), so one already in view does
    /// not move.
    private func reveal(_ scope: String, with proxy: ScrollViewProxy) {
        if scope == UpdatesScope.market, tabs.first?.scope == scope {
            proxy.scrollTo(Self.stripID, anchor: .leading)
        } else {
            proxy.scrollTo(scope)
        }
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
