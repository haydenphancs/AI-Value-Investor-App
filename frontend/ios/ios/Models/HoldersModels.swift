//
//  HoldersModels.swift
//  ios
//
//  Data models for the Holders tab in Ticker Detail
//  Includes shareholder breakdown and smart money flow data
//

import Foundation
import SwiftUI

// MARK: - Shareholder Breakdown

/// Represents the ownership distribution of a company
struct ShareholderBreakdown: Identifiable {
    let id = UUID()
    let insidersPercent: Double
    let institutionsPercent: Double
    let publicOtherPercent: Double

    /// Top 10 institutional holders data (legacy)
    let topHolders: [InstitutionalHolder]

    /// Top 10 owners data (institutions and insiders)
    let top10Owners: Top10OwnersData

    // Computed property for validation
    var totalPercent: Double {
        insidersPercent + institutionsPercent + publicOtherPercent
    }

    // Formatted strings
    var formattedInsiders: String {
        String(format: "%.0f%%", insidersPercent)
    }

    var formattedInstitutions: String {
        String(format: "%.0f%%", institutionsPercent)
    }

    var formattedPublicOther: String {
        String(format: "%.0f%%", publicOtherPercent)
    }
}

/// Individual institutional holder
struct InstitutionalHolder: Identifiable {
    let id = UUID()
    let name: String
    let sharesHeld: Double
    let percentOwnership: Double
    let changePercent: Double?

    var formattedShares: String {
        if sharesHeld >= 1_000_000_000 {
            return String(format: "%.2fB", sharesHeld / 1_000_000_000)
        } else if sharesHeld >= 1_000_000 {
            return String(format: "%.2fM", sharesHeld / 1_000_000)
        }
        return String(format: "%.0f", sharesHeld)
    }

    var formattedPercent: String {
        String(format: "%.2f%%", percentOwnership)
    }

    var formattedChange: String? {
        guard let change = changePercent else { return nil }
        let sign = change >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", change))%"
    }

    var changeColor: Color {
        guard let change = changePercent else { return AppColors.textSecondary }
        return change >= 0 ? AppColors.bullish : AppColors.bearish
    }
}

// MARK: - Smart Money Tab Types

enum SmartMoneyTab: String, CaseIterable {
    case insider = "Insider"
    case hedgeFunds = "Hedge Funds"
    case congress = "Congress"
}

// MARK: - Stock Price Data Point

/// Stock price data for a specific month (for comparison with smart money activity)
struct StockPriceDataPoint: Identifiable {
    let id = UUID()
    let month: String
    let price: Double  // Closing price for the month

    var formattedPrice: String {
        String(format: "$%.2f", price)
    }
}

// MARK: - Smart Money Flow Data

/// Monthly smart money flow data point
struct SmartMoneyFlowDataPoint: Identifiable {
    let id = UUID()
    let month: String
    let buyVolume: Double   // In millions
    let sellVolume: Double  // In millions

    var netFlow: Double {
        buyVolume - sellVolume
    }

    var isPositiveNet: Bool {
        netFlow >= 0
    }

    var formattedNetFlow: String {
        let sign = netFlow >= 0 ? "+" : ""
        if abs(netFlow) >= 1000 {
            return "\(sign)\(String(format: "%.1f", netFlow / 1000))B"
        }
        return "\(sign)\(String(format: "%.1f", netFlow))M"
    }
}

/// Summary of smart money activity
struct SmartMoneyFlowSummary {
    let totalNetFlow: Double  // Total net in millions
    let isPositive: Bool
    let periodDescription: String  // e.g., "12-Month"

    var formattedNetFlow: String {
        let sign = totalNetFlow >= 0 ? "+" : ""
        if abs(totalNetFlow) >= 1000 {
            return "$\(String(format: "%.2f", abs(totalNetFlow) / 1000))B"
        }
        return "$\(String(format: "%.2f", abs(totalNetFlow)))M"
    }

