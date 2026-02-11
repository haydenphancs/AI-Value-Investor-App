//
//  InsiderFlowSummaryCard.swift
//  ios
//
//  Molecule: Summary card showing Informative Buys vs Informative Sells
//  Displays totals and buyer/seller counts
//

import SwiftUI

struct InsiderFlowSummaryCard: View {
    let summary: InsiderActivitySummary

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            // Two-column layout: Informative Buys | Informative Sells
            HStack(spacing: 0) {
                // Informative Buys column
                VStack(spacing: AppSpacing.xxs) {
                    Text("INFORMATIVE BUYS")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .tracking(0.5)

                    Text(summary.formattedBuys)
                        .font(AppTypography.title3)
                        .foregroundColor(AppColors.bullish)

                    Text(summary.buyersLabel)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
                .frame(maxWidth: .infinity)

                // Vertical divider
                Rectangle()
                    .fill(AppColors.textMuted.opacity(0.3))
                    .frame(width: 1)
                    .padding(.vertical, AppSpacing.xs)

                // Informative Sells column
                VStack(spacing: AppSpacing.xxs) {
                    Text("INFORMATIVE SELLS")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .tracking(0.5)

                    Text(summary.formattedSells)
                        .font(AppTypography.title3)
                        .foregroundColor(AppColors.bearish)

                    Text(summary.sellersLabel)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .stroke(AppColors.textMuted.opacity(0.3), lineWidth: 1)
                )
        )
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            InsiderFlowSummaryCard(
                summary: InsiderActivitySummary.sampleData
            )

            // Alternative with different values
            InsiderFlowSummaryCard(
                summary: InsiderActivitySummary(
                    periodDescription: "Last 6 Months",
                    informativeBuysInMillions: 5.2,
                    informativeSellsInMillions: 8.7,
                    numBuyers: 1,
                    numSellers: 3
                )
            )
        }
        .padding()
    }
}
