//
//  FinancialModels.swift
//  ios
//
//  Data models for the Financials tab in Ticker Detail
//

import Foundation
import SwiftUI

// MARK: - Earnings Models

/// Time period for earnings display
enum EarningsTimePeriod: String, CaseIterable {
    case oneYear = "1Y"
    case threeYears = "3Y"

    var displayName: String { rawValue }
}

/// Metric type for earnings display
enum EarningsMetricType: String, CaseIterable {
    case eps = "EPS"
    case revenue = "Revenue"

    var displayName: String { rawValue }
}

/// Result type for quarterly earnings
enum EarningsResultType {
    case beat
    case missed
    case met

    var color: Color {
        switch self {
        case .beat: return AppColors.bullish
        case .missed: return AppColors.bearish
        case .met: return AppColors.neutral
        }
    }

    var displayName: String {
        switch self {
        case .beat: return "Beat"
        case .missed: return "Missed"
        case .met: return "Met"
        }
    }
}

/// Single quarter earnings data point
struct EarningsQuarter: Identifiable {
    let id = UUID()
    let quarter: String // e.g., "Q1 '24"
    let actual: Double
    let estimate: Double
    let surprised: Double // percentage surprise
    let stockPrice: Double? // optional stock price at that time

    var resultType: EarningsResultType {
        if surprised > 0.5 { return .beat }
        else if surprised < -0.5 { return .missed }
        else { return .met }
    }

    var formattedActual: String {
        String(format: "%.2f", actual)
    }

    var formattedEstimate: String {
        String(format: "%.2f", estimate)
    }

    var formattedSurprise: String {
        let sign = surprised >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", surprised))%"
    }

    var surpriseColor: Color {
        surprised >= 0 ? AppColors.bullish : AppColors.bearish
    }
}

extension EarningsQuarter {
    static let sampleData: [EarningsQuarter] = [
        EarningsQuarter(quarter: "Q1 '24", actual: 1.53, estimate: 1.50, surprised: 4.2, stockPrice: 171.48),
        EarningsQuarter(quarter: "Q2 '24", actual: 1.40, estimate: 1.35, surprised: -5.6, stockPrice: 181.18),
        EarningsQuarter(quarter: "Q3 '24", actual: 1.64, estimate: 1.60, surprised: -1.2, stockPrice: 178.61),
        EarningsQuarter(quarter: "Q4 '24", actual: 2.18, estimate: 2.35, surprised: 2.0, stockPrice: 185.64),
        EarningsQuarter(quarter: "Q1 '25", actual: 1.65, estimate: 1.62, surprised: 3.0, stockPrice: 178.42),
        EarningsQuarter(quarter: "Q2 '25", actual: 1.48, estimate: 1.45, surprised: 0.0, stockPrice: nil)
    ]
}

/// Complete earnings data for a ticker
struct EarningsData {
    let quarters: [EarningsQuarter]
    let nextEarningsDate: String
    let beatRate: Double // percentage of beats

    var formattedBeatRate: String {
        String(format: "%.0f%%", beatRate)
    }
}

extension EarningsData {
    static let sampleData = EarningsData(
        quarters: EarningsQuarter.sampleData,
        nextEarningsDate: "Jan 29, 2026",
        beatRate: 67
    )
}

// MARK: - Growth Models

/// Growth metric type
enum GrowthMetricType: String, CaseIterable {
    case eps = "EPS"
    case revenue = "Revenue"
    case netIncome = "Net Income"
    case operatingProfit = "Operating Profit"
    case freeCashFlow = "Free Cash Flow"

    var displayName: String { rawValue }

    var shortName: String {
        switch self {
        case .eps: return "EPS"
        case .revenue: return "Revenue"
        case .netIncome: return "Net Income"
        case .operatingProfit: return "Op. Profit"
        case .freeCashFlow: return "FCF"
        }
    }
}

/// Growth period type
enum GrowthPeriodType: String, CaseIterable {
    case annual = "Annual"
    case quarterly = "Quarterly"

    var displayName: String { rawValue }
}

/// Single growth data point (year or quarter)
struct GrowthDataPoint: Identifiable {
    let id = UUID()
    let period: String // e.g., "2020", "Q1 '24"
    let value: Double // in billions or actual value
    let yoyGrowth: Double // year-over-year growth percentage
    let sectorAverage: Double? // optional sector comparison

