//
//  TickerReportViewModel.swift
//  ios
//
//  ViewModel for the Ticker Report screen - MVVM Architecture
//  Fetches real data from GET /stocks/{ticker}/report backend endpoint.
//

import Foundation
import Combine

@MainActor
class TickerReportViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var reportData: TickerReportData?
    @Published var isLoading: Bool = false
    @Published var error: String?

    // AI chat input (bound directly to CaydexAIChatBar in the View)
    @Published var aiInputText: String = ""

    // Chat response state
    @Published var chatResponse: String?
    @Published var isChatLoading: Bool = false
    @Published var chatUserQuestion: String?
    @Published var showChatResponse: Bool = false

    // MARK: - Private Properties
    private let ticker: String
    private let persona: String
    /// Backend research_reports row ID. When present, the fetch path
    /// prefers the cached `ticker_report_data` JSONB (instant) over a
    /// fresh /stocks/{ticker}/report call (~30-60s + FMP cost).
    private let reportId: String?
    private var loadAttempts: Int = 0

    // Deep Dive Modules - stored once to avoid regenerating UUIDs on every access
    let deepDiveModules: [DeepDiveModule] = [
        DeepDiveModule(title: "Recent Price Movement", iconName: "chart.xyaxis.line", type: .recentPriceMovement),
        DeepDiveModule(title: "The Revenue Engine", iconName: "dollarsign.circle", type: .revenueEngine),
        DeepDiveModule(title: "Fundamentals & Growth", iconName: "chart.bar.fill", type: .fundamentalsGrowth),
        DeepDiveModule(title: "Future Forecast", iconName: "binoculars.fill", type: .futureForecast),
        DeepDiveModule(title: "Insider & Management", iconName: "person.2.fill", type: .insiderManagement),
        DeepDiveModule(title: "Industry & Competitive Moat", iconName: "shield.fill", type: .moatCompetition),
        DeepDiveModule(title: "Macro-Economic & Geopolitical", iconName: "globe", type: .macroGeopolitical),
        DeepDiveModule(title: "Wall Street Consensus", iconName: "building.columns.fill", type: .wallStreetConsensus)
    ]

    // MARK: - Initialization
    init(ticker: String, persona: String = "warren_buffett") {
        self.ticker = ticker
        self.persona = persona
        self.reportId = nil
        loadReport()
    }

    /// Init from a Reports-tab `AnalysisReport`. Carries the backend
    /// row ID so we can hit the cached ticker_report_data JSONB and
    /// preserves the persona the report was generated with.
    init(report: AnalysisReport) {
        self.ticker = report.ticker
        self.persona = report.persona.backendKey
        self.reportId = report.backendId
        loadReport()
    }

    /// Preview-only initializer: sets data synchronously, no async Task.
    init(ticker: String, preloadedReport: TickerReportData) {
        self.ticker = ticker
        self.persona = "warren_buffett"
        self.reportId = nil
        self.reportData = preloadedReport
        self.isLoading = false
    }

    // MARK: - Data Loading

    func loadReport() {
        isLoading = true
        error = nil
        loadAttempts += 1

        Task { [weak self] in
            guard let self = self else { return }
            await self._fetchReport()
        }
    }

    /// Async refresh that properly awaits completion (used by .refreshable)
    func refresh() async {
        await _fetchReport()
    }

    /// Core fetch logic — shared by loadReport() and refresh()
    private func _fetchReport() async {
        let attempt = self.loadAttempts
        print("📊 [TickerReport] Loading report for \(self.ticker) with persona \(self.persona) (attempt \(attempt))...")

        // Path A — cached JSONB on a known research_reports row.
        // This is the fast path when navigating from the Reports tab:
        // the report was already generated, the full TickerReportResponse
        // is stored in ticker_report_data, and this returns instantly
        // with zero new FMP/Gemini calls.
        if let reportId = self.reportId {
            do {
                let response: TickerReportAPIResponse = try await APIClient.shared.request(
                    endpoint: .getResearchTickerReport(reportId: reportId),
                    responseType: TickerReportAPIResponse.self
                )
                print("✅ [TickerReport] Cache hit via report \(reportId) for \(response.symbol) (persona=\(self.persona))")
                self.reportData = response.toTickerReportData()
                self.error = nil
                self.isLoading = false
                return
            } catch {
                // Most common: 409 (report not yet completed) or 404
                // (data column was empty). Fall through to live fetch.
                print("⚠️ [TickerReport] Cached ticker_report_data unavailable for \(reportId): \(type(of: error)): \(error.localizedDescription). Falling back to live fetch.")
            }
        }

        // Path B — generate (or hit the 24h ticker_report_data cache
        // by ticker+persona) via the public ticker-report endpoint.
        do {
            let response: TickerReportAPIResponse = try await APIClient.shared.request(
                endpoint: .getTickerReport(ticker: self.ticker, persona: self.persona),
                responseType: TickerReportAPIResponse.self
            )

            print("✅ [TickerReport] Report loaded successfully for \(response.symbol)")
            print("   Quality Score: \(response.qualityScore)")
            print("   Agent: \(response.agent)")
            print("   Vitals: valuation=\(response.keyVitals.valuation != nil), moat=\(response.keyVitals.moat != nil)")
            print("   Sections: fundamentals=\(response.fundamentalMetrics.count), criticalFactors=\(response.criticalFactors.count)")

            let reportData = response.toTickerReportData()
            self.reportData = reportData
            self.error = nil
            self.isLoading = false

        } catch {
            // Surface the underlying error type so future debugging
            // can distinguish APIError.notFound vs decoding vs network.
            print("❌ [TickerReport] Failed to load report: \(type(of: error)): \(error)")
            if let apiError = error as? APIError {
                print("   API Error: \(apiError)")
            }
            print("   Error details: \(error.localizedDescription)")

            self.isLoading = false
            self.error = self.userFriendlyError(error)
            // Don't set reportData — let the error view show with retry button
        }
    }

    // MARK: - Actions
    func shareTapped() {
        print("📤 [TickerReport] Share report tapped for \(ticker)")
    }

    func viewDetailedAnalysis() {
        print("🔍 [TickerReport] View detailed analysis tapped for \(ticker)")
    }

    func chatWithReport() {
        guard !aiInputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let message = aiInputText
        aiInputText = ""

        print("💬 [TickerReport] Chat with report: \"\(message)\" for \(ticker)")

        chatUserQuestion = message
        isChatLoading = true
        chatResponse = nil
        showChatResponse = true

        Task { [weak self] in
            guard let self = self else { return }

            do {
                let response: TickerReportChatResponse = try await APIClient.shared.request(
                    endpoint: .chatWithTickerReport(
                        ticker: self.ticker,
                        message: message,
                        persona: self.persona
                    ),
                    responseType: TickerReportChatResponse.self
                )

                print("✅ [TickerReport] Chat response received: \(response.reply.prefix(100))...")
                self.chatResponse = response.reply
                self.isChatLoading = false
            } catch {
                print("❌ [TickerReport] Chat failed: \(error)")
                self.chatResponse = "Sorry, I couldn't process that right now. Please try again."
                self.isChatLoading = false
            }
        }
    }

    func dismissChatResponse() {
        showChatResponse = false
        chatResponse = nil
        chatUserQuestion = nil
    }

    // MARK: - Error Helpers

    private func userFriendlyError(_ error: Error) -> String {
        if let apiError = error as? APIError {
            switch apiError {
            case .networkError:
                return "Network error. Check your connection and make sure the backend is running."

            // Phase 3: backend now emits {error_code, user_message, …}
            // on the report-pipeline endpoints, surfaced as
            // .businessError. Route by code so users see actionable
            // copy (retry vs. wait vs. check symbol) and our logs
            // carry the underlying cause.
            case .businessError(let code, let message):
                switch code {
                case "TICKER_NOT_FOUND":
                    return "Ticker '\(ticker)' wasn't found. Check the symbol and try again."
                case "INVALID_PERSONA":
                    return "That investor persona isn't supported."
                case "INVALID_INPUT":
                    return message.isEmpty
                        ? "The request was invalid. Please try again."
                        : message
                case "FMP_RATE_LIMITED":
                    return "Market data is rate-limited right now. Please try again in a minute."
                case "FMP_UNAVAILABLE":
                    return "Our market data provider is temporarily unavailable. Try again shortly."
                case "GEMINI_QUOTA_EXCEEDED":
                    return "AI analysis quota exceeded. Please try again in a few minutes."
                case "GEMINI_UNAVAILABLE":
                    return "The AI analysis engine is temporarily unavailable. Try again shortly."
                case "DATA_INCOMPLETE":
                    return "We couldn't gather enough data for \(ticker) to produce a full report."
                case "REPORT_GENERATION_FAILED":
                    return "Report generation failed. Please try again."
                case "REPORT_NOT_FOUND":
                    return "That report no longer exists."
                case "REPORT_NOT_READY":
                    return "The report is still generating. Try again in a few seconds."
                case "INSUFFICIENT_CREDITS":
                    return "You're out of credits. Upgrade your tier or wait for the monthly reset."
                default:
                    // Unknown code — show backend's user_message
                    // verbatim so we still surface the cause without
                    // shipping an iOS update.
                    return message.isEmpty
                        ? "Something went wrong. Please try again."
                        : message
                }

            case .serverError(let code):
                return "Server error (\(code)). The AI report generation may have timed out. Try again."
            case .notFound:
                return "Ticker '\(ticker)' was not found. Check the symbol and try again."
            case .decodingError:
                return "Received unexpected data from the server. This is a bug — please report it."
            case .rateLimited(let retryAfter):
                return "You've hit a request limit. Try again in \(retryAfter)s."
            case .unauthorized:
                return "Your session expired. Please sign in again."
            case .forbidden:
                return "You don't have access to this. If this seems wrong, contact support."
            case .unknown(let message):
                return message.isEmpty
                    ? "Something went wrong. Please try again."
                    : message
            }
        }
        return "Could not load report. Please check your connection and try again."
    }
}

// MARK: - Chat Response DTO

struct TickerReportChatResponse: Codable {
    let reply: String
    let ticker: String

    enum CodingKeys: String, CodingKey {
        case reply, ticker
    }
}
