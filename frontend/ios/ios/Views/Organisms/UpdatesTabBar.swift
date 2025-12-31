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
    var onAddTicker: (() -> Void)?
    var onFilterTapped: (() -> Void)?
    var hasActiveFilters: Bool = false

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

                    // Add Ticker Button
                    AddTickerButton {
                        onAddTicker?()
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }

            // Filter Button
            FilterButton(hasActiveFilters: hasActiveFilters) {
                onFilterTapped?()
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
            selectedTab: .constant(NewsFilterTab(title: "Market", ticker: nil, changePercent: nil, isMarketTab: true))
        )
        Spacer()
    }
    .background(AppColors.background)
}
