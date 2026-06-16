//
//  ReportExecutiveSummaryCard.swift
//  ios
//
//  Molecule: Executive summary — a general overview paragraph. (Category
//  bullets were removed; the specific number-backed points live in Bull/Bear.)
//

import SwiftUI

struct ReportExecutiveSummaryCard: View {
    let summaryText: String

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section header
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "doc.text.fill")
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.primaryBlue)

                Text("Executive Summary")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Summary text — a general overview (what the company is, how it's
            // doing, the report's take). Specifics live in Bull/Bear below.
            Text(summaryText)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(4)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }
}

#Preview {
    ReportExecutiveSummaryCard(
        summaryText: TickerReportData.sampleOracle.executiveSummaryText
    )
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
