//
//  TickerReportResponse.swift
//  ios
//
//  Codable DTOs for decoding the backend GET /stocks/{ticker}/report response.
//  These are bridge structs — they decode JSON then transform into the
//  rich view-model types in TickerReportModels.swift.
//
//  NOTE: APIClient does NOT use .convertFromSnakeCase, so all CodingKeys
//  use explicit snake_case raw values.
//

import Foundation

// MARK: - Top-Level Response

struct TickerReportAPIResponse: Codable {
    let symbol: String
    let companyName: String
    let exchange: String
    let logoUrl: String?
    let liveDate: String
    let agent: String
    let qualityScore: Double
    let executiveSummaryText: String
    let executiveSummaryBullets: [ESBulletDTO]
    let keyVitals: KeyVitalsDTO
    let coreThesis: CoreThesisDTO
    let fundamentalMetrics: [FundamentalMetricCardDTO]
    let overallAssessment: OverallAssessmentDTO
    let revenueForecast: RevenueForecastDTO
    let insiderData: InsiderDataDTO
    let keyManagement: KeyManagementDTO
    let priceAction: PriceActionDTO
    let revenueEngine: RevenueEngineDTO
    let moatCompetition: MoatCompetitionDTO
    let macroData: MacroDataDTO
    let wallStreetConsensus: WallStreetConsensusDTO
    let criticalFactors: [CriticalFactorDTO]
    let disclaimerText: String

    enum CodingKeys: String, CodingKey {
        case symbol
        case companyName = "company_name"
        case exchange
        case logoUrl = "logo_url"
        case liveDate = "live_date"
        case agent
        case qualityScore = "quality_score"
        case executiveSummaryText = "executive_summary_text"
        case executiveSummaryBullets = "executive_summary_bullets"
        case keyVitals = "key_vitals"
        case coreThesis = "core_thesis"
        case fundamentalMetrics = "fundamental_metrics"
        case overallAssessment = "overall_assessment"
        case revenueForecast = "revenue_forecast"
        case insiderData = "insider_data"
        case keyManagement = "key_management"
        case priceAction = "price_action"
        case revenueEngine = "revenue_engine"
        case moatCompetition = "moat_competition"
        case macroData = "macro_data"
        case wallStreetConsensus = "wall_street_consensus"
        case criticalFactors = "critical_factors"
        case disclaimerText = "disclaimer_text"
    }
}

// MARK: - Executive Summary Bullet

struct ESBulletDTO: Codable {
    let category: String
    let text: String
    let sentiment: String

    enum CodingKeys: String, CodingKey {
        case category, text, sentiment
    }
}

// MARK: - Vital Score

struct VitalScoreDTO: Codable {
    let value: Double
    let status: String

    enum CodingKeys: String, CodingKey {
        case value, status
    }
}

// MARK: - Key Vitals

struct KeyVitalsDTO: Codable {
    let valuation: ValuationVitalDTO?
    let moat: MoatVitalDTO?
    let financialHealth: FinancialHealthVitalDTO?
    let revenue: RevenueVitalDTO?
    let insider: InsiderVitalDTO?
    let macro: MacroVitalDTO?
    let forecast: ForecastVitalDTO?
    let wallStreet: WallStreetVitalDTO?

    enum CodingKeys: String, CodingKey {
        case valuation, moat
        case financialHealth = "financial_health"
        case revenue, insider, macro, forecast
        case wallStreet = "wall_street"
    }
}

struct ValuationVitalDTO: Codable {
    let status: String
    let currentPrice: Double
    let fairValue: Double
    let upsidePotential: Double

    enum CodingKeys: String, CodingKey {
        case status
        case currentPrice = "current_price"
        case fairValue = "fair_value"
        case upsidePotential = "upside_potential"
    }
}

struct MoatTagDTO: Codable {
    let label: String
    let strength: String

    enum CodingKeys: String, CodingKey {
        case label, strength
    }
}

struct MoatVitalDTO: Codable {
    let overallRating: String
    let primarySource: String
    let tags: [MoatTagDTO]
    let valueLabel: String
    let stabilityLabel: String

    enum CodingKeys: String, CodingKey {
        case overallRating = "overall_rating"
        case primarySource = "primary_source"
        case tags
        case valueLabel = "value_label"
        case stabilityLabel = "stability_label"
    }
}

struct FinancialHealthVitalDTO: Codable {
    let level: String
    let altmanZScore: Double
    let altmanZLabel: String
    let additionalMetric: String
    let additionalMetricStatus: String
    let fcfNote: String

    enum CodingKeys: String, CodingKey {
        case level
        case altmanZScore = "altman_z_score"
        case altmanZLabel = "altman_z_label"
        case additionalMetric = "additional_metric"
        case additionalMetricStatus = "additional_metric_status"
        case fcfNote = "fcf_note"
    }
}

