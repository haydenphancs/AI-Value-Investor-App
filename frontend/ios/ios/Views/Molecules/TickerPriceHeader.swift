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
    /// `false` when the backend said the day change is UNKNOWN (`change_known`): the
    /// caller's `isPositive` is then a placeholder, so the header must not assert a
    /// direction — neutral colour, no arrow, no flash. Defaults `true` for the callers
    /// whose models carry no such flag yet.
    var changeKnown: Bool = true

    @State private var priceFlashColor: Color?

    private var changeColor: Color {
        guard changeKnown else { return AppColors.textSecondary }
        return isPositive ? AppColors.bullish : AppColors.bearish
    }

    private var arrowIcon: String {
        isPositive ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill"
    }

    var body: some View {
        HStack(alignment: .top) {
            // Left side - Company name and market status
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(companyName)
                    .font(AppTypography.label)
                    .foregroundColor(AppColors.textSecondary)
                
                MarketStatusBadge(status: marketStatus)
            }
            
            Spacer()
            
            // Right side - Price info
            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text(price)
                    .font(AppTypography.title)
                    .foregroundColor(priceFlashColor ?? AppColors.textPrimary)
                    .contentTransition(.numericText())
                    .animation(.snappy(duration: 0.3), value: price)

                // Price change
                HStack(spacing: 4) {
                    // An arrow asserts a direction; an unknown change has none.
                    if changeKnown {
                        Image(systemName: arrowIcon)
                            .font(AppTypography.iconTiny).fontWeight(.semibold)
                            .foregroundColor(changeColor)
                    }

                    Text("\(priceChange) \(priceChangePercent)")
                        .font(AppTypography.labelSmall)
                        .fontWeight(.semibold)
                        .foregroundColor(changeColor)
                        .contentTransition(.numericText())
                        .animation(.snappy(duration: 0.3), value: priceChange)
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .onChange(of: price) { _, _ in
            // Brief color flash on price update (green for positive, red for negative)
            guard changeKnown else { return }
            priceFlashColor = isPositive ? AppColors.bullish : AppColors.bearish
            withAnimation(.easeOut(duration: 0.6)) {
                priceFlashColor = nil
            }
        }
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

        // Unknown change: em dash, no arrow, neutral — never a red decline.
        TickerPriceHeader(
            companyName: "S&P 500",
            symbol: "^GSPC",
            price: "6,012.34",
            priceChange: "—",
            priceChangePercent: "",
            isPositive: false,
            marketStatus: .open,
            changeKnown: false
        )
    }
    .padding(.vertical)
    .background(AppColors.background)
}
