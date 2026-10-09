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

    var borderColor: Color {
        return AppColors.textSecondary
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

    /// Is there a prior-year figure to compute YoY against?
    ///
    /// FMP's product segmentation re-keys segments between fiscal years — a line
    /// broken out for the first time, or simply renamed, has no match in the prior
    /// year, and the backend's `prior_lookup.get(name, 0.0)` then hands us a 0.
    /// `growth` collapses that to 0.0, which the card rendered as a confident
    /// "Stable (+0.0% YoY)" — a fabricated flat for a segment we have no history for.
    /// The role heuristics still need a number, so `growth` keeps returning 0; the
    /// UI asks THIS before printing a percentage.
    var hasPriorAnchor: Bool { previousRevenue > 0 }

    /// Growth rate as decimal (e.g., 0.80 = 80% growth). 0 when there is no prior
    /// anchor — check `hasPriorAnchor` before showing it as a rate.
    var growth: Double {
        guard previousRevenue > 0 else { return 0 }
        return (currentRevenue - previousRevenue) / previousRevenue
    }

    /// Revenue as percentage of total
    var revenuePercentage: Double {
        guard totalRevenue > 0 else { return 0 }
        return (currentRevenue / totalRevenue) * 100
    }

    // The amount is formatted by `ReportRevenueEngineData.formattedRevenue(for:)`: the
    // reporting currency belongs to the whole breakdown, not to a row.

    /// Formatted percentage string (e.g., "25%")
    var formattedPercentage: String {
        String(format: "%.0f%%", revenuePercentage)
    }

    /// Auto-formatted growth text based on growth rate
    /// Examples: "Hyper-growth (+80% YoY)", "Stable (+2% YoY)", "Declining (-15% YoY)"
    var formattedGrowth: String {
        guard hasPriorAnchor else { return "No prior-year figure" }
        let percentage = growth * 100
        let sign = growth >= 0 ? "+" : ""
        let percentString = String(format: "%@%.1f%%", sign, percentage)

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
        guard hasPriorAnchor else { return AppColors.textMuted }
        if growth >= 0.05 {
            return AppColors.bullish
        } else if growth >= -0.05 {
            return AppColors.textSecondary
        } else {
            return AppColors.bearish
        }
    }
}

// MARK: - Revenue Engine Data

struct ReportRevenueEngineData {
    let segments: [RevenueSegment]
    /// The share denominator, in millions: REPORTED revenue when the income statement has
    /// it (the Financials tab's basis), else the segment sum.
    let totalRevenue: Double
    let revenueUnit: String         // the unit of every value: "Millions"
    let period: String              // e.g., "FY 2024"; "" when the fiscal year is unknown
    let analysisNote: String?       // Optional AI insight
    /// Millions, positive: intersegment sales a GROSS segment stack includes and
    /// consolidation removes (INTC FY2025: $70.5B of segments vs $52.9B of revenue). The
    /// segments' shares of reported revenue add to more than 100% by exactly this much, so
    /// the panel draws it as a negative line. nil for every other stack and older reports.
    /// `var` with a default so existing memberwise inits and previews still compile.
    var intersegmentEliminations: Double? = nil
    /// ISO 4217 code every amount here is reported in ("TWD" for TSM), never converted. nil
    /// when unknown and on reports cached before 2026-10-08. `var` with a default for the
    /// same reason as `intersegmentEliminations`.
    var reportingCurrency: String? = nil

    /// The eliminations line, when there is one to draw (finite and positive).
    var hasEliminations: Bool {
        guard let e = intersegmentEliminations else { return false }
        return e.isFinite && e > 0
    }

    /// "-$17.7B" — the same M / B / T tiers and currency as the segment rows.
    var formattedEliminations: String {
        guard let e = intersegmentEliminations, e.isFinite else { return "—" }
        return "-" + formatMillions(e)
    }

    /// A segment's amount: "$12.5B", or "TWD 2.90T" for a non-USD filer.
    func formattedRevenue(for segment: RevenueSegment) -> String {
        formatMillions(segment.currentRevenue)
    }

    /// "-33%" of the reported total, so the column visibly adds back to 100%.
    var formattedEliminationsPercentage: String {
        guard let e = intersegmentEliminations, e.isFinite, totalRevenue > 0 else { return "—" }
        return String(format: "-%.0f%%", e / totalRevenue * 100)
    }

    /// Every amount on the card goes through here. Backend emits MILLIONS, so:
    ///   < 1,000        → "$NM"    (e.g., $57M company)
    ///   < 1,000,000    → "$N.NB"  (e.g., $57,230M = "$57.2B")
    ///   ≥ 1,000,000    → "$N.NNT" (e.g., $1,200,000M = "$1.20T")
    /// with `moneyPrefix` in place of "$" ("TWD 3.81T").
    private func formatMillions(_ value: Double) -> String {
        let prefix = Self.moneyPrefix(reportingCurrency)
        if value >= 1_000_000 {
            return prefix + String(format: "%.2fT", value / 1_000_000)
        } else if value >= 1000 {
            return prefix + String(format: "%.1fB", value / 1000)
        } else {
            return prefix + String(format: "%.0fM", value)
        }
    }

