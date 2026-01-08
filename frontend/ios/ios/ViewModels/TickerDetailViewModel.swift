//
//  TickerDetailViewModel.swift
//  ios
//
//  ViewModel for the Ticker Detail screen
//

import Foundation
import SwiftUI

@MainActor
class TickerDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var tickerData: TickerDetailData?
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: TickerDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .threeMonths
    @Published var isFavorite: Bool = false
    @Published var aiInputText: String = ""

    // MARK: - Private Properties

    private let tickerSymbol: String

    // MARK: - Initialization

    init(tickerSymbol: String) {
        self.tickerSymbol = tickerSymbol
    }

    // MARK: - Public Methods

    func loadTickerData() {
        isLoading = true
        errorMessage = nil

        // Simulate API call with sample data
        // In production, this would fetch from your backend
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self = self else { return }

            // For demo, use sample data
            self.tickerData = TickerDetailData.sampleApple
            self.isLoading = false
        }
    }

    func refresh() async {
        loadTickerData()
    }

    func toggleFavorite() {
        isFavorite.toggle()
        // TODO: Persist favorite state to backend/local storage
    }

    func handleNotificationTap() {
        // TODO: Navigate to notification settings for this ticker
        print("Notification settings for \(tickerSymbol)")
    }

    func handleMoreOptions() {
        // TODO: Show more options action sheet
        print("More options for \(tickerSymbol)")
    }

    func handleDeepResearch() {
        // TODO: Navigate to AI Deep Research view
        print("AI Deep Research for \(tickerSymbol)")
    }

    func handleWebsiteTap() {
        guard let website = tickerData?.companyProfile.website,
              let url = URL(string: "https://\(website)") else { return }

        UIApplication.shared.open(url)
    }

    func handleRelatedTickerTap(_ ticker: RelatedTicker) {
        // TODO: Navigate to related ticker detail
        print("Navigate to \(ticker.symbol)")
    }

    func handleSuggestionTap(_ suggestion: TickerAISuggestion) {
        aiInputText = suggestion.text
    }

    func handleAISend() {
        guard !aiInputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        // TODO: Send AI query
        print("AI Query: \(aiInputText)")
        aiInputText = ""
    }

    func updateChartRange(_ range: ChartTimeRange) {
        selectedChartRange = range
        // TODO: Fetch new chart data for selected range
    }

    // MARK: - Computed Properties

    var formattedPrice: String {
        tickerData?.formattedPrice ?? "--"
    }

    var formattedChange: String {
        tickerData?.formattedChange ?? "--"
    }

    var formattedChangePercent: String {
        tickerData?.formattedChangePercent ?? "--"
    }

    var isPositive: Bool {
        tickerData?.isPositive ?? true
    }

    var chartData: [Double] {
        tickerData?.chartData ?? []
    }

    var aiSuggestions: [TickerAISuggestion] {
        TickerAISuggestion.defaultSuggestions
    }
}
