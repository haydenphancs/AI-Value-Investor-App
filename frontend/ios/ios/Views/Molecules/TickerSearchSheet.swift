//
//  TickerSearchSheet.swift
//  ios
//
//  Molecule: Simple ticker-only search sheet for navigating to a ticker detail page
//  Supports routing to Stock, Crypto, ETF, Index, and Commodity detail views.
//

import SwiftUI

/// Lightweight struct to carry search selection with asset type info.
struct SearchSelection: Identifiable, Hashable {
    var id: String { "\(symbol)_\(type)" }
    let symbol: String
    let type: String  // "stock", "crypto", "etf", "fund", "index", "commodity"
}

struct TickerLiveSearchSheet: View {
    @State private var searchText = ""
    @State private var searchResults: [StockSearchResult] = []
    @State private var isSearching = false
    @State private var searchTask: Task<Void, Never>?

    var onTickerSelected: ((SearchSelection) -> Void)?
    var onDismiss: (() -> Void)?
    /// Returns true when the result is already on the user's master watchlist —
    /// drives the star icon's filled/empty state. When nil, the star button
    /// is hidden (used by call sites that don't have a watchlist context,
    /// e.g. the Ticker Detail in-screen search).
    var isInWatchlist: ((String) -> Bool)?
    /// Tap handler for the star button. When provided alongside `isInWatchlist`,
    /// each result row gets a star button on the far right outside the card.
    var onAddToWatchlist: ((StockSearchResult) -> Void)?

    private let stockRepository = StockRepository.shared

    var body: some View {
        NavigationView {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                VStack(spacing: AppSpacing.lg) {
                    SearchBar(
                        text: $searchText,
                        placeholder: "Search stocks, crypto, ETFs...",
                        autoFocus: true
                    )
                    .padding(.horizontal, AppSpacing.lg)

                    if searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        // Nothing typed → the trending chips, directly under the field: the
                        // keyboard rises the moment this sheet opens and hides the bottom half.
                        // A chip opens the ticker like a result would, but is NOT recorded as
                        // a search pick (the list would feed itself).
                        ScrollView {
                            SearchTrendingChipsSection(
                                sections: SearchTrendingStore.shared.sections(for: .all),
                                onItemTapped: { item in onTickerSelected?(item.selection) }
                            )
                        }
                        .scrollDismissesKeyboard(.interactively)
                        // Beats the trailing Spacer for the free height — without it the stack
                        // split the space in half and cut the second section off.
                        .layoutPriority(1)
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
                                    resultRow(result)
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
        // Full height from the first frame, like the Home search sheet. With a `.medium`
        // detent it opened at half height and then jumped to full when `SearchBar`'s
        // auto-focus raised the keyboard 0.35 s later — a two-step open for a screen
        // whose only job is typing.
        .presentationDetents([.large])
        .onChange(of: searchText) { _, newValue in
            debounceSearch(newValue)
        }
        .task { await SearchTrendingStore.shared.prefetch() }
    }

    @ViewBuilder
    private func resultRow(_ result: StockSearchResult) -> some View {
        HStack(spacing: AppSpacing.sm) {
            Button {
                // A real search pick — the ONLY place this sheet records one (not the star,
                // not a chip).
                SearchTrendingStore.shared.recordPick(symbol: result.ticker, type: result.type)
                onTickerSelected?(SearchSelection(
                    symbol: result.ticker,
                    type: result.type ?? "stock"
                ))
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
                .cardSurface(cornerRadius: AppCornerRadius.medium)
            }
            .buttonStyle(.plain)

            // Star button — only shown when the call site supplies the
            // watchlist callbacks. Lives outside the card on purpose so the
            // tap target is unambiguous: tap card = navigate, tap star = add.
            if let isInWatchlist, let onAddToWatchlist {
                // Ask with the spelling the group STORES: a coin is persisted as the pair
                // (`BTCUSD`, migration 160) while search hands us the bare `BTC`. The bare
                // key never matched a tracked coin (empty star, every tap "dead") and DID
                // match the same-ticker security (the BTC ETF) for a user who only holds
                // that — filled star, and a tap silently added Bitcoin.
                let inList = isInWatchlist(CryptoSymbol.storedSymbol(for: result))
                Button {
                    onAddToWatchlist(result)
                } label: {
                    Image(systemName: inList ? "star.fill" : "star")
                        .font(AppTypography.iconMedium)
                        .foregroundColor(inList ? AppColors.neutral : AppColors.textPrimary)
                        .frame(width: HitSlop.minimumTarget, height: HitSlop.minimumTarget)
                        .hitSlop(reaching: HitSlop.minimumTarget, maxInset: AppSpacing.xs)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(inList ? "On watchlist" : "Add to watchlist")
            }
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
