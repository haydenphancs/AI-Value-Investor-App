//
//  PortfolioInsightsSection.swift
//  ios
//
//  Organism: Portfolio Insights section with a toggle.
//
//  Hidden behind an opt-in `Toggle` because the diversification score is only
//  meaningful when the user has filled in shares / market value for at least
//  some of their watchlist tickers — first-run users see the toggle and an
//  inviting blurb instead of a misleading "0" score.
//

import SwiftUI

struct PortfolioInsightsSection: View {
    let score: DiversificationScore?
    @Binding var isEnabled: Bool
    var onConfigureTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section Header — title + toggle, always visible.
            HStack {
                Text("Portfolio Insights")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Toggle("", isOn: $isEnabled)
                    .labelsHidden()
                    .tint(AppColors.primaryBlue)
            }
            .padding(.horizontal, AppSpacing.lg)

            content
        }
        .padding(.top, AppSpacing.lg)
        .padding(.bottom, AppSpacing.sm)
    }

    @ViewBuilder
    private var content: some View {
        if !isEnabled {
            collapsedHint
        } else if let score = score {
            VStack(alignment: .trailing, spacing: AppSpacing.xs) {
                DiversificationCard(score: score)

                if onConfigureTapped != nil {
                    Button {
                        onConfigureTapped?()
                    } label: {
                        HStack(spacing: AppSpacing.xxs) {
                            Image(systemName: "pencil")
                                .font(AppTypography.iconXS)
                            Text("Edit holdings")
                                .font(AppTypography.bodySmallEmphasis)
                        }
                        .foregroundColor(AppColors.primaryBlue)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        } else {
            emptyState
        }
    }

    private var collapsedHint: some View {
        Text("Toggle on to score your portfolio's diversification.")
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textSecondary)
            .padding(.horizontal, AppSpacing.lg)
            .padding(.bottom, AppSpacing.xs)
    }

    private var emptyState: some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "chart.pie")
                .font(AppTypography.iconDisplay)
                .foregroundColor(AppColors.textMuted)

            Text("Enter shares or amounts for the tickers you own to see your diversification score.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.lg)

            if let onConfigureTapped = onConfigureTapped {
                Button {
                    onConfigureTapped()
                } label: {
                    Text("Set up Portfolio Insights")
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

#Preview {
    VStack(spacing: AppSpacing.xxl) {
        PortfolioInsightsSection(
            score: DiversificationScore.sampleData,
            isEnabled: .constant(true),
            onConfigureTapped: {}
        )
        PortfolioInsightsSection(
            score: nil,
            isEnabled: .constant(true),
            onConfigureTapped: {}
        )
        PortfolioInsightsSection(
            score: nil,
            isEnabled: .constant(false),
            onConfigureTapped: {}
        )
    }
    .padding(.vertical)
    .background(AppColors.background)
}
