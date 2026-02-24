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
    @Published var error: String?

    // Reports Tab Properties
    @Published var reports: [AnalysisReport] = AnalysisReport.mockReports
    @Published var reportSortOption: ReportSortOption = .dateNewest {
        didSet {
            sortReports()
        }
    }
    @Published var communityInsights: [CommunityInsight] = CommunityInsight.mockInsights

    // MARK: - Initialization
    init(prefilledTicker: String? = nil) {
        if let ticker = prefilledTicker {
            _searchText = Published(initialValue: ticker)
        }
        loadMockData()
        sortReports()
    }

    // MARK: - Data Loading
    func loadMockData() {
        isLoading = true

        Task { [weak self] in
            guard let self = self else { return }
            let tickers = QuickTicker.defaults
            let allPersonas = AnalysisPersona.allCases
            let allFeatures = AnalysisFeature.allFeatures
            let trending = TrendingAnalysis.mockTrending

            self.quickTickers = tickers
            self.personas = allPersonas
            self.features = allFeatures
            self.trendingAnalyses = trending
            self.isLoading = false
        }
    }

    func refresh() async {
        loadMockData()
    }

    // MARK: - Actions
    func selectPersona(_ persona: AnalysisPersona) {
        selectedPersona = persona
    }

    func selectQuickTicker(_ ticker: QuickTicker) {
        searchText = ticker.symbol
    }

    func generateAnalysis() {
        guard !searchText.isEmpty else { return }
        guard creditBalance.credits >= analysisCost.credits else {
            error = "Insufficient credits"
            return
        }

        isGeneratingAnalysis = true

        Task { [weak self] in
            // Simulate analysis generation delay
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard let self = self else { return }
            self.isGeneratingAnalysis = false
            print("Analysis generated for \(self.searchText) using \(self.selectedPersona.rawValue) style")
        }
    }

    func addMoreCredits() {
        // Navigate to purchase flow
        print("Add more credits tapped")
    }

    func exploreTrending() {
        // Navigate to trending analyses
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
