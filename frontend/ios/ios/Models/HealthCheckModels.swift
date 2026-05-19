//
//  HealthCheckModels.swift
//  ios
//
//  Data models for the Health Check financial metrics card
//

import Foundation
import SwiftUI

// MARK: - Health Check Overall Rating

enum HealthCheckRating: String, CaseIterable {
    case excellent = "Excellent"
    case good = "Good"
    case mix = "Mix"
    case caution = "Caution"
    case poor = "Poor"

    var color: Color {
        switch self {
        case .excellent:
            return AppColors.bullish
        case .good:
            return Color(hex: "4ADE80")
        case .mix:
            return AppColors.neutral
        case .caution:
            return AppColors.alertOrange
        case .poor:
            return AppColors.bearish
        }
    }

    var iconName: String {
        switch self {
        case .excellent, .good:
            return "checkmark.circle.fill"
        case .mix:
            return "checkmark.circle.fill"
        case .caution:
            return "exclamationmark.triangle.fill"
        case .poor:
            return "xmark.circle.fill"
        }
    }
}

// MARK: - Health Check Metric Type

enum HealthCheckMetricType: String, CaseIterable, Identifiable {
    case debtToEquity = "Debt-to-Equity"
    case peRatio = "P/E Ratio"
    case returnOnEquity = "Return on Equity"
    case currentRatio = "Current Ratio"
    case altmanZScore = "Altman Z-Score"
    case interestCoverage = "Interest Coverage"
    case quickRatio = "Quick Ratio"

    var id: String { rawValue }

    var subtitle: String {
        switch self {
        case .debtToEquity:
            return "Lower is better"
        case .peRatio:
            return "Valuation metric"
        case .returnOnEquity:
            return "Profitability"
        case .currentRatio:
            return "Liquidity"
        case .altmanZScore:
            return "Bankruptcy risk"
        case .interestCoverage:
            return "Debt service"
        case .quickRatio:
            return "Near-cash liquidity"
        }
    }

    var leftLabel: String {
        switch self {
        case .debtToEquity:
            return "Healthy"
        case .peRatio:
            return "Cheap"
        case .returnOnEquity:
            return "Poor"
        case .currentRatio:
            return "Low"
        case .altmanZScore:
            return "Distress"
        case .interestCoverage:
            return "Thin"
        case .quickRatio:
            return "Tight"
        }
    }

    var rightLabel: String {
        switch self {
        case .debtToEquity:
            return "Risky"
        case .peRatio:
            return "Expensive"
        case .returnOnEquity:
            return "Great"
        case .currentRatio:
            return "High"
        case .altmanZScore:
            return "Safe"
        case .interestCoverage:
            return "Strong"
        case .quickRatio:
            return "Ample"
        }
    }

    /// Description for value investors
    var valueInvestorDescription: String {
        switch self {
        case .debtToEquity:
            return "Measures a company's financial leverage. Value investors prefer lower ratios as they indicate less reliance on debt financing and lower bankruptcy risk."
        case .peRatio:
            return "Price-to-Earnings ratio shows how much investors pay per dollar of earnings. Lower P/E relative to sector may indicate undervaluation - a key value investing signal."
        case .returnOnEquity:
            return "Measures profitability relative to shareholder equity. Higher ROE indicates efficient use of capital, but compare to sector average for context."
        case .currentRatio:
            return "Measures ability to pay short-term obligations. A ratio above 1.0 indicates good liquidity. Value investors look for financial stability."
        case .altmanZScore:
            return "Predicts bankruptcy probability using five financial ratios. Z > 3.0 is safe, 1.8–3.0 is a grey zone, and below 1.8 signals distress. A key metric for value investors assessing downside risk."
        case .interestCoverage:
            return "EBIT divided by interest expense. Higher means the company can comfortably service its debt; a ratio under 2 signals vulnerability to earnings pressure."
        case .quickRatio:
            return "Near-cash assets divided by current liabilities. Excludes inventory and prepaid items. Above 1.0 indicates the company can cover short-term debts without selling stock."
        }
    }
}

