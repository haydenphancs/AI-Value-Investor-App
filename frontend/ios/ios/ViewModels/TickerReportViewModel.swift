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
    @Published var expandedSections: Set<DeepDiveModuleType> = [.fundamentalsGrowth]

    // Price Movement Timeframe
    @Published var selectedPriceTimeframe: PriceTimeframe = .oneWeek

    // MARK: - Private Properties
    private let ticker: String

    // Deep Dive Modules - stored once to avoid regenerating UUIDs on every access
    let deepDiveModules: [DeepDiveModule] = [
        DeepDiveModule(title: "Recent price movement", iconName: "chart.xyaxis.line", type: .recentPriceMovement),
        DeepDiveModule(title: "Fundamentals & Growth", iconName: "chart.bar.fill", type: .fundamentalsGrowth),
        DeepDiveModule(title: "Future Forecast", iconName: "sparkles", type: .futureForecast),
        DeepDiveModule(title: "Insider & Management", iconName: "person.2.fill", type: .insiderManagement),
        DeepDiveModule(title: "Moat & Competition", iconName: "shield.fill", type: .moatCompetition),
        DeepDiveModule(title: "Macro-Economic & Geopolitical", iconName: "globe", type: .macroGeopolitical),
        DeepDiveModule(title: "Wall Street Consensus", iconName: "building.columns.fill", type: .wallStreetConsensus)
    ]

    // MARK: - Initialization
    init(ticker: String) {
        self.ticker = ticker
        loadReport()
    }

    // MARK: - Data Loading
    func loadReport() {
        isLoading = true
        // Simulate network delay
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.reportData = TickerReportData.sampleOracle
            self?.isLoading = false
        }
    }

    func refresh() async {
        isLoading = true
        try? await Task.sleep(nanoseconds: 1_000_000_000)
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
