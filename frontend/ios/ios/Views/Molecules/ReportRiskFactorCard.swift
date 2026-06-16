//
//  ReportRiskFactorCard.swift
//  ios
//
//  Molecule: a single macro risk factor as a STATIC compact row — no expand
//  (the section-level "Show more / less" handles density). Layout:
//    icon · title ............................. trend (arrow + label)
//    description (always visible)
//  Severity is conveyed by the icon tint; the trend sits in the top-right slot.
//

import SwiftUI

struct ReportRiskFactorCard: View {
    let factor: MacroRiskFactor

    var body: some View {
        // Standard report-list-row framing (Key Management style): flat, no card
        // chrome, hairline divider below. Content is title-row + description
        // (variable height) rather than a name/value pair.
        VStack(spacing: AppSpacing.xs) {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Top line: category icon · title · trend (right-aligned)
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: factor.category.iconName)
                        .font(AppTypography.iconSmall).fontWeight(.medium)
                        .foregroundColor(factor.severity.color)
                        .frame(width: 24, height: 24)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                .fill(factor.severity.color.opacity(0.12))
                        )

                    Text(factor.title)
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)

                    Spacer(minLength: AppSpacing.xs)

                    // Trend — moved into the slot the badge/chevron used to hold.
                    HStack(spacing: AppSpacing.xxs) {
                        Image(systemName: factor.trend.iconName)
                            .font(AppTypography.iconTiny).fontWeight(.semibold)
                            .foregroundColor(factor.trend.color)
                        Text(factor.trend.rawValue)
                            .font(AppTypography.caption)
                            .foregroundColor(factor.trend.color)
                    }
                }

                // Description — always shown.
                Text(factor.description)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textMuted)
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Divider()
                .background(AppColors.textMuted.opacity(0.15))
        }
    }
}

#Preview {
    VStack(alignment: .leading, spacing: AppSpacing.md) {
        ForEach(TickerReportData.sampleOracle.macroData.riskFactors) { factor in
            ReportRiskFactorCard(factor: factor)
        }
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