    var flowColor: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    var flowIcon: String {
        isPositive ? "arrow.up" : "arrow.down"
    }
}

/// Complete smart money data for a specific tab (Insider/Hedge Funds/Congress)
struct SmartMoneyData: Identifiable {
    let id = UUID()
    let tab: SmartMoneyTab
    let priceData: [StockPriceDataPoint]  // Stock price for comparison
    let flowData: [SmartMoneyFlowDataPoint]
    let summary: SmartMoneyFlowSummary
}

// MARK: - Combined Holders Data

struct HoldersData {
    let shareholderBreakdown: ShareholderBreakdown
    let insiderData: SmartMoneyData
    let hedgeFundsData: SmartMoneyData
    let congressData: SmartMoneyData

    func smartMoneyData(for tab: SmartMoneyTab) -> SmartMoneyData {
        switch tab {
        case .insider:
            return insiderData
        case .hedgeFunds:
            return hedgeFundsData
        case .congress:
            return congressData
        }
    }
}

// MARK: - Sample Data

extension ShareholderBreakdown {
    static let sampleData = ShareholderBreakdown(
        insidersPercent: 12,
        institutionsPercent: 55,
        publicOtherPercent: 33,
        topHolders: InstitutionalHolder.sampleData,
        top10Owners: Top10OwnersData.sampleData
    )
}

extension InstitutionalHolder {
    static let sampleData: [InstitutionalHolder] = [
        InstitutionalHolder(name: "The Vanguard Group", sharesHeld: 1_340_000_000, percentOwnership: 8.61, changePercent: 0.3),
        InstitutionalHolder(name: "BlackRock Inc.", sharesHeld: 1_080_000_000, percentOwnership: 6.94, changePercent: -0.2),
        InstitutionalHolder(name: "Berkshire Hathaway", sharesHeld: 905_000_000, percentOwnership: 5.82, changePercent: 0.0),
        InstitutionalHolder(name: "State Street Corp", sharesHeld: 625_000_000, percentOwnership: 4.02, changePercent: 0.5),
        InstitutionalHolder(name: "FMR LLC", sharesHeld: 420_000_000, percentOwnership: 2.70, changePercent: -0.8),
        InstitutionalHolder(name: "Geode Capital Management", sharesHeld: 310_000_000, percentOwnership: 1.99, changePercent: 0.1),
        InstitutionalHolder(name: "Morgan Stanley", sharesHeld: 285_000_000, percentOwnership: 1.83, changePercent: 1.2),
        InstitutionalHolder(name: "Northern Trust Corp", sharesHeld: 245_000_000, percentOwnership: 1.57, changePercent: -0.3),
        InstitutionalHolder(name: "Bank of America Corp", sharesHeld: 198_000_000, percentOwnership: 1.27, changePercent: 0.4),
        InstitutionalHolder(name: "JP Morgan Chase", sharesHeld: 175_000_000, percentOwnership: 1.12, changePercent: 0.6)
    ]
}

extension StockPriceDataPoint {
    /// Sample stock price data (AAPL-like prices for 12 months)
    static let sampleData: [StockPriceDataPoint] = [
        StockPriceDataPoint(month: "02/2025", price: 155.30),
        StockPriceDataPoint(month: "03/2025", price: 158.20),
        StockPriceDataPoint(month: "04/2025", price: 162.40),
        StockPriceDataPoint(month: "05/2025", price: 165.10),
        StockPriceDataPoint(month: "06/2025", price: 168.50),
        StockPriceDataPoint(month: "07/2025", price: 170.80),
        StockPriceDataPoint(month: "08/2025", price: 171.20),
        StockPriceDataPoint(month: "09/2025", price: 168.90),
        StockPriceDataPoint(month: "10/2025", price: 165.80),
        StockPriceDataPoint(month: "11/2025", price: 170.50),
        StockPriceDataPoint(month: "12/2025", price: 175.20),
        StockPriceDataPoint(month: "01/2026", price: 178.42)
    ]
}

