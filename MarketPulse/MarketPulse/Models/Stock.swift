import Foundation
import Combine

// MARK: - Stock Models

struct Stock: Codable, Identifiable, Equatable {
    let id: String
    let ticker: String
    let companyName: String
    let exchange: String?
    let sector: String?
    let industry: String?
    let marketCap: Decimal?
    let description: String?
    let website: String?
    let logoUrl: String?
    let createdAt: Date?
    let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, ticker, exchange, sector, industry, description, website
        case companyName = "company_name"
        case marketCap = "market_cap"
        case logoUrl = "logo_url"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    var formattedMarketCap: String? {
        guard let marketCap = marketCap else { return nil }
        let value = NSDecimalNumber(decimal: marketCap).doubleValue

        if value >= 1_000_000_000_000 {
            return String(format: "$%.2fT", value / 1_000_000_000_000)
        } else if value >= 1_000_000_000 {
            return String(format: "$%.2fB", value / 1_000_000_000)
        } else if value >= 1_000_000 {
            return String(format: "$%.2fM", value / 1_000_000)
        }
        return String(format: "$%.0f", value)
    }
}

struct StockSearchResult: Codable, Identifiable {
    let id: String
    let ticker: String
    let companyName: String
    let sector: String?
    let industry: String?
    let marketCap: Decimal?
    let logoUrl: String?
    let exchange: String?

    enum CodingKeys: String, CodingKey {
        case id, ticker, sector, industry, exchange
        case companyName = "company_name"
        case marketCap = "market_cap"
        case logoUrl = "logo_url"
    }
}

// MARK: - Fundamentals

struct Fundamental: Codable, Identifiable {
    let id: String
    let stockId: String
    let fiscalYear: Int
    let fiscalQuarter: Int?
    let periodType: String
    let revenue: Decimal?
    let netIncome: Decimal?
    let eps: Decimal?
    let operatingIncome: Decimal?
    let grossProfit: Decimal?
    let totalAssets: Decimal?
    let totalDebt: Decimal?
    let freeCashFlow: Decimal?
    let reportedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, revenue, eps
        case stockId = "stock_id"
        case fiscalYear = "fiscal_year"
        case fiscalQuarter = "fiscal_quarter"
        case periodType = "period_type"
        case netIncome = "net_income"
        case operatingIncome = "operating_income"
        case grossProfit = "gross_profit"
        case totalAssets = "total_assets"
        case totalDebt = "total_debt"
        case freeCashFlow = "free_cash_flow"
        case reportedAt = "reported_at"
    }
}

// MARK: - Earnings

struct Earnings: Codable, Identifiable {
    let id: String
    let stockId: String
    let earningsDate: Date?
    let fiscalYear: Int?
    let fiscalQuarter: Int?
    let epsEstimate: Decimal?
    let revenueEstimate: Decimal?
    let epsActual: Decimal?
    let revenueActual: Decimal?

    enum CodingKeys: String, CodingKey {
        case id
        case stockId = "stock_id"
        case earningsDate = "earnings_date"
        case fiscalYear = "fiscal_year"
        case fiscalQuarter = "fiscal_quarter"
        case epsEstimate = "eps_estimate"
        case revenueEstimate = "revenue_estimate"
        case epsActual = "eps_actual"
        case revenueActual = "revenue_actual"
    }
}

// MARK: - Watchlist

struct WatchlistItem: Codable, Identifiable {
    let id: String
    let userId: String
    let stockId: String
    let stock: Stock
    let alertOnNews: Bool
    let customNotes: String?
    let hasBreakingNews: Bool
    let addedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, stock
        case userId = "user_id"
        case stockId = "stock_id"
        case alertOnNews = "alert_on_news"
        case customNotes = "custom_notes"
        case hasBreakingNews = "has_breaking_news"
        case addedAt = "added_at"
    }
}

struct WatchlistCreate: Codable {
    let stockId: String
    let alertOnNews: Bool
    let customNotes: String?

    enum CodingKeys: String, CodingKey {
        case stockId = "stock_id"
        case alertOnNews = "alert_on_news"
        case customNotes = "custom_notes"
    }
}
