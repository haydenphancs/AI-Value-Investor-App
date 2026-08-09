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
    /// Exactly one of the user's portfolios is active, and it is what Home's watchlist
    /// section and the Updates ticker chips follow (migration 126). This used to be a
    /// device-local `UserDefaults` string, which is why those two screens could never
    /// track the group the user picked — and why switching groups on one device did not
    /// follow the user to another.
    var isActive: Bool = false

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
    /// Absent on any backend that predates migration 126.
    ///
    /// ⚠️ The tolerance comes from the hand-written `init(from:)` below, NOT from the
    /// default value. Swift synthesises `decodeIfPresent` **only for Optional
    /// properties**: a non-Optional `var x: T = default` whose name is in `CodingKeys`
    /// still emits a plain `decode(...)` and THROWS `keyNotFound` when the key is
    /// missing. Verified empirically with `swiftc` — the sibling trap already on this
    /// codebase's record (`let x: T = default`, which silently never decodes at all)
    /// invites exactly the wrong conclusion about the `var` form.
    ///
    /// It matters here because `PortfolioDTO` is the element type of
    /// `PortfolioListResponseDTO`, so ONE missing key fails the whole `GET /portfolios`
    /// decode; `loadPortfolios()` swallows that with a `print` and leaves `portfolios`
    /// empty, so the user watches every group vanish with no error shown —
    /// indistinguishable from a total data wipe.
    var isActive: Bool = false

    enum CodingKeys: String, CodingKey {
        case id, name, items
        case sortOrder = "sort_order"
        case isActive = "is_active"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        name = try container.decode(String.self, forKey: .name)
        sortOrder = try container.decode(Int.self, forKey: .sortOrder)
        items = try container.decode([PortfolioItemDTO].self, forKey: .items)
        // The one tolerant field. A pre-126 backend omits it, and `false` is the honest
        // reading — nothing is active — after which `ensureActiveSelection()` picks locally.
        isActive = try container.decodeIfPresent(Bool.self, forKey: .isActive) ?? false
    }

    func toPortfolio() -> Portfolio {
        Portfolio(
            id: id,
            name: name,
            sortOrder: sortOrder,
            items: items.map { $0.toItem() },
            isActive: isActive
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
