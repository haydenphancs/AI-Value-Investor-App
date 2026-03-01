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

    // MARK: - Dependencies
    private let stockRepository: StockRepository

    // Debounce support for live search
    private var searchTask: Task<Void, Never>?

    // MARK: - Initialization
    init(stockRepository: StockRepository? = nil) {
        self.stockRepository = stockRepository ?? StockRepository()
        loadInitialData()
    }

    // MARK: - Data Loading
    private func loadInitialData() {
        querySuggestions = SearchQuerySuggestion.sampleData
        recentSearches = SearchResultItem.sampleData
        latestNews = SearchNewsItem.sampleData
        books = SearchBookItem.sampleData
    }

    func refresh() async {
        isLoading = true
        loadInitialData()
        // If there's an active search, re-run it
        if !searchText.isEmpty {
            await performSearchAsync()
        }
        isLoading = false
    }

    // MARK: - Actions

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

        print("🔍 SearchViewModel: Searching for '\(query)' via API...")

        do {
            let results = try await stockRepository.searchStocks(query: query, limit: 10)

            print("🔍 SearchViewModel: Got \(results.count) results for '\(query)'")

            // Store raw API results
            searchResults = results

            // Convert API results to SearchResultItem for the existing UI
            recentSearches = results.map { stock in
                SearchResultItem(
                    type: .stock,
                    ticker: stock.ticker,
                    name: stock.companyName,
                    subtitle: stock.exchange ?? stock.sector ?? "Stock",
                    imageName: nil,
                    isFollowable: false,
                    isFollowing: false
                )
            }

            isLoading = false

        } catch {
            print("❌ SearchViewModel: Search failed — \(error)")
            self.error = error.localizedDescription
            isLoading = false
        }
    }

    func selectSuggestion(_ suggestion: SearchQuerySuggestion) {
        searchText = suggestion.text
        performSearch()
    }

    func selectSearchResult(_ item: SearchResultItem) {
        print("Selected: \(item.name) (\(item.ticker ?? ""))")
        // Navigation to detail view is handled by the View layer
    }

    func toggleFollow(for item: SearchResultItem) {
        if let index = recentSearches.firstIndex(where: { $0.id == item.id }) {
            let updatedItem = SearchResultItem(
                type: item.type,
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
    }

    func openNewsItem(_ item: SearchNewsItem) {
        print("Opening news: \(item.headline)")
    }

    func openBook(_ book: SearchBookItem) {
        print("Opening book: \(book.title)")
    }

    func chatWithBook(_ book: SearchBookItem) {
        print("Chat with book: \(book.title)")
    }

    func readKeyIdeas(_ book: SearchBookItem) {
        print("Read key ideas: \(book.title)")
    }
}
