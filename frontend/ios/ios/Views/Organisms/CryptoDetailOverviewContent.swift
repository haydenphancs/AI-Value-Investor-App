//
//  CryptoDetailOverviewContent.swift
//  ios
//
//  Organism: Overview tab content combining all sections for Crypto Detail
//  Sections: Key Statistics, Snapshots, Crypto Profile
//

import SwiftUI

struct CryptoDetailOverviewContent: View {
    let cryptoData: CryptoDetailData
    var onDeepResearchTap: (() -> Void)?
    var onWebsiteTap: (() -> Void)?
    var onWhitepaperTap: (() -> Void)?
    var onRelatedCryptoTap: ((RelatedTicker) -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Key Statistics (FMP data)
            CryptoDetailKeyStatsSection(statisticsGroups: cryptoData.keyStatisticsGroups)

            // Performance
            TickerDetailPerformanceSection(periods: cryptoData.performancePeriods)

            // Snapshots (Origin and Technology, Tokenomics, Next Big Moves, Risks)
            CryptoDetailSnapshotsSection(
                snapshots: cryptoData.snapshots,
                onDeepResearchTap: onDeepResearchTap
            )

            // Crypto Profile
            CryptoProfileSection(
                profile: cryptoData.cryptoProfile,
                onWebsiteTap: onWebsiteTap,
                onWhitepaperTap: onWhitepaperTap
            )

            // People Also Check
            TickerDetailRelatedSection(
                relatedTickers: cryptoData.relatedCryptos,
                onTickerTap: onRelatedCryptoTap
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
        CryptoDetailOverviewContent(cryptoData: CryptoDetailData.sampleEthereum)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
