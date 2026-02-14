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
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Horizontal scrolling cards (reuses KeyStatisticsCard)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 0) {
                    ForEach(Array(statisticsGroups.enumerated()), id: \.element.id) { index, group in
                        HStack(spacing: 0) {
                            KeyStatisticsCard(statistics: group.statistics)

                            // Vertical divider between cards (except for last)
                            if index < statisticsGroups.count - 1 {
                                Rectangle()
                                    .fill(AppColors.cardBackgroundLight)
                                    .frame(width: 1)
                                    .padding(.vertical, AppSpacing.lg)
                            }
                        }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
        .padding(.top, AppSpacing.md)
        .padding(.bottom, AppSpacing.sm)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }
}

#Preview {
    ScrollView {
        CryptoDetailKeyStatsSection(statisticsGroups: CryptoKeyStatisticsGroup.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
