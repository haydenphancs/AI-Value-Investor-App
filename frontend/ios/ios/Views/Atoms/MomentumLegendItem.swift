//
//  MomentumLegendItem.swift
//  ios
//
//  Legend item for momentum chart (Net Positive/Net Negative)
//

import SwiftUI

struct MomentumLegendItem: View {
    let color: Color
    let label: String
    let value: Int

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)

            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)

            Text(value >= 0 ? "+\(value)" : "\(value)")
                .font(AppTypography.captionBold)
                .foregroundColor(color)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        HStack(spacing: AppSpacing.lg) {
            MomentumLegendItem(color: AppColors.bullish, label: "Net Positive", value: 17)
            MomentumLegendItem(color: AppColors.bearish, label: "Net Negative", value: -7)
        }
    }
}
