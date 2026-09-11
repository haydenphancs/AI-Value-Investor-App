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
        // Normalised and, for an unknown/legacy value ("Stock", the DB column default the
        // backend used to publish verbatim), derived from the SYMBOL: a `BTCUSD` row typed
        // "Stock" used to fall through to the equity screen, which is FMP-blocked for it.
        // The search route's "fund" is folded into `.etf` by `resolve` — a closed-end or
        // index fund is the ETF screen, not the company-profile pipeline.
        switch MarketTickerType.resolve(selection.type, symbol: selection.symbol) {
        case .crypto:
            CryptoDetailView(cryptoSymbol: selection.symbol)
        case .etf:
            ETFDetailView(etfSymbol: selection.symbol)
        case .index:
            IndexDetailView(indexSymbol: selection.symbol)
        case .commodity:
            CommodityDetailView(commoditySymbol: selection.symbol)
        case .stock:
            // The Research route is parked on `AppState.pendingResearchTicker` by the detail
            // screen itself now. It used to be an injected closure that only Tracking supplied,
            // so the "AI Deep Research" button opened a chat from every other entry point.
            TickerDetailView(tickerSymbol: selection.symbol)
        }
    }
}
