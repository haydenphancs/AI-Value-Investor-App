//
//  TickerReportModels.swift
//  ios
//
//  Data models for the Ticker Report (Buffett Agent) screen
//

import Foundation
import SwiftUI

// MARK: - Navigation Helper

struct ReportTickerNavigation: Identifiable {
    let id = UUID()
    let ticker: String
}

// MARK: - Report Agent Persona

enum ReportAgentPersona: String, CaseIterable {
    case buffett = "ANALYZED BY BUFFETT AGENT"
    case wood = "ANALYZED BY WOOD AGENT"
    case lynch = "ANALYZED BY LYNCH AGENT"
    case dalio = "ANALYZED BY DALIO AGENT"

    var starRating: Double {
        switch self {
        case .buffett: return 4.0
        case .wood: return 3.5
        case .lynch: return 4.5
        case .dalio: return 4.0
        }
    }
}

// MARK: - Report Quality Rating

struct ReportQualityRating {
    let score: Double       // 0-100
    let maxScore: Double    // 100
    let label: String       // Auto-generated based on score

    var formattedScore: String {
        String(format: "%.0f", score)
    }

    var formattedMax: String {
        "/ \(Int(maxScore))"
    }

    // Helper to generate label from score
    static func labelForScore(_ score: Double) -> String {
        switch score {
        case 90...100:
            return "Excellent Quality Business"
        case 75..<90:
            return "Strong Quality Business"
        case 50..<75:
            return "Fair Quality Business"
        case 30..<50:
            return "Weak Quality Business"
        default:
            return "Distressed Quality Business"
        }
    }

    // Convenience initializer that auto-generates label
    init(score: Double, maxScore: Double = 100) {
        self.score = score
        self.maxScore = maxScore
        self.label = ReportQualityRating.labelForScore(score)
    }

    // Full initializer for custom labels
    init(score: Double, maxScore: Double, label: String) {
        self.score = score
        self.maxScore = maxScore
        self.label = label
    }
}

// MARK: - Executive Summary Bullet

struct ExecutiveSummaryBullet: Identifiable {
    let id = UUID()
    let category: String        // e.g. "Catalyst", "Valuation", "Risk"
    let text: String
    let sentiment: BulletSentiment

    enum BulletSentiment {
        case positive, neutral, negative

        var color: Color {
            switch self {
            case .positive: return AppColors.bullish
            case .neutral: return AppColors.neutral
            case .negative: return AppColors.bearish
            }
        }

        var iconName: String {
            switch self {
            case .positive: return "arrow.up.circle.fill"
            case .neutral: return "minus.circle.fill"
            case .negative: return "arrow.down.circle.fill"
            }
        }
    }
}

// MARK: - Valuation Status

enum ValuationStatus: String {
    case overpriced = "Overpriced"
    case fairValue = "Fair Value"
    case underpriced = "Underpriced"
    case deepUndervalued = "Deep Undervalued"

    var color: Color {
        switch self {
        case .overpriced: return AppColors.bearish
        case .fairValue: return AppColors.neutral
        case .underpriced: return AppColors.bullish
        case .deepUndervalued: return AppColors.bullish
        }
    }

    var backgroundColor: Color {
        color.opacity(0.15)
    }
}

// MARK: - Key Vital: Valuation Card Data

struct ReportValuationData {
    let status: ValuationStatus
    let currentPrice: Double
    let fairValue: Double
    let upsidePotential: Double  // percentage

    var formattedCurrentPrice: String {
        String(format: "$%.2f", currentPrice)
    }

    var formattedFairValue: String {
        String(format: "$%.0f", fairValue)
    }

    var formattedUpside: String {
        let sign = upsidePotential >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", upsidePotential))%"
    }

    var upsideColor: Color {
        upsidePotential >= 0 ? AppColors.bullish : AppColors.bearish
    }
}

// MARK: - Moat Quality Tag

struct MoatTag: Identifiable {
    let id = UUID()
    let label: String
    let strength: MoatStrength

    enum MoatStrength: String {
        case wide = "Wide"
        case narrow = "Narrow"
        case none = "None"

        var color: Color {
            switch self {
            case .wide: return AppColors.bullish
            case .narrow: return AppColors.neutral
            case .none: return AppColors.bearish
            }
        }
    }
}

// MARK: - Key Vital: Moat Card Data

struct ReportMoatData {
    let overallRating: MoatTag.MoatStrength  // Wide, Narrow, or None
    let primarySource: String                 // "High Switching Costs"
    let tags: [MoatTag]
    let valueLabel: String      // "Value" or "Stable"
    let stabilityLabel: String  // "Stable"
}

// MARK: - Financial Health Indicator

enum FinancialHealthLevel: String {
    case strong = "Strong"
    case moderate = "Moderate"
    case weak = "Weak"
    case critical = "Critical"

    var color: Color {
        switch self {
        case .strong: return AppColors.bullish      // Green - Safe Zone
        case .moderate: return AppColors.alertOrange // Orange - Grey Zone
        case .weak: return AppColors.alertOrange     // Orange - Grey Zone
        case .critical: return AppColors.bearish     // Red - Distress
        }
    }
    
    // Z-Score based level
    static func fromZScore(_ score: Double) -> FinancialHealthLevel {
        if score < 1.8 {
            return .critical    // Red - Distress
        } else if score < 3.0 {
            return .weak        // Orange - Grey Zone (could also be .moderate)
        } else {
            return .strong      // Green - Safe Zone
        }
    }
}

// MARK: - Key Vital: Financial Health Card Data

struct ReportFinancialHealthData {
    let level: FinancialHealthLevel
    let altmanZScore: Double
    let altmanZLabel: String            // "Below 1.8 is risk"
    let additionalMetric: String        // "Increasing Cost"
    let additionalMetricStatus: FinancialHealthLevel
    let fcfNote: String                 // "Negative FCF in last 2 years"

    var formattedZScore: String {
        String(format: "%.1f", altmanZScore)
    }
    
    // Convert "Increasing Cost" to "Rising Expenses" for better clarity
    var additionalMetricDisplayText: String {
        if additionalMetric.lowercased().contains("increasing cost") {
            return "Rising Expenses"
        }
        return additionalMetric
    }
}

// MARK: - Vital Score (0.0-10.0 Scale)

/// Internal importance score computed by VitalRulesEngine.
/// The numeric value is never displayed in the UI — only VitalStatus is shown.
struct VitalScore {
    let value: Double           // 0.0-10.0 (importance, internal only)
    let status: VitalStatus     // displayed on card badge

    init(value: Double, status: VitalStatus) {
        self.value = min(max(value, 0.0), 10.0)
        self.status = status
    }

    /// Convenience initializer from a VitalEvaluation result.
    init(from evaluation: VitalEvaluation) {
        self.value = evaluation.score
        self.status = evaluation.status
    }

    var shouldSurface: Bool {
        status != .neutral
    }
}

// MARK: - Key Vital: Revenue Card Data

struct ReportRevenueVitalData {
    let score: VitalScore
    let totalRevenue: String            // "$14.1B"
    let revenueGrowth: Double           // YoY percentage
    let topSegment: String              // "Cloud Infrastructure"
    let topSegmentGrowth: Double        // percentage