    var formattedValue: String {
        if abs(value) >= 1000 {
            return String(format: "%.0fB", value / 1000)
        } else if abs(value) >= 1 {
            return String(format: "%.1fB", value)
        } else {
            return String(format: "%.2f", value)
        }
    }

    var formattedGrowth: String {
        let sign = yoyGrowth >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", yoyGrowth))%"
    }

    var growthColor: Color {
        yoyGrowth >= 0 ? AppColors.bullish : AppColors.bearish
    }
}

extension GrowthDataPoint {
    static let revenueSampleData: [GrowthDataPoint] = [
        GrowthDataPoint(period: "2020", value: 274.5, yoyGrowth: -1.81, sectorAverage: 2.1),
        GrowthDataPoint(period: "2021", value: 365.8, yoyGrowth: -7.43, sectorAverage: 8.2),
        GrowthDataPoint(period: "2022", value: 394.3, yoyGrowth: 7.80, sectorAverage: 5.4),
        GrowthDataPoint(period: "2023", value: 383.3, yoyGrowth: -3.22, sectorAverage: 4.1),
        GrowthDataPoint(period: "2024", value: 391.0, yoyGrowth: 19.62, sectorAverage: 7.8),
        GrowthDataPoint(period: "2025", value: 410.2, yoyGrowth: -8.32, sectorAverage: 6.2)
    ]

    static let epsSampleData: [GrowthDataPoint] = [
        GrowthDataPoint(period: "2020", value: 3.28, yoyGrowth: 10.4, sectorAverage: 8.2),
        GrowthDataPoint(period: "2021", value: 5.61, yoyGrowth: 71.0, sectorAverage: 15.3),
        GrowthDataPoint(period: "2022", value: 6.11, yoyGrowth: 8.9, sectorAverage: 12.1),
        GrowthDataPoint(period: "2023", value: 6.13, yoyGrowth: 0.3, sectorAverage: 5.8),
        GrowthDataPoint(period: "2024", value: 6.75, yoyGrowth: 10.1, sectorAverage: 9.4),
        GrowthDataPoint(period: "2025", value: 7.10, yoyGrowth: 5.2, sectorAverage: 7.1)
    ]
}

/// Complete growth data
struct GrowthData {
    let revenueData: [GrowthDataPoint]
    let epsData: [GrowthDataPoint]
    let netIncomeData: [GrowthDataPoint]
    let operatingProfitData: [GrowthDataPoint]
    let freeCashFlowData: [GrowthDataPoint]

    func dataForMetric(_ metric: GrowthMetricType) -> [GrowthDataPoint] {
        switch metric {
        case .eps: return epsData
        case .revenue: return revenueData
        case .netIncome: return netIncomeData
        case .operatingProfit: return operatingProfitData
        case .freeCashFlow: return freeCashFlowData
        }
    }
}

extension GrowthData {
    static let sampleData = GrowthData(
        revenueData: GrowthDataPoint.revenueSampleData,
        epsData: GrowthDataPoint.epsSampleData,
        netIncomeData: GrowthDataPoint.revenueSampleData.map {
            GrowthDataPoint(period: $0.period, value: $0.value * 0.25, yoyGrowth: $0.yoyGrowth * 1.1, sectorAverage: $0.sectorAverage)
        },
        operatingProfitData: GrowthDataPoint.revenueSampleData.map {
            GrowthDataPoint(period: $0.period, value: $0.value * 0.30, yoyGrowth: $0.yoyGrowth * 0.9, sectorAverage: $0.sectorAverage)
        },
        freeCashFlowData: GrowthDataPoint.revenueSampleData.map {
            GrowthDataPoint(period: $0.period, value: $0.value * 0.22, yoyGrowth: $0.yoyGrowth * 1.2, sectorAverage: $0.sectorAverage)
        }
    )
}

// MARK: - Revenue Breakdown Models (How Company Makes Money)

/// Revenue segment with value and percentage
struct RevenueSegment: Identifiable {
    let id = UUID()
    let name: String
    let value: Double // in billions
    let percentage: Double
    let color: Color

    var formattedValue: String {
        String(format: "%.1fB", value)
    }

    var formattedPercentage: String {
        String(format: "%.0f%%", percentage)
    }
}

/// Cost and profit segment
struct CostProfitSegment: Identifiable {
    let id = UUID()
    let name: String
    let value: Double // in billions
    let percentage: Double
    let color: Color

    var formattedValue: String {
        String(format: "%.0fB", value)
    }

