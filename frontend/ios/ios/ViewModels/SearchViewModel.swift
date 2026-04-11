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
    @Published var books: [SearchBookItem] = []
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

        // Load static data immediately (suggestions, books stay local for MVP)
        querySuggestions = SearchQuerySuggestion.sampleData
        books = SearchBookItem.sampleData

        // Show sample news as placeholder until backend data arrives
        latestNews = SearchNewsItem.sampleData

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

    /// Fetch latest news articles from GET /api/v1/news
    private func fetchLatestNews() async {
        print("📰 SearchViewModel: Fetching news from /api/v1/news?page=1&per_page=5 ...")

        do {
            let response = try await apiClient.request(
                endpoint: .getNewsFeed(page: 1, perPage: 5),
                responseType: SearchNewsFeedResponse.self
            )

            let articles = response.articles.map { SearchNewsItem(from: $0) }

            if articles.isEmpty {
                print("📰 SearchViewModel: Backend returned 0 news articles — keeping sample data")
            } else {
                print("✅ SearchViewModel: Loaded \(articles.count) news articles from backend")
                latestNews = articles
            }

        } catch {
            // Keep sample data as fallback; news is non-critical
            print("❌ SearchViewModel: News fetch failed — \(error). Keeping sample data.")
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

    func toggleFollow(for item: SearchResultItem) {
        if let index = recentSearches.firstIndex(where: { $0.id == item.id }) {
            let updatedItem = SearchResultItem(
                type: item.type,
                rawType: item.rawType,
                ticker: item.ticker,
                name: item.name,
                subtitle: item.subtitle,
                imageName: item.imageName,
                isFollowable: item.isFollowable,
                isFollowing: !item.isFollowing
            )
            recentSearches[index] = updatedItem
        }
    }

    func clearAllRecentSearches() {
        recentSearches.removeAll()
        searchResults.removeAll()
        searchText = ""
        print("🗑️ SearchViewModel: Cleared all search results")
    }

    func openNewsItem(_ item: SearchNewsItem) {
        print("📰 SearchViewModel: Opening news — \(item.headline)")
    }

    func openBook(_ book: SearchBookItem) {
        print("📚 SearchViewModel: Opening book — \(book.title)")
    }

    func chatWithBook(_ book: SearchBookItem) {
        print("💬 SearchViewModel: Chat with book — \(book.title)")
    }

    func readKeyIdeas(_ book: SearchBookItem) {
        print("💡 SearchViewModel: Read key ideas — \(book.title)")
    }

    /// Dismiss error after user acknowledges
    func dismissError() {
        error = nil
    }
}
