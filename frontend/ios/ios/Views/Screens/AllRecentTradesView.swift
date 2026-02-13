//
//  AllRecentTradesView.swift
//  ios
//
//  All Recent Trades screen — shown when user taps "more" on Recent Trades
//  Displays all whale trading activity in a timeline
//

import SwiftUI

// MARK: - AllRecentTradesView
struct AllRecentTradesView: View {
    @ObservedObject var viewModel: TrackingViewModel

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: 0) {
                    ForEach(Array(viewModel.allWhaleTrades.enumerated()), id: \.element.id) { groupIndex, group in
                        ForEach(Array(group.activities.enumerated()), id: \.element.id) { activityIndex, activity in
                            let isFirst = groupIndex == 0 && activityIndex == 0
                            let isLast = groupIndex == viewModel.allWhaleTrades.count - 1
                                && activityIndex == group.activities.count - 1

                            WhaleTradeTimelineRow(
                                activity: activity,
                                isFirst: isFirst,
                                isLast: isLast,
                                onTapped: { viewModel.viewTradeGroupDetail(activity) }
                            )
                            .padding(.horizontal, AppSpacing.lg)
                        }
                    }

                    // Bottom spacing
                    Spacer()
                        .frame(height: 100)
                }
                .padding(.top, AppSpacing.md)
            }
        }
        .navigationTitle("Recent Trades")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - Preview
#Preview {
    NavigationStack {
        AllRecentTradesView(viewModel: TrackingViewModel())
    }
    .preferredColorScheme(.dark)
}
