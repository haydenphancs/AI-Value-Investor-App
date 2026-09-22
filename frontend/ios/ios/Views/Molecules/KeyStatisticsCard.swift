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
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(valueColor(for: statistic))
                        .lineLimit(1)
                }
            }
        }
        .padding(AppSpacing.lg)
        .frame(width: 160)
        // Stretches to the tallest card in the row, so a four-row group sits in a card the
        // same height as its five-row neighbour with its rows pinned to the TOP (the default
        // `.center` would float them to the middle — the TestFlight defect in mirror image).
        // Only takes effect when the row proposes a definite height; `KeyStatisticsCarousel`
        // arranges that. `width:` belongs to the other `frame` overload, hence two calls.
        // Placed BEFORE `.cardSurface`, which paints exactly the frame it is attached to —
        // after it, the fill would stay content-sized and only the hit area would grow.
        .frame(maxHeight: .infinity, alignment: .top)
        .cardSurface(AppColors.cardBackgroundNested, cornerRadius: AppCornerRadius.large)
    }

    private func valueColor(for statistic: KeyStatistic) -> Color {
        if let state = statistic.colorState {
            switch state {
            case "warning": return AppColors.bearish
            case "squeeze": return AppColors.bullish
            default: break
            }
        }
        return statistic.isHighlighted ? AppColors.primaryBlue : AppColors.textPrimary
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
                KeyStatistic(label: "Dividends", value: "1.04 (0.39%)"),
                KeyStatistic(label: "Ex-Dividend Date", value: "11/10/2025")
            ])
        }
        .padding()
    }
    .background(AppColors.background)
}
