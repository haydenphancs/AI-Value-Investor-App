//
//  KeyStatisticItem.swift
//  ios
//
//  Molecule: Individual key statistic display cell
//

import SwiftUI

struct KeyStatisticItem: View {
    let statistic: KeyStatistic

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            // Label
            Text(statistic.label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .lineLimit(1)

            // Value
            Text(statistic.value)
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

#Preview {
    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: AppSpacing.md), count: 4), spacing: AppSpacing.lg) {
        KeyStatisticItem(statistic: KeyStatistic(label: "Open", value: "262.36"))
        KeyStatisticItem(statistic: KeyStatistic(label: "P/E (TTM)", value: "35.15"))
        KeyStatisticItem(statistic: KeyStatistic(label: "P/S", value: "52.57"))
        KeyStatisticItem(statistic: KeyStatistic(label: "Short % of Float", value: "0.83%", isHighlighted: true))
        KeyStatisticItem(statistic: KeyStatistic(label: "Previous Close", value: "267.26"))
        KeyStatisticItem(statistic: KeyStatistic(label: "P/E (FWD)", value: "31.84"))
        KeyStatisticItem(statistic: KeyStatistic(label: "P/S", value: "9.31"))
        KeyStatisticItem(statistic: KeyStatistic(label: "Shares Outstanding", value: "15.638"))
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
