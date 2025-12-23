//
//  StockListItem.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct StockListItem: View {
    let stock: Stock

    var body: some View {
        HStack(spacing: 12) {
            // Left: Stock Info
            VStack(alignment: .leading, spacing: 4) {
                Text(stock.ticker)
                    .font(.system(size: 16, weight: .bold))
                    .foregroundColor(AppColors.primaryText)

                Text(stock.companyName)
                    .font(.system(size: 12, weight: .regular))
                    .foregroundColor(AppColors.secondaryText)

                HStack(spacing: 12) {
                    Text(stock.formattedOpen)
                        .font(.system(size: 11, weight: .regular))
                        .foregroundColor(AppColors.tertiaryText)

                    Text(stock.formattedVolume)
                        .font(.system(size: 11, weight: .regular))
                        .foregroundColor(AppColors.tertiaryText)
                }
                .padding(.top, 2)
            }

            Spacer()

            // Middle: Chart
            MiniLineChart(
                dataPoints: stock.chartData,
                isPositive: stock.isPositive,
                lineWidth: 1.5
            )
            .frame(width: 60, height: 30)

            // Right: Price Info
            VStack(alignment: .trailing, spacing: 4) {
                Text(stock.formattedPrice)
                    .font(.system(size: 16, weight: .bold))
                    .foregroundColor(AppColors.primaryText)

                Text(stock.formattedChange)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(stock.isPositive ? AppColors.positive : AppColors.negative)
            }
        }
        .padding(14)
        .cardStyle()
    }
}

#Preview {
    VStack(spacing: 12) {
        ForEach(Stock.mockPortfolio) { stock in
            StockListItem(stock: stock)
        }
    }
    .padding()
    .background(AppColors.background)
}
