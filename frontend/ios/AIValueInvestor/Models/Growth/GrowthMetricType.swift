import Foundation

/// Represents the different financial growth metrics available for analysis
enum GrowthMetricType: String, CaseIterable, Identifiable {
    case eps = "EPS"
    case revenue = "Revenue"
    case netIncome = "Net Income"
    case operatingProfit = "Operating Profit"
    case freeCashFlow = "Free Cash Flow"

    var id: String { rawValue }

    /// Display name for the metric tab
    var displayName: String {
        rawValue
    }

    /// Short description explaining the metric
    var description: String {
        switch self {
        case .eps:
            return "Earnings Per Share - Net income divided by outstanding shares"
        case .revenue:
            return "Total income generated from business operations"
        case .netIncome:
            return "Total profit after all expenses and taxes"
        case .operatingProfit:
            return "Profit from core business operations before interest and taxes"
        case .freeCashFlow:
            return "Cash generated after capital expenditures"
        }
    }

    /// Unit suffix for formatting values
    var unitSuffix: String {
        switch self {
        case .eps:
            return ""
        case .revenue, .netIncome, .operatingProfit, .freeCashFlow:
            return "B" // Billions
        }
    }

    /// Whether this metric typically shows in billions
    var showsInBillions: Bool {
        switch self {
        case .eps:
            return false
        default:
            return true
        }
    }
}