    var formattedGrowth: String {
        let sign = revenueGrowth >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.0f", revenueGrowth))% YoY"
    }

    var formattedTopSegmentGrowth: String {
        let sign = topSegmentGrowth >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.0f", topSegmentGrowth))%"
    }

    var growthColor: Color {
        revenueGrowth >= 0 ? AppColors.bullish : AppColors.bearish
    }
}

// MARK: - Key Vital: Insider Card Data

struct ReportInsiderVitalData {
    let score: VitalScore
    let sentiment: InsiderSentiment
    let netActivity: String             // "Net Selling" or "Net Buying"
    let buyCount: Int
    let sellCount: Int
    let keyInsight: String              // "Heavy insider selling last 90 days"

    var activityColor: Color {
        sentiment.color
    }
}

// MARK: - Key Vital: Macro Card Data

struct ReportMacroVitalData {
    let score: VitalScore
    let threatLevel: ThreatLevel
    let topRisk: String                 // "Fed Rate Uncertainty"
    let riskTrend: RiskTrend
    let activeRiskCount: Int            // number of elevated+ risks

    var formattedRiskCount: String {
        "\(activeRiskCount) Active"
    }
}

// MARK: - Key Vital: Forecast Card Data

struct ReportForecastVitalData {
    let score: VitalScore
    let revenueCAGR: Double             // percentage
    let epsCAGR: Double                 // percentage
    let guidance: ManagementGuidance
    let outlook: String                 // "Accelerating Growth"

    var formattedRevenueCAGR: String {
        "+\(String(format: "%.0f", revenueCAGR))% CAGR"
    }

    var formattedEPSCAGR: String {
        "+\(String(format: "%.0f", epsCAGR))% CAGR"
    }
}

// MARK: - Key Vital: Wall Street Card Data

struct ReportWallStreetVitalData {
    let score: VitalScore
    let consensusRating: ConsensusRating
    let priceTarget: Double
    let currentPrice: Double
    let upgrades: Int
    let downgrades: Int

    var formattedTarget: String {
        String(format: "$%.0f", priceTarget)
    }

    var formattedUpside: String {
        let upside = ((priceTarget - currentPrice) / currentPrice) * 100
        let sign = upside >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.0f", upside))%"
    }

    var upsideColor: Color {
        priceTarget >= currentPrice ? AppColors.bullish : AppColors.bearish
    }
}

// MARK: - Key Vitals Section
// All fields are optional — VitalRulesEngine determines which vitals are
// important enough to surface. nil = not noteworthy, don't display card.

struct ReportKeyVitals {
    let valuation: ReportValuationData?
    let moat: ReportMoatData?
    let financialHealth: ReportFinancialHealthData?
    let revenue: ReportRevenueVitalData?
    let insider: ReportInsiderVitalData?
    let macro: ReportMacroVitalData?
    let forecast: ReportForecastVitalData?
    let wallStreet: ReportWallStreetVitalData?

    /// Returns true if at least one vital card is present.
    var hasAny: Bool {
        valuation != nil || moat != nil || financialHealth != nil ||
        revenue != nil || insider != nil || macro != nil ||
        forecast != nil || wallStreet != nil
    }
}

// MARK: - Core Thesis Bullet

struct CoreThesisBullet: Identifiable {
    let id = UUID()
    let text: String
}

// MARK: - Core Thesis Data

struct ReportCoreThesis {
    let bullCase: [CoreThesisBullet]
    let bearCase: [CoreThesisBullet]
}

// MARK: - Deep Dive Metric Card Data

struct DeepDiveMetricCard: Identifiable {
    let id = UUID()
    let title: String           // "Profitability", "Valuation", "Growth", "Health"
    let starRating: Int         // 1-5
    let metrics: [DeepDiveMetric]
    let qualityLabel: String    // "A Cash Machine", "Priced for perfection", etc.

    /// True when any metric in this card has been compared to the sector
    /// average (and therefore renders with a trailing " *"). Drives the
    /// asterisk footnote below the 2x2 grid.
    var hasSectorComparison: Bool {
        metrics.contains { $0.displayLabel.hasSuffix(" *") }
    }
}

// MARK: - Deep Dive Metric

struct DeepDiveMetric: Identifiable {
    let id = UUID()
    let label: String
    let value: String
    let trend: MetricTrend?

    /// Compact label suitable for the narrow 2-column metric grid.
    /// Strips verbose sector-comparison suffix (e.g. "(0.98x sector avg 27)"
    /// or "(vs sector 4.5)"), drops "(YoY)" boilerplate, and applies common
    /// abbreviations (ROE, ROA, FCF). When a sector suffix was present, a
    /// trailing " *" is appended to mark the metric for the footnote.
    var displayLabel: String {
        let withoutSector = label.replacingOccurrences(
            of: #"\s*\([^)]*sector[^)]*\)"#,
            with: "",
            options: .regularExpression
        )
        let hadSectorSuffix = (withoutSector != label)

        var result = withoutSector.replacingOccurrences(
            of: #"\s*\(YoY\)"#,
            with: "",
            options: .regularExpression
        )

        let abbreviations: [(String, String)] = [
            ("Return on Equity (ROE)", "ROE"),
            ("Return on Assets (ROA)", "ROA"),
            ("Return on Equity", "ROE"),
            ("Return on Assets", "ROA"),
            ("Free Cash Flow", "FCF"),
            // "Operating" → "Op." covers both "Operating Margin" (Profitability
            // card) and "Operating Income Growth" (Growth card). The latter
            // still won't fit on one line, which is why the metric label uses
            // .lineLimit(2) — it wraps to "Op. Income" / "Growth".
            ("Operating", "Op."),
        ]
        for (long, short) in abbreviations {
            result = result.replacingOccurrences(of: long, with: short)
        }

        result = result.trimmingCharacters(in: .whitespaces)
        return hadSectorSuffix ? "\(result) *" : result
    }

    enum MetricTrend {
        case up, down, flat

        var iconName: String {
            switch self {
            case .up: return "arrow.up"
            case .down: return "arrow.down"
            case .flat: return "minus"
            }
        }

        var color: Color {
            switch self {
            case .up: return AppColors.bullish
            case .down: return AppColors.bearish
            case .flat: return AppColors.textSecondary
            }
        }
    }
}

// MARK: - Overall Assessment

struct ReportOverallAssessment {
    let text: String
    let averageRating: Double
    let strongCount: Int
    let weakCount: Int
}

// MARK: - Revenue Forecast Data

struct ReportRevenueForecast {
    let cagr: Double                    // revenue growth percentage
    let epsGrowth: Double               // EPS growth percentage
    let managementGuidance: ManagementGuidance
    let projections: [RevenueProjection]
    let guidanceQuote: String?
    // Attribution metadata for `guidanceQuote` (PR 6 — verbatim from
    // earnings transcript). Both nil when no quote was extracted.
    let guidanceSpeaker: String?       // "CFO" | "CEO" | "IR"
    let guidancePeriod: String?        // "Q4 2025" | "FY 2026"

    var formattedCAGR: String {
        "+\(String(format: "%.0f", cagr))% CAGR"
    }

