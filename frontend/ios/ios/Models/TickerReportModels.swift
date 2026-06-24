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
    case ackman = "ANALYZED BY ACKMAN AGENT"

    var starRating: Double {
        switch self {
        case .buffett: return 4.0
        case .wood: return 3.5
        case .lynch: return 4.5
        case .ackman: return 4.0
        }
    }
}

// MARK: - Report Quality Rating

/// The five headline quality tiers. SINGLE SOURCE OF TRUTH for the label, the
/// gauge color, AND the band boundaries — all keyed off the SAME rounded
/// integer the gauge displays, so the number, label, and color can never
/// disagree. (Before this, the gauge printed `%.0f` (49.6 → "50") while the
/// label/color switched on the raw Double (49.6 → 30..<50 → "Weak"/orange),
/// producing the "50 shown under a Weak/orange arc" boundary bug at every cut
/// point.) Bands are integer ranges because the displayed score is an integer.
enum QualityBand {
    case excellent, strong, fair, weak, distressed

    /// Map an already-rounded 0–100 score to its band.
    static func forScore(_ score: Int) -> QualityBand {
        switch score {
        case 90...:   return .excellent
        case 75...89: return .strong
        case 50...74: return .fair
        case 30...49: return .weak
        default:      return .distressed   // < 30
        }
    }

    var label: String {
        switch self {
        case .excellent:  return "Excellent Quality Business"
        case .strong:     return "Strong Quality Business"
        case .fair:       return "Fair Quality Business"
        case .weak:       return "Weak Quality Business"
        case .distressed: return "Distressed Quality Business"
        }
    }

    var color: Color {
        switch self {
        case .excellent, .strong: return AppColors.bullish
        case .fair:               return AppColors.neutral
        case .weak:               return AppColors.alertOrange
        case .distressed:         return AppColors.bearish
        }
    }
}

struct ReportQualityRating {
    let score: Double       // raw 0-100 (continuous; drives the gauge arc fill)
    let maxScore: Double    // 100

    /// The integer actually shown in the gauge. Rounded ONCE here so the
    /// displayed number, the label, and the color all derive from this single
    /// value and stay mutually consistent.
    var displayScore: Int { Int(score.rounded()) }

    /// Band of the *displayed* (rounded) score — not the raw Double.
    var band: QualityBand { QualityBand.forScore(displayScore) }

    /// Auto-generated from `band`, so it always agrees with the shown number.
    var label: String { band.label }

    var formattedScore: String { "\(displayScore)" }
    var formattedMax: String { "/ \(Int(maxScore))" }

    init(score: Double, maxScore: Double = 100) {
        self.score = score
        self.maxScore = maxScore
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
    // Stable identity = the (unique) card title. Using a fresh UUID per decode
    // would make `.sheet(item:)` dismiss the open drill-down whenever the
    // report re-decodes in the background.
    var id: String { title }
    let title: String           // "Profitability", "Valuation", "Growth", "Health"
    let starRating: Int         // 1-5
    let metrics: [DeepDiveMetric]
    let qualityLabel: String    // "A Cash Machine", "Priced for perfection", etc.
    /// Sentiment of `qualityLabel` ("positive" | "negative" | "neutral"),
    /// derived server-side. Drives the footer COLOR independently of the star
    /// rating (which mirrors the Financials tab and can disagree with the
    /// takeaway — e.g. a 4★ Health card whose footer is "Debt 4.21, Far Too High").
    let qualitySentiment: String
    /// "industry" / "sector" / nil — the peer group this card's benchmark
    /// comparisons use. Drives the "vs industry/sector" footnote + drill-down
    /// legend wording. nil (legacy reports) → treated as "sector".
    let peerGroupLevel: String?

    init(
        title: String,
        starRating: Int,
        metrics: [DeepDiveMetric],
        qualityLabel: String,
        qualitySentiment: String = "neutral",
        peerGroupLevel: String? = nil
    ) {
        self.title = title
        self.starRating = starRating
        self.metrics = metrics
        self.qualityLabel = qualityLabel
        self.qualitySentiment = qualitySentiment
        self.peerGroupLevel = peerGroupLevel
    }

    /// True when any metric in this card has been compared to the sector
    /// average (and therefore renders with a trailing " *"). Drives the
    /// asterisk footnote below the 2x2 grid.
    var hasSectorComparison: Bool {
        // Matches the non-breaking-space suffix `displayLabel` appends (U+00A0).
        metrics.contains { $0.displayLabel.hasSuffix("\u{00A0}*") }
    }

    /// Metrics in this card that carry a chartable time series — drives the
    /// drill-down's metric picker.
    var chartableMetrics: [DeepDiveMetric] { metrics.filter(\.hasHistory) }

    /// True when at least one metric has history → the card is tappable and
    /// opens the time-series drill-down. Legacy reports → false (inert card).
    var hasHistory: Bool { metrics.contains(where: \.hasHistory) }
}

// MARK: - Deep Dive Metric

/// One point in a fundamentals metric's time series (oldest→newest).
struct MetricHistoryPoint: Identifiable {
    let id = UUID()
    let period: String   // "2024" (annual) or "Q1 '24" (quarterly)
    let value: Double?    // nil = no datapoint for that period
}

struct DeepDiveMetric: Identifiable {
    let id = UUID()
    let label: String
    let value: String
    let trend: MetricTrend?
    // ── Tap-to-expand history (optional; baked into the frozen report) ──
    // Absent on legacy/cached reports → the metric simply isn't chartable.
    // `historyUnit` ∈ {"percent","x","score"} drives axis/value formatting.
    // Defaults keep the memberwise init backward-compatible (mocks call
    // `DeepDiveMetric(label:value:trend:)`).
    let historyKey: String?
    let historyUnit: String?
    let annualHistory: [MetricHistoryPoint]?
    let quarterlyHistory: [MetricHistoryPoint]?
    // Sector-average overlay (the "*" metrics), aligned to the company periods.
    let sectorAnnualHistory: [MetricHistoryPoint]?
    let sectorQuarterlyHistory: [MetricHistoryPoint]?