    var formattedPercentage: String {
        String(format: "%.0f%%", percentage)
    }
}

/// Complete revenue breakdown data
struct RevenueBreakdownData {
    let totalRevenue: Double
    let segments: [RevenueSegment]
    let costOfSales: CostProfitSegment
    let operatingExpenses: CostProfitSegment
    let tax: CostProfitSegment
    let netProfit: CostProfitSegment
    let netProfitMargin: Double

    var formattedTotalRevenue: String {
        String(format: "$%.0fB", totalRevenue)
    }

    var formattedNetProfitMargin: String {
        String(format: "%.0f%%", netProfitMargin)
    }
}

extension RevenueBreakdownData {
    static let sampleApple = RevenueBreakdownData(
        totalRevenue: 391,
        segments: [
            RevenueSegment(name: "iPhone", value: 200.58, percentage: 52, color: Color(hex: "3B82F6")),
            RevenueSegment(name: "Services", value: 73.10, percentage: 23, color: Color(hex: "22C55E")),
            RevenueSegment(name: "Mac", value: 32.20, percentage: 11, color: Color(hex: "F59E0B")),
            RevenueSegment(name: "iPad", value: 25.10, percentage: 8, color: Color(hex: "EF4444")),
            RevenueSegment(name: "Other", value: 20.46, percentage: 6, color: Color(hex: "8B5CF6"))
        ],
        costOfSales: CostProfitSegment(name: "Cost of Sales", value: 192, percentage: 49, color: Color(hex: "EF4444")),
        operatingExpenses: CostProfitSegment(name: "Op. Expense", value: 91, percentage: 24, color: Color(hex: "F97316")),
        tax: CostProfitSegment(name: "Tax", value: 58, percentage: 4, color: Color(hex: "6366F1")),
        netProfit: CostProfitSegment(name: "Net Profit", value: 72, percentage: 38, color: Color(hex: "22C55E")),
        netProfitMargin: 38
    )
}

// MARK: - Profit Power (Margins) Models

/// Single margin data point for a period
struct MarginDataPoint: Identifiable {
    let id = UUID()
    let period: String
    let grossMargin: Double
    let operatingMargin: Double
    let fcfMargin: Double
    let netMargin: Double
    let sectorAverageNetMargin: Double?

    var formattedGrossMargin: String {
        String(format: "%.1f%%", grossMargin)
    }

    var formattedOperatingMargin: String {
        String(format: "%.1f%%", operatingMargin)
    }

    var formattedFCFMargin: String {
        String(format: "%.1f%%", fcfMargin)
    }

    var formattedNetMargin: String {
        String(format: "%.1f%%", netMargin)
    }
}

extension MarginDataPoint {
    static let sampleData: [MarginDataPoint] = [
        MarginDataPoint(period: "2020", grossMargin: 38.2, operatingMargin: 24.1, fcfMargin: 23.1, netMargin: 20.9, sectorAverageNetMargin: 15.2),
        MarginDataPoint(period: "2021", grossMargin: 41.8, operatingMargin: 29.8, fcfMargin: 27.8, netMargin: 25.9, sectorAverageNetMargin: 16.8),
        MarginDataPoint(period: "2022", grossMargin: 43.3, operatingMargin: 30.3, fcfMargin: 27.8, netMargin: 25.3, sectorAverageNetMargin: 14.5),
        MarginDataPoint(period: "2023", grossMargin: 44.1, operatingMargin: 29.8, fcfMargin: 26.4, netMargin: 25.3, sectorAverageNetMargin: 13.8),
        MarginDataPoint(period: "2024", grossMargin: 46.2, operatingMargin: 30.7, fcfMargin: 26.8, netMargin: 24.3, sectorAverageNetMargin: 15.1),
        MarginDataPoint(period: "2025", grossMargin: 46.5, operatingMargin: 31.2, fcfMargin: 27.2, netMargin: 25.1, sectorAverageNetMargin: 15.8)
    ]
}

/// Margin type for filtering
enum MarginType: String, CaseIterable, Identifiable {
    case grossMargin = "Gross Margin"
    case operatingMargin = "Operating Margin"
    case fcfMargin = "FCF Margin"
    case netMargin = "Net Margin"

    var id: String { rawValue }

    var shortName: String {
        switch self {
        case .grossMargin: return "Gross"
        case .operatingMargin: return "Operating"
        case .fcfMargin: return "FCF"
        case .netMargin: return "Net"
        }
    }

