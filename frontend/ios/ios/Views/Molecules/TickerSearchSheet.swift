//
//  TickerSearchSheet.swift
//  ios
//
//  Molecule: Simple ticker-only search sheet for navigating to a ticker detail page
//

import SwiftUI

struct TickerLiveSearchSheet: View {
    @State private var searchText = ""
    @State private var searchResults: [StockSearchResult] = []
    @State private var isSearching = false
    @State private var searchTask: Task<Void, Never>?

    var onTickerSelected: ((String) -> Void)?
    var onDismiss: (() -> Void)?

    private let stockRepository = StockRepository()

    var body: some View {
        NavigationView {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                VStack(spacing: AppSpacing.lg) {
                    SearchBar(
                        text: $searchText,
                        placeholder: "Search ticker symbol..."
                    )
                    .padding(.horizontal, AppSpacing.lg)

                    if searchText.isEmpty {
                        VStack(spacing: AppSpacing.md) {
                            Image(systemName: "magnifyingglass")
                                .font(AppTypography.iconHero)
                                .foregroundColor(AppColors.textMuted)

                            Text("Search for a stock ticker")
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textSecondary)
                                .multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else if isSearching {
                        ProgressView()
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else if searchResults.isEmpty {
                        VStack(spacing: AppSpacing.md) {
                            Image(systemName: "magnifyingglass")
                                .font(AppTypography.iconLarge)
                                .foregroundColor(AppColors.textMuted)

                            Text("No results found for \"\(searchText)\"")
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textSecondary)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else {
                        ScrollView {
                            LazyVStack(spacing: AppSpacing.sm) {
                                ForEach(searchResults) { result in
                                    Button {
                                        onTickerSelected?(result.ticker)
                                    } label: {
                                        HStack(spacing: AppSpacing.md) {
                                            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                                                Text(result.ticker)
                                                    .font(AppTypography.bodyEmphasis)
                                                    .foregroundColor(AppColors.textPrimary)

                                                Text(result.companyName)
                                                    .font(AppTypography.caption)
                                                    .foregroundColor(AppColors.textSecondary)
                                                    .lineLimit(1)
                                            }

                                            Spacer()

                                            if let exchange = result.exchange {
                                                Text(exchange)
                                                    .font(AppTypography.captionSmall)
                                                    .foregroundColor(AppColors.textMuted)
                                                    .padding(.horizontal, AppSpacing.sm)
                                                    .padding(.vertical, AppSpacing.xxs)
                                                    .background(AppColors.cardBackgroundLight)
                                                    .cornerRadius(AppCornerRadius.small)
                                            }

                                            Image(systemName: "chevron.right")
                                                .font(AppTypography.iconSmall)
                                                .foregroundColor(AppColors.textMuted)
                                        }
                                        .padding(AppSpacing.md)
                                        .background(AppColors.cardBackground)
                                        .cornerRadius(AppCornerRadius.medium)
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                            .padding(.horizontal, AppSpacing.lg)
                        }
                    }

                    Spacer()
                }
                .padding(.top, AppSpacing.lg)
            }
            .navigationTitle("Search Ticker")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        onDismiss?()
                    }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .onChange(of: searchText) { _, newValue in
            debounceSearch(newValue)
        }
    }

    private func debounceSearch(_ query: String) {
        searchTask?.cancel()

        guard !query.isEmpty else {
            searchResults = []
            return
        }

        searchTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled else { return }

            isSearching = true
            do {
                searchResults = try await stockRepository.searchStocks(query: query, limit: 10)
            } catch {
                searchResults = []
            }
            isSearching = false
        }
    }
}
