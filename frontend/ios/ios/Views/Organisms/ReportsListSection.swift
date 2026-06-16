//
//  ReportsListSection.swift
//  ios
//
//  Organism: List of analysis reports with sort option
//

import SwiftUI

struct ReportsListSection: View {
    let reports: [AnalysisReport]
    @Binding var sortOption: ReportSortOption
    var onReportTapped: ((AnalysisReport) -> Void)?
    var onRetryTapped: ((AnalysisReport) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Sort header
            // Pull-down Menu (same component as the report's ••• overflow menu),
            // so the options render at the compact context-menu size — matching
            // "Share" — instead of the larger action-sheet buttons a
            // .confirmationDialog would use.
            Menu {
                ForEach(ReportSortOption.allCases, id: \.rawValue) { option in
                    Button(option.rawValue) {
                        sortOption = option
                    }
                }
            } label: {
                HStack(spacing: AppSpacing.xxs) {
                    Text("Sort")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)

                    Image(systemName: "arrow.up.arrow.down")
                        .font(AppTypography.iconTiny).fontWeight(.medium)
                        .foregroundColor(AppColors.textSecondary)
                }
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xs)
                .background(
                    Capsule()
                        .fill(AppColors.cardBackgroundLight)
                )
            }

            // Reports list
            LazyVStack(spacing: AppSpacing.md) {
                ForEach(reports) { report in
                    ReportCard(
                        report: report,
                        onTap: {
                            onReportTapped?(report)
                        },
                        onRetry: {
                            onRetryTapped?(report)
                        }
                    )
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    ScrollView {
        ReportsListSection(
            reports: AnalysisReport.mockReports,
            sortOption: .constant(.dateNewest)
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
