//
//  ETFDetailOverviewContent.swift
//  ios
//
//  Organism: Overview tab content combining all sections for ETF Detail
//  Sections: Key Statistics, Performance, Snapshots, ETF Profile, People Also Check
//

import SwiftUI

struct ETFDetailOverviewContent: View {
    let etfData: ETFDetailData
    var onDeepResearchTap: (() -> Void)?
    var onWebsiteTap: (() -> Void)?
    var onRelatedETFTap: ((RelatedTicker) -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Key Statistics (FMP data)
            ETFDetailKeyStatsSection(statisticsGroups: etfData.keyStatisticsGroups)

            // Performance
            TickerDetailPerformanceSection(
                periods: etfData.performancePeriods,
                benchmarkSummary: etfData.benchmarkSummary
            )

            // Snapshots (Identity & Rating, Strategy, Net Yield, Holdings & Risk)
            ETFDetailSnapshotsSection(
                etfData: etfData,
                onDeepResearchTap: onDeepResearchTap
            )

            // ETF Profile
            ETFProfileSection(
                profile: etfData.etfProfile,
                onWebsiteTap: onWebsiteTap
            )

            // People Also Check
            TickerDetailRelatedSection(
                relatedTickers: etfData.relatedETFs,
                onTickerTap: onRelatedETFTap
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
        ETFDetailOverviewContent(etfData: ETFDetailData.sampleSPY)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
