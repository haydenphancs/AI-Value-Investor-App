//
//  TickerCard.swift
//  ios
//
//  Molecule: Market ticker card with price and sparkline
//

import SwiftUI

struct TickerCard: View {
    let ticker: MarketTicker

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            // Ticker Name
            Text(ticker.name)
                .font(AppTypography.tickerName)
                .foregroundColor(AppColors.textSecondary)
                .lineLimit(1)

            // Price
            Text(ticker.formattedPrice)
                .font(AppTypography.tickerPrice)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)

            // Sparkline
            SparklineView(data: ticker.sparklineData, isPositive: ticker.isPositive)
                .frame(height: 20)

            // Change Percentage
            Text(ticker.formattedChange)
                .font(AppTypography.tickerChange)
                .foregroundColor(ticker.isPositive ? AppColors.bullish : AppColors.bearish)
        }
        .padding(AppSpacing.sm)
        .frame(width: 80, height: 80)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}

#Preview {
    HStack(spacing: 12) {
        TickerCard(ticker: MarketTicker(
            name: "S&P 500",
            price: 6783.45,
            changePercent: 0.85,
            sparklineData: [100, 102, 98, 105, 103, 108, 110, 107, 112, 115]
        ))

        TickerCard(ticker: MarketTicker(
            name: "Bitcoin",
            price: 89394.43,
            changePercent: -2.34,
            sparklineData: [115, 112, 108, 105, 110, 103, 100, 98, 95, 92]
        ))
    }
    .padding()
    .background(AppColors.background)
}
