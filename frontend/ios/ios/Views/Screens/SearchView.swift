//
//  SearchView.swift
//  ios
//
//  Ticker/company lookup plus the user's own history. Opened from the Home, Updates, Learn,
//  ETF, Crypto and Commodity screens' search bars.
//
//  TICKERS ONLY. This screen used to double as a Cay AI entry — an "Ask Cay AI" row above the
//  results and a strip of starter-question chips. Both are gone: chat has its own door in the
//  global header (`AskCayAIButton`), and leaving a second one here meant two controls a few
//  points apart claiming the same thing.
//
//  TWO STATES, and keeping them apart is the point:
//    field has text → live Results
//    field empty    → Recent Searches (durable history)
//  They used to share one array, which is why "Recent Searches" was never a history at all.
//
//  Deliberately no news section. Market news lives on the Updates tab, and duplicating a feed
//  here made the primary action harder to find.
//

import SwiftUI

struct SearchView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel = SearchViewModel()
    /// Observed so a recorded search re-renders the history list without the ViewModel having
    /// to mirror the store's contents.
    @ObservedObject private var history = SearchHistoryStore.shared

    /// The current query with surrounding whitespace stripped. Chooses between the two states:
    /// empty (or spaces-only) shows the durable history, anything else shows live results.
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
                    onBackTapped: handleBackTapped,
                    onSearchSubmit: handleSearchSubmit
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
        .backSwipe { handleBackTapped() }
        .fullScreenCover(item: $viewModel.selectedSearchSelection) { selection in
            NavigationStack {
                AssetDetailRouter(selection: selection)
                    .navigationBarHidden(true)
            }
        }
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

    /// A history row reopens the ticker's detail screen.
    ///
    /// `.question` cannot occur: `SearchHistoryStore.load` filters the history to `.ticker`
    /// since this screen stopped being able to ask anything. The case still EXISTS in the model
    /// on purpose — dropping it would fail to decode any stored question row, and that decode
    /// error deletes the whole blob, taking the user's ticker history with it. So the switch
    /// stays exhaustive and this arm is deliberately inert rather than removed.
    private func handleHistoryTapped(_ entry: SearchHistoryEntry) {
        switch entry.kind {
        case .ticker:
            viewModel.openHistoryEntry(entry)
        case .question:
            break
        }
    }
}

#Preview {
    SearchView()
}
