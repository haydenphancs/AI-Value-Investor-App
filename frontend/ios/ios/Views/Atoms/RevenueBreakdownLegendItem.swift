//
//  RevenueBreakdownLegendItem.swift
//  ios
//
//  Atom: Single legend item for revenue breakdown chart
//

import SwiftUI

struct RevenueBreakdownLegendItem: View {
    let color: Color
    let name: String
    let value: String
    let percentage: String

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            // Color dot
            Circle()
                .fill(color)
                .frame(width: 10, height: 10)
                .padding(.top, 4)

            // Name
            Text(name)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textPrimary)
                .frame(minWidth: 60, alignment: .leading)

            Spacer()

            // Value and percentage stacked
            VStack(alignment: .trailing, spacing: 0) {
                Text(value)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textPrimary)
                Text("(\(percentage))")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.md) {
            RevenueBreakdownLegendItem(
                color: .blue,
                name: "iPhone",
                value: "205.5B",
                percentage: "52%"
            )

            RevenueBreakdownLegendItem(
                color: .purple,
                name: "Services",
                value: "73.10B",
                percentage: "23%"
            )

            RevenueBreakdownLegendItem(
                color: .green,
                name: "Net Profit",
                value: "72B",
                percentage: "38%"
            )
        }
        .padding()
    }
}
