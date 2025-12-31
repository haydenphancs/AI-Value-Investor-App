//
//  DiversificationCard.swift
//  ios
//
//  Molecule: Portfolio diversification score card
//

import SwiftUI

struct DiversificationCard: View {
    let score: DiversificationScore

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with icon
            HStack(spacing: AppSpacing.sm) {
                ZStack {
                    Circle()
                        .fill(AppColors.primaryBlue.opacity(0.15))
                        .frame(width: 32, height: 32)

                    Image(systemName: "lightbulb.fill")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(AppColors.primaryBlue)
                }

                Text("Diversification Score")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Description
            Text(score.message)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)

            // Progress Bar with Score
            HStack(spacing: AppSpacing.md) {
                GradientProgressBar(
                    progress: score.progressValue,
                    height: 8,
                    gradientColors: [AppColors.bullish, AppColors.neutral]
                )

                Text(score.formattedScore)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 40, alignment: .trailing)
            }

            // Sector Breakdown
            LazyVGrid(columns: [
                GridItem(.flexible()),
                GridItem(.flexible())
            ], spacing: AppSpacing.sm) {
                ForEach(score.allocations) { allocation in
                    HStack(spacing: AppSpacing.xs) {
                        Text("\(allocation.name):")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        Text(allocation.formattedPercentage)
                            .font(AppTypography.captionBold)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    DiversificationCard(score: DiversificationScore.sampleData)
        .padding()
        .background(AppColors.background)
}
