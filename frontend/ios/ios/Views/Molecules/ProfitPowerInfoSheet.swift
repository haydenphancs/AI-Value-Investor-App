//
//  ProfitPowerInfoSheet.swift
//  ios
//
//  Molecule: Educational sheet explaining profit power metrics and value investing tips
//

import SwiftUI

struct ProfitPowerInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xxl) {
                    // Introduction
                    introductionSection

                    // Margin Types Explained
                    marginTypesSection

                    // Value Investing Tips
                    investingTipsSection
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Profit Power")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
            .toolbarBackground(AppColors.cardBackground, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
    }

    // MARK: - Introduction Section

    private var introductionSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Understanding Profit Power")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            Text("Profit Power shows multiple profitability margins over time, helping you assess a company's ability to convert revenue into profit at different stages of operations.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Margin Types Section

    private var marginTypesSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Margin Types")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            ForEach(ProfitMarginType.allCases) { marginType in
                marginTypeCard(marginType)
            }
        }
    }

    private func marginTypeCard(_ marginType: ProfitMarginType) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Color indicator
            Circle()
                .fill(marginType.color)
                .frame(width: 12, height: 12)
                .padding(.top, 4)

            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(marginType.rawValue)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(marginType.description)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Investing Tips Section

    private var investingTipsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Value Investing Tips")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            ForEach(ProfitPowerInfoItem.valueInvestingTips) { tip in
                investingTipCard(tip)
            }
        }
    }

    private func investingTipCard(_ tip: ProfitPowerInfoItem) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: tip.icon)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(AppColors.primaryBlue)

                Text(tip.title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text(tip.description)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            if let example = tip.example {
                HStack(alignment: .top, spacing: AppSpacing.xs) {
                    Image(systemName: "lightbulb.fill")
                        .font(.system(size: 11))
                        .foregroundColor(AppColors.neutral)

                    Text(example)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .italic()
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
}

#Preview {
    ProfitPowerInfoSheet()
}
