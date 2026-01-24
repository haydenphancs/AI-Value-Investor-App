//
//  SignalOfConfidenceModels.swift
//  ios
//
//  Data models for the Signal of Confidence Section in the Financial tab
//  Displays dividends, buybacks, and shares outstanding over time
//

import Foundation
import SwiftUI

// MARK: - Signal of Confidence View Type

enum SignalOfConfidenceViewType: String, CaseIterable, Identifiable {
    case yield = "Yield (%)"
    case capital = "Capital ($)"

    var id: String { rawValue }
}

// MARK: - Signal of Confidence Metric Type

enum SignalOfConfidenceMetricType: String, CaseIterable, Identifiable {
    case dividends = "Dividends"
    case buybacks = "Buybacks"
    case sharesOutstanding = "Shares Outstanding"

    var id: String { rawValue }

    var color: Color {
        switch self {
        case .dividends: return AppColors.confidenceDividends
        case .buybacks: return AppColors.confidenceBuybacks
        case .sharesOutstanding: return AppColors.confidenceSharesOutstanding
        }
    }

    var isLine: Bool {
        self == .sharesOutstanding
    }

    var description: String {
        switch self {
        case .dividends:
            return "Cash payments made to shareholders, typically quarterly. A consistent or growing dividend signals financial stability and management confidence."
        case .buybacks:
            return "When a company repurchases its own shares, reducing shares outstanding. This returns cash to shareholders and can boost earnings per share."
        case .sharesOutstanding:
            return "Total number of shares held by all shareholders. Decreasing share count from buybacks is generally positive; increasing count from dilution is concerning."
        }
    }
}

// MARK: - Signal of Confidence Data Point

struct SignalOfConfidenceDataPoint: Identifiable {
    let id = UUID()
    let period: String                    // e.g., "Q2 '24", "Q3 '24"
    let dividendYield: Double             // Percentage (e.g., 1.3 for 1.3%)
    let buybackYield: Double              // Percentage
    let dividendAmount: Double            // Dollar amount in millions
    let buybackAmount: Double             // Dollar amount in millions
    let sharesOutstanding: Double         // In millions (e.g., 150 for 150M)

    /// Total shareholder yield (dividend + buyback)
    var totalYield: Double {
        dividendYield + buybackYield
    }

    /// Total capital returned in millions
    var totalCapitalReturned: Double {
        dividendAmount + buybackAmount
    }
}

// MARK: - Signal of Confidence Summary

struct SignalOfConfidenceSummary {
    let totalYield: Double                // Total shareholder yield percentage
    let dividendYield: Double             // Dividend portion
    let buybackYield: Double              // Buyback portion
    let shareCountChange: Double          // Percentage change (negative = buybacks reducing count)

    var shareCountDescription: String {
        if shareCountChange < 0 {
            return "Share count decrease by \(String(format: "%.1f", abs(shareCountChange)))%."
        } else if shareCountChange > 0 {
            return "Share count increase by \(String(format: "%.1f", shareCountChange))%."
        } else {
            return "Share count unchanged."
        }
    }

    var formattedSummary: String {
        "Total Yield: \(String(format: "%.1f", totalYield))% (\(String(format: "%.1f", dividendYield))% Dividends + \(String(format: "%.1f", buybackYield))% Buyback)."
    }
}

// MARK: - Dividend Yield Status

enum DividendYieldStatus: String {
    case low = "Low"
    case fair = "Fair"
    case high = "High"
    case veryHigh = "Very High"

    var color: Color {
        switch self {
        case .low: return AppColors.bearish
        case .fair: return AppColors.neutral
        case .high: return AppColors.bullish
        case .veryHigh: return AppColors.primaryBlue
        }
    }

    static func from(yield: Double, industryAverage: Double) -> DividendYieldStatus {
        let ratio = yield / industryAverage
        if ratio < 0.7 { return .low }
        if ratio < 1.0 { return .fair }
        if ratio < 1.5 { return .high }
        return .veryHigh
    }
}

// MARK: - Buyback Status

enum BuybackStatus: String {
    case low = "Low"
    case moderate = "Moderate"
    case high = "High"
    case veryHigh = "Very High"

    var color: Color {
        switch self {
        case .low: return AppColors.bearish
        case .moderate: return AppColors.neutral
        case .high: return AppColors.bullish
        case .veryHigh: return AppColors.primaryBlue
        }
    }
}

// MARK: - Dividend Info

struct DividendInfo {
    let exDividendDate: Date
    let paymentDate: Date
    let fiveYearAvgYield: Double
    let status: DividendYieldStatus
    let buybackStatus: BuybackStatus

    var formattedExDividendDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d, yyyy"
        return formatter.string(from: exDividendDate)
    }

    var formattedPaymentDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d, yyyy"
        return formatter.string(from: paymentDate)
    }

    var formattedYield: String {
        String(format: "%.2f%%", fiveYearAvgYield)
    }
}

extension DividendInfo {
    static let sample: DividendInfo = {
        var exComponents = DateComponents()
        exComponents.year = 2025
        exComponents.month = 11
        exComponents.day = 10
        let exDate = Calendar.current.date(from: exComponents) ?? Date()

        var payComponents = DateComponents()
        payComponents.year = 2025
        payComponents.month = 11
        payComponents.day = 16
        let payDate = Calendar.current.date(from: payComponents) ?? Date()

        return DividendInfo(
            exDividendDate: exDate,
            paymentDate: payDate,
            fiveYearAvgYield: 0.68,
            status: .low,
            buybackStatus: .moderate
        )
    }()
}

// MARK: - Signal of Confidence Section Data

