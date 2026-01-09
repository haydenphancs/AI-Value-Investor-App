//
//  TickerNewsRelatedTickers.swift
//  ios
//
//  Molecule: Row of related ticker chips for news card
//

import SwiftUI

struct TickerNewsRelatedTickers: View {
    let tickers: [String]
    var currentTicker: String?
    var onTickerTap: ((String) -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            ForEach(tickers, id: \.self) { ticker in
                Button(action: {
                    onTickerTap?(ticker)
                }) {
                    RelatedTickerChip(
                        symbol: ticker,
                        isHighlighted: false
                    )
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        TickerNewsRelatedTickers(
            tickers: ["AAPL", "MSFT"],
            currentTicker: "AAPL"
        )

        TickerNewsRelatedTickers(
            tickers: ["AAPL", "META"],
            currentTicker: "AAPL"
        )

        TickerNewsRelatedTickers(
            tickers: ["AAPL", "GOOGL"],
            currentTicker: "AAPL"
        )
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
