//
//  TargetSearchSheet.swift
//  ios
//
//  Sheet shown when the user taps the "Find a company" search field on the
//  Research tab. Live-searches stocks via /stocks/search and filters out
//  ETFs / funds / crypto so only company stocks can be picked as a target.
//

import SwiftUI

struct TargetSearchSheet: View {
    let onSelect: (StockSearchResult) -> Void
    @Environment(\.dismiss) private var dismiss

    @State private var query: String = ""
    @State private var results: [StockSearchResult] = []
    @State private var isSearching: Bool = false
    @State private var error: String?
    @State private var searchTask: Task<Void, Never>?

    private let repository = StockRepository(apiClient: .shared)

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()

                VStack(spacing: 0) {
                    SearchBar(
                        text: $query,
                        placeholder: "Find a company…",
                        autoFocus: true
                    )
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.sm)

                    contentBody
                        .padding(.top, AppSpacing.md)

                    Spacer(minLength: 0)
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("Select a Company")
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button(action: { dismiss() }) {
                        Image(systemName: "xmark")
                            .font(AppTypography.iconSmall).fontWeight(.semibold)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }
            .toolbarBackground(AppColors.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .onChange(of: query) { _, newValue in
                scheduleSearch(for: newValue)
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Content

    @ViewBuilder
    private var contentBody: some View {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)

        if trimmed.isEmpty {
            emptyState
        } else if isSearching && results.isEmpty {
            searchingState
        } else if let error {
            errorState(error)
        } else if results.isEmpty {
            noResultsState
        } else {
            resultsList
        }
    }

    private var emptyState: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 28))
                .foregroundColor(AppColors.textMuted)
            Text("Search any U.S.-listed company")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
            Text("Try a ticker (AAPL) or a name (Microsoft)")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.xxxl)
    }

    private var searchingState: some View {
        HStack(spacing: AppSpacing.sm) {
            ProgressView().tint(AppColors.textMuted).scaleEffect(0.85)
            Text("Searching…")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.xxl)
    }

    private func errorState(_ message: String) -> some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 24))
                .foregroundColor(AppColors.bearish)
            Text(message)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, AppSpacing.xl)
        .padding(.top, AppSpacing.xxl)
    }

    private var noResultsState: some View {
        VStack(spacing: AppSpacing.sm) {
            Text("No companies found")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
            Text("Try a different ticker or name. ETFs and crypto aren't supported here.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, AppSpacing.xl)
        .padding(.top, AppSpacing.xxl)
    }

    private var resultsList: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: 0) {
                ForEach(results) { result in
                    Button {
                        onSelect(result)
                        dismiss()
                    } label: {
                        resultRow(result)
                    }
                    .buttonStyle(.plain)

                    if result.id != results.last?.id {
                        Divider()
                            .background(AppColors.textMuted.opacity(0.2))
                            .padding(.leading, AppSpacing.lg)
                    }
                }
            }
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
            .padding(.horizontal, AppSpacing.lg)
        }
    }

    private func resultRow(_ result: StockSearchResult) -> some View {
        HStack(spacing: AppSpacing.md) {
            // Ticker badge
            Text(result.ticker)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.accentCyan)
                .frame(width: 64, alignment: .leading)

            VStack(alignment: .leading, spacing: 2) {
                Text(result.companyName)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                if let exchange = result.exchange {
                    Text(exchange)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            Spacer()

            Image(systemName: "chevron.right")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, 12)
        .contentShape(Rectangle())
    }

    // MARK: - Search

    private func scheduleSearch(for raw: String) {
        searchTask?.cancel()
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            results = []
            isSearching = false
            error = nil
            return
        }
        isSearching = true
        error = nil

        searchTask = Task {
            try? await Task.sleep(nanoseconds: 300_000_000) // 300ms debounce
            if Task.isCancelled { return }
            do {
                let raw = try await repository.searchStocks(query: trimmed, limit: 20)
                if Task.isCancelled { return }
                // Companies only — exclude etf, fund, crypto.
                let stocksOnly = raw.filter { ($0.type ?? "stock") == "stock" }
                await MainActor.run {
                    self.results = stocksOnly
                    self.isSearching = false
                }
            } catch {
                if Task.isCancelled { return }
                await MainActor.run {
                    self.results = []
                    self.isSearching = false
                    self.error = "Search failed. Please try again."
                }
            }
        }
    }
}

#Preview {
    TargetSearchSheet { _ in }
        .preferredColorScheme(.dark)
}