    var formattedEPSGrowth: String {
        "+\(String(format: "%.0f", epsGrowth))% CAGR"
    }

    /// Compose the iOS attribution caption shown beneath the quote bubble.
    /// Returns nil when neither speaker nor period is available — the
    /// view should hide the caption row entirely in that case.
    var formattedGuidanceAttribution: String? {
        switch (guidanceSpeaker, guidancePeriod) {
        case let (s?, p?): return "\(s), \(p)"
        case let (s?, nil): return s
        case let (nil, p?): return p
        case (nil, nil): return nil
        }
    }
}

struct RevenueProjection: Identifiable {
    let id = UUID()
    let period: String      // x-axis category e.g. "FY24", "FY25E"
    let revenue: Double     // revenue value (billions)
    let revenueLabel: String // display label e.g. "$120B"
    let revenueYoyPct: Double? // YoY %, nil for the first visible year when no anchor exists
    let eps: Double         // EPS value e.g. 4.50
    let epsLabel: String    // display label e.g. "$4.50"
    let epsYoyPct: Double?  // YoY %, nil for the first visible year when no anchor exists
    let isForecast: Bool

    /// Compact YoY string for the bar/dot annotations. Returns nil when
    /// we have no anchor — the view should hide the row entirely.
    var revenueYoYText: String? {
        guard let pct = revenueYoyPct else { return nil }
        return String(format: "%@%.0f%%", pct >= 0 ? "+" : "", pct)
    }

    var epsYoYText: String? {
        guard let pct = epsYoyPct else { return nil }
        return String(format: "%@%.0f%%", pct >= 0 ? "+" : "", pct)
    }

    /// Color for the YoY chip: green for growth, red for decline, gray
    /// when missing (caller should also gate on the *Text property).
    var revenueYoYColor: Color {
        guard let pct = revenueYoyPct else { return AppColors.textMuted }
        return pct >= 0 ? AppColors.bullish : AppColors.bearish
    }

    var epsYoYColor: Color {
        guard let pct = epsYoyPct else { return AppColors.textMuted }
        return pct >= 0 ? AppColors.bullish : AppColors.bearish
    }
}

enum ManagementGuidance: String {
    case raised = "RAISED"
    case maintained = "MAINTAINED"
    case lowered = "LOWERED"

    var color: Color {
        switch self {
        case .raised: return AppColors.bullish
        case .maintained: return AppColors.neutral
        case .lowered: return AppColors.bearish
        }
    }

    var backgroundColor: Color {
        color.opacity(0.15)
    }
}

// MARK: - Insider Activity

enum InsiderSentiment: String {
    case positive = "Positive"
    case negative = "Negative"
    case neutral = "Neutral"

    var color: Color {
        switch self {
        case .positive: return AppColors.bullish
        case .negative: return AppColors.bearish
        case .neutral: return AppColors.neutral
        }
    }

    var backgroundColor: Color {
        color.opacity(0.15)
    }
}

struct InsiderTransaction: Identifiable {
    let id = UUID()
    let type: String        // "Buys" or "Sells"
    let count: Int
    let shares: String
    let value: String
}

struct ReportInsiderData {
    let sentiment: InsiderSentiment
    let timeframe: String           // "Last 90 Days"
    let transactions: [InsiderTransaction]
    let ownershipNote: String?      // "The stock is heavily sold off by insiders."
}

// MARK: - Key Management

struct KeyManager: Identifiable {
    let id = UUID()
    let name: String
    let title: String
    let ownership: String       // e.g. "40.3%", "$2,025", etc.
    let ownershipValue: String  // dollar amount or additional info
    let percentOwnership: Double?  // 13G beneficial %, nil for non-5%-filers

    var percentOwnershipLabel: String? {
        guard let pct = percentOwnership, pct > 0 else { return nil }
        return String(format: "%.0f%% owner", pct)
    }
}

struct ReportKeyManagement {
    let topHolders: [KeyManager]    // 10%+ owners (paired with 13G filings)
    let officers: [KeyManager]      // sorted CEO → CFO → COO → … → directors
    let ownershipInsight: String    // "Oracle's high ownership ensures long-term thinking..."
}

// MARK: - Wall Street Consensus

enum ConsensusRating: String {
    case strongBuy = "BUY RATING"
    case buy = "BUY"
    case hold = "HOLD"
    case sell = "SELL"
    case strongSell = "STRONG SELL"

    var color: Color {
        switch self {
        case .strongBuy, .buy: return AppColors.bullish
        case .hold: return AppColors.neutral
        case .sell, .strongSell: return AppColors.bearish
        }
    }

    var backgroundColor: Color {
        color.opacity(0.15)
    }
}

struct ReportWallStreetConsensus {
    let rating: ConsensusRating
    let currentPrice: Double
    // nil when there's no real analyst coverage. The view renders an honest
    // "no analyst price targets" state instead of fabricated numbers.
    let targetPrice: Double?
    let lowTarget: Double?
    let highTarget: Double?
    let valuationStatus: ValuationStatus
    let discountPercent: Double         // "Trading 33.4% below fair value estimate"
    // NAMING: `hedgeFund*` = FMP 13F institutional-ownership data, rendered in the
    // report's "Institutions" section (SmartMoneyTab.hedgeFunds = "Institutions").
    let hedgeFundNote: String?          // "Net inflow of $430M from institutional..."
    let hedgeFundPriceData: [StockPriceDataPoint]   // Price data for hedge fund chart
    let hedgeFundFlowData: [SmartMoneyFlowDataPoint] // Buy/sell volume data (legacy monthly fallback)
    // Quarterly institutional flow mirrored from the Holders tab. When
    // present, the Hedge Funds chart renders this (quarterly bars +
    // net-flow badge) instead of the monthly fallback above.
    let hedgeFundSmartMoney: SmartMoneyData?
    let momentumUpgrades: Int
    let momentumDowngrades: Int

    /// True only when the backend returned a real analyst consensus range.
    /// The pole, target badges, and forecast copy are gated on this.
    var hasAnalystTargets: Bool {
        targetPrice != nil && lowTarget != nil && highTarget != nil
    }

    var formattedCurrentPrice: String {
        String(format: "$%.0f", currentPrice)
    }

    var formattedTargetPrice: String {
        guard let targetPrice else { return "—" }
        return String(format: "$%.0f", targetPrice)
    }

    var formattedHighTarget: String {
        guard let highTarget else { return "—" }
        return String(format: "$%.0f", highTarget)
    }

    var formattedLowTarget: String {
        guard let lowTarget else { return "—" }
        return String(format: "$%.0f", lowTarget)
    }

    var formattedHighTargetPercent: String {
        guard let highTarget else { return "—" }
        let percent = ((highTarget - currentPrice) / currentPrice) * 100
        return String(format: "%+.1f%%", percent)
    }

    var formattedAvgTargetPercent: String {
        guard let targetPrice else { return "—" }
        let percent = ((targetPrice - currentPrice) / currentPrice) * 100
        return String(format: "%+.1f%%", percent)
    }

    var formattedLowTargetPercent: String {
        guard let lowTarget else { return "—" }
        let percent = ((lowTarget - currentPrice) / currentPrice) * 100
        return String(format: "%+.1f%%", percent)
    }

