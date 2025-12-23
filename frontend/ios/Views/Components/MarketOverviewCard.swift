//
//  MarketOverviewCard.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct MarketOverviewCard: View {
    let marketIndex: MarketIndex

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Header
            Text(marketIndex.name)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(AppColors.secondaryText)

            // Value
            Text(marketIndex.value)
                .font(.system(size: 20, weight: .bold))
                .foregroundColor(AppColors.primaryText)

            // Chart
            MiniLineChart(
                dataPoints: marketIndex.chartData,
                isPositive: marketIndex.isPositive
            )
            .frame(height: 30)
            .padding(.vertical, 4)

            // Change Percentage
            Text(marketIndex.formattedChange)
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(marketIndex.isPositive ? AppColors.positive : AppColors.negative)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardStyle()
    }
}

#Preview {
    HStack(spacing: 12) {
        ForEach(MarketIndex.mockData) { index in
            MarketOverviewCard(marketIndex: index)
        }
    }
    .padding()
    .background(AppColors.background)
}
