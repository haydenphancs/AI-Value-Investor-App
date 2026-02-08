//
//  ReportPeerComparisonRow.swift
//  ios
//
//  Molecule: Single competitor row with visual score bar for moat comparison
//

import SwiftUI

struct ReportPeerComparisonRow: View {
    let competitor: CompetitorComparison
    let maxScore: Double = 10.0

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            HStack {
                // Name + Ticker
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(competitor.name)
                        .font(AppTypography.subheadline)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)
                    Text(competitor.ticker)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Spacer()

                // Threat badge
                Text(competitor.threatLevel.rawValue)
                    .font(AppTypography.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(competitor.threatLevel.color)
                    .padding(.horizontal, AppSpacing.sm)
                    .padding(.vertical, AppSpacing.xxs)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.small)
                            .fill(competitor.threatLevel.color.opacity(0.12))
                    )
            }

            // Score bar
            HStack(spacing: AppSpacing.sm) {
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        // Track
                        RoundedRectangle(cornerRadius: 3)
                            .fill(AppColors.cardBackgroundLight)
                            .frame(height: 6)

                        // Fill
                        RoundedRectangle(cornerRadius: 3)
                            .fill(barColor)
                            .frame(
                                width: geo.size.width * (competitor.moatScore / maxScore),
                                height: 6
                            )
                    }
                }
                .frame(height: 6)

                Text(String(format: "%.1f", competitor.moatScore))
                    .font(AppTypography.captionBold)
                    .foregroundColor(AppColors.textSecondary)
                    .frame(width: 28, alignment: .trailing)
            }

            // Market share
            HStack {
                Text("Market Share")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Spacer()
                Text(String(format: "%.0f%%", competitor.marketSharePercent))
                    .font(AppTypography.captionBold)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
        )
    }

    private var barColor: Color {
        switch competitor.threatLevel {
        case .high: return AppColors.bearish
        case .moderate: return AppColors.neutral
        case .low: return AppColors.bullish
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ForEach(TickerReportData.sampleOracle.moatCompetition.competitors) { comp in
            ReportPeerComparisonRow(competitor: comp)
        }
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