    var formattedDiscount: String {
        "Trading \(String(format: "%.1f", discountPercent))% below fair value estimate"
    }
}

// MARK: - Critical Factor

struct CriticalFactor: Identifiable {
    let id = UUID()
    let title: String
    let description: String
    let severity: CriticalSeverity

    enum CriticalSeverity {
        case high, medium, low

        var color: Color {
            switch self {
            case .high: return AppColors.bearish
            case .medium: return AppColors.alertOrange
            case .low: return AppColors.neutral
            }
        }

        var iconName: String {
            switch self {
            case .high: return "exclamationmark.triangle.fill"
            case .medium: return "exclamationmark.circle.fill"
            case .low: return "info.circle.fill"
            }
        }
    }
}

// MARK: - Price Action

struct PriceEvent {
    let tag: String           // "Earnings Miss", "FDA Approval", "Guidance Cut"
    let date: String          // "Feb 2"
    let index: Int            // position in prices array where event occurred
}

struct PriceActionData {
    let prices: [Double]      // daily closing prices (oldest → newest)
    let currentPrice: Double
    let event: PriceEvent?    // optional catalyst
    let narrative: String     // short explanation text
    let changePct: Double     // signed % over the chosen window (or since event)
    let direction: String     // "up" | "down" | "flat" — drives badge + AI grounding
    let windowLabel: String   // "Last {N} Days" (N dynamic) or "Since {event date}"
    let tag: String           // "Typical" / "Notable" / "Unusual" / "Extreme" / event tag

    // Volatility context — drives the sub-label "Normal range: ±X% (Y% daily σ)".
    // All optional so older cached reports decode and so we can render the
    // section honestly even when the baseline (<30 trading days) is too short
    // to compute a meaningful σ.
    let tier: String?            // "Typical" | "Notable" | "Unusual" | "Extreme"
    let zScore: Double?          // |move| / (σ_daily × √N)
    let sigmaDailyPct: Double?   // daily return σ, in percent (e.g. 1.52)
    let expectedBandPct: Double? // ±2σ band for the chosen window, in percent
}

// MARK: - Price Movement (Legacy)

enum PriceTimeframe: String, CaseIterable, Identifiable {
    case oneDay = "1D"
    case oneWeek = "1W"
    case oneMonth = "1M"

    var id: String { rawValue }
}

struct PricePoint: Identifiable {
    let id = UUID()
    let index: Int          // x-axis position
    let price: Double
    let volume: Double?
}

struct PriceMovementStats {
    let currentPrice: Double
    let priceChange: Double
    let percentChange: Double
    let periodHigh: Double
    let periodLow: Double
    let avgVolume: String

    var formattedPrice: String { String(format: "$%.2f", currentPrice) }
    var formattedChange: String {
        let sign = priceChange >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", priceChange))"
    }
    var formattedPercent: String {
        let sign = percentChange >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", percentChange))%"
    }
    var isPositive: Bool { priceChange >= 0 }
    var trendColor: Color { isPositive ? AppColors.bullish : AppColors.bearish }
}

struct ReportPriceMovementData {
    let stats: [PriceTimeframe: PriceMovementStats]
    let points: [PriceTimeframe: [PricePoint]]
}

// MARK: - Moat & Competition

// MARK: - Market Dynamics

enum MarketConcentration: String {
    case monopoly = "Monopoly"
    case duopoly = "Duopoly"
    case oligopoly = "Oligopoly"
    case fragmented = "Fragmented"

    var color: Color {
        switch self {
        case .monopoly: return AppColors.bullish
        case .duopoly: return AppColors.neutral
        case .oligopoly: return AppColors.textSecondary
        case .fragmented: return AppColors.alertOrange
        }
    }

    var backgroundColor: Color {
        switch self {
        case .monopoly: return AppColors.bullish.opacity(0.15)
        case .duopoly: return AppColors.neutral.opacity(0.15)
        case .oligopoly: return AppColors.textSecondary.opacity(0.15)
        case .fragmented: return AppColors.alertOrange.opacity(0.15)
        }
    }
}

enum LifecyclePhase: String {
    case emerging = "Emerging"
    case secularGrowth = "Secular Growth"
    case mature = "Mature"
    case declining = "Declining"

    var color: Color {
        switch self {
        case .emerging: return AppColors.bullish
        case .secularGrowth: return AppColors.primaryBlue
        case .mature: return AppColors.neutral
        case .declining: return AppColors.bearish
        }
    }

    var backgroundColor: Color {
        switch self {
        case .emerging: return AppColors.bullish.opacity(0.15)
        case .secularGrowth: return AppColors.primaryBlue.opacity(0.15)
        case .mature: return AppColors.neutral.opacity(0.15)
        case .declining: return AppColors.bearish.opacity(0.15)
        }
    }
}

struct MarketDynamics {
    let industry: String                    // "Cloud Computing"
    let concentration: MarketConcentration  // .oligopoly
    // Nil when no source could produce a CAGR (cache miss + no peers);
    // iOS renders "—" rather than a misleading "+0.0%".
    let cagr5Yr: Double?
    let currentTAM: Double                  // 900 (in billions); 0 when unknown
    let futureTAM: Double                   // 1600 (in billions); 0 when unknown
    let currentYear: String                 // "2025"
    let futureYear: String                  // "2030"
    let lifecyclePhase: LifecyclePhase      // .secularGrowth
    // Verbatim quote from the earnings transcript / company description
    // that the AI used to derive `currentTAM` / `futureTAM`. Nil when
    // TAM came from FRED proxy or wasn't sourced at all.
    let tamSourceQuote: String?
    // Caption shown under the TAM row: "Earnings call quote" when AI
    // extracted it from the transcript, "BEA <Sector> value-added (via
    // FRED)" when FRED proxy was used, nil when TAM is 0 (UI hides).
    let tamSourceLabel: String?
    // Grain of the source data: "industry" | "sector" | "all_industry".
    // Drives `tamGrainWarning` so the UI can flag when the TAM/CAGR is
    // sourced from a broader bucket than the company's own industry
    // (e.g., we fell back to the sector-level FRED series because no
    // industry-specific NAICS was mapped). Nil for AI-quote sourced TAM.
    let sourceGrain: String?

    var formattedCAGR: String {
        guard let cagr = cagr5Yr else { return "—" }
        let sign = cagr >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", cagr))%"
    }

    var cagrColor: Color {
        guard let cagr = cagr5Yr else { return AppColors.textMuted }
        return cagr >= 0 ? AppColors.bullish : AppColors.bearish
    }

    var formattedCurrentTAM: String {
        if currentTAM >= 1000 {
            return String(format: "$%.1fT", currentTAM / 1000)
        } else {
            return String(format: "$%.0fB", currentTAM)
        }
    }

    var formattedFutureTAM: String {
        if futureTAM >= 1000 {
            return String(format: "$%.1fT", futureTAM / 1000)
        } else {
            return String(format: "$%.0fB", futureTAM)
        }
    }

