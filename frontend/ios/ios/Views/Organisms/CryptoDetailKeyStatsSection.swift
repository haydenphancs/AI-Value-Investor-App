//
//  CryptoDetailKeyStatsSection.swift
//  ios
//
//  Organism: Key Statistics section for Crypto Detail with horizontally scrollable cards
//  Uses FMP (Financial Modeling Prep) data: Market Cap, Volume, Supply, ATH/ATL, Dominance, etc.
//

import SwiftUI

struct CryptoDetailKeyStatsSection: View {
    let statisticsGroups: [KeyStatisticsGroup]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Section title inside card styling
            Text("Key Statistics")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // The card row. Shared by every detail screen — equal-height, top-aligned cards
            // live in KeyStatisticsCarousel, not here.
            KeyStatisticsCarousel(statisticsGroups: statisticsGroups)
        }
        .padding(.top, AppSpacing.md)
        .padding(.bottom, AppSpacing.sm)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
    }
}

#Preview {
    ScrollView {
        CryptoDetailKeyStatsSection(statisticsGroups: CryptoKeyStatisticsGroup.sampleETH)
    }
    .background(AppColors.background)
}
