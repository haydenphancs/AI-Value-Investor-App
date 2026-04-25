//
//  PortfolioModels.swift
//  ios
//
//  Models for the named-portfolio feature on the Tracking screen. A portfolio
//  is a user-named subset of the master watchlist; tickers in `items` must
//  also exist on `watchlist_items`. Each item carries optional per-portfolio
//  holding values (shares / market_value) that drive the Portfolio Insights
//  diversification score for the active portfolio — independent across
//  portfolios. The server is the source of truth — these types Codable
//  into/out of the /api/v1/portfolios endpoints.
//

import Foundation

// MARK: - View-layer models

/// One ticker inside a portfolio with optional per-portfolio holding values.
/// `shares` and `marketValue` are the iOS Insights config inputs and the
/// numbers that feed the diversification calc — stored per portfolio (not
/// per watchlist row), so the same ticker in two portfolios can carry
/// different positions.
struct PortfolioItem: Identifiable, Equatable {
    var id: String { ticker }
    let ticker: String
    var shares: Double?
    var marketValue: Double?

    /// `true` when the user has filled in either field for this ticker in this
    /// portfolio — the row counts toward the diversification score.
    var isHolding: Bool {
        (shares ?? 0) > 0 || (marketValue ?? 0) > 0
    }
}

struct Portfolio: Identifiable, Equatable {
    let id: String
    var name: String
    var sortOrder: Int
    var items: [PortfolioItem]

    /// Convenience for code that only needs the ticker list (display, alert
    /// filtering, etc.) and doesn't care about per-item holdings.
    var tickers: [String] { items.map(\.ticker) }
}

// MARK: - DTOs

struct PortfolioListResponseDTO: Codable {
    let portfolios: [PortfolioDTO]
}

struct PortfolioItemDTO: Codable {
    let ticker: String
    let shares: Double?
    let marketValue: Double?

    enum CodingKeys: String, CodingKey {
        case ticker, shares
        case marketValue = "market_value"
    }

    func toItem() -> PortfolioItem {
        PortfolioItem(
            ticker: ticker.uppercased(),
            shares: shares,
            marketValue: marketValue
        )
    }
}

struct PortfolioDTO: Codable {
    let id: String
    let name: String
    let sortOrder: Int
    let items: [PortfolioItemDTO]

    enum CodingKeys: String, CodingKey {
        case id, name, items
        case sortOrder = "sort_order"
    }

    func toPortfolio() -> Portfolio {
        Portfolio(
            id: id,
            name: name,
            sortOrder: sortOrder,
            items: items.map { $0.toItem() }
        )
    }
}

// MARK: - Request bodies

nonisolated struct CreatePortfolioRequestBody: Encodable, Sendable {
    let name: String
}

nonisolated struct RenamePortfolioRequestBody: Encodable, Sendable {
    let name: String
}

nonisolated struct SetPortfolioTickersRequestBody: Encodable, Sendable {
    let tickers: [String]
}

nonisolated struct ReorderPortfoliosRequestBody: Encodable, Sendable {
    let portfolioIds: [String]

    enum CodingKeys: String, CodingKey {
        case portfolioIds = "portfolio_ids"
    }
}

/// Wraps `[HoldingUpdateItem]` for the per-portfolio holdings PUT endpoint.
/// The server uses the same item shape it accepts on the watchlist-global
/// /tracking/assets/holdings endpoint, so we reuse `HoldingUpdateItem` for
/// the row payload.
nonisolated struct SetPortfolioHoldingsRequestBody: Encodable, Sendable {
    let items: [HoldingUpdateItem]
}
