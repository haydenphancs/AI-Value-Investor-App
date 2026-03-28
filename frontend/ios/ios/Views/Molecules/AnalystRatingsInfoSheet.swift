//
//  AnalystRatingsInfoSheet.swift
//  ios
//
//  Molecule: Educational sheet explaining analyst ratings for value investing
//

import SwiftUI

struct AnalystRatingsInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xxl) {
                    headerSection
                    consensusSection
                    priceTargetSection
                    momentumSection
                    valueInvestingTipsSection

                    // Disclaimer
                    Text("Disclaimer: Analyst ratings are opinions from financial professionals and not guarantees of future performance. Always conduct your own research and consider your personal financial situation before making investment decisions.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, AppSpacing.sm)
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Understanding Analyst Ratings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "person.3.fill")
                    .font(AppTypography.iconXL)
                    .foregroundColor(AppColors.primaryBlue)

                Text("Analyst Ratings")
                    .font(AppTypography.titleCompact)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text("Analyst ratings aggregate recommendations from Wall Street professionals who research and cover the stock. They provide consensus views on whether to buy, hold, or sell.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Consensus Section

    private var consensusSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Consensus Rating")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                consensusRow(
                    label: "Strong Buy",
                    color: AppColors.bullish,
                    description: "Most analysts are highly confident the stock will outperform. Strong conviction to buy."
                )

                consensusRow(
                    label: "Buy",
                    color: Color(hex: "4ADE80"),
                    description: "Analysts generally recommend purchasing the stock, expecting it to beat the market."
                )

                consensusRow(
                    label: "Hold",
                    color: AppColors.neutral,
                    description: "Mixed views. Analysts suggest keeping existing positions but not adding more."
                )

                consensusRow(
                    label: "Sell",
                    color: AppColors.bearish,
                    description: "Analysts expect the stock to underperform. Consider reducing your position."
                )

                consensusRow(
                    label: "Strong Sell",
                    color: Color(hex: "991B1B"),
                    description: "Strong conviction the stock will decline significantly. Analysts recommend exiting."
                )
            }
        }
    }

    private func consensusRow(label: String, color: Color, description: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Circle()
                    .fill(color)
                    .frame(width: 12, height: 12)

                Text(label)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(color)
            }

            Text(description)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Price Target Section

    private var priceTargetSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Price Target Range")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                metricRow(
                    icon: "arrow.down.to.line",
                    title: "Low Target",
                    description: "The most conservative analyst estimate. Represents the downside scenario."
                )

                metricRow(
                    icon: "target",
                    title: "Average Target",
                    description: "The consensus price target, averaged across all covering analysts. Compare this to the current price to gauge potential upside."
                )

                metricRow(
                    icon: "arrow.up.to.line",
                    title: "High Target",
                    description: "The most optimistic analyst estimate. Represents the best-case scenario."
                )
            }
        }
    }

    private func metricRow(icon: String, title: String, description: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: icon)
                    .font(AppTypography.iconDefault)
                    .foregroundColor(AppColors.primaryBlue)
                    .frame(width: 24)

                Text(title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
            }

            Text(description)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Momentum Section

    private var momentumSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Rating Momentum")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Momentum tracks how analyst opinions are changing over time. It shows upgrades (positive) and downgrades (negative) over selected periods.")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)

                Text("Rising momentum (more upgrades than downgrades) suggests improving fundamentals, while falling momentum may signal deteriorating outlook.")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(AppSpacing.md)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackground)
            )
        }
    }

    // MARK: - Value Investing Tips Section

    private var valueInvestingTipsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "lightbulb.fill")
                    .foregroundColor(AppColors.neutral)

                Text("Value Investing Tips")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
            }

            VStack(spacing: AppSpacing.md) {
                tipCard(
                    icon: "eye",
                    title: "Don't Follow Blindly",
                    description: "Analyst ratings are useful data points but not guarantees. Do your own research on the company's fundamentals before making decisions."
                )

                tipCard(
                    icon: "chart.line.downtrend.xyaxis",
                    title: "Contrarian Opportunities",
                    description: "Stocks with mostly Sell ratings can sometimes be undervalued if the market has overreacted. Look for fundamentally sound companies with negative sentiment."
                )

                tipCard(
                    icon: "arrow.left.arrow.right",
                    title: "Watch for Changes",
                    description: "A shift from Hold to Buy or from Buy to Sell is often more informative than the rating itself. Pay attention to momentum and recent upgrades or downgrades."
                )
            }
        }
    }

    private func tipCard(icon: String, title: String, description: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: icon)
                    .font(AppTypography.iconDefault)
                    .foregroundColor(AppColors.bullish)
                    .frame(width: 24)

                Text(title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text(description)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

#Preview {
    AnalystRatingsInfoSheet()
}
