//
//  TechnicalSignalBadge.swift
//  ios
//
//  Badge displaying technical signal (Buy, Sell, Hold, etc.)
//

import SwiftUI

struct TechnicalSignalBadge: View {
    let title: String
    let signal: TechnicalSignal
    let indicatorCount: String

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text(title)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Text(signal.rawValue)
                .font(AppTypography.headline)
                .foregroundColor(signal.color)

            Text(indicatorCount)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        HStack(spacing: AppSpacing.xl) {
            TechnicalSignalBadge(
                title: "Daily Signal",
                signal: .buy,
                indicatorCount: "12 of 18 indicators"
            )

            TechnicalSignalBadge(
                title: "Weekly Signal",
                signal: .strongBuy,
                indicatorCount: "14 of 18 indicators"
            )
        }
        .padding()
    }
}
