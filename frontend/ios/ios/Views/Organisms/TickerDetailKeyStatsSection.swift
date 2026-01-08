//
//  TickerDetailKeyStatsSection.swift
//  ios
//
//  Organism: Key Statistics section with horizontally scrollable cards
//

import SwiftUI

struct TickerDetailKeyStatsSection: View {
    let statisticsGroups: [KeyStatisticsGroup]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section header
            Text("Key Statistics")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Horizontal scrolling cards
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.md) {
                    ForEach(statisticsGroups) { group in
                        KeyStatisticsCard(statistics: group.statistics)
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

#Preview {
    ScrollView {
        TickerDetailKeyStatsSection(statisticsGroups: KeyStatisticsGroup.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
