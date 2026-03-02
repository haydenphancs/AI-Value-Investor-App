//
//  ResearchViewModel.swift
//  ios
//
//  ViewModel for Research screen - MVVM Architecture
//  Fetches real data from backend for reports, credits, and manages AI generation.
//

import Foundation
import Combine

@MainActor
class ResearchViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var selectedTab: ResearchTab = .research
    @Published var searchText: String = ""
    @Published var quickTickers: [QuickTicker] = QuickTicker.defaults
    @Published var personas: [AnalysisPersona] = AnalysisPersona.allCases
    @Published var selectedPersona: AnalysisPersona = .warrenBuffett
    @Published var features: [AnalysisFeature] = AnalysisFeature.allFeatures
    @Published var creditBalance: CreditBalance = .mock
    @Published var trendingAnalyses: [TrendingAnalysis] = TrendingAnalysis.mockTrending
    @Published var analysisCost: AnalysisCost = .standard
    @Published var isLoading: Bool = false
    @Published var isGeneratingAnalysis: Bool = false
    @Published var generationProgress: Int = 0
    @Published var generationStep: String = ""
    @Published var error: String?

    // Reports Tab Properties
    @Published var reports: [AnalysisReport] = []
    @Published var reportSortOption: ReportSortOption = .dateNewest {
        didSet {
            sortReports()
        }
    }
    @Published var communityInsights: [CommunityInsight] = CommunityInsight.mockInsights

    // Auth gate: set to true when user is signed in
    @Published var showSignInPrompt: Bool = false

    // MARK: - Dependencies
    private let apiClient: APIClient
    private let pollingManager: TaskPollingManager
    private var isAuthenticated: () -> Bool = { false }

    // MARK: - Initialization
    init(prefilledTicker: String? = nil, apiClient: APIClient = .shared, isAuthenticated: @escaping () -> Bool = { false }) {
        self.apiClient = apiClient
        self.pollingManager = TaskPollingManager(apiClient: apiClient)
        self.isAuthenticated = isAuthenticated
        if let ticker = prefilledTicker {
            _searchText = Published(initialValue: ticker)
        }
        // Start with static data immediately, then load real data
        quickTickers = QuickTicker.defaults
        personas = AnalysisPersona.allCases
        features = AnalysisFeature.allFeatures
        trendingAnalyses = TrendingAnalysis.mockTrending

        // Fetch real data from backend
        Task { [weak self] in
            await self?.loadBackendData()
        }
    }

    // MARK: - Backend Data Loading

    /// Load real reports + credits from the backend. Falls back to mock on failure.
    private func loadBackendData() async {
        // Load reports and credits in parallel
        async let reportsTask: () = loadReports()
        async let creditsTask: () = loadCredits()
        _ = await (reportsTask, creditsTask)
    }

    /// Fetch user's research reports from GET /research/reports
    func loadReports() async {
        print("📋 ResearchVM: Loading reports from backend...")
        do {
            let backendReports: [BackendReportListItem] = try await apiClient.request(
                endpoint: .getMyReports(limit: 50),
                responseType: [BackendReportListItem].self
            )
            print("✅ ResearchVM: Loaded \(backendReports.count) reports from backend")
            self.reports = backendReports.map { AnalysisReport.from($0) }
            sortReports()
        } catch {
            print("⚠️ ResearchVM: Failed to load reports — \(error). Using mock data.")
            if reports.isEmpty {
                reports = AnalysisReport.mockReports
                sortReports()
            }
        }
    }

    /// Fetch user's credit balance from GET /users/me/credits
    func loadCredits() async {
        print("💳 ResearchVM: Loading credits from backend...")
        do {
            let backendCredits: BackendCreditsResponse = try await apiClient.request(
                endpoint: .getUserCredits,
                responseType: BackendCreditsResponse.self
            )
            print("✅ ResearchVM: Credits loaded — \(backendCredits.remaining) remaining of \(backendCredits.total)")
            self.creditBalance = CreditBalance.from(backendCredits)
        } catch {
            print("⚠️ ResearchVM: Failed to load credits — \(error). Using mock data.")
            // Keep existing (mock) value
        }
    }

    func refresh() async {
        isLoading = true
        await loadBackendData()
        isLoading = false
    }

    // MARK: - Auth Configuration
    func setAuthCheck(_ check: @escaping () -> Bool) {
        self.isAuthenticated = check
    }

    // MARK: - Actions
    func selectPersona(_ persona: AnalysisPersona) {
        selectedPersona = persona
    }

    func selectQuickTicker(_ ticker: QuickTicker) {
        searchText = ticker.symbol
    }

    func generateAnalysis() {
        guard isAuthenticated() else {
            showSignInPrompt = true
            return
        }

        let ticker = searchText.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else { return }
        guard creditBalance.credits >= analysisCost.credits else {
            error = "Insufficient credits"
            return
        }

        isGeneratingAnalysis = true
        generationProgress = 0
        generationStep = "Starting analysis..."
        error = nil

        let personaKey = selectedPersona.backendKey

        print("🔬 ResearchVM: Generating analysis for \(ticker) with persona \(personaKey)...")

        Task { [weak self] in
            guard let self = self else { return }

            do {
                let stream = await self.pollingManager.generateAndMonitorResearch(
                    stockId: ticker,
                    persona: personaKey
                )

                for try await progress in stream {
                    switch progress {
                    case .started(let taskId):
                        print("🔬 ResearchVM: Research started — report ID: \(taskId)")
                        self.generationStep = "Research initiated..."

                    case .progress(let percent, let step):
                        print("🔬 ResearchVM: Progress \(percent)% — \(step)")
                        self.generationProgress = percent
                        self.generationStep = step

                    case .completed(let report):
                        print("✅ ResearchVM: Research complete for \(ticker) — \(report.title ?? "Untitled")")
                        self.isGeneratingAnalysis = false
                        self.generationProgress = 100
                        self.generationStep = "Complete!"
                        // Reload reports and credits from backend to get fresh data
                        await self.loadReports()
                        await self.loadCredits()

                    case .failed(let appError):
                        print("❌ ResearchVM: Research failed — \(appError.message)")
                        self.isGeneratingAnalysis = false
                        self.error = appError.message
                    }
                }
            } catch {
                print("❌ ResearchVM: Research stream error — \(error)")
                self.isGeneratingAnalysis = false
                self.error = error.localizedDescription
            }
        }
    }

    func addMoreCredits() {
        print("Add more credits tapped")
    }

    func exploreTrending() {
        print("Explore trending tapped")
    }

    func selectTrendingAnalysis(_ analysis: TrendingAnalysis) {
        searchText = analysis.title
        print("Selected trending: \(analysis.title)")
    }

    func viewAllPersonas() {
        print("View all personas tapped")
    }

    // MARK: - Reports Tab Actions
    func sortReports() {
        switch reportSortOption {
        case .dateNewest:
            reports.sort { $0.date > $1.date }
        case .dateOldest:
            reports.sort { $0.date < $1.date }
        case .ratingHigh:
            reports.sort { ($0.rating ?? 0) > ($1.rating ?? 0) }
        case .ratingLow:
            reports.sort { ($0.rating ?? 0) < ($1.rating ?? 0) }
        }
    }

    func openReport(_ report: AnalysisReport) {
        guard report.status == .ready else { return }
        print("Opening report: \(report.companyName)")
    }

    func retryReport(_ report: AnalysisReport) {
        guard report.status == .failed else { return }
        print("🔄 ResearchVM: Retrying report for \(report.ticker)...")
        searchText = report.ticker
        selectedPersona = report.persona
        generateAnalysis()
    }

    func joinDiscussion() {
        print("Join discussion tapped")
    }

    func likeInsight(_ insight: CommunityInsight) {
        print("Liked insight from: \(insight.userName)")
    }

    func commentOnInsight(_ insight: CommunityInsight) {
        print("Comment on insight from: \(insight.userName)")
    }

    func shareInsight(_ insight: CommunityInsight) {
        print("Share insight from: \(insight.userName)")
    }

    // MARK: - Computed Properties
    var canGenerateAnalysis: Bool {
        !searchText.isEmpty && creditBalance.credits >= analysisCost.credits
    }

    var selectedPersonaDescription: String {
        selectedPersona.description
    }

    var analysisStyleTitle: String {
        "\(selectedPersona.rawValue.components(separatedBy: " ").last ?? "") Style Analysis"
    }
}