    /// True when no TAM source has populated either bound — view hides
    /// the entire "Market Size (TAM)" column in that case.
    var tamIsAvailable: Bool {
        currentTAM > 0 || futureTAM > 0
    }

    var formattedTAMRange: String {
        guard tamIsAvailable else { return "—" }
        return "\(formattedCurrentTAM) → \(formattedFutureTAM)"
    }

    // MARK: - Today-aligned projection
    //
    // Phase A (Census/FRED) data is typically a year or two stale —
    // e.g., Census AIES "Software publishers" data is 2023 even when a
    // user opens the report in 2026. To keep the displayed years
    // current AND the math honest, project the source TAM forward to
    // today using the source CAGR. If the source is already current
    // (Gemini overrides, FMP transcript quotes), `yearsToProject` is 0
    // and the displayed values equal the raw source values.

    private var todayYear: Int {
        Calendar.current.component(.year, from: Date())
    }

    private var sourceYearInt: Int? { Int(currentYear) }
    private var futureYearInt: Int? { Int(futureYear) }

    private var yearsToProject: Int {
        guard let src = sourceYearInt else { return 0 }
        return max(0, todayYear - src)
    }

    private var projectionMultiplier: Double {
        guard yearsToProject > 0, let cagr = cagr5Yr else { return 1.0 }
        return pow(1.0 + (cagr / 100.0), Double(yearsToProject))
    }

    /// Year shown in the UI as "current". Bumped forward to today when
    /// the underlying data is older than today.
    var displayedCurrentYear: String {
        guard yearsToProject > 0 else { return currentYear }
        return String(todayYear)
    }

    /// Future year shown in the UI. Preserves the source data's
    /// (future − current) span (typically 5 years) but anchored to
    /// `displayedCurrentYear` rather than the stale source year.
    var displayedFutureYear: String {
        guard let src = sourceYearInt, let fut = futureYearInt else {
            return futureYear
        }
        let span = fut - src
        let anchor = Int(displayedCurrentYear) ?? src
        return String(anchor + span)
    }

    /// Current TAM projected forward to `displayedCurrentYear` using CAGR.
    var displayedCurrentTAM: Double { currentTAM * projectionMultiplier }

    /// Future TAM projected forward by the same multiplier so the
    /// implied CAGR over (displayedFuture − displayedCurrent) is unchanged.
    var displayedFutureTAM: Double { futureTAM * projectionMultiplier }

    var formattedDisplayedCurrentTAM: String {
        let v = displayedCurrentTAM
        return v >= 1000
            ? String(format: "$%.1fT", v / 1000)
            : String(format: "$%.0fB", v)
    }

    var formattedDisplayedFutureTAM: String {
        let v = displayedFutureTAM
        return v >= 1000
            ? String(format: "$%.1fT", v / 1000)
            : String(format: "$%.0fB", v)
    }
}

enum MoatOverallRating: String {
    case wide = "Wide Moat"
    case narrow = "Narrow Moat"
    case none = "No Moat"

    var color: Color {
        switch self {
        case .wide: return AppColors.alertPurple      // Indigo-500 (Purple) - Elite defense
        case .narrow: return AppColors.accentYellow   // Yellow-500 - Strong but beatable
        case .none: return AppColors.textSecondary    // Gray-500 - No structural advantage
        }
    }

    var backgroundColor: Color {
        switch self {
        case .wide: return AppColors.alertPurple.opacity(0.15)
        case .narrow: return AppColors.accentYellow.opacity(0.15)
        case .none: return AppColors.textSecondary.opacity(0.15)
        }
    }

    var iconName: String { "shield.lefthalf.filled" }

    // Calculate moat rating from dimensions using the Max-Score Rule
    static func from(dimensions: [MoatDimension]) -> MoatOverallRating {
        guard let maxScore = dimensions.map({ $0.score }).max() else {
            return .none
        }

        if maxScore >= 8.5 {
            return .wide    // Elite defense
        } else if maxScore >= 7.0 {
            return .narrow  // Strong but beatable
        } else {
            return .none    // No structural advantage
        }
    }
}

struct MoatDimension: Identifiable {
    let id = UUID()
    let name: String        // e.g. "Switching Costs"
    let score: Double       // 0.0 - 10.0
    let peerScore: Double   // competitor avg for comparison

    var normalizedScore: Double { score / 10.0 }
    var normalizedPeerScore: Double { peerScore / 10.0 }
}

enum CompetitorThreatLevel: String {
    case low = "Low"
    case moderate = "Moderate"
    case high = "High"

    var color: Color {
        switch self {
        case .low: return AppColors.bullish
        case .moderate: return AppColors.neutral
        case .high: return AppColors.bearish
        }
    }
}

struct CompetitorComparison: Identifiable {
    let id = UUID()
    let name: String
    let ticker: String
    let competitiveScore: Double       // 0-10
    let marketSharePercent: Double
    let threatLevel: CompetitorThreatLevel
}

struct ReportMoatCompetitionData {
    let marketDynamics: MarketDynamics
    let dimensions: [MoatDimension]
    let durabilityNote: String
    let competitors: [CompetitorComparison]
    let competitiveInsight: String

    // Computed: Overall rating based on Max-Score Rule
    var overallRating: MoatOverallRating {
        MoatOverallRating.from(dimensions: dimensions)
    }

    // Computed: Primary driver (highest scoring dimension)
    var primaryDriver: MoatDimension? {
        dimensions.max(by: { $0.score < $1.score })
    }

    // Computed: Primary driver name
    var primaryDriverName: String {
        primaryDriver?.name ?? "Unknown"
    }
}

// MARK: - Macro & Geopolitical

enum ThreatLevel: String, CaseIterable {
    case low = "LOW"
    case elevated = "ELEVATED"
    case high = "HIGH"
    case severe = "SEVERE"
    case critical = "CRITICAL"

    var color: Color {
        switch self {
        case .low: return AppColors.bullish
        case .elevated: return Color(hex: "84CC16")     // lime
        case .high: return AppColors.neutral
        case .severe: return AppColors.alertOrange
        case .critical: return AppColors.bearish
        }
    }

    var numericLevel: Int {
        switch self {
        case .low: return 1
        case .elevated: return 2
        case .high: return 3
        case .severe: return 4
        case .critical: return 5
        }
    }
}

enum MacroRiskCategory: String {
    case inflation = "Inflation"
    case interestRates = "Interest Rates"
    case geopolitical = "Geopolitical"
    case currency = "Currency"
    case regulation = "Regulation"
    case supplyChain = "Supply Chain"
    case tariffs = "Trade & Tariffs"
    case energy = "Energy"

    var iconName: String {
        switch self {
        case .inflation: return "chart.line.uptrend.xyaxis"
        case .interestRates: return "percent"
        case .geopolitical: return "globe.americas"
        case .currency: return "dollarsign.arrow.circlepath"
        case .regulation: return "building.columns"
        case .supplyChain: return "shippingbox"
        case .tariffs: return "arrow.left.arrow.right"
        case .energy: return "bolt.fill"
        }
    }
}

enum RiskTrend: String {
    case improving = "Improving"
    case stable = "Stable"
    case worsening = "Worsening"

