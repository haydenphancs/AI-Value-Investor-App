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

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // 2x2 metric grid
            LazyVGrid(
                columns: [
                    GridItem(.flexible(), spacing: AppSpacing.md),
                    GridItem(.flexible(), spacing: AppSpacing.md)
                ],
                spacing: AppSpacing.md
            ) {
                ForEach(metrics) { metric in
                    ReportDeepDiveMetricCard(data: metric)
                }
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
                        .font(.system(size: 16, weight: .semibold))

                    Text("Insight")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(LinearGradient(
                            colors: [.indigo, .cyan],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        ))
                }

                Text(assessment.text)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(3)
            }
            .padding(AppSpacing.md)
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
