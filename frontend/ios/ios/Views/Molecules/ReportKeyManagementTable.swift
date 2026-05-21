//
//  ReportKeyManagementTable.swift
//  ios
//
//  Molecule: Key management table with two sub-sections — Top Holders
//  (10%+ owners, paired with 13G filings) and Officers (sorted by role
//  rank: CEO → CFO → COO → …).
//

import SwiftUI

struct ReportKeyManagementTable: View {
    let management: ReportKeyManagement

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Key Management")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)

            // Column header sits above the first non-empty sub-section.
            HStack {
                Text("")
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text("Ownership")
                    .frame(width: 80, alignment: .trailing)
            }
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)

            if !management.topHolders.isEmpty {
                sectionHeader("Top Holders")
                ForEach(management.topHolders) { managerRow($0) }
            }

            if !management.officers.isEmpty {
                sectionHeader("Officers")
                ForEach(management.officers) { managerRow($0) }
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
                        .font(AppTypography.iconDefault).fontWeight(.semibold)

                    Text("Insight")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundStyle(LinearGradient(
                            colors: [.indigo, .cyan],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        ))
                }

                Text(management.ownershipInsight)
                    .font(AppTypography.label)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(3)
            }
            .padding(AppSpacing.md)
        }
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title)
            .font(AppTypography.caption)
            .fontWeight(.semibold)
            .foregroundColor(AppColors.textMuted)
            .padding(.top, AppSpacing.xs)
    }

    private func managerRow(_ manager: KeyManager) -> some View {
        VStack(spacing: AppSpacing.xs) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    HStack(spacing: AppSpacing.xs) {
                        Text(manager.name)
                            .font(AppTypography.label)
                            .foregroundColor(AppColors.textPrimary)
                        if let chip = manager.percentOwnershipLabel {
                            // 13G beneficial ownership chip — only
                            // shows for 5%+ filers (Ellison-style).
                            Text(chip)
                                .font(AppTypography.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(AppColors.bullish)
                                .padding(.horizontal, AppSpacing.sm)
                                .padding(.vertical, 2)
                                .background(
                                    Capsule()
                                        .fill(AppColors.bullish.opacity(0.15))
                                )
                        }
                    }
                    Text(manager.title)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                    Text(manager.ownership)
                        .font(AppTypography.label)
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
}

#Preview {
    ReportKeyManagementTable(management: TickerReportData.sampleOracle.keyManagement)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
