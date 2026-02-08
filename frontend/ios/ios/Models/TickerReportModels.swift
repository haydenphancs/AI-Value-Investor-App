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
    case buffett = "BUFFETT AGENT"
    case wood = "WOOD AGENT"
    case lynch = "LYNCH AGENT"
    case dalio = "DALIO AGENT"

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
    let score: Double       // e.g. 4.1
    let maxScore: Double    // e.g. 5.0
    let label: String       // e.g. "Good quality business"

    var formattedScore: String {
        String(format: "%.1f", score)
    }

    var formattedMax: String {
        "/ \(Int(maxScore))"
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
        case .strong: return AppColors.bullish
        case .moderate: return AppColors.neutral
        case .weak: return AppColors.alertOrange
        case .critical: return AppColors.bearish
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
    let cagr: Double                    // percentage
    let managementGuidance: ManagementGuidance
    let projections: [RevenueProjection]
    let guidanceQuote: String?

    var formattedCAGR: String {
        "+\(String(format: "%.0f", cagr))% CAGR"
    }
}

struct RevenueProjection: Identifiable {
    let id = UUID()
    let label: String       // e.g. "$120", "$200B"
    let value: Double
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
    case strongBuy = "Buy Rating"
    case buy = "Buy"
    case hold = "Hold"
    case sell = "Sell"
    case strongSell = "Strong Sell"

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
    let momentumUpgrades: Int
    let momentumDowngrades: Int

    var formattedCurrentPrice: String {
        String(format: "$%.0f", currentPrice)
    }

    var formattedTargetPrice: String {
        String(format: "$%.0f", targetPrice)
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

// MARK: - Deep Dive Module

struct DeepDiveModule: Identifiable {
    let id = UUID()
    let title: String
    let iconName: String
    let type: DeepDiveModuleType
}

enum DeepDiveModuleType {
    case recentPriceMovement
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
            score: 4.1,
            maxScore: 5.0,
            label: "Good quality business"
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
                status: .overpriced,
                currentPrice: 142.82,
                fairValue: 206,
                upsidePotential: -0.8
            ),
            moat: ReportMoatData(
                tags: [
                    MoatTag(label: "High Switching Costs", strength: .wide),
                    MoatTag(label: "Network Effects", strength: .narrow)
                ],
                valueLabel: "Value",
                stabilityLabel: "Stable"
            ),
            financialHealth: ReportFinancialHealthData(
                level: .weak,
                altmanZScore: 1.8,
                altmanZLabel: "Below 1.8 is risk",
                additionalMetric: "Increasing Cost",
                additionalMetricStatus: .weak,
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
            managementGuidance: .raised,
            projections: [
                RevenueProjection(label: "$120", value: 120, isForecast: false),
                RevenueProjection(label: "$00B", value: 160, isForecast: true),
                RevenueProjection(label: "$00B", value: 200, isForecast: true)
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
        wallStreetConsensus: ReportWallStreetConsensus(
            rating: .strongBuy,
            currentPrice: 142,
            targetPrice: 190,
            lowTarget: 140,
            highTarget: 250,
            valuationStatus: .deepUndervalued,
            discountPercent: 33.4,
            hedgeFundNote: "Net inflow of $430M from institutional investors last quarter.",
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