extension SmartMoneyFlowDataPoint {
    static let insiderSampleData: [SmartMoneyFlowDataPoint] = [
        SmartMoneyFlowDataPoint(month: "02/2025", buyVolume: 10.2, sellVolume: 6.5),
        SmartMoneyFlowDataPoint(month: "03/2025", buyVolume: 7.8, sellVolume: 9.2),
        SmartMoneyFlowDataPoint(month: "04/2025", buyVolume: 8.5, sellVolume: 12.3),
        SmartMoneyFlowDataPoint(month: "05/2025", buyVolume: 11.2, sellVolume: 5.8),
        SmartMoneyFlowDataPoint(month: "06/2025", buyVolume: 12.5, sellVolume: 8.2),
        SmartMoneyFlowDataPoint(month: "07/2025", buyVolume: 6.9, sellVolume: 10.5),
        SmartMoneyFlowDataPoint(month: "08/2025", buyVolume: 8.3, sellVolume: 11.1),
        SmartMoneyFlowDataPoint(month: "09/2025", buyVolume: 9.5, sellVolume: 7.2),
        SmartMoneyFlowDataPoint(month: "10/2025", buyVolume: 14.7, sellVolume: 7.5),
        SmartMoneyFlowDataPoint(month: "11/2025", buyVolume: 10.8, sellVolume: 8.9),
        SmartMoneyFlowDataPoint(month: "12/2025", buyVolume: 13.2, sellVolume: 6.1),
        SmartMoneyFlowDataPoint(month: "01/2026", buyVolume: 16.2, sellVolume: 7.9)
    ]

    static let hedgeFundsSampleData: [SmartMoneyFlowDataPoint] = [
        SmartMoneyFlowDataPoint(month: "02/2025", buyVolume: 42.1, sellVolume: 35.2),
        SmartMoneyFlowDataPoint(month: "03/2025", buyVolume: 38.5, sellVolume: 42.1),
        SmartMoneyFlowDataPoint(month: "04/2025", buyVolume: 35.2, sellVolume: 48.3),
        SmartMoneyFlowDataPoint(month: "05/2025", buyVolume: 48.9, sellVolume: 32.5),
        SmartMoneyFlowDataPoint(month: "06/2025", buyVolume: 45.2, sellVolume: 38.5),
        SmartMoneyFlowDataPoint(month: "07/2025", buyVolume: 39.8, sellVolume: 45.2),
        SmartMoneyFlowDataPoint(month: "08/2025", buyVolume: 52.1, sellVolume: 41.3),
        SmartMoneyFlowDataPoint(month: "09/2025", buyVolume: 44.5, sellVolume: 38.9),
        SmartMoneyFlowDataPoint(month: "10/2025", buyVolume: 38.9, sellVolume: 55.2),
        SmartMoneyFlowDataPoint(month: "11/2025", buyVolume: 51.2, sellVolume: 36.8),
        SmartMoneyFlowDataPoint(month: "12/2025", buyVolume: 48.5, sellVolume: 33.2),
        SmartMoneyFlowDataPoint(month: "01/2026", buyVolume: 55.8, sellVolume: 31.2)
    ]

    static let congressSampleData: [SmartMoneyFlowDataPoint] = [
        SmartMoneyFlowDataPoint(month: "02/2025", buyVolume: 1.8, sellVolume: 1.2),
        SmartMoneyFlowDataPoint(month: "03/2025", buyVolume: 2.1, sellVolume: 1.8),
        SmartMoneyFlowDataPoint(month: "04/2025", buyVolume: 2.5, sellVolume: 3.1),
        SmartMoneyFlowDataPoint(month: "05/2025", buyVolume: 3.2, sellVolume: 1.5),
        SmartMoneyFlowDataPoint(month: "06/2025", buyVolume: 2.1, sellVolume: 1.5),
        SmartMoneyFlowDataPoint(month: "07/2025", buyVolume: 1.5, sellVolume: 2.8),
        SmartMoneyFlowDataPoint(month: "08/2025", buyVolume: 3.2, sellVolume: 0.8),
        SmartMoneyFlowDataPoint(month: "09/2025", buyVolume: 2.8, sellVolume: 1.9),
        SmartMoneyFlowDataPoint(month: "10/2025", buyVolume: 1.8, sellVolume: 2.9),
        SmartMoneyFlowDataPoint(month: "11/2025", buyVolume: 3.5, sellVolume: 1.2),
        SmartMoneyFlowDataPoint(month: "12/2025", buyVolume: 2.9, sellVolume: 2.1),
        SmartMoneyFlowDataPoint(month: "01/2026", buyVolume: 3.9, sellVolume: 1.4)
    ]
}

