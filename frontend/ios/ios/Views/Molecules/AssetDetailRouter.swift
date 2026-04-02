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
    var onNavigateToResearch: (() -> Void)? = nil

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
            TickerDetailView(tickerSymbol: selection.symbol, onNavigateToResearch: onNavigateToResearch)
        }
    }
}
