//
//  ReportMoatCompetitionSection.swift
//  ios
//
//  Organism: Industry & Competitive Moat deep dive content.
//  Market dynamics overview + Pentagon radar chart for competitive dimensions + peer comparison bars + moat rating.
//

import SwiftUI

struct ReportMoatCompetitionSection: View {
    let data: ReportMoatCompetitionData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Market Dynamics Section
            marketDynamicsSection

            Divider()
                .background(AppColors.cardBackgroundLight)
                .padding(.vertical, AppSpacing.sm)

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

    // MARK: - Market Dynamics Section

    private var marketDynamicsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section Title
            Text("Market Dynamics")
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textPrimary)

            // Industry and Concentration row
            HStack(spacing: AppSpacing.lg) {
                // Industry
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text("Industry")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)

                    Text(data.marketDynamics.industry)
                        .font(AppTypography.subheadline)
                        .fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()

                // Concentration
                VStack(alignment: .trailing, spacing: AppSpacing.xs) {
                    Text("Concentration")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)

                    Text(data.marketDynamics.concentration.rawValue)
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(data.marketDynamics.concentration.color)
                        .padding(.horizontal, AppSpacing.sm)
                        .padding(.vertical, AppSpacing.xs)
                        .background(
                            Capsule()
                                .fill(data.marketDynamics.concentration.backgroundColor)
                        )
                }
                .alignmentGuide(.firstTextBaseline) { d in d[.top] }
            }
            .padding(.top, AppSpacing.xs)

            // 3-column metrics
            HStack(spacing: AppSpacing.sm) {
                // Column 1: CAGR
                marketMetricColumn(
                    label: "CAGR (5Yr)",
                    value: data.marketDynamics.formattedCAGR,
                    valueColor: data.marketDynamics.cagr5Yr >= 0 ? AppColors.bullish : AppColors.bearish,
                    subtitle: nil
                )
                .frame(maxWidth: 75)

                Divider()
                    .frame(height: 40)
                    .background(AppColors.cardBackgroundLight)

                // Column 2: Market Size (TAM)
                VStack(alignment: .center, spacing: AppSpacing.xxs) {
                    Text("Market Size (TAM)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)

                    HStack(spacing: AppSpacing.xxs) {
                        Text(data.marketDynamics.formattedCurrentTAM)
                            .font(.system(size: 14, weight: .bold))
                            .foregroundColor(AppColors.textPrimary)

                        Text("→")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(AppColors.textSecondary)

                        Text(data.marketDynamics.formattedFutureTAM)
                            .font(.system(size: 14, weight: .bold))
                            .foregroundColor(AppColors.textPrimary)
                    }

                    HStack(spacing: AppSpacing.xxs) {
                        Text("(\(data.marketDynamics.currentYear))")
                            .font(.system(size: 9))
                            .foregroundColor(AppColors.textMuted)

                        Spacer()
                            .frame(width: 14)

                        Text("(\(data.marketDynamics.futureYear))")
                            .font(.system(size: 9))
                            .foregroundColor(AppColors.textMuted)
                    }
                }
                .frame(maxWidth: .infinity)

                Divider()
                    .frame(height: 40)
                    .background(AppColors.cardBackgroundLight)

                // Column 3: Lifecycle Phase
                marketMetricColumn(
                    label: "Lifecycle Phase",
                    value: data.marketDynamics.lifecyclePhase.rawValue,
                    valueColor: data.marketDynamics.lifecyclePhase.color,
                    subtitle: nil,
                    isBadge: true
                )
                .frame(maxWidth: 105)
            }
            .padding(.top, AppSpacing.sm)
        }
    }

    private func marketMetricColumn(
        label: String,
        value: String,
        valueColor: Color,
        subtitle: String?,
        isBadge: Bool = false
    ) -> some View {
        VStack(alignment: .center, spacing: AppSpacing.xxs) {
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            if isBadge {
                Text(value)
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(valueColor)
                    .padding(.horizontal, AppSpacing.sm)
                    .padding(.vertical, AppSpacing.xs)
                    .background(
                        Capsule()
                            .fill(valueColor.opacity(0.15))
                    )
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            } else {
                Text(value)
                    .font(.system(size: 16, weight: .bold))
                    .foregroundColor(valueColor)
            }

            if let subtitle = subtitle {
                Text(subtitle)
                    .font(.system(size: 9))
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Moat Rating Header

    private var moatRatingHeader: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Top row: Badge + Legend
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

            // Primary Driver & Meaning
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                HStack(spacing: AppSpacing.xs) {
                    Text("Primary Driver:")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)

                    Text(data.primaryDriverName)
                        .font(AppTypography.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)

                    if let driver = data.primaryDriver {
                        Text("(\(String(format: "%.1f", driver.score))/10)")
                            .font(AppTypography.caption)
                            .foregroundColor(data.overallRating.color)
                    }
                }

                Text(data.overallRating.meaning)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .italic()
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
