//
//  ReportHeaderBar.swift
//  ios
//
//  Molecule: Top navigation bar for the report with back, company info, and share
//

import SwiftUI

struct ReportHeaderBar: View {
    let companyName: String
    let ticker: String
    let exchange: String
    let onBack: () -> Void
    let onShare: () -> Void

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Back button
            Button(action: onBack) {
                Image(systemName: "chevron.left")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 36, height: 36)
            }

            // Company logo placeholder + info
            HStack(spacing: AppSpacing.md) {
                // Logo placeholder
                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 36, height: 36)
                    .overlay(
                        Text(String(companyName.prefix(1)))
                            .font(AppTypography.headline)
                            .foregroundColor(AppColors.textPrimary)
                    )

                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(companyName)
                        .font(AppTypography.headline)
                        .foregroundColor(AppColors.textPrimary)

                    HStack(spacing: AppSpacing.xs) {
                        Text(ticker)
                            .font(AppTypography.footnote)
                            .foregroundColor(AppColors.textSecondary)

                        Text("•")
                            .font(AppTypography.footnote)
                            .foregroundColor(AppColors.textMuted)

                        Text(exchange)
                            .font(AppTypography.footnote)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }

            Spacer()

            // Share button
            Button(action: onShare) {
                Image(systemName: "square.and.arrow.up")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 36, height: 36)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
    }
}

#Preview {
    ReportHeaderBar(
        companyName: "Oracle",
        ticker: "ORCL",
        exchange: "Nasdaq",
        onBack: {},
        onShare: {}
    )
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
