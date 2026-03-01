//
//  ETFDetailViewModel.swift
//  ios
//
//  ViewModel for the ETF Detail screen.
//  Fetches real data from GET /api/v1/etfs/{symbol}?range=3M
//  and maps the response DTO to display models.
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

    let etfSymbol: String

    // MARK: - Initialization

    init(etfSymbol: String) {
        self.etfSymbol = etfSymbol
    }

    // MARK: - Data Loading

    func loadETFData() {
        isLoading = true
        errorMessage = nil

        Task { [weak self] in
            guard let self = self else { return }
            await self.fetchETFDetail()
        }
    }

    func refresh() async {
        await fetchETFDetail()
    }

    /// Fetches ETF detail from the backend and maps to display models.
    private func fetchETFDetail() async {
        let startTime = CFAbsoluteTimeGetCurrent()

        do {
            print("[ETFDetailVM] Fetching ETF detail for \(etfSymbol), range: \(selectedChartRange.rawValue)")

            let response = try await APIClient.shared.request(
                endpoint: .getETFDetail(
                    symbol: etfSymbol,
                    range: selectedChartRange.rawValue
                ),
                responseType: ETFDetailResponseDTO.self
            )

            let elapsed = CFAbsoluteTimeGetCurrent() - startTime
            print("[ETFDetailVM] ✅ ETF detail loaded in \(String(format: "%.2f", elapsed))s — \(response.symbol) @ $\(response.currentPrice)")

            self.errorMessage = nil
            self.etfData = response.toDisplayModel()
            self.newsArticles = response.toNewsArticles()
            self.isLoading = false

        } catch {
            let elapsed = CFAbsoluteTimeGetCurrent() - startTime
            print("[ETFDetailVM] ❌ ETF detail failed after \(String(format: "%.2f", elapsed))s — \(error)")

            if let apiError = error as? APIError {
                print("[ETFDetailVM] API Error detail: \(apiError)")
            }

            self.errorMessage = "Unable to load ETF data. Pull to refresh."
            self.isLoading = false

            // Load fallback data so the screen isn't empty
            loadFallbackData()
        }
    }

    /// Reload chart data when user changes the time range.
    func updateChartRange(_ range: ChartTimeRange) {
        guard range != selectedChartRange else { return }
        selectedChartRange = range

        Task { [weak self] in
            guard let self = self else { return }

            print("[ETFDetailVM] Updating chart range to \(range.rawValue)")

            do {
                let response = try await APIClient.shared.request(
                    endpoint: .getETFDetail(
                        symbol: self.etfSymbol,
                        range: range.rawValue
                    ),
                    responseType: ETFDetailResponseDTO.self
                )

                print("[ETFDetailVM] ✅ Chart range updated — \(response.chartData.count) data points")

                let updated = response.toDisplayModel()
                self.etfData = updated
                self.newsArticles = response.toNewsArticles()

            } catch {
                print("[ETFDetailVM] ⚠️ Chart range update failed, keeping existing data — \(error)")
                // Keep existing data on failure
            }
        }
    }

    // MARK: - Fallback Data

    private func loadFallbackData() {
        print("[ETFDetailVM] Loading fallback sample data")
        self.etfData = ETFDetailData.sampleSPY
        self.newsArticles = TickerNewsArticle.sampleDataForTicker(etfSymbol)
    }

    // MARK: - User Actions

    func toggleFavorite() {
        isFavorite.toggle()
        print("[ETFDetailVM] Favorite toggled: \(isFavorite) for \(etfSymbol)")
    }

    func handleNotificationTap() {
        print("[ETFDetailVM] Notification settings for \(etfSymbol)")
    }

    func handleWebsiteTap() {
        guard let website = etfData?.etfProfile.website,
              !website.isEmpty,
              let url = URL(string: "https://\(website)") else { return }
        UIApplication.shared.open(url)
    }

    func handleRelatedETFTap(_ ticker: RelatedTicker) {
        print("[ETFDetailVM] Navigate to related ETF: \(ticker.symbol)")
    }

    func handleNewsArticleTap(_ article: TickerNewsArticle) {
        print("[ETFDetailVM] Open news article: \(article.headline)")
    }

    func handleNewsExternalLink(_ article: TickerNewsArticle) {
        guard let url = article.articleURL else { return }
        UIApplication.shared.open(url)
    }

    func handleNewsTickerTap(_ ticker: String) {
        print("[ETFDetailVM] Navigate to ticker: \(ticker)")
    }

    func handleSuggestionTap(_ suggestion: ETFAISuggestion) {
        aiInputText = suggestion.text
    }

    func handleAISend() {
        guard !aiInputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        print("[ETFDetailVM] AI Query: \(aiInputText)")
        aiInputText = ""
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
