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
            // Industry name — centered, styled to match the "Insider
            // Activity" subsection header (`bodySmallEmphasis` 14pt
            // semibold, `textSecondary` muted-white) so subsection
            // anchors read consistently across the report. The "Market
            // Dynamics" subtitle and the small "Industry" caption are
            // intentionally removed — the parent card header ("Industry
            // & Competitive Moat") already names the topic, so the
            // industry string only needs to identify *which* industry.
            Text(data.marketDynamics.industry)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)
                .frame(maxWidth: .infinity, alignment: .center)
                .multilineTextAlignment(.center)

            // 3-column metrics — `.top` alignment so the value rows of all
            // three columns share a baseline. The TAM column's year row
            // (current → future) hangs below its value row without
            // shifting the other two columns out of alignment.
            HStack(alignment: .top, spacing: AppSpacing.sm) {
                // Column 1: CAGR — shows "—" when source data is missing
                marketMetricColumn(
                    label: "CAGR (5Yr)",
                    value: data.marketDynamics.formattedCAGR,
                    valueColor: data.marketDynamics.cagrColor,
                    subtitle: nil
                )
                .frame(maxWidth: 75)

                Divider()
                    .frame(height: 40)
                    .background(AppColors.cardBackgroundLight)

                // Column 2: Market Size (TAM) — value row uses the same
                // `labelSmall` 12pt-bold font as CAGR / Concentration so
                // the three values share a baseline. The year row below
                // shows current → future and is projected forward to
                // today's year via the CAGR (see
                // MarketDynamics.displayedCurrentYear in
                // TickerReportModels.swift) so stale Census source years
                // don't show on a report opened years later.
                VStack(alignment: .center, spacing: AppSpacing.xxs) {
                    Text("Market Size (TAM)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)

                    if data.marketDynamics.tamIsAvailable {
                        HStack(spacing: AppSpacing.xxs) {
                            Text(data.marketDynamics.formattedDisplayedCurrentTAM)
                                .font(AppTypography.labelSmall).fontWeight(.bold)
                                .foregroundColor(AppColors.textPrimary)

                            Text("→")
                                .font(AppTypography.labelSmall).fontWeight(.bold)
                                .foregroundColor(AppColors.textSecondary)

                            Text(data.marketDynamics.formattedDisplayedFutureTAM)
                                .font(AppTypography.labelSmall).fontWeight(.bold)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        HStack(spacing: AppSpacing.xxs) {
                            Text("(\(data.marketDynamics.displayedCurrentYear))")
                                .font(AppTypography.captionTiny)
                                .foregroundColor(AppColors.textMuted)

                            Spacer()
                                .frame(width: 14)

                            Text("(\(data.marketDynamics.displayedFutureYear))")
                                .font(AppTypography.captionTiny)
                                .foregroundColor(AppColors.textMuted)
                        }
                    } else {
                        Text("—")
                            .font(AppTypography.labelSmall).fontWeight(.bold)
                            .foregroundColor(AppColors.textMuted)
                    }
                }
                .frame(maxWidth: .infinity)

                Divider()
                    .frame(height: 40)
                    .background(AppColors.cardBackgroundLight)

                // Column 3: Concentration — rendered as a pill via
                // `isBadge: true` so the strongest-moat signal still
                // reads as a colored chip (Monopoly = bullish green,
                // Fragmented = alert orange) even after moving out of
                // the right-side slot in the industry row.
                marketMetricColumn(
                    label: "Concentration",
                    value: data.marketDynamics.concentration.rawValue,
                    valueColor: data.marketDynamics.concentration.color,
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
                    .font(AppTypography.caption).fontWeight(.bold)
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
                    .font(AppTypography.labelSmall).fontWeight(.bold)
                    .foregroundColor(valueColor)
            }

            if let subtitle = subtitle {
                Text(subtitle)
                    .font(AppTypography.captionTiny)
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
                // Moat badge — title case to match the Concentration badge
                // ("Monopoly", "Oligopoly") in the Market Dynamics row.
                Text(data.overallRating.rawValue)
                    .font(AppTypography.caption).fontWeight(.bold)
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

            // Primary Driver — qualitative tone moved into the AI Insight
            // below (durability_note prompt is fed a moat-strength hint
            // so the generated copy naturally conveys "strong defense" /
            // "elite fortress" / etc.).
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
                .frame(minWidth: 55, alignment: .leading)
        }
    }

    // MARK: - Radar Chart

    private var radarChartSection: some View {
        // The radar chart frame is 340pt while the actual chart is 200pt,
        // leaving ~70pt of empty margin above and below for axis labels.
        // Asymmetric negative padding: a small top pull keeps breathing
        // room under the "Primary Driver" caption (which sits close to
        // the "Switching Costs" top-axis label), while a larger bottom
        // pull tightens the visible gap before the durability Insight.
        // Labels continue to draw inside the chart's own frame — no
        // clipping risk from negative padding here.
        ReportMoatRadarChart(dimensions: data.dimensions)
            .frame(maxWidth: .infinity)
            .padding(.top, -AppSpacing.md)
            // -44 is past the largest AppSpacing token (xxxl=32); the
            // bottom of the chart frame holds the largest dead-space
            // gap (Cost Advantage / Brand Power labels sit shallow
            // inside it), so an extra-tight pull here lands cleanly
            // above the Insight box.
            .padding(.bottom, -44)
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
                    .font(AppTypography.iconDefault).fontWeight(.semibold)

                Text("Insight")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundStyle(LinearGradient(
                        colors: [.indigo, .cyan],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ))
            }

            Text(data.durabilityNote)
                .font(AppTypography.label)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .padding(AppSpacing.md)
    }

    // MARK: - Peer Comparison

    private var peerComparisonSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Competitors")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)

            if data.competitors.isEmpty {
                Text("No competitor data available")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            } else {
                ForEach(data.competitors) { competitor in
                    ReportPeerComparisonRow(competitor: competitor)
                }
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
                    .font(AppTypography.iconDefault).fontWeight(.semibold)

                Text("Insight")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundStyle(LinearGradient(
                        colors: [.indigo, .cyan],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ))
            }

            Text(data.competitiveInsight)
                .font(AppTypography.label)
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
