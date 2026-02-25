//
//  CommodityDetailViewModel.swift
//  ios
//
//  ViewModel for the Commodity Detail screen
//

import Foundation
import SwiftUI
import Combine

@MainActor
class CommodityDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var commodityData: CommodityDetailData?
    @Published var newsArticles: [TickerNewsArticle] = []
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: CommodityDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .threeMonths
    @Published var isFavorite: Bool = false
    @Published var aiInputText: String = ""

    // MARK: - Private Properties

    private let commoditySymbol: String

    // MARK: - Initialization

    init(commoditySymbol: String) {
        self.commoditySymbol = commoditySymbol
    }

    // MARK: - Public Methods

    func loadCommodityData() {
        isLoading = true
        errorMessage = nil

        // In production, this would fetch from FMP API:
        // GET /v3/quote/{symbol}?apikey=KEY
        // GET /v3/historical-price-full/{symbol}?apikey=KEY
        Task { [weak self] in
            guard let self = self else { return }

            let commodity = CommodityDetailData.sampleGold
            let news = TickerNewsArticle.sampleDataForTicker(self.commoditySymbol)

            self.commodityData = commodity
            self.newsArticles = news
            self.isLoading = false
        }
    }

    func refresh() async {
        loadCommodityData()
    }

    func toggleFavorite() {
        isFavorite.toggle()
    }

    func handleNotificationTap() {
        print("Notification settings for \(commoditySymbol)")
    }

    func handleRelatedCommodityTap(_ commodity: RelatedTicker) {
        print("Navigate to \(commodity.symbol)")
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

    func handleSuggestionTap(_ suggestion: CommodityAISuggestion) {
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
        commodityData?.formattedPrice ?? "--"
    }

    var formattedChange: String {
        commodityData?.formattedChange ?? "--"
    }

    var formattedChangePercent: String {
        commodityData?.formattedChangePercent ?? "--"
    }

    var isPositive: Bool {
        commodityData?.isPositive ?? true
    }

    var chartData: [Double] {
        commodityData?.chartData ?? []
    }

    var aiSuggestions: [CommodityAISuggestion] {
        CommodityAISuggestion.defaultSuggestions
    }
}
