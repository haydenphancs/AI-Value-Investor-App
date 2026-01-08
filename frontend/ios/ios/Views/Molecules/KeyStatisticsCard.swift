//
//  KeyStatisticsCard.swift
//  ios
//
//  Molecule: Vertical card containing multiple key statistics for horizontal scroll
//

import SwiftUI

struct KeyStatisticsCard: View {
    let statistics: [KeyStatistic]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            ForEach(statistics) { statistic in
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    // Label
                    Text(statistic.label)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(1)

                    // Value
                    Text(statistic.value)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(statistic.isHighlighted ? AppColors.primaryBlue : AppColors.textPrimary)
                        .lineLimit(1)
                }
            }
        }
        .padding(AppSpacing.lg)
        .frame(width: 160)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: AppSpacing.md) {
            KeyStatisticsCard(statistics: [
                KeyStatistic(label: "Open", value: "262.36"),
                KeyStatistic(label: "Previous Close", value: "267.26"),
                KeyStatistic(label: "Volume", value: "39.43M"),
                KeyStatistic(label: "Avg. Volume (3M)", value: "45.23M"),
                KeyStatistic(label: "Market Cap", value: "3.89T")
            ])

            KeyStatisticsCard(statistics: [
                KeyStatistic(label: "P/E (TTM)", value: "35.15"),
                KeyStatistic(label: "P/E (FWD)", value: "31.84"),
                KeyStatistic(label: "EPS (TTM)", value: "7.47"),
                KeyStatistic(label: "Dividend & Yield", value: "1.04 (0.39%)"),
                KeyStatistic(label: "Ex-Dividend Date", value: "11/10/2025")
            ])
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