    var color: Color {
        switch self {
        case .grossMargin: return Color(hex: "3B82F6")
        case .operatingMargin: return Color(hex: "22C55E")
        case .fcfMargin: return Color(hex: "F59E0B")
        case .netMargin: return Color(hex: "EF4444")
        }
    }
}

/// Complete profit power data
struct ProfitPowerData {
    let marginData: [MarginDataPoint]
    let periodType: GrowthPeriodType

    func valueForMarginType(_ type: MarginType, at index: Int) -> Double {
        guard index < marginData.count else { return 0 }
        let point = marginData[index]
        switch type {
        case .grossMargin: return point.grossMargin
        case .operatingMargin: return point.operatingMargin
        case .fcfMargin: return point.fcfMargin
        case .netMargin: return point.netMargin
        }
    }
}

extension ProfitPowerData {
    static let sampleData = ProfitPowerData(
        marginData: MarginDataPoint.sampleData,
        periodType: .annual
    )
}

// MARK: - Health Check Models

/// Health check metric status
enum HealthStatus {
    case excellent
    case good
    case neutral
    case warning
    case poor

    var color: Color {
        switch self {
        case .excellent: return AppColors.bullish
        case .good: return Color(hex: "4ADE80")
        case .neutral: return AppColors.neutral
        case .warning: return Color(hex: "F97316")
        case .poor: return AppColors.bearish
        }
    }

    var backgroundColor: Color {
        switch self {
        case .excellent, .good: return AppColors.bullish.opacity(0.15)
        case .neutral: return AppColors.neutral.opacity(0.15)
        case .warning, .poor: return AppColors.bearish.opacity(0.15)
        }
    }
}

/// Single health check metric
struct HealthCheckMetric: Identifiable {
    let id = UUID()
    let name: String
    let subtitle: String
    let value: Double
    let displayValue: String
    let status: HealthStatus
    let description: String
    let comparisonText: String
    let minValue: Double
    let maxValue: Double
    let isLowerBetter: Bool

    var normalizedValue: Double {
        let clamped = min(max(value, minValue), maxValue)
        return (clamped - minValue) / (maxValue - minValue)
    }
}

extension HealthCheckMetric {
    static let sampleData: [HealthCheckMetric] = [
        HealthCheckMetric(
            name: "Debt-to-Equity",
            subtitle: "Lower is Better",
            value: 0.88,
            displayValue: "0.88",
            status: .excellent,
            description: "63% lower debt than sector average. Strong balance sheet with conservative leverage.",
            comparisonText: "vs. 2.4 sector avg",
            minValue: 0,
            maxValue: 3,
            isLowerBetter: true
        ),
        HealthCheckMetric(
            name: "P/E Ratio",
            subtitle: "Valuation Metric",
            value: 24.3,
            displayValue: "24.3",
            status: .neutral,
            description: "Trading at a 16% discount to the Tech sector average. Fair value opportunity.",
            comparisonText: "vs. 29 sector avg",
            minValue: 0,
            maxValue: 50,
            isLowerBetter: false
        ),
        HealthCheckMetric(
            name: "Return on Equity",
            subtitle: "Profitability",
            value: 12.8,
            displayValue: "12.8%",
            status: .warning,
            description: "22% below ROE than peers. Low capital efficiency with improving trend.",
            comparisonText: "vs. 16.4% sector avg",
            minValue: 0,
            maxValue: 30,
            isLowerBetter: false
        ),
        HealthCheckMetric(
            name: "Current Ratio",
            subtitle: "Liquidity",
            value: 1.82,
            displayValue: "1.82",
            status: .good,
            description: "Above sector average, normal short-term liquidity position.",
            comparisonText: "vs. 1.5 sector avg",
            minValue: 0,
            maxValue: 3,
            isLowerBetter: false
        )
    ]
}

/// Health check summary
struct HealthCheckData {
    let metrics: [HealthCheckMetric]
    let overallScore: Int // out of 4
    let mixLabel: String // e.g., "[2/4] Mix"

    var formattedScore: String {
        "[\(overallScore)/\(metrics.count)] Mix"
    }
}

extension HealthCheckData {
    static let sampleData = HealthCheckData(
        metrics: HealthCheckMetric.sampleData,
        overallScore: 2,
        mixLabel: "[2/4] Mix"
    )
}

// MARK: - Signal of Confidence (Dividends & Buybacks) Models