    var iconName: String {
        switch self {
        case .improving: return "arrow.down.right"
        case .stable: return "arrow.right"
        case .worsening: return "arrow.up.right"
        }
    }

    var color: Color {
        switch self {
        case .improving: return AppColors.bullish
        case .stable: return AppColors.textSecondary
        case .worsening: return AppColors.bearish
        }
    }
}

struct MacroRiskFactor: Identifiable {
    let id = UUID()
    let category: MacroRiskCategory
    let title: String
    let impact: Double          // 0.0 - 1.0
    let description: String
    let trend: RiskTrend
    let severity: ThreatLevel
}

struct ReportMacroData {
    let overallThreatLevel: ThreatLevel
    let headline: String            // "Elevated macro risk from rate policy and trade tensions"
    let riskFactors: [MacroRiskFactor]
    let intelligenceBrief: String   // AI summary paragraph
    let lastUpdated: String         // "Updated Feb 8, 2026"
}

// MARK: - Deep Dive Module

struct DeepDiveModule: Identifiable {
    let id = UUID()
    let title: String
    let iconName: String
    let type: DeepDiveModuleType
}

enum DeepDiveModuleType {
    case recentPriceMovement
    case revenueEngine
    case fundamentalsGrowth
    case futureForecast
    case insiderManagement
    case moatCompetition
    case macroGeopolitical
    case wallStreetConsensus
}

// MARK: - Full Report Data

struct TickerReportData: Identifiable {
    let id = UUID()
    let symbol: String
    let companyName: String
    let exchange: String
    let logoName: String?
    let liveDate: String

    // Agent & Rating
    let agent: ReportAgentPersona
    let qualityRating: ReportQualityRating

    // Executive Summary
    let executiveSummaryText: String
    let executiveSummaryBullets: [ExecutiveSummaryBullet]

    // Key Vitals
    let keyVitals: ReportKeyVitals

    // Core Thesis
    let coreThesis: ReportCoreThesis

    // Deep Dive: Fundamentals
    let fundamentalMetrics: [DeepDiveMetricCard]
    let overallAssessment: ReportOverallAssessment

    // Deep Dive: Future Forecast
    let revenueForecast: ReportRevenueForecast

    // Deep Dive: Insider & Management
    let insiderData: ReportInsiderData
    let keyManagement: ReportKeyManagement

    // Deep Dive: Price Action
    let priceAction: PriceActionData

    // Deep Dive: Revenue Engine
    let revenueEngine: ReportRevenueEngineData

    // Deep Dive: Moat & Competition
    let moatCompetition: ReportMoatCompetitionData

    // Deep Dive: Macro & Geopolitical
    let macroData: ReportMacroData

    // Deep Dive: Wall Street
    let wallStreetConsensus: ReportWallStreetConsensus

    // Critical Factors
    let criticalFactors: [CriticalFactor]

    // Disclaimer
    let disclaimerText: String
}

// MARK: - Sample Data

