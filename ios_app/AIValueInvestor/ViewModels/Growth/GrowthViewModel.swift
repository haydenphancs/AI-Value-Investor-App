import Foundation
import Combine

/// ViewModel managing the Growth section state and data
@MainActor
final class GrowthViewModel: ObservableObject {

    // MARK: - Published Properties

    /// Currently selected metric type
    @Published var selectedMetric: GrowthMetricType = .revenue

    /// Currently selected period type (Annual/Quarterly)
    @Published var selectedPeriod: GrowthPeriodType = .annual

    /// Current growth data for the selected metric
    @Published private(set) var currentData: GrowthMetricData?

    /// Loading state
    @Published private(set) var isLoading: Bool = false

    /// Error message if any
    @Published private(set) var errorMessage: String?

    /// Whether the info sheet is showing
    @Published var isShowingInfo: Bool = false

    // MARK: - Private Properties

    private let ticker: String
    private var cancellables = Set<AnyCancellable>()

    /// Cache for loaded metric data to avoid re-fetching
    private var dataCache: [String: GrowthMetricData] = [:]

    // MARK: - Computed Properties

    /// Available metrics for the tab bar
    var availableMetrics: [GrowthMetricType] {
        GrowthMetricType.allCases
    }

    /// Available period types for the toggle
    var availablePeriods: [GrowthPeriodType] {
        GrowthPeriodType.allCases
    }

    /// Data points from current data
    var dataPoints: [GrowthDataPoint] {
        currentData?.dataPoints ?? []
    }

    /// Maximum value for chart Y-axis scaling
    var chartMaxValue: Double {
        currentData?.maxValue ?? 250
    }

    /// Cache key for current selection
    private var cacheKey: String {
        "\(ticker)_\(selectedMetric.rawValue)_\(selectedPeriod.rawValue)"
    }

    // MARK: - Initialization

    init(ticker: String) {
        self.ticker = ticker
        setupBindings()
        loadInitialData()
    }

    // MARK: - Public Methods

    /// Refresh data for current selection
    func refreshData() async {
        await loadData(forceRefresh: true)
    }

    /// Select a new metric type
    func selectMetric(_ metric: GrowthMetricType) {
        guard metric != selectedMetric else { return }
        selectedMetric = metric
    }

    /// Select a new period type
    func selectPeriod(_ period: GrowthPeriodType) {
        guard period != selectedPeriod else { return }
        selectedPeriod = period
    }

    /// Show the info sheet
    func showInfo() {
        isShowingInfo = true
    }

    /// Navigate to detail view
    func navigateToDetail() {
        // This would trigger navigation through coordinator or navigation state
        // For now, this is a placeholder for future implementation
    }

    // MARK: - Private Methods

    private func setupBindings() {
        // React to metric changes
        $selectedMetric
            .dropFirst()
            .sink { [weak self] _ in
                Task {
                    await self?.loadData()
                }
            }
            .store(in: &cancellables)

        // React to period changes
        $selectedPeriod
            .dropFirst()
            .sink { [weak self] _ in
                Task {
                    await self?.loadData()
                }
            }
            .store(in: &cancellables)
    }

    private func loadInitialData() {
        Task {
            await loadData()
        }
    }

    private func loadData(forceRefresh: Bool = false) async {
        // Check cache first
        if !forceRefresh, let cached = dataCache[cacheKey] {
            currentData = cached
            return
        }

        isLoading = true
        errorMessage = nil

        // Simulate API call - In production, this would call the actual API
        // For now, using sample data based on metric type
        do {
            try await Task.sleep(nanoseconds: 300_000_000) // 0.3 second delay

            let data = getSampleData(for: selectedMetric)
            dataCache[cacheKey] = data
            currentData = data
            isLoading = false
        } catch {
            isLoading = false
            errorMessage = "Failed to load growth data"
        }
    }

    /// Returns sample data for the given metric type
    /// In production, this would be replaced with actual API calls
    private func getSampleData(for metric: GrowthMetricType) -> GrowthMetricData {
        switch metric {
        case .eps:
            return .sampleEPS
        case .revenue:
            return .sampleRevenue
        case .netIncome:
            return .sampleNetIncome
        case .operatingProfit:
            return .sampleOperatingProfit
        case .freeCashFlow:
            return .sampleFreeCashFlow
        }
    }
}

// MARK: - Preview Helper

extension GrowthViewModel {
    /// Creates a preview instance with sample data
    static var preview: GrowthViewModel {
        let viewModel = GrowthViewModel(ticker: "AAPL")
        return viewModel
    }
}
