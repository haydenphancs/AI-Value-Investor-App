//
//  AssetDetailRouter.swift
//  ios
//
//  Routes a SearchSelection to the correct detail view based on asset type.
//  Supports: stock, etf, crypto, index, commodity.
//

import SwiftUI

struct AssetDetailRouter: View {
    let selection: SearchSelection

    var body: some View {
        switch selection.type {
        case "crypto":
            CryptoDetailView(cryptoSymbol: selection.symbol)
        case "etf", "fund":
            ETFDetailView(etfSymbol: selection.symbol)
        case "index":
            IndexDetailView(indexSymbol: selection.symbol)
        case "commodity":
            CommodityDetailView(commoditySymbol: selection.symbol)
        default:
            // The Research route is parked on `AppState.pendingResearchTicker` by the detail
            // screen itself now. It used to be an injected closure that only Tracking supplied,
            // so the "AI Deep Research" button opened a chat from every other entry point.
            TickerDetailView(tickerSymbol: selection.symbol)
        }
    }
}
