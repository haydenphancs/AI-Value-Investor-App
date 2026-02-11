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

                Text(management.ownershipInsight)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(3)
            }
            .padding(AppSpacing.md)
        }
    }
}

#Preview {
    ReportKeyManagementTable(management: TickerReportData.sampleOracle.keyManagement)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
