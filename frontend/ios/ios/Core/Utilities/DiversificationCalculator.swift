//
//  DiversificationCalculator.swift
//  ios
//
//  Pure-function scoring engine using the "Robo-Advisor" three-bucket rubric.
//
//  Bucket 1 — Single Asset Concentration (40 pts)
//  Bucket 2 — Sector Weighting (40 pts)
//  Bucket 3 — Asset Class & Geography (20 pts)
//
//  Total = 100 points. Deductions for concentration risk.
//

import Foundation

// MARK: - Thresholds

/// Tunable constants for the diversification scoring algorithm.
enum DiversificationThresholds {

    // ── Bucket 1: Single Asset Concentration ──────────────────────
    static let singleAssetHealthyLimit: Double = 0.10       // 10%
    static let singleAssetSevereLimit: Double = 0.30        // 30%
    static let concentrationPenaltyPerPct: Double = 1.0
    static let concentrationSevereMultiplier: Double = 1.5

    // ── Bucket 2: Sector Weighting ────────────────────────────────
    static let sectorHealthyLimit: Double = 0.25            // 25%
    static let sectorSevereLimit: Double = 0.50             // 50%
    static let sectorPenaltyPerPct: Double = 0.8
    static let sectorSevereMultiplier: Double = 1.5

    // ── Bucket 3: Asset Class & Geography ─────────────────────────
    static let pointsPerAssetClass: Double = 4.0
    static let classPointsCap: Double = 12.0
    static let internationalExposureBonus: Double = 5.0
    static let internationalSignificantBonus: Double = 3.0
    static let internationalSignificantThreshold: Double = 0.20

    // ── Bucket Maximums ───────────────────────────────────────────
    static let bucket1Max: Double = 40.0
    static let bucket2Max: Double = 40.0
    static let bucket3Max: Double = 20.0

    // ── Minimum holdings to show a meaningful score ───────────────
    static let minimumHoldings: Int = 2
}

// MARK: - Calculator

/// Pure-function scoring engine. Takes portfolio holdings, returns a
/// `DiversificationScore`. All methods are static and side-effect free.
///
/// Usage:
/// ```swift
/// let score = DiversificationCalculator.calculate(holdings: myHoldings)
/// ```
struct DiversificationCalculator {

    // MARK: - Main Entry Point

    /// Calculate the overall diversification score from a list of holdings.
    /// Returns `nil` if the portfolio has fewer than `minimumHoldings` positions.
    static func calculate(holdings: [PortfolioHolding]) -> DiversificationScore? {
        guard holdings.count >= DiversificationThresholds.minimumHoldings else {
            return nil
        }

        let totalValue = holdings.reduce(0.0) { $0 + $1.marketValue }
        guard totalValue > 0 else { return nil }

        // Compute weights
        var weighted = holdings
        for i in weighted.indices {
            weighted[i].weight = weighted[i].marketValue / totalValue
        }

        // Calculate each bucket
        let bucket1 = calculateConcentrationScore(weighted)
        let bucket2 = calculateSectorScore(weighted)
        let bucket3 = calculateDiversityScore(weighted)

        let totalScore = Int(round(bucket1 + bucket2 + bucket3))
        let clampedScore = max(0, min(100, totalScore))

        // Build sector allocations for the donut chart
        let allocations = buildSectorAllocations(weighted)

        // Generate context-aware message
        let message = generateMessage(
            score: clampedScore,
            sectorCount: allocations.count,
            holdings: weighted
        )

        let subScores = DiversificationSubScores(
            concentrationScore: Int(round(bucket1)),
            sectorScore: Int(round(bucket2)),
            diversityScore: Int(round(bucket3))
        )

        return DiversificationScore(
            score: clampedScore,
            message: message,
            sectorCount: allocations.count,
            allocations: allocations,
            subScores: subScores
        )
    }

    // MARK: - Bucket 1: Single Asset Concentration (40 points)

