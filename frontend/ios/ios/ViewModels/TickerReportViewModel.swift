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

    // AI chat input (bound directly to CaudexAIChatBar in the View)
    @Published var aiInputText: String = ""

    // Chat response state
    @Published var chatResponse: String?
    @Published var isChatLoading: Bool = false

    // Deep Dive Section Expansion States
    @Published var expandedSections: Set<DeepDiveModuleType> = []

    // MARK: - Private Properties
    private let ticker: String
    private let persona: String
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
        loadReport()
    }

    /// Preview-only initializer: sets data synchronously, no async Task.
    init(ticker: String, preloadedReport: TickerReportData) {
        self.ticker = ticker
        self.persona = "warren_buffett"
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

            let attempt = self.loadAttempts
            print("📊 [TickerReport] Loading report for \(self.ticker) with persona \(self.persona) (attempt \(attempt))...")

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
                print("❌ [TickerReport] Failed to load report: \(error)")
                if let apiError = error as? APIError {
                    print("   API Error: \(apiError)")
                }
                print("   Error details: \(error.localizedDescription)")

                self.isLoading = false
                self.error = self.userFriendlyError(error)
                // Don't set reportData — let the error view show with retry button
            }
        }
    }

    func refresh() async {
        loadReport()
    }

    // MARK: - Section Toggle
    func toggleSection(_ type: DeepDiveModuleType) {
        if expandedSections.contains(type) {
            expandedSections.remove(type)
        } else {
            expandedSections.insert(type)
        }
    }

    func isSectionExpanded(_ type: DeepDiveModuleType) -> Bool {
        expandedSections.contains(type)
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

        isChatLoading = true
        chatResponse = nil

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

    // MARK: - Error Helpers

    private func userFriendlyError(_ error: Error) -> String {
        if let apiError = error as? APIError {
            switch apiError {
            case .networkError:
                return "Network error. Check your connection and make sure the backend is running."
            case .serverError(let code):
                return "Server error (\(code)). The AI report generation may have timed out. Try again."
            case .notFound:
                return "Ticker '\(ticker)' was not found. Check the symbol and try again."
            case .decodingError:
                return "Received unexpected data from the server. This is a bug — please report it."
            default:
                return "Something went wrong. Please try again."
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
