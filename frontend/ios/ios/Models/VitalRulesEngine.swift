//
//  VitalRulesEngine.swift
//  ios
//
//  Rules Engine: Evaluates raw financial data and determines which Key Vitals
//  are important enough to surface. Uses a Value Investor mindset — downside
//  risks are weighted heavier than upside potential.
//
//  Each evaluation function returns a VitalEvaluation containing:
//    - score (0.0-10.0): Internal importance ranking (never displayed)
//    - status: The user-facing badge (Critical / Warning / Good / Neutral)
//    - headline: Short explanation shown to the user
//    - shouldSurface: Whether this card is noteworthy enough to display
//

import SwiftUI

// MARK: - Vital Status

/// The user-facing status displayed on each vital card badge.
/// Neutral vitals are filtered out — they never reach the UI.
enum VitalStatus: String {
    case critical = "Critical"
    case warning  = "Warning"
    case good     = "Good"
    case neutral  = "Neutral"

    var color: Color {
        switch self {
        case .critical: return AppColors.bearish
        case .warning:  return AppColors.neutral
        case .good:     return AppColors.bullish
        case .neutral:  return AppColors.textSecondary
        }
    }

    var backgroundColor: Color {
        color.opacity(0.15)
    }

    var iconName: String {
        switch self {
        case .critical: return "exclamationmark.triangle.fill"
        case .warning:  return "exclamationmark.circle.fill"
        case .good:     return "checkmark.seal.fill"
        case .neutral:  return "minus.circle.fill"
        }
    }
}

// MARK: - Vital Evaluation Result

/// Internal result from the rules engine. The score is used for priority
/// sorting only — it is never shown in the UI.
struct VitalEvaluation {
    let score: Double           // 0.0-10.0 (importance)
    let status: VitalStatus
    let headline: String        // "Bankruptcy Risk", "Deep Value", etc.

    var shouldSurface: Bool {
        status != .neutral
    }

    /// Convenience for "nothing noteworthy" — won't surface.
    static let hidden = VitalEvaluation(score: 0.0, status: .neutral, headline: "")
}

// MARK: - Smart Thresholds
// Constants grounded in standard market analysis and academic research.

enum VitalThresholds {

    // ── Valuation ────────────────────────────────────────────────────
    /// PE < 50% of sector average → deep discount
    static let DEEP_VALUE_PE_RATIO: Double      = 0.50
    /// PE < 70% of sector average → undervalued
    static let UNDERVALUED_PE_RATIO: Double     = 0.70
    /// PE > 150% of sector average → overvalued
    static let OVERVALUED_PE_RATIO: Double      = 1.50
    /// PE > 200% of sector average → bubble territory
    static let BUBBLE_PE_RATIO: Double          = 2.00
    /// Fair-value upside > 30% → deep value opportunity
    static let DEEP_VALUE_UPSIDE: Double        = 30.0
    /// Fair-value downside > 20% → overvaluation risk
    static let SIGNIFICANT_DOWNSIDE: Double     = -20.0

    // ── Financial Health ─────────────────────────────────────────────
    /// Altman Z < 1.1 → imminent default risk (academic bankruptcy zone)
    static let ALTMAN_IMMINENT_DEFAULT: Double  = 1.1
    /// Altman Z < 1.8 → distress zone (original Altman cutoff)
    static let ALTMAN_DISTRESS: Double          = 1.8
    /// Altman Z > 3.0 → safe zone
    static let ALTMAN_SAFE: Double              = 3.0
    /// Altman Z > 4.5 → fortress balance sheet
    static let ALTMAN_FORTRESS: Double          = 4.5
    /// Current ratio < 1.0 → can't cover short-term obligations
    static let DANGEROUS_CURRENT_RATIO: Double  = 1.0
    /// D/E > 2.5 → dangerously leveraged
    static let DANGEROUS_DEBT_RATIO: Double     = 2.5
    /// D/E > 5.0 → extreme leverage
    static let EXTREME_DEBT_RATIO: Double       = 5.0

