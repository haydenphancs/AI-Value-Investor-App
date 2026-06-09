//
//  ReportInsiderSection.swift
//  ios
//
//  Organism: Insider & Management deep dive — insider activity, capital
//  allocation (buybacks + dividends), and key management.
//

import SwiftUI

struct ReportInsiderSection: View {
    let insiderData: ReportInsiderData
    let management: ReportKeyManagement

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxl) {
            // Insider Activity
            ReportInsiderActivityTable(insiderData: insiderData)

            // Capital Allocation (buybacks + dividends) — shown when available.
            if let ca = insiderData.capitalAllocation {
                capitalAllocationCard(ca)
            }

            // Key Management
            ReportKeyManagementTable(management: management)
        }
    }

    // MARK: - Capital Allocation

    private func capitalAllocationCard(_ ca: ReportCapitalAllocation) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Capital Allocation")
                .font(AppTypography.label)
                .fontWeight(.semibold)
                .foregroundColor(AppColors.textPrimary)

            HStack(alignment: .top, spacing: AppSpacing.sm) {
                metric(
                    label: "Buybacks",
                    value: ca.buybackStatus,
                    color: sentimentColor(ca.buybackSentiment)
                )
                cardDivider
                metric(
                    label: "Dividend Yield",
                    value: ca.dividendYield > 0 ? ca.dividendYieldText : "None",
                    color: AppColors.textPrimary
                )
                cardDivider
                metric(
                    label: "Share Count",
                    value: ca.shareCountChangeText,
                    color: ca.shareCountChange < 0 ? AppColors.bullish
                        : ca.shareCountChange > 0 ? AppColors.bearish
                        : AppColors.textSecondary
                )
            }

            // Dilution mini-chart — shows WHY the buyback status reads as it
            // does (rising shares = diluting). The window caption scopes the
            // cumulative (up to ~2yr) share-count change so it isn't misread
            // as a 1-year figure. Hidden when fewer than 2 quarters exist.
            if ca.hasTrend {
                Rectangle()
                    .fill(AppColors.textMuted.opacity(0.15))
                    .frame(height: 1)
                    .padding(.top, AppSpacing.xs)

                Text("Share count change over \(ca.shareCountWindowText)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)

                CapitalAllocationMiniChart(dataPoints: ca.dataPoints)

                SignalOfConfidenceLegendView()
                    .padding(.top, AppSpacing.xs)
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
        )
    }

    private var cardDivider: some View {
        Rectangle()
            .fill(AppColors.textMuted.opacity(0.2))
            .frame(width: 1, height: 30)
    }

    private func metric(label: String, value: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
            Text(value)
                .font(AppTypography.label)
                .fontWeight(.semibold)
                .foregroundColor(color)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func sentimentColor(_ s: String) -> Color {
        switch s {
        case "positive": return AppColors.bullish
        case "negative": return AppColors.bearish
        default: return AppColors.neutral
        }
    }
}

#Preview {
    ReportInsiderSection(
        insiderData: TickerReportData.sampleOracle.insiderData,
        management: TickerReportData.sampleOracle.keyManagement
    )
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
