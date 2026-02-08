//
//  ReportKeyVitalCard.swift
//  ios
//
//  Molecule: Individual Key Vital card (Valuation, Moat, or Financial Health)
//

import SwiftUI

// MARK: - Valuation Vital Card

struct ReportValuationVitalCard: View {
    let data: ReportValuationData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Title + badge
            HStack {
                Text("Valuation")
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                ReportSentimentBadge(
                    text: data.status.rawValue,
                    textColor: data.status.color,
                    backgroundColor: data.status.backgroundColor
                )
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.3))

            // Current Price
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Current Price")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Text(data.formattedCurrentPrice)
                    .font(AppTypography.title3)
                    .fontWeight(.bold)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Fair Value
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Fair Value")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Text(data.formattedFairValue)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.primaryBlue)
            }

            // Upside Potential
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Upside Potential")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Text(data.formattedUpside)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(data.upsideColor)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Moat Vital Card

struct ReportMoatVitalCard: View {
    let data: ReportMoatData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Moat")
                .font(AppTypography.footnoteBold)
                .foregroundColor(AppColors.textPrimary)

            Divider()
                .background(AppColors.textMuted.opacity(0.3))

            // Moat tags
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                ForEach(data.tags) { tag in
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "checkmark.shield.fill")
                            .font(.system(size: 10))
                            .foregroundColor(tag.strength.color)
                        Text(tag.label)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }

            Spacer()

            // Value/Stable labels
            HStack(spacing: AppSpacing.sm) {
                Text(data.valueLabel)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.horizontal, AppSpacing.sm)
                    .padding(.vertical, AppSpacing.xxs)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.small)
                            .stroke(AppColors.textMuted.opacity(0.3), lineWidth: 1)
                    )
                Text(data.stabilityLabel)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.horizontal, AppSpacing.sm)
                    .padding(.vertical, AppSpacing.xxs)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.small)
                            .stroke(AppColors.textMuted.opacity(0.3), lineWidth: 1)
                    )
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Financial Health Vital Card

struct ReportFinancialHealthVitalCard: View {
    let data: ReportFinancialHealthData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Title + level badge
            HStack {
                Text("Financial Health")
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                ReportSentimentBadge(
                    text: data.level.rawValue,
                    textColor: data.level.color,
                    backgroundColor: data.level.color.opacity(0.15)
                )
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.3))

            // Altman Z-Score
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Altman Z-Score")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                HStack(alignment: .firstTextBaseline, spacing: AppSpacing.xs) {
                    Text(data.formattedZScore)
                        .font(AppTypography.title3)
                        .fontWeight(.bold)
                        .foregroundColor(data.level.color)

                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 12))
                        .foregroundColor(AppColors.neutral)
                }

                Text(data.altmanZLabel)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            // Additional metric
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 10))
                    .foregroundColor(data.additionalMetricStatus.color)
                Text(data.additionalMetric)
                    .font(AppTypography.caption)
                    .foregroundColor(data.additionalMetricStatus.color)
            }

            // FCF note
            Text(data.fcfNote)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .lineLimit(2)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

#Preview {
    let sample = TickerReportData.sampleOracle
    ScrollView(.horizontal) {
        HStack(spacing: AppSpacing.md) {
            ReportValuationVitalCard(data: sample.keyVitals.valuation)
                .frame(width: 160)
            ReportMoatVitalCard(data: sample.keyVitals.moat)
                .frame(width: 160)
            ReportFinancialHealthVitalCard(data: sample.keyVitals.financialHealth)
                .frame(width: 160)
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