    init(
        label: String,
        value: String,
        trend: MetricTrend?,
        historyKey: String? = nil,
        historyUnit: String? = nil,
        annualHistory: [MetricHistoryPoint]? = nil,
        quarterlyHistory: [MetricHistoryPoint]? = nil,
        sectorAnnualHistory: [MetricHistoryPoint]? = nil,
        sectorQuarterlyHistory: [MetricHistoryPoint]? = nil
    ) {
        self.label = label
        self.value = value
        self.trend = trend
        self.historyKey = historyKey
        self.historyUnit = historyUnit
        self.annualHistory = annualHistory
        self.quarterlyHistory = quarterlyHistory
        self.sectorAnnualHistory = sectorAnnualHistory
        self.sectorQuarterlyHistory = sectorQuarterlyHistory
    }

    /// True when a sector-average series exists for this metric (the "*"
    /// metrics with benchmark coverage) → the drill-down overlays a sector line.
    var hasSector: Bool {
        (sectorAnnualHistory?.contains { $0.value != nil } ?? false)
            || (sectorQuarterlyHistory?.contains { $0.value != nil } ?? false)
    }

    /// True when this metric has a chartable series (≥2 real points in EITHER
    /// granularity). Drives whether the parent card is tappable and whether this
    /// metric appears in the drill-down's metric picker. Quarterly is included so
    /// a metric with rich quarterly history but <2 annual points (e.g. P/FCF
    /// gated to None in most fiscal years, or a recently-IPO'd company) is still
    /// reachable rather than silently dropped from the drill-down.
    var hasHistory: Bool {
        (annualHistory?.compactMap(\.value).count ?? 0) >= 2
            || (quarterlyHistory?.compactMap(\.value).count ?? 0) >= 2
    }

    /// True when the metric is chartable at ANNUAL granularity (≥2 real annual
    /// points). Drives the initial A/Q toggle so a quarterly-only metric opens on
    /// Quarterly instead of an empty "Not enough history" annual chart.
    var hasAnnualHistory: Bool {
        (annualHistory?.compactMap(\.value).count ?? 0) >= 2
    }

