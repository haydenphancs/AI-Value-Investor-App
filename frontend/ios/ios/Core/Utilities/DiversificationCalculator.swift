//
//  DiversificationCalculator.swift
//  ios
//
//  OFFLINE FALLBACK for the diversification score. The server is the source of
//  truth (GET /portfolios/{id}/insights); this mirrors its additive-points
//  model closely enough to render the card when the network call fails.
//
//  Each dimension earns points = quality × maxPoints; the budgets sum to 100,
//  so the bars add up to the overall score. Budgets match the backend:
//  position 40 / sector 40 / market-cap 20 when market cap is a measurable
//  signal (≥2 buckets, ≥half the book priced), else market-cap's share folds
//  into position 50 / sector 50. Geography is excluded (US-only).
//

import Foundation

/// Shared thresholds for the diversification feature.
enum DiversificationThresholds {
    /// Minimum holdings required for a meaningful score (matches the backend
    /// `MIN_HOLDINGS`). Below this the card shows the "add at least N" hint.
    static let minimumHoldings = 2
}

struct DiversificationCalculator {

    /// Calculate the offline diversification score. Returns `nil` below the
    /// minimum holdings or when the total value is non-positive.
    static func calculate(holdings rawHoldings: [PortfolioHolding]) -> DiversificationScore? {
        // Drop zero/negative-value positions before weighting (mirrors backend
        // score_holdings): a failed price lookup can leave marketValue == 0,
        // which would inflate n and deflate the normalized-HHI denominators.
        let holdings = rawHoldings.filter { $0.marketValue > 0 }
        guard holdings.count >= DiversificationThresholds.minimumHoldings else {
            return nil
        }

        let totalValue = holdings.reduce(0.0) { $0 + $1.marketValue }
        guard totalValue > 0 else { return nil }

        var weighted = holdings
        for i in weighted.indices {
            weighted[i].weight = weighted[i].marketValue / totalValue
        }
        let weights = weighted.map { $0.weight }
        let n = weights.count

        // ── Per-dimension quality (0–100) ──────────────────────────────
        let positionQ = normalizedHHI(weights, n)

        var sectorWeights: [String: Double] = [:]
        for h in weighted {
            sectorWeights[h.sector ?? "Other", default: 0] += h.weight
        }
        let sectorQ = normalizedHHI(Array(sectorWeights.values), sectorWeights.count)

        // Market-cap mix (scored only over holdings with a known cap).
        var capWeights: [String: Double] = [:]
        var knownCapWeight = 0.0
        for h in weighted {
            if let bucket = capBucket(h.marketCap) {
                capWeights[bucket, default: 0] += h.weight
                knownCapWeight += h.weight
            }
        }
        // Only score market-cap MIX when it's a measurable signal: at least two
        // distinct buckets AND at least half the book priced (mirrors backend).
        // Gating on "any single holding has a cap" was non-monotonic (one cap
        // datum could drop the score) and unfairly capped a single-bucket
        // blue-chip book at 80.
        let marketcapAvailable = capWeights.count >= 2 && knownCapWeight >= 0.5
        let marketcapQ = marketcapAvailable
            ? normalizedHHI(capWeights.values.map { $0 / knownCapWeight }, capWeights.count)
            : 0.0

        // ── Additive points (budgets sum to 100; bars add up to the score) ──
        // Position Balance (normalized HHI) already captures single-name
        // concentration, so there's no separate concentration bar. Cap present
        // → 40/40/20; absent → 50/50.
        var budgets: [(key: String, label: String, quality: Double, max: Int)] = [
            ("position", "Position Balance", positionQ, marketcapAvailable ? 40 : 50),
            ("sector", "Sector Spread", sectorQ, marketcapAvailable ? 40 : 50),
        ]
        if marketcapAvailable {
            budgets.append(("marketcap", "Market-Cap Mix", marketcapQ, 20))
        }

        var subScores: [DiversificationSubScore] = []
        var total = 0
        for b in budgets {
            let pts = max(0, min(b.max, Int((b.quality / 100.0 * Double(b.max)).rounded())))
            total += pts
            let ratio = b.max > 0 ? Int((Double(pts) / Double(b.max) * 100).rounded()) : 0
            subScores.append(DiversificationSubScore(
                key: b.key, label: b.label, points: pts, maxPoints: b.max, zone: zone(for: ratio)
            ))
        }
        total = max(0, min(100, total))

        let hhi = weights.reduce(0.0) { $0 + $1 * $1 }
        let effectiveHoldings = hhi > 0 ? 1.0 / hhi : 0.0

        let sectorAllocations = sectorWeights
            .sorted { $0.value > $1.value }
            .map { SectorAllocation(name: $0.key, percentage: $0.value * 100.0) }

        // Size donut: bucket every holding (unknown caps grouped as "Unknown").
        var capAlloc: [String: Double] = [:]
        for h in weighted {
            capAlloc[capBucket(h.marketCap) ?? "Unknown", default: 0] += h.weight
        }
        let marketcapAllocations = capAlloc
            .sorted { $0.value > $1.value }
            .map { SectorAllocation(name: $0.key, percentage: $0.value * 100.0) }

        return DiversificationScore(
            score: total,
            zone: zone(for: total),
            effectiveHoldings: effectiveHoldings,
            message: message(for: total),
            sectorCount: sectorAllocations.count,
            subScores: subScores,
            sectorAllocations: sectorAllocations,
            marketcapAllocations: marketcapAllocations
        )
    }

    // MARK: - Math helpers (mirror the backend)

    /// Normalized HHI quality: 100 = perfectly even across the `n` buckets,
    /// 0 = fully concentrated in one.
    private static func normalizedHHI(_ weights: [Double], _ n: Int) -> Double {
        guard n > 1 else { return 0 }
        let hhi = weights.reduce(0.0) { $0 + $1 * $1 }
        let minHHI = 1.0 / Double(n)
        guard 1.0 - minHHI > 0 else { return 100 }
        let norm = (hhi - minHHI) / (1.0 - minHHI)
        return max(0.0, min(100.0, (1.0 - norm) * 100.0))
    }

    /// Market-cap bucket (USD cutoffs mirror the backend).
    private static func capBucket(_ marketCap: Double?) -> String? {
        guard let mc = marketCap, mc > 0 else { return nil }
        if mc >= 200_000_000_000 { return "Mega Cap" }
        if mc >= 10_000_000_000 { return "Large Cap" }
        if mc >= 2_000_000_000 { return "Mid Cap" }
        return "Small Cap"
    }

    private static func zone(for ratio: Int) -> String {
        switch ratio {
        case 70...:   return "green"
        case 40..<70: return "yellow"
        default:      return "red"
        }
    }

    private static func message(for score: Int) -> String {
        switch score {
        case 85...:   return "Excellent diversification"
        case 70..<85: return "Well diversified"
        case 55..<70: return "Moderately diversified"
        case 40..<55: return "Somewhat concentrated"
        default:      return "Highly concentrated"
        }
    }
}