/// Signal view type
enum SignalViewType: String, CaseIterable {
    case yield = "Yield (%)"
    case capital = "Capital ($)"

    var displayName: String { rawValue }
}

/// Single quarter dividend/buyback data
struct SignalQuarterData: Identifiable {
    let id = UUID()
    let quarter: String
    let dividendYield: Double
    let buybackYield: Double
    let dividendAmount: Double // in billions
    let buybackAmount: Double // in billions
    let sharesOutstanding: Double // in billions

    var formattedDividendYield: String {
        String(format: "%.2f%%", dividendYield)
    }

    var formattedBuybackYield: String {
        String(format: "%.2f%%", buybackYield)
    }

    var totalYield: Double {
        dividendYield + buybackYield
    }

    var formattedTotalYield: String {
        String(format: "%.2f%%", totalYield)
    }

    var formattedDividendAmount: String {
        String(format: "$%.1fB", dividendAmount)
    }

    var formattedBuybackAmount: String {
        String(format: "$%.1fB", buybackAmount)
    }

    var formattedSharesOutstanding: String {
        String(format: "%.2fB", sharesOutstanding)
    }
}

extension SignalQuarterData {
    static let sampleData: [SignalQuarterData] = [
        SignalQuarterData(quarter: "Q2 '24", dividendYield: 0.49, buybackYield: 1.12, dividendAmount: 3.8, buybackAmount: 23.5, sharesOutstanding: 15.44),
        SignalQuarterData(quarter: "Q3 '24", dividendYield: 0.48, buybackYield: 0.92, dividendAmount: 3.8, buybackAmount: 25.1, sharesOutstanding: 15.34),
        SignalQuarterData(quarter: "Q4 '24", dividendYield: 0.50, buybackYield: 1.35, dividendAmount: 3.9, buybackAmount: 20.8, sharesOutstanding: 15.20),
        SignalQuarterData(quarter: "Q1 '25", dividendYield: 0.51, buybackYield: 1.48, dividendAmount: 4.0, buybackAmount: 18.2, sharesOutstanding: 15.12),
        SignalQuarterData(quarter: "Q2 '25", dividendYield: 0.52, buybackYield: 1.25, dividendAmount: 4.1, buybackAmount: 22.4, sharesOutstanding: 15.04)
    ]
}

/// Complete signal of confidence data
struct SignalOfConfidenceData {
    let quarterData: [SignalQuarterData]
    let totalYield: Double // dividend + buyback yield
    let dividendYield: Double
    let buybackYield: Double
    let shareChangePercent: Double // change in shares outstanding

    var formattedTotalYield: String {
        String(format: "%.2f%%", totalYield)
    }

    var formattedDividendYield: String {
        String(format: "%.2f%%", dividendYield)
    }

    var formattedBuybackYield: String {
        String(format: "%.2f%%", buybackYield)
    }

    var formattedShareChange: String {
        let sign = shareChangePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", shareChangePercent))%"
    }

    var shareChangeColor: Color {
        // For shares, decrease is positive for shareholders
        shareChangePercent <= 0 ? AppColors.bullish : AppColors.bearish
    }

    var yieldBreakdown: String {
        return "\(String(format: "%.1f", dividendYield))% Dividends + \(String(format: "%.1f", buybackYield))% Buyback"
    }
}

extension SignalOfConfidenceData {
    static let sampleData = SignalOfConfidenceData(
        quarterData: SignalQuarterData.sampleData,
        totalYield: 4.26,
        dividendYield: 1.55,
        buybackYield: 2.71,
        shareChangePercent: -2.6
    )
}

// MARK: - Complete Financial Data Model

/// Combined financial data for a ticker
struct TickerFinancialData: Identifiable {
    let id = UUID()
    let symbol: String
    let earnings: EarningsData
    let growth: GrowthData
    let revenueBreakdown: RevenueBreakdownData
    let profitPower: ProfitPowerData
    let healthCheck: HealthCheckData
    let signalOfConfidence: SignalOfConfidenceData
}

extension TickerFinancialData {
    static let sampleApple = TickerFinancialData(
        symbol: "AAPL",
        earnings: EarningsData.sampleData,
        growth: GrowthData.sampleData,
        revenueBreakdown: RevenueBreakdownData.sampleApple,
        profitPower: ProfitPowerData.sampleData,
        healthCheck: HealthCheckData.sampleData,
        signalOfConfidence: SignalOfConfidenceData.sampleData
    )
}
