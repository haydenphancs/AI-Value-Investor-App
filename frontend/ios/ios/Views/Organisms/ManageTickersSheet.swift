//
//  ManageTickersSheet.swift
//  ios
//
//  Organism: dedicated reorder + delete surface for the tickers in the
//  active portfolio. Reached from the "..." menu in PortfolioHeaderBar
//  (Manage Tickers). Every edit goes through `TrackingViewModel.moveTickers`
//  / `removeTickers` — the same paths Edit Portfolio and the Holdings swipe
//  use — so the Custom-sort switch, the remove-fully rule and the failure
//  reporting cannot drift between the three.
//

import SwiftUI

struct ManageTickersSheet: View {
    @ObservedObject var viewModel: TrackingViewModel
    @Environment(\.dismiss) private var dismiss

    private var activePortfolio: Portfolio? {
        viewModel.portfolioStore.activePortfolio
    }

    /// Read straight from the store every render so reorder/delete edits
    /// stay in sync with optimistic updates.
    private var liveTickers: [String] {
        activePortfolio?.tickers ?? []
    }

    /// Symbol → company name lookup for the row subtitle. The live feed first; the saved
    /// snapshot's rows while the feed has not answered (the sheet can open over a snapshot);
    /// the bare symbol when neither knows the ticker.
    private var companyNames: [String: String] {
        let rows = viewModel.trackedAssets.isEmpty
            ? (viewModel.presentedSnapshot?.assets ?? [])
            : viewModel.trackedAssets
        return Dictionary(
            rows.map { ($0.ticker.uppercased(), $0.companyName) },
            // first-wins: `.uppercased()` can collapse case-differing rows onto one key,
            // and `uniqueKeysWithValues:` traps on the duplicate.
            uniquingKeysWith: { first, _ in first }
        )
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()

                tickerList
            }
            .navigationTitle(activePortfolio?.name ?? "Manage Tickers")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .fontWeight(.semibold)
                }
            }
        }
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
                    Text("Drag to set your Custom order — Holdings switches to Custom when you reorder. Removing a ticker no other portfolio holds also takes it off your watchlist.")
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
            Text("Add tickers with the search bar on Holdings, then come back here to reorder them.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xl)
            Spacer()
        }
    }

    // MARK: - Actions

    /// Synchronous on purpose: the view model applies the new order before returning, so the
    /// dragged row never snaps back. A failure is reported (and the order restored) by it.
    private func handleMove(from source: IndexSet, to destination: Int) {
        guard let portfolioId = activePortfolio?.id else { return }
        var reordered = liveTickers
        reordered.move(fromOffsets: source, toOffset: destination)
        viewModel.moveTickers(in: portfolioId, to: reordered)
    }

    private func handleDelete(at offsets: IndexSet) {
        guard let portfolioId = activePortfolio?.id else { return }
        let tickers = liveTickers
        let toRemove = offsets.compactMap { tickers.indices.contains($0) ? tickers[$0] : nil }
        viewModel.removeTickers(toRemove, from: portfolioId)
    }
}

#Preview {
    ManageTickersSheet(viewModel: TrackingViewModel())
        .environment(\.appState, AppState())
}