struct RevenueVitalDTO: Codable {
    let score: VitalScoreDTO
    let totalRevenue: String
    let revenueGrowth: Double
    let topSegment: String
    let topSegmentGrowth: Double

    enum CodingKeys: String, CodingKey {
        case score
        case totalRevenue = "total_revenue"
        case revenueGrowth = "revenue_growth"
        case topSegment = "top_segment"
        case topSegmentGrowth = "top_segment_growth"
    }
}

struct InsiderVitalDTO: Codable {
    let score: VitalScoreDTO
    let sentiment: String
    let netActivity: String
    let buyCount: Int
    let sellCount: Int
    let keyInsight: String

    enum CodingKeys: String, CodingKey {
        case score, sentiment
        case netActivity = "net_activity"
        case buyCount = "buy_count"
        case sellCount = "sell_count"
        case keyInsight = "key_insight"
    }
}

struct MacroVitalDTO: Codable {
    let score: VitalScoreDTO
    let threatLevel: String
    let topRisk: String
    let riskTrend: String
    let activeRiskCount: Int

    enum CodingKeys: String, CodingKey {
        case score
        case threatLevel = "threat_level"
        case topRisk = "top_risk"
        case riskTrend = "risk_trend"
        case activeRiskCount = "active_risk_count"
    }
}

struct ForecastVitalDTO: Codable {
    let score: VitalScoreDTO
    let revenueCAGR: Double
    let epsCAGR: Double
    let guidance: String
    let outlook: String

    enum CodingKeys: String, CodingKey {
        case score
        case revenueCAGR = "revenue_cagr"
        case epsCAGR = "eps_cagr"
        case guidance, outlook
    }
}

struct WallStreetVitalDTO: Codable {
    let score: VitalScoreDTO
    let consensusRating: String
    let priceTarget: Double
    let currentPrice: Double
    let upgrades: Int
    let downgrades: Int

    enum CodingKeys: String, CodingKey {
        case score
        case consensusRating = "consensus_rating"
        case priceTarget = "price_target"
        case currentPrice = "current_price"
        case upgrades, downgrades
    }
}

// MARK: - Core Thesis

struct CoreThesisDTO: Codable {
    let bullCase: [String]
    let bearCase: [String]

    enum CodingKeys: String, CodingKey {
        case bullCase = "bull_case"
        case bearCase = "bear_case"
    }
}

// MARK: - Fundamentals

struct DeepDiveMetricDTO: Codable {
    let label: String
    let value: String
    let trend: String?

    enum CodingKeys: String, CodingKey {
        case label, value, trend
    }
}

struct FundamentalMetricCardDTO: Codable {
    let title: String
    let starRating: Int
    let metrics: [DeepDiveMetricDTO]
    let qualityLabel: String

    enum CodingKeys: String, CodingKey {
        case title
        case starRating = "star_rating"
        case metrics
        case qualityLabel = "quality_label"
    }
}

struct OverallAssessmentDTO: Codable {
    let text: String
    let averageRating: Double
    let strongCount: Int
    let weakCount: Int

    enum CodingKeys: String, CodingKey {
        case text
        case averageRating = "average_rating"
        case strongCount = "strong_count"
        case weakCount = "weak_count"
    }
}

// MARK: - Revenue Forecast

struct RevenueProjectionDTO: Codable {
    let period: String
    let revenue: Double
    let revenueLabel: String
    let revenueYoyPct: Double?
    let eps: Double
    let epsLabel: String
    let epsYoyPct: Double?
    let isForecast: Bool

    enum CodingKeys: String, CodingKey {
        case period, revenue
        case revenueLabel = "revenue_label"
        case revenueYoyPct = "revenue_yoy_pct"
        case eps
        case epsLabel = "eps_label"
        case epsYoyPct = "eps_yoy_pct"
        case isForecast = "is_forecast"
    }
}

struct RevenueForecastDTO: Codable {
    let cagr: Double
    let epsGrowth: Double
    let managementGuidance: String
    let projections: [RevenueProjectionDTO]
    let guidanceQuote: String?
    let guidanceSpeaker: String?
    let guidancePeriod: String?

    enum CodingKeys: String, CodingKey {
        case cagr
        case epsGrowth = "eps_growth"
        case managementGuidance = "management_guidance"
        case projections
        case guidanceQuote = "guidance_quote"
        case guidanceSpeaker = "guidance_speaker"
        case guidancePeriod = "guidance_period"
    }
}

// MARK: - Insider & Management

struct InsiderTransactionDTO: Codable {
    let type: String
    let count: Int
    let shares: String
    let value: String

    enum CodingKeys: String, CodingKey {
        case type, count, shares, value
    }
}

struct InsiderDataDTO: Codable {
    let sentiment: String
    let timeframe: String
    let transactions: [InsiderTransactionDTO]
    let ownershipNote: String?

