//
//  ManageTickersSheet.swift
//  ios
//
//  Organism: dedicated reorder + delete surface for the tickers in the
//  active portfolio. Reached from the "..." menu in PortfolioHeaderBar
//  (Manage Tickers). Persists order via PortfolioStore.setTickers and
//  removals via PortfolioStore.removeTicker — same backend hooks the
//  EditPortfolioSheet's per-portfolio editor uses.
//

import SwiftUI

struct ManageTickersSheet: View {
    @ObservedObject var viewModel: TrackingViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var errorMessage: String?

    private var activePortfolio: Portfolio? {
        viewModel.portfolioStore.activePortfolio
    }

    /// Read straight from the store every render so reorder/delete edits
    /// stay in sync with optimistic updates.
    private var liveTickers: [String] {
        activePortfolio?.tickers ?? []
    }

    /// Symbol → company name lookup for the row subtitle. Falls back to
    /// the symbol when the asset feed hasn't populated this ticker yet.
    private var companyNames: [String: String] {
        Dictionary(
            uniqueKeysWithValues: viewModel.trackedAssets.map {
                ($0.ticker.uppercased(), $0.companyName)
            }
        )
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()

                tickerList

                if let errorMessage {
                    VStack {
                        Spacer()
                        Text(errorMessage)
                            .font(AppTypography.caption)
                            .foregroundColor(.white)
                            .padding(AppSpacing.md)
                            .background(AppColors.bearish)
                            .cornerRadius(AppCornerRadius.medium)
                            .padding(.bottom, AppSpacing.xl)
                    }
                }
            }
            .navigationTitle(activePortfolio?.name ?? "Manage Tickers")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .fontWeight(.semibold)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - List

    @ViewBuilder
    private var tickerList: some View {
        if liveTickers.isEmpty {
            emptyState
        } else {
            List {
                Section {
                    ForEach(liveTickers, id: \.self) { ticker in
                        tickerRow(ticker)
                            .listRowBackground(AppColors.cardBackground)
                    }
                    .onMove(perform: handleMove)
                    .onDelete(perform: handleDelete)
                } footer: {
                    Text("Drag the handles on the right to reorder. Swipe or tap the minus to remove a ticker from this portfolio.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(AppColors.background)
            .environment(\.editMode, .constant(.active))
        }
    }

    private func tickerRow(_ ticker: String) -> some View {
        let symbol = ticker.uppercased()
        let companyName = companyNames[symbol]

        return VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            Text(symbol)
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)

            if let companyName, companyName != symbol {
                Text(companyName)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(1)
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: AppSpacing.sm) {
            Spacer()
            Text("No tickers in this portfolio")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
            Text("Add tickers from the Tracking screen, then come back here to reorder them.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xl)
            Spacer()
        }
    }

    // MARK: - Actions

    private func handleMove(from source: IndexSet, to destination: Int) {
        guard let portfolioId = activePortfolio?.id else { return }
        var reordered = liveTickers
        reordered.move(fromOffsets: source, toOffset: destination)
        Task { @MainActor in
            do {
                try await viewModel.portfolioStore.setTickers(reordered, in: portfolioId)
            } catch {
                errorMessage = "Couldn't save the new order. Try again."
                print("[ManageTickersSheet] ❌ Reorder failed: \(error)")
            }
        }
    }

    private func handleDelete(at offsets: IndexSet) {
        guard let portfolioId = activePortfolio?.id else { return }
        let tickers = liveTickers
        let toRemove = offsets.compactMap { tickers.indices.contains($0) ? tickers[$0] : nil }
        Task { @MainActor in
            for ticker in toRemove {
                do {
                    try await viewModel.portfolioStore.removeTicker(ticker, from: portfolioId)
                } catch {
                    errorMessage = "Couldn't remove \(ticker)."
                    print("[ManageTickersSheet] ❌ Remove \(ticker) failed: \(error)")
                }
            }
        }
    }
}

#Preview {
    ManageTickersSheet(viewModel: TrackingViewModel())
        .preferredColorScheme(.dark)
}
