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

    // Search results (as-you-type)
    @Published var searchResults: [StockSearchResult] = []
    @Published var isSearching: Bool = false
    @Published var showSearchResults: Bool = false

    // Auth gate: set to true when user is signed in
    @Published var showSignInPrompt: Bool = false

    // Sheet presentation flags
    @Published var showCreditsSheet: Bool = false
    @Published var showPersonasSheet: Bool = false
    @Published var showProfileSheet: Bool = false
    @Published var showTargetSearchSheet: Bool = false

    /// Currently chosen company. Constraint: only one ticker at a time.
    /// Setting this also drives `searchText` so `generateAnalysis()` keeps working.
    @Published var selectedTarget: StockSearchResult?

    // MARK: - Dependencies
    private let apiClient: APIClient
    private let stockRepository: StockRepository
    private let pollingManager: TaskPollingManager
    private var isAuthenticated: () -> Bool = { false }
    private var searchTask: Task<Void, Never>?
    private var reportsPollTask: Task<Void, Never>?
    private var cancellables = Set<AnyCancellable>()

    // MARK: - Initialization
    init(prefilledTicker: String? = nil, apiClient: APIClient = .shared, isAuthenticated: @escaping () -> Bool = { false }) {
        self.apiClient = apiClient
        self.stockRepository = StockRepository(apiClient: apiClient)
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

        // Search is handled by the dedicated TargetSearchSheet — no debounce here.

        // Fetch real data from backend
        Task { [weak self] in
            await self?.loadBackendData()
        }
    }

    // MARK: - Backend Data Loading

    /// Load real reports + credits + trending + personas from the backend.
    /// Falls back to mock/static defaults on failure.
    private func loadBackendData() async {
        async let reportsTask: () = loadReports()
        async let creditsTask: () = loadCredits()
        async let trendingTask: () = loadTrending()
        async let personasTask: () = loadPersonas()
        _ = await (reportsTask, creditsTask, trendingTask, personasTask)
    }

    /// Fetch active personas from GET /research/personas.
    func loadPersonas() async {
        print("👤 ResearchVM: Loading personas from backend...")
        do {
            let backend: [BackendPersona] = try await apiClient.request(
                endpoint: .getPersonas,
                responseType: [BackendPersona].self
            )
            print("✅ ResearchVM: Loaded \(backend.count) personas")
            let mapped = backend.map(AnalysisPersona.from)
            guard !mapped.isEmpty else { return }
            self.personas = mapped
            // Keep current selection if still present, else default to first.
            if !mapped.contains(where: { $0.key == self.selectedPersona.key }) {
                self.selectedPersona = mapped[0]
            }
        } catch {
            print("⚠️ ResearchVM: Failed to load personas — \(error). Keeping fallbacks.")
        }
    }

    /// Fetch trending analyses from GET /research/trending.
    func loadTrending() async {
        print("📈 ResearchVM: Loading trending analyses from backend...")
        do {
            let backendTrending: [BackendTrendingAnalysis] = try await apiClient.request(
                endpoint: .getTrendingAnalyses,
                responseType: [BackendTrendingAnalysis].self
            )
            print("✅ ResearchVM: Loaded \(backendTrending.count) trending themes")
            let mapped = backendTrending.map(TrendingAnalysis.from)
            if !mapped.isEmpty {
                self.trendingAnalyses = mapped
            }
        } catch {
            print("⚠️ ResearchVM: Failed to load trending — \(error). Keeping current data.")
            // Keep existing (mock) value
        }
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

    // MARK: - Reports Tab Live Polling

    /// Poll the reports list every 5s while any report is in-flight.
    /// Called when the user switches to the Reports tab. Self-terminates
    /// once no processing/pending reports remain — no need to cancel
    /// manually in that case.
    func startReportsPolling() {
        stopReportsPolling()
        reportsPollTask = Task { [weak self] in
            // 5s cadence balances "card animates" with FMP/Supabase load.
            // Each tick is a single Supabase query — no FMP cost.
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                if Task.isCancelled { return }
                guard let self = self else { return }
                let hasInflight = self.reports.contains { $0.status == .processing }
                if !hasInflight {
                    return
                }
                await self.loadReports()
            }
        }
    }

    func stopReportsPolling() {
        reportsPollTask?.cancel()
        reportsPollTask = nil
    }

    // MARK: - Auth Configuration
    func setAuthCheck(_ check: @escaping () -> Bool) {
        self.isAuthenticated = check
    }

    // MARK: - Search

    private func setupSearchDebounce() {
        $searchText
            .debounce(for: .milliseconds(300), scheduler: RunLoop.main)
            .removeDuplicates()
            .sink { [weak self] query in
                guard let self else { return }
                let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
                if trimmed.isEmpty {
                    self.searchResults = []
                    self.showSearchResults = false
                    self.searchTask?.cancel()
                    return
                }
                self.performSearch(query: trimmed)
            }
            .store(in: &cancellables)
    }

    private func performSearch(query: String) {
        searchTask?.cancel()
        searchTask = Task { [weak self] in
            guard let self else { return }
            self.isSearching = true
            self.showSearchResults = true
            do {
                let results = try await self.stockRepository.searchStocks(query: query, limit: 8)
                if !Task.isCancelled {
                    self.searchResults = results
                    self.isSearching = false
                }
            } catch {
                if !Task.isCancelled {
                    self.searchResults = []
                    self.isSearching = false
                }
            }
        }
    }

    func selectSearchResult(_ result: StockSearchResult) {
        searchText = result.ticker
        searchResults = []
        showSearchResults = false
    }

    func dismissSearchResults() {
        showSearchResults = false
    }

    // MARK: - Actions
    func selectPersona(_ persona: AnalysisPersona) {
        selectedPersona = persona
    }

    func selectQuickTicker(_ ticker: QuickTicker) {
        searchText = ticker.symbol
        searchResults = []
        showSearchResults = false
        selectedTarget = StockSearchResult(
            ticker: ticker.symbol,
            companyName: ticker.symbol,
            exchange: nil,
            sector: nil,
            logoUrl: nil,
            type: "stock"
        )
    }

    // MARK: - Target Selection

    func openTargetSearch() {
        showTargetSearchSheet = true
    }

    func selectTarget(_ result: StockSearchResult) {
        selectedTarget = result
        searchText = result.ticker
        showTargetSearchSheet = false
    }

    func clearTarget() {
        selectedTarget = nil
        searchText = ""
    }

    func generateAnalysis() {
        print("🔬 ResearchVM: generateAnalysis() tapped — searchText='\(searchText)', persona=\(selectedPersona.backendKey), credits=\(creditBalance.credits)")

        // DEV: auth disabled — backend handles unauthenticated callers as guest.

        let ticker = searchText.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !ticker.isEmpty else {
            print("⚠️ ResearchVM: bailed — ticker is empty after trimming. Surfacing error.")
            error = "Please select a ticker first."
            return
        }
        guard creditBalance.credits >= analysisCost.credits else {
            print("⚠️ ResearchVM: bailed — insufficient credits (\(creditBalance.credits) < \(analysisCost.credits)).")
            error = "Insufficient credits"
            return
        }

        isGeneratingAnalysis = true
        selectedTab = .reports
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

                // Track the last percent we used to refresh the list,
                // so we don't hammer the backend on every tick.
                var lastListRefreshPercent = -1

                for try await progress in stream {
                    switch progress {
                    case .started(let taskId):
                        print("🔬 ResearchVM: Research started — report ID: \(taskId)")
                        self.generationStep = "Research initiated..."
                        // Surface the new pending row in the Reports list
                        // immediately so the Tesla-style processing card
                        // appears the moment the user switches tabs.
                        await self.loadReports()

                    case .progress(let percent, let step):
                        print("🔬 ResearchVM: Progress \(percent)% — \(step)")
                        self.generationProgress = percent
                        self.generationStep = step
                        // Refresh the list at 25% boundaries so the card
                        // animates without spamming Supabase. The poller
                        // in startReportsPolling() is the steady-state
                        // updater; this is a coarser belt-and-braces.
                        let bucket = (percent / 25) * 25
                        if bucket > lastListRefreshPercent {
                            lastListRefreshPercent = bucket
                            await self.loadReports()
                        }

                    case .completed(let report):
                        print("✅ ResearchVM: Research complete for \(ticker) — \(report.title ?? "Untitled")")
                        self.isGeneratingAnalysis = false
                        self.generationProgress = 100
                        self.generationStep = "Complete!"
                        // Reload reports and credits from backend to get fresh data
                        await self.loadReports()
                        await self.loadCredits()

                    case .failed(let appError):
                        print("❌ ResearchVM: Research failed — \(type(of: appError)): \(appError.message)")
                        self.isGeneratingAnalysis = false
                        self.error = appError.message
                        // Refresh so the failed card appears in the list
                        await self.loadReports()
                    }
                }
            } catch {
                print("❌ ResearchVM: Research stream error — \(type(of: error)): \(error)")
                self.isGeneratingAnalysis = false
                self.error = error.localizedDescription
            }
        }
    }

    func addMoreCredits() {
        showCreditsSheet = true
    }

    func viewAllPersonas() {
        showPersonasSheet = true
    }

    func showProfile() {
        showProfileSheet = true
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
