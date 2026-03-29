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
    @Published var isNewsLoading: Bool = false
    @Published var hasMoreNews: Bool = false
    @Published var fearGreedData: CryptoFearGreedData?
    @Published var isFearGreedLoading: Bool = false
    @Published var sentimentAnalysisData: SentimentAnalysisData?
    @Published var isSentimentLoading: Bool = false
    @Published var technicalAnalysisData: TechnicalAnalysisData?
    @Published var technicalAnalysisDetailData: TechnicalAnalysisDetailData?
    @Published var isTechnicalDetailLoading: Bool = false
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: CryptoDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .threeMonths
    @Published var isFavorite: Bool = false
    @Published var aiInputText: String = ""

    // Analysis tab state
    @Published var selectedFearGreedTimeframe: FearGreedTimeframe = .today
    @Published var selectedMomentumPeriod: AnalystMomentumPeriod = .sixMonths
    @Published var selectedSentimentTimeframe: SentimentTimeframe = .last24h
    @Published var chartSettings = ChartSettings()
    @Published var chartDataVersion: Int = 0

    // MARK: - Private Properties

    private let cryptoSymbol: String
    private let apiClient = APIClient.shared
    private let stockRepository: StockRepository = .shared
    private var cancellables = Set<AnyCancellable>()

    // News pagination
    private var allNewsArticles: [TickerNewsArticle] = []
    private var newsDisplayCount: Int = 10
    private let newsPageSize: Int = 10

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

                // Technical analysis: sample data for now
                self.technicalAnalysisData = TechnicalAnalysisData.sampleData

                self.isLoading = false
                self.errorMessage = nil

                // Fetch news + analysis data in parallel
                await self.fetchCryptoNews()
                await self.fetchCryptoAnalysis()

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
            self.technicalAnalysisData = TechnicalAnalysisData.sampleData
            self.isLoading = false

            // Refresh news + analysis data
            await fetchCryptoNews()
            await fetchCryptoAnalysis()
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
        self.technicalAnalysisData = TechnicalAnalysisData.sampleData
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

    func handleSentimentMore() {
        print("Sentiment analysis more options for \(cryptoSymbol)")
    }

    func handleTechnicalDetail() {
        print("Technical analysis detail for \(cryptoSymbol)")
    }

    func fetchTechnicalAnalysisDetail() {
        guard technicalAnalysisDetailData == nil, !isTechnicalDetailLoading else { return }
        isTechnicalDetailLoading = true

        Task { [weak self] in
            guard let self = self else { return }
            do {
                let dto = try await self.apiClient.request(
                    endpoint: .getTechnicalAnalysisDetail(ticker: self.cryptoSymbol),
                    responseType: TechnicalAnalysisDetailDTO.self
                )
                self.technicalAnalysisDetailData = dto.toDisplayModel()
                print("✅ [CryptoDetail] Got technical analysis detail for \(self.cryptoSymbol)")
            } catch {
                print("⚠️ [CryptoDetail] Technical analysis detail failed: \(error)")
                self.technicalAnalysisDetailData = TechnicalAnalysisDetailData.sampleData
            }
            self.isTechnicalDetailLoading = false
        }
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

    // MARK: - News (Cache-Aside + Enrichment)

    private func fetchCryptoNews() async {
        self.isNewsLoading = true
        do {
            let response = try await stockRepository.getCryptoNews(symbol: cryptoSymbol, limit: 50)
            let apiNews = response.articles
            let cached = response.cached ?? false
            print("📰 [CryptoDetail] Got \(apiNews.count) news articles for \(cryptoSymbol) (cached: \(cached))")

            self.allNewsArticles = apiNews.map { mapApiToUiArticle($0) }
            self.newsDisplayCount = newsPageSize
            self.hasMoreNews = allNewsArticles.count > newsDisplayCount

            // Show articles immediately with raw data
            self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            self.isNewsLoading = false

            // Enrich in background, then update displayed articles
            let unenrichedIds = self.newsArticles
                .filter { !$0.aiProcessed }
                .map { $0.apiId }
                .filter { !$0.isEmpty && !$0.hasPrefix("temp_") && !$0.hasPrefix("raw_") }

            if !unenrichedIds.isEmpty {
                await attemptEnrichment(symbol: cryptoSymbol, articleIds: unenrichedIds)
                self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            }
        } catch {
            print("⚠️ [CryptoDetail] Failed to fetch news for \(cryptoSymbol): \(error)")
        }
        self.isNewsLoading = false
    }

    func loadMoreNews() {
        guard hasMoreNews else { return }

        newsDisplayCount += newsPageSize
        newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
        hasMoreNews = allNewsArticles.count > newsDisplayCount

        Task {
            await enrichVisibleArticles()
        }
    }

    private func attemptEnrichment(symbol: String, articleIds: [String], maxAttempts: Int = 2) async {
        for attempt in 1...maxAttempts {
            do {
                let enrichResponse = try await stockRepository.enrichCryptoNews(
                    symbol: symbol,
                    articleIds: articleIds
                )
                mergeEnrichment(enrichResponse.articles)

                let enrichedCount = allNewsArticles.prefix(newsDisplayCount)
                    .filter { $0.aiProcessed }.count
                if enrichedCount > 0 {
                    print("✅ [CryptoDetail] Attempt \(attempt) enriched \(enrichedCount) articles")
                    return
                } else if attempt < maxAttempts {
                    print("⚠️ [CryptoDetail] Attempt \(attempt) returned 0 enriched, retrying in 3s...")
                    try await Task.sleep(nanoseconds: 3_000_000_000)
                } else {
                    print("⚠️ [CryptoDetail] Enrichment returned 0 enriched after \(maxAttempts) attempts")
                }
            } catch {
                if attempt < maxAttempts {
                    print("⚠️ [CryptoDetail] Enrichment attempt \(attempt) failed: \(error), retrying...")
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                } else {
                    print("⚠️ [CryptoDetail] Enrichment failed after \(maxAttempts) attempts: \(error)")
                }
            }
        }
    }

    private func enrichVisibleArticles() async {
        let unenriched = newsArticles.filter { !$0.aiProcessed }
        guard !unenriched.isEmpty else { return }

        let ids = unenriched.map { $0.apiId }.filter { !$0.isEmpty && !$0.hasPrefix("temp_") && !$0.hasPrefix("raw_") }
        guard !ids.isEmpty else { return }

        await attemptEnrichment(symbol: cryptoSymbol, articleIds: ids)
        newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
    }

    private func mergeEnrichment(_ enrichedArticles: [StockNewsArticle]) {
        let enrichedById = Dictionary(
            enrichedArticles.map { ($0.id, $0) },
            uniquingKeysWith: { first, _ in first }
        )

        var actuallyEnriched = 0
        for i in allNewsArticles.indices {
            if let enriched = enrichedById[allNewsArticles[i].apiId] {
                let wasProcessed = enriched.aiProcessed ?? false
                let hasBullets = enriched.summaryBullets?.isEmpty == false

                if wasProcessed || hasBullets {
                    let bullets: [String] = {
                        if let b = enriched.summaryBullets, !b.isEmpty { return b }
                        if let s = enriched.summary, !s.isEmpty { return [s] }
                        return allNewsArticles[i].summaryBullets
                    }()
                    allNewsArticles[i].summaryBullets = bullets
                    allNewsArticles[i].sentiment = mapSentiment(enriched.sentiment)
                    allNewsArticles[i].aiProcessed = true
                    actuallyEnriched += 1
                }
            }
        }
        print("📰 [CryptoDetail] Merged \(actuallyEnriched)/\(enrichedArticles.count) truly enriched articles")
    }

    // MARK: - News Helpers

    private func mapApiToUiArticle(_ article: StockNewsArticle) -> TickerNewsArticle {
        let bullets: [String] = {
            if let aiBullets = article.summaryBullets, !aiBullets.isEmpty {
                return aiBullets
            }
            if let summary = article.summary, !summary.isEmpty {
                return [summary]
            }
            return []
        }()

        return TickerNewsArticle(
            apiId: article.id,
            headline: article.title,
            source: NewsSource(name: article.source ?? "Unknown", iconName: nil),
            sentiment: mapSentiment(article.sentiment),
            publishedAt: article.publishedAt.flatMap { parseDate($0) } ?? Date(),
            thumbnailName: nil,
            imageURL: article.imageUrl.flatMap { URL(string: $0) },
            relatedTickers: article.relatedTickers ?? [],
            summaryBullets: bullets,
            articleURL: article.url.flatMap { URL(string: $0) },
            aiProcessed: article.aiProcessed ?? false
        )
    }

    private func mapSentiment(_ sentiment: String?) -> NewsSentiment {
        switch sentiment?.lowercased() {
        case "positive", "bullish": return .positive
        case "negative", "bearish": return .negative
        default: return .neutral
        }
    }

    private func parseDate(_ dateString: String) -> Date? {
        let isoFormatter = ISO8601DateFormatter()
        isoFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = isoFormatter.date(from: dateString) { return date }
        isoFormatter.formatOptions = [.withInternetDateTime]
        if let date = isoFormatter.date(from: dateString) { return date }

        let dateFormatter = DateFormatter()
        dateFormatter.locale = Locale(identifier: "en_US_POSIX")
        dateFormatter.timeZone = TimeZone(identifier: "UTC")
        dateFormatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        if let date = dateFormatter.date(from: dateString) { return date }

        dateFormatter.dateFormat = "yyyy-MM-dd"
        return dateFormatter.date(from: dateString)
    }

    // MARK: - Analysis Data (Fear & Greed + Sentiment)

    private func fetchCryptoAnalysis() async {
        self.isFearGreedLoading = true
        self.isSentimentLoading = true

        // Fetch Fear & Greed and Sentiment in parallel
        async let fearGreedTask: () = fetchFearGreed()
        async let sentimentTask: () = fetchSentiment()
        _ = await (fearGreedTask, sentimentTask)
    }

    private func fetchFearGreed() async {
        do {
            let dto = try await stockRepository.getCryptoFearGreed()
            self.fearGreedData = dto.toDisplayModel()
            print("✅ [CryptoDetail] Got Fear & Greed Index: \(dto.value) (\(dto.classification))")
        } catch {
            print("⚠️ [CryptoDetail] Fear & Greed failed: \(error)")
        }
        self.isFearGreedLoading = false
    }

    private func fetchSentiment() async {
        do {
            let dto = try await stockRepository.getCryptoSentiment(symbol: cryptoSymbol)
            self.sentimentAnalysisData = dto.toDisplayModel()
            print("✅ [CryptoDetail] Got sentiment for \(cryptoSymbol): mood \(dto.moodScore)")
        } catch {
            print("⚠️ [CryptoDetail] Sentiment failed for \(cryptoSymbol): \(error)")
        }
        self.isSentimentLoading = false
    }

    // MARK: - Contextual Chat Context

    /// News tab context — recent headlines with sentiment
    private var newsContext: String? {
        let recent = newsArticles.prefix(3)
        guard !recent.isEmpty else { return nil }
        var parts: [String] = []
        parts.append("Recent Headlines:")
        for article in recent {
            let sentiment = article.sentiment.displayName
            parts.append("- [\(sentiment)] \(article.headline)")
        }
        return parts.joined(separator: "\n")
    }

    /// Build context string for the current tab to inject into AI chat
    var contextForCurrentTab: String? {
        var sections: [String] = []

        // Base crypto context
        if let data = cryptoData {
            var base: [String] = []
            base.append("\(data.name) (\(data.symbol))")
            base.append("Price: \(data.formattedPrice) \(data.formattedChange) (\(data.formattedChangePercent))")
            if let profile = Optional(data.cryptoProfile), !profile.description.isEmpty {
                base.append("About: \(profile.description.prefix(200))")
            }
            sections.append(base.joined(separator: ". "))
        }

        switch selectedTab {
        case .overview:
            break
        case .news:
            if let ctx = newsContext { sections.append(ctx) }
        case .analysis:
            break
        }

        sections.append("User is viewing the \(selectedTab.rawValue) tab.")

        return sections.isEmpty ? nil : sections.joined(separator: "\n\n")
    }
}
