//
//  ResearchViewModel.swift
//  ios
//
//  ViewModel for Research screen - MVVM Architecture
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
    @Published var reports: [AnalysisReport] = AnalysisReport.mockReports
    @Published var reportSortOption: ReportSortOption = .dateNewest {
        didSet {
            sortReports()
        }
    }
    @Published var communityInsights: [CommunityInsight] = CommunityInsight.mockInsights

    // MARK: - Dependencies
    private let apiClient: APIClient
    private let pollingManager: TaskPollingManager

    // MARK: - Initialization
    init(prefilledTicker: String? = nil, apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        self.pollingManager = TaskPollingManager(apiClient: apiClient)
        if let ticker = prefilledTicker {
            _searchText = Published(initialValue: ticker)
        }
        loadInitialData()
        sortReports()
    }

    // MARK: - Data Loading
    private func loadInitialData() {
        quickTickers = QuickTicker.defaults
        personas = AnalysisPersona.allCases
        features = AnalysisFeature.allFeatures
        trendingAnalyses = TrendingAnalysis.mockTrending
    }

    func refresh() async {
        loadInitialData()
    }

    // MARK: - Actions
    func selectPersona(_ persona: AnalysisPersona) {
        selectedPersona = persona
    }

    func selectQuickTicker(_ ticker: QuickTicker) {
        searchText = ticker.symbol
    }

    func generateAnalysis() {
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
                        // Add to reports list as a ready report
                        let newReport = AnalysisReport(
                            companyName: report.companyName,
                            ticker: report.ticker,
                            industry: "",
                            persona: self.selectedPersona,
                            status: .ready,
                            progress: nil,
                            rating: nil,
                            ratingLabel: nil,
                            date: Date(),
                            isRefunded: false
                        )
                        self.reports.insert(newReport, at: 0)

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
        print("Retrying report: \(report.companyName)")
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
