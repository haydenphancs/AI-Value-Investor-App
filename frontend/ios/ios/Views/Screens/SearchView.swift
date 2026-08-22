//
//  SearchView.swift
//  ios
//
//  Universal search screen: ticker/company lookup + "Ask Cay AI" + the user's own history.
//  Opened from the Home, Updates, Learn, ETF, Crypto and Commodity screens' search bars.
//
//  TWO STATES, and keeping them apart is the point:
//    field has text → Ask Cay AI + live Results
//    field empty    → Recent Searches (durable history)
//  They used to share one array, which is why "Recent Searches" was never a history at all.
//
//  Deliberately no news section. This screen is search and Cay AI; market news lives on the
//  Updates tab, and duplicating a feed here made the primary action harder to find.
//

import SwiftUI

struct SearchView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel = SearchViewModel()
    /// Caller-owned chat VM (the established pattern — see TickerDetailView). A fresh
    /// SearchView is created per present, so a fresh conversation per open is correct.
    @StateObject private var chatViewModel = ChatViewModel()
    /// Observed so a recorded search re-renders the history list without the ViewModel having
    /// to mirror the store's contents.
    @ObservedObject private var history = SearchHistoryStore.shared
    @State private var showAIChat = false

    /// The current query with surrounding whitespace stripped. Drives the "Ask Cay AI"
    /// row's visibility so an empty / spaces-only field never seeds a chat.
    private var trimmedQuery: String {
        viewModel.searchText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                SearchHeader(
                    searchText: $viewModel.searchText,
                    suggestions: viewModel.querySuggestions,
                    onBackTapped: handleBackTapped,
                    onSearchSubmit: handleSearchSubmit,
                    onSuggestionTapped: handleSuggestionTapped
                )

                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xxl) {
                        if let error = viewModel.error {
                            errorBanner(message: error)
                        }

                        if trimmedQuery.isEmpty {
                            // Nothing typed → the durable history.
                            RecentSearchesSection(
                                entries: history.entries,
                                onClearAll: viewModel.clearAllHistory,
                                onEntryTapped: handleHistoryTapped,
                                onEntryRemoved: viewModel.removeHistoryEntry
                            )
                        } else {
                            // Ask Cay AI first: it is the primary action, and it works for any
                            // query including ones that match no ticker.
                            askCayAIRow(query: trimmedQuery)

                            SearchResultsSection(
                                items: viewModel.results,
                                onItemTapped: viewModel.selectSearchResult
                            )
                        }

                        Spacer()
                            .frame(height: AppSpacing.xxxl)
                    }
                    .padding(.top, AppSpacing.md)
                }
            }

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
            .cardSurface(cornerRadius: AppCornerRadius.medium)
            .padding(.horizontal, AppSpacing.lg)
        }
        .buttonStyle(PlainButtonStyle())
    }

    // MARK: - Error Banner
    private func errorBanner(message: String) -> some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(AppColors.caution)

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
        .cardSurface(cornerRadius: AppCornerRadius.medium)
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Action Handlers
    private func handleBackTapped() {
        dismiss()
    }

    private func handleSearchSubmit() {
        viewModel.performSearch()
    }

    /// Seed a fresh Cay AI conversation with the query, RECORD it, and present the chat cover.
    /// `ChatViewModel.startNewConversation` has its own one-seed-in-flight guard, so a
    /// rapid double-tap can't create two sessions.
    private func handleAskCayAI(_ query: String) {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        // Recorded here rather than inside ChatViewModel: this is the SEARCH screen's history,
        // and hooking the shared `startNewConversation` would also capture chats started from a
        // ticker page or a Money Moves article, which are not searches.
        SearchHistoryStore.shared.record(question: trimmed)
        // Explicitly `nil`, NOT `.none`.
        //
        // `ChatContextType` has a `case none = "NONE"` AND the parameter is
        // `ChatContextType?`, so a bare `.none` is ambiguous and Swift resolves it to
        // `Optional.none` — i.e. nil. That is the behaviour that ships and the one we want
        // (general chat, grounded on nothing); writing it out stops the ambiguity warning
        // and stops anyone "correcting" it to the enum case later.
        //
        // The two are near-equivalent anyway — the backend folds "NONE" into `_NO_CONTEXT`
        // (`chat_context_resolver.py`) and `AIChatScreen` renders the grounding chip only
        // when `ctx != .none` — so this is a clarity fix, not a behaviour change.
        chatViewModel.startNewConversation(firstMessage: trimmed, contextType: nil)
        showAIChat = true
    }

    /// Suggestion chips are starter questions ("What is P/E ratio?", "Why did AAPL move
    /// today?") — route them straight to Cay AI, not to ticker search.
    private func handleSuggestionTapped(_ suggestion: SearchQuerySuggestion) {
        handleAskCayAI(suggestion.text)
    }

    /// A history row. A ticker reopens its detail screen; a question is asked again in a fresh
    /// conversation (the entry stores the text, not a session id, so this always works — even
    /// signed out, or after that session was deleted).
    private func handleHistoryTapped(_ entry: SearchHistoryEntry) {
        switch entry.kind {
        case .ticker:
            viewModel.openHistoryEntry(entry)
        case .question:
            handleAskCayAI(entry.text)
        }
    }
}

#Preview {
    SearchView()
}
