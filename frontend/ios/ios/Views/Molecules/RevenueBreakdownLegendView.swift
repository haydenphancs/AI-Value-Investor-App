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
        HStack(alignment: .top, spacing: AppSpacing.xl) {
            // Revenue Sources Column
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Revenue Sources")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)
                    .padding(.bottom, AppSpacing.xs)

                ForEach(data.revenueSources) { source in
                    RevenueBreakdownLegendItem(
                        color: source.color,
                        name: source.name,
                        value: source.formattedValue,
                        percentage: source.formattedPercentage(of: data.totalRevenue)
                    )
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            // Costs & Profit Column
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Costs & Profit")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)
                    .padding(.bottom, AppSpacing.xs)

                ForEach(data.costItems) { item in
                    RevenueBreakdownLegendItem(
                        color: item.color,
                        name: item.name,
                        value: item.formattedValue,
                        percentage: item.formattedPercentage(of: data.totalRevenue)
                    )
                }

                // Net Profit/Loss
                RevenueBreakdownLegendItem(
                    color: data.netProfitColor,
                    name: data.netProfitLabel,
                    value: data.formattedNetProfit,
                    percentage: String(format: "%.0f%%", data.netProfitPercentage())
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