    // ── Revenue ──────────────────────────────────────────────────────
    /// YoY growth > 25% → hyper-growth
    static let HYPER_GROWTH_THRESHOLD: Double   = 0.25
    /// Segment growth > 40% → breakout segment
    static let SEGMENT_BREAKOUT: Double         = 0.40
    /// YoY growth < -5% → revenue decline
    static let DECLINE_THRESHOLD: Double        = -0.05
    /// YoY growth < -15% → revenue collapse
    static let REVENUE_COLLAPSE: Double         = -0.15

    // ── Insider Activity ─────────────────────────────────────────────
    /// > 80% of transactions are sells → insider dumping
    static let INSIDER_DUMPING_RATIO: Double    = 0.80
    /// > 65% of transactions are sells → notable selling pressure
    static let INSIDER_HEAVY_SELL_RATIO: Double = 0.65
    /// > 70% of transactions are buys → strong insider conviction
    static let INSIDER_STRONG_BUY_RATIO: Double = 0.70
    /// Minimum total transactions to consider signal meaningful
    static let INSIDER_MIN_TRANSACTIONS: Int    = 3

    // ── Moat ─────────────────────────────────────────────────────────
    /// Max dimension score >= 8.5 → wide moat (per MoatOverallRating)
    static let WIDE_MOAT_SCORE: Double          = 8.5
    /// Max dimension score < 5.0 → no structural advantage
    static let NO_MOAT_CEILING: Double          = 5.0

    // ── Macro ────────────────────────────────────────────────────────
    /// 3+ risk factors at elevated-or-above → concentrated risk
    static let HIGH_RISK_COUNT_THRESHOLD: Int   = 3

    // ── Forecast ─────────────────────────────────────────────────────
    /// CAGR > 20% → exceptional growth trajectory
    static let EXCEPTIONAL_CAGR: Double         = 0.20
    /// Negative EPS CAGR → earnings deterioration
    static let NEGATIVE_GROWTH: Double          = 0.0

    // ── Wall Street ──────────────────────────────────────────────────
    /// Price target > 40% above current → massive upside consensus
    static let MASSIVE_UPSIDE_PCT: Double       = 40.0
    /// Price target > 25% above current → significant upside
    static let SIGNIFICANT_UPSIDE_PCT: Double   = 25.0
    /// Price target > 20% below current → analysts see downside
    static let SIGNIFICANT_WS_DOWNSIDE: Double  = -20.0
    /// Downgrades > 2x upgrades → downgrade wave
    static let DOWNGRADE_WAVE_RATIO: Double     = 2.0

    // ── Risk Weighting ───────────────────────────────────────────────
    /// Downside risks score 30% higher than equivalent upside signals.
    /// A Value Investor loses more from one bankruptcy than they gain
    /// from one 30% winner. This asymmetry must be reflected.
    static let RISK_WEIGHT_MULTIPLIER: Double   = 1.30
}

// MARK: - Vital Rules Engine

/// Pure-function evaluation engine. Every method is static and side-effect
/// free. Feed it raw financial data, get back a prioritized evaluation.
struct VitalRulesEngine {

    // MARK: - Risk Weight Helper

    /// Apply the Value Investor risk premium — downside signals get amplified.
    private static func weightRisk(_ baseScore: Double) -> Double {
        min(baseScore * VitalThresholds.RISK_WEIGHT_MULTIPLIER, 10.0)
    }

    // MARK: - Valuation

