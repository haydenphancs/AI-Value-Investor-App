//
//  CommodityPriceHeader.swift
//  ios
//
//  Molecule: Commodity price display with name, symbol, price and change
//

import SwiftUI

struct CommodityPriceHeader: View {
    let commodityName: String
    let symbol: String
    let price: String
    let priceChange: String
    let priceChangePercent: String
    let isPositive: Bool
    let marketStatus: CommodityMarketStatus

    private var changeColor: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    private var arrowIcon: String {
        isPositive ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill"
    }

    var body: some View {
        HStack(alignment: .top) {
            // Left side - Commodity name and market status
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(commodityName)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)

                CommodityMarketStatusBadge(status: marketStatus)
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

// MARK: - Commodity Market Status Badge
struct CommodityMarketStatusBadge: View {
    let status: CommodityMarketStatus

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Circle()
                .fill(statusColor)
                .frame(width: 6, height: 6)

            Text(status.displayText)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }

    private var statusColor: Color {
        switch status {
        case .open:
            return AppColors.bullish
        case .closed:
            return AppColors.bearish
        case .preMarket:
            return AppColors.neutral
        case .afterHours:
            return AppColors.neutral
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.xl) {
        CommodityPriceHeader(
            commodityName: "Gold",
            symbol: "GCUSD",
            price: "$2,345.60",
            priceChange: "+18.40",
            priceChangePercent: "(+0.79%)",
            isPositive: true,
            marketStatus: .open
        )

        CommodityPriceHeader(
            commodityName: "Crude Oil WTI",
            symbol: "CLUSD",
            price: "$78.42",
            priceChange: "-1.23",
            priceChangePercent: "(-1.54%)",
            isPositive: false,
            marketStatus: .closed(date: Date(), time: "5:00 PM", timezone: "ET")
        )
    }
    .padding(.vertical)
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
