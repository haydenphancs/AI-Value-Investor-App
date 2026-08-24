//
//  NotificationRouteDestination.swift
//  ios
//
//  Renders whatever screen a `NotificationRoute` points at.
//
//  A view-type DISPATCHER, not a navigation router — the same job `AssetDetailRouter`
//  does for an asset kind. It exists so the inbox and the push tap-handler resolve a
//  route through ONE switch: two copies would drift the moment a new route kind is added,
//  and the symptom would be "taps from the inbox go to the right place but taps from the
//  banner don't", which is miserable to diagnose.
//

import SwiftUI

struct NotificationRouteDestination: View {
    let route: NotificationRoute

    var body: some View {
        NavigationStack {
            Group {
                switch route {
                case .ticker(let symbol, let assetType, let destination):
                    // Asset type comes from the PAYLOAD. The old tap handler hardcoded
                    // `.stock`, so a crypto alert opened `TickerDetailView` and showed
                    // stock fundamentals for a coin.
                    switch assetType {
                    case .index:     IndexDetailView(indexSymbol: symbol)
                    // Only the stock screen has Financials/Holders, so only it takes
                    // the destination. The others ignore a tab they do not have.
                    case .stock:     TickerDetailView(tickerSymbol: symbol, destination: destination)
                    case .crypto:    CryptoDetailView(cryptoSymbol: symbol)
                    case .commodity: CommodityDetailView(commoditySymbol: symbol)
                    case .etf:       ETFDetailView(etfSymbol: symbol)
                    }

                case .report(_, let ticker):
                    // Reports were not routable at all before this. The report id is
                    // carried in the payload for a future direct-open; today the ticker's
                    // detail screen is where the finished report is read, and landing
                    // there beats landing nowhere.
                    TickerDetailView(tickerSymbol: ticker ?? "")

                case .inbox:
                    // Unreachable by construction: the only caller
                    // (`NotificationInboxContent`) filters `.inbox` out before setting a
                    // route, because "the row itself is already the content". Kept as an
                    // explicit no-op so the switch stays exhaustive — `NotificationRoute`
                    // gains cases over time and a `default:` here would silently swallow
                    // the next one.
                    EmptyView()
                }
            }
            .navigationBarHidden(route.isTickerLike)
        }
    }
}

private extension NotificationRoute {
    /// The detail screens draw their own header, so the nav bar is hidden for them.
    var isTickerLike: Bool {
        switch self {
        case .ticker, .report: return true
        case .inbox: return false
        }
    }
}
