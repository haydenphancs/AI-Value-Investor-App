//
//  MarketTickersRow.swift
//  ios
//
//  Organism: Horizontal scrollable row of market tickers
//

import SwiftUI

struct MarketTickersRow: View {
    let tickers: [MarketTicker]
    var onTickerTap: ((MarketTicker) -> Void)?

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: AppSpacing.md) {
                ForEach(tickers) { ticker in
                    TickerCard(ticker: ticker) { tapped in
                        onTickerTap?(tapped)
                    }
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    MarketTickersRow(tickers: [
        MarketTicker(name: "S&P 500", symbol: "^GSPC", type: .index, price: 6783.45, changePercent: 0.85, sparklineData: [100, 102, 98, 105, 103, 108, 110, 107, 112, 115]),
        MarketTicker(name: "Nasdaq", symbol: "^IXIC", type: .index, price: 23293.23, changePercent: 0.85, sparklineData: [100, 102, 98, 105, 103, 108, 110, 107, 112, 115]),
        MarketTicker(name: "Bitcoin", symbol: "BTCUSD", type: .crypto, price: 89394.43, changePercent: -2.34, sparklineData: [115, 112, 108, 105, 110, 103, 100, 98, 95, 92]),
        MarketTicker(name: "Gold", symbol: "GCUSD", type: .commodity, price: 4322.43, changePercent: -1.34, sparklineData: [115, 112, 108, 105, 110, 103, 100, 98, 95, 92])
    ])
    .background(AppColors.background)
}
