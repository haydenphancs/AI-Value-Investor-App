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
    @State private var showCompetitorsInfo = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Market Dynamics Section
            marketDynamicsSection

            Divider()
                .overlay(AppColors.cardBackgroundLight)
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
            // three columns share a baseline. Measured only (owner decision
            // 2026-10-22): the market size at ITS year, the average yearly growth
            // over its real window, no projected value — so no calendar is read.
            HStack(alignment: .top, spacing: AppSpacing.sm) {
                growthColumn
                    .frame(maxWidth: 70)

                columnDivider

                marketSizeColumn
                    .frame(maxWidth: .infinity)

                columnDivider

                concentrationColumn
                    .frame(maxWidth: 92)
            }
            .padding(.top, AppSpacing.sm)

            sourceCaption
        }
    }

    // Column 1: average yearly growth — label above the value because
    // "Avg/yr 2017–24" on one line (73pt) is wider than the 70pt column; the
    // window rides below as the subtitle. Neutral ink: a realized industry rate
    // is not a verdict, and a -0.2% in red read as "shrinking".
    private var growthColumn: some View {
        let md = data.marketDynamics
        return marketMetricColumn(
            label: "Avg/yr",
            value: md.formattedGrowth,
            valueColor: md.cagr5Yr == nil ? AppColors.textMuted : AppColors.textPrimary,
            subtitle: md.growthWindowLabel
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(md.growthAccessibilityLabel)
    }

    private var columnDivider: some View {
        // `.background` is inert on a Divider; `.overlay` paints it.
        Divider()
            .frame(height: 40)
            .overlay(AppColors.divider)
    }

    // Column 2: market size. A measured figure shows one value at its own
    // year; only a company's earnings-call quote with a STATED target shows
    // the arrow pair.
    private var marketSizeColumn: some View {
        VStack(alignment: .center, spacing: AppSpacing.xxs) {
            // Scope-prefixed header: "US - Market Size (TAM)" so the market
            // size is never ambiguous; unprefixed for a company quote.
            Text(data.marketDynamics.tamHeaderLabel)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .lineLimit(1)
                .minimumScaleFactor(0.8)

            if data.marketDynamics.tamIsAvailable {
                marketSizeValues
            } else {
                Text("—")
                    .font(AppTypography.labelSmall).fontWeight(.bold)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }

    @ViewBuilder
    private var marketSizeValues: some View {
        let md = data.marketDynamics
        if let target = md.formattedTargetTAM, let targetYear = md.targetYearLabel {
            HStack(spacing: AppSpacing.xxs) {
                metricValueText(md.formattedCurrentTAM)
                Text("→")
                    .font(AppTypography.labelSmall).fontWeight(.bold)
                    .foregroundColor(AppColors.textSecondary)
                metricValueText(target)
            }
            HStack(spacing: AppSpacing.xxs) {
                yearText(md.measuredYearLabel ?? "")
                Spacer()
                    .frame(width: 14)
                yearText(targetYear)
            }
        } else {
            metricValueText(md.formattedCurrentTAM)
            if let year = md.measuredYearLabel {
                yearText(year)
            }
        }
    }

    private func metricValueText(_ value: String) -> some View {
        Text(value)
            .font(AppTypography.labelSmall).fontWeight(.bold)
            .foregroundColor(AppColors.textPrimary)
    }

    private func yearText(_ value: String) -> some View {
        Text(value)
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
    }

    // Column 3: Concentration — plain text, same labelSmall bold as the
    // other values (no badge, no colour). It is REVENUE shares of the
    // industry's US-listed constituents, so it tops out at "Oligopoly"; "—"
    // when it could not be measured.
    private var concentrationColumn: some View {
        let md = data.marketDynamics
        return marketMetricColumn(
            label: "Concentration",
            value: md.concentration.rawValue,
            valueColor: md.concentration == .unknown ? AppColors.textMuted : AppColors.textPrimary,
            subtitle: md.concentrationSubtitle
        )
    }

    // Where the market size comes from. Wraps (2-3 lines): cutting it would
    // hide the NAICS codes a composite figure is summed from.
    @ViewBuilder
    private var sourceCaption: some View {
        if let caption = data.marketDynamics.sourceCaption {
            Text(caption)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
                .fixedSize(horizontal: false, vertical: true)
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
                    // Opaque: a text colour on its own 15% tint fails AA in light
                    // (test_ios_theme_parity §6c).
                    .background(
                        Capsule()
                            .fill(AppColors.cardBackgroundLight)
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
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
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
                    // Opaque: `accentYellow` on its own 15% tint is 4.41:1 in light even on a
                    // white card, 3.90 on `cardBackgroundLight` (test_ios_theme_parity §6c).
                    .background(
                        Capsule()
                            .fill(AppColors.cardBackgroundLight)
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
                Image(systemName: AppSymbols.ai)
                    .foregroundStyle(
                        AppColors.aiRampStart
                    )
                    .font(AppTypography.iconDefault).fontWeight(.semibold)

                Text("Insight")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundStyle(AppGradients.ai)
            }

            Text(data.durabilityNote)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Peer Comparison

    private var peerComparisonSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Competitors")
                // Match the "Software - Infrastructure" industry subtitle: same
                // font/size (bodySmallEmphasis) AND the same secondary (gray)
                // color, so the two section labels read identically.
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)

            if data.competitors.isEmpty {
                Text("No competitor data available")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            } else {
                competitorOrderLegend
                ForEach(data.competitors) { competitor in
                    ReportPeerComparisonRow(competitor: competitor)
                }
            }
        }
        .sheet(isPresented: $showCompetitorsInfo) {
            CompetitorsInfoSheet(order: data.competitorOrder)
        }
    }

    /// What the row order and the score mean (TestFlight #57: "Is NVIDIA the main
    /// competitor? How did we get the competitors?"). The caption follows the report's
    /// own order marker, so a threat-ordered report never claims "most direct first".
    private var competitorOrderLegend: some View {
        HStack(alignment: .center, spacing: AppSpacing.xs) {
            Text(data.competitorOrder.caption)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)

            Button {
                showCompetitorsInfo = true
            } label: {
                Image(systemName: "info.circle")
                    .font(AppTypography.iconSmall).fontWeight(.medium)
                    .foregroundColor(AppColors.textMuted)
                    // Size the box to the minimum target first (slop alone is clipped by
                    // a parent that fits it tightly); `.trailing` keeps the glyph flush
                    // with the rows' right edge.
                    .frame(width: HitSlop.minimumTarget, height: HitSlop.minimumTarget,
                           alignment: .trailing)
                    .hitSlop(reaching: HitSlop.minimumTarget)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("How competitors are chosen")
        }
    }

    // MARK: - Competitive Insight

    private var competitiveInsightSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: AppSymbols.ai)
                    .foregroundStyle(
                        AppColors.aiRampStart
                    )
                    .font(AppTypography.iconDefault).fontWeight(.semibold)

                Text("Insight")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundStyle(AppGradients.ai)
            }

            Text(data.competitiveInsight)
                .font(AppTypography.body)
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
}

#Preview("Measured industry figure") {
    let oracle = TickerReportData.sampleOracle.moatCompetition
    ScrollView {
        ReportMoatCompetitionSection(
            data: ReportMoatCompetitionData(
                marketDynamics: MarketDynamics.sampleMeasured,
                dimensions: oracle.dimensions,
                durabilityNote: oracle.durabilityNote,
                competitors: oracle.competitors,
                competitiveInsight: oracle.competitiveInsight
            )
        )
        .padding()
    }
    .background(AppColors.cardBackground)
}