    /// Clean metric title for the chart header / picker — strips the sector
    /// suffix and "(YoY)" but keeps full words (unlike the compact
    /// `displayLabel` used in the narrow card grid).
    var historyTitle: String {
        label
            .replacingOccurrences(
                of: #"\s*\([^)]*sector[^)]*\)"#, with: "",
                options: .regularExpression)
            .replacingOccurrences(
                of: #"\s*\(YoY\)"#, with: "", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
    }

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
        // Non-breaking space (U+00A0) before the "*" so the asterisk never word-
        // wraps onto its own line in the narrow 2-col card (e.g. "Interest
        // Coverage *"). It always travels with the last word of the label.
        return hadSectorSuffix ? "\(result)\u{00A0}*" : result
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

struct EarningsTrackRecordPoint: Identifiable {
    let id = UUID()
    let period: String            // "Q1 '24"
    let surprisePercent: Double   // EPS surprise: signed beat (+) / miss (−), %
    let beat: Bool

    /// Signed EPS surprise for the cell, e.g. "+5.2%" / "-3.0%".
    var surpriseText: String {
        String(format: "%+.1f%%", surprisePercent)
    }
}

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
    // Stage-B narrative explaining WHY the forward trajectory looks the way
    // it does. nil on older cached reports / the fallback path — the view
    // hides the Insight card when nil or empty.
    let insight: String?
    // Earnings beat/miss track record — last ~6 reported quarters vs estimate.
    // `var` with defaults so sample/memberwise inits need no change.
    var earningsTrackRecord: [EarningsTrackRecordPoint] = []
    var beatSummary: String? = nil      // "Beat 6 of 8"
    // ONE gapless yearly series (historical actuals → all forward estimates)
    // for the Earnings Timeline sheet. Empty on older cached reports.
    var annualTimeline: [RevenueProjection] = []
    // Analysts behind the nearest forecast year — forecast attribution.
    var forecastAnalystCount: Int? = nil
    // Monthly close series (FROZEN at generation) for the Earnings Timeline
    // PRICE overlay — embedded in the report so the panel never fetches
    // /earnings live (which would show today's prices on an old report).
    var timelinePrices: [EarningsDailyPricePoint] = []

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
    let revenueAnalystCount: Int? // analysts behind a forecast year; nil on actuals
    let epsAnalystCount: Int?     // analysts behind a forecast year; nil on actuals
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

struct ReportCapitalAllocation {
    let buybackStatus: String     // Diluting / … / Very High
    let dividendStatus: String    // Low / Fair / High / Very High
    let dividendYield: Double
    let buybackYield: Double
    let totalYield: Double
    let shareCountChange: Double  // % (negative = shrinking via buybacks)
    // Per-quarter series (same data as the Financials-tab chart). Empty when
    // Signal of Confidence is unavailable → the card renders numbers-only.
    let dataPoints: [SignalOfConfidenceDataPoint]

    /// Sentiment of the buyback status → drives the chip color (green/red).
    var buybackSentiment: String {
        let s = buybackStatus.lowercased()
        if s.contains("dilut") { return "negative" }
        if s == "high" || s == "very high" || s == "moderate" { return "positive" }
        return "neutral"
    }

    var dividendYieldText: String { String(format: "%.2f%%", dividendYield) }
    var shareCountChangeText: String {
        String(format: "%@%.1f%%", shareCountChange >= 0 ? "+" : "", shareCountChange)
    }

    /// ≥2 quarters available → the mini-chart can render.
    var hasTrend: Bool { dataPoints.count >= 2 }

    /// Newest quarter's gross buyback spend (data points are oldest→newest, so
    /// `.last` is the most recent). Green when the company bought back stock;
    /// "$0" white when it didn't — a plain spend figure, distinct from the
    /// net-dilution verdict (which lives on Share Count).
    var newestBuybackText: String {
        guard let amt = dataPoints.last?.buybackAmount, amt > 0 else { return "$0" }
        if amt >= 1000 { return String(format: "$%.1fB", amt / 1000) }  // amt is $ millions
        if amt >= 1 { return String(format: "$%.0fM", amt) }
        return String(format: "$%.1fM", amt)
    }
    var newestBuybackColor: Color {
        (dataPoints.last?.buybackAmount ?? 0) > 0
            ? AppColors.confidenceBuybacks   // green, matches the buyback bars
            : AppColors.textPrimary          // white "$0", like the dividend yield
    }

    /// Net share-count read: the % first, with a verdict in parens ONLY when the
    /// change is meaningful — beyond ±2%, the same threshold the backend uses to
    /// flag dilution. e.g. "+3.7% (Diluting)", "-4.1% (Reducing)", or just
    /// "+1.2%" inside the ±2% noise band. NET read: a company can spend on
    /// buybacks yet still read "Diluting" if stock-comp issuance outpaced them.
    private var _shareCountDiluting: Bool { shareCountChange > 2.0 }
    private var _shareCountReducing: Bool { shareCountChange < -2.0 }
    var shareCountVerdictText: String {
        if _shareCountDiluting { return "\(shareCountChangeText) (Diluting)" }
        if _shareCountReducing { return "\(shareCountChangeText) (Reducing)" }
        return shareCountChangeText
    }
    var shareCountVerdictColor: Color {
        if _shareCountDiluting { return AppColors.bearish }
        if _shareCountReducing { return AppColors.bullish }
        return AppColors.textSecondary
    }
}

struct ReportInsiderData {
    let sentiment: InsiderSentiment
    let timeframe: String           // "Last 12 Months"
    let transactions: [InsiderTransaction]
    let ownershipNote: String?      // "The stock is heavily sold off by insiders."
    var capitalAllocation: ReportCapitalAllocation? = nil
    // Insider trend chart + recent trades (reused from the Holders tab, same
    // numbers). nil / empty → those blocks hide. Compact, not the full tab.
    var insiderFlow: SmartMoneyData? = nil
    var recentTransactions: [InsiderActivity] = []
}

// MARK: - Key Management

struct KeyManager: Identifiable {
    let id = UUID()
    let name: String
    let title: String
    let ownership: String       // e.g. "40.3%", "$2,025", etc.
    let ownershipValue: String  // dollar amount or additional info
    let percentOwnership: Double?  // 13G beneficial %, nil for non-5%-filers
    // Direct ownership % (shares / shares outstanding). Shown for OFFICERS in
    // the right column ("0.43% / 1.0M"); top holders use the 13G chip instead.
    var percentOwned: Double? = nil

    var percentOwnershipLabel: String? {
        guard let pct = percentOwnership, pct > 0 else { return nil }
        return String(format: "%.0f%% owner", pct)
    }

    /// Significant-figure % string for the direct-ownership figure (officers
    /// can be tiny, e.g. "0.0032%"); below 0.001% it collapses to "<0.001%".
    var formattedPercentOwned: String? {
        guard let pct = percentOwned, pct > 0 else { return nil }
        if pct < 0.001 { return "<0.001%" }
        let fmt: String
        switch pct {
        case 10...:       fmt = "%.1f%%"
        case 1..<10:      fmt = "%.2f%%"
        case 0.1..<1:     fmt = "%.2f%%"
        case 0.01..<0.1:  fmt = "%.3f%%"
        default:          fmt = "%.4f%%"   // 0.001 ..< 0.01
        }
        return String(format: fmt, pct)
    }

    /// Right-column primary text. Officers (no 13G chip) show "pct% / shares";
    /// top holders show just the share count (the chip carries their %).
    var ownershipPrimaryText: String {
        if let pctText = formattedPercentOwned, percentOwnershipLabel == nil {
            return "\(pctText) / \(ownership)"
        }
        return ownership
    }
}

struct ReportKeyManagement {
    let topHolders: [KeyManager]    // 10%+ owners (paired with 13G filings)
    let officers: [KeyManager]      // sorted CEO → CFO → COO → … → directors
    let ownershipInsight: String    // "Oracle's high ownership ensures long-term thinking..."
}

// MARK: - Wall Street Consensus

enum ConsensusRating: String {
    case strongBuy = "STRONG BUY"
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

// MARK: - Valuation Status (Wall Street Consensus)

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
    // AI "Insight": synthesis across price targets, institutions, and momentum.
    let wallStreetInsight: String?
    // NAMING: `hedgeFund*` = FMP 13F institutional-ownership data, rendered in the
    // report's "Institutions" section (SmartMoneyTab.hedgeFunds = "Institutions").
    let hedgeFundPriceData: [StockPriceDataPoint]   // Price data for hedge fund chart
    let hedgeFundFlowData: [SmartMoneyFlowDataPoint] // Buy/sell volume data (legacy monthly fallback)
    // Quarterly institutional flow mirrored from the Holders tab. When
    // present, the Hedge Funds chart renders this (quarterly bars +
    // net-flow badge) instead of the monthly fallback above.
    let hedgeFundSmartMoney: SmartMoneyData?
    let momentumUpgrades: Int
    let momentumDowngrades: Int
    let momentumMaintains: Int  // analyst "maintain"/reiterate count (trailing 12mo)
    // Analyst rating distribution (one grade per firm), aggregated to Buy/Hold/Sell
    // below for the consensus bar.
    let analystStrongBuy: Int
    let analystBuy: Int
    let analystHold: Int
    let analystSell: Int
    let analystStrongSell: Int

    // MARK: Analyst consensus distribution (5 levels)
    var analystTotalRatings: Int {
        analystStrongBuy + analystBuy + analystHold + analystSell + analystStrongSell
    }
    var hasAnalystDistribution: Bool { analystTotalRatings > 0 }

    /// The 5 rating levels with the SAME colors as the Analysis tab
    /// (`StockRepository` distColors). Reuses `AnalystRatingDistribution` so the
    /// report bar and the Analysis tab share one model + palette and stay in sync.
    var analystLevels: [AnalystRatingDistribution] {
        // Ordered most-bearish → most-bullish: Strong Sell on the left, Strong Buy
        // on the right, Hold in the middle. Colors travel with each level, so this
        // reorders both the bar and the legend together.
        [
            AnalystRatingDistribution(label: "Strong Sell", count: analystStrongSell, color: Color(hex: "B91C1C")),
            AnalystRatingDistribution(label: "Sell", count: analystSell, color: AppColors.bearish),
            AnalystRatingDistribution(label: "Hold", count: analystHold, color: AppColors.neutral),
            AnalystRatingDistribution(label: "Buy", count: analystBuy, color: Color(hex: "4ADE80")),
            AnalystRatingDistribution(label: "Strong Buy", count: analystStrongBuy, color: AppColors.bullish),
        ]
    }

    /// Percentage (0–100) of total ratings for a bucket count.
    func analystPercent(_ count: Int) -> Double {
        analystTotalRatings > 0 ? Double(count) / Double(analystTotalRatings) * 100 : 0
    }

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
    let description: String          // short SIGNAL — what's notable + why
    let severity: CriticalSeverity   // priority to watch
    let watch: String?               // forward-looking action; nil → hide line

    enum CriticalSeverity {
        case high, medium, low

        // Calm, non-red "watch" palette — priority is conveyed by a subtle
        // color (high amber → medium gold → low blue) so the list reads as
        // "things to monitor" rather than "something is wrong".
        var color: Color {
            switch self {
            case .high: return AppColors.alertOrange
            case .medium: return AppColors.neutral
            case .low: return AppColors.primaryBlue
            }
        }

        // One calm "watch" symbol for every priority — no alarm triangles.
        var iconName: String { "eye.fill" }
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
    // Scope of the TAM figure: "us" or "global". Drives the explicit "US"/
    // "Global" pill next to the market size. Nil when TAM wasn't populated
    // (older cached reports / no source) → no pill shown.
    let tamScope: String?

    /// "Global" / "US" scope label, or nil when scope is unknown.
    var scopeLabel: String? {
        switch tamScope?.lowercased() {
        case "global": return "Global"
        case "us": return "US"
        default: return nil
        }
    }

    /// Market-size column header, scope-prefixed when known:
    /// "US - Market Size (TAM)" / "Global - Market Size (TAM)".
    var tamHeaderLabel: String {
        scopeLabel.map { "\($0) - Market Size (TAM)" } ?? "Market Size (TAM)"
    }

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
    case recession = "Recession"
    case credit = "Credit"
    case volatility = "Market Volatility"

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
        case .recession: return "chart.line.downtrend.xyaxis"
        case .credit: return "creditcard.trianglebadge.exclamationmark"
        case .volatility: return "waveform.path.ecg"
        }
    }
}

enum RiskTrend: String {
    case improving = "Improving"
    case stable = "Stable"
    case worsening = "Worsening"

