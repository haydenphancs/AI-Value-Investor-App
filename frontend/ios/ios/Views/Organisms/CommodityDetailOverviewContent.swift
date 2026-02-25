//
//  CommodityDetailOverviewContent.swift
//  ios
//
//  Organism: Overview tab content combining all sections for Commodity Detail
//

import SwiftUI

struct CommodityDetailOverviewContent: View {
    let commodityData: CommodityDetailData
    var onRelatedCommodityTap: ((RelatedTicker) -> Void)?

    var body: some View {
        LazyVStack(spacing: AppSpacing.lg) {
            // Key Statistics (reuses TickerDetailKeyStatsSection)
            TickerDetailKeyStatsSection(statisticsGroups: commodityData.keyStatisticsGroups)

            // Performance (reuses TickerDetailPerformanceSection)
            TickerDetailPerformanceSection(
                periods: commodityData.performancePeriods,
                benchmarkSummary: commodityData.benchmarkSummary
            )

            // Commodity Profile
            CommodityDetailProfileSection(
                profile: commodityData.commodityProfile
            )

            // People Also Check (reuses TickerDetailRelatedSection)
            TickerDetailRelatedSection(
                relatedTickers: commodityData.relatedCommodities,
                onTickerTap: onRelatedCommodityTap
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
        CommodityDetailOverviewContent(commodityData: CommodityDetailData.sampleGold)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
