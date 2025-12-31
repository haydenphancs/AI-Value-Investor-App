//
//  PortfolioInsightsSection.swift
//  ios
//
//  Organism: Portfolio Insights section with diversification score
//

import SwiftUI

struct PortfolioInsightsSection: View {
    let score: DiversificationScore?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section Header
            Text("Portfolio Insights")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Diversification Card
            if let score = score {
                DiversificationCard(score: score)
                    .padding(.horizontal, AppSpacing.lg)
            } else {
                // Empty State
                VStack(spacing: AppSpacing.md) {
                    Image(systemName: "chart.pie")
                        .font(.system(size: 32))
                        .foregroundColor(AppColors.textMuted)

                    Text("Add more assets to see insights")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.xxl)
                .background(AppColors.cardBackground)
                .cornerRadius(AppCornerRadius.large)
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.xxl) {
        PortfolioInsightsSection(score: DiversificationScore.sampleData)
        PortfolioInsightsSection(score: nil)
    }
    .padding(.vertical)
    .background(AppColors.background)
}
