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

    // Tapping a card with baked history opens the time-series drill-down.
    // Legacy reports (no history) leave cards inert — selectedCard stays nil.
    @State private var selectedCard: DeepDiveMetricCard?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // 2x2 metric grid
            LazyVGrid(
                columns: [
                    GridItem(.flexible(), spacing: AppSpacing.sm),
                    GridItem(.flexible(), spacing: AppSpacing.sm)
                ],
                spacing: AppSpacing.md
            ) {
                ForEach(metrics) { metric in
                    if metric.hasHistory {
                        Button { selectedCard = metric } label: {
                            ReportDeepDiveMetricCard(data: metric)
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
            FundamentalsHistorySheet(card: card)
        }
    }
}

#Preview {
    ReportFundamentalsSection(
        metrics: TickerReportData.sampleOracle.fundamentalMetrics,
        assessment: TickerReportData.sampleOracle.overallAssessment
    )
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
