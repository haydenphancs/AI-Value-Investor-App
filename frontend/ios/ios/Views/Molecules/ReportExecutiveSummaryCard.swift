//
//  ReportExecutiveSummaryCard.swift
//  ios
//
//  Molecule: Executive summary section with description and categorized bullets
//

import SwiftUI

struct ReportExecutiveSummaryCard: View {
    let summaryText: String
    let bullets: [ExecutiveSummaryBullet]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section header
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "doc.text.fill")
                    .font(.system(size: 14))
                    .foregroundColor(AppColors.primaryBlue)

                Text("Executive Summary")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Summary text
            Text(summaryText)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(4)

            // Bullet points
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                ForEach(bullets) { bullet in
                    HStack(alignment: .top, spacing: AppSpacing.sm) {
                        Circle()
                            .fill(bullet.sentiment.color)
                            .frame(width: 6, height: 6)
                            .padding(.top, 6)

                        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                            Text(bullet.category + ":")
                                .font(AppTypography.footnoteBold)
                                .foregroundColor(AppColors.textPrimary)
                            +
                            Text(" " + bullet.text)
                                .font(AppTypography.footnote)
                                .foregroundColor(AppColors.textSecondary)
                        }
                    }
                }
            }
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
        summaryText: TickerReportData.sampleOracle.executiveSummaryText,
        bullets: TickerReportData.sampleOracle.executiveSummaryBullets
    )
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
