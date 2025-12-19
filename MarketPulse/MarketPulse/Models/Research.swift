import Foundation

// MARK: - Research Models

struct ResearchReport: Codable, Identifiable {
    let id: String
    let userId: String
    let stockId: String
    let stock: Stock?
    let investorPersona: InvestorPersona
    let analysisPeriod: String
    let status: ReportStatus
    let title: String?
    let executiveSummary: String?
    let investmentThesis: InvestmentThesis?
    let pros: [String]?
    let cons: [String]?
    let moatAnalysis: MoatAnalysis?
    let valuationAnalysis: ValuationAnalysis?
    let riskAssessment: RiskAssessment?
    let actionRecommendation: String?
    let userRating: Int?
    let userFeedback: String?
    let errorMessage: String?
    let createdAt: Date
    let updatedAt: Date?
    let completedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, stock, status, title, pros, cons
        case userId = "user_id"
        case stockId = "stock_id"
        case investorPersona = "investor_persona"
        case analysisPeriod = "analysis_period"
        case executiveSummary = "executive_summary"
        case investmentThesis = "investment_thesis"
        case moatAnalysis = "moat_analysis"
        case valuationAnalysis = "valuation_analysis"
        case riskAssessment = "risk_assessment"
        case actionRecommendation = "action_recommendation"
        case userRating = "user_rating"
        case userFeedback = "user_feedback"
        case errorMessage = "error_message"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case completedAt = "completed_at"
    }

    var personaEmoji: String {
        investorPersona.emoji
    }

    var personaDisplayName: String {
        investorPersona.displayName
    }

    var isCompleted: Bool {
        status == .completed
    }

    var isFailed: Bool {
        status == .failed
    }

    var isProcessing: Bool {
        status == .processing || status == .pending
    }
}

struct InvestmentThesis: Codable {
    let summary: String
    let keyDrivers: [String]
    let risks: [String]
    let timeHorizon: String
    let convictionLevel: String

    enum CodingKeys: String, CodingKey {
        case summary, risks
        case keyDrivers = "key_drivers"
        case timeHorizon = "time_horizon"
        case convictionLevel = "conviction_level"
    }
}

struct MoatAnalysis: Codable {
    let moatRating: String
    let moatSources: [String]
    let moatSustainability: String?
    let competitivePosition: String
    let barriersToEntry: [String]

    enum CodingKeys: String, CodingKey {
        case competitivePosition = "competitive_position"
        case moatRating = "moat_rating"
        case moatSources = "moat_sources"
        case moatSustainability = "moat_sustainability"
        case barriersToEntry = "barriers_to_entry"
    }
}

struct ValuationAnalysis: Codable {
    let valuationRating: String
    let keyMetrics: [String: AnyCodable]
    let peerComparison: [String: AnyCodable]?
    let historicalContext: String?
    let marginOfSafety: String?

    enum CodingKeys: String, CodingKey {
        case valuationRating = "valuation_rating"
        case keyMetrics = "key_metrics"
        case peerComparison = "peer_comparison"
        case historicalContext = "historical_context"
        case marginOfSafety = "margin_of_safety"
    }
}

struct RiskAssessment: Codable {
    let overallRisk: String
    let businessRisks: [String]
    let financialRisks: [String]
    let marketRisks: [String]
    let managementRisks: [String]?
    let regulatoryRisks: [String]?

    enum CodingKeys: String, CodingKey {
        case overallRisk = "overall_risk"
        case businessRisks = "business_risks"
        case financialRisks = "financial_risks"
        case marketRisks = "market_risks"
        case managementRisks = "management_risks"
        case regulatoryRisks = "regulatory_risks"
    }
}

struct ResearchReportCreate: Codable {
    let stockId: String
    let investorPersona: InvestorPersona
    let analysisPeriod: String
    let customInstructions: String?

    enum CodingKeys: String, CodingKey {
        case stockId = "stock_id"
        case investorPersona = "investor_persona"
        case analysisPeriod = "analysis_period"
        case customInstructions = "custom_instructions"
    }

    init(stockId: String, investorPersona: InvestorPersona, analysisPeriod: String = "annual", customInstructions: String? = nil) {
        self.stockId = stockId
        self.investorPersona = investorPersona
        self.analysisPeriod = analysisPeriod
        self.customInstructions = customInstructions
    }
}

struct ResearchReportRate: Codable {
    let userRating: Int
    let userFeedback: String?

    enum CodingKeys: String, CodingKey {
        case userRating = "user_rating"
        case userFeedback = "user_feedback"
    }
}
