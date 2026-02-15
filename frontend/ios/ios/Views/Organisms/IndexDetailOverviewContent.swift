//
//  IndexDetailOverviewContent.swift
//  ios
//
//  Organism: Overview tab content combining all sections for Index Detail
//

import SwiftUI

struct IndexDetailOverviewContent: View {
    let indexData: IndexDetailData
    var onWebsiteTap: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Key Statistics
            TickerDetailKeyStatsSection(statisticsGroups: indexData.keyStatisticsGroups)

            // Snapshots (Valuation, Sector Performance, The Macro Forecast)
            IndexDetailSnapshotsSection(snapshots: indexData.snapshots)

            // Index Profile
            IndexDetailProfileSection(
                profile: indexData.indexProfile,
                onWebsiteTap: onWebsiteTap
            )

            // Bottom spacing for AI bar
            Spacer()
                .frame(height: 120)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
    }
}

#Preview {
    ScrollView {
        IndexDetailOverviewContent(indexData: IndexDetailData.sampleSP500)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
