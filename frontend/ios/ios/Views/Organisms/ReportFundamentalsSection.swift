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
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "brain.head.profile")
                        .font(.system(size: 14))
                        .foregroundColor(AppColors.neutral)

                    Text("Overall Assessment")
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.textPrimary)
                }

                Text(assessment.text)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(3)
            }
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