    /// Surfaces when the stock is deeply discounted (opportunity) OR trading
    /// at a dangerous premium (bubble). Fair-value stocks are not noteworthy.
    ///
    /// - Parameters:
    ///   - pe: Trailing P/E ratio. Pass nil if earnings are negative.
    ///   - sectorPE: Sector median P/E for comparison.
    ///   - currentPrice: Current market price.
    ///   - fairValue: Analyst / model fair value estimate.
    static func evaluateValuation(
        pe: Double?,
        sectorPE: Double?,
        currentPrice: Double,
        fairValue: Double
    ) -> VitalEvaluation {
        let upsidePct = currentPrice > 0
            ? ((fairValue - currentPrice) / currentPrice) * 100.0
            : 0.0

        // ── Negative earnings → always critical ──────────────────────
        if let pe = pe, pe < 0 {
            return VitalEvaluation(
                score: weightRisk(9.0),
                status: .critical,
                headline: "Negative Earnings"
            )
        }

        // ── P/E-based checks (if available) ──────────────────────────
        if let pe = pe, let sectorPE = sectorPE, sectorPE > 0 {
            let peRatio = pe / sectorPE

            if peRatio > VitalThresholds.BUBBLE_PE_RATIO {
                return VitalEvaluation(
                    score: weightRisk(8.5),
                    status: .critical,
                    headline: "Bubble Territory"
                )
            }
            if peRatio > VitalThresholds.OVERVALUED_PE_RATIO {
                return VitalEvaluation(
                    score: weightRisk(7.0),
                    status: .warning,
                    headline: "Premium Valuation"
                )
            }
            if peRatio < VitalThresholds.DEEP_VALUE_PE_RATIO {
                return VitalEvaluation(
                    score: 8.0,
                    status: .good,
                    headline: "Deep Value"
                )
            }
            if peRatio < VitalThresholds.UNDERVALUED_PE_RATIO {
                return VitalEvaluation(
                    score: 7.0,
                    status: .good,
                    headline: "Undervalued"
                )
            }
        }

        // ── Price-to-Fair-Value checks ───────────────────────────────
        if upsidePct >= VitalThresholds.DEEP_VALUE_UPSIDE {
            return VitalEvaluation(
                score: 8.0,
                status: .good,
                headline: "Deep Value"
            )
        }
        if upsidePct <= VitalThresholds.SIGNIFICANT_DOWNSIDE {
            return VitalEvaluation(
                score: weightRisk(7.5),
                status: .warning,
                headline: "Overvalued"
            )
        }

        // ── Fair value zone — not noteworthy ─────────────────────────
        return .hidden
    }

    // MARK: - Financial Health

    /// Surfaces when there is bankruptcy risk or extreme leverage.
    /// A Value Investor's #1 rule: Don't lose money. Bankruptcy risk always
    /// surfaces with maximum urgency.
    ///
    /// - Parameters:
    ///   - altmanZ: Altman Z-Score (< 1.8 = distress, > 3.0 = safe).
    ///   - currentRatio: Current assets / current liabilities. Pass nil if unavailable.
    ///   - debtToEquity: Total debt / shareholder equity. Pass nil if unavailable.
    ///   - fcfNegative: Whether free cash flow is negative.
    static func evaluateHealth(
        altmanZ: Double,
        currentRatio: Double? = nil,
        debtToEquity: Double? = nil,
        fcfNegative: Bool = false
    ) -> VitalEvaluation {

        // ── Imminent default ─────────────────────────────────────────
        if altmanZ < VitalThresholds.ALTMAN_IMMINENT_DEFAULT {
            return VitalEvaluation(
                score: 10.0,  // Maximum urgency — no weighting needed
                status: .critical,
                headline: "Bankruptcy Risk"
            )
        }

        // ── Distress zone ────────────────────────────────────────────
        if altmanZ < VitalThresholds.ALTMAN_DISTRESS {
            var score = weightRisk(8.5)
            // Compound risk: distress + negative FCF
            if fcfNegative { score = min(score + 0.5, 10.0) }
            return VitalEvaluation(
                score: score,
                status: .critical,
                headline: "Financial Distress"
            )
        }

        // ── Grey zone + liquidity crunch ─────────────────────────────
        if altmanZ < VitalThresholds.ALTMAN_SAFE {
            if let cr = currentRatio, cr < VitalThresholds.DANGEROUS_CURRENT_RATIO {
                return VitalEvaluation(
                    score: weightRisk(7.5),
                    status: .warning,
                    headline: "Liquidity Concern"
                )
            }
            if let de = debtToEquity, de > VitalThresholds.EXTREME_DEBT_RATIO {
                return VitalEvaluation(
                    score: weightRisk(7.5),
                    status: .warning,
                    headline: "Heavy Debt Load"
                )
            }
        }

        // ── Extreme leverage outside grey zone ───────────────────────
        if let de = debtToEquity, de > VitalThresholds.DANGEROUS_DEBT_RATIO {
            return VitalEvaluation(
                score: weightRisk(6.5),
                status: .warning,
                headline: "Elevated Leverage"
            )
        }

        // ── Fortress balance sheet ───────────────────────────────────
        if altmanZ > VitalThresholds.ALTMAN_FORTRESS {
            if let cr = currentRatio, cr > 2.0 {
                return VitalEvaluation(
                    score: 7.0,
                    status: .good,
                    headline: "Fortress Balance Sheet"
                )
            }
        }

        return .hidden
    }

