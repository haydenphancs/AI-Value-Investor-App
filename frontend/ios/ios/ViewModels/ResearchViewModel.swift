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

    // MARK: - Reports Tab: Search + Multi-Select
    /// Distinct from `searchText` (which drives the Research-tab stock target
    /// search + generateAnalysis). This one only filters the Reports list.
    @Published var reportSearchText: String = ""
    @Published var isReportSearchActive: Bool = false
    @Published var isSelectingReports: Bool = false
    /// Keyed by `backendId` (NOT the per-load `AnalysisReport.id` UUID, which is
    /// reminted on every `loadReports()`), so a selection survives the 5s poll
    /// reload. Mock rows have no `backendId` and are therefore not selectable —
    /// which is fine, they can't be deleted either.
    @Published var selectedReportIds: Set<String> = []
    @Published var isDeletingReports: Bool = false
    @Published var showDeleteConfirm: Bool = false

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
    /// Backend id of the report currently being generated, so the live
    /// progress stream can mirror its % + step onto the matching list row
    /// (the GET /reports list query lags the per-report /status poll).
    private var generatingReportId: String?
    private var cancellables = Set<AnyCancellable>()

    /// Backend report IDs the client has locally given up on because they've
    /// been .processing past `processingTimeoutSeconds`. Survives across
    /// loadReports() calls so a stubborn backend "processing" row stays
    /// flipped to .failed in the UI. Cleared per-id when the backend reports
    /// a terminal status (.ready / .failed).
    private var locallyTimedOutReportIds: Set<String> = []

    /// Generous upper bound — real generations finish in 3-5 min. After
    /// 10 min with no terminal status, we assume Railway is down (or the
    /// task got abandoned) and surface the error card.
    private let processingTimeoutSeconds: TimeInterval = 600

    /// Backend report IDs the user has retried out of (or otherwise
    /// dismissed). The failed card disappears from the list immediately
    /// on retry tap; we then filter these out of every loadReports()
    /// result so it doesn't pop back when the backend still returns the
    /// stale failed row. In-memory only — app restart resets it.
    private var dismissedReportIds: Set<String> = []

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

    /// Detect reports stuck in .processing past `processingTimeoutSeconds`,
    /// register their backend IDs, and flip them to `.failed` locally so the
    /// ReportCard's failed branch (with the Retry button) appears. Runs
    /// against `self.reports` in-place after every load attempt. Mock
    /// reports without a `backendId` are skipped — the timeout only applies
    /// to real backend-tracked generations.
    private func applyClientSideTimeoutPass() {
        let now = Date()
        reports = reports.map { report in
            guard let backendId = report.backendId else { return report }
            // Backend gave us a terminal status — trust it, clear any prior flag.
            if report.status == .ready || report.status == .failed {
                locallyTimedOutReportIds.remove(backendId)
                return report
            }
            // Still .processing — age out if past the timeout.
            let age = now.timeIntervalSince(report.date)
            if age > processingTimeoutSeconds {
                locallyTimedOutReportIds.insert(backendId)
            }
            if locallyTimedOutReportIds.contains(backendId) {
                return report.withClientTimeout()
            }
            return report
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
            self.reports = backendReports
                .filter { !dismissedReportIds.contains($0.id) }
                .map { AnalysisReport.from($0) }
            applyClientSideTimeoutPass()
            sortReports()
            applyLiveProgress()   // keep the in-flight row at the live stream %
        } catch {
            print("⚠️ ResearchVM: Failed to load reports — \(error). Using mock data.")
            if reports.isEmpty {
                reports = AnalysisReport.mockReports
                sortReports()
            } else {
                // Network blip — keep existing rows but still age out the stale ones.
                applyClientSideTimeoutPass()
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
        guard !isDeletingReports else { return }   // don't race the delete fan-out
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
                // Don't churn the list mid-selection — a reload remints row
                // UUIDs and can reorder rows under the user. Skip this tick;
                // the task stays alive and resumes once selection ends.
                if self.isSelectingReports { continue }
                await self.loadReports()
            }
        }
    }

    func stopReportsPolling() {
        reportsPollTask?.cancel()
        reportsPollTask = nil
    }

    /// Mirror the live generation stream onto the in-flight report row so the
    /// processing card animates in real time. The GET /reports list query's
    /// stored progress can lag the per-report /status poll the stream uses, so
    /// without this the card freezes at the last list-refreshed value (e.g. 5%
    /// while the stream is already at 20%). No-op once the row goes ready/failed.
    private func applyLiveProgress() {
        guard let rid = generatingReportId, generationProgress > 0,
              let idx = reports.firstIndex(where: { $0.backendId == rid }),
              reports[idx].status == .processing else { return }
        reports[idx].progress = Double(generationProgress) / 100.0
        if !generationStep.isEmpty {
            reports[idx].currentStep = generationStep
        }
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

        // Debounce: a single fast double-tap on Generate (or Retry) would
        // otherwise post /research/generate twice and create two rows.
        guard !isGeneratingAnalysis else {
            print("⚠️ ResearchVM: generation already in flight, ignoring duplicate tap.")
            return
        }

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
                        self.generatingReportId = taskId
                        self.generationStep = "Research initiated..."
                        // Surface the new pending row in the Reports list
                        // immediately so the Tesla-style processing card
                        // appears the moment the user switches tabs.
                        await self.loadReports()

                    case .progress(let percent, let step):
                        print("🔬 ResearchVM: Progress \(percent)% — \(step)")
                        self.generationProgress = percent
                        self.generationStep = step
                        self.applyLiveProgress()   // update the processing card now
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
                        self.generatingReportId = nil
                        // Reload reports and credits from backend to get fresh data
                        await self.loadReports()
                        await self.loadCredits()

                    case .failed(let appError):
                        print("❌ ResearchVM: Research failed — \(type(of: appError)): \(appError.message)")
                        self.isGeneratingAnalysis = false
                        self.generatingReportId = nil
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

    // MARK: - Reports Tab: Derived (search + grouping)

    /// `reports` filtered by the report search query (ticker OR company name,
    /// case-insensitive). `reports` is already sorted in place by sortReports(),
    /// so this preserves the chosen sort order.
    var filteredReports: [AnalysisReport] {
        let q = reportSearchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return reports }
        return reports.filter {
            $0.ticker.lowercased().contains(q) || $0.companyName.lowercased().contains(q)
        }
    }

    /// Filtered reports grouped into time bands, ordered newest → oldest.
    /// Empty bands are omitted so no stray section header renders. The Sort
    /// option orders cards WITHIN each band (since `filteredReports` is sorted).
    var groupedReports: [ReportSectionGroup] {
        let buckets = Dictionary(grouping: filteredReports) { ReportTimeSection.bucket(for: $0.date) }
        return ReportTimeSection.allCases.compactMap { section in
            guard let rows = buckets[section], !rows.isEmpty else { return nil }
            return ReportSectionGroup(section: section, reports: rows)
        }
    }

    var selectedReportCount: Int { selectedReportIds.count }

    // MARK: - Reports Tab: Selection + Delete

    func toggleReportSelection(_ report: AnalysisReport) {
        guard let bid = report.backendId else { return }   // mock rows aren't selectable
        if selectedReportIds.contains(bid) {
            selectedReportIds.remove(bid)
        } else {
            selectedReportIds.insert(bid)
        }
    }

    func exitSelectionMode() {
        isSelectingReports = false
        selectedReportIds.removeAll()
    }

    /// Delete every selected report. Fans out parallel DELETEs against the
    /// existing per-report endpoint (soft-delete, idempotent → safe to parallel).
    /// Rows are removed optimistically and seeded into `dismissedReportIds` so a
    /// subsequent poll/loadReports() can't resurrect them. On partial failure the
    /// failed ids are un-dismissed and the list is reconciled via loadReports().
    func deleteSelectedReports() async {
        guard !selectedReportIds.isEmpty, !isDeletingReports else { return }
        isDeletingReports = true
        defer { isDeletingReports = false }

        let ids = Array(selectedReportIds)

        // Optimistic removal + dismiss-seed (mirrors retryReport's pattern).
        for id in ids { dismissedReportIds.insert(id) }
        reports.removeAll { report in
            guard let bid = report.backendId else { return false }
            return ids.contains(bid)
        }
        exitSelectionMode()

        // Parallel fan-out. Return (rid, success) — a Sendable tuple, so no
        // `any Error` crosses the task boundary.
        var failedIds: [String] = []
        await withTaskGroup(of: (String, Bool).self) { group in
            for rid in ids {
                group.addTask { [apiClient] in
                    do {
                        try await apiClient.request(endpoint: .deleteReport(reportId: rid))
                        return (rid, true)
                    } catch {
                        return (rid, false)
                    }
                }
            }
            for await (rid, ok) in group where !ok {
                failedIds.append(rid)
                dismissedReportIds.remove(rid)   // allow the failed row to come back
            }
        }

        if !failedIds.isEmpty {
            await loadReports()   // reconcile: rows that failed to delete reappear
            let n = failedIds.count
            self.error = "Couldn't delete \(n) report\(n == 1 ? "" : "s"). Please try again."
        }
    }

    func retryReport(_ report: AnalysisReport) {
        guard report.status == .failed else { return }
        // Same debounce as generateAnalysis() — if another generation is
        // already in flight, don't dismiss the failed card pre-emptively
        // (otherwise the card would vanish with no replacement).
        guard !isGeneratingAnalysis else {
            print("⚠️ ResearchVM: another generation in flight, ignoring retry tap.")
            return
        }
        print("🔄 ResearchVM: Retrying report for \(report.ticker)...")
        // Drop the failed card immediately — both from the in-memory list
        // and from the dismiss-set so the next loadReports() doesn't
        // re-surface it. The new processing card will appear when
        // generateAnalysis() spawns the next report.
        if let backendId = report.backendId {
            dismissedReportIds.insert(backendId)
            reports.removeAll { $0.backendId == backendId }
        }
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
