//
//  TaskPollingManager.swift
//  ios
//
//  Long-Running Task Management with Polling
//
//  Handles AI research generation and other async tasks:
//  1. Start task → Get task ID
//  2. Poll for status every N seconds
//  3. Yield progress updates via AsyncStream
//  4. Complete when done or timeout
//
//  Usage:
//  ```swift
//  for await progress in pollingManager.monitorResearch(reportId: "123") {
//      switch progress {
//      case .progress(let percent, let step):
//          updateUI(percent: percent, step: step)
//      case .completed(let report):
//          showReport(report)
//      case .failed(let error):
//          handleError(error)
//      }
//  }
//  ```
//

import Foundation

// MARK: - Task Progress

/// Progress updates for long-running tasks
enum TaskProgress<T: Sendable>: Sendable {
    case started(taskId: String)
    case progress(percent: Int, step: String)
    case completed(T)
    case failed(AppError)
}

// MARK: - Research Status Response

struct ResearchStatusResponse: Decodable, Sendable {
    let reportId: String
    let status: String
    let progress: Int
    let currentStep: String?
    let errorMessage: String?
    let estimatedTimeRemaining: Int?

    enum CodingKeys: String, CodingKey {
        case reportId = "report_id"
        case status, progress
        case currentStep = "current_step"
        case errorMessage = "error_message"
        case estimatedTimeRemaining = "estimated_time_remaining"
    }

    var isCompleted: Bool { status == "completed" }
    var isFailed: Bool { status == "failed" }
    var isProcessing: Bool { status == "pending" || status == "processing" }
}

// MARK: - Research Generation Response

struct ResearchGenerationResponse: Decodable, Sendable {
    let reportId: String
    let status: String
    let estimatedSeconds: Int?
    let pollUrl: String?

    enum CodingKeys: String, CodingKey {
        case reportId = "report_id"
        case status
        case estimatedSeconds = "estimated_seconds"
        case pollUrl = "poll_url"
    }
}

// MARK: - Task Polling Manager

/// Manages polling for long-running tasks like AI research generation
actor TaskPollingManager {

    private let apiClient: APIClient
    private let pollInterval: TimeInterval
    private let maxPollDuration: TimeInterval

    /// Active polling tasks (taskId -> Task)
    private var activeTasks: [String: Task<Void, Never>] = [:]

    // MARK: - Initialization

    init(
        apiClient: APIClient,
        pollInterval: TimeInterval = APIConfig.researchPollInterval,
        maxPollDuration: TimeInterval = APIConfig.researchPollTimeout
    ) {
        self.apiClient = apiClient
        self.pollInterval = pollInterval
        self.maxPollDuration = maxPollDuration
    }

    // MARK: - Research Generation

    /// Start research generation and monitor progress
    func generateAndMonitorResearch(
        stockId: String,
        persona: String
    ) -> AsyncThrowingStream<TaskProgress<ResearchReportDetail>, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    // 1. Start generation
                    let response = try await apiClient.request(
                        endpoint: .generateResearch(stockId: stockId, persona: persona),
                        responseType: ResearchGenerationResponse.self
                    )

                    let reportId = response.reportId
                    continuation.yield(.started(taskId: reportId))

                    // 2. Poll for completion
                    let startTime = Date()

                    while true {
                        // Check timeout
                        if Date().timeIntervalSince(startTime) > maxPollDuration {
                            continuation.yield(.failed(.timeout))
                            continuation.finish()
                            return
                        }

                        // Wait before polling
                        try await Task.sleep(nanoseconds: UInt64(pollInterval * 1_000_000_000))

                        // Check status
                        let status = try await apiClient.request(
                            endpoint: .getResearchStatus(reportId: reportId),
                            responseType: ResearchStatusResponse.self
                        )

                        if status.isProcessing {
                            continuation.yield(.progress(
                                percent: status.progress,
                                step: status.currentStep ?? "Processing..."
                            ))
                        } else if status.isCompleted {
                            // Fetch full report
                            let report = try await apiClient.request(
                                endpoint: .getResearchReport(reportId: reportId),
                                responseType: ResearchReportDetail.self
                            )
                            continuation.yield(.completed(report))
                            continuation.finish()
                            return
                        } else if status.isFailed {
                            let errorMessage = status.errorMessage ?? "Research generation failed"
                            continuation.yield(.failed(.apiError(code: "RESEARCH_FAILED", message: errorMessage)))
                            continuation.finish()
                            return
                        }
                    }
                } catch {
                    continuation.yield(.failed(AppError.from(error)))
                    continuation.finish()
                }
            }
        }
    }

    /// Monitor an existing research report by ID
    func monitorResearch(reportId: String) -> AsyncThrowingStream<TaskProgress<ResearchReportDetail>, Error> {
        AsyncThrowingStream { continuation in
            Task {
                let startTime = Date()

                while true {
                    // Check timeout
                    if Date().timeIntervalSince(startTime) > maxPollDuration {
                        continuation.yield(.failed(.timeout))
                        continuation.finish()
                        return
                    }

                    do {
                        let status = try await apiClient.request(
                            endpoint: .getResearchStatus(reportId: reportId),
                            responseType: ResearchStatusResponse.self
                        )

                        if status.isProcessing {
                            continuation.yield(.progress(
                                percent: status.progress,
                                step: status.currentStep ?? "Processing..."
                            ))
                        } else if status.isCompleted {
                            let report = try await apiClient.request(
                                endpoint: .getResearchReport(reportId: reportId),
                                responseType: ResearchReportDetail.self
                            )
                            continuation.yield(.completed(report))
                            continuation.finish()
                            return
                        } else if status.isFailed {
                            let errorMessage = status.errorMessage ?? "Research generation failed"
                            continuation.yield(.failed(.apiError(code: "RESEARCH_FAILED", message: errorMessage)))
                            continuation.finish()
                            return
                        }

                        // Wait before next poll
                        try await Task.sleep(nanoseconds: UInt64(pollInterval * 1_000_000_000))

                    } catch {
                        continuation.yield(.failed(AppError.from(error)))
                        continuation.finish()
                        return
                    }
                }
            }
        }
    }

    // MARK: - Task Management

    /// Cancel a polling task
    func cancelTask(_ taskId: String) {
        activeTasks[taskId]?.cancel()
        activeTasks.removeValue(forKey: taskId)
    }

    /// Cancel all active polling tasks
    func cancelAllTasks() {
        for (_, task) in activeTasks {
            task.cancel()
        }
        activeTasks.removeAll()
    }

    /// Check if a task is being polled
    func isPolling(_ taskId: String) -> Bool {
        activeTasks[taskId] != nil
    }
}

