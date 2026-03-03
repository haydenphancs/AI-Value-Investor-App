//
//  DiversificationCard.swift
//  ios
//
//  Molecule: Portfolio diversification score card with sector pie chart
//

import SwiftUI

struct DiversificationCard: View {
    let score: DiversificationScore

    // Color palette for sector segments
    private static let sectorColors: [Color] = [
        AppColors.primaryBlue,
        AppColors.bullish,
        AppColors.alertOrange,
        AppColors.accentCyan,
        AppColors.accentYellow,
        AppColors.bearish,
        AppColors.neutral
    ]

    private var chartSegments: [DonutChartSegment] {
        score.allocations.enumerated().map { index, allocation in
            DonutChartSegment(
                value: allocation.percentage,
                color: Self.sectorColors[index % Self.sectorColors.count],
                label: allocation.name
            )
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Sector Breakdown Chart
            DonutChartView(
                segments: chartSegments,
                lineWidth: 20,
                showLabels: true
            )
            .frame(maxWidth: .infinity)

            // Divider
            Rectangle()
                .fill(AppColors.textMuted.opacity(0.15))
                .frame(height: 1)

            // Diversification Score Header
            HStack(spacing: AppSpacing.sm) {
                ZStack {
                    Circle()
                        .fill(AppColors.primaryBlue.opacity(0.15))
                        .frame(width: 32, height: 32)

                    Image(systemName: "chart.pie.fill")
                        .font(AppTypography.iconSmall).fontWeight(.semibold)
                        .foregroundColor(AppColors.primaryBlue)
                }

                Text("Diversification Score")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Text(score.formattedScore)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.accentCyan)
            }

            // Description
            Text(score.message)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)

            // Progress Bar
            GradientProgressBar(
                progress: score.progressValue,
                height: 8,
                gradientColors: [AppColors.bullish, AppColors.neutral]
            )

            // Sub-Score Breakdown
            if let sub = score.subScores {
                Rectangle()
                    .fill(AppColors.textMuted.opacity(0.15))
                    .frame(height: 1)

                VStack(spacing: AppSpacing.sm) {
                    SubScoreRow(
                        label: sub.concentrationLabel,
                        score: sub.concentrationScore,
                        max: sub.concentrationMax,
                        color: AppColors.primaryBlue
                    )
                    SubScoreRow(
                        label: sub.sectorLabel,
                        score: sub.sectorScore,
                        max: sub.sectorMax,
                        color: AppColors.bullish
                    )
                    SubScoreRow(
                        label: sub.diversityLabel,
                        score: sub.diversityScore,
                        max: sub.diversityMax,
                        color: AppColors.accentCyan
                    )
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

// MARK: - Sub-Score Row

private struct SubScoreRow: View {
    let label: String
    let score: Int
    let max: Int
    let color: Color

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .frame(width: 130, alignment: .leading)

            GradientProgressBar(
                progress: Double(score) / Double(max),
                height: 4,
                gradientColors: [color, color.opacity(0.5)]
            )

            Text("\(score)/\(max)")
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textSecondary)
                .frame(width: 36, alignment: .trailing)
        }
    }
}

#Preview {
    DiversificationCard(score: DiversificationScore.sampleData)
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
