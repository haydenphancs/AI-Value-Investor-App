//
//  RelatedTickersRow.swift
//  ios
//
//  Molecule: Horizontal row of related ticker tags with icon
//

import SwiftUI

struct RelatedTickersRow: View {
    let tickers: [String]
    var onTickerTapped: ((String) -> Void)?

    var body: some View {
        HStack(alignment: .center, spacing: AppSpacing.sm) {
            // Tag Icon
            Image(systemName: "tag.fill")
                .font(.system(size: 14, weight: .medium))
                .foregroundColor(AppColors.neutral)
                .rotationEffect(.degrees(-90))

            // Ticker Tags
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.sm) {
                    ForEach(tickers, id: \.self) { ticker in
                        RelatedTickerTag(ticker: ticker) {
                            onTickerTapped?(ticker)
                        }
                    }
                }
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        RelatedTickersRow(
            tickers: ["APPL", "ORCL", "TSLA"],
            onTickerTapped: { ticker in
                print("Tapped: \(ticker)")
            }
        )

        RelatedTickersRow(
            tickers: ["NVDA", "AMD", "INTC", "GOOGL", "META"]
        )

        RelatedTickersRow(
            tickers: ["AAPL"]
        )
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