extension TickerReportData {
    static let sampleOracle = TickerReportData(
        symbol: "ORCL",
        companyName: "Oracle",
        exchange: "Nasdaq",
        logoName: nil,
        liveDate: "Live Data as of Feb 8, 8:04 AM",
        agent: .buffett,
        qualityRating: ReportQualityRating(
            score: 82
        ),
        executiveSummaryText: "Oracle is successfully pivoting to cloud infrastructure (OCI) with a massive backlog, but cash burn increases a short-term risk.",
        executiveSummaryBullets: [
            ExecutiveSummaryBullet(
                category: "Catalyst",
                text: "$12.5B RPO guarantees future revenue",
                sentiment: .positive
            ),
            ExecutiveSummaryBullet(
                category: "Valuation",
                text: "Trading at 15% discount to Fair Value",
                sentiment: .positive
            ),
            ExecutiveSummaryBullet(
                category: "Risk",
                text: "Negative Free Cash Flow in Q2",
                sentiment: .negative
            )
        ],
        keyVitals: ReportKeyVitals(
            valuation: ReportValuationData(
                status: .underpriced,
                currentPrice: 142.82,
                fairValue: 190,
                upsidePotential: 33.0
            ),
            moat: ReportMoatData(
                overallRating: .wide,
                primarySource: "High Switching Costs",
                tags: [
                    MoatTag(label: "High Switching Costs", strength: .wide),
                    MoatTag(label: "Network Effects", strength: .narrow)
                ],
                valueLabel: "Value",
                stabilityLabel: "Stable"
            ),
            financialHealth: ReportFinancialHealthData(
                level: FinancialHealthLevel.fromZScore(1.7),  // Will be .critical (Red - Distress)
                altmanZScore: 1.7,
                altmanZLabel: "Distress Zone (Below 1.8)",
                additionalMetric: "Increasing Cost",
                additionalMetricStatus: FinancialHealthLevel.fromZScore(1.7),
                fcfNote: "Negative FCF in last 2 years"
            ),
            revenue: ReportRevenueVitalData(
                score: VitalScore(value: 7.0, status: .good),
                totalRevenue: "$14.1B",
                revenueGrowth: 18,
                topSegment: "Cloud (OCI)",
                topSegmentGrowth: 66
            ),
            insider: ReportInsiderVitalData(
                score: VitalScore(value: 9.0, status: .critical),
                sentiment: .negative,
                netActivity: "Net Selling",
                buyCount: 3,
                sellCount: 12,
                keyInsight: "Heavy insider selling last 90 days"
            ),
            macro: ReportMacroVitalData(
                score: VitalScore(value: 7.0, status: .warning),
                threatLevel: .elevated,
                topRisk: "Fed Rate Uncertainty",
                riskTrend: .stable,
                activeRiskCount: 4
            ),
            forecast: ReportForecastVitalData(
                score: VitalScore(value: 7.0, status: .good),
                revenueCAGR: 15,
                epsCAGR: 18,
                guidance: .raised,
                outlook: "Accelerating Growth"
            ),
            wallStreet: ReportWallStreetVitalData(
                score: VitalScore(value: 7.0, status: .good),
                consensusRating: .strongBuy,
                priceTarget: 190,
                currentPrice: 142,
                upgrades: 8,
                downgrades: 3
            )
        ),
        coreThesis: ReportCoreThesis(
            bullCase: [
                CoreThesisBullet(text: "$12.5B safety net: Oracle's cloud infrastructure revenue provides a massive cushion against legacy decline"),
                CoreThesisBullet(text: "IaaS widening: Cloud infrastructure growing 66% YoY, capturing enterprise AI workloads"),
                CoreThesisBullet(text: "TikTok safety: Strategic partnership positions Oracle as critical infrastructure provider")
            ],
            bearCase: [
                CoreThesisBullet(text: "Cash flow deceleration: Negative $195 FCF as massive Capex spend outpaces revenue growth"),
                CoreThesisBullet(text: "Credit downgrade danger: Rising debt levels and negative FCF threaten investment-grade rating"),
                CoreThesisBullet(text: "Aggressive self-image: Aggressive cloud data center construction schedule escalating true infrastructure costs")
            ]
        ),
        fundamentalMetrics: [
            DeepDiveMetricCard(
                title: "Profitability",
                starRating: 5,
                metrics: [
                    DeepDiveMetric(label: "Operating Margin", value: "30.7%", trend: nil),
                    DeepDiveMetric(label: "Net Margin", value: "25.3%", trend: nil),
                    DeepDiveMetric(label: "Return on Equity (ROE)", value: "65.4%", trend: nil),
                    DeepDiveMetric(label: "Return on Assets (ROA)", value: "8.5%", trend: nil)
                ],
                qualityLabel: "A Cash Machine"
            ),
            DeepDiveMetricCard(
                title: "Growth",
                starRating: 4,
                metrics: [
                    DeepDiveMetric(label: "Revenue Growth (YoY)", value: "+18.0%", trend: .up),
                    DeepDiveMetric(label: "EPS Growth", value: "+22.0%", trend: .up),
                    DeepDiveMetric(label: "Free Cash Flow Growth (YoY)", value: "-8.2%", trend: .down),
                    DeepDiveMetric(label: "Operating Income Growth", value: "+11.4%", trend: .up)
                ],
                qualityLabel: "Accelerating"
            ),
            DeepDiveMetricCard(
                title: "Price",
                starRating: 3,
                metrics: [
                    DeepDiveMetric(label: "P/E", value: "25.1", trend: nil),
                    DeepDiveMetric(label: "P/S", value: "7.2", trend: nil),
                    DeepDiveMetric(label: "P/FCF", value: "24.0", trend: nil),
                    DeepDiveMetric(label: "EV/EBITDA", value: "18.4", trend: nil)
                ],
                qualityLabel: "Priced for perfection"
            ),
            DeepDiveMetricCard(
                title: "Financial Health",
                starRating: 2,
                metrics: [
                    DeepDiveMetric(label: "Altman Z-Score", value: "1.7", trend: nil),
                    DeepDiveMetric(label: "Interest Coverage", value: "3.2x", trend: nil),
                    DeepDiveMetric(label: "Cash to Debt", value: "0.18", trend: nil),
                    DeepDiveMetric(label: "Free Cash Flow Margin", value: "-2.1%", trend: nil),
                    DeepDiveMetric(label: "Asset Turnover", value: "0.45", trend: nil)
                ],
                qualityLabel: "Heavy Debt Load"
            )
        ],
        overallAssessment: ReportOverallAssessment(
            text: "Strong profitability and growth, but investor health concerns exist due to high leverage. Monitor debt levels closely.",
            averageRating: 3.5,
            strongCount: 2,
            weakCount: 1
        ),
        revenueForecast: ReportRevenueForecast(
            cagr: 15,
            epsGrowth: 18,
            managementGuidance: .raised,
            projections: [
                RevenueProjection(period: "2026", revenue: 67,  revenueLabel: "$67B",  revenueYoyPct: 18, eps: 7.48,  epsLabel: "$7.48",  epsYoyPct: 25, isForecast: false),
                RevenueProjection(period: "2027", revenue: 89,  revenueLabel: "$89B",  revenueYoyPct: 32, eps: 7.99,  epsLabel: "$7.99",  epsYoyPct: 7,  isForecast: true),
                RevenueProjection(period: "2028", revenue: 130, revenueLabel: "$130B", revenueYoyPct: 46, eps: 10.76, epsLabel: "$10.76", epsYoyPct: 35, isForecast: true),
                RevenueProjection(period: "2029", revenue: 179, revenueLabel: "$179B", revenueYoyPct: 37, eps: 15.14, epsLabel: "$15.14", epsYoyPct: 41, isForecast: true)
            ],
            guidanceQuote: "CFO expects accelerating cloud demand in Q3",
            guidanceSpeaker: "CFO",
            guidancePeriod: "Q3 2026"
        ),
        insiderData: ReportInsiderData(
            sentiment: .negative,
            timeframe: "Last 90 Days",
            transactions: [
                InsiderTransaction(type: "Buys", count: 3, shares: "12", value: "$1,234"),
                InsiderTransaction(type: "Sells", count: 12, shares: "45", value: "$4.1M")
            ],
            ownershipNote: "The stock is heavily sold off by insiders."
        ),
        keyManagement: ReportKeyManagement(
            topHolders: [
                KeyManager(name: "Lawrence Joseph Ellison", title: "director, 10 percent owner, Executive Chairman", ownership: "1.16B", ownershipValue: "$214.5B", percentOwnership: 43)
            ],
            officers: [
                KeyManager(name: "Dietrich Niebuhr", title: "Chief Executive Officer", ownership: "1.0M", ownershipValue: "$192.3M", percentOwnership: nil),
                KeyManager(name: "Marla Smith", title: "Chief Financial Officer", ownership: "224K", ownershipValue: "$41.6M", percentOwnership: nil),
                KeyManager(name: "Dania Caral", title: "Pres., Global Field Operations", ownership: "249K", ownershipValue: "$46.2M", percentOwnership: nil),
                KeyManager(name: "Jeffrey Henley", title: "director, Vice Chairman", ownership: "745K", ownershipValue: "$138.1M", percentOwnership: nil)
            ],
            ownershipInsight: "Oracle's high ownership ensures long-term thinking, though governance risk is high."
        ),
        priceAction: PriceActionData(
            prices: [
                163.20, 162.80, 164.10, 163.50, 162.90,
                161.40, 160.80, 159.20, 155.30, 150.10,
                148.60, 145.20, 143.80, 141.50, 140.20,
                142.10, 141.30, 143.50, 142.00, 142.82
            ],
            currentPrice: 142.82,
            event: PriceEvent(tag: "Earnings Miss", date: "Feb 2", index: 7),
            narrative: "Oracle dropped 12% after reporting Q3 earnings below consensus estimates. Revenue of $13.8B missed the $14.1B forecast, driven by slower-than-expected cloud migration deals. This reflects a fundamental concern about the pace of cloud migration — next quarter's bookings will decide whether the guidance reset is a one-off.",
            changePct: -10.3,
            direction: "down",
            windowLabel: "Since Feb 2",
            tag: "Earnings Miss",
            tier: "Unusual",
            zScore: 2.3,
            sigmaDailyPct: 1.52,
            expectedBandPct: 10.2
        ),
        revenueEngine: ReportRevenueEngineData.sampleOracle,
        moatCompetition: ReportMoatCompetitionData(
            marketDynamics: MarketDynamics(
                industry: "Cloud Computing",
                concentration: .oligopoly,
                cagr5Yr: 18.5,
                currentTAM: 900,
                futureTAM: 1600,
                currentYear: "2025",
                futureYear: "2030",
                lifecyclePhase: .secularGrowth,
                tamSourceQuote: "We see a $900B addressable cloud market today expanding to $1.6T by 2030.",
                tamSourceLabel: "Earnings call quote",
                sourceGrain: nil
            ),
            dimensions: [
                MoatDimension(name: "Switching Costs", score: 9.2, peerScore: 6.5),
                MoatDimension(name: "Network Effects", score: 5.8, peerScore: 7.0),
                MoatDimension(name: "Brand Power", score: 7.5, peerScore: 8.2),
                MoatDimension(name: "Cost Advantage", score: 6.0, peerScore: 5.5),
                MoatDimension(name: "Intangible Assets", score: 8.4, peerScore: 7.0)
            ],
            durabilityNote: "Oracle's moat is anchored by extremely high switching costs in enterprise database and ERP. Customers face multi-year migration timelines and significant retraining costs, creating durable lock-in.",
            competitors: [
                CompetitorComparison(name: "Amazon Web Services", ticker: "AMZN", competitiveScore: 9.0, marketSharePercent: 31.0, threatLevel: .high),
                CompetitorComparison(name: "Microsoft Azure", ticker: "MSFT", competitiveScore: 8.5, marketSharePercent: 25.0, threatLevel: .high),
                CompetitorComparison(name: "Google Cloud", ticker: "GOOGL", competitiveScore: 7.2, marketSharePercent: 11.0, threatLevel: .moderate),
                CompetitorComparison(name: "SAP", ticker: "SAP", competitiveScore: 7.0, marketSharePercent: 5.0, threatLevel: .low)
            ],
            competitiveInsight: "Oracle holds dominant position in enterprise databases but faces intense hyperscaler competition in cloud infrastructure. Switching cost moat remains the primary defensive asset."
        ),
        macroData: ReportMacroData(
            overallThreatLevel: .elevated,
            headline: "Elevated macro risk from rate policy and US-China trade tensions",
            riskFactors: [
                MacroRiskFactor(
                    category: .interestRates,
                    title: "Fed Rate Uncertainty",
                    impact: 0.72,
                    description: "Higher-for-longer rates pressure growth stock valuations and increase Oracle's debt servicing costs on $86B long-term debt.",
                    trend: .stable,
                    severity: .high
                ),
                MacroRiskFactor(
                    category: .tariffs,
                    title: "US-China Tech Restrictions",
                    impact: 0.65,
                    description: "Export controls on advanced chips may constrain Oracle's AI infrastructure buildout timeline and increase hardware costs.",
                    trend: .worsening,
                    severity: .severe
                ),
                MacroRiskFactor(
                    category: .currency,
                    title: "USD Strength",
                    impact: 0.40,
                    description: "Strong dollar headwind on international revenue (37% of total). Each 1% USD rise impacts revenue by ~$180M annually.",
                    trend: .stable,
                    severity: .elevated
                ),
                MacroRiskFactor(
                    category: .regulation,
                    title: "AI Regulation Wave",
                    impact: 0.55,
                    description: "EU AI Act and potential US frameworks could increase compliance costs for Oracle's AI cloud services.",
                    trend: .worsening,
                    severity: .high
                ),
                MacroRiskFactor(
                    category: .inflation,
                    title: "Data Center Cost Inflation",
                    impact: 0.58,
                    description: "Rising construction and energy costs inflating Capex per data center by an estimated 12-18% YoY.",
                    trend: .improving,
                    severity: .elevated
                ),
                MacroRiskFactor(
                    category: .energy,
                    title: "Power Grid Constraints",
                    impact: 0.45,
                    description: "Growing energy demand for AI data centers straining regional power grids, potentially delaying new facility deployments.",
                    trend: .worsening,
                    severity: .elevated
                )
            ],
            intelligenceBrief: "Oracle's macro exposure is concentrated in two vectors: debt sensitivity to rate policy (largest corporate bond issuer in tech) and supply chain vulnerability to US-China decoupling. The company's aggressive $80B+ Capex plan amplifies both risks. Mitigating factor: 72% of revenue is recurring subscription, providing cash flow resilience. Monitor the March Fed meeting and any escalation in semiconductor export controls.",
            lastUpdated: "Updated Feb 8, 2026"
        ),
        wallStreetConsensus: ReportWallStreetConsensus(
            rating: .strongBuy,
            currentPrice: 142,
            targetPrice: 190,
            lowTarget: 140,
            highTarget: 250,
            valuationStatus: .deepUndervalued,
            discountPercent: 33.4,
            hedgeFundNote: "Net inflow of $430M from institutional investors last quarter.",
            hedgeFundPriceData: [
                StockPriceDataPoint(month: "02/2025", price: 163.20),
                StockPriceDataPoint(month: "03/2025", price: 162.80),
                StockPriceDataPoint(month: "04/2025", price: 161.40),
                StockPriceDataPoint(month: "05/2025", price: 160.80),
                StockPriceDataPoint(month: "06/2025", price: 159.20),
                StockPriceDataPoint(month: "07/2025", price: 155.30),
                StockPriceDataPoint(month: "08/2025", price: 150.10),
                StockPriceDataPoint(month: "09/2025", price: 148.60),
                StockPriceDataPoint(month: "10/2025", price: 145.20),
                StockPriceDataPoint(month: "11/2025", price: 143.80),
                StockPriceDataPoint(month: "12/2025", price: 141.50),
                StockPriceDataPoint(month: "01/2026", price: 142.82)
            ],
            hedgeFundFlowData: [
                SmartMoneyFlowDataPoint(month: "02/2025", buyVolume: 42.1, sellVolume: 35.2),
                SmartMoneyFlowDataPoint(month: "03/2025", buyVolume: 38.5, sellVolume: 42.1),
                SmartMoneyFlowDataPoint(month: "04/2025", buyVolume: 35.2, sellVolume: 48.3),
                SmartMoneyFlowDataPoint(month: "05/2025", buyVolume: 48.9, sellVolume: 32.5),
                SmartMoneyFlowDataPoint(month: "06/2025", buyVolume: 45.2, sellVolume: 38.5),
                SmartMoneyFlowDataPoint(month: "07/2025", buyVolume: 39.8, sellVolume: 45.2),
                SmartMoneyFlowDataPoint(month: "08/2025", buyVolume: 52.1, sellVolume: 41.3),
                SmartMoneyFlowDataPoint(month: "09/2025", buyVolume: 44.5, sellVolume: 38.9),
                SmartMoneyFlowDataPoint(month: "10/2025", buyVolume: 38.9, sellVolume: 55.2),
                SmartMoneyFlowDataPoint(month: "11/2025", buyVolume: 51.2, sellVolume: 36.8),
                SmartMoneyFlowDataPoint(month: "12/2025", buyVolume: 48.5, sellVolume: 33.2),
                SmartMoneyFlowDataPoint(month: "01/2026", buyVolume: 55.8, sellVolume: 31.2)
            ],
            hedgeFundSmartMoney: SmartMoneyData.hedgeFundsSampleData,
            momentumUpgrades: 8,
            momentumDowngrades: 3
        ),
        criticalFactors: [
            CriticalFactor(
                title: "Credit Agency Downgrade",
                description: "Monitor S&P and Moody's ratings for debt-laden companies.",
                severity: .high
            ),
            CriticalFactor(
                title: "Accounting Depreciation Changes",
                description: "Watch for shifts in infrastructure depreciation schedules.",
                severity: .medium
            ),
            CriticalFactor(
                title: "Capex vs Revenue Trajectory",
                description: "Track if cloud revenue growth catches up to infrastructure spend.",
                severity: .medium
            )
        ],
        disclaimerText: "This analysis is for educational purposes only and does not constitute financial advice. AI-generated content may be inaccurate. Always conduct your own research and consult with a qualified financial advisor before making investment decisions."
    )
}
