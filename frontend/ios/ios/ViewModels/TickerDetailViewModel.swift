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

    // MARK: - API Data (live from backend)
    @Published var stockDetail: StockDetail?
    @Published var stockQuote: StockQuote?

    // MARK: - Private Properties

    private let tickerSymbol: String
    private let stockRepository: StockRepository

    // MARK: - Initialization

    init(tickerSymbol: String, stockRepository: StockRepository? = nil) {
        self.tickerSymbol = tickerSymbol
        self.stockRepository = stockRepository ?? StockRepository()
    }

    // MARK: - Public Methods

    func loadTickerData() {
        isLoading = true
        errorMessage = nil

        Task { [weak self] in
            guard let self = self else { return }

            let ticker = self.tickerSymbol
            print("📊 TickerDetailVM: Loading data for \(ticker) from API...")

            // Fetch API data in parallel, then fall back to mock for sections not served by API
            await withTaskGroup(of: Void.self) { group in
                group.addTask { await self.fetchStockDetail(ticker) }
                group.addTask { await self.fetchStockQuote(ticker) }
                group.addTask { await self.fetchStockNews(ticker) }
            }

            // Load sample data for rich sections the backend doesn't serve yet
            self.tickerData = TickerDetailData.sampleApple
            self.analysisData = TickerAnalysisData.sampleData
            self.earningsData = EarningsData.sampleData
            self.growthData = GrowthSectionData.sampleData
            self.profitPowerData = ProfitPowerSectionData.sampleData
            self.signalOfConfidenceData = SignalOfConfidenceSectionData.sampleData
            self.revenueBreakdownData = RevenueBreakdownData.sampleApple
            self.healthCheckData = HealthCheckSectionData.sampleData
            self.holdersData = HoldersData.sampleData

            // If we don't have API news, load sample news
            if self.newsArticles.isEmpty {
                self.newsArticles = TickerNewsArticle.sampleDataForTicker(ticker)
            }

            self.isLoading = false
        }
    }

    // MARK: - API Fetching

    private func fetchStockDetail(_ ticker: String) async {
        do {
            let detail = try await stockRepository.getStock(ticker: ticker)
            self.stockDetail = detail
            print("✅ TickerDetailVM: Got stock detail for \(ticker) — price: \(detail.price ?? 0)")
        } catch {
            print("⚠️ TickerDetailVM: Failed to fetch stock detail for \(ticker): \(error)")
            // Non-fatal: we'll use sample data
        }
    }

    private func fetchStockQuote(_ ticker: String) async {
        do {
            let quote = try await stockRepository.getStockQuote(ticker: ticker)
            self.stockQuote = quote
            print("✅ TickerDetailVM: Got quote for \(ticker) — price: \(quote.price ?? 0)")
        } catch {
            print("⚠️ TickerDetailVM: Failed to fetch quote for \(ticker): \(error)")
        }
    }

    private func fetchStockNews(_ ticker: String) async {
        do {
            let apiNews = try await stockRepository.getStockNews(ticker: ticker, limit: 10)
            print("✅ TickerDetailVM: Got \(apiNews.count) news articles for \(ticker)")
            // Convert API news to UI news model
            self.newsArticles = apiNews.map { article in
                TickerNewsArticle(
                    headline: article.title,
                    source: NewsSource(name: article.source ?? "Unknown", iconName: nil),
                    sentiment: mapSentiment(article.sentiment),
                    publishedAt: article.publishedAt.flatMap { parseDate($0) } ?? Date(),
                    thumbnailName: nil,
                    relatedTickers: article.relatedTickers ?? [],
                    summaryBullets: article.summary != nil ? [article.summary!] : [],
                    articleURL: article.url.flatMap { URL(string: $0) }
                )
            }
        } catch {
            print("⚠️ TickerDetailVM: Failed to fetch news for \(ticker): \(error)")
        }
    }

    private func mapSentiment(_ sentiment: String?) -> NewsSentiment {
        switch sentiment?.lowercased() {
        case "positive", "bullish": return .positive
        case "negative", "bearish": return .negative
        default: return .neutral
        }
    }

    private func parseDate(_ dateString: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: dateString) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: dateString)
    }

    func refresh() async {
        loadTickerData()
    }

    func toggleFavorite() {
        isFavorite.toggle()
    }

    func handleNotificationTap() {
        print("Notification settings for \(tickerSymbol)")
    }

    func handleMoreOptions() {
        print("More options for \(tickerSymbol)")
    }

    func handleDeepResearch() {
        print("AI Deep Research for \(tickerSymbol)")
    }

    func handleWebsiteTap() {
        // Prefer API data for website
        let website = stockDetail?.website ?? tickerData?.companyProfile.website
        guard let site = website,
              let url = URL(string: site.hasPrefix("http") ? site : "https://\(site)") else { return }
        UIApplication.shared.open(url)
    }

    func handleRelatedTickerTap(_ ticker: RelatedTicker) {
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

    func handleSuggestionTap(_ suggestion: TickerAISuggestion) {
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
        print("Analyst ratings more options for \(tickerSymbol)")
    }

    func handleSentimentMore() {
        print("Sentiment analysis more options for \(tickerSymbol)")
    }

    func handleTechnicalDetail() {
        print("Technical analysis detail for \(tickerSymbol)")
    }

    // MARK: - Financials Tab Handlers

    func handleEarningsDetail() {
        print("Earnings detail for \(tickerSymbol)")
    }

    func handleGrowthDetail() {
        print("Growth detail for \(tickerSymbol)")
    }

    func handleProfitPowerDetail() {
        print("Profit power detail for \(tickerSymbol)")
    }

    func handleSignalOfConfidenceDetail() {
        print("Signal of confidence detail for \(tickerSymbol)")
    }

    func handleRevenueBreakdownDetail() {
        print("Revenue breakdown detail for \(tickerSymbol)")
    }

    func handleHealthCheckDetail() {
        print("Health check detail for \(tickerSymbol)")
    }

    // MARK: - Computed Properties (prefer live API data over sample data)

    var formattedPrice: String {
        if let price = stockQuote?.price ?? stockDetail?.price {
            return String(format: "$%.2f", price)
        }
        return tickerData?.formattedPrice ?? "--"
    }

    var formattedChange: String {
        if let change = stockQuote?.change ?? stockDetail?.change {
            let sign = change >= 0 ? "+" : ""
            return "\(sign)\(String(format: "%.2f", change))"
        }
        return tickerData?.formattedChange ?? "--"
    }

    var formattedChangePercent: String {
        if let percent = stockQuote?.changePercent {
            let sign = percent >= 0 ? "+" : ""
            return "(\(sign)\(String(format: "%.2f", percent))%)"
        }
        return tickerData?.formattedChangePercent ?? "--"
    }

    var isPositive: Bool {
        if let change = stockQuote?.change ?? stockDetail?.change {
            return change >= 0
        }
        return tickerData?.isPositive ?? true
    }

    var chartData: [Double] {
        tickerData?.chartData ?? []
    }

    var aiSuggestions: [TickerAISuggestion] {
        TickerAISuggestion.defaultSuggestions
    }
}
