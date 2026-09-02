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
        // A plain VStack, NOT LazyVStack - see HomeDashboardView.content for the full write-up.
        // The direct children here are a fixed, hand-written list, so laziness bought nothing,
        // while a lazy stack whose child RESIZES IN PLACE re-walks its predecessor chain and can
        // wedge the main thread at 100% inside LazySubviewPlacements -> _ViewList_Node.applyNodes.
        //
        // RecentActivitiesSection swaps three tab bodies and three Show-All toggles.
        VStack(spacing: AppSpacing.lg) {
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
