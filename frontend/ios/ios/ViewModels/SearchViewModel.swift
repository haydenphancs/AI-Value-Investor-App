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

    // MARK: - Initialization
    init() {
        loadMockData()
    }

    // MARK: - Data Loading
    func loadMockData() {
        isLoading = true

        // Simulate network delay
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.loadQuerySuggestions()
            self?.loadRecentSearches()
            self?.loadLatestNews()
            self?.loadBooks()
            self?.isLoading = false
        }
    }

    func refresh() async {
        isLoading = true
        try? await Task.sleep(nanoseconds: 800_000_000)
        loadMockData()
    }

    // MARK: - Private Loaders
    private func loadQuerySuggestions() {
        querySuggestions = SearchQuerySuggestion.sampleData
    }

    private func loadRecentSearches() {
        recentSearches = SearchResultItem.sampleData
    }

    private func loadLatestNews() {
        latestNews = SearchNewsItem.sampleData
    }

    private func loadBooks() {
        books = SearchBookItem.sampleData
    }

    // MARK: - Actions
    func performSearch() {
        guard !searchText.isEmpty else { return }
        print("Searching for: \(searchText)")
        // In a real app, this would trigger an API call
    }

    func selectSuggestion(_ suggestion: SearchQuerySuggestion) {
        searchText = suggestion.text
        performSearch()
    }

    func selectSearchResult(_ item: SearchResultItem) {
        print("Selected: \(item.name)")
        // Navigate to detail view
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