    // MARK: - Insider Activity

    /// Surfaces when insiders are dumping shares (bearish signal) or
    /// clustering buys (bullish conviction). Balanced activity is noise.
    ///
    /// Insider dumping is weighted as a risk because insiders know more
    /// about the business than any outside analyst.
    ///
    /// - Parameters:
    ///   - buyCount: Number of insider buy transactions in the period.
    ///   - sellCount: Number of insider sell transactions in the period.
    static func evaluateInsider(
        buyCount: Int,
        sellCount: Int
    ) -> VitalEvaluation {
        let total = buyCount + sellCount

        // Not enough data to draw a conclusion
        guard total >= VitalThresholds.INSIDER_MIN_TRANSACTIONS else {
            return .hidden
        }

        let sellRatio = Double(sellCount) / Double(total)
        let buyRatio = Double(buyCount) / Double(total)

        // ── Insider dumping → highest risk signal ────────────────────
        if sellRatio >= VitalThresholds.INSIDER_DUMPING_RATIO {
            return VitalEvaluation(
                score: weightRisk(9.0),
                status: .critical,
                headline: "Insider Dumping"
            )
        }

        // ── Notable selling pressure ─────────────────────────────────
        if sellRatio >= VitalThresholds.INSIDER_HEAVY_SELL_RATIO {
            return VitalEvaluation(
                score: weightRisk(7.0),
                status: .warning,
                headline: "Insider Selling"
            )
        }

        // ── Strong insider buying → conviction signal ────────────────
        if buyRatio >= VitalThresholds.INSIDER_STRONG_BUY_RATIO {
            return VitalEvaluation(
                score: 7.5,
                status: .good,
                headline: "Insider Buying"
            )
        }

        return .hidden
    }

    // MARK: - Moat

    /// Surfaces when the company has a fortress-level moat (opportunity)
    /// or no structural advantage at all (risk). Narrow moats in stable
    /// markets are not unusual enough to surface.
    ///
    /// - Parameters:
    ///   - rating: The computed MoatOverallRating (.wide, .narrow, .none).
    ///   - maxDimensionScore: The highest individual moat dimension score.
    static func evaluateMoat(
        rating: MoatOverallRating,
        maxDimensionScore: Double? = nil
    ) -> VitalEvaluation {
        switch rating {
        case .wide:
            return VitalEvaluation(
                score: 7.5,
                status: .good,
                headline: "Wide Moat"
            )
        case .none:
            return VitalEvaluation(
                score: weightRisk(7.5),
                status: .warning,
                headline: "No Competitive Moat"
            )
        case .narrow:
            // Only surface narrow moats if the max score is barely above
            // the threshold — i.e. the moat is eroding.
            if let max = maxDimensionScore, max < VitalThresholds.NO_MOAT_CEILING {
                return VitalEvaluation(
                    score: weightRisk(6.5),
                    status: .warning,
                    headline: "Eroding Moat"
                )
            }
            return .hidden
        }
    }

    // MARK: - Revenue

