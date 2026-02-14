//
//  CryptoPriceHeader.swift
//  ios
//
//  Molecule: Crypto price display with name, symbol, price and change
//

import SwiftUI

struct CryptoPriceHeader: View {
    let cryptoName: String
    let symbol: String
    let price: String
    let priceChange: String
    let priceChangePercent: String
    let isPositive: Bool
    let marketStatus: CryptoMarketStatus

    private var changeColor: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    private var arrowIcon: String {
        isPositive ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill"
    }

    var body: some View {
        HStack(alignment: .top) {
            // Left side - Crypto name and market status
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(cryptoName)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)

                CryptoMarketStatusBadge(status: marketStatus)
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

// MARK: - Crypto Market Status Badge
struct CryptoMarketStatusBadge: View {
    let status: CryptoMarketStatus

    var body: some View {
        Text(status.displayText)
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textSecondary)
    }
}

#Preview {
    VStack(spacing: AppSpacing.xl) {
        CryptoPriceHeader(
            cryptoName: "Bitcoin",
            symbol: "BTC",
            price: "$97,542.18",
            priceChange: "+$1,832.45",
            priceChangePercent: "(+1.91%)",
            isPositive: true,
            marketStatus: .trading
        )

        CryptoPriceHeader(
            cryptoName: "Ethereum",
            symbol: "ETH",
            price: "$3,456.78",
            priceChange: "-$45.23",
            priceChangePercent: "(-1.29%)",
            isPositive: false,
            marketStatus: .maintenance(resumeTime: "2:00 PM UTC")
        )
    }
    .padding(.vertical)
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