    enum CodingKeys: String, CodingKey {
        case sentiment, timeframe, transactions
        case ownershipNote = "ownership_note"
    }
}

struct KeyManagerDTO: Codable {
    let name: String
    let title: String
    let ownership: String
    let ownershipValue: String
    let percentOwnership: Double?

    enum CodingKeys: String, CodingKey {
        case name, title, ownership
        case ownershipValue = "ownership_value"
        case percentOwnership = "percent_ownership"
    }
}

struct KeyManagementDTO: Codable {
    let topHolders: [KeyManagerDTO]
    let officers: [KeyManagerDTO]
    let ownershipInsight: String

    enum CodingKeys: String, CodingKey {
        case topHolders = "top_holders"
        case officers
        case ownershipInsight = "ownership_insight"
    }

    // Decoder-only fallback for the pre-split backend shape.
    private enum LegacyCodingKeys: String, CodingKey {
        case managers
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let newTopHolders = try c.decodeIfPresent([KeyManagerDTO].self, forKey: .topHolders) ?? []
        let newOfficers = try c.decodeIfPresent([KeyManagerDTO].self, forKey: .officers) ?? []

        if newTopHolders.isEmpty && newOfficers.isEmpty {
            // Legacy payload (pre-split): a single `managers` list.
            // The 24h `ticker_report_cache` row + already-generated
            // `research_reports` rows still carry this shape until they
            // expire / are cleared. Split client-side so the table
            // renders meaningfully during the rollout window: 10%+
            // ownership rows go to topHolders, the rest to officers.
            let legacyContainer = try decoder.container(keyedBy: LegacyCodingKeys.self)
            let legacy = try legacyContainer.decodeIfPresent(
                [KeyManagerDTO].self, forKey: .managers
            ) ?? []
            self.topHolders = legacy.filter { ($0.percentOwnership ?? 0) >= 10 }
            self.officers = legacy.filter { ($0.percentOwnership ?? 0) < 10 }
        } else {
            self.topHolders = newTopHolders
            self.officers = newOfficers
        }

        self.ownershipInsight = (try? c.decode(String.self, forKey: .ownershipInsight)) ?? ""
    }
}

// MARK: - Price Action

struct PriceEventDTO: Codable {
    let tag: String
    let date: String
    let index: Int

    enum CodingKeys: String, CodingKey {
        case tag, date, index
    }
}

struct PriceActionDTO: Codable {
    let prices: [Double]
    let currentPrice: Double
    let event: PriceEventDTO?
    let narrative: String
    let changePct: Double
    let direction: String
    let windowLabel: String
    let tag: String
    // Volatility-aware additions — all optional so older cached reports decode.
    let tier: String?
    let zScore: Double?
    let sigmaDailyPct: Double?
    let expectedBandPct: Double?

    enum CodingKeys: String, CodingKey {
        case prices
        case currentPrice = "current_price"
        case event, narrative
        case changePct = "change_pct"
        case direction
        case windowLabel = "window_label"
        case tag
        case tier
        case zScore = "z_score"
        case sigmaDailyPct = "sigma_daily_pct"
        case expectedBandPct = "expected_band_pct"
    }
}

// MARK: - Revenue Engine

struct RevenueSegmentDTO: Codable {
    let name: String
    let currentRevenue: Double
    let previousRevenue: Double
    let totalRevenue: Double

    enum CodingKeys: String, CodingKey {
        case name
        case currentRevenue = "current_revenue"
        case previousRevenue = "previous_revenue"
        case totalRevenue = "total_revenue"
    }
}

struct RevenueEngineDTO: Codable {
    let segments: [RevenueSegmentDTO]
    let totalRevenue: Double
    let revenueUnit: String
    let period: String
    let analysisNote: String?

    enum CodingKeys: String, CodingKey {
        case segments
        case totalRevenue = "total_revenue"
        case revenueUnit = "revenue_unit"
        case period
        case analysisNote = "analysis_note"
    }
}

// MARK: - Moat & Competition

struct MarketDynamicsDTO: Codable {
    let industry: String
    let concentration: String
    // Optional now — nil signals "unknown" (sector batch hasn't run AND
    // we couldn't derive from in-hand peers); iOS renders "—" rather
    // than misleading "+0.0%".
    let cagr5yr: Double?
    let currentTam: Double
    let futureTam: Double
    let currentYear: String
    let futureYear: String
    let lifecyclePhase: String
    let tamSourceQuote: String?
    // Caption attribution shown under the TAM row when TAM is sourced
    // (AI transcript quote OR FRED industry proxy). Nil when TAM is 0.
    let tamSourceLabel: String?
    // Grain of the source data: "industry" | "sector" | "all_industry".
    // The UI renders a "⚠ Broader than industry" chip when this is not
    // "industry", letting users know the TAM/CAGR is a proxy from a
    // broader bucket than the company's own industry. Nil when TAM came
    // from an AI-extracted earnings-call quote (company-specific) or
    // when no source produced data.
    let sourceGrain: String?

