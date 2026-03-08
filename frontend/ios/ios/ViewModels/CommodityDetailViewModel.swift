//
//  CommodityDetailViewModel.swift
//  ios
//
//  ViewModel for the Commodity Detail screen.
//  Fetches real data from GET /api/v1/commodities/{symbol}?range=3M&interval=daily
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
    @Published var chartSettings = ChartSettings()
    @Published var chartDataVersion: Int = 0

    // MARK: - Private Properties

    private let commoditySymbol: String
    private let apiClient = APIClient.shared
    private var cancellables = Set<AnyCancellable>()

    // MARK: - Initialization

    init(commoditySymbol: String) {
        self.commoditySymbol = commoditySymbol

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

    func loadCommodityData() {
        isLoading = true
        errorMessage = nil

        Task { [weak self] in
            guard let self = self else { return }

            do {
                print("🏗️ [CommodityDetail] Fetching data for \(self.commoditySymbol) range=\(self.selectedChartRange.rawValue)")

                let response = try await self.apiClient.request(
                    endpoint: .getCommodityDetail(
                        symbol: self.commoditySymbol,
                        range: self.selectedChartRange.rawValue,
                        interval: self.chartSettings.selectedInterval.rawValue
                    ),
                    responseType: CommodityDetailResponseDTO.self
                )

                print("✅ [CommodityDetail] Loaded \(response.name) — $\(response.currentPrice)")
                print("   📊 Chart points: \(response.chartData.count)")
                print("   📰 News articles: \(response.newsArticles.count)")

                self.commodityData = response.toDisplayModel()
                self.chartDataVersion += 1
                self.newsArticles = response.toNewsArticles()
                self.isLoading = false
                self.errorMessage = nil

            } catch {
                print("❌ [CommodityDetail] Failed to load \(self.commoditySymbol): \(error)")
                self.handleLoadError(error)
            }
        }
    }

    func refresh() async {
        isLoading = true
        errorMessage = nil

        do {
            let response = try await apiClient.request(
                endpoint: .getCommodityDetail(
                    symbol: commoditySymbol,
                    range: selectedChartRange.rawValue,
                    interval: chartSettings.selectedInterval.rawValue
                ),
                responseType: CommodityDetailResponseDTO.self
            )
            print("✅ [CommodityDetail] Refreshed \(response.name)")
            self.commodityData = response.toDisplayModel()
            self.chartDataVersion += 1
            self.newsArticles = response.toNewsArticles()
            self.isLoading = false
        } catch {
            print("❌ [CommodityDetail] Refresh failed: \(error)")
            handleLoadError(error)
        }
    }

    // MARK: - Chart Range Change

    func updateChartRange(_ range: ChartTimeRange) {
        selectedChartRange = range
    }

    private func fetchChartForRange() async {
        let range = selectedChartRange
        print("🏗️ [CommodityDetail] Updating chart range to \(range.rawValue)")

        do {
            let response = try await apiClient.request(
                endpoint: .getCommodityDetail(
                    symbol: commoditySymbol,
                    range: range.rawValue,
                    interval: chartSettings.selectedInterval.rawValue
                ),
                responseType: CommodityDetailResponseDTO.self
            )

            self.commodityData = response.toDisplayModel()
            self.chartDataVersion += 1
            self.newsArticles = response.toNewsArticles()
            print("✅ [CommodityDetail] Chart range updated — \(response.chartData.count) data points")
        } catch {
            print("⚠️ [CommodityDetail] Chart range update failed — \(error)")
        }
    }

    // MARK: - Error Handling

    private func handleLoadError(_ error: Error) {
        self.isLoading = false

        if let apiError = error as? APIError {
            switch apiError {
            case .networkError:
                self.errorMessage = "Unable to connect. Check your internet connection."
            case .serverError(let code):
                self.errorMessage = "Server error (\(code)). Please try again."
            case .notFound:
                self.errorMessage = "Commodity data not found for \(commoditySymbol)."
            case .decodingError:
                self.errorMessage = "Failed to parse commodity data."
            default:
                self.errorMessage = "Something went wrong. Please try again."
            }
        } else {
            self.errorMessage = "Unexpected error. Please try again."
        }

        // Fallback to sample data
        print("   🔄 Falling back to sample data for \(commoditySymbol)")
        self.commodityData = CommodityDetailData.sampleGold
        self.newsArticles = TickerNewsArticle.sampleDataForTicker(commoditySymbol)
    }

    // MARK: - User Actions

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

    var chartPricePoints: [StockPricePoint] {
        commodityData?.chartPricePoints ?? []
    }

    var aiSuggestions: [CommodityAISuggestion] {
        CommodityAISuggestion.defaultSuggestions
    }
}
