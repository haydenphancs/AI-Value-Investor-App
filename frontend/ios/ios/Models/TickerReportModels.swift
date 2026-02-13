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

// MARK: - Key Vitals Section

struct ReportKeyVitals {
    let valuation: ReportValuationData
    let moat: ReportMoatData
    let financialHealth: ReportFinancialHealthData
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
}

// MARK: - Deep Dive Metric

struct DeepDiveMetric: Identifiable {
    let id = UUID()
    let label: String
    let value: String
    let trend: MetricTrend?

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

    var formattedCAGR: String {
        "+\(String(format: "%.0f", cagr))% CAGR"
    }

    var formattedEPSGrowth: String {
        "+\(String(format: "%.0f", epsGrowth))% CAGR"
    }
}

struct RevenueProjection: Identifiable {
    let id = UUID()
    let period: String      // x-axis category e.g. "FY24", "FY25E"
    let revenue: Double     // revenue value (billions)
    let revenueLabel: String // display label e.g. "$120B"
    let eps: Double         // EPS value e.g. 4.50
    let epsLabel: String    // display label e.g. "$4.50"
    let isForecast: Bool
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
}

struct ReportKeyManagement {
    let managers: [KeyManager]
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
    let targetPrice: Double
    let lowTarget: Double
    let highTarget: Double
    let valuationStatus: ValuationStatus
    let discountPercent: Double         // "Trading 33.4% below fair value estimate"
    let hedgeFundNote: String?          // "Net inflow of $430M from institutional..."
    let hedgeFundPriceData: [StockPriceDataPoint]   // Price data for hedge fund chart
    let hedgeFundFlowData: [SmartMoneyFlowDataPoint] // Buy/sell volume data
    let momentumUpgrades: Int
    let momentumDowngrades: Int

    var formattedCurrentPrice: String {
        String(format: "$%.0f", currentPrice)
    }

    var formattedTargetPrice: String {
        String(format: "$%.0f", targetPrice)
    }

    var formattedHighTarget: String {
        String(format: "$%.0f", highTarget)
    }

    var formattedLowTarget: String {
        String(format: "$%.0f", lowTarget)
    }

    var formattedHighTargetPercent: String {
        let percent = ((highTarget - currentPrice) / currentPrice) * 100
        return String(format: "%+.1f%%", percent)
    }

    var formattedAvgTargetPercent: String {
        let percent = ((targetPrice - currentPrice) / currentPrice) * 100
        return String(format: "%+.1f%%", percent)
    }

    var formattedLowTargetPercent: String {
        let percent = ((lowTarget - currentPrice) / currentPrice) * 100
        return String(format: "%+.1f%%", percent)
    }

    var formattedDiscount: String {
        "Trading \(String(format: "%.1f", discountPercent))% below fair value estimate"
    }

    var formattedHighTarget: String {
        String(format: "$%.0f", highTarget)
    }

    var formattedLowTarget: String {
        String(format: "$%.0f", lowTarget)
    }

    var formattedHighTargetPercent: String {
        let percent = ((highTarget - currentPrice) / currentPrice) * 100
        return String(format: "%+.2f%%", percent)
    }

    var formattedAvgTargetPercent: String {
        let percent = ((targetPrice - currentPrice) / currentPrice) * 100
        return String(format: "%+.2f%%", percent)
    }

    var formattedLowTargetPercent: String {
        let percent = ((lowTarget - currentPrice) / currentPrice) * 100
        return String(format: "%+.2f%%", percent)
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
    let cagr5Yr: Double                     // 18.5 (percentage)
    let currentTAM: Double                  // 900 (in billions)
    let futureTAM: Double                   // 1600 (in billions)
    let currentYear: String                 // "2025"
    let futureYear: String                  // "2030"
    let lifecyclePhase: LifecyclePhase      // .secularGrowth

    var formattedCAGR: String {
        let sign = cagr5Yr >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", cagr5Yr))%"
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

    var formattedTAMRange: String {
        "\(formattedCurrentTAM) → \(formattedFutureTAM)"
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

    var meaning: String {
        switch self {
        case .wide: return ""
        case .narrow: return "Strong defense, but beatable."
        case .none: return "No structural advantage."
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
    let moatScore: Double       // 0-10
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
                    DeepDiveMetric(label: "Gross Margin", value: "70%", trend: nil),
                    DeepDiveMetric(label: "Net Margin", value: "25%", trend: nil)
                ],
                qualityLabel: "A Cash Machine"
            ),
            DeepDiveMetricCard(
                title: "Valuation",
                starRating: 3,
                metrics: [
                    DeepDiveMetric(label: "P/E Ratio", value: "25x", trend: nil),
                    DeepDiveMetric(label: "P/B Ratio", value: "1.2", trend: nil)
                ],
                qualityLabel: "Priced for perfection"
            ),
            DeepDiveMetricCard(
                title: "Growth",
                starRating: 4,
                metrics: [
                    DeepDiveMetric(label: "Revenue ROII", value: "+18%", trend: .up),
                    DeepDiveMetric(label: "EPS Growth", value: "+22%", trend: .up)
                ],
                qualityLabel: "Accelerating"
            ),
            DeepDiveMetricCard(
                title: "Health",
                starRating: 2,
                metrics: [
                    DeepDiveMetric(label: "Current Ratio", value: "0.8", trend: .down),
                    DeepDiveMetric(label: "Debt/Equity", value: "4.5x", trend: .up)
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
                RevenueProjection(period: "2026", revenue: 120, revenueLabel: "$120B", eps: 4.50, epsLabel: "$4.50", isForecast: false),
                RevenueProjection(period: "2027", revenue: 132, revenueLabel: "$132B", eps: 5.10, epsLabel: "$5.10", isForecast: true),
                RevenueProjection(period: "2028", revenue: 145, revenueLabel: "$145B", eps: 6.20, epsLabel: "$6.20", isForecast: true)
            ],
            guidanceQuote: "CFO expects accelerating cloud demand in Q3"
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
            managers: [
                KeyManager(name: "Lawrence Ellison", title: "Co-Founder", ownership: "40.3%", ownershipValue: "$43.5b"),
                KeyManager(name: "Jeffrey Henley", title: "Co-Founder", ownership: "0.022%", ownershipValue: "$102.6m"),
                KeyManager(name: "Dania Caral", title: "Executive Vice Chair...", ownership: "0.0059%", ownershipValue: "$9.7m"),
                KeyManager(name: "Marla Smith", title: "Executive VP & Chief Accounting Officer", ownership: "0.002%", ownershipValue: "$7.1m"),
                KeyManager(name: "Dietrich Niebuhr", title: "Chief Executive Officer & Director", ownership: "0.0005%", ownershipValue: "$18.7m")
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
            narrative: "Oracle dropped 12% after reporting Q3 earnings below consensus estimates. Revenue of $13.8B missed the $14.1B forecast, driven by slower-than-expected cloud migration deals. The sell-off intensified on guidance cut for Q4."
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
                lifecyclePhase: .secularGrowth
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
                CompetitorComparison(name: "Amazon Web Services", ticker: "AMZN", moatScore: 9.0, marketSharePercent: 31.0, threatLevel: .high),
                CompetitorComparison(name: "Microsoft Azure", ticker: "MSFT", moatScore: 8.5, marketSharePercent: 25.0, threatLevel: .high),
                CompetitorComparison(name: "Google Cloud", ticker: "GOOGL", moatScore: 7.2, marketSharePercent: 11.0, threatLevel: .moderate),
                CompetitorComparison(name: "SAP", ticker: "SAP", moatScore: 7.0, marketSharePercent: 5.0, threatLevel: .low)
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