    enum CodingKeys: String, CodingKey {
        case industry, concentration
        case cagr5yr = "cagr_5yr"
        case currentTam = "current_tam"
        case futureTam = "future_tam"
        case currentYear = "current_year"
        case futureYear = "future_year"
        case lifecyclePhase = "lifecycle_phase"
        case tamSourceQuote = "tam_source_quote"
        case tamSourceLabel = "tam_source_label"
        case sourceGrain = "source_grain"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.industry = try c.decode(String.self, forKey: .industry)
        self.concentration = try c.decode(String.self, forKey: .concentration)
        self.cagr5yr = try c.decodeIfPresent(Double.self, forKey: .cagr5yr)
        self.currentTam = (try? c.decode(Double.self, forKey: .currentTam)) ?? 0
        self.futureTam = (try? c.decode(Double.self, forKey: .futureTam)) ?? 0
        self.currentYear = try c.decode(String.self, forKey: .currentYear)
        self.futureYear = try c.decode(String.self, forKey: .futureYear)
        self.lifecyclePhase = try c.decode(String.self, forKey: .lifecyclePhase)
        self.tamSourceQuote = try c.decodeIfPresent(String.self, forKey: .tamSourceQuote)
        self.tamSourceLabel = try c.decodeIfPresent(String.self, forKey: .tamSourceLabel)
        self.sourceGrain = try c.decodeIfPresent(String.self, forKey: .sourceGrain)
    }
}

struct MoatDimensionDTO: Codable {
    let name: String
    let score: Double
    let peerScore: Double
    let source: String?

    enum CodingKeys: String, CodingKey {
        case name, score, source
        case peerScore = "peer_score"
    }
}

struct CompetitorDTO: Codable {
    let name: String
    let ticker: String
    let competitiveScore: Double
    let marketSharePercent: Double
    let threatLevel: String

    enum CodingKeys: String, CodingKey {
        case name, ticker
        case competitiveScore = "competitive_score"
        case marketSharePercent = "market_share_percent"
        case threatLevel = "threat_level"
    }
}

struct MoatCompetitionDTO: Codable {
    let marketDynamics: MarketDynamicsDTO
    let dimensions: [MoatDimensionDTO]
    let durabilityNote: String
    let competitors: [CompetitorDTO]
    let competitiveInsight: String

    enum CodingKeys: String, CodingKey {
        case marketDynamics = "market_dynamics"
        case dimensions
        case durabilityNote = "durability_note"
        case competitors
        case competitiveInsight = "competitive_insight"
    }
}

// MARK: - Macro & Geopolitical

struct MacroRiskFactorDTO: Codable {
    let category: String
    let title: String
    let impact: Double
    let description: String
    let trend: String
    let severity: String

    enum CodingKeys: String, CodingKey {
        case category, title, impact, description, trend, severity
    }
}

struct MacroDataDTO: Codable {
    let overallThreatLevel: String
    let headline: String
    let riskFactors: [MacroRiskFactorDTO]
    let intelligenceBrief: String
    let lastUpdated: String

    enum CodingKeys: String, CodingKey {
        case overallThreatLevel = "overall_threat_level"
        case headline
        case riskFactors = "risk_factors"
        case intelligenceBrief = "intelligence_brief"
        case lastUpdated = "last_updated"
    }
}

// MARK: - Wall Street Consensus

struct StockPricePointDTO: Codable {
    let month: String
    let price: Double

    enum CodingKeys: String, CodingKey {
        case month, price
    }
}

struct SmartMoneyFlowPointDTO: Codable {
    let month: String
    let buyVolume: Double
    let sellVolume: Double

    enum CodingKeys: String, CodingKey {
        case month
        case buyVolume = "buy_volume"
        case sellVolume = "sell_volume"
    }
}

struct WallStreetConsensusDTO: Codable {
    let rating: String
    let currentPrice: Double
    // Null when the backend has no real analyst coverage — decoded via
    // decodeIfPresent so missing/null becomes nil (honest "no targets" state).
    let targetPrice: Double?
    let lowTarget: Double?
    let highTarget: Double?
    let valuationStatus: String
    let discountPercent: Double
    // NAMING: these `hedgeFund*` fields are FMP 13F institutional-ownership data,
    // surfaced in the report's "Institutions" section (SmartMoneyTab.hedgeFunds =
    // "Institutions"), not a "Hedge Funds" label.
    let hedgeFundNote: String?
    let hedgeFundPriceData: [StockPricePointDTO]
    let hedgeFundFlowData: [SmartMoneyFlowPointDTO]
    /// Quarterly institutional 13F flow, identical to the Holders tab's
    /// `hedge_funds_data`. Optional: legacy persisted reports predate it,
    /// in which case the view falls back to the monthly chart above.
    let hedgeFundSmartMoney: SmartMoneyDataDTO?
    let momentumUpgrades: Int
    let momentumDowngrades: Int

