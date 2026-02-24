//
//  TickerReportViewModel.swift
//  ios
//
//  ViewModel for the Ticker Report screen - MVVM Architecture
//

import Foundation
import Combine

@MainActor
class TickerReportViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var reportData: TickerReportData?
    @Published var isLoading: Bool = false
    @Published var error: String?

    // Deep Dive Section Expansion States
    @Published var expandedSections: Set<DeepDiveModuleType> = []

    // MARK: - Private Properties
    private let ticker: String

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
    init(ticker: String) {
        self.ticker = ticker
        loadReport()
    }

    /// Preview-only initializer: sets data synchronously, no async Task.
    init(ticker: String, preloadedReport: TickerReportData) {
        self.ticker = ticker
        self.reportData = preloadedReport
        self.isLoading = false
    }

    // MARK: - Data Loading
    func loadReport() {
        isLoading = true
        Task { [weak self] in
            guard let self = self else { return }
            let report = TickerReportData.sampleOracle
            self.reportData = report
            self.isLoading = false
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
        print("Share report tapped for \(ticker)")
    }

    func viewDetailedAnalysis() {
        print("View detailed analysis tapped for \(ticker)")
    }

    func chatWithReport() {
        print("Chat with report tapped for \(ticker)")
    }
}
