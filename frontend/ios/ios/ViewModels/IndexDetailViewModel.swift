//
//  IndexDetailViewModel.swift
//  ios
//
//  ViewModel for the Index Detail screen
//
//  Fetches aggregated index data from GET /api/v1/indices/{symbol}.
//  Falls back to local sample data when the backend is unreachable.
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
    @Published var technicalAnalysisDetailData: TechnicalAnalysisDetailData?
    @Published var isTechnicalDetailLoading: Bool = false
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: IndexDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .threeMonths
    @Published var isFavorite: Bool = false
    @Published var aiInputText: String = ""

    // Analysis tab state
    @Published var selectedMomentumPeriod: AnalystMomentumPeriod = .sixMonths
    @Published var selectedSentimentTimeframe: SentimentTimeframe = .last24h
    @Published var chartSettings = ChartSettings()
    @Published var chartDataVersion: Int = 0

    // MARK: - Private Properties

    private let indexSymbol: String
    private var cancellables = Set<AnyCancellable>()

    // MARK: - Initialization

    init(indexSymbol: String) {
        self.indexSymbol = indexSymbol

        // Observe chart range changes and reload chart data
        $selectedChartRange
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] newRange in
                guard let self = self else { return }
                self.chartSettings.selectedInterval = newRange.defaultInterval
                Task {
                    await self.loadChartData(range: newRange)
                }
            }
            .store(in: &cancellables)

        // Observe interval changes and re-fetch chart data
        chartSettings.$selectedInterval
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] _ in
                guard let self = self else { return }
                Task {
                    await self.loadChartData(range: self.selectedChartRange)
                }
            }
            .store(in: &cancellables)
    }

    // MARK: - Public Methods

    func loadIndexData() {
        isLoading = true
        errorMessage = nil

        Task { [weak self] in
            guard let self = self else { return }
            await self.fetchIndexDetail()
        }
    }

    func refresh() async {
        errorMessage = nil
        await fetchIndexDetail()
    }

    func toggleFavorite() {
        isFavorite.toggle()
    }

    func handleNotificationTap() {
        print("🔔 [IndexDetailVM] Notification settings for \(indexSymbol)")
    }

    func handleWebsiteTap() {
        guard let website = indexData?.indexProfile.website,
              let url = URL(string: "https://\(website)") else { return }

        UIApplication.shared.open(url)
    }

    func handleNewsArticleTap(_ article: TickerNewsArticle) {
        print("📰 [IndexDetailVM] Open news article: \(article.headline)")
    }

    func handleNewsExternalLink(_ article: TickerNewsArticle) {
        guard let url = article.articleURL else { return }
        UIApplication.shared.open(url)
    }

    func handleNewsTickerTap(_ ticker: String) {
        print("🔗 [IndexDetailVM] Navigate to ticker: \(ticker)")
    }

    func handleSuggestionTap(_ suggestion: IndexAISuggestion) {
        aiInputText = suggestion.text
    }

    func handleAISend() {
        guard !aiInputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        print("🤖 [IndexDetailVM] AI Query for \(indexSymbol): \(aiInputText)")
        aiInputText = ""
    }

    func updateChartRange(_ range: ChartTimeRange) {
        selectedChartRange = range
    }

    // MARK: - Analysis Tab Handlers

    func handleAnalystRatingsMore() {
        print("📊 [IndexDetailVM] Analyst ratings more options for \(indexSymbol)")
    }

    func handleSentimentMore() {
        print("💬 [IndexDetailVM] Sentiment analysis more options for \(indexSymbol)")
    }

    func handleTechnicalDetail() {
        print("📈 [IndexDetailVM] Technical analysis detail for \(indexSymbol)")
    }

    func fetchTechnicalAnalysisDetail() {
        guard technicalAnalysisDetailData == nil, !isTechnicalDetailLoading else { return }
        isTechnicalDetailLoading = true

        Task { [weak self] in
            guard let self = self else { return }
            do {
                let dto = try await APIClient.shared.request(
                    endpoint: .getTechnicalAnalysisDetail(ticker: self.indexSymbol),
                    responseType: TechnicalAnalysisDetailDTO.self
                )
                self.technicalAnalysisDetailData = dto.toDisplayModel()
                print("✅ [IndexDetailVM] Got technical analysis detail for \(self.indexSymbol)")
            } catch {
                print("⚠️ [IndexDetailVM] Technical analysis detail failed: \(error)")
                self.technicalAnalysisDetailData = TechnicalAnalysisDetailData.sampleData
            }
            self.isTechnicalDetailLoading = false
        }
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

    var chartPricePoints: [StockPricePoint] {
        indexData?.chartPricePoints ?? []
    }

    var aiSuggestions: [IndexAISuggestion] {
        IndexAISuggestion.defaultSuggestions
    }

    // MARK: - Network

    private func fetchIndexDetail() async {
        let startTime = CFAbsoluteTimeGetCurrent()
        let range = selectedChartRange.rawValue
        let endpoint = APIEndpoint.getIndexDetail(symbol: indexSymbol, range: range, interval: chartSettings.selectedInterval.rawValue)

        print("📡 [IndexDetailVM] Fetching index detail for \(indexSymbol) (range: \(range)) from \(APIConfig.baseURL.absoluteString)\(endpoint.path) ...")

        do {
            let response = try await APIClient.shared.request(
                endpoint: endpoint,
                responseType: IndexDetailResponse.self
            )
            let elapsed = String(format: "%.2f", CFAbsoluteTimeGetCurrent() - startTime)

            // Clear previous error on success
            self.errorMessage = nil

            // Map DTOs → display models
            self.indexData = response.toDisplayModel()
            self.chartDataVersion += 1
            self.newsArticles = response.toNewsArticles()

            // Analysis data is not yet served by the backend — use sample
            self.analysisData = TickerAnalysisData.sampleData

            self.isLoading = false

            print("✅ [IndexDetailVM] Index detail loaded in \(elapsed)s")
            print("   💰 Price: \(response.currentPrice) | Change: \(response.priceChange) (\(response.priceChangePercent)%)")
            print("   📊 Chart points: \(response.chartData.count)")
            print("   📰 News: \(response.newsArticles.count) articles")
            print("   🏢 Profile: \(response.indexName) (\(response.indexProfile.numberOfConstituents) constituents)")
            if let snap = indexData?.snapshotsData {
                print("   📈 Valuation: P/E \(snap.valuation.peRatio)x | Level: \(snap.valuation.level.rawValue)")
                print("   🌍 Sectors: \(snap.sectorPerformance.sectors.count) sectors loaded")
                print("   🏛️ Macro: \(snap.macroForecast.indicators.count) indicators")
            }

        } catch {
            let elapsed = String(format: "%.2f", CFAbsoluteTimeGetCurrent() - startTime)
            print("❌ [IndexDetailVM] Fetch failed after \(elapsed)s: \(error)")
            if let apiError = error as? APIError {
                print("   🔍 API Error detail: \(apiError)")
            }

            self.errorMessage = "Unable to load index data. Pull to refresh."
            loadFallbackData()
            self.isLoading = false
        }
    }

    /// Reload only the chart data when the user changes time range.
    private func loadChartData(range: ChartTimeRange) async {
        let startTime = CFAbsoluteTimeGetCurrent()
        print("📡 [IndexDetailVM] Reloading chart for \(indexSymbol) range: \(range.rawValue)")

        do {
            let response = try await APIClient.shared.request(
                endpoint: .getIndexDetail(symbol: indexSymbol, range: range.rawValue, interval: chartSettings.selectedInterval.rawValue),
                responseType: IndexDetailResponse.self
            )

            let elapsed = String(format: "%.2f", CFAbsoluteTimeGetCurrent() - startTime)

            // Update all data — the backend returns a fresh snapshot
            self.indexData = response.toDisplayModel()
            self.chartDataVersion += 1
            if !response.newsArticles.isEmpty {
                self.newsArticles = response.toNewsArticles()
            }

            print("✅ [IndexDetailVM] Chart reloaded in \(elapsed)s — \(response.chartData.count) data points")

        } catch {
            print("❌ [IndexDetailVM] Chart reload failed: \(error)")
            // Keep existing data — don't wipe the screen on a chart range failure
        }
    }

    // MARK: - Fallback

    private func loadFallbackData() {
        if indexData == nil {
            indexData = IndexDetailData.sampleSP500
            print("🔄 [IndexDetailVM] Using fallback sample data for index")
        }
        if newsArticles.isEmpty {
            newsArticles = TickerNewsArticle.sampleDataForTicker(indexSymbol)
            print("🔄 [IndexDetailVM] Using fallback sample news")
        }
        if analysisData == nil {
            analysisData = TickerAnalysisData.sampleData
            print("🔄 [IndexDetailVM] Using fallback sample analysis")
        }
    }
}
