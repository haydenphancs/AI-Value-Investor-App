//
//  PortfolioHoldingModels.swift
//  ios
//
//  Models for portfolio holdings used by the DiversificationCalculator.
//

import Foundation

// MARK: - Asset Type

/// Classification of a portfolio holding for diversification scoring.
enum AssetType: String, Codable, CaseIterable {
    case stock = "Stock"
    case etf = "ETF"
    case bond = "Bond"
    case crypto = "Crypto"
    case cash = "Cash"
    case internationalStock = "International Stock"

    /// Broad asset class for Bucket 3 scoring.
    var assetClass: AssetClass {
        switch self {
        case .stock, .internationalStock: return .equity
        case .etf:                        return .etf
        case .bond:                       return .fixedIncome
        case .crypto:                     return .alternative
        case .cash:                       return .cash
        }
    }
}

// MARK: - Asset Class

/// Broad category for asset class diversity scoring.
enum AssetClass: String, CaseIterable {
    case equity = "Equity"
    case etf = "ETF"
    case fixedIncome = "Fixed Income"
    case alternative = "Alternative"
    case cash = "Cash"
}

// MARK: - Portfolio Holding

/// A single holding in the user's portfolio, enriched with metadata
/// required for diversification scoring.
struct PortfolioHolding: Identifiable, Codable {
    let id: UUID
    let ticker: String
    let companyName: String
    let marketValue: Double
    let sector: String?
    let assetType: AssetType
    let country: String

    /// Portfolio weight as a fraction (0.0–1.0). Computed by the calculator.
    var weight: Double = 0.0

    enum CodingKeys: String, CodingKey {
        case id, ticker
        case companyName = "company_name"
        case marketValue = "market_value"
        case sector
        case assetType = "asset_type"
        case country
    }

    // MARK: - Codable Init (from backend JSON)

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)

        // Backend returns UUID as string; generate one if missing
        if let idStr = try? container.decode(String.self, forKey: .id),
           let parsed = UUID(uuidString: idStr) {
            id = parsed
        } else if let parsed = try? container.decode(UUID.self, forKey: .id) {
            id = parsed
        } else {
            id = UUID()
        }

        ticker = try container.decode(String.self, forKey: .ticker)
        companyName = try container.decode(String.self, forKey: .companyName)
        marketValue = try container.decode(Double.self, forKey: .marketValue)
        sector = try container.decodeIfPresent(String.self, forKey: .sector)

        let assetTypeStr = try container.decodeIfPresent(String.self, forKey: .assetType) ?? "Stock"
        assetType = AssetType(rawValue: assetTypeStr) ?? .stock

        country = try container.decodeIfPresent(String.self, forKey: .country) ?? "US"
        weight = 0.0
    }

    // MARK: - Memberwise Init (existing, unchanged)

    init(
        id: UUID = UUID(),
        ticker: String,
        companyName: String,
        marketValue: Double,
        sector: String? = nil,
        assetType: AssetType = .stock,
        country: String = "US"
    ) {
        self.id = id
        self.ticker = ticker
        self.companyName = companyName
        self.marketValue = marketValue
        self.sector = sector
        self.assetType = assetType
        self.country = country
    }
}

// MARK: - Sample Data

extension PortfolioHolding {
    static let sampleData: [PortfolioHolding] = [
        PortfolioHolding(
            ticker: "AAPL",
            companyName: "Apple Inc.",
            marketValue: 17842.0,
            sector: "Technology",
            assetType: .stock,
            country: "US"
        ),
        PortfolioHolding(
            ticker: "NVDA",
            companyName: "NVIDIA Corp.",
            marketValue: 24761.0,
            sector: "Technology",
            assetType: .stock,
            country: "US"
        ),
        PortfolioHolding(
            ticker: "MSFT",
            companyName: "Microsoft Corp.",
            marketValue: 18945.5,
            sector: "Technology",
            assetType: .stock,
            country: "US"
        ),
        PortfolioHolding(
            ticker: "GOOGL",
            companyName: "Alphabet Inc.",
            marketValue: 13967.0,
            sector: "Communication Services",
            assetType: .stock,
            country: "US"
        ),
        PortfolioHolding(
            ticker: "JNJ",
            companyName: "Johnson & Johnson",
            marketValue: 8000.0,
            sector: "Healthcare",
            assetType: .stock,
            country: "US"
        ),
        PortfolioHolding(
            ticker: "VTI",
            companyName: "Vanguard Total Stock Market ETF",
            marketValue: 12000.0,
            sector: nil,
            assetType: .etf,
            country: "US"
        ),
    ]
}
