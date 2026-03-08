//
//  CryptoDetailViewModel.swift
//  ios
//
//  ViewModel for the Crypto Detail screen
//  Fetches real data from FastAPI backend → FMP + Gemini AI
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
    @Published var chartSettings = ChartSettings()
    @Published var chartDataVersion: Int = 0

    // MARK: - Private Properties

    private let cryptoSymbol: String
    private let apiClient = APIClient.shared
    private var cancellables = Set<AnyCancellable>()

    // MARK: - Initialization

    init(cryptoSymbol: String) {
        self.cryptoSymbol = cryptoSymbol

        $selectedChartRange
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] range in
                guard let self = self else { return }
                self.chartSettings.selectedInterval = range.defaultInterval
                Task { await self.fetchChartForRange() }
            }
            .store(in: &cancellables)

        // Observe interval changes and re-fetch chart data
        chartSettings.$selectedInterval
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] _ in
                guard let self = self else { return }
                Task { await self.fetchChartForRange() }
            }
            .store(in: &cancellables)
    }

    // MARK: - Data Loading

    func loadCryptoData() {
        isLoading = true
        errorMessage = nil

        Task { [weak self] in
            guard let self = self else { return }

            do {
                print("🪙 [CryptoDetail] Fetching data for \(self.cryptoSymbol) range=\(self.selectedChartRange.rawValue)")

                let response = try await self.apiClient.request(
                    endpoint: .getCryptoDetail(
                        symbol: self.cryptoSymbol,
                        range: self.selectedChartRange.rawValue,
                        interval: self.chartSettings.selectedInterval.rawValue
                    ),
                    responseType: CryptoDetailResponse.self
                )

                print("✅ [CryptoDetail] Loaded \(response.name) — $\(response.currentPrice)")
                print("   📊 Chart points: \(response.chartData.count)")
                print("   📰 News articles: \(response.newsArticles.count)")
                print("   🔗 Related cryptos: \(response.relatedCryptos.count)")
                print("   📸 Snapshots: \(response.snapshots.count)")

                // Map API response → UI models
                self.cryptoData = response.toModel()
                self.chartDataVersion += 1
                self.newsArticles = response.newsArticles.map { $0.toModel() }

                // Analysis tab: use sample data for now (will be added to backend later)
                self.analysisData = TickerAnalysisData.sampleData

                self.isLoading = false
                self.errorMessage = nil

            } catch {
                print("❌ [CryptoDetail] Failed to load \(self.cryptoSymbol): \(error)")
                self.handleLoadError(error)
            }
        }
    }

    func refresh() async {
        isLoading = true
        errorMessage = nil

        do {
            print("🪙 [CryptoDetail] Refreshing \(cryptoSymbol)...")
            let response = try await apiClient.request(
                endpoint: .getCryptoDetail(
                    symbol: cryptoSymbol,
                    range: selectedChartRange.rawValue,
                    interval: chartSettings.selectedInterval.rawValue
                ),
                responseType: CryptoDetailResponse.self
            )
            print("✅ [CryptoDetail] Refreshed \(response.name)")
            self.cryptoData = response.toModel()
            self.chartDataVersion += 1
            self.newsArticles = response.newsArticles.map { $0.toModel() }
            self.analysisData = TickerAnalysisData.sampleData
            self.isLoading = false
        } catch {
            print("❌ [CryptoDetail] Refresh failed: \(error)")
            handleLoadError(error)
        }
    }

    // MARK: - Chart Range Change

    func updateChartRange(_ range: ChartTimeRange) {
        selectedChartRange = range
    }

    /// Called by Combine observer when selectedChartRange changes.
    private func fetchChartForRange() async {
        let range = selectedChartRange
        print("🪙 [CryptoDetail] Updating chart range to \(range.rawValue)")

        do {
            let response = try await apiClient.request(
                endpoint: .getCryptoDetail(
                    symbol: cryptoSymbol,
                    range: range.rawValue,
                    interval: chartSettings.selectedInterval.rawValue
                ),
                responseType: CryptoDetailResponse.self
            )

            self.cryptoData = response.toModel()
            self.chartDataVersion += 1
            self.newsArticles = response.newsArticles.map { $0.toModel() }
            print("✅ [CryptoDetail] Chart range updated — \(response.chartData.count) data points")
        } catch {
            print("⚠️ [CryptoDetail] Chart range update failed — \(error)")
        }
    }

    // MARK: - Error Handling

    private func handleLoadError(_ error: Error) {
        self.isLoading = false

        if let apiError = error as? APIError {
            switch apiError {
            case .networkError:
                self.errorMessage = "Unable to connect. Check your internet connection."
                print("   🌐 Network error — is the backend running at 127.0.0.1:8000?")
            case .serverError(let code):
                self.errorMessage = "Server error (\(code)). Please try again."
                print("   🖥️ Server returned HTTP \(code)")
            case .notFound:
                self.errorMessage = "Crypto data not found for \(cryptoSymbol)."
                print("   🔍 404 — symbol may not be supported")
            case .decodingError(let decodingError):
                self.errorMessage = "Failed to parse crypto data."
                print("   🧩 Decoding error: \(decodingError)")
            default:
                self.errorMessage = "Something went wrong. Please try again."
                print("   ⚠️ API error: \(apiError)")
            }
        } else {
            self.errorMessage = "Unexpected error. Please try again."
            print("   ⚠️ Unexpected: \(error.localizedDescription)")
        }

        // Fallback to sample data so the screen isn't completely empty
        print("   🔄 Falling back to sample data for \(cryptoSymbol)")
        self.cryptoData = CryptoDetailData.sampleEthereum
        self.newsArticles = TickerNewsArticle.sampleDataForTicker(cryptoSymbol)
        self.analysisData = TickerAnalysisData.sampleData
    }

    // MARK: - User Actions

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

    var chartPricePoints: [StockPricePoint] {
        cryptoData?.chartPricePoints ?? []
    }

    var aiSuggestions: [CryptoAISuggestion] {
        CryptoAISuggestion.defaultSuggestions
    }
}
