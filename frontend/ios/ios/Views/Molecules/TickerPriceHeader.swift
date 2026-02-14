//
//  TickerPriceHeader.swift
//  ios
//
//  Molecule: Ticker price display with company name, symbol, price and change
//

import SwiftUI

struct TickerPriceHeader: View {
    let companyName: String
    let symbol: String
    let price: String
    let priceChange: String
    let priceChangePercent: String
    let isPositive: Bool
    let marketStatus: MarketStatus

    private var changeColor: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    private var arrowIcon: String {
        isPositive ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill"
    }

    var body: some View {
        HStack(alignment: .top) {
            // Left side - Company name and market status
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(companyName)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)
                
                MarketStatusBadge(status: marketStatus)
            }
            
            Spacer()
            
            // Right side - Price info
            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text(price)
                    .font(.system(size: 24, weight: .bold))
                    .foregroundColor(AppColors.textPrimary)

                // Price change
                HStack(spacing: 4) {
                    Image(systemName: arrowIcon)
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(changeColor)

                    Text("\(priceChange) \(priceChangePercent)")
                        .font(AppTypography.footnote)
                        .fontWeight(.semibold)
                        .foregroundColor(changeColor)
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    VStack(spacing: AppSpacing.xl) {
        TickerPriceHeader(
            companyName: "Apple Inc.",
            symbol: "AAPL",
            price: "$178.42",
            priceChange: "+$2.34",
            priceChangePercent: "(+1.33%)",
            isPositive: true,
            marketStatus: .closed(date: Date(), time: "4:00 PM", timezone: "EST")
        )

        TickerPriceHeader(
            companyName: "Tesla, Inc.",
            symbol: "TSLA",
            price: "$252.18",
            priceChange: "-$3.45",
            priceChangePercent: "(-1.35%)",
            isPositive: false,
            marketStatus: .open
        )
    }
    .padding(.vertical)
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
