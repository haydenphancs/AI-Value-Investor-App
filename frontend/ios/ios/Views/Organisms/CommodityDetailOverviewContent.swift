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
        // Eager, matching its four siblings. This was the only Overview content that
        // nested a lazy stack inside the screen's lazy stack — two placement caches to
        // invalidate instead of one — and its four sections are all in-memory with no
        // AsyncImage, so the laziness bought nothing. See DetailScrollContainer.
        VStack(spacing: AppSpacing.lg) {
            // Key Statistics (reuses TickerDetailKeyStatsSection)
            TickerDetailKeyStatsSection(statisticsGroups: commodityData.keyStatisticsGroups)

            // Performance (reuses TickerDetailPerformanceSection)
            TickerDetailPerformanceSection(
                periods: commodityData.performancePeriods,
                benchmarkSummary: commodityData.benchmarkSummary,
                symbol: commodityData.symbol
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
                .frame(height: AppSpacing.aiBarReserve)
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
}
