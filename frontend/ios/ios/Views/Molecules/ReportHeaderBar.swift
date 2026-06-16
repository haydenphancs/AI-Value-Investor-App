//
//  ReportHeaderBar.swift
//  ios
//
//  Molecule: Top navigation bar for the report with back, company info,
//  and an overflow (•••) menu containing share, detailed analysis, delete.
//

import SwiftUI

struct ReportHeaderBar: View {
    let companyName: String
    let ticker: String
    let exchange: String
    let currentPrice: Double
    let onBack: () -> Void
    let onShare: () -> Void
    let onViewDetailedAnalysis: () -> Void
    let onDelete: () -> Void
    /// PDF export (Share + View Detailed Analysis) is only available for saved
    /// research reports. Hidden otherwise so the menu never offers a dead action.
    var canExportPDF: Bool = true

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Back button
            Button(action: onBack) {
                Image(systemName: "chevron.down")
                    .font(AppTypography.iconMedium).fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 36, height: 36)
            }

            // Company logo placeholder + info
            HStack(spacing: AppSpacing.md) {
                // Company logo (real FMP logo by ticker, initials fallback)
                CompanyLogoView(ticker: ticker, size: 36)

                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(companyName)
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)

                    HStack(spacing: AppSpacing.xs) {
                        Text(ticker)
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textSecondary)

                        // Live current price, right next to the ticker (e.g.
                        // "ORCL • $226"). Emphasized so it reads as the key number.
                        if currentPrice > 0 {
                            Text("•")
                                .font(AppTypography.labelSmall)
                                .foregroundColor(AppColors.textMuted)

                            Text(String(format: "$%.2f", currentPrice))
                                .font(AppTypography.labelSmall).fontWeight(.semibold)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        if !exchange.isEmpty {
                            Text("•")
                                .font(AppTypography.labelSmall)
                                .foregroundColor(AppColors.textMuted)

                            Text(exchange)
                                .font(AppTypography.labelSmall)
                                .foregroundColor(AppColors.textSecondary)
                        }
                    }
                }
            }

            Spacer()

            // Overflow menu (••• → Share / View Ticker / View Detailed Analysis /
            // Regenerate / Delete). Destructive role auto-renders Delete in red.
            Menu {
                if canExportPDF {
                    Button(action: onShare) {
                        Label("Share", systemImage: "square.and.arrow.up")
                    }
                    Button(action: onViewDetailedAnalysis) {
                        Label("View Detailed Analysis", systemImage: "doc.text.magnifyingglass")
                    }
                    Divider()
                }
                Button(role: .destructive, action: onDelete) {
                    Label("Delete", systemImage: "trash")
                }
            } label: {
                Image(systemName: "line.3.horizontal.decrease")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
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
        currentPrice: 226,
        onBack: {},
        onShare: {},
        onViewDetailedAnalysis: {},
        onDelete: {}
    )
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
