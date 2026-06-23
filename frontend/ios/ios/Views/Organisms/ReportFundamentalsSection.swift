//
//  ReportFundamentalsSection.swift
//  ios
//
//  Organism: Fundamentals & Growth deep dive content with 2x2 metric grid + assessment
//

import SwiftUI

struct ReportFundamentalsSection: View {
    let metrics: [DeepDiveMetricCard]
    let assessment: ReportOverallAssessment
    // Rich Growth chart data (parity with the free Growth chart). When present,
    // the Growth card becomes tappable and opens the rich chart in a sheet (like
    // the other cards open their history drill-down) instead of rendering inline.
    // nil on legacy reports → the Growth card falls back to its per-metric history
    // drill-down if it has one, else it's inert.
    var growthData: GrowthSectionData? = nil

    // Tapping a card opens a drill-down sheet:
    //  • Growth (with chart data) → the rich Growth chart (GrowthChartSheet)
    //  • Profitability / Valuation / Health (with baked history) → FundamentalsHistorySheet
    // Legacy cards with neither stay inert — selectedCard stays nil.
    @State private var selectedCard: DeepDiveMetricCard?

    /// A card is tappable when it's the Growth card and we have the rich chart
    /// data, OR it carries a baked per-metric time series.
    private func isTappable(_ metric: DeepDiveMetricCard) -> Bool {
        if metric.title == "Growth" && growthData != nil { return true }
        return metric.hasHistory
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // 2x2 metric grid — ALL fundamental cards (Growth included as a
            // compact card; its rich chart opens on tap, not inline).
            LazyVGrid(
                columns: [
                    GridItem(.flexible(), spacing: AppSpacing.sm),
                    GridItem(.flexible(), spacing: AppSpacing.sm)
                ],
                spacing: AppSpacing.md
            ) {
                ForEach(metrics) { metric in
                    if isTappable(metric) {
                        Button { selectedCard = metric } label: {
                            ReportDeepDiveMetricCard(data: metric, showsChevron: true)
                        }
                        .buttonStyle(.plain)
                    } else {
                        ReportDeepDiveMetricCard(data: metric)
                    }
                }
            }

            // Footnote explaining the " *" on sector-compared metrics
            // (Valuation P/E, P/B, P/S … and Health Debt-to-Equity etc.).
            if metrics.contains(where: { $0.hasSectorComparison }) {
                Text("* Compared to its sector average.")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.horizontal, AppSpacing.xs)
            }

            // Overall Assessment
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

                Text(assessment.text)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(3)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .sheet(item: $selectedCard) { card in
            // The Growth card opens the rich chart; the others open the
            // per-metric history drill-down. Growth with no chart data falls
            // through to its history drill-down (legacy reports).
            if card.title == "Growth", let growthData {
                GrowthChartSheet(card: card, growthData: growthData)
            } else {
                FundamentalsHistorySheet(card: card)
            }
        }
    }
}

#Preview {
    ReportFundamentalsSection(
        metrics: TickerReportData.sampleOracle.fundamentalMetrics,
        assessment: TickerReportData.sampleOracle.overallAssessment,
        growthData: GrowthSectionData.sampleData
    )
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
