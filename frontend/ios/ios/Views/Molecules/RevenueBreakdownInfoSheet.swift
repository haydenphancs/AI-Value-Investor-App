//
//  RevenueBreakdownInfoSheet.swift
//  ios
//
//  Molecule: Info sheet explaining revenue breakdown for value investors
//

import SwiftUI

struct RevenueBreakdownInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // Header explanation
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        Text("Understanding Revenue Breakdown")
                            .font(AppTypography.title2)
                            .foregroundColor(AppColors.textPrimary)

                        Text("This chart shows how a company generates revenue and where the money goes. Understanding these flows helps assess business quality and profitability.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                    }
                    .padding(.bottom, AppSpacing.md)

                    // Chart explanation
                    chartExplanation

                    Divider()
                        .background(AppColors.cardBackgroundLight)

                    // Educational content
                    ForEach(RevenueBreakdownInfoItem.educationalContent) { item in
                        infoCard(item: item)
                    }

                    // Key ratios section
                    keyRatiosSection

                    Spacer()
                        .frame(height: AppSpacing.xxxl)
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
    }

    // MARK: - Chart Explanation

    private var chartExplanation: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Reading the Chart")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                explanationRow(
                    color: RevenueSource.iPhoneColor,
                    title: "Left Bar (Stacked)",
                    description: "Shows all revenue sources stacked. Height represents total revenue."
                )

                explanationRow(
                    color: Color(hex: "EF4444"),
                    title: "Middle Bars (Waterfall)",
                    description: "Shows costs being subtracted from revenue: Cost of Sales, Operating Expenses, and Taxes."
                )

                explanationRow(
                    color: AppColors.bullish,
                    title: "Right Bar (Result)",
                    description: "Green = Net Profit (what's left after all costs). Red = Net Loss (costs exceeded revenue)."
                )
            }
        }
    }

    private func explanationRow(color: Color, title: String, description: String) -> some View {
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
                    .font(AppTypography.footnote)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
    }

    // MARK: - Info Card

    private func infoCard(item: RevenueBreakdownInfoItem) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Image(systemName: item.icon)
                .font(.system(size: 24))
                .foregroundColor(AppColors.primaryBlue)
                .frame(width: 32)

            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(item.title)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(item.description)
                    .font(AppTypography.footnote)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Key Ratios Section

    private var keyRatiosSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Key Profitability Ratios")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.sm) {
                ratioRow(
                    name: "Gross Margin",
                    formula: "(Revenue - Cost of Sales) / Revenue",
                    benchmark: "Tech: 50-70%, Retail: 20-40%"
                )

                ratioRow(
                    name: "Operating Margin",
                    formula: "Operating Profit / Revenue",
                    benchmark: "Tech: 20-35%, Manufacturing: 5-15%"
                )

                ratioRow(
                    name: "Net Profit Margin",
                    formula: "Net Profit / Revenue",
                    benchmark: "Healthy: 10-20%, Excellent: >20%"
                )
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    private func ratioRow(name: String, formula: String, benchmark: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            Text(name)
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textPrimary)

            Text(formula)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .italic()

            Text(benchmark)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.accentCyan)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, AppSpacing.xs)
    }
}

#Preview {
    RevenueBreakdownInfoSheet()
}