    // MARK: - Reporting Currency

    /// An ISO-4217-shaped code ("USD", "TWD") or nil — never a guess, never defaulted to USD.
    /// Mirrors the backend's `app/utils/currency.py::currency_code`: trimmed, then exactly
    /// three ASCII letters, then upper-cased. " twd " → "TWD"; "US$", "N/A", "", "USDT" and
    /// non-ASCII letters ("ÜSD") → nil.
    static func currencyCode(_ raw: String?) -> String? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines) else { return nil }
        let scalars = trimmed.unicodeScalars
        guard scalars.count == 3, scalars.allSatisfy({ asciiLetters.contains($0) }) else { return nil }
        return trimmed.uppercased()
    }

    /// A–Z and a–z only: `CharacterSet.letters` would admit "Ü".
    private static let asciiLetters: CharacterSet =
        CharacterSet(charactersIn: "A"..."Z").union(CharacterSet(charactersIn: "a"..."z"))

    /// What goes before an amount: "$" for US dollars AND for an unknown currency (the
    /// card's behaviour before the currency was carried), else the code and a space
    /// ("TWD "), so a non-USD filer's figure is never dressed as dollars. Mirrors the
    /// backend's `money_prefix`.
    static func moneyPrefix(_ currency: String?) -> String {
        guard let code = currencyCode(currency), code != "USD" else { return "$" }
        return code + " "
    }

    // MARK: - Role Assignment Logic

    /// Automatically assigns roles to segments based on their characteristics
    func roleForSegment(_ segment: RevenueSegment) -> RevenueSegmentRole {
        guard !segments.isEmpty else { return .diversified }

        // Growth-based roles are computed over segments that actually HAVE a prior
        // year. A segment with no anchor reports growth 0, which would otherwise let
        // it win "most negative growth" against a field of real decliners.
        let anchored = segments.filter { $0.hasPriorAnchor }
        let maxGrowth = anchored.map { $0.growth }.max() ?? 0
        let minGrowth = anchored.map { $0.growth }.min() ?? 0
        let maxRevenue = segments.map { $0.currentRevenue }.max() ?? 0

        // Rule 1: Rising Segment - highest growth (and growth > 10%)
        if segment.hasPriorAnchor && segment.growth == maxGrowth && segment.growth > 0.10 {
            return .risingSegment
        }

        // Rule 2: Headwind - most negative growth (and growth < -5%)
        if segment.hasPriorAnchor && segment.growth == minGrowth && segment.growth < -0.05 {
            return .headwind
        }

        // Rule 3: Core Business - largest revenue
        if segment.currentRevenue == maxRevenue {
            return .coreBusiness
        }

        // Rule 4: Diversified - everything else
        return .diversified
    }

    /// Total revenue formatted — same tiers and currency as the segment rows.
    var formattedTotalRevenue: String {
        formatMillions(totalRevenue)
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

    /// A GROSS stack (illustrative, not market data): the segments include sales between
    /// the company's own segments, so their shares of reported revenue add to ~134% and
    /// the eliminations line brings the column back to 100%.
    static let sampleGross = ReportRevenueEngineData(
        segments: [
            RevenueSegment(name: "Client Products", currentRevenue: 32_200, previousRevenue: 30_300, totalRevenue: 52_900),
            RevenueSegment(name: "Foundry Services", currentRevenue: 17_800, previousRevenue: 18_900, totalRevenue: 52_900),
            RevenueSegment(name: "Data Center", currentRevenue: 16_900, previousRevenue: 15_500, totalRevenue: 52_900),
            RevenueSegment(name: "Other", currentRevenue: 3_600, previousRevenue: 3_500, totalRevenue: 52_900)
        ],
        totalRevenue: 52_900,
        revenueUnit: "Millions",
        period: "FY 2025",
        analysisNote: nil,
        intersegmentEliminations: 17_600
    )

    /// A non-USD filer (illustrative, not market data): every amount carries the reporting
    /// currency's code instead of "$" ("TWD 2.90T").
    static let sampleForeignCurrency = ReportRevenueEngineData(
        segments: [
            RevenueSegment(name: "Wafers", currentRevenue: 2_900_000, previousRevenue: 2_300_000, totalRevenue: 3_800_000),
            RevenueSegment(name: "Other", currentRevenue: 900_000, previousRevenue: 850_000, totalRevenue: 3_800_000)
        ],
        totalRevenue: 3_800_000,
        revenueUnit: "Millions",
        period: "FY 2025",
        analysisNote: nil,
        reportingCurrency: "TWD"
    )
}
