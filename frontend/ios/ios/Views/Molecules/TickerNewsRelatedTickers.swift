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
        // Wrap onto new lines instead of an HStack that squeezes every chip onto
        // one row — with many related tickers the chips got so compressed the
        // symbols wrapped mid-text ("SC\nHW") and were unreadable.
        FlowLayout(spacing: AppSpacing.sm, lineSpacing: AppSpacing.sm) {
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
        .frame(maxWidth: .infinity, alignment: .leading)
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
