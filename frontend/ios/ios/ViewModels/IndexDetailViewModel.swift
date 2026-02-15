//
//  IndexDetailViewModel.swift
//  ios
//
//  ViewModel for the Index Detail screen
//

import Foundation
import SwiftUI
import Combine

@MainActor
class IndexDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var indexData: IndexDetailData?
    @Published var newsArticles: [TickerNewsArticle] = []
    @Published var analysisData: TickerAnalysisData?
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: IndexDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .threeMonths
    @Published var isFavorite: Bool = false
    @Published var aiInputText: String = ""

    // Analysis tab state
    @Published var selectedMomentumPeriod: AnalystMomentumPeriod = .sixMonths
    @Published var selectedSentimentTimeframe: SentimentTimeframe = .last24h

    // MARK: - Private Properties

    private let indexSymbol: String

    // MARK: - Initialization

    init(indexSymbol: String) {
        self.indexSymbol = indexSymbol
    }

    // MARK: - Public Methods

    func loadIndexData() {
        isLoading = true
        errorMessage = nil

        // Simulate API call with sample data
        // In production, this would fetch from FMP API
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self = self else { return }

            self.indexData = IndexDetailData.sampleSP500
            self.newsArticles = TickerNewsArticle.sampleDataForTicker(self.indexSymbol)
            self.analysisData = TickerAnalysisData.sampleData
            self.isLoading = false
        }
    }

    func refresh() async {
        loadIndexData()
    }

    func toggleFavorite() {
        isFavorite.toggle()
    }

    func handleNotificationTap() {
        print("Notification settings for \(indexSymbol)")
    }

    func handleWebsiteTap() {
        guard let website = indexData?.indexProfile.website,
              let url = URL(string: "https://\(website)") else { return }

        UIApplication.shared.open(url)
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

    func handleSuggestionTap(_ suggestion: IndexAISuggestion) {
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

    // MARK: - Analysis Tab Handlers

    func handleAnalystRatingsMore() {
        print("Analyst ratings more options for \(indexSymbol)")
    }

    func handleSentimentMore() {
        print("Sentiment analysis more options for \(indexSymbol)")
    }

    func handleTechnicalDetail() {
        print("Technical analysis detail for \(indexSymbol)")
    }

    // MARK: - Computed Properties

    var formattedPrice: String {
        indexData?.formattedPrice ?? "--"
    }

    var formattedChange: String {
        indexData?.formattedChange ?? "--"
    }

    var formattedChangePercent: String {
        indexData?.formattedChangePercent ?? "--"
    }

    var isPositive: Bool {
        indexData?.isPositive ?? true
    }

    var chartData: [Double] {
        indexData?.chartData ?? []
    }

    var aiSuggestions: [IndexAISuggestion] {
        IndexAISuggestion.defaultSuggestions
    }
}