// MARK: - Health Check Metric Status

enum HealthCheckMetricStatus {
    case positive  // Green - good metric
    case neutral   // Yellow - average/mixed
    case negative  // Red - concerning

    var primaryColor: Color {
        switch self {
        case .positive:
            return AppColors.bullish
        case .neutral:
            return AppColors.neutral
        case .negative:
            return AppColors.bearish
        }
    }
}

// MARK: - Health Check Metric Data

struct HealthCheckMetric: Identifiable {
    let id = UUID()
    let type: HealthCheckMetricType
    let value: Double
    let comparisonValue: Double?  // Sector average or benchmark
    let percentDifference: Double?  // % difference from sector
    let gaugePosition: Double  // 0.0 to 1.0 position on gauge
    let status: HealthCheckMetricStatus
    let insightText: String
    let highlightedValue: String?  // e.g., "43% lower" or "15% discount"
    let highlightedLabel: String?  // e.g., "debt" or "discount"

    var formattedValue: String {
        switch type {
        case .debtToEquity:
            return String(format: "%.2f", value)
        case .peRatio:
            return String(format: "%.1f", value)
        case .returnOnEquity:
            return String(format: "%.1f%%", value)
        case .currentRatio:
            return String(format: "%.2f", value)
        case .altmanZScore:
            return String(format: "%.1f", value)
        case .interestCoverage:
            return String(format: "%.2f", value)
        case .quickRatio:
            return String(format: "%.2f", value)
        }
    }

    var formattedComparison: String? {
        switch type {
        case .altmanZScore:
            // Show zone label instead of sector comparison
            if value > 3.0 { return "Safe zone" }
            else if value > 1.8 { return "Grey zone" }
            else { return "Distress zone" }
        default:
            break
        }

        guard let comparison = comparisonValue else { return nil }

        switch type {
        case .debtToEquity, .currentRatio, .interestCoverage, .quickRatio:
            return "vs \(String(format: "%.2f", comparison))"
        case .peRatio:
            return "vs \(String(format: "%.1f", comparison))"
        case .returnOnEquity:
            return "vs \(String(format: "%.1f%%", comparison))"
        case .altmanZScore:
            return nil
        }
    }

    var valueColor: Color {
        colorAtPosition(gaugePosition, for: type)
    }
    
    /// Calculate the color at a specific position on the gauge gradient
    private func colorAtPosition(_ position: Double, for metricType: HealthCheckMetricType) -> Color {
        let clampedPosition = min(max(position, 0.0), 1.0)
        
        let gradientColors: [Color]
        switch metricType {
        case .debtToEquity, .peRatio:
            // Lower is better: green -> lime -> yellow -> orange -> red
            gradientColors = [
                AppColors.bullish,
                Color(hex: "84CC16"),
                AppColors.neutral,
                AppColors.alertOrange,
                AppColors.bearish
            ]
        case .returnOnEquity, .currentRatio, .altmanZScore, .interestCoverage, .quickRatio:
            // Higher is better: red -> orange -> yellow -> lime -> green
            gradientColors = [
                AppColors.bearish,
                AppColors.alertOrange,
                AppColors.neutral,
                Color(hex: "84CC16"),
                AppColors.bullish
            ]
        }
        
        // Map position to color index (0.0 -> first color, 1.0 -> last color)
        let colorCount = gradientColors.count
        let scaledPosition = clampedPosition * Double(colorCount - 1)
        let lowerIndex = Int(floor(scaledPosition))
        let upperIndex = min(lowerIndex + 1, colorCount - 1)
        let fraction = scaledPosition - Double(lowerIndex)
        
        // For simplicity, return the closest color (no interpolation)
        if fraction < 0.5 {
            return gradientColors[lowerIndex]
        } else {
            return gradientColors[upperIndex]
        }
    }
}

// MARK: - Health Check Section Data

struct HealthCheckSectionData {
    let overallRating: HealthCheckRating
    let passedCount: Int
    let totalCount: Int
    let metrics: [HealthCheckMetric]

