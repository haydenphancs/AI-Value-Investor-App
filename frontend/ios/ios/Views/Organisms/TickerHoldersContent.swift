//
//  TickerHoldersContent.swift
//  ios
//
//  Organism: Holders tab content combining all holder-related sections for Ticker Detail
//  Includes Shareholder Breakdown, Smart Money, and Recent Activities sections
//

import SwiftUI

struct TickerHoldersContent: View {
    let holdersData: HoldersData
    /// Sub-tab a notification deep link asked for; `nil` = the section's own default.
    var initialActivitiesTab: RecentActivitiesTab?

    var body: some View {
        LazyVStack(spacing: AppSpacing.lg) {
            // Shareholder Breakdown Section
            ShareholderBreakdownSection(
                breakdownData: holdersData.shareholderBreakdown
            )

            // Smart Money Section
            SmartMoneySection(holdersData: holdersData)

            // Recent Activities Section
            RecentActivitiesSection(
                data: holdersData.recentActivities,
                initialTab: initialActivitiesTab
            )

            // Bottom spacing for AI bar
            Spacer()
                .frame(height: AppSpacing.aiBarReserve)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
    }
}

#Preview {
    ScrollView {
        TickerHoldersContent(
            holdersData: HoldersData.sampleData
        )
    }
    .background(AppColors.background)
}