    enum CodingKeys: String, CodingKey {
        case rating
        case currentPrice = "current_price"
        case targetPrice = "target_price"
        case lowTarget = "low_target"
        case highTarget = "high_target"
        case valuationStatus = "valuation_status"
        case discountPercent = "discount_percent"
        case hedgeFundNote = "hedge_fund_note"
        case hedgeFundPriceData = "hedge_fund_price_data"
        case hedgeFundFlowData = "hedge_fund_flow_data"
        case hedgeFundSmartMoney = "hedge_fund_smart_money"
        case momentumUpgrades = "momentum_upgrades"
        case momentumDowngrades = "momentum_downgrades"
    }
}

// MARK: - Critical Factors

struct CriticalFactorDTO: Codable {
    let title: String
    let description: String
    let severity: String

    enum CodingKeys: String, CodingKey {
        case title, description, severity
    }
}

// MARK: - Transformer: DTO → View Model

extension TickerReportAPIResponse {
    /// Convert the Codable API response into the rich view-model type.
    func toTickerReportData() -> TickerReportData {
        // Agent
        let agentPersona: ReportAgentPersona = {
            switch agent.lowercased() {
            case "buffett": return .buffett
            case "wood": return .wood
            case "lynch": return .lynch
            case "dalio": return .dalio
            default: return .buffett
            }
        }()

        // Quality Rating
        let quality = ReportQualityRating(score: qualityScore)

        // Executive Summary Bullets
        let esBullets = executiveSummaryBullets.map { b in
            let sent: ExecutiveSummaryBullet.BulletSentiment = {
                switch b.sentiment.lowercased() {
                case "positive": return .positive
                case "negative": return .negative
                default: return .neutral
                }
            }()
            return ExecutiveSummaryBullet(category: b.category, text: b.text, sentiment: sent)
        }

        // Key Vitals
        let valuation: ReportValuationData? = keyVitals.valuation.map { v in
            let status: ValuationStatus = {
                switch v.status.lowercased() {
                case "overpriced": return .overpriced
                case "underpriced": return .underpriced
                case "deep_undervalued": return .deepUndervalued
                default: return .fairValue
                }
            }()
            return ReportValuationData(
                status: status,
                currentPrice: v.currentPrice,
                fairValue: v.fairValue,
                upsidePotential: v.upsidePotential
            )
        }

        let moat: ReportMoatData? = keyVitals.moat.map { m in
            let rating: MoatTag.MoatStrength = {
                switch m.overallRating.lowercased() {
                case "wide": return .wide
                case "narrow": return .narrow
                default: return .none
                }
            }()
            let tags = m.tags.map { t in
                let s: MoatTag.MoatStrength = {
                    switch t.strength.lowercased() {
                    case "wide": return .wide
                    case "narrow": return .narrow
                    default: return .none
                    }
                }()
                return MoatTag(label: t.label, strength: s)
            }
            return ReportMoatData(
                overallRating: rating,
                primarySource: m.primarySource,
                tags: tags,
                valueLabel: m.valueLabel,
                stabilityLabel: m.stabilityLabel
            )
        }

        let health: ReportFinancialHealthData? = keyVitals.financialHealth.map { h in
            let level = FinancialHealthLevel.fromZScore(h.altmanZScore)
            let addStatus = FinancialHealthLevel.fromZScore(h.altmanZScore)
            return ReportFinancialHealthData(
                level: level,
                altmanZScore: h.altmanZScore,
                altmanZLabel: h.altmanZLabel,
                additionalMetric: h.additionalMetric,
                additionalMetricStatus: addStatus,
                fcfNote: h.fcfNote
            )
        }

        let revVital: ReportRevenueVitalData? = keyVitals.revenue.map { r in
            ReportRevenueVitalData(
                score: VitalScore(value: r.score.value, status: Self.mapVitalStatus(r.score.status)),
                totalRevenue: r.totalRevenue,
                revenueGrowth: r.revenueGrowth,
                topSegment: r.topSegment,
                topSegmentGrowth: r.topSegmentGrowth
            )
        }

        let insVital: ReportInsiderVitalData? = keyVitals.insider.map { i in
            let sent: InsiderSentiment = {
                switch i.sentiment.lowercased() {
                case "positive": return .positive
                case "negative": return .negative
                default: return .neutral
                }
            }()
            return ReportInsiderVitalData(
                score: VitalScore(value: i.score.value, status: Self.mapVitalStatus(i.score.status)),
                sentiment: sent,
                netActivity: i.netActivity,
                buyCount: i.buyCount,
                sellCount: i.sellCount,
                keyInsight: i.keyInsight
            )
        }

        let macVital: ReportMacroVitalData? = keyVitals.macro.map { m in
            ReportMacroVitalData(
                score: VitalScore(value: m.score.value, status: Self.mapVitalStatus(m.score.status)),
                threatLevel: Self.mapThreatLevel(m.threatLevel),
                topRisk: m.topRisk,
                riskTrend: Self.mapRiskTrend(m.riskTrend),
                activeRiskCount: m.activeRiskCount
            )
        }

        let forVital: ReportForecastVitalData? = keyVitals.forecast.map { f in
            ReportForecastVitalData(
                score: VitalScore(value: f.score.value, status: Self.mapVitalStatus(f.score.status)),
                revenueCAGR: f.revenueCAGR,
                epsCAGR: f.epsCAGR,
                guidance: Self.mapGuidance(f.guidance),
                outlook: f.outlook
            )
        }

        let wsVital: ReportWallStreetVitalData? = keyVitals.wallStreet.map { w in
            ReportWallStreetVitalData(
                score: VitalScore(value: w.score.value, status: Self.mapVitalStatus(w.score.status)),
                consensusRating: Self.mapConsensusRating(w.consensusRating),
                priceTarget: w.priceTarget,
                currentPrice: w.currentPrice,
                upgrades: w.upgrades,
                downgrades: w.downgrades
            )
        }

        let vitals = ReportKeyVitals(
            valuation: valuation, moat: moat, financialHealth: health,
            revenue: revVital, insider: insVital, macro: macVital,
            forecast: forVital, wallStreet: wsVital
        )

        // Core Thesis
        let thesis = ReportCoreThesis(
            bullCase: coreThesis.bullCase.map { CoreThesisBullet(text: $0) },
            bearCase: coreThesis.bearCase.map { CoreThesisBullet(text: $0) }
        )

        // Fundamental Metrics
        let fundMetrics = fundamentalMetrics.map { card in
            DeepDiveMetricCard(
                title: card.title,
                starRating: card.starRating,
                metrics: card.metrics.map { m in
                    let trend: DeepDiveMetric.MetricTrend? = {
                        guard let t = m.trend?.lowercased() else { return nil }
                        switch t {
                        case "up": return .up
                        case "down": return .down
                        case "flat": return .flat
                        default: return nil
                        }
                    }()
                    return DeepDiveMetric(label: m.label, value: m.value, trend: trend)
                },
                qualityLabel: card.qualityLabel
            )
        }

        // Overall Assessment
        let assessment = ReportOverallAssessment(
            text: overallAssessment.text,
            averageRating: overallAssessment.averageRating,
            strongCount: overallAssessment.strongCount,
            weakCount: overallAssessment.weakCount
        )

        // Revenue Forecast
        let forecast = ReportRevenueForecast(
            cagr: revenueForecast.cagr,
            epsGrowth: revenueForecast.epsGrowth,
            managementGuidance: Self.mapGuidance(revenueForecast.managementGuidance),
            projections: {
                // Client-side YoY fallback: when the backend payload pre-
                // dates the YoY-fields commit (e.g., a Railway deploy that
                // hasn't shipped yet), `revenueYoyPct` / `epsYoyPct` come
                // back nil. For every bar that has a neighbor in the
                // visible window we can compute YoY locally from the
                // adjacent projection — that covers bars 2 and 3. The
                // first bar still needs the backend's hidden anchor
                // (FY-1 estimate) so it stays nil here; the chip will
                // appear after the next regen against new-code backend.
                let dtoProjections = revenueForecast.projections
                return dtoProjections.enumerated().map { (idx, p) -> RevenueProjection in
                    var revYoy = p.revenueYoyPct
                    var epsYoy = p.epsYoyPct
                    if idx > 0 {
                        let prev = dtoProjections[idx - 1]
                        if revYoy == nil, prev.revenue > 0 {
                            revYoy = (p.revenue - prev.revenue) / prev.revenue * 100
                        }
                        if epsYoy == nil, prev.eps > 0 {
                            epsYoy = (p.eps - prev.eps) / prev.eps * 100
                        }
                    }
                    return RevenueProjection(
                        period: p.period, revenue: p.revenue,
                        revenueLabel: p.revenueLabel,
                        revenueYoyPct: revYoy,
                        eps: p.eps,
                        epsLabel: p.epsLabel,
                        epsYoyPct: epsYoy,
                        isForecast: p.isForecast
                    )
                }
            }(),
            guidanceQuote: revenueForecast.guidanceQuote,
            guidanceSpeaker: revenueForecast.guidanceSpeaker,
            guidancePeriod: revenueForecast.guidancePeriod
        )

        // Insider Data
        let insider = ReportInsiderData(
            sentiment: Self.mapInsiderSentiment(insiderData.sentiment),
            timeframe: insiderData.timeframe,
            transactions: insiderData.transactions.map { t in
                InsiderTransaction(type: t.type, count: t.count, shares: t.shares, value: t.value)
            },
            ownershipNote: insiderData.ownershipNote
        )

        // Key Management — split into top holders (10%+ owners) and
        // officers (CEO/CFO/COO/… by role rank).
        let mapManager: (KeyManagerDTO) -> KeyManager = { m in
            KeyManager(
                name: m.name,
                title: m.title,
                ownership: m.ownership,
                ownershipValue: m.ownershipValue,
                percentOwnership: m.percentOwnership
            )
        }
        let management = ReportKeyManagement(
            topHolders: keyManagement.topHolders.map(mapManager),
            officers: keyManagement.officers.map(mapManager),
            ownershipInsight: keyManagement.ownershipInsight
        )

        // Price Action
        let pa = PriceActionData(
            prices: priceAction.prices,
            currentPrice: priceAction.currentPrice,
            event: priceAction.event.map { e in
                PriceEvent(tag: e.tag, date: e.date, index: e.index)
            },
            narrative: priceAction.narrative,
            changePct: priceAction.changePct,
            direction: priceAction.direction,
            windowLabel: priceAction.windowLabel,
            tag: priceAction.tag,
            tier: priceAction.tier,
            zScore: priceAction.zScore,
            sigmaDailyPct: priceAction.sigmaDailyPct,
            expectedBandPct: priceAction.expectedBandPct
        )

        // Revenue Engine
        let revEng = ReportRevenueEngineData(
            segments: revenueEngine.segments.map { s in
                RevenueSegment(
                    name: s.name,
                    currentRevenue: s.currentRevenue,
                    previousRevenue: s.previousRevenue,
                    totalRevenue: s.totalRevenue
                )
            },
            totalRevenue: revenueEngine.totalRevenue,
            revenueUnit: revenueEngine.revenueUnit,
            period: revenueEngine.period,
            analysisNote: revenueEngine.analysisNote
        )

        // Moat & Competition
        let mc = ReportMoatCompetitionData(
            marketDynamics: MarketDynamics(
                industry: moatCompetition.marketDynamics.industry,
                concentration: Self.mapConcentration(moatCompetition.marketDynamics.concentration),
                cagr5Yr: moatCompetition.marketDynamics.cagr5yr,
                currentTAM: moatCompetition.marketDynamics.currentTam,
                futureTAM: moatCompetition.marketDynamics.futureTam,
                currentYear: moatCompetition.marketDynamics.currentYear,
                futureYear: moatCompetition.marketDynamics.futureYear,
                lifecyclePhase: Self.mapLifecycle(moatCompetition.marketDynamics.lifecyclePhase),
                tamSourceQuote: moatCompetition.marketDynamics.tamSourceQuote,
                tamSourceLabel: moatCompetition.marketDynamics.tamSourceLabel,
                sourceGrain: moatCompetition.marketDynamics.sourceGrain
            ),
            dimensions: moatCompetition.dimensions.map { d in
                MoatDimension(name: d.name, score: d.score, peerScore: d.peerScore)
            },
            durabilityNote: moatCompetition.durabilityNote,
            competitors: moatCompetition.competitors.map { c in
                CompetitorComparison(
                    name: c.name, ticker: c.ticker,
                    competitiveScore: c.competitiveScore,
                    marketSharePercent: c.marketSharePercent,
                    threatLevel: Self.mapCompetitorThreat(c.threatLevel)
                )
            },
            competitiveInsight: moatCompetition.competitiveInsight
        )

        // Macro Data
        let macro = ReportMacroData(
            overallThreatLevel: Self.mapThreatLevel(macroData.overallThreatLevel),
            headline: macroData.headline,
            riskFactors: macroData.riskFactors.map { rf in
                MacroRiskFactor(
                    category: Self.mapMacroCategory(rf.category),
                    title: rf.title, impact: rf.impact,
                    description: rf.description,
                    trend: Self.mapRiskTrend(rf.trend),
                    severity: Self.mapThreatLevel(rf.severity)
                )
            },
            intelligenceBrief: macroData.intelligenceBrief,
            lastUpdated: macroData.lastUpdated
        )

        // Wall Street Consensus
        let ws = ReportWallStreetConsensus(
            rating: Self.mapConsensusRating(wallStreetConsensus.rating),
            currentPrice: wallStreetConsensus.currentPrice,
            targetPrice: wallStreetConsensus.targetPrice,
            lowTarget: wallStreetConsensus.lowTarget,
            highTarget: wallStreetConsensus.highTarget,
            valuationStatus: Self.mapValuationStatus(wallStreetConsensus.valuationStatus),
            discountPercent: wallStreetConsensus.discountPercent,
            hedgeFundNote: wallStreetConsensus.hedgeFundNote,
            hedgeFundPriceData: wallStreetConsensus.hedgeFundPriceData.map { p in
                StockPriceDataPoint(month: p.month, price: p.price)
            },
            hedgeFundFlowData: wallStreetConsensus.hedgeFundFlowData.map { f in
                SmartMoneyFlowDataPoint(month: f.month, buyVolume: f.buyVolume, sellVolume: f.sellVolume)
            },
            hedgeFundSmartMoney: wallStreetConsensus.hedgeFundSmartMoney?.toDisplayModel(),
            momentumUpgrades: wallStreetConsensus.momentumUpgrades,
            momentumDowngrades: wallStreetConsensus.momentumDowngrades
        )

        // Critical Factors
        let factors = criticalFactors.map { f in
            let sev: CriticalFactor.CriticalSeverity = {
                switch f.severity.lowercased() {
                case "high": return .high
                case "medium": return .medium
                default: return .low
                }
            }()
            return CriticalFactor(title: f.title, description: f.description, severity: sev)
        }

        return TickerReportData(
            symbol: symbol,
            companyName: companyName,
            exchange: exchange,
            logoName: logoUrl,
            liveDate: liveDate,
            agent: agentPersona,
            qualityRating: quality,
            executiveSummaryText: executiveSummaryText,
            executiveSummaryBullets: esBullets,
            keyVitals: vitals,
            coreThesis: thesis,
            fundamentalMetrics: fundMetrics,
            overallAssessment: assessment,
            revenueForecast: forecast,
            insiderData: insider,
            keyManagement: management,
            priceAction: pa,
            revenueEngine: revEng,
            moatCompetition: mc,
            macroData: macro,
            wallStreetConsensus: ws,
            criticalFactors: factors,
            disclaimerText: disclaimerText
        )
    }

