//
//  ReportRiskFactorCard.swift
//  ios
//
//  Molecule: Individual macro risk factor card with impact gauge, trend arrow, and severity badge.
//  Designed with an intelligence-briefing aesthetic.
//

import SwiftUI

struct ReportRiskFactorCard: View {
    let factor: MacroRiskFactor

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Top row: icon + severity badge
            HStack {
                // Category icon
                Image(systemName: factor.category.iconName)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(factor.severity.color)
                    .frame(width: 28, height: 28)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.small)
                            .fill(factor.severity.color.opacity(0.12))
                    )

                Spacer()

                // Severity tag
                Text(factor.severity.rawValue)
                    .font(.system(size: 9, weight: .bold))
                    .foregroundColor(factor.severity.color)
                    .tracking(0.5)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 3)
                    .background(
                        RoundedRectangle(cornerRadius: 4)
                            .fill(factor.severity.color.opacity(0.12))
                    )
            }

            // Title
            Text(factor.title)
                .font(AppTypography.footnoteBold)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(2)

            // Impact gauge
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                HStack {
                    Text("Impact")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Spacer()
                    Text("\(Int(factor.impact * 100))%")
                        .font(AppTypography.captionBold)
                        .foregroundColor(gaugeColor)
                }

                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        // Track
                        RoundedRectangle(cornerRadius: 2.5)
                            .fill(AppColors.background)
                            .frame(height: 5)

                        // Fill with gradient
                        RoundedRectangle(cornerRadius: 2.5)
                            .fill(
                                LinearGradient(
                                    colors: [gaugeColor.opacity(0.7), gaugeColor],
                                    startPoint: .leading,
                                    endPoint: .trailing
                                )
                            )
                            .frame(width: geo.size.width * factor.impact, height: 5)
                    }
                }
                .frame(height: 5)
            }

            // Description
            Text(factor.description)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .lineSpacing(2)
                .lineLimit(3)

            // Trend indicator
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: factor.trend.iconName)
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(factor.trend.color)

                Text(factor.trend.rawValue)
                    .font(AppTypography.caption)
                    .foregroundColor(factor.trend.color)
            }
            .padding(.top, AppSpacing.xxs)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .stroke(factor.severity.color.opacity(0.15), lineWidth: 1)
                )
        )
    }

    private var gaugeColor: Color {
        switch factor.impact {
        case 0..<0.35: return AppColors.bullish
        case 0.35..<0.60: return AppColors.neutral
        case 0.60..<0.80: return AppColors.alertOrange
        default: return AppColors.bearish
        }
    }
}

#Preview {
    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: AppSpacing.md) {
        ForEach(TickerReportData.sampleOracle.macroData.riskFactors) { factor in
            ReportRiskFactorCard(factor: factor)
        }
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