    /// Start with 40 points. Deduct for each holding exceeding the healthy limit.
    /// Penalty accelerates above the severe limit.
    private static func calculateConcentrationScore(
        _ holdings: [PortfolioHolding]
    ) -> Double {
        let T = DiversificationThresholds.self
        var score = T.bucket1Max

        for holding in holdings {
            let excess = holding.weight - T.singleAssetHealthyLimit
            guard excess > 0 else { continue }

            if holding.weight > T.singleAssetSevereLimit {
                // Two-zone penalty: normal zone + severe zone
                let normalExcess = (T.singleAssetSevereLimit - T.singleAssetHealthyLimit) * 100.0
                let severeExcess = (holding.weight - T.singleAssetSevereLimit) * 100.0

                let penalty = (normalExcess * T.concentrationPenaltyPerPct)
                    + (severeExcess * T.concentrationPenaltyPerPct * T.concentrationSevereMultiplier)
                score -= penalty
            } else {
                // Linear penalty zone
                let excessPct = excess * 100.0
                score -= excessPct * T.concentrationPenaltyPerPct
            }
        }

        return max(0, score)
    }

    // MARK: - Bucket 2: Sector Weighting (40 points)

    /// Start with 40 points. Deduct for each sector exceeding the healthy limit.
    /// Penalty accelerates above the severe limit.
    private static func calculateSectorScore(
        _ holdings: [PortfolioHolding]
    ) -> Double {
        let T = DiversificationThresholds.self
        var score = T.bucket2Max

        // Group by sector
        let sectorWeights = Dictionary(grouping: holdings) { $0.sector ?? "Other" }
            .mapValues { group in group.reduce(0.0) { $0 + $1.weight } }

        for (_, weight) in sectorWeights {
            let excess = weight - T.sectorHealthyLimit
            guard excess > 0 else { continue }

            if weight > T.sectorSevereLimit {
                let normalExcess = (T.sectorSevereLimit - T.sectorHealthyLimit) * 100.0
                let severeExcess = (weight - T.sectorSevereLimit) * 100.0

                let penalty = (normalExcess * T.sectorPenaltyPerPct)
                    + (severeExcess * T.sectorPenaltyPerPct * T.sectorSevereMultiplier)
                score -= penalty
            } else {
                let excessPct = excess * 100.0
                score -= excessPct * T.sectorPenaltyPerPct
            }
        }

        return max(0, score)
    }

    // MARK: - Bucket 3: Asset Class & Geography (20 points)

    /// Additive scoring. Points for asset class diversity + international exposure.
    private static func calculateDiversityScore(
        _ holdings: [PortfolioHolding]
    ) -> Double {
        let T = DiversificationThresholds.self
        var score: Double = 0.0

        // Asset class diversity: up to classPointsCap (12) points
        let distinctClasses = Set(holdings.map { $0.assetType.assetClass })
        let classPoints = min(
            Double(distinctClasses.count) * T.pointsPerAssetClass,
            T.classPointsCap
        )
        score += classPoints

        // Geographic diversity
        let internationalWeight = holdings
            .filter { $0.country != "US" }
            .reduce(0.0) { $0 + $1.weight }

        if internationalWeight > 0 {
            score += T.internationalExposureBonus
            if internationalWeight >= T.internationalSignificantThreshold {
                score += T.internationalSignificantBonus
            }
        }

        return min(score, T.bucket3Max)
    }

    // MARK: - Sector Allocations (for Donut Chart)

    private static func buildSectorAllocations(
        _ holdings: [PortfolioHolding]
    ) -> [SectorAllocation] {
        Dictionary(grouping: holdings) { $0.sector ?? "Other" }
            .mapValues { group in group.reduce(0.0) { $0 + $1.weight } }
            .sorted { $0.value > $1.value }
            .map { SectorAllocation(name: $0.key, percentage: $0.value * 100.0) }
    }

    // MARK: - Message Generation

    private static func generateMessage(
        score: Int,
        sectorCount: Int,
        holdings: [PortfolioHolding]
    ) -> String {
        switch score {
        case 80...100:
            return "Excellent diversification across \(sectorCount) sectors"
        case 60..<80:
            return "Well-diversified across \(sectorCount) sectors"
        case 40..<60:
            if let top = holdings.max(by: { $0.weight < $1.weight }), top.weight > 0.25 {
                return "Consider reducing \(top.ticker) concentration (\(Int(top.weight * 100))%)"
            }
            return "Moderate diversification — consider spreading across more sectors"
        default:
            return "High concentration risk — diversify across more sectors and asset classes"
        }
    }
}