    var ratingBadgeText: String {
        "[\(passedCount)/\(totalCount)] \(overallRating.rawValue)"
    }
}

// MARK: - Sample Data

extension HealthCheckSectionData {
    static let sampleData = HealthCheckSectionData(
        overallRating: .mix,
        passedCount: 3,
        totalCount: 5,
        metrics: [
            HealthCheckMetric(
                type: .debtToEquity,
                value: 0.68,
                comparisonValue: 1.19,
                percentDifference: -43,
                gaugePosition: 0.25,
                status: .positive,
                insightText: "Strong balance sheet with conservative leverage.",
                highlightedValue: "43%",
                highlightedLabel: "lower debt than sector average."
            ),
            HealthCheckMetric(
                type: .peRatio,
                value: 24.3,
                comparisonValue: 28.5,
                percentDifference: -15,
                gaugePosition: 0.42,
                status: .positive,
                insightText: "to the Tech sector average. Fair value opportunity.",
                highlightedValue: "15%",
                highlightedLabel: "Trading at a discount"
            ),
            HealthCheckMetric(
                type: .returnOnEquity,
                value: 12.8,
                comparisonValue: 28.5,
                percentDifference: -22,
                gaugePosition: 0.35,
                status: .negative,
                insightText: "ROE than peers. Low capital efficiency with improving trend.",
                highlightedValue: "22%",
                highlightedLabel: "below"
            ),
            HealthCheckMetric(
                type: .currentRatio,
                value: 1.82,
                comparisonValue: 1.5,
                percentDifference: 21,
                gaugePosition: 0.68,
                status: .positive,
                insightText: "sector average, normal short-term liquidity position.",
                highlightedValue: "21%",
                highlightedLabel: "above"
            ),
            HealthCheckMetric(
                type: .altmanZScore,
                value: 2.4,
                comparisonValue: nil,
                percentDifference: nil,
                gaugePosition: 0.53,
                status: .neutral,
                insightText: "Grey zone. Moderate financial stress signals.",
                highlightedValue: "2.4",
                highlightedLabel: "Z-Score."
            )
        ]
    )

    static let sampleApple = HealthCheckSectionData(
        overallRating: .good,
        passedCount: 3,
        totalCount: 5,
        metrics: [
            HealthCheckMetric(
                type: .debtToEquity,
                value: 1.87,
                comparisonValue: 0.95,
                percentDifference: 97,
                gaugePosition: 0.65,
                status: .neutral,
                insightText: "Moderate leverage compared to tech sector average.",
                highlightedValue: "97%",
                highlightedLabel: "higher debt than sector average."
            ),
            HealthCheckMetric(
                type: .peRatio,
                value: 35.15,
                comparisonValue: 27.04,
                percentDifference: 30,
                gaugePosition: 0.72,
                status: .neutral,
                insightText: "to sector average. Premium valuation for quality.",
                highlightedValue: "30%",
                highlightedLabel: "Trading at premium"
            ),
            HealthCheckMetric(
                type: .returnOnEquity,
                value: 147.2,
                comparisonValue: 25.0,
                percentDifference: 489,
                gaugePosition: 0.95,
                status: .positive,
                insightText: "ROE than peers. Exceptional capital efficiency.",
                highlightedValue: "5.9x",
                highlightedLabel: "higher"
            ),
            HealthCheckMetric(
                type: .currentRatio,
                value: 0.99,
                comparisonValue: 1.5,
                percentDifference: -34,
                gaugePosition: 0.35,
                status: .negative,
                insightText: "sector average. Tight but manageable liquidity.",
                highlightedValue: "34%",
                highlightedLabel: "below"
            ),
            HealthCheckMetric(
                type: .altmanZScore,
                value: 4.8,
                comparisonValue: nil,
                percentDifference: nil,
                gaugePosition: 0.98,
                status: .positive,
                insightText: "Fortress balance sheet. Very low bankruptcy risk.",
                highlightedValue: "4.8",
                highlightedLabel: "Z-Score."
            )
        ]
    )
}
