//
//  TickerChip.swift
//  ios
//
//  Atom: Quick ticker selection chip
//

import SwiftUI

struct TickerChip: View {
    let ticker: QuickTicker
    var isSelected: Bool = false
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Text(ticker.symbol)
                .font(AppTypography.footnote)
                .fontWeight(.medium)
                .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.small)
                        .fill(isSelected ? AppColors.primaryBlue : AppColors.cardBackgroundLight)
                )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    HStack(spacing: AppSpacing.sm) {
        TickerChip(ticker: QuickTicker(symbol: "AAPL"))
        TickerChip(ticker: QuickTicker(symbol: "TSLA"), isSelected: true)
        TickerChip(ticker: QuickTicker(symbol: "NVDA"))
        TickerChip(ticker: QuickTicker(symbol: "BTC"))
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
