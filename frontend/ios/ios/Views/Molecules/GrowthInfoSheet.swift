//
//  GrowthInfoSheet.swift
//  ios
//
//  Molecule: Educational sheet explaining growth metrics for value investing
//

import SwiftUI

struct GrowthInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xxl) {
                    // Header Section
                    headerSection

                    // Understanding Growth Section
                    understandingGrowthSection

                    // Value Investing Tips
                    valueInvestingTipsSection

                    // Chart Reading Guide
                    chartReadingGuideSection
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Understanding Growth")
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
                Image(systemName: "chart.line.uptrend.xyaxis")
                    .font(.system(size: 24))
                    .foregroundColor(AppColors.primaryBlue)

                Text("Growth Analysis")
                    .font(AppTypography.title2)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text("Growth metrics help you understand how a company's financial performance is improving (or declining) over time. For value investors, sustainable growth at reasonable prices is key.")
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

    // MARK: - Understanding Growth Section

    private var understandingGrowthSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Key Metrics Explained")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                ForEach(GrowthMetricType.allCases) { metric in
                    metricExplanationRow(metric: metric)
                }
            }
        }
    }

    private func metricExplanationRow(metric: GrowthMetricType) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text(metric.rawValue)
                .font(AppTypography.bodyBold)
                .foregroundColor(AppColors.primaryBlue)

            Text(metric.description)
                .font(AppTypography.callout)
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

    // MARK: - Value Investing Tips Section

    private var valueInvestingTipsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "lightbulb.fill")
                    .foregroundColor(AppColors.neutral)

                Text("Value Investing Tips")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
            }

            VStack(spacing: AppSpacing.md) {
                ForEach(GrowthInfoItem.valueInvestingTips) { tip in
                    tipCard(tip: tip)
                }
            }
        }
    }

    private func tipCard(tip: GrowthInfoItem) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: tip.icon)
                    .font(.system(size: 16))
                    .foregroundColor(AppColors.bullish)
                    .frame(width: 24)

                Text(tip.title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text(tip.description)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            if let example = tip.example {
                HStack(alignment: .top, spacing: AppSpacing.sm) {
                    Text("Example:")
                        .font(AppTypography.captionBold)
                        .foregroundColor(AppColors.accentCyan)

                    Text(example)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.top, AppSpacing.xs)
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Chart Reading Guide Section

    private var chartReadingGuideSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Reading the Chart")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(alignment: .leading, spacing: AppSpacing.md) {
                chartLegendExplanation(
                    color: AppColors.growthBarBlue,
                    title: "Blue Bars (Value)",
                    description: "Shows the absolute value for each period. Taller bars mean higher values."
                )

                chartLegendExplanation(
                    color: AppColors.growthYoYYellow,
                    title: "Yellow Line (YoY %)",
                    description: "Year-over-Year growth rate. Points above the midline indicate positive growth."
                )

                chartLegendExplanation(
                    color: AppColors.growthSectorGray,
                    title: "Gray Dashed Line (Sector Avg)",
                    description: "Industry average growth. Compare to see if company outperforms peers."
                )

                chartLegendExplanation(
                    color: AppColors.bullish,
                    title: "Green Percentages",
                    description: "Positive YoY growth - the company improved from the previous year."
                )

                chartLegendExplanation(
                    color: AppColors.bearish,
                    title: "Red Percentages",
                    description: "Negative YoY growth - decline from the previous year."
                )
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
            )
        }
    }

    private func chartLegendExplanation(color: Color, title: String, description: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Circle()
                .fill(color)
                .frame(width: 12, height: 12)
                .padding(.top, 4)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(description)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

#Preview {
    GrowthInfoSheet()
}
