//
//  ETFDetailViewModel.swift
//  ios
//
//  ViewModel for the ETF Detail screen
//

import Foundation
import SwiftUI
import Combine

@MainActor
class ETFDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var etfData: ETFDetailData?
    @Published var newsArticles: [TickerNewsArticle] = []
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: ETFDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .threeMonths
    @Published var isFavorite: Bool = false
    @Published var aiInputText: String = ""

    // MARK: - Private Properties

    private let etfSymbol: String

    // MARK: - Initialization

    init(etfSymbol: String) {
        self.etfSymbol = etfSymbol
    }

    // MARK: - Public Methods

    func loadETFData() {
        isLoading = true
        errorMessage = nil

        // Simulate API call with sample data
        // In production, this would fetch from FMP API
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self = self else { return }

            self.etfData = ETFDetailData.sampleSPY
            self.newsArticles = TickerNewsArticle.sampleDataForTicker(self.etfSymbol)
            self.isLoading = false
        }
    }

    func refresh() async {
        loadETFData()
    }

    func toggleFavorite() {
        isFavorite.toggle()
    }

    func handleNotificationTap() {
        print("Notification settings for \(etfSymbol)")
    }

    func handleWebsiteTap() {
        guard let website = etfData?.etfProfile.website,
              let url = URL(string: "https://\(website)") else { return }
        UIApplication.shared.open(url)
    }

    func handleRelatedETFTap(_ ticker: RelatedTicker) {
        print("Navigate to \(ticker.symbol)")
    }

    func handleNewsArticleTap(_ article: TickerNewsArticle) {
        print("Open news article: \(article.headline)")
    }

    func handleNewsExternalLink(_ article: TickerNewsArticle) {
        guard let url = article.articleURL else { return }
        UIApplication.shared.open(url)
    }

    func handleNewsTickerTap(_ ticker: String) {
        print("Navigate to ticker: \(ticker)")
    }

    func handleSuggestionTap(_ suggestion: ETFAISuggestion) {
        aiInputText = suggestion.text
    }

    func handleAISend() {
        guard !aiInputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        print("AI Query: \(aiInputText)")
        aiInputText = ""
    }

    func updateChartRange(_ range: ChartTimeRange) {
        selectedChartRange = range
    }

    // MARK: - Computed Properties

    var formattedPrice: String {
        etfData?.formattedPrice ?? "--"
    }

    var formattedChange: String {
        etfData?.formattedChange ?? "--"
    }

    var formattedChangePercent: String {
        etfData?.formattedChangePercent ?? "--"
    }

    var isPositive: Bool {
        etfData?.isPositive ?? true
    }

    var chartData: [Double] {
        etfData?.chartData ?? []
    }

    var aiSuggestions: [ETFAISuggestion] {
        ETFAISuggestion.defaultSuggestions
    }
}
