//
//  ReportMetricsStrip.swift
//  ios
//
//  Molecule: a row of N labeled metrics in ONE gray card, separated by thin
//  vertical "|" dividers — the Capital Allocation card style, extracted so the
//  report's stat strips (Capital Allocation, Congressional Trades, Short
//  Selling) read identically. Each column: value on top (bold, color varies) ·
//  label below (muted).
//

import SwiftUI

struct ReportMetricItem: Identifiable {
    let id = UUID()
    let label: String
    let value: String
    var valueColor: Color = AppColors.textPrimary
}

struct ReportMetricsStrip: View {
    let metrics: [ReportMetricItem]

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            ForEach(Array(metrics.enumerated()), id: \.offset) { index, metric in
                if index > 0 {
                    Rectangle()
                        .fill(AppColors.textMuted.opacity(0.2))
                        .frame(width: 1, height: 30)
                }
                VStack(alignment: .center, spacing: 2) {
                    Text(metric.value)
                        .font(AppTypography.label)
                        .fontWeight(.semibold)
                        .foregroundColor(metric.valueColor)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                    Text(metric.label)
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textMuted)
                }
                .frame(maxWidth: .infinity, alignment: .center)
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
        )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ReportMetricsStrip(metrics: [
            ReportMetricItem(label: "Dividend Yield", value: "0.93%"),
            ReportMetricItem(label: "Buybacks", value: "Diluting", valueColor: AppColors.bearish),
            ReportMetricItem(label: "Share Count", value: "+3.7%", valueColor: AppColors.bearish),
        ])
        ReportMetricsStrip(metrics: [
            ReportMetricItem(label: "Buyer", value: "1", valueColor: AppColors.bullish),
            ReportMetricItem(label: "Sellers", value: "4", valueColor: AppColors.bearish),
            ReportMetricItem(label: "Net", value: "Sell", valueColor: AppColors.bearish),
        ])
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
