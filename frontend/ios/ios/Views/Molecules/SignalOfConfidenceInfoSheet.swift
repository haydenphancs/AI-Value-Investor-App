//
//  SignalOfConfidenceInfoSheet.swift
//  ios
//
//  Molecule: Educational sheet explaining Signal of Confidence metrics and value investing tips
//

import SwiftUI

struct SignalOfConfidenceInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xxl) {
                    // Introduction
                    introductionSection

                    // Metric Types Explained
                    metricTypesSection

                    // Value Investing Tips
                    investingTipsSection
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Signal of Confidence")
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
            Text("Understanding Signal of Confidence")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            Text("Signal of Confidence shows how a company returns capital to shareholders through dividends and share buybacks. These actions demonstrate management's confidence in the business and commitment to shareholder value.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Metric Types Section

    private var metricTypesSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Metric Types")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            ForEach(SignalOfConfidenceMetricType.allCases) { metricType in
                metricTypeCard(metricType)
            }
        }
    }

    private func metricTypeCard(_ metricType: SignalOfConfidenceMetricType) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Color indicator
            Circle()
                .fill(metricType.color)
                .frame(width: 12, height: 12)
                .padding(.top, 4)

            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(metricType.rawValue)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(metricType.description)
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

            ForEach(SignalOfConfidenceInfoItem.valueInvestingTips) { tip in
                investingTipCard(tip)
            }
        }
    }

    private func investingTipCard(_ tip: SignalOfConfidenceInfoItem) -> some View {
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
    SignalOfConfidenceInfoSheet()
}
