//
//  TickerStickyHeader.swift
//  ios
//
//  Molecule: Compact sticky header for Ticker Detail when scrolling
//

import SwiftUI

struct TickerStickyHeader: View {
    let companyName: String
    let symbol: String
    let price: String
    let priceChange: String
    let priceChangePercent: String
    let isPositive: Bool

    private var changeColor: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    var body: some View {
        HStack {
            // Left side - Company info
            VStack(alignment: .leading, spacing: 2) {
                Text(companyName)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)

                Text(symbol)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            Spacer()

            // Right side - Price info
            VStack(alignment: .trailing, spacing: 2) {
                Text(price)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                HStack(spacing: 4) {
                    Text("\(priceChange) \(priceChangePercent)")
                        .font(AppTypography.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(changeColor)
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
    }
}

#Preview {
    VStack {
        TickerStickyHeader(
            companyName: "Apple Inc.",
            symbol: "AAPL",
            price: "$178.42",
            priceChange: "+$2.34",
            priceChangePercent: "(+1.33%)",
            isPositive: true
        )

        TickerStickyHeader(
            companyName: "Tesla, Inc.",
            symbol: "TSLA",
            price: "$252.18",
            priceChange: "-$3.45",
            priceChangePercent: "(-1.35%)",
            isPositive: false
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
