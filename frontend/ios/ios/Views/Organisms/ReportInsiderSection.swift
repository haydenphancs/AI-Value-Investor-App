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

    // Owns the mini-chart's selected quarter so a tap anywhere outside a chart
    // column dismisses the popup (mirrors the Institutions chart in Wall Street
    // Consensus). The chart's own tap wins for taps on a column.
    @State private var selectedChartPeriod: String?

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
        .contentShape(Rectangle())
        .onTapGesture { selectedChartPeriod = nil }
    }

    // MARK: - Capital Allocation

    private func capitalAllocationCard(_ ca: ReportCapitalAllocation) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section header — OUTSIDE the card, same style as "Insider Activity"
            // / "Key Management" (bodySmallEmphasis · textSecondary).
            Text("Capital Allocation")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)

            // The three metrics in one gray card — shared strip (same style as
            // Congressional Trades / Short Selling).
            ReportMetricsStrip(metrics: [
                ReportMetricItem(
                    label: "Dividend Yield",
                    value: ca.dividendYield > 0 ? ca.dividendYieldText : "None"
                ),
                ReportMetricItem(
                    label: "Buybacks",
                    value: ca.buybackStatus,
                    valueColor: sentimentColor(ca.buybackSentiment)
                ),
                ReportMetricItem(
                    label: "Share Count",
                    value: ca.shareCountChangeText,
                    valueColor: ca.shareCountChange < 0 ? AppColors.bullish
                        : ca.shareCountChange > 0 ? AppColors.bearish
                        : AppColors.textSecondary
                ),
            ])

            // Dilution mini-chart — FLAT (no card), so the chart reads cleanly
            // against the section like the Hidden Market Signals charts. Shows
            // WHY the buyback status reads as it does (rising shares = diluting);
            // the x-axis labels the quarters; tap a quarter for values. Hidden
            // when fewer than 2 quarters exist.
            if ca.hasTrend {
                VStack(spacing: AppSpacing.xs) {
                    CapitalAllocationMiniChart(dataPoints: ca.dataPoints, selectedPeriod: $selectedChartPeriod)

                    SignalOfConfidenceLegendView()
                        .frame(maxWidth: .infinity, alignment: .center)
                }
            }
        }
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