extension SmartMoneyData {
    static let insiderSampleData = SmartMoneyData(
        tab: .insider,
        priceData: StockPriceDataPoint.sampleData,
        flowData: SmartMoneyFlowDataPoint.insiderSampleData,
        summary: SmartMoneyFlowSummary(
            totalNetFlow: 8.27,
            isPositive: true,
            periodDescription: "12-Month"
        )
    )

    static let hedgeFundsSampleData = SmartMoneyData(
        tab: .hedgeFunds,
        priceData: StockPriceDataPoint.sampleData,
        flowData: SmartMoneyFlowDataPoint.hedgeFundsSampleData,
        summary: SmartMoneyFlowSummary(
            totalNetFlow: 57.7,
            isPositive: true,
            periodDescription: "12-Month"
        )
    )

    static let congressSampleData = SmartMoneyData(
        tab: .congress,
        priceData: StockPriceDataPoint.sampleData,
        flowData: SmartMoneyFlowDataPoint.congressSampleData,
        summary: SmartMoneyFlowSummary(
            totalNetFlow: 7.4,
            isPositive: true,
            periodDescription: "12-Month"
        )
    )
}

extension HoldersData {
    static let sampleData = HoldersData(
        shareholderBreakdown: ShareholderBreakdown.sampleData,
        insiderData: SmartMoneyData.insiderSampleData,
        hedgeFundsData: SmartMoneyData.hedgeFundsSampleData,
        congressData: SmartMoneyData.congressSampleData
    )
}

// MARK: - Holders Colors

/// Centralized colors for the Holders tab
struct HoldersColors {
    // Shareholder breakdown colors
    static let insiders = Color(hex: "F59E0B")       // Orange/Amber
    static let institutions = AppColors.primaryBlue  // Blue
    static let publicOther = Color(hex: "6B7280")    // Gray

    // Smart money flow colors
    static let buyVolume = AppColors.bullish         // Green
    static let sellVolume = AppColors.bearish        // Red
    static let flowLine = AppColors.primaryBlue      // Blue for cumulative flow line
}

// MARK: - Top 10 Owner Tab

enum Top10OwnerTab: String, CaseIterable {
    case institutions = "Institutions"
    case insiders = "Insiders"
}

// MARK: - Top Institution Owner

/// Represents a top institutional owner
struct TopInstitution: Identifiable {
    let id = UUID()
    let rank: Int
    let name: String
    let category: String  // e.g., "Asset Management", "Investment Banking"
    let valueInBillions: Double
    let percentOwnership: Double

    var formattedValue: String {
        if valueInBillions >= 1 {
            return String(format: "$%.1fB", valueInBillions)
        } else {
            return String(format: "$%.0fM", valueInBillions * 1000)
        }
    }

    var formattedPercent: String {
        String(format: "%.1f%%", percentOwnership)
    }
}

// MARK: - Top Insider Owner

/// Represents a top insider owner
struct TopInsider: Identifiable {
    let id = UUID()
    let rank: Int
    let name: String
    let title: String  // e.g., "CEO", "CFO", "Director"
    let valueInMillions: Double
    let percentOwnership: Double