struct SignalOfConfidenceSectionData {
    let dataPoints: [SignalOfConfidenceDataPoint]
    let summary: SignalOfConfidenceSummary
    let dividendInfo: DividendInfo?

    init(
        dataPoints: [SignalOfConfidenceDataPoint],
        summary: SignalOfConfidenceSummary,
        dividendInfo: DividendInfo? = nil
    ) {
        self.dataPoints = dataPoints
        self.summary = summary
        self.dividendInfo = dividendInfo
    }

    /// Get max yield for chart scaling
    var maxYield: Double {
        let maxDividend = dataPoints.map { $0.dividendYield }.max() ?? 0
        let maxBuyback = dataPoints.map { $0.buybackYield }.max() ?? 0
        return max(maxDividend, maxBuyback) * 1.15
    }

    /// Get max capital for chart scaling (in millions)
    var maxCapital: Double {
        let maxDividend = dataPoints.map { $0.dividendAmount }.max() ?? 0
        let maxBuyback = dataPoints.map { $0.buybackAmount }.max() ?? 0
        return max(maxDividend, maxBuyback) * 1.15
    }

    /// Get shares outstanding range for normalization
    var sharesRange: (min: Double, max: Double) {
        let shares = dataPoints.map { $0.sharesOutstanding }
        let minShares = (shares.min() ?? 0) * 0.95
        let maxShares = (shares.max() ?? 1) * 1.05
        return (minShares, maxShares)
    }
}

// MARK: - Sample Data

extension SignalOfConfidenceSectionData {
    static let sampleData = SignalOfConfidenceSectionData(
        dataPoints: [
            SignalOfConfidenceDataPoint(
                period: "Q2 '24",
                dividendYield: 1.3,
                buybackYield: 1.1,
                dividendAmount: 3800,
                buybackAmount: 3200,
                sharesOutstanding: 155
            ),
            SignalOfConfidenceDataPoint(
                period: "Q3 '24",
                dividendYield: 1.6,
                buybackYield: 1.3,
                dividendAmount: 4200,
                buybackAmount: 3500,
                sharesOutstanding: 152
            ),
            SignalOfConfidenceDataPoint(
                period: "Q4 '24",
                dividendYield: 1.55,
                buybackYield: 0.95,
                dividendAmount: 4100,
                buybackAmount: 2500,
                sharesOutstanding: 158
            ),
            SignalOfConfidenceDataPoint(
                period: "Q1 '25",
                dividendYield: 1.35,
                buybackYield: 1.15,
                dividendAmount: 3900,
                buybackAmount: 3300,
                sharesOutstanding: 162
            ),
            SignalOfConfidenceDataPoint(
                period: "Q2 '25",
                dividendYield: 2.65,
                buybackYield: 1.6,
                dividendAmount: 7500,
                buybackAmount: 4500,
                sharesOutstanding: 168
            )
        ],
        summary: SignalOfConfidenceSummary(
            totalYield: 4.2,
            dividendYield: 1.5,
            buybackYield: 2.7,
            shareCountChange: 0.0
        ),
        dividendInfo: .sample
    )
}

// MARK: - Signal of Confidence Info Item

struct SignalOfConfidenceInfoItem: Identifiable {
    let id = UUID()
    let title: String
    let description: String
    let icon: String
    let example: String?

    init(title: String, description: String, icon: String, example: String? = nil) {
        self.title = title
        self.description = description
        self.icon = icon
        self.example = example
    }
}

extension SignalOfConfidenceInfoItem {
    static let valueInvestingTips: [SignalOfConfidenceInfoItem] = [
        SignalOfConfidenceInfoItem(
            title: "Total Shareholder Yield",
            description: "The sum of dividend yield and buyback yield. This shows the total percentage of market cap being returned to shareholders annually. A higher yield indicates management confidence and commitment to rewarding shareholders.",
            icon: "percent",
            example: "A 4% total yield (1.5% dividends + 2.5% buybacks) means shareholders receive 4% of their investment back annually."
        ),
        SignalOfConfidenceInfoItem(
            title: "Dividend Consistency",
            description: "Companies that maintain or grow dividends through economic cycles demonstrate financial strength. Look for companies with long dividend track records (Dividend Aristocrats have 25+ years).",
            icon: "calendar.badge.checkmark",
            example: "Apple increased dividends for 12 consecutive years, signaling management's confidence in future cash flows."
        ),
        SignalOfConfidenceInfoItem(
            title: "Buyback Effectiveness",
            description: "Buybacks are most valuable when shares are undervalued. Companies buying back stock at high valuations destroy value. Check if buybacks actually reduce share count or just offset dilution from stock compensation.",
            icon: "arrow.down.circle.fill",
            example: "If share count drops 3% annually from buybacks, EPS grows 3% even with flat earnings."
        ),
        SignalOfConfidenceInfoItem(
            title: "Share Count Trend",
            description: "A declining share count over time indicates effective capital allocation. Rising share counts despite buybacks suggest excessive stock-based compensation diluting existing shareholders.",
            icon: "chart.line.downtrend.xyaxis",
            example: "If a company spends $10B on buybacks but share count increases, the money went to employees, not shareholders."
        ),
        SignalOfConfidenceInfoItem(
            title: "Dividend vs Buyback Trade-off",
            description: "Dividends are taxed immediately; buybacks defer taxes until you sell. However, dividends are harder to cut (signals distress), while buybacks can stop anytime without negative perception.",
            icon: "arrow.left.arrow.right",
            example: "Tech companies often prefer buybacks for tax efficiency; utilities favor dividends for income-seeking investors."
        )
    ]
}
