//
//  PortfolioInsightsSection.swift
//  ios
//
//  Organism: Portfolio Insights section with diversification score
//

import SwiftUI

struct PortfolioInsightsSection: View {
    let score: DiversificationScore?
    var onAddHoldingTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section Header
            HStack {
                Text("Portfolio Insights")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                if onAddHoldingTapped != nil {
                    Button {
                        onAddHoldingTapped?()
                    } label: {
                        HStack(spacing: AppSpacing.xxs) {
                            Image(systemName: "plus")
                                .font(AppTypography.iconXS).fontWeight(.semibold)
                            Text("Add holding")
                                .font(AppTypography.bodySmallEmphasis)
                        }
                        .foregroundColor(AppColors.primaryBlue)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, AppSpacing.lg)

            // Diversification Card
            if let score = score {
                DiversificationCard(score: score)
                    .padding(.horizontal, AppSpacing.lg)
            } else {
                // Empty State
                VStack(spacing: AppSpacing.md) {
                    Image(systemName: "chart.pie")
                        .font(AppTypography.iconDisplay)
                        .foregroundColor(AppColors.textMuted)

                    Text("Add holdings to see your diversification score")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, AppSpacing.lg)

                    if let onAddHoldingTapped = onAddHoldingTapped {
                        Button {
                            onAddHoldingTapped()
                        } label: {
                            Text("Add Holding")
                                .font(AppTypography.bodySmallEmphasis)
                                .foregroundColor(.white)
                                .padding(.horizontal, AppSpacing.xl)
                                .padding(.vertical, AppSpacing.sm)
                                .background(AppColors.primaryBlue)
                                .cornerRadius(AppCornerRadius.pill)
                        }
                        .buttonStyle(.plain)
                        .padding(.top, AppSpacing.xs)
                    }
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
        PortfolioInsightsSection(
            score: DiversificationScore.sampleData,
            onAddHoldingTapped: {}
        )
        PortfolioInsightsSection(
            score: nil,
            onAddHoldingTapped: {}
        )
    }
    .padding(.vertical)
    .background(AppColors.background)
}
