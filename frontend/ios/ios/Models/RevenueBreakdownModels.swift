//
//  RevenueBreakdownModels.swift
//  ios
//
//  Data models for the "How [TICKER] Makes Money" section in the Financial tab
//

import Foundation
import SwiftUI

// MARK: - Revenue Source
struct RevenueSource: Identifiable {
    let id = UUID()
    let name: String
    let value: Double
    let color: Color

    var percentage: Double {
        0 // Will be calculated in context of total revenue
    }

    func percentage(of total: Double) -> Double {
        guard total > 0 else { return 0 }
        return (value / total) * 100
    }

    var formattedValue: String {
        formatLargeNumber(value)
    }

    func formattedPercentage(of total: Double) -> String {
        String(format: "%.0f%%", percentage(of: total))
    }

    private func formatLargeNumber(_ number: Double) -> String {
        let absNumber = abs(number)
        if absNumber >= 1_000_000_000_000 {
            return String(format: "%.1fT", number / 1_000_000_000_000)
        } else if absNumber >= 1_000_000_000 {
            return String(format: "%.2fB", number / 1_000_000_000)
        } else if absNumber >= 1_000_000 {
            return String(format: "%.1fM", number / 1_000_000)
        } else if absNumber >= 1_000 {
            return String(format: "%.1fK", number / 1_000)
        }
        return String(format: "%.0f", number)
    }
}

// MARK: - Cost Item
struct CostItem: Identifiable {
    let id = UUID()
    let name: String
    let value: Double
    let color: Color

    func percentage(of total: Double) -> Double {
        guard total > 0 else { return 0 }
        return (abs(value) / total) * 100
    }

    var formattedValue: String {
        formatLargeNumber(value)
    }

    func formattedPercentage(of total: Double) -> String {
        String(format: "%.0f%%", percentage(of: total))
    }

    private func formatLargeNumber(_ number: Double) -> String {
        let absNumber = abs(number)
        if absNumber >= 1_000_000_000_000 {
            return String(format: "%.1fT", number / 1_000_000_000_000)
        } else if absNumber >= 1_000_000_000 {
            return String(format: "%.0fB", number / 1_000_000_000)
        } else if absNumber >= 1_000_000 {
            return String(format: "%.1fM", number / 1_000_000)
        } else if absNumber >= 1_000 {
            return String(format: "%.1fK", number / 1_000)
        }
        return String(format: "%.0f", number)
    }
}

// MARK: - Revenue Breakdown Data
struct RevenueBreakdownData {
    let tickerSymbol: String
    let revenueSources: [RevenueSource]
    let costOfSales: Double
    let operatingExpense: Double
    let tax: Double

    // MARK: - Computed Properties

    var totalRevenue: Double {
        revenueSources.reduce(0) { $0 + $1.value }
    }

    var totalCosts: Double {
        costOfSales + operatingExpense + tax
    }

    var netProfit: Double {
        totalRevenue - totalCosts
    }

    var isProfit: Bool {
        netProfit >= 0
    }

    var netProfitLabel: String {
        isProfit ? "Net Profit" : "Net Loss"
    }

    var netProfitColor: Color {
        isProfit ? AppColors.bullish : Color(hex: "8B0000") // Dark red for loss
    }

    // Cost items for display
    var costItems: [CostItem] {
        [
            CostItem(name: "Cost of Sales", value: costOfSales, color: Color(hex: "EF4444")),
            CostItem(name: "Op. Expense", value: operatingExpense, color: Color(hex: "F87171")),
            CostItem(name: "Tax", value: tax, color: Color(hex: "FCA5A5"))
        ]
    }

    // Net profit/loss as a cost item (for legend)
    var netProfitItem: CostItem {
        CostItem(
            name: netProfitLabel,
            value: netProfit,
            color: netProfitColor
        )
    }

    // Formatted values
    var formattedTotalRevenue: String {
        formatLargeNumber(totalRevenue)
    }

    var formattedNetProfit: String {
        let prefix = isProfit ? "" : "-"
        return prefix + formatLargeNumber(abs(netProfit))
    }

    func netProfitPercentage() -> Double {
        guard totalRevenue > 0 else { return 0 }
        return (abs(netProfit) / totalRevenue) * 100
    }

    // MARK: - Chart Calculations

    /// Maximum value for chart scaling (total revenue)
    var chartMaxValue: Double {
        totalRevenue * 1.1
    }

    /// Calculate cumulative position for waterfall chart
    func waterfallPosition(for index: Int) -> (start: Double, end: Double) {
        var currentPosition = totalRevenue

        for i in 0..<index {
            currentPosition -= costItems[i].value
        }

        let start = currentPosition
        let end = currentPosition - costItems[index].value

        return (start, end)
    }

    // MARK: - Private Helpers

