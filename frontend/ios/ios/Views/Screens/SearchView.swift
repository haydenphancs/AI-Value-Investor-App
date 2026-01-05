//
//  SearchView.swift
//  ios
//
//  Search screen combining all search-related organisms
//

import SwiftUI

struct SearchView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel = SearchViewModel()
    @State private var selectedNewsArticle: NewsArticle?

    var body: some View {
        ZStack {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main Content
            VStack(spacing: 0) {
                // Header with search bar
                SearchHeader(
                    searchText: $viewModel.searchText,
                    suggestions: viewModel.querySuggestions,
                    onBackTapped: handleBackTapped,
                    onSearchSubmit: handleSearchSubmit,
                    onSuggestionTapped: handleSuggestionTapped
                )

                // Scrollable Content
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xxl) {
                        // Recent Searches Section
                        RecentSearchesSection(
                            items: viewModel.recentSearches,
                            onClearAll: handleClearAll,
                            onItemTapped: handleSearchItemTapped,
                            onFollowTapped: handleFollowTapped
                        )

                        // Latest News Section
                        SearchLatestNewsSection(
                            items: viewModel.latestNews,
                            onItemTapped: handleNewsItemTapped,
                            onReadMore: handleNewsReadMore
                        )

                        // AI-Enabled Books Section
                        SearchBooksSection(
                            books: viewModel.books,
                            onChatWithBook: handleChatWithBook,
                            onReadKeyIdeas: handleReadKeyIdeas
                        )

                        // Bottom spacing for safe area
                        Spacer()
                            .frame(height: AppSpacing.xxxl)
                    }
                    .padding(.top, AppSpacing.md)
                }
                .refreshable {
                    await viewModel.refresh()
                }
            }

            // Loading overlay
            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .navigationBarHidden(true)
        .gesture(
            DragGesture()
                .onEnded { gesture in
                    // Swipe right to go back
                    if gesture.translation.width > 100 {
                        handleBackTapped()
                    }
                }
        )
        .fullScreenCover(item: $selectedNewsArticle) { article in
            NewsDetailView(article: article)
                .preferredColorScheme(.dark)
        }
    }

    // MARK: - Action Handlers
    private func handleBackTapped() {
        dismiss()
    }

    private func handleSearchSubmit() {
        viewModel.performSearch()
    }

    private func handleSuggestionTapped(_ suggestion: SearchQuerySuggestion) {
        viewModel.selectSuggestion(suggestion)
    }

    private func handleClearAll() {
        viewModel.clearAllRecentSearches()
    }

    private func handleSearchItemTapped(_ item: SearchResultItem) {
        viewModel.selectSearchResult(item)
    }

    private func handleFollowTapped(_ item: SearchResultItem) {
        viewModel.toggleFollow(for: item)
    }

    private func handleNewsItemTapped(_ item: SearchNewsItem) {
        // Convert SearchNewsItem to NewsArticle and show detail
        selectedNewsArticle = NewsArticle(
            headline: item.headline,
            summary: item.summary,
            source: NewsSource(name: item.source, iconName: nil),
            sentiment: .neutral,
            publishedAt: Date(),
            thumbnailName: item.imageName,
            relatedTickers: []
        )
    }

    private func handleNewsReadMore(_ item: SearchNewsItem) {
        handleNewsItemTapped(item)
    }

    private func handleChatWithBook(_ book: SearchBookItem) {
        viewModel.chatWithBook(book)
    }

    private func handleReadKeyIdeas(_ book: SearchBookItem) {
        viewModel.readKeyIdeas(book)
    }
}

// MARK: - SearchContentView (For use in NavigationStack)
struct SearchContentView: View {
    @StateObject private var viewModel = SearchViewModel()
    @State private var selectedNewsArticle: NewsArticle?
    var onDismiss: (() -> Void)?

    var body: some View {
        ZStack {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main Content
            VStack(spacing: 0) {
                // Header with search bar
                SearchHeader(
                    searchText: $viewModel.searchText,
                    suggestions: viewModel.querySuggestions,
                    onBackTapped: { onDismiss?() },
                    onSearchSubmit: { viewModel.performSearch() },
                    onSuggestionTapped: { viewModel.selectSuggestion($0) }
                )

                // Scrollable Content
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xxl) {
                        // Recent Searches Section
                        RecentSearchesSection(
                            items: viewModel.recentSearches,
                            onClearAll: { viewModel.clearAllRecentSearches() },
                            onItemTapped: { viewModel.selectSearchResult($0) },
                            onFollowTapped: { viewModel.toggleFollow(for: $0) }
                        )

                        // Latest News Section
                        SearchLatestNewsSection(
                            items: viewModel.latestNews,
                            onItemTapped: { item in
                                selectedNewsArticle = NewsArticle(
                                    headline: item.headline,
                                    summary: item.summary,
                                    source: NewsSource(name: item.source, iconName: nil),
                                    sentiment: .neutral,
                                    publishedAt: Date(),
                                    thumbnailName: item.imageName,
                                    relatedTickers: []
                                )
                            },
                            onReadMore: { item in
                                selectedNewsArticle = NewsArticle(
                                    headline: item.headline,
                                    summary: item.summary,
                                    source: NewsSource(name: item.source, iconName: nil),
                                    sentiment: .neutral,
                                    publishedAt: Date(),
                                    thumbnailName: item.imageName,
                                    relatedTickers: []
                                )
                            }
                        )

                        // AI-Enabled Books Section
                        SearchBooksSection(
                            books: viewModel.books,
                            onChatWithBook: { viewModel.chatWithBook($0) },
                            onReadKeyIdeas: { viewModel.readKeyIdeas($0) }
                        )

                        // Bottom spacing
                        Spacer()
                            .frame(height: AppSpacing.xxxl)
                    }
                    .padding(.top, AppSpacing.md)
                }
                .refreshable {
                    await viewModel.refresh()
                }
            }

            // Loading overlay
            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .gesture(
            DragGesture()
                .onEnded { gesture in
                    if gesture.translation.width > 100 {
                        onDismiss?()
                    }
                }
        )
        .fullScreenCover(item: $selectedNewsArticle) { article in
            NewsDetailView(article: article)
                .preferredColorScheme(.dark)
        }
    }
}

#Preview {
    SearchView()
        .preferredColorScheme(.dark)
}
