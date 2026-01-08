//
//  TickerDetailKeyStatsSection.swift
//  ios
//
//  Organism: Key Statistics section for Ticker Detail
//

import SwiftUI

struct TickerDetailKeyStatsSection: View {
    let statistics: [KeyStatistic]

    // Grid columns - 4 columns layout
    private let columns = Array(repeating: GridItem(.flexible(), spacing: AppSpacing.md), count: 4)

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section header
            Text("Key Statistics")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Statistics grid
            LazyVGrid(columns: columns, spacing: AppSpacing.lg) {
                ForEach(statistics) { statistic in
                    KeyStatisticItem(statistic: statistic)
                }
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        TickerDetailKeyStatsSection(statistics: KeyStatistic.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