    /// Surfaces when revenue is in hyper-growth (opportunity), has a
    /// breakout segment, or is declining/collapsing (risk).
    /// Steady 5-15% growers are the market norm — not noteworthy.
    ///
    /// - Parameters:
    ///   - revenueGrowthYoY: Year-over-year revenue growth as a decimal (0.18 = 18%).
    ///   - topSegmentGrowth: Growth rate of the fastest-growing segment. Pass nil if unavailable.
    static func evaluateRevenue(
        revenueGrowthYoY: Double,
        topSegmentGrowth: Double? = nil
    ) -> VitalEvaluation {

        // ── Revenue collapse → critical ──────────────────────────────
        if revenueGrowthYoY <= VitalThresholds.REVENUE_COLLAPSE {
            return VitalEvaluation(
                score: weightRisk(9.0),
                status: .critical,
                headline: "Revenue Collapse"
            )
        }

        // ── Revenue decline → warning ────────────────────────────────
        if revenueGrowthYoY <= VitalThresholds.DECLINE_THRESHOLD {
            return VitalEvaluation(
                score: weightRisk(7.5),
                status: .warning,
                headline: "Revenue Decline"
            )
        }

        // ── Hyper-growth → good ──────────────────────────────────────
        if revenueGrowthYoY >= VitalThresholds.HYPER_GROWTH_THRESHOLD {
            return VitalEvaluation(
                score: 8.0,
                status: .good,
                headline: "Hyper-Growth"
            )
        }

        // ── Breakout segment even if overall growth is moderate ──────
        if let segGrowth = topSegmentGrowth,
           segGrowth >= VitalThresholds.SEGMENT_BREAKOUT {
            return VitalEvaluation(
                score: 7.0,
                status: .good,
                headline: "Segment Breakout"
            )
        }

        return .hidden
    }

    // MARK: - Macro & Geopolitical

    /// Surfaces when macro threats are severe/critical or when multiple
    /// risk factors are concentrated. Benign macro environments are the
    /// baseline expectation — not worth a card.
    ///
    /// - Parameters:
    ///   - threatLevel: The overall threat level for this stock.
    ///   - riskCount: Number of risk factors at "elevated" or above.
    ///   - hasWorseningTrend: Whether the dominant risk trend is worsening.
    static func evaluateMacro(
        threatLevel: ThreatLevel,
        riskCount: Int,
        hasWorseningTrend: Bool = false
    ) -> VitalEvaluation {

        switch threatLevel {
        case .critical:
            return VitalEvaluation(
                score: weightRisk(9.5),
                status: .critical,
                headline: "Severe Macro Risk"
            )
        case .severe:
            return VitalEvaluation(
                score: weightRisk(8.0),
                status: .critical,
                headline: "High Macro Risk"
            )
        case .high:
            let score = hasWorseningTrend ? weightRisk(7.5) : weightRisk(7.0)
            return VitalEvaluation(
                score: score,
                status: .warning,
                headline: hasWorseningTrend ? "Worsening Macro" : "Macro Headwinds"
            )
        case .elevated:
            if riskCount >= VitalThresholds.HIGH_RISK_COUNT_THRESHOLD && hasWorseningTrend {
                return VitalEvaluation(
                    score: weightRisk(6.5),
                    status: .warning,
                    headline: "Concentrated Risk"
                )
            }
            return .hidden
        case .low:
            return .hidden
        }
    }

    // MARK: - Forecast

    /// Surfaces when management has lowered guidance (risk) or when the
    /// growth trajectory is exceptional with raised guidance (opportunity).
    /// "Maintained" guidance with moderate growth is the norm.
    ///
    /// - Parameters:
    ///   - revenueCAGR: Projected revenue CAGR as a decimal (0.15 = 15%).
    ///   - epsCAGR: Projected EPS CAGR as a decimal (0.18 = 18%).
    ///   - guidance: Management's latest guidance direction.
    static func evaluateForecast(
        revenueCAGR: Double,
        epsCAGR: Double,
        guidance: ManagementGuidance
    ) -> VitalEvaluation {

        // ── Lowered guidance + shrinking earnings → critical ─────────
        if guidance == .lowered && epsCAGR <= VitalThresholds.NEGATIVE_GROWTH {
            return VitalEvaluation(
                score: weightRisk(8.5),
                status: .critical,
                headline: "Guidance Cut + Shrinking EPS"
            )
        }

        // ── Lowered guidance alone → warning ─────────────────────────
        if guidance == .lowered {
            return VitalEvaluation(
                score: weightRisk(7.5),
                status: .warning,
                headline: "Guidance Lowered"
            )
        }

        // ── Exceptional trajectory with raised guidance → good ───────
        if guidance == .raised &&
           (revenueCAGR >= VitalThresholds.EXCEPTIONAL_CAGR ||
            epsCAGR >= VitalThresholds.EXCEPTIONAL_CAGR) {
            return VitalEvaluation(
                score: 8.0,
                status: .good,
                headline: "Accelerating Growth"
            )
        }

        // ── Raised guidance with solid growth → good ─────────────────
        if guidance == .raised {
            return VitalEvaluation(
                score: 7.0,
                status: .good,
                headline: "Raised Guidance"
            )
        }

        // ── Maintained guidance with negative earnings → warning ─────
        if epsCAGR <= VitalThresholds.NEGATIVE_GROWTH {
            return VitalEvaluation(
                score: weightRisk(6.5),
                status: .warning,
                headline: "Earnings Deterioration"
            )
        }

        return .hidden
    }