    var formattedValue: String {
        if valueInMillions >= 1000 {
            return String(format: "$%.1fB", valueInMillions / 1000)
        } else {
            return String(format: "$%.1fM", valueInMillions)
        }
    }

    var formattedPercent: String {
        String(format: "%.2f%%", percentOwnership)
    }
}

// MARK: - Top 10 Owners Data

struct Top10OwnersData {
    let institutions: [TopInstitution]
    let insiders: [TopInsider]
}

// MARK: - Top 10 Sample Data

extension TopInstitution {
    static let sampleData: [TopInstitution] = [
        TopInstitution(rank: 1, name: "Vanguard Group Inc", category: "Asset Management", valueInBillions: 14.5, percentOwnership: 5.2),
        TopInstitution(rank: 2, name: "BlackRock Fund Advisors", category: "Investment Management", valueInBillions: 12.8, percentOwnership: 4.6),
        TopInstitution(rank: 3, name: "State Street Corporation", category: "Financial Services", valueInBillions: 9.2, percentOwnership: 3.3),
        TopInstitution(rank: 4, name: "Fidelity Management", category: "Mutual Funds", valueInBillions: 8.7, percentOwnership: 3.1),
        TopInstitution(rank: 5, name: "Geode Capital Management", category: "Investment Advisor", valueInBillions: 6.4, percentOwnership: 2.3),
        TopInstitution(rank: 6, name: "Northern Trust Corporation", category: "Wealth Management", valueInBillions: 5.9, percentOwnership: 2.1),
        TopInstitution(rank: 7, name: "Morgan Stanley", category: "Investment Banking", valueInBillions: 5.1, percentOwnership: 1.8),
        TopInstitution(rank: 8, name: "JPMorgan Chase & Co", category: "Commercial Banking", valueInBillions: 4.7, percentOwnership: 1.7),
        TopInstitution(rank: 9, name: "Bank of America Corporation", category: "Financial Services", valueInBillions: 4.3, percentOwnership: 1.5),
        TopInstitution(rank: 10, name: "Goldman Sachs Group Inc", category: "Investment Banking", valueInBillions: 3.8, percentOwnership: 1.4)
    ]
}

extension TopInsider {
    static let sampleData: [TopInsider] = [
        TopInsider(rank: 1, name: "Tim Cook", title: "Chief Executive Officer", valueInMillions: 1850.5, percentOwnership: 0.66),
        TopInsider(rank: 2, name: "Arthur D. Levinson", title: "Chairman of the Board", valueInMillions: 892.3, percentOwnership: 0.32),
        TopInsider(rank: 3, name: "Jeff Williams", title: "Chief Operating Officer", valueInMillions: 645.8, percentOwnership: 0.23),
        TopInsider(rank: 4, name: "Luca Maestri", title: "Chief Financial Officer", valueInMillions: 421.2, percentOwnership: 0.15),
        TopInsider(rank: 5, name: "Katherine Adams", title: "General Counsel & SVP", valueInMillions: 312.5, percentOwnership: 0.11),
        TopInsider(rank: 6, name: "Deirdre O'Brien", title: "SVP Retail + People", valueInMillions: 285.4, percentOwnership: 0.10),
        TopInsider(rank: 7, name: "Craig Federighi", title: "SVP Software Engineering", valueInMillions: 268.9, percentOwnership: 0.10),
        TopInsider(rank: 8, name: "John Ternus", title: "SVP Hardware Engineering", valueInMillions: 245.1, percentOwnership: 0.09),
        TopInsider(rank: 9, name: "Greg Joswiak", title: "SVP Worldwide Marketing", valueInMillions: 198.7, percentOwnership: 0.07),
        TopInsider(rank: 10, name: "James A. Bell", title: "Independent Director", valueInMillions: 156.2, percentOwnership: 0.06)
    ]
}

extension Top10OwnersData {
    static let sampleData = Top10OwnersData(
        institutions: TopInstitution.sampleData,
        insiders: TopInsider.sampleData
    )
}
