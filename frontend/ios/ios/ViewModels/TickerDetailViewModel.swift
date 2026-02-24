//
//  TickerDetailViewModel.swift
//  ios
//
//  ViewModel for the Ticker Detail screen
//

import Foundation
import SwiftUI
import Combine

@MainActor
class TickerDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var tickerData: TickerDetailData?
    @Published var newsArticles: [TickerNewsArticle] = []
    @Published var analysisData: TickerAnalysisData?
    @Published var earningsData: EarningsData?
    @Published var growthData: GrowthSectionData?
    @Published var profitPowerData: ProfitPowerSectionData?
    @Published var signalOfConfidenceData: SignalOfConfidenceSectionData?
    @Published var revenueBreakdownData: RevenueBreakdownData?
    @Published var healthCheckData: HealthCheckSectionData?
    @Published var holdersData: HoldersData?
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: TickerDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .threeMonths
    @Published var isFavorite: Bool = false
    @Published var aiInputText: String = ""

    // Analysis tab state
    @Published var selectedMomentumPeriod: AnalystMomentumPeriod = .sixMonths
    @Published var selectedSentimentTimeframe: SentimentTimeframe = .last24h

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

        // In production, this would fetch from your backend
        Task { [weak self] in
            guard let self = self else { return }

            let ticker = self.tickerSymbol
            let tickerData = TickerDetailData.sampleApple
            let news = TickerNewsArticle.sampleDataForTicker(ticker)
            let analysis = TickerAnalysisData.sampleData
            let earnings = EarningsData.sampleData
            let growth = GrowthSectionData.sampleData
            let profitPower = ProfitPowerSectionData.sampleData
            let signalOfConfidence = SignalOfConfidenceSectionData.sampleData
            let revenueBreakdown = RevenueBreakdownData.sampleApple
            let healthCheck = HealthCheckSectionData.sampleData
            let holders = HoldersData.sampleData

            self.tickerData = tickerData
            self.newsArticles = news
            self.analysisData = analysis
            self.earningsData = earnings
            self.growthData = growth
            self.profitPowerData = profitPower
            self.signalOfConfidenceData = signalOfConfidence
            self.revenueBreakdownData = revenueBreakdown
            self.healthCheckData = healthCheck
            self.holdersData = holders
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

    func handleNewsArticleTap(_ article: TickerNewsArticle) {
        // TODO: Navigate to full news detail view
        print("Open news article: \(article.headline)")
    }

    func handleNewsExternalLink(_ article: TickerNewsArticle) {
        guard let url = article.articleURL else { return }
        UIApplication.shared.open(url)
    }

    func handleNewsTickerTap(_ ticker: String) {
        // TODO: Navigate to ticker detail for the related ticker
        print("Navigate to ticker: \(ticker)")
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

    // MARK: - Analysis Tab Handlers

    func handleAnalystRatingsMore() {
        // TODO: Show more analyst ratings options
        print("Analyst ratings more options for \(tickerSymbol)")
    }

    func handleSentimentMore() {
        // TODO: Show more sentiment analysis options
        print("Sentiment analysis more options for \(tickerSymbol)")
    }

    func handleTechnicalDetail() {
        // TODO: Navigate to detailed technical analysis view
        print("Technical analysis detail for \(tickerSymbol)")
    }

    // MARK: - Financials Tab Handlers

    func handleEarningsDetail() {
        // TODO: Navigate to detailed earnings view
        print("Earnings detail for \(tickerSymbol)")
    }

    func handleGrowthDetail() {
        // TODO: Navigate to detailed growth view
        print("Growth detail for \(tickerSymbol)")
    }

    func handleProfitPowerDetail() {
        // TODO: Navigate to detailed profit power view
        print("Profit power detail for \(tickerSymbol)")
    }

    func handleSignalOfConfidenceDetail() {
        // TODO: Navigate to detailed signal of confidence view
        print("Signal of confidence detail for \(tickerSymbol)")
    }

    func handleRevenueBreakdownDetail() {
        // TODO: Navigate to detailed revenue breakdown view
        print("Revenue breakdown detail for \(tickerSymbol)")
    }

    func handleHealthCheckDetail() {
        // TODO: Navigate to detailed health check view
        print("Health check detail for \(tickerSymbol)")
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
