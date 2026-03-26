//
//  TickerDetailOverviewContent.swift
//  ios
//
//  Organism: Overview tab content combining all sections for Ticker Detail
//

import SwiftUI

struct TickerDetailOverviewContent: View {
    let tickerData: TickerDetailData
    var onDeepResearchTap: (() -> Void)?
    var onWebsiteTap: (() -> Void)?
    var onRelatedTickerTap: ((RelatedTicker) -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Key Statistics
            TickerDetailKeyStatsSection(statisticsGroups: tickerData.keyStatisticsGroups)

            // Performance
            TickerDetailPerformanceSection(
                periods: tickerData.performancePeriods,
                benchmarkSummary: tickerData.benchmarkSummary
            )

            // Snapshots
            TickerDetailSnapshotsSection(
                snapshots: tickerData.snapshots,
                onDeepResearchTap: onDeepResearchTap
            )

            // Sector & Industry
            TickerDetailSectorIndustrySection(info: tickerData.sectorIndustry)

            // Company Profile
            TickerDetailCompanyProfileSection(
                profile: tickerData.companyProfile,
                onWebsiteTap: onWebsiteTap
            )

            // People Also Check
            TickerDetailRelatedSection(
                relatedTickers: tickerData.relatedTickers,
                onTickerTap: onRelatedTickerTap
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
        TickerDetailOverviewContent(tickerData: TickerDetailData.sampleApple)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