    // MARK: - Wall Street Consensus

    /// Surfaces when Wall Street sees massive upside/downside, or when
    /// there's an active downgrade wave. Consensus "Hold" at fair value
    /// is not interesting.
    ///
    /// - Parameters:
    ///   - consensusRating: The consensus analyst rating.
    ///   - priceTarget: Average analyst price target.
    ///   - currentPrice: Current market price.
    ///   - recentUpgrades: Number of recent upgrades.
    ///   - recentDowngrades: Number of recent downgrades.
    static func evaluateWallStreet(
        consensusRating: ConsensusRating,
        priceTarget: Double,
        currentPrice: Double,
        recentUpgrades: Int,
        recentDowngrades: Int
    ) -> VitalEvaluation {
        let upsidePct = currentPrice > 0
            ? ((priceTarget - currentPrice) / currentPrice) * 100.0
            : 0.0

        // ── Strong Sell consensus → critical ─────────────────────────
        if consensusRating == .strongSell {
            return VitalEvaluation(
                score: weightRisk(8.5),
                status: .critical,
                headline: "Strong Sell"
            )
        }

        // ── Sell consensus → warning ─────────────────────────────────
        if consensusRating == .sell {
            return VitalEvaluation(
                score: weightRisk(7.5),
                status: .warning,
                headline: "Sell Consensus"
            )
        }

        // ── Downgrade wave → warning ─────────────────────────────────
        if recentDowngrades > 0 && recentUpgrades > 0 {
            let ratio = Double(recentDowngrades) / Double(recentUpgrades)
            if ratio >= VitalThresholds.DOWNGRADE_WAVE_RATIO {
                return VitalEvaluation(
                    score: weightRisk(7.5),
                    status: .warning,
                    headline: "Downgrade Wave"
                )
            }
        }

        // ── Target implies significant downside ──────────────────────
        if upsidePct <= VitalThresholds.SIGNIFICANT_WS_DOWNSIDE {
            return VitalEvaluation(
                score: weightRisk(7.0),
                status: .warning,
                headline: "Downside Target"
            )
        }

        // ── Massive upside consensus → good ──────────────────────────
        if upsidePct >= VitalThresholds.MASSIVE_UPSIDE_PCT {
            return VitalEvaluation(
                score: 8.0,
                status: .good,
                headline: "Strong Buy"
            )
        }

        // ── Significant upside → good ────────────────────────────────
        if upsidePct >= VitalThresholds.SIGNIFICANT_UPSIDE_PCT {
            return VitalEvaluation(
                score: 7.0,
                status: .good,
                headline: "Upside Consensus"
            )
        }

        return .hidden
    }

    // MARK: - Aggregate: Surface & Sort

    /// Given an array of evaluations, returns only those that should be
    /// surfaced, sorted by score descending (most critical first).
    /// The resulting order is what the horizontal scroll should display.
    static func surfaceAndSort(_ evaluations: [VitalEvaluation]) -> [VitalEvaluation] {
        evaluations
            .filter { $0.shouldSurface }
            .sorted { $0.score > $1.score }
    }
}
