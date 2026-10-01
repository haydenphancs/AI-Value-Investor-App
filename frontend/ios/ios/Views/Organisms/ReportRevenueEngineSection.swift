//
//  ReportRevenueEngineSection.swift
//  ios
//
//  Organism: Revenue Engine deep dive content.
//  Shows revenue segment breakdown with automatic role assignment,
//  growth metrics, and visual indicators for each segment.
//

import SwiftUI

struct ReportRevenueEngineSection: View {
    let data: ReportRevenueEngineData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            if data.segments.isEmpty {
                // No segmentation for this company. The backend emits
                // `total_revenue: 0.0` in that case (`_build_revenue_engine` returns
                // its empty shell), and the header rendered that as a confident
                // "Total Revenue $0M · FY 2026" — a fabricated figure for the many
                // tickers FMP has no product segmentation for: ADRs, banks, insurers
                // and REITs among them. Say what is actually true instead, the same
                // way the Moat CAGR renders "—" and analyst targets stay null.
                unavailableSection
            } else {
                // Header: Total Revenue & Period
                headerSection

                // Segments List
                VStack(spacing: AppSpacing.md) {
                    ForEach(data.segments) { segment in
                        segmentCard(segment)
                    }
                }

                // A GROSS stack: the segments include sales between the company's own
                // segments, so their shares of reported revenue add to more than 100%.
                // This negative line is what brings the column back to 100% — the same
                // step the Financials tab's Revenue Breakdown legend shows.
                if data.hasEliminations {
                    eliminationsRow
                }

                // Analysis Note (if available)
                if let note = data.analysisNote {
                    analysisNoteSection(note)
                }
            }
        }
    }

    // MARK: - Empty State

    private var unavailableSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text("Revenue breakdown unavailable")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
            // Also shown when a feed lists only a sliver of revenue (one segment covering
            // a few percent) — the Financials tab rejects that as a breakdown, and the
            // report must not present it as "100% of total".
            Text("No reliable product or service segment split is reported for this company.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    // MARK: - Intersegment Eliminations

    private var eliminationsRow: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            HStack(alignment: .firstTextBaseline) {
                Text("Intersegment eliminations")
                    .font(AppTypography.labelSmallEmphasis)
                    .foregroundColor(AppColors.textSecondary)
                Spacer()
                Text(data.formattedEliminations)
                    .font(AppTypography.labelSmallEmphasis)
                    .foregroundColor(AppColors.textSecondary)
            }
            Text("\(data.formattedEliminationsPercentage) of total · sales between segments, removed in consolidation")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    // MARK: - Header Section

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text("Total Revenue")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)

            HStack(alignment: .firstTextBaseline, spacing: AppSpacing.xs) {
                Text(data.formattedTotalRevenue)
                    .font(AppTypography.dataCompact)
                    .foregroundColor(AppColors.textPrimary)

                Text(data.period)
                    .font(AppTypography.label)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }

    // MARK: - Segment Card

    private func segmentCard(_ segment: RevenueSegment) -> some View {
        let role = data.roleForSegment(segment)

        return VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Top row: Role badge + Revenue
            HStack(alignment: .center) {
                // Role badge
                HStack(spacing: AppSpacing.xs) {
                    Text(role.rawValue.uppercased())
                        .font(AppTypography.caption)
                        .foregroundColor(role.color)
                }
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xs)
                .background(
                    Capsule()
                        .fill(role.backgroundColor)
                )

                Spacer()

                // Revenue amount
                Text(segment.formattedRevenue)
                    .font(AppTypography.labelSmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Segment name
            Text(segment.name)
                .font(AppTypography.labelSmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(2)

            // Bottom row: Percentage + Growth
            HStack(spacing: AppSpacing.lg) {
                // Percentage of total
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "chart.pie.fill")
                        .font(AppTypography.captionTiny)
                        .foregroundColor(AppColors.textSecondary)

                    Text(segment.formattedPercentage)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)

                    Text("of total")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                // Growth indicator. No prior-year figure → no arrow and no
                // percentage; `formattedGrowth` says so in words instead.
                HStack(spacing: AppSpacing.xs) {
                    if segment.hasPriorAnchor {
                        Image(systemName: segment.growth >= 0 ? "arrow.up.right" : "arrow.down.right")
                            .font(.system(size: 7, weight: .semibold))
                            .foregroundColor(segment.growthColor)
                    }

                    Text(segment.formattedGrowth)
                        .font(AppTypography.caption)
                        .foregroundColor(segment.growthColor)
                }
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .cardFill(AppColors.cardBackgroundNested)   // nested in a card: step the surface up in dark
        )
    }

    // MARK: - Analysis Note Section

    private func analysisNoteSection(_ note: String) -> some View {
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

            Text(note)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Preview

#Preview {
    ScrollView {
        ReportRevenueEngineSection(
            data: ReportRevenueEngineData.sampleOracle
        )
        .padding()
    }
    .background(AppColors.cardBackground)
}

#Preview("Gross stack — eliminations line") {
    ScrollView {
        ReportRevenueEngineSection(
            data: ReportRevenueEngineData.sampleGross
        )
        .padding()
    }
    .background(AppColors.cardBackground)
}
