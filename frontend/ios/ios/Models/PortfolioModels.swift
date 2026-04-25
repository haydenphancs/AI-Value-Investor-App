//
//  PortfolioModels.swift
//  ios
//
//  Models for the named-portfolio feature on the Tracking screen. A portfolio
//  is a user-named subset of the master watchlist; tickers in `tickers` must
//  also exist on `watchlist_items`. The server is the source of truth — these
//  types Codable into/out of the /api/v1/portfolios endpoints.
//

import Foundation

// MARK: - View-layer model

struct Portfolio: Identifiable, Equatable {
    let id: String
    var name: String
    var sortOrder: Int
    var tickers: [String]
}

// MARK: - DTOs

struct PortfolioListResponseDTO: Codable {
    let portfolios: [PortfolioDTO]
}

struct PortfolioDTO: Codable {
    let id: String
    let name: String
    let sortOrder: Int
    let tickers: [String]

    enum CodingKeys: String, CodingKey {
        case id, name, tickers
        case sortOrder = "sort_order"
    }

    func toPortfolio() -> Portfolio {
        Portfolio(
            id: id,
            name: name,
            sortOrder: sortOrder,
            tickers: tickers.map { $0.uppercased() }
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
