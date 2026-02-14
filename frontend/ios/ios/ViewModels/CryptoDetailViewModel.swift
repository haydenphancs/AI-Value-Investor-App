//
//  CryptoDetailViewModel.swift
//  ios
//
//  ViewModel for the Crypto Detail screen
//

import Foundation
import SwiftUI
import Combine

@MainActor
class CryptoDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var cryptoData: CryptoDetailData?
    @Published var newsArticles: [TickerNewsArticle] = []
    @Published var analysisData: TickerAnalysisData?
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: CryptoDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .threeMonths
    @Published var isFavorite: Bool = false
    @Published var aiInputText: String = ""

    // Analysis tab state
    @Published var selectedMomentumPeriod: AnalystMomentumPeriod = .sixMonths
    @Published var selectedSentimentTimeframe: SentimentTimeframe = .last24h

    // MARK: - Private Properties

    private let cryptoSymbol: String

    // MARK: - Initialization

    init(cryptoSymbol: String) {
        self.cryptoSymbol = cryptoSymbol
    }

    // MARK: - Public Methods

    func loadCryptoData() {
        isLoading = true
        errorMessage = nil

        // Simulate API call with sample data
        // In production, this would fetch from FMP API
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self = self else { return }

            self.cryptoData = CryptoDetailData.sampleBitcoin
            self.newsArticles = TickerNewsArticle.sampleDataForTicker(self.cryptoSymbol)
            self.analysisData = TickerAnalysisData.sampleData
            self.isLoading = false
        }
    }

    func refresh() async {
        loadCryptoData()
    }

    func toggleFavorite() {
        isFavorite.toggle()
    }

    func handleNotificationTap() {
        print("Notification settings for \(cryptoSymbol)")
    }

    func handleWebsiteTap() {
        guard let website = cryptoData?.cryptoProfile.website,
              let url = URL(string: "https://\(website)") else { return }
        UIApplication.shared.open(url)
    }

    func handleWhitepaperTap() {
        guard let whitepaper = cryptoData?.cryptoProfile.whitepaper,
              let url = URL(string: "https://\(whitepaper)") else { return }
        UIApplication.shared.open(url)
    }

    func handleRelatedCryptoTap(_ ticker: RelatedTicker) {
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

    func handleSuggestionTap(_ suggestion: CryptoAISuggestion) {
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
        print("Analyst ratings more options for \(cryptoSymbol)")
    }

    func handleSentimentMore() {
        print("Sentiment analysis more options for \(cryptoSymbol)")
    }

    func handleTechnicalDetail() {
        print("Technical analysis detail for \(cryptoSymbol)")
    }

    // MARK: - Computed Properties

    var formattedPrice: String {
        cryptoData?.formattedPrice ?? "--"
    }

    var formattedChange: String {
        cryptoData?.formattedChange ?? "--"
    }

    var formattedChangePercent: String {
        cryptoData?.formattedChangePercent ?? "--"
    }

    var isPositive: Bool {
        cryptoData?.isPositive ?? true
    }

    var chartData: [Double] {
        cryptoData?.chartData ?? []
    }

    var aiSuggestions: [CryptoAISuggestion] {
        CryptoAISuggestion.defaultSuggestions
    }
}
