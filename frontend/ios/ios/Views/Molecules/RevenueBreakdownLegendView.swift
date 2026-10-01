//
//  RevenueBreakdownLegendView.swift
//  ios
//
//  Molecule: Two-column legend showing revenue sources and costs/profit
//

import SwiftUI

struct RevenueBreakdownLegendView: View {
    let data: RevenueBreakdownData

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Revenue Sources Column
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Revenue Sources")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .padding(.bottom, AppSpacing.xs)

                ForEach(data.revenueSources) { source in
                    // `legendValue(for:)`, not `formattedValue`: a negative-revenue filer's
                    // lone Total Revenue bar is drawn at 0 but its row prints the reported,
                    // signed figure (and "—" for its share, since there is no positive base).
                    RevenueBreakdownLegendItem(
                        color: source.color,
                        name: source.name,
                        value: data.legendValue(for: source),
                        percentage: source.formattedPercentage(of: data.revenueBasis)
                    )
                }
                // The negative line that makes a gross stack add to 100%: INTC's segments
                // are 61 + 34 + 32 + 7 = 134% of revenue until the eliminations take 33%
                // back. Same grey as the waterfall step it explains.
                if let eliminations = data.eliminationsLegendItem {
                    RevenueBreakdownLegendItem(
                        color: eliminations.color,
                        name: eliminations.name,
                        value: eliminations.formattedValue,
                        percentage: eliminations.formattedPercentage(of: data.revenueBasis)
                    )
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            // Costs & Profit / Loss column — the heading follows the sign of net income
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text(data.costsColumnTitle)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .padding(.bottom, AppSpacing.xs)

                // `revenueBasis`, not `totalRevenue`: percentages divide by REPORTED revenue,
                // which the segment sum does not have to equal.
                ForEach(data.costItems) { item in
                    RevenueBreakdownLegendItem(
                        color: item.color,
                        name: item.name,
                        value: item.formattedValue,
                        percentage: item.formattedPercentage(of: data.revenueBasis)
                    )
                }

                // Net Profit/Loss
                RevenueBreakdownLegendItem(
                    color: data.netProfitColor,
                    name: data.netProfitLabel,
                    value: data.formattedNetProfit,
                    percentage: data.formattedNetProfitPercentage
                )
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xxl) {
            RevenueBreakdownLegendView(data: RevenueBreakdownData.sampleApple)
                .padding()

            Divider()

            RevenueBreakdownLegendView(data: RevenueBreakdownData.sampleLossCompany)
                .padding()
        }
    }
}