    var iconName: String {
        switch self {
        // "up = good, down = bad" convention (pairs with the green/red color):
        // improving trends up, worsening trends down.
        case .improving: return "arrow.up.right"
        case .stable: return "arrow.right"
        case .worsening: return "arrow.down.right"
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

// MARK: - Hidden Market Signals (congress trades + short interest)

struct CongressSignal {
    let numBuyers: Int
    let numSellers: Int
    let totalBuysInMillions: Double
    let totalSellsInMillions: Double
    let netDirection: String   // "buy" | "sell" | "balanced"
    let period: String         // "Last 12 Months"
    let trades: [CongressActivity]   // individual trades (who traded), last 12 months
}

struct ShortInterestPoint: Identifiable {
    let id = UUID()
    let settlementDate: String?
    let sharesShort: Double?
    let daysToCover: Double?
}

struct ShortInterestSignal {
    let percentOfFloat: Double?
    let daysToCover: Double?
    let sharesShort: Double?
    let change3m: Double?       // % vs ~3 months ago
    let settlementDate: String?
    let history: [ShortInterestPoint]  // up to 24 points (~12 months biweekly); empty → no chart
}

struct ReportHiddenMarketSignals {
    let congress: CongressSignal?
    let shortInterest: ShortInterestSignal?
    let insight: String
}

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
    case hiddenMarketSignals
}

// MARK: - Full Report Data

struct TickerReportData: Identifiable {
    let id = UUID()
    let symbol: String
    let companyName: String
    let exchange: String
    let logoName: String?
    let liveDate: String
    /// ISO "yyyy-MM-dd" of the last completed market close the price reflects.
    /// The header formats "Previous Close · …" render-time from this; nil →
    /// falls back to `liveDate`. `var` with a default keeps it overridable in
    /// the memberwise init, so existing mocks compile unchanged.
    var priceCloseDate: String? = nil

    // Agent & Rating
    let agent: ReportAgentPersona
    let qualityRating: ReportQualityRating

    // Executive Summary
    let executiveSummaryText: String
    let executiveSummaryBullets: [ExecutiveSummaryBullet]

    // Core Thesis
    let coreThesis: ReportCoreThesis

    // Deep Dive: Fundamentals
    let fundamentalMetrics: [DeepDiveMetricCard]
    // Rich Growth chart (parity with the free Growth chart) — drives the
    // redesigned full-width Growth card. nil on legacy reports → falls back to
    // the compact grid card.
    var growthChart: GrowthSectionData? = nil
    // The 4 MARGIN series (gross/operating/net/fcf) for the Profitability drill-down,
    // built from the frozen `profit_power` so they match the free Profit Power chart.
    // ROE/ROA are added in the sheet from the Profitability card's baked history.
    // nil on legacy reports → the card falls back to FundamentalsHistorySheet.
    var profitabilityMarginSeries: [ProfitabilityMetricSeries]? = nil
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

    // Deep Dive: Hidden Market Signals (nil → module hidden)
    var hiddenMarketSignals: ReportHiddenMarketSignals? = nil

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
        priceCloseDate: "2026-02-06",
        agent: .buffett,
        qualityRating: ReportQualityRating(
            score: 82
        ),
        executiveSummaryText: "Oracle is a legacy enterprise-software leader re-platforming its business around cloud infrastructure (OCI). Profitability stays strong, but the balance sheet is stretched and free cash flow has turned negative. Overall, this report weighs a credible cloud-growth story against real balance-sheet and valuation risk.",
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
        coreThesis: ReportCoreThesis(
            bullCase: [
                CoreThesisBullet(text: "70.51% gross margin shows durable enterprise-software pricing power"),
                CoreThesisBullet(text: "Cloud infrastructure growing 66% YoY, capturing enterprise AI workloads"),
                CoreThesisBullet(text: "~30% operating margin funds the cloud buildout from core profits")
            ],
            bearCase: [
                CoreThesisBullet(text: "Free cash flow is negative (−$394M) as capex outpaces operating cash"),
                CoreThesisBullet(text: "Heavy leverage: 4.21 debt-to-equity against negative free cash flow"),
                CoreThesisBullet(text: "Rich valuation: 41.63 P/E prices in growth that isn't yet proven")
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
                RevenueProjection(period: "2026", revenue: 67,  revenueLabel: "$67B",  revenueYoyPct: 18, eps: 7.48,  epsLabel: "$7.48",  epsYoyPct: 25, revenueAnalystCount: nil, epsAnalystCount: nil, isForecast: false),
                RevenueProjection(period: "2027", revenue: 89,  revenueLabel: "$89B",  revenueYoyPct: 32, eps: 7.99,  epsLabel: "$7.99",  epsYoyPct: 7,  revenueAnalystCount: 31, epsAnalystCount: 30, isForecast: true),
                RevenueProjection(period: "2028", revenue: 130, revenueLabel: "$130B", revenueYoyPct: 46, eps: 10.76, epsLabel: "$10.76", epsYoyPct: 35, revenueAnalystCount: 28, epsAnalystCount: 27, isForecast: true),
                RevenueProjection(period: "2029", revenue: 179, revenueLabel: "$179B", revenueYoyPct: 37, eps: 15.14, epsLabel: "$15.14", epsYoyPct: 41, revenueAnalystCount: 22, epsAnalystCount: 20, isForecast: true)
            ],
            guidanceQuote: "CFO expects accelerating cloud demand in Q3",
            guidanceSpeaker: "CFO",
            guidancePeriod: "Q3 2026",
            insight: "Revenue is projected to compound ~15% to $179B by 2029 as cloud demand outruns the maturing license base, with EPS growing faster (+18% CAGR) on operating leverage. Management raising guidance signals real confidence the backlog converts. The steepening 2028 ramp is the swing factor — execution risk concentrates there.",
            earningsTrackRecord: [
                EarningsTrackRecordPoint(period: "Q2 '24", surprisePercent: -1.8, beat: false),
                EarningsTrackRecordPoint(period: "Q3 '24", surprisePercent: 2.4, beat: true),
                EarningsTrackRecordPoint(period: "Q4 '24", surprisePercent: 5.1, beat: true),
                EarningsTrackRecordPoint(period: "Q1 '25", surprisePercent: -3.2, beat: false),
                EarningsTrackRecordPoint(period: "Q2 '25", surprisePercent: 1.2, beat: true),
                EarningsTrackRecordPoint(period: "Q3 '25", surprisePercent: 4.6, beat: true),
                EarningsTrackRecordPoint(period: "Q4 '25", surprisePercent: 3.0, beat: true),
                EarningsTrackRecordPoint(period: "Q1 '26", surprisePercent: -0.7, beat: false),
                EarningsTrackRecordPoint(period: "Q2 '26", surprisePercent: 6.3, beat: true),
                EarningsTrackRecordPoint(period: "Q3 '26", surprisePercent: 2.9, beat: true)
            ],
            beatSummary: "Beat 7 of 10",
            annualTimeline: [
                RevenueProjection(period: "2023", revenue: 50,  revenueLabel: "$50.0B",  revenueYoyPct: nil, eps: 5.10,  epsLabel: "$5.10",  epsYoyPct: nil, revenueAnalystCount: nil, epsAnalystCount: nil, isForecast: false),
                RevenueProjection(period: "2024", revenue: 53,  revenueLabel: "$53.0B",  revenueYoyPct: 6,   eps: 5.50,  epsLabel: "$5.50",  epsYoyPct: 8,   revenueAnalystCount: nil, epsAnalystCount: nil, isForecast: false),
                RevenueProjection(period: "2025", revenue: 57,  revenueLabel: "$57.4B",  revenueYoyPct: 8,   eps: 6.00,  epsLabel: "$6.00",  epsYoyPct: 9,   revenueAnalystCount: nil, epsAnalystCount: nil, isForecast: false),
                RevenueProjection(period: "2026", revenue: 67,  revenueLabel: "$67.3B",  revenueYoyPct: 17,  eps: 7.48,  epsLabel: "$7.48",  epsYoyPct: 25,  revenueAnalystCount: 31, epsAnalystCount: 30, isForecast: true),
                RevenueProjection(period: "2027", revenue: 89,  revenueLabel: "$88.8B",  revenueYoyPct: 32,  eps: 7.99,  epsLabel: "$7.99",  epsYoyPct: 7,   revenueAnalystCount: 28, epsAnalystCount: 27, isForecast: true),
                RevenueProjection(period: "2028", revenue: 130, revenueLabel: "$130.0B", revenueYoyPct: 46,  eps: 10.76, epsLabel: "$10.76", epsYoyPct: 35,  revenueAnalystCount: 22, epsAnalystCount: 20, isForecast: true)
            ],
            forecastAnalystCount: 31,
            timelinePrices: [
                EarningsDailyPricePoint(date: "2023-06-30", price: 118.0),
                EarningsDailyPricePoint(date: "2023-12-29", price: 105.0),
                EarningsDailyPricePoint(date: "2024-06-28", price: 140.0),
                EarningsDailyPricePoint(date: "2024-12-31", price: 166.0),
                EarningsDailyPricePoint(date: "2025-06-30", price: 210.0),
                EarningsDailyPricePoint(date: "2025-12-31", price: 195.0)
            ]
        ),
        insiderData: ReportInsiderData(
            sentiment: .negative,
            timeframe: "Last 12 Months",
            transactions: [
                InsiderTransaction(type: "Buys", count: 3, shares: "12", value: "$1,234"),
                InsiderTransaction(type: "Sells", count: 12, shares: "45", value: "$4.1M")
            ],
            ownershipNote: "The stock is heavily sold off by insiders.",
            // Oracle's capital-allocation story: small dividend, modest buybacks,
            // but a share count rising +3.7% over the window → "Diluting".
            capitalAllocation: ReportCapitalAllocation(
                buybackStatus: "Diluting",
                dividendStatus: "Low",
                dividendYield: 0.93,
                buybackYield: 0.42,
                totalYield: 1.35,
                shareCountChange: 3.7,
                dataPoints: [
                    SignalOfConfidenceDataPoint(period: "Q3 '23", dividendYield: 0.90, buybackYield: 0.30, dividendAmount: 1100, buybackAmount: 360, sharesOutstanding: 1000),
                    SignalOfConfidenceDataPoint(period: "Q4 '23", dividendYield: 0.91, buybackYield: 0.35, dividendAmount: 1120, buybackAmount: 420, sharesOutstanding: 1006),
                    SignalOfConfidenceDataPoint(period: "Q1 '24", dividendYield: 0.92, buybackYield: 0.28, dividendAmount: 1130, buybackAmount: 340, sharesOutstanding: 1012),
                    SignalOfConfidenceDataPoint(period: "Q2 '24", dividendYield: 0.93, buybackYield: 0.45, dividendAmount: 1150, buybackAmount: 540, sharesOutstanding: 1019),
                    SignalOfConfidenceDataPoint(period: "Q3 '24", dividendYield: 0.92, buybackYield: 0.40, dividendAmount: 1160, buybackAmount: 480, sharesOutstanding: 1026),
                    SignalOfConfidenceDataPoint(period: "Q4 '24", dividendYield: 0.93, buybackYield: 0.50, dividendAmount: 1180, buybackAmount: 610, sharesOutstanding: 1031),
                    SignalOfConfidenceDataPoint(period: "Q1 '25", dividendYield: 0.93, buybackYield: 0.44, dividendAmount: 1190, buybackAmount: 560, sharesOutstanding: 1037)
                ]
            ),
            insiderFlow: SmartMoneyData(
                tab: .insider,
                priceData: [],
                dailyPrices: [],
                flowData: [
                    SmartMoneyFlowDataPoint(month: "08/2025", buyVolume: 0.0, sellVolume: 0.5),
                    SmartMoneyFlowDataPoint(month: "09/2025", buyVolume: 0.2, sellVolume: 0.0),
                    SmartMoneyFlowDataPoint(month: "10/2025", buyVolume: 0.0, sellVolume: 1.2),
                    SmartMoneyFlowDataPoint(month: "11/2025", buyVolume: 0.0, sellVolume: 0.8),
                    SmartMoneyFlowDataPoint(month: "12/2025", buyVolume: 0.1, sellVolume: 0.0),
                    SmartMoneyFlowDataPoint(month: "01/2026", buyVolume: 0.0, sellVolume: 0.015)
                ],
                summary: SmartMoneyFlowSummary(totalNetFlow: -2.2, totalBuy: 0.3, totalSell: 2.5, isPositive: false, periodDescription: "12-Month", unit: .shares)
            ),
            recentTransactions: Array(InsiderActivity.sampleData.prefix(5))
        ),
        keyManagement: ReportKeyManagement(
            topHolders: [
                KeyManager(name: "Lawrence Joseph Ellison", title: "director, 10 percent owner, Executive Chairman", ownership: "1.16B", ownershipValue: "$214.5B", percentOwnership: 43)
            ],
            officers: [
                KeyManager(name: "Dietrich Niebuhr", title: "Chief Executive Officer", ownership: "1.0M", ownershipValue: "$192.3M", percentOwnership: nil, percentOwned: 0.037),
                KeyManager(name: "Marla Smith", title: "Chief Financial Officer", ownership: "224K", ownershipValue: "$41.6M", percentOwnership: nil, percentOwned: 0.0083),
                KeyManager(name: "Dania Caral", title: "Pres., Global Field Operations", ownership: "249K", ownershipValue: "$46.2M", percentOwnership: nil, percentOwned: 0.0092),
                KeyManager(name: "Jeffrey Henley", title: "director, Vice Chairman", ownership: "745K", ownershipValue: "$138.1M", percentOwnership: nil, percentOwned: 0.0276)
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
                sourceGrain: nil,
                tamScope: "global"
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
            wallStreetInsight: "Buy-rated with a $190 target (~14% upside), institutions added $430M last quarter, and upgrades lead downgrades 8-to-3 — analysts, funds, and the rating trend all lean bullish.",
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
            momentumDowngrades: 3,
            momentumMaintains: 12,
            analystStrongBuy: 18,
            analystBuy: 14,
            analystHold: 6,
            analystSell: 2,
            analystStrongSell: 0
        ),
        criticalFactors: [
            CriticalFactor(
                title: "Free Cash Flow",
                description: "FCF is negative (−$394M) on heavy cloud capex.",
                severity: .high,
                watch: "Next earnings — is operating cash flow catching up to capex spend?"
            ),
            CriticalFactor(
                title: "Debt & Capital Allocation",
                description: "Debt is elevated (D/E 4.21) against negative free cash flow.",
                severity: .medium,
                watch: "Track whether net debt falls as the cloud buildout slows."
            ),
            CriticalFactor(
                title: "Valuation",
                description: "Shares trade at 41.6× earnings — a rich multiple.",
                severity: .medium,
                watch: "Confirm earnings growth keeps pace, or the multiple compresses."
            )
        ],
        disclaimerText: "This analysis is for educational purposes only and does not constitute financial advice. AI-generated content may be inaccurate. Always conduct your own research and consult with a qualified financial advisor before making investment decisions."
    )
}
