//
//  CongressFlowSummaryCard.swift
//  ios
//
//  Molecule: Summary card showing Total Buys vs Total Sells for Congress
//  Displays totals and buyer/seller counts (same layout as InsiderFlowSummaryCard)
//

import SwiftUI

struct CongressFlowSummaryCard: View {
    let summary: CongressActivitySummary

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            // Two-column layout: Total Buys | Total Sells
            HStack(spacing: 0) {
                // Total Buys column
                VStack(spacing: AppSpacing.xxs) {
                    Text("EST. TOTAL BUYS")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .tracking(0.5)

                    Text(summary.formattedBuys)
                        .font(AppTypography.heading)
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

                // Total Sells column
                VStack(spacing: AppSpacing.xxs) {
                    Text("EST. TOTAL SELLS")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .tracking(0.5)

                    Text(summary.formattedSells)
                        .font(AppTypography.heading)
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
            CongressFlowSummaryCard(
                summary: CongressActivitySummary.sampleData
            )
        }
        .padding()
    }
}
