//
//  RatingDistributionBar.swift
//  ios
//
//  Horizontal bar representing a single rating distribution
//

import SwiftUI

struct RatingDistributionBar: View {
    let label: String
    let count: Int
    let color: Color
    let maxCount: Int

    private var fillRatio: CGFloat {
        guard maxCount > 0 else { return 0 }
        return CGFloat(count) / CGFloat(maxCount)
    }

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Label
            Text(label)
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textSecondary)
                .frame(width: 70, alignment: .leading)

            // Bar
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    // Background track
                    RoundedRectangle(cornerRadius: 2)
                        .fill(AppColors.cardBackgroundLight)
                        .frame(height: 8)

                    // Filled portion
                    RoundedRectangle(cornerRadius: 2)
                        .fill(color)
                        .frame(width: geometry.size.width * fillRatio, height: 8)
                }
            }
            .frame(height: 8)

            // Count
            Text("\(count)")
                .font(AppTypography.footnoteBold)
                .foregroundColor(AppColors.textPrimary)
                .frame(width: 24, alignment: .trailing)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.md) {
            RatingDistributionBar(label: "Strong Buy", count: 18, color: AppColors.bullish, maxCount: 18)
            RatingDistributionBar(label: "Buy", count: 14, color: Color(hex: "4ADE80"), maxCount: 18)
            RatingDistributionBar(label: "Hold", count: 6, color: AppColors.neutral, maxCount: 18)
            RatingDistributionBar(label: "Sell", count: 2, color: AppColors.bearish, maxCount: 18)
            RatingDistributionBar(label: "Strong Sell", count: 0, color: Color(hex: "991B1B"), maxCount: 18)
        }
        .padding()
    }
}
