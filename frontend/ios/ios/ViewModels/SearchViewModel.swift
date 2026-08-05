//
//  SearchViewModel.swift
//  ios
//
//  ViewModel for Search screen - MVVM Architecture
//

import Foundation
import Combine

@MainActor
class SearchViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var searchText: String = ""
    @Published var querySuggestions: [SearchQuerySuggestion] = []
    @Published var recentSearches: [SearchResultItem] = []
    @Published var latestNews: [SearchNewsItem] = []
    @Published var isLoading: Bool = false
    @Published var error: String?

    // MARK: - Live search results from API
    @Published var searchResults: [StockSearchResult] = []

    // MARK: - Navigation
    @Published var selectedSearchSelection: SearchSelection?

    // MARK: - Dependencies
    private let stockRepository: StockRepository
    private let apiClient: APIClient

    // Debounce support for live search
    private var searchTask: Task<Void, Never>?

    // Track whether initial data has loaded from backend
    private var hasLoadedInitialData = false

    // Combine subscription for debounced live search
    private var cancellables = Set<AnyCancellable>()

    // MARK: - Initialization
    init(stockRepository: StockRepository? = nil, apiClient: APIClient? = nil) {
        self.stockRepository = stockRepository ?? .shared
        self.apiClient = apiClient ?? .shared

        // Starter questions shown as chips in the empty state → tapped, they seed a
        // Cay AI conversation (handled in SearchView), turning the empty search into a
        // lightweight AI discovery hub.
        querySuggestions = SearchQuerySuggestion.sampleData

        // NO placeholder news. `SearchNewsItem.sampleData` is invented content
        // ("Apple Announces Revolutionary AI Features...", "Bitcoin Reaches New
        // All-Time High Above $68K") that rendered as real, tappable headlines
        // on every open — the same fabrication class removed from UpdatesViewModel.
        // The list stays empty until real articles arrive.

        // Live debounced search as user types
        $searchText
            .debounce(for: .milliseconds(300), scheduler: RunLoop.main)
            .removeDuplicates()
            .sink { [weak self] query in
                guard let self else { return }
                let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
                if trimmed.isEmpty {
                    self.searchResults = []
                    self.recentSearches = []
                    return
                }
                self.searchTask?.cancel()
                self.searchTask = Task { [weak self] in
                    await self?.performSearchAsync()
                }
            }
            .store(in: &cancellables)
    }

    // MARK: - Initial Data Loading

    /// Called once when the view appears. Fetches real news from backend.
    func loadInitialData() async {
        guard !hasLoadedInitialData else { return }
        hasLoadedInitialData = true

        print("📰 SearchViewModel: Loading initial search screen data...")
        await fetchLatestNews()
    }

    /// Fetch the latest market news.
    ///
    /// Uses the Updates market feed, NOT `GET /api/v1/news`: that route reads the
    /// `news_articles` table, which has no writer anywhere in the backend, so it
    /// returned an empty list on every call — which is precisely why this screen
    /// fell back to invented headlines 100% of the time.
    private func fetchLatestNews() async {
        print("📰 SearchViewModel: Fetching market news from /api/v1/updates/feed ...")

        do {
            let response = try await apiClient.request(
                endpoint: .getUpdatesFeed(scope: UpdatesScope.market, limit: 5),
                responseType: UpdatesFeedResponse.self
            )
            let articles = (response.articles ?? []).map { SearchNewsItem(from: $0) }

            if articles.isEmpty {
                print("📰 SearchViewModel: 0 market articles available")
            } else {
                print("✅ SearchViewModel: Loaded \(articles.count) market news articles")
            }
            // Assign either way. An empty list is the honest answer; substituting
            // fabricated headlines is not.
            latestNews = articles
        } catch {
            latestNews = []
            print("❌ SearchViewModel: News fetch failed — \(AppError.from(error).message)")
        }
    }

    func refresh() async {
        isLoading = true
        error = nil

        print("🔄 SearchViewModel: Pull-to-refresh triggered")

        // Re-fetch news from backend
        await fetchLatestNews()

        // If there's an active search, re-run it
        if !searchText.isEmpty {
            await performSearchAsync()
        }

        isLoading = false
    }

    // MARK: - Search Actions

    /// Called when user submits search (press return / tap suggestion)
    func performSearch() {
        guard !searchText.isEmpty else { return }

        // Cancel any pending search
        searchTask?.cancel()

        searchTask = Task { [weak self] in
            await self?.performSearchAsync()
        }
    }

    /// Actual async API call for stock search
    private func performSearchAsync() async {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return }

        isLoading = true
        error = nil

        print("🔍 SearchViewModel: Searching for '\(query)' via GET /api/v1/stocks/search?q=\(query)&limit=10")

        do {
            let results = try await stockRepository.searchStocks(query: query, limit: 10)

            print("✅ SearchViewModel: Got \(results.count) results for '\(query)'")
            for (i, stock) in results.prefix(3).enumerated() {
                print("   [\(i+1)] \(stock.ticker) — \(stock.companyName) (\(stock.exchange ?? "?"))")
            }
            if results.count > 3 {
                print("   ... and \(results.count - 3) more")
            }

            // Store raw API results
            searchResults = results

            // Convert API results to SearchResultItem for the existing UI
            recentSearches = results.map { stock in
                let resultType: SearchResultType
                let subtitle: String
                switch stock.type {
                case "crypto":
                    resultType = .crypto
                    subtitle = "Crypto"
                case "etf":
                    resultType = .etf
                    subtitle = "ETF"
                case "fund":
                    resultType = .etf  // Display funds with ETF icon
                    subtitle = "Fund"
                default:
                    resultType = .stock
                    subtitle = stock.exchange ?? stock.sector ?? "Stock"
                }
                return SearchResultItem(
                    type: resultType,
                    rawType: stock.type ?? "stock",
                    ticker: stock.ticker,
                    name: stock.companyName,
                    subtitle: subtitle,
                    imageName: nil,
                    isFollowable: false,
                    isFollowing: false
                )
            }

            if results.isEmpty {
                print("⚠️ SearchViewModel: No results found for '\(query)'")
            }

            isLoading = false

        } catch {
            print("❌ SearchViewModel: Search failed — \(error)")
            self.error = "Search failed. Please try again."
            isLoading = false
        }
    }

    func selectSuggestion(_ suggestion: SearchQuerySuggestion) {
        searchText = suggestion.text
        performSearch()
    }

    func selectSearchResult(_ item: SearchResultItem) {
        guard let ticker = item.ticker, !ticker.isEmpty else {
            print("⚠️ SearchViewModel: Cannot navigate — no ticker for \(item.name)")
            return
        }
        print("➡️ SearchViewModel: Selected \(item.name) (\(ticker)) type=\(item.rawType)")
        selectedSearchSelection = SearchSelection(symbol: ticker, type: item.rawType)
    }

    // `toggleFollow(for:)` removed. It rebuilt one `recentSearches` element with `isFollowing`
    // flipped and returned — no `APIClient`, no `WhaleService`, no sign-in gate, no
    // `AppActions.reportMutationFailure`, and no durability (the array is transient). It was
    // also unreachable: every live result is constructed with `isFollowable: false`, so the
    // button never rendered. `WhaleService.toggleFollow` is the real implementation.

    func clearAllRecentSearches() {
        recentSearches.removeAll()
        searchResults.removeAll()
        searchText = ""
        print("🗑️ SearchViewModel: Cleared all search results")
    }

    /// Dismiss error after user acknowledges
    func dismissError() {
        error = nil
    }
}
