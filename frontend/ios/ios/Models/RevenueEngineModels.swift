//
//  RevenueEngineModels.swift
//  ios
//
//  Data models for The Revenue Engine deep dive section
//  Shows revenue segment breakdown with automatic role assignment and growth formatting
//

import Foundation
import SwiftUI

// MARK: - Revenue Segment Role

enum RevenueSegmentRole: String {
    case risingSegment = "Rising Segment"
    case headwind = "Headwind"
    case coreBusiness = "Core Business"
    case diversified = "Diversified"

    var color: Color {
        switch self {
        case .risingSegment:
            return AppColors.bullish
        case .headwind:
            return AppColors.bearish
        case .coreBusiness:
            return AppColors.primaryBlue
        case .diversified:
            return AppColors.textSecondary
        }
    }

    var backgroundColor: Color {
        color.opacity(0.15)
    }

    var iconName: String {
        switch self {
        case .risingSegment:
            return "arrow.up.right.circle.fill"
        case .headwind:
            return "arrow.down.right.circle.fill"
        case .coreBusiness:
            return "star.circle.fill"
        case .diversified:
            return "circle.grid.2x2.fill"
        }
    }
}

// MARK: - Revenue Segment

struct RevenueSegment: Identifiable {
    let id = UUID()
    let name: String
    let currentRevenue: Double      // in millions or billions (consistent unit)
    let previousRevenue: Double     // in millions or billions (consistent unit)
    let totalRevenue: Double        // total company revenue for percentage calculation

    // MARK: - Computed Properties

    /// Growth rate as decimal (e.g., 0.80 = 80% growth)
    var growth: Double {
        guard previousRevenue > 0 else { return 0 }
        return (currentRevenue - previousRevenue) / previousRevenue
    }

    /// Revenue as percentage of total
    var revenuePercentage: Double {
        guard totalRevenue > 0 else { return 0 }
        return (currentRevenue / totalRevenue) * 100
    }

    /// Formatted revenue string (e.g., "$12.5B")
    var formattedRevenue: String {
        if currentRevenue >= 1000 {
            return String(format: "$%.1fB", currentRevenue / 1000)
        } else {
            return String(format: "$%.0fM", currentRevenue)
        }
    }

    /// Formatted percentage string (e.g., "25%")
    var formattedPercentage: String {
        String(format: "%.0f%%", revenuePercentage)
    }

    /// Auto-formatted growth text based on growth rate
    /// Examples: "Hyper-growth (+80% YoY)", "Stable (+2% YoY)", "Declining (-15% YoY)"
    var formattedGrowth: String {
        let percentage = growth * 100
        let sign = growth >= 0 ? "+" : ""
        let percentString = String(format: "%@%.0f%%", sign, percentage)

        if growth >= 0.40 {
            return "Hyper-growth (\(percentString) YoY)"
        } else if growth >= 0.15 {
            return "Strong growth (\(percentString) YoY)"
        } else if growth >= 0.05 {
            return "Growing (\(percentString) YoY)"
        } else if growth >= -0.05 {
            return "Stable (\(percentString) YoY)"
        } else if growth >= -0.20 {
            return "Declining (\(percentString) YoY)"
        } else {
            return "Sharp decline (\(percentString) YoY)"
        }
    }

    /// Growth trend color
    var growthColor: Color {
        if growth >= 0.05 {
            return AppColors.bullish
        } else if growth >= -0.05 {
            return AppColors.neutral
        } else {
            return AppColors.bearish
        }
    }
}

// MARK: - Revenue Engine Data

struct ReportRevenueEngineData {
    let segments: [RevenueSegment]
    let totalRevenue: Double
    let revenueUnit: String         // "Millions" or "Billions"
    let period: String              // e.g., "FY 2024"
    let analysisNote: String?       // Optional AI insight

    // MARK: - Role Assignment Logic

    /// Automatically assigns roles to segments based on their characteristics
    func roleForSegment(_ segment: RevenueSegment) -> RevenueSegmentRole {
        guard !segments.isEmpty else { return .diversified }

        // Find segment with highest growth
        let maxGrowth = segments.map { $0.growth }.max() ?? 0
        let minGrowth = segments.map { $0.growth }.min() ?? 0
        let maxRevenue = segments.map { $0.currentRevenue }.max() ?? 0

        // Rule 1: Rising Segment - highest growth (and growth > 10%)
        if segment.growth == maxGrowth && segment.growth > 0.10 {
            return .risingSegment
        }

        // Rule 2: Headwind - most negative growth (and growth < -5%)
        if segment.growth == minGrowth && segment.growth < -0.05 {
            return .headwind
        }

        // Rule 3: Core Business - largest revenue
        if segment.currentRevenue == maxRevenue {
            return .coreBusiness
        }

        // Rule 4: Diversified - everything else
        return .diversified
    }

    /// Total revenue formatted
    var formattedTotalRevenue: String {
        if totalRevenue >= 1000 {
            return String(format: "$%.1fB", totalRevenue / 1000)
        } else {
            return String(format: "$%.0fM", totalRevenue)
        }
    }
}

// MARK: - Sample Data

extension ReportRevenueEngineData {
    /// Sample data demonstrating all 4 role types
    static let sampleOracle = ReportRevenueEngineData(
        segments: [
            // Core Business: Largest revenue
            RevenueSegment(
                name: "Cloud Services & License Support",
                currentRevenue: 38_500,    // $38.5B
                previousRevenue: 37_200,   // $37.2B
                totalRevenue: 53_000
            ),
            // Rising Segment: Highest growth
            RevenueSegment(
                name: "Cloud Infrastructure (IaaS)",
                currentRevenue: 8_500,     // $8.5B
                previousRevenue: 4_700,    // $4.7B (80% growth!)
                totalRevenue: 53_000
            ),
            // Headwind: Declining
            RevenueSegment(
                name: "License Revenue",
                currentRevenue: 3_200,     // $3.2B
                previousRevenue: 4_100,    // $4.1B (-22% decline)
                totalRevenue: 53_000
            ),
            // Diversified: Everything else
            RevenueSegment(
                name: "Hardware & Other",
                currentRevenue: 2_800,     // $2.8B
                previousRevenue: 2_700,    // $2.7B (4% growth - diversified)
                totalRevenue: 53_000
            )
        ],
        totalRevenue: 53_000,  // $53B total
        revenueUnit: "Millions",
        period: "FY 2024",
        analysisNote: "Oracle's revenue engine is transforming: cloud infrastructure is exploding at 80% YoY while legacy license revenue shrinks. The core support business remains stable and massive, generating $38.5B in recurring revenue."
    )
}
