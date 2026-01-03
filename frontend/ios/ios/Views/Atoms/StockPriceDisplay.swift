//
//  StockPriceDisplay.swift
//  ios
//
//  Atom: Large stock price display with change percentage
//

import SwiftUI

struct StockPriceDisplay: View {
    let price: String
    let change: String
    let period: String
    let isPositive: Bool

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(price)
                    .font(.system(size: 32, weight: .bold, design: .rounded))
                    .foregroundColor(AppColors.textPrimary)

                Text("Current Price")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            Spacer()

            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text(change)
                    .font(AppTypography.headline)
                    .foregroundColor(isPositive ? AppColors.bullish : AppColors.bearish)

                Text(period)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        StockPriceDisplay(price: "$242.84", change: "+8.7%", period: "1 Month", isPositive: true)
        StockPriceDisplay(price: "$185.92", change: "-2.3%", period: "1 Week", isPositive: false)
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
