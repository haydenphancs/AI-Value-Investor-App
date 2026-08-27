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
        // Same formatter as the cost column, so a tiny segment says "<0.1%" instead of
        // claiming "0%" beside a non-zero amount.
        PercentShare.string(percentage(of: total))
    }

    private func formatLargeNumber(_ number: Double) -> String {
        // Shared formatter. This copy was itself inconsistent — `%.1fT`, `%.0fB`,
        // `%.1fM`, `%.1fK`: a decimal everywhere EXCEPT billions, which is the tier most
        // large caps land in. It and the chart axis are on the same screen and disagreed.
        CompactNumberFormat.string(number)
    }
}

// MARK: - Cost Item
struct CostItem: Identifiable {
    let id = UUID()
    let name: String
    let value: Double
    let color: Color
    /// True when the underlying line came back NEGATIVE and has been flipped into a credit.
    ///
    /// A negative "cost" is not an expense, it is income — FMP reports LMT's
    /// `operatingExpenses` as −112M (SG&A 50M + R&D 2.0B + other income −2.16B) and Ford's
    /// `incomeTaxExpense` as −3.67B (a tax benefit). The legend used to print the signed
    /// value, compute its percentage with `abs()`, and let the bar clamp it to zero — three
    /// different readings of one number in one row, which is what got reported. Credits now
    /// carry a positive magnitude, an income colour and their own label; see
    /// `RevenueBreakdownData.costLine`.
    let isCredit: Bool
    /// Fill for the stacked bar. Deliberately separate from `color`: the legend dot is read
    /// as meaningful content and takes a TEXT-role token (4.5:1), while a chart fill takes
    /// the 3:1 `*Graphic` variant. Keeping both on the item is what lets the bar iterate
    /// these items without a text token leaking into the chart layer.
    let chartColor: Color

    init(name: String, value: Double, color: Color, chartColor: Color? = nil, isCredit: Bool = false) {
        self.name = name
        self.value = value
        self.color = color
        self.chartColor = chartColor ?? color
        self.isCredit = isCredit
    }

    /// Share of revenue, **signed**. The `abs()` that used to live here silently reported a
    /// credit as a positive share of revenue.
    func percentage(of total: Double) -> Double {
        guard total > 0 else { return 0 }
        let pct = (value / total) * 100
        // Cap to prevent display overflow in edge cases.
        return min(max(pct, -999), 999)
    }

    var formattedValue: String {
        CompactNumberFormat.string(value)
    }

    func formattedPercentage(of total: Double) -> String {
        PercentShare.string(percentage(of: total))
    }
}

// MARK: - Percentage formatting

/// Share-of-revenue formatting for this card.
///
/// ⚠️ `String(format: "%.0f%%", …)` was the whole of the old implementation, and it printed
/// **"0%" next to a non-zero amount** — LMT's −112M is 0.15% of revenue, which floors to
/// zero. A percentage that contradicts the number beside it is worse than no percentage, and
/// a tester reported exactly that. Below 1% the value keeps a decimal; below 0.1% it says so
/// rather than claiming zero. At 1% and above the output is byte-identical to before, so the
/// rest of the card looks untouched.
enum PercentShare {
    static func string(_ pct: Double) -> String {
        guard pct.isFinite else { return "—" }
        let magnitude = abs(pct)
        if magnitude < 0.05 {
            // Genuinely zero reads as "0%"; a rounding artefact must not.
            return magnitude == 0 ? "0%" : (pct < 0 ? "<-0.1%" : "<0.1%")
        }
        if magnitude < 1 {
            return String(format: "%.1f%%", pct)
        }
        return String(format: "%.0f%%", pct)
    }
}

// MARK: - Revenue Breakdown Data
struct RevenueBreakdownData {
    let tickerSymbol: String
    let fiscalYear: String  // e.g. "2024" — which fiscal year this data represents
    let revenueSources: [RevenueSource]
    let costOfSales: Double
    let operatingExpense: Double
    let tax: Double

    // ── Server-supplied composition. nil ⇒ pre-fix backend or a stale cached row. ──
    /// FMP `netIncome`. See `netProfit`.
    let reportedNetIncome: Double?
    /// FMP `revenue`. See `revenueBasis`.
    let reportedRevenue: Double?
    /// Interest, non-operating items, minority interest, discontinued ops.
    let otherExpense: Double?

    init(tickerSymbol: String,
         fiscalYear: String,
         revenueSources: [RevenueSource],
         costOfSales: Double,
         operatingExpense: Double,
         tax: Double,
         reportedNetIncome: Double? = nil,
         reportedRevenue: Double? = nil,
         otherExpense: Double? = nil) {
        self.tickerSymbol = tickerSymbol
        self.fiscalYear = fiscalYear
        self.revenueSources = revenueSources
        self.costOfSales = costOfSales
        self.operatingExpense = operatingExpense
        self.tax = tax
        self.reportedNetIncome = reportedNetIncome
        self.reportedRevenue = reportedRevenue
        self.otherExpense = otherExpense
    }

    // MARK: - Computed Properties

    /// Sum of the revenue SEGMENTS — the height of the revenue bar.
    var totalRevenue: Double {
        revenueSources.reduce(0) { $0 + $1.value }
    }

    /// Denominator for every percentage on the card.
    ///
    /// ⚠️ NOT `totalRevenue`. Segments do not have to add up to revenue — LMT FY2025 reports
    /// 75.06B while its segments sum to 74.4B — so dividing by the segment sum ran every
    /// percentage ~0.9% high, and further wherever segment coverage is thinner. Falls back to
    /// the segment sum only when the backend did not send reported revenue.
    var revenueBasis: Double {
        if let reportedRevenue, reportedRevenue > 0 { return reportedRevenue }
        return totalRevenue
    }

