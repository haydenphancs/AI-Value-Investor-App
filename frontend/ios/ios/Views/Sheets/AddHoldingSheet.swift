//
//  AddHoldingSheet.swift
//  ios
//
//  Sheet for adding a portfolio holding (used by Portfolio Insights).
//
//  Two input modes:
//   - Shares: backend recomputes market_value from the live FMP price on
//     every read so the diversification score stays accurate over time.
//   - Dollar amount: stored as-is and never refreshed.
//

import SwiftUI

// MARK: - Holding Input Mode

private enum HoldingInputMode: String, CaseIterable {
    case shares = "Shares"
    case dollars = "Dollar amount"

    var helperText: String {
        switch self {
        case .shares:
            return "Value updates as the price moves."
        case .dollars:
            return "Stored as a static amount."
        }
    }
}

// MARK: - Add Holding Sheet

struct AddHoldingSheet: View {
    @ObservedObject var viewModel: TrackingViewModel
    @Environment(\.dismiss) private var dismiss

    // Search state
    @State private var searchText: String = ""
    @State private var searchResults: [StockSearchResult] = []
    @State private var isSearching: Bool = false
    @State private var searchTask: Task<Void, Never>?

    // Selection + form state
    @State private var selected: StockSearchResult?
    @State private var inputMode: HoldingInputMode = .shares
    @State private var sharesInput: String = ""
    @State private var dollarsInput: String = ""

    // Submission state
    @State private var isSubmitting: Bool = false
    @State private var formError: String?

    private let stockRepository = StockRepository.shared

    var body: some View {
        NavigationView {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                VStack(spacing: AppSpacing.lg) {
                    if let pinned = selected {
                        SelectedTickerHeader(result: pinned) {
                            selected = nil
                        }
                        .padding(.horizontal, AppSpacing.lg)

                        inputForm(for: pinned)
                            .padding(.horizontal, AppSpacing.lg)

                        Spacer()
                    } else {
                        SearchBar(text: $searchText, placeholder: "Search ticker or name…")
                            .padding(.horizontal, AppSpacing.lg)

                        searchBody
                    }
                }
                .padding(.top, AppSpacing.lg)
            }
            .navigationTitle("Add Holding")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .onChange(of: searchText) { _, newValue in
            debounceSearch(newValue)
        }
    }

    // MARK: - Search Results

    @ViewBuilder
    private var searchBody: some View {
        if searchText.isEmpty {
            searchEmptyState(
                icon: "magnifyingglass",
                title: "Search for a ticker to add to your portfolio"
            )
        } else if isSearching {
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if searchResults.isEmpty {
            searchEmptyState(
                icon: "magnifyingglass",
                title: "No results for \"\(searchText)\""
            )
        } else {
            ScrollView {
                LazyVStack(spacing: AppSpacing.sm) {
                    ForEach(searchResults) { result in
                        Button {
                            selectResult(result)
                        } label: {
                            AddHoldingResultRow(result: result)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }

    private func searchEmptyState(icon: String, title: String) -> some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: icon)
                .font(AppTypography.iconHero)
                .foregroundColor(AppColors.textMuted)

            Text(title)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Input Form

    @ViewBuilder
    private func inputForm(for result: StockSearchResult) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Mode picker
            Picker("Input mode", selection: $inputMode) {
                ForEach(HoldingInputMode.allCases, id: \.self) { mode in
                    Text(mode.rawValue).tag(mode)
                }
            }
            .pickerStyle(.segmented)

            // Mode-specific input
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                if inputMode == .shares {
                    TextField("e.g. 25", text: $sharesInput)
                        .keyboardType(.decimalPad)
                        .textFieldStyle(.roundedBorder)
                } else {
                    TextField("e.g. 12500", text: $dollarsInput)
                        .keyboardType(.decimalPad)
                        .textFieldStyle(.roundedBorder)
                }

                Text(inputMode.helperText)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            if let error = formError {
                Text(error)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.bearish)
            }

            Button {
                submit(for: result)
            } label: {
                HStack {
                    if isSubmitting {
                        ProgressView()
                            .progressViewStyle(.circular)
                            .tint(.white)
                    }
                    Text(isSubmitting ? "Adding…" : "Add to Portfolio")
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(.white)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.md)
                .background(canSubmit ? AppColors.primaryBlue : AppColors.cardBackgroundLight)
                .cornerRadius(AppCornerRadius.medium)
            }
            .buttonStyle(.plain)
            .disabled(!canSubmit || isSubmitting)
        }
    }

    // MARK: - Helpers

    private var canSubmit: Bool {
        switch inputMode {
        case .shares:
            return Double(sharesInput).map { $0 > 0 } ?? false
        case .dollars:
            return Double(dollarsInput).map { $0 > 0 } ?? false
        }
    }

    private func selectResult(_ result: StockSearchResult) {
        selected = result
        searchText = ""
        searchResults = []
        formError = nil
    }

    private func submit(for result: StockSearchResult) {
        formError = nil
        let shares: Double?
        let marketValue: Double?
        switch inputMode {
        case .shares:
            shares = Double(sharesInput)
            marketValue = nil
        case .dollars:
            shares = nil
            marketValue = Double(dollarsInput)
        }

        let assetType = mapAssetType(result.type)

        isSubmitting = true
        Task { @MainActor in
            do {
                try await viewModel.addHolding(
                    ticker: result.ticker,
                    companyName: result.companyName,
                    shares: shares,
                    marketValue: marketValue,
                    assetType: assetType
                )
                isSubmitting = false
                dismiss()
            } catch {
                print("[AddHoldingSheet] ❌ Add failed: \(error)")
                formError = "Couldn't add \(result.ticker). It may already be in your portfolio."
                isSubmitting = false
            }
        }
    }

    private func mapAssetType(_ type: String?) -> String {
        switch type?.lowercased() {
        case "crypto":      return "Crypto"
        case "etf":         return "ETF"
        case "trust":       return "ETF"
        default:            return "Stock"
        }
    }

    private func debounceSearch(_ query: String) {
        searchTask?.cancel()
        formError = nil

        guard !query.isEmpty else {
            searchResults = []
            return
        }

        searchTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 300_000_000) // 300ms
            guard !Task.isCancelled else { return }

            isSearching = true
            do {
                searchResults = try await stockRepository.searchStocks(query: query, limit: 10)
            } catch {
                print("[AddHoldingSheet] Search failed: \(error)")
                searchResults = []
            }
            isSearching = false
        }
    }
}

// MARK: - Selected Ticker Header

private struct SelectedTickerHeader: View {
    let result: StockSearchResult
    var onClear: () -> Void

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(result.ticker)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)

                Text(result.companyName)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(1)
            }

            Spacer()

            Button {
                onClear()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(AppTypography.iconLarge)
                    .foregroundColor(AppColors.textMuted)
            }
            .buttonStyle(.plain)
        }
        .padding(AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}

// MARK: - Search Result Row

/// File-private row used by AddHoldingSheet's ticker picker. Named distinctly
/// from `Views/Molecules/SearchResultRow.swift` so the call site picks the
/// right one without ambiguity.
private struct AddHoldingResultRow: View {
    let result: StockSearchResult

    var body: some View {
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

            Image(systemName: "plus.circle.fill")
                .font(AppTypography.iconLarge)
                .foregroundColor(AppColors.primaryBlue)
        }
        .padding(AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}
