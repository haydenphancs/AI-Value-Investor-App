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

    @State private var showSortMenu = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Sort header
            Button(action: {
                showSortMenu = true
            }) {
                HStack(spacing: AppSpacing.xxs) {
                    Text("Sort")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)

                    Image(systemName: "arrow.up.arrow.down")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundColor(AppColors.textSecondary)
                }
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xs)
                .background(
                    Capsule()
                        .fill(AppColors.cardBackgroundLight)
                )
            }
            .buttonStyle(PlainButtonStyle())
            .confirmationDialog("Sort Reports", isPresented: $showSortMenu) {
                ForEach(ReportSortOption.allCases, id: \.rawValue) { option in
                    Button(option.rawValue) {
                        sortOption = option
                    }
                }
                Button("Cancel", role: .cancel) {}
            }

            // Reports list
            VStack(spacing: AppSpacing.md) {
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