// MARK: - Research Report Detail

/// Full research report from backend
struct ResearchReportDetail: Decodable, Identifiable, Sendable {
    let id: String
    let userId: String
    let stockId: String
    let ticker: String
    let companyName: String
    let investorPersona: String
    let status: String

    // Report content
    let title: String?
    let executiveSummary: String?
    let investmentThesis: InvestmentThesis?
    let pros: [String]?
    let cons: [String]?
    let moatAnalysis: MoatAnalysis?
    let valuationAnalysis: ValuationAnalysis?
    let riskAssessment: RiskAssessment?
    let fullReport: String?
    let keyTakeaways: [String]?
    let actionRecommendation: String?

    // Metadata
    let generationTimeSeconds: Int?
    let tokensUsed: Int?
    let createdAt: String
    let completedAt: String?

    // User interaction
    let userRating: Int?
    let userFeedback: String?

    enum CodingKeys: String, CodingKey {
        case id
        case userId = "user_id"
        case stockId = "stock_id"
        case ticker
        case companyName = "company_name"
        case investorPersona = "investor_persona"
        case status, title
        case executiveSummary = "executive_summary"
        case investmentThesis = "investment_thesis"
        case pros, cons
        case moatAnalysis = "moat_analysis"
        case valuationAnalysis = "valuation_analysis"
        case riskAssessment = "risk_assessment"
        case fullReport = "full_report"
        case keyTakeaways = "key_takeaways"
        case actionRecommendation = "action_recommendation"
        case generationTimeSeconds = "generation_time_seconds"
        case tokensUsed = "tokens_used"
        case createdAt = "created_at"
        case completedAt = "completed_at"
        case userRating = "user_rating"
        case userFeedback = "user_feedback"
    }
}

// MARK: - Report Sub-Models

struct InvestmentThesis: Decodable, Sendable {
    let summary: String
    let keyDrivers: [String]
    let risks: [String]
    let timeHorizon: String
    let convictionLevel: String

    enum CodingKeys: String, CodingKey {
        case summary
        case keyDrivers = "key_drivers"
        case risks
        case timeHorizon = "time_horizon"
        case convictionLevel = "conviction_level"
    }
}

struct MoatAnalysis: Decodable, Sendable {
    let moatRating: String
    let moatSources: [String]
    let moatSustainability: String
    let competitivePosition: String
    let barriersToEntry: [String]

    enum CodingKeys: String, CodingKey {
        case moatRating = "moat_rating"
        case moatSources = "moat_sources"
        case moatSustainability = "moat_sustainability"
        case competitivePosition = "competitive_position"
        case barriersToEntry = "barriers_to_entry"
    }
}

struct ValuationAnalysis: Decodable, Sendable {
    let valuationRating: String
    let keyMetrics: [String: AnyCodable]
    let historicalContext: String?
    let marginOfSafety: String?

    enum CodingKeys: String, CodingKey {
        case valuationRating = "valuation_rating"
        case keyMetrics = "key_metrics"
        case historicalContext = "historical_context"
        case marginOfSafety = "margin_of_safety"
    }
}

struct RiskAssessment: Decodable, Sendable {
    let overallRisk: String
    let businessRisks: [String]
    let financialRisks: [String]
    let marketRisks: [String]

    enum CodingKeys: String, CodingKey {
        case overallRisk = "overall_risk"
        case businessRisks = "business_risks"
        case financialRisks = "financial_risks"
        case marketRisks = "market_risks"
    }
}
