//
//  HomeModels.swift
//  ios
//
//  The small shared types left from the old Home screen: the asset class behind every
//  ticker tap, the tapped-ticker selection, the sentiment enum and the tab bar.
//

import Foundation

// MARK: - Market Ticker Type
enum MarketTickerType: String, Codable, Hashable {
    case index      // S&P 500, Nasdaq, Dow Jones → IndexDetailView
    case stock      // Individual stocks → TickerDetailView
    case crypto     // Bitcoin, Ethereum → CryptoDetailView
    case commodity  // Gold, Oil, Silver → CommodityDetailView
    case etf        // ETFs → ETFDetailView

    /// Resolve a wire `type` string the way the backend's `resolve_asset_class` does: a
    /// SPECIFIC class ("crypto", "commodity", "index", "etf" — and the search route's
    /// "fund", which is the ETF screen) is trusted; anything else, including "Stock" (the
    /// DB column default the backend used to publish verbatim for every row) falls back
    /// to the SYMBOL — never to `.stock` blindly. A starred coin arrived as `type: "Stock"`
    /// for `BTCUSD`, decoded as `.stock`, and its tap opened the FMP-blocked equity screen.
    /// The symbol still says what it is: `^GSPC` is an index, `BTCUSD` a coin, `GCUSD` a
    /// commodity code.
    static func resolve(_ raw: String?, symbol: String) -> MarketTickerType {
        let normalized = (raw ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        if normalized == "fund" { return .etf }
        if normalized != "stock", let known = MarketTickerType(rawValue: normalized) {
            return known
        }
        let sym = symbol.trimmingCharacters(in: .whitespaces).uppercased()
        if sym.hasPrefix("^") { return .index }
        if MarketHoursUtil.commoditySymbols.contains(sym) { return .commodity }
        if sym.count > 3, sym.hasSuffix("USD") || sym.hasSuffix("USDT") { return .crypto }
        return .stock
    }
}

// MARK: - Market Ticker
/// A tapped ticker, held just long enough to present its detail screen.
struct MarketTicker: Identifiable, Hashable {
    let id: UUID
    let name: String
    let symbol: String
    let type: MarketTickerType
    let price: Double
    let changePercent: Double
    let sparklineData: [Double]

    init(name: String, symbol: String, type: MarketTickerType, price: Double, changePercent: Double, sparklineData: [Double]) {
        self.id = UUID()
        self.name = name
        self.symbol = symbol
        self.type = type
        self.price = price
        self.changePercent = changePercent
        self.sparklineData = sparklineData
    }
}

// MARK: - Market Sentiment
enum MarketSentiment: String, Codable {
    case bullish = "Bullish"
    case bearish = "Bearish"
    case neutral = "Neutral"
}

// MARK: - Tab Item
enum HomeTab: String, CaseIterable {
    case home = "Home"
    case updates = "Updates"
    case research = "Research"
    case tracking = "Tracking"
    case wiser = "Wiser"

    var iconName: String {
        switch self {
        case .home: return "icon_home"
        case .updates: return "icon_updates"
        case .research: return "icon_research"
        case .tracking: return "icon_tracking"
        case .wiser: return "icon_wiser"
        }
    }

    var systemIconName: String {
        switch self {
        case .home: return "house.fill"
        case .updates: return "chart.bar.doc.horizontal"
        case .research: return "magnifyingglass"
        case .tracking: return "star.fill"
        case .wiser: return "lightbulb.fill"
        }
    }
}