    // MARK: - Mapping Helpers

    private static func mapVitalStatus(_ s: String) -> VitalStatus {
        switch s.lowercased() {
        case "critical": return .critical
        case "warning": return .warning
        case "good": return .good
        default: return .neutral
        }
    }

    private static func mapThreatLevel(_ s: String) -> ThreatLevel {
        switch s.lowercased() {
        case "low": return .low
        case "elevated": return .elevated
        case "high": return .high
        case "severe": return .severe
        case "critical": return .critical
        default: return .low
        }
    }

    private static func mapRiskTrend(_ s: String) -> RiskTrend {
        switch s.lowercased() {
        case "improving": return .improving
        case "worsening": return .worsening
        default: return .stable
        }
    }

    private static func mapGuidance(_ s: String) -> ManagementGuidance {
        switch s.lowercased() {
        case "raised": return .raised
        case "lowered": return .lowered
        default: return .maintained
        }
    }

    private static func mapInsiderSentiment(_ s: String) -> InsiderSentiment {
        switch s.lowercased() {
        case "positive": return .positive
        case "negative": return .negative
        default: return .neutral
        }
    }

    private static func mapConsensusRating(_ s: String) -> ConsensusRating {
        switch s.lowercased() {
        case "strong_buy": return .strongBuy
        case "buy": return .buy
        case "sell": return .sell
        case "strong_sell": return .strongSell
        default: return .hold
        }
    }

