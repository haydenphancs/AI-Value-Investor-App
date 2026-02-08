//
//  ReportMoatCompetitionSection.swift
//  ios
//
//  Organism: Moat & Competition deep dive content.
//  Pentagon radar chart for competitive dimensions + peer comparison bars + moat rating.
//

import SwiftUI

struct ReportMoatCompetitionSection: View {
    let data: ReportMoatCompetitionData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxl) {
            // Moat Rating + Legend
            moatRatingHeader

            // Radar Chart
            radarChartSection

            // Durability Insight
            durabilityInsight

            // Peer Comparison
            peerComparisonSection

            // Competitive Insight
            competitiveInsightSection
        }
    }

    // MARK: - Moat Rating Header

    private var moatRatingHeader: some View {
        HStack(spacing: AppSpacing.md) {
            // Shield badge
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: data.overallRating.iconName)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(data.overallRating.color)

                Text(data.overallRating.rawValue)
                    .font(AppTypography.headline)
                    .foregroundColor(data.overallRating.color)
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.sm)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(data.overallRating.color.opacity(0.1))
                    .overlay(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .stroke(data.overallRating.color.opacity(0.25), lineWidth: 1)
                    )
            )

            Spacer()

            // Legend
            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                legendItem(color: AppColors.primaryBlue, label: "Company")
                legendItem(color: AppColors.textMuted, label: "Peer Avg")
            }
        }
    }

    private func legendItem(color: Color, label: String) -> some View {
        HStack(spacing: AppSpacing.xs) {
            RoundedRectangle(cornerRadius: 1)
                .fill(color)
                .frame(width: 12, height: 3)
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }

    // MARK: - Radar Chart

    private var radarChartSection: some View {
        ReportMoatRadarChart(dimensions: data.dimensions)
            .frame(maxWidth: .infinity)
    }

    // MARK: - Durability Insight

    private var durabilityInsight: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "clock.arrow.circlepath")
                    .font(.system(size: 12))
                    .foregroundColor(AppColors.accentCyan)
                Text("Moat Durability")
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text(data.durabilityNote)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.accentCyan.opacity(0.06))
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .stroke(AppColors.accentCyan.opacity(0.15), lineWidth: 1)
                )
        )
    }

    // MARK: - Peer Comparison

    private var peerComparisonSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "person.3.fill")
                    .font(.system(size: 12))
                    .foregroundColor(AppColors.textSecondary)
                Text("Competitive Landscape")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)
            }

            ForEach(data.competitors) { competitor in
                ReportPeerComparisonRow(competitor: competitor)
            }
        }
    }

    // MARK: - Competitive Insight

    private var competitiveInsightSection: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            Image(systemName: "lightbulb.fill")
                .font(.system(size: 12))
                .foregroundColor(AppColors.neutral)

            Text(data.competitiveInsight)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.neutral.opacity(0.06))
        )
    }
}

#Preview {
    ScrollView {
        ReportMoatCompetitionSection(
            data: TickerReportData.sampleOracle.moatCompetition
        )
        .padding()
    }
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
