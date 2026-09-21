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
//  Bucketing mirrors the backend too: a coin is one "Crypto" slice in BOTH donuts and
//  never enters the equity size mix (the Tracking feed hands this mirror CoinGecko's cap
//  for a coin, which would otherwise file Dogecoin under "Large Cap"), and a placeholder
//  sector such as "N/A" folds into "Other" instead of becoming a bucket of its own —
//  the literal "N/A 0%" legend row a tester saw in 1.0 (8). The literal set below is
//  pinned against the backend's `PLACEHOLDER_TEXT` by test_ios_diversification_guards.py.
//

import Foundation

/// Shared thresholds for the diversification feature.
enum DiversificationThresholds {
    /// Minimum holdings required for a meaningful score (matches the backend
    /// `MIN_HOLDINGS`). Below this the card shows the "add at least N" hint.
    static let minimumHoldings = 2
    /// Below this many SCORED holdings the card adds a one-line "add more tickers" hint:
    /// with 2–4 positions a normalized-HHI score is low by construction, and the verdict
    /// alone ("Highly concentrated") told the user nothing they could act on
    /// (TestFlight 1.0 (7)). Strictly above `minimumHoldings`, which is the floor the
    /// score needs at all.
    static let smallBookHoldings = 5
}

/// The one informational line under the Diversification verdict.
///
/// Derived on the client, not sent by the server: the server's `message` is a neutral
/// descriptor by decision (June 2026 — every nudge was removed from the card), and the
/// inputs this needs are all client-side. The copy is a DATA-ENTRY instruction — what to
/// enter or add to the tracker — never a buy/sell/sector recommendation, because the
/// disclaimer three rows down says the card is not personalised investment advice.
/// `test_ios_diversification_guards.py` pins the vocabulary.
enum DiversificationHint {
    /// First match wins; nil when the card needs no hint.
    ///
    /// - `scoredHoldings`: what the score was computed over (server `holdings_count`,
    ///   or the calculator's `n` offline).
    /// - `enteredTickers`: tickers in the active group with shares or an amount entered.
    /// - `totalTickers`: tickers in the active group.
    static func make(scoredHoldings: Int, enteredTickers: Int, totalTickers: Int) -> String? {
        let gap = max(0, totalTickers - enteredTickers)
        let small = scoredHoldings < DiversificationThresholds.smallBookHoldings
        let holdingsNoun = scoredHoldings == 1 ? "holding" : "holdings"
        if small && gap > 0 {
            let others = gap == 1 ? "your other ticker" : "your other \(gap) tickers"
            return "Scored on \(scoredHoldings) \(holdingsNoun) — enter shares or an amount for \(others), or add more, to give the score more to measure."
        }
        if small {
            return "Scored on \(scoredHoldings) \(holdingsNoun) — add more tickers with shares or an amount to give the score more to measure."
        }
        if gap > 0 {
            let subject = gap == 1 ? "1 ticker isn't" : "\(gap) tickers aren't"
            let object = gap == 1 ? "it" : "them"
            return "\(subject) counted yet — enter shares or an amount to include \(object)."
        }
        return nil
    }
}

struct DiversificationCalculator {

    /// The label a coin gets in both donuts (backend `CRYPTO_BUCKET`).
    static let cryptoBucket = "Crypto"
    /// Strings that mean "unknown" and must never become a bucket (backend
    /// `PLACEHOLDER_TEXT`, lower-cased, trimmed). Deliberately WITHOUT "unknown", which
    /// is the size label emitted for a capless equity.
    static let placeholderText: Set<String> = [
        "", "n/a", "na", "n.a.", "none", "null", "nan", "-", "\u{2014}", "\u{2013}",
    ]

    /// Sector-donut bucket for one holding: coin → "Crypto"; missing/placeholder → "Other".
    static func sectorBucket(_ h: PortfolioHolding) -> String {
        if h.assetType == .crypto { return cryptoBucket }
        let sector = (h.sector ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if placeholderText.contains(sector.lowercased()) { return "Other" }
        return sector
    }

    /// Size-donut bucket: coin → "Crypto"; else the cap bucket, or "Unknown".
    static func sizeBucket(_ h: PortfolioHolding) -> String {
        if h.assetType == .crypto { return cryptoBucket }
        return capBucket(h.marketCap) ?? "Unknown"
    }

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
            sectorWeights[sectorBucket(h), default: 0] += h.weight
        }
        let sectorQ = normalizedHHI(Array(sectorWeights.values), sectorWeights.count)

        // Market-cap mix (scored only over holdings with a known cap).
        var capWeights: [String: Double] = [:]
        var knownCapWeight = 0.0
        for h in weighted {
            // A coin never enters the equity size mix, whatever cap the feed carried for it.
            guard h.assetType != .crypto else { continue }
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

        // Size donut: bucket every holding (coins as "Crypto", unknown caps as "Unknown").
        var capAlloc: [String: Double] = [:]
        for h in weighted {
            capAlloc[sizeBucket(h), default: 0] += h.weight
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
            marketcapAllocations: marketcapAllocations,
            holdingsCount: n
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