    var totalCosts: Double {
        costOfSales + operatingExpense + tax + (otherExpense ?? 0)
    }

    /// The bottom line.
    ///
    /// 🔴 THIS IS REPORTED, NOT DERIVED — do not "simplify" it back to `revenue - costs`.
    /// That residual omits interest expense and every non-operating item, so it is operating
    /// profit after tax under a "Net Profit" label. Measured against live FMP data across 12
    /// large caps, 9 were off by more than 10% and **two inverted the sign of profitability**:
    /// the card showed Ford at +$6.2bn in a year it lost $8.2bn, and Boeing loss-making in a
    /// year it earned $2.2bn. `otherExpense` is the bucket that makes the rest reconcile.
    ///
    /// The fallback is the old residual, used only when the backend sent no `net_income`
    /// (an older deploy, or a cache row written before this shipped). Wrong in the same way it
    /// always was, but never worse — and never a fabricated zero.
    var netProfit: Double {
        if let reportedNetIncome { return reportedNetIncome }
        return totalRevenue - totalCosts
    }

    var isProfit: Bool {
        netProfit >= 0
    }

    var netProfitLabel: String {
        isProfit ? "Net Profit" : "Net Loss"
    }

    var netProfitColor: Color {
        isProfit ? AppColors.gain : AppColors.loss
    }

    /// Builds one legend line, flipping a negative cost into a credit.
    ///
    /// A line that comes back below zero is income, not expense, so it gets the credit label,
    /// a positive magnitude and the gain colour. Everything downstream — percentage, legend,
    /// bar — then agrees on it, instead of the legend showing a minus sign, the percentage
    /// showing `abs()` and the bar clamping it away.
    private func costLine(_ label: String,
                          credit creditLabel: String,
                          value: Double,
                          color: Color,
                          chartColor: Color? = nil) -> CostItem {
        value < 0
            ? CostItem(name: creditLabel, value: -value, color: AppColors.gain,
                       chartColor: AppColors.gainGraphic, isCredit: true)
            : CostItem(name: label, value: value, color: color, chartColor: chartColor)
    }

    // Cost items for display
    var costItems: [CostItem] {
        var items: [CostItem] = [
            // A 3-step tonal ramp that stays separable in BOTH modes. The old
            // frozen tints (#F87171 2.77:1, #FCA5A5 1.90:1) collapsed against a
            // light card, so the bar rendered as two segments instead of three.
            costLine("Cost of Sales", credit: "Cost of Sales", value: costOfSales,
                     color: AppColors.loss, chartColor: AppColors.lossGraphic),
            costLine("Op. Expense", credit: "Other Operating Income", value: operatingExpense, color: AppColors.alertOrange),
            costLine("Tax", credit: "Tax Benefit", value: tax, color: AppColors.caution)
        ]
        // Only present once the backend sends the composition. Without it the waterfall
        // silently dropped interest and every non-operating item into "Net Profit".
        if let otherExpense {
            items.append(costLine("Interest & Other",
                                  credit: "Other Income, net",
                                  value: otherExpense,
                                  color: AppColors.growthSectorGray))
        }
        return items
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
        CompactNumberFormat.string(revenueBasis)
    }

    var formattedNetProfit: String {
        // `CompactNumberFormat` already carries the sign; the old code hand-prefixed "-"
        // onto an `abs()` value using its own duplicate formatter.
        CompactNumberFormat.string(netProfit)
    }

    /// Net margin as a percentage of revenue, **signed**.
    ///
    /// This used to return `abs(netProfit) / totalRevenue`, so a company losing
    /// more than its revenue rendered as a bare "103%" in the legend next to
    /// the "Net Loss" label — a reader scanning the percentage column saw a
    /// positive-looking figure for a loss. The sign is the whole point here.
    func netProfitPercentage() -> Double {
        guard revenueBasis > 0 else { return 0 }
        let pct = (netProfit / revenueBasis) * 100
        return min(max(pct, -999), 999) // Cap to prevent display overflow
    }

    /// Net margin, formatted the same way every other share on the card is.
    var formattedNetProfitPercentage: String {
        PercentShare.string(netProfitPercentage())
    }

    // MARK: - Chart Calculations

    /// Maximum value for chart scaling - uses larger of revenue or total costs
    var chartMaxValue: Double {
        max(totalRevenue, totalCosts) * 1.1
    }

    /// Whether costs exceed revenue (company is loss-making)
    var costsExceedRevenue: Bool {
        totalCosts > totalRevenue
    }

    /// Revenue as percentage of chart max (for break-even line positioning)
    var revenuePercentageOfMax: Double {
        guard chartMaxValue > 0 else { return 0 }
        return totalRevenue / chartMaxValue
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

    // NB: the private `formatLargeNumber` that used to live here is gone — it was the third
    // byte-identical copy of a formatter whose millions tier was hardcoded `%.1f`, which is
    // why a legend could print "67B" beside "-112.0M". Everything routes through
    // `CompactNumberFormat` now, which is the migration its own header comment asked for.
}

// MARK: - Revenue Source Colors
extension RevenueSource {
    static let iPhoneColor = AppColors.primaryBlue      // Blue
    static let servicesColor = AppColors.alertPurple    // Purple
    static let macColor = AppColors.alertOrange         // Orange
    static let iPadColor = AppColors.accentCyan        // Cyan
    static let otherColor = AppColors.growthSectorGray       // Gray
    static let wearablesColor = AppColors.caution   // Amber
}

// MARK: - Sample Data
extension RevenueBreakdownData {
    // Apple - Profitable company
    static let sampleApple = RevenueBreakdownData(
        tickerSymbol: "AAPL",
        fiscalYear: "2024",
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
        fiscalYear: "2024",
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
        fiscalYear: "2024",
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
