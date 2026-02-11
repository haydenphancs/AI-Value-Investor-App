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
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Moat Rating + Legend
            moatRatingHeader

            // Radar Chart
            radarChartSection

            // Durability Insight
            durabilityInsight

            // Peer Comparison
            peerComparisonSection
        }
    }

    // MARK: - Moat Rating Header

    private var moatRatingHeader: some View {
        HStack(spacing: AppSpacing.md) {
            // Moat badge (styled like RISING SEGMENT)
            Text(data.overallRating.rawValue.uppercased())
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(data.overallRating.color)
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xs)
                .background(
                    Capsule()
                        .fill(data.overallRating.backgroundColor)
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
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "sparkles.2")
                    .foregroundStyle(
                        LinearGradient(
                            colors: [.indigo],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .font(.system(size: 16, weight: .semibold))

                Text("Insight")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(LinearGradient(
                        colors: [.indigo, .cyan],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ))
            }

            Text(data.durabilityNote)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .padding(AppSpacing.md)
    }

    // MARK: - Peer Comparison

    private var peerComparisonSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Competitive Landscape")
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textPrimary)

            ForEach(data.competitors) { competitor in
                ReportPeerComparisonRow(competitor: competitor)
            }
        }
    }

    // MARK: - Competitive Insight

    private var competitiveInsightSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "sparkles.2")
                    .foregroundStyle(
                        LinearGradient(
                            colors: [.indigo],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .font(.system(size: 16, weight: .semibold))

                Text("Insight")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(LinearGradient(
                        colors: [.indigo, .cyan],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ))
            }

            Text(data.competitiveInsight)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .padding(AppSpacing.md)
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
