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

    @Environment(\.differentiateWithoutColor) private var differentiate

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(price)
                    .font(AppTypography.dataHero)
                    .foregroundColor(AppColors.textPrimary)

                Text("Current Price")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            Spacer()

            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                // `change` is a caller-supplied STRING coloured by an unrelated Bool, so
                // nothing here can guarantee it carries a sign — the direction may be
                // encoded in the hue alone. The arrow is derived from `isPositive`, the
                // same flag that picks the colour, so the two can never disagree.
                // `PriceChangeLabel` does the same thing and is the pattern to copy.
                HStack(spacing: 2) {
                    if differentiate {
                        Image(systemName: AppSentiment.symbolName(isPositive: isPositive))
                            .font(AppTypography.iconXS)
                            .accessibilityHidden(true)
                    }
                    Text(change)
                        .font(AppTypography.headingSmall)
                }
                .foregroundColor(isPositive ? AppColors.bullish : AppColors.bearish)
                .accessibilityLabel("\(isPositive ? "Up" : "Down") \(change) over \(period)")

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
}
