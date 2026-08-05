//
//  SearchView.swift
//  ios
//
//  Universal search screen: ticker/company lookup + "Ask Cay AI".
//  Opened from the Home, Updates, and Wiser top search bars.
//

import SwiftUI

struct SearchView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel = SearchViewModel()
    /// Caller-owned chat VM (the established pattern — see TickerDetailView). A fresh
    /// SearchView is created per present, so a fresh conversation per open is correct.
    @StateObject private var chatViewModel = ChatViewModel()
    @State private var showAIChat = false
    @State private var selectedNewsArticle: NewsArticle?

    /// The current query with surrounding whitespace stripped. Drives the "Ask Cay AI"
    /// row's visibility so an empty / spaces-only field never seeds a chat.
    private var trimmedQuery: String {
        viewModel.searchText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

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
                        // Error banner (if any)
                        if let error = viewModel.error {
                            errorBanner(message: error)
                        }

                        // Ask Cay AI — the primary action whenever the user has typed
                        // something. Routes the query to the real chat (not ticker search).
                        if !trimmedQuery.isEmpty {
                            askCayAIRow(query: trimmedQuery)
                        }

                        // Recent Searches Section (live ticker/company results)
                        RecentSearchesSection(
                            items: viewModel.recentSearches,
                            onClearAll: handleClearAll,
                            // `onFollowTapped` deliberately not wired. Search results are all
                            // built with `isFollowable: false`, so `SearchResultRow` never
                            // renders the button — and the handler it used to point at only
                            // flipped a flag in the transient `recentSearches` array: no
                            // backend call, no sign-in gate, no failure reporting. Following
                            // is account-scoped and `.signInRequired` on both sides
                            // (`WhaleService.toggleFollow` is the real implementation), so
                            // wiring the old one up would have broken three auth rules at
                            // once. Making search return followable results is a product
                            // decision, not a fix.
                            onItemTapped: handleSearchItemTapped
                        )

                        // Latest News Section
                        SearchLatestNewsSection(
                            items: viewModel.latestNews,
                            onItemTapped: handleNewsItemTapped,
                            onReadMore: handleNewsReadMore
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
        .task {
            await viewModel.loadInitialData()
        }
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
        }
        .fullScreenCover(item: $viewModel.selectedSearchSelection) { selection in
            NavigationStack {
                AssetDetailRouter(selection: selection)
                    .navigationBarHidden(true)
            }
        }
        // Item-based cover (via .aiChatCover) presents reliably even though SearchView is
        // itself presented as a sheet/cover from the tabs.
        .aiChatCover(isPresented: $showAIChat, viewModel: chatViewModel)
    }

    // MARK: - Ask Cay AI Row
    private func askCayAIRow(query: String) -> some View {
        Button {
            handleAskCayAI(query)
        } label: {
            HStack(spacing: AppSpacing.md) {
                Image(systemName: "sparkles")
                    .font(AppTypography.iconMedium)
                    .foregroundColor(AppColors.primaryBlue)
                    .frame(width: 40, height: 40)
                    .background(AppColors.primaryBlue.opacity(0.12))
                    .clipShape(Circle())

                VStack(alignment: .leading, spacing: 2) {
                    Text("Ask Cay AI")
                        .font(AppTypography.body).fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                    Text("“\(query)”")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(1)
                }

                Spacer()

                Image(systemName: "arrow.up.right")
                    .font(AppTypography.caption).fontWeight(.semibold)
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(AppSpacing.md)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.medium)
            .padding(.horizontal, AppSpacing.lg)
        }
        .buttonStyle(PlainButtonStyle())
    }

    // MARK: - Error Banner
    private func errorBanner(message: String) -> some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.yellow)

            Text(message)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textPrimary)

            Spacer()

            Button {
                viewModel.dismissError()
            } label: {
                Image(systemName: "xmark")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .padding(AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Action Handlers
    private func handleBackTapped() {
        dismiss()
    }

    private func handleSearchSubmit() {
        viewModel.performSearch()
    }

    /// Seed a fresh Cay AI conversation with the query and present the chat cover.
    /// `ChatViewModel.startNewConversation` has its own one-seed-in-flight guard, so a
    /// rapid double-tap can't create two sessions.
    private func handleAskCayAI(_ query: String) {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        chatViewModel.startNewConversation(firstMessage: trimmed, contextType: .none)
        showAIChat = true
    }

    /// Suggestion chips are starter questions ("What is P/E ratio?", "Why did AAPL move
    /// today?") — route them straight to Cay AI, not to ticker search.
    private func handleSuggestionTapped(_ suggestion: SearchQuerySuggestion) {
        handleAskCayAI(suggestion.text)
    }

    private func handleClearAll() {
        viewModel.clearAllRecentSearches()
    }

    private func handleSearchItemTapped(_ item: SearchResultItem) {
        viewModel.selectSearchResult(item)
    }

    private func handleNewsItemTapped(_ item: SearchNewsItem) {
        // `toNewsArticle()` carries the REAL apiId / url / sentiment / date, so the
        // detail screen can enrich. Building it inline stamped `sentiment: .neutral`
        // and `publishedAt: Date()` — a verdict no model produced and a fabricated
        // "just now" timestamp, on an article that may be days old.
        selectedNewsArticle = item.toNewsArticle()
    }

    private func handleNewsReadMore(_ item: SearchNewsItem) {
        handleNewsItemTapped(item)
    }
}

#Preview {
    SearchView()
}
