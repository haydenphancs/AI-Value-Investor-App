//
//  ReportMetricRow.swift
//  ios
//
//  Atom: Label-value row for displaying metrics in report cards
//

import SwiftUI

struct ReportMetricRow: View {
    let label: String
    let value: String
    var valueColor: Color = AppColors.textPrimary
    var labelColor: Color = AppColors.textSecondary
    var trend: DeepDiveMetric.MetricTrend? = nil

    var body: some View {
        HStack {
            Text(label)
                .font(AppTypography.subheadline)
                .foregroundColor(labelColor)

            Spacer()

            HStack(spacing: AppSpacing.xs) {
                if let trend = trend {
                    Image(systemName: trend.iconName)
                        .font(.system(size: 10, weight: .bold))
                        .foregroundColor(trend.color)
                }
                Text(value)
                    .font(AppTypography.subheadline)
                    .fontWeight(.semibold)
                    .foregroundColor(valueColor)
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.sm) {
        ReportMetricRow(label: "Gross Margin", value: "70%")
        ReportMetricRow(label: "Net Margin", value: "25%", valueColor: AppColors.bullish)
        ReportMetricRow(label: "Revenue ROII", value: "+18%", trend: .up)
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