    private static func mapValuationStatus(_ s: String) -> ValuationStatus {
        switch s.lowercased() {
        case "overpriced": return .overpriced
        case "underpriced": return .underpriced
        case "deep_undervalued": return .deepUndervalued
        default: return .fairValue
        }
    }

    private static func mapConcentration(_ s: String) -> MarketConcentration {
        switch s.lowercased() {
        case "monopoly": return .monopoly
        case "duopoly": return .duopoly
        case "oligopoly": return .oligopoly
        default: return .fragmented
        }
    }

    private static func mapLifecycle(_ s: String) -> LifecyclePhase {
        switch s.lowercased() {
        case "emerging": return .emerging
        case "secular_growth": return .secularGrowth
        case "declining": return .declining
        default: return .mature
        }
    }

    private static func mapCompetitorThreat(_ s: String) -> CompetitorThreatLevel {
        switch s.lowercased() {
        case "high": return .high
        case "moderate": return .moderate
        default: return .low
        }
    }

    private static func mapMacroCategory(_ s: String) -> MacroRiskCategory {
        switch s.lowercased() {
        case "inflation": return .inflation
        case "interest_rates": return .interestRates
        case "geopolitical": return .geopolitical
        case "currency": return .currency
        case "regulation": return .regulation
        case "supply_chain": return .supplyChain
        case "tariffs": return .tariffs
        case "energy": return .energy
        default: return .regulation
        }
    }
}
