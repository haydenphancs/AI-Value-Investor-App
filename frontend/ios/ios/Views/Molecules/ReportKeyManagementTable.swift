//
//  ReportKeyManagementTable.swift
//  ios
//
//  Molecule: Key management table showing executives and ownership
//

import SwiftUI

struct ReportKeyManagementTable: View {
    let management: ReportKeyManagement

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Key Management")
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textSecondary)

            // Table header
            HStack {
                Text("")
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text("Ownership")
                    .frame(width: 80, alignment: .trailing)
            }
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)

            // Manager rows
            ForEach(management.managers) { manager in
                VStack(spacing: AppSpacing.xs) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                            Text(manager.name)
                                .font(AppTypography.subheadline)
                                .foregroundColor(AppColors.textPrimary)
                            Text(manager.title)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
                                .lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)

                        VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                            Text(manager.ownership)
                                .font(AppTypography.subheadline)
                                .fontWeight(.medium)
                                .foregroundColor(AppColors.textPrimary)
                            Text(manager.ownershipValue)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
                        }
                        .frame(width: 80, alignment: .trailing)
                    }

                    Divider()
                        .background(AppColors.textMuted.opacity(0.15))
                }
            }

            // Ownership insight
            HStack(alignment: .top, spacing: AppSpacing.sm) {
                Image(systemName: "lightbulb.fill")
                    .font(.system(size: 12))
                    .foregroundColor(AppColors.neutral)

                Text(management.ownershipInsight)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(2)
            }
            .padding(AppSpacing.sm)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(AppColors.neutral.opacity(0.08))
            )
        }
    }
}

#Preview {
    ReportKeyManagementTable(management: TickerReportData.sampleOracle.keyManagement)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
