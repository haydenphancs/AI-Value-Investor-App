//
//  TargetSelectionSection.swift
//  ios
//
//  Organism: Target selection with search bar, live search results, and quick ticker chips
//

import SwiftUI

struct TargetSelectionSection: View {
    @Binding var searchText: String
    let quickTickers: [QuickTicker]
    var searchResults: [StockSearchResult] = []
    var isSearching: Bool = false
    var showSearchResults: Bool = false
    var onTickerSelected: ((QuickTicker) -> Void)?
    var onSearchSubmit: (() -> Void)?
    var onResultSelected: ((StockSearchResult) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section header
            Text("Select Your Target:")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)

            // Search bar
            SearchBar(
                text: $searchText,
                placeholder: "Find a company...",
                onSubmit: onSearchSubmit
            )

            // Search results dropdown
            if showSearchResults && !searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                searchResultsView
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Search Results Dropdown
    @ViewBuilder
    private var searchResultsView: some View {
        VStack(spacing: 0) {
            if isSearching {
                HStack(spacing: AppSpacing.sm) {
                    ProgressView()
                        .tint(AppColors.textMuted)
                        .scaleEffect(0.8)
                    Text("Searching...")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textMuted)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.md)
            } else if searchResults.isEmpty {
                Text("No companies found")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
            } else {
                ForEach(searchResults) { result in
                    Button {
                        onResultSelected?(result)
                    } label: {
                        searchResultRow(result)
                    }
                    .buttonStyle(.plain)

                    if result.id != searchResults.last?.id {
                        Divider()
                            .background(AppColors.textMuted.opacity(0.2))
                    }
                }
            }
        }
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    private func searchResultRow(_ result: StockSearchResult) -> some View {
        HStack(spacing: AppSpacing.sm) {
            // Ticker badge
            Text(result.ticker)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.accentCyan)
                .frame(width: 60, alignment: .leading)

            // Company name
            Text(result.companyName)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)

            Spacer()

            // Exchange
            if let exchange = result.exchange {
                Text(exchange)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
    }
}

#Preview {
    VStack {
        TargetSelectionSection(
            searchText: .constant("App"),
            quickTickers: QuickTicker.defaults,
            searchResults: [
                StockSearchResult(ticker: "AAPL", companyName: "Apple Inc.", exchange: "NASDAQ", sector: nil, logoUrl: nil),
                StockSearchResult(ticker: "APLE", companyName: "Apple Hospitality REIT Inc.", exchange: "NYSE", sector: nil, logoUrl: nil),
            ],
            isSearching: false,
            showSearchResults: true
        )
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