    private func formatLargeNumber(_ number: Double) -> String {
        let absNumber = abs(number)
        if absNumber >= 1_000_000_000_000 {
            return String(format: "%.1fT", number / 1_000_000_000_000)
        } else if absNumber >= 1_000_000_000 {
            return String(format: "%.0fB", number / 1_000_000_000)
        } else if absNumber >= 1_000_000 {
            return String(format: "%.1fM", number / 1_000_000)
        } else if absNumber >= 1_000 {
            return String(format: "%.1fK", number / 1_000)
        }
        return String(format: "%.0f", number)
    }
}

// MARK: - Revenue Source Colors
extension RevenueSource {
    static let iPhoneColor = Color(hex: "3B82F6")      // Blue
    static let servicesColor = Color(hex: "A855F7")    // Purple
    static let macColor = Color(hex: "F97316")         // Orange
    static let iPadColor = Color(hex: "06B6D4")        // Cyan
    static let otherColor = Color(hex: "9CA3AF")       // Gray
    static let wearablesColor = Color(hex: "22C55E")   // Green
}

// MARK: - Sample Data
extension RevenueBreakdownData {
    // Apple - Profitable company
    static let sampleApple = RevenueBreakdownData(
        tickerSymbol: "AAPL",
        revenueSources: [
            RevenueSource(name: "iPhone", value: 205_500_000_000, color: RevenueSource.iPhoneColor),
            RevenueSource(name: "Services", value: 73_100_000_000, color: RevenueSource.servicesColor),
            RevenueSource(name: "Mac", value: 32_200_000_000, color: RevenueSource.macColor),
            RevenueSource(name: "iPad", value: 25_100_000_000, color: RevenueSource.iPadColor),
            RevenueSource(name: "Other", value: 20_450_000_000, color: RevenueSource.otherColor)
        ],
        costOfSales: 192_000_000_000,
        operatingExpense: 91_000_000_000,
        tax: 5_000_000_000
    )

    // Example of a company with net loss
    static let sampleLossCompany = RevenueBreakdownData(
        tickerSymbol: "RIVN",
        revenueSources: [
            RevenueSource(name: "Vehicles", value: 4_400_000_000, color: RevenueSource.iPhoneColor),
            RevenueSource(name: "Services", value: 300_000_000, color: RevenueSource.servicesColor),
            RevenueSource(name: "Other", value: 100_000_000, color: RevenueSource.otherColor)
        ],
        costOfSales: 6_500_000_000,
        operatingExpense: 3_200_000_000,
        tax: 50_000_000
    )

    // Microsoft
    static let sampleMicrosoft = RevenueBreakdownData(
        tickerSymbol: "MSFT",
        revenueSources: [
            RevenueSource(name: "Cloud", value: 110_000_000_000, color: RevenueSource.iPhoneColor),
            RevenueSource(name: "Office", value: 48_000_000_000, color: RevenueSource.servicesColor),
            RevenueSource(name: "Windows", value: 22_000_000_000, color: RevenueSource.macColor),
            RevenueSource(name: "Gaming", value: 16_000_000_000, color: RevenueSource.iPadColor),
            RevenueSource(name: "LinkedIn", value: 15_000_000_000, color: RevenueSource.wearablesColor),
            RevenueSource(name: "Other", value: 14_000_000_000, color: RevenueSource.otherColor)
        ],
        costOfSales: 72_000_000_000,
        operatingExpense: 63_000_000_000,
        tax: 16_000_000_000
    )
}

// MARK: - Info Items for Educational Content
struct RevenueBreakdownInfoItem: Identifiable {
    let id = UUID()
    let title: String
    let description: String
    let icon: String
}

extension RevenueBreakdownInfoItem {
    static let educationalContent: [RevenueBreakdownInfoItem] = [
        RevenueBreakdownInfoItem(
            title: "Revenue Diversification",
            description: "Companies with multiple revenue streams are generally more stable. A single dominant source (>70%) can indicate concentration risk.",
            icon: "chart.pie.fill"
        ),
        RevenueBreakdownInfoItem(
            title: "Gross Margin",
            description: "Revenue minus Cost of Sales shows gross profit. Higher margins indicate pricing power or operational efficiency.",
            icon: "arrow.up.right.circle.fill"
        ),
        RevenueBreakdownInfoItem(
            title: "Operating Expenses",
            description: "Includes R&D, sales, marketing, and administrative costs. Watch for expenses growing faster than revenue.",
            icon: "building.2.fill"
        ),
        RevenueBreakdownInfoItem(
            title: "Net Profit Margin",
            description: "Net profit as a percentage of revenue. Compare to industry peers - tech companies often have 15-25% margins.",
            icon: "percent"
        ),
        RevenueBreakdownInfoItem(
            title: "Revenue Quality",
            description: "Recurring revenue (subscriptions, services) is more valuable than one-time sales. Look for growing services segments.",
            icon: "repeat.circle.fill"
        )
    ]
}
