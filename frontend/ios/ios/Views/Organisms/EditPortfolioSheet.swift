//
//  EditPortfolioSheet.swift
//  ios
//
//  Organism: management sheet for the user's portfolios. Lists every portfolio
//  with rename + delete affordances, drag handles to reorder, and a tap target
//  to drill into a per-portfolio editor for ticker management.
//
//  The destructive delete is hidden when only one portfolio remains — the
//  server backstops the same constraint.
//

import SwiftUI

struct EditPortfolioSheet: View {
    @ObservedObject var viewModel: TrackingViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var pendingDelete: Portfolio?
    @State private var errorMessage: String?

    private var portfolios: [Portfolio] {
        viewModel.portfolioStore.portfolios
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()

                portfolioList

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
            .navigationTitle("Edit Portfolios")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar(content: editToolbar)
            .alert(
                "Delete portfolio?",
                isPresented: deleteConfirmationBinding,
                presenting: pendingDelete
            ) { portfolio in
                Button("Delete", role: .destructive) {
                    confirmDelete(portfolio)
                }
                Button("Cancel", role: .cancel) {
                    pendingDelete = nil
                }
            } message: { portfolio in
                Text("\(portfolio.name) will be removed. The tickers stay on your master watchlist.")
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Portfolio list

    private var portfolioList: some View {
        List {
            Section {
                ForEach(portfolios) { portfolio in
                    NavigationLink {
                        PortfolioDetailEditor(viewModel: viewModel, portfolio: portfolio)
                    } label: {
                        portfolioRow(portfolio)
                    }
                    .listRowBackground(AppColors.cardBackground)
                }
                .onMove(perform: handleMove)
                .onDelete(perform: handleDelete)
            } footer: {
                if portfolios.count == 1 {
                    Text("You have one portfolio. Add another from the “+” button to enable reordering and deleting.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background(AppColors.background)
        .environment(\.editMode, Binding<EditMode>.constant(.active))
    }

    // MARK: - Toolbar

    @ToolbarContentBuilder
    private func editToolbar() -> some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button {
                viewModel.openNewPortfolioSheet()
            } label: {
                Image(systemName: "plus")
            }
        }
        ToolbarItem(placement: .confirmationAction) {
            Button("Done") { dismiss() }
                .fontWeight(.semibold)
        }
    }

    // MARK: - Row

    private func portfolioRow(_ portfolio: Portfolio) -> some View {
        HStack(spacing: AppSpacing.md) {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(portfolio.name)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text(tickerCountLabel(portfolio))
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            Spacer()

            if portfolio.id == viewModel.portfolioStore.activePortfolioId {
                Text("Active")
                    .font(AppTypography.captionSmall).fontWeight(.semibold)
                    .foregroundColor(AppColors.primaryBlue)
                    .padding(.horizontal, AppSpacing.sm)
                    .padding(.vertical, AppSpacing.xxs)
                    .background(AppColors.primaryBlue.opacity(0.15))
                    .cornerRadius(AppCornerRadius.small)
            }
        }
    }

    private func tickerCountLabel(_ portfolio: Portfolio) -> String {
        portfolio.tickers.count == 1
            ? "1 ticker"
            : "\(portfolio.tickers.count) tickers"
    }

    // MARK: - Actions

    private func handleMove(from source: IndexSet, to destination: Int) {
        guard portfolios.count > 1 else { return }
        var reordered = portfolios
        reordered.move(fromOffsets: source, toOffset: destination)
        Task { @MainActor in
            do {
                try await viewModel.portfolioStore.reorderPortfolios(reordered)
            } catch {
                errorMessage = "Couldn't save the new order. Try again."
                print("[EditPortfolioSheet] ❌ Reorder failed: \(error)")
            }
        }
    }

    private func handleDelete(at offsets: IndexSet) {
        guard portfolios.count > 1, let index = offsets.first else { return }
        pendingDelete = portfolios[index]
    }

    private var deleteConfirmationBinding: Binding<Bool> {
        Binding(
            get: { pendingDelete != nil },
            set: { if !$0 { pendingDelete = nil } }
        )
    }

    private func confirmDelete(_ portfolio: Portfolio) {
        pendingDelete = nil
        Task { @MainActor in
            do {
                try await viewModel.deletePortfolio(id: portfolio.id)
            } catch {
                errorMessage = "Couldn't delete \(portfolio.name)."
                print("[EditPortfolioSheet] ❌ Delete failed: \(error)")
            }
        }
    }
}

// MARK: - Per-portfolio editor

private struct PortfolioDetailEditor: View {
    @ObservedObject var viewModel: TrackingViewModel
    let portfolio: Portfolio
    @Environment(\.dismiss) private var dismiss

    @State private var draftName: String
    @State private var isSavingName: Bool = false
    @State private var nameError: String?

    init(viewModel: TrackingViewModel, portfolio: Portfolio) {
        self.viewModel = viewModel
        self.portfolio = portfolio
        _draftName = State(initialValue: portfolio.name)
    }

    /// Always read fresh from the store so swipe-to-delete and reorder render
    /// against current state.
    private var liveTickers: [String] {
        viewModel.portfolioStore.portfolios
            .first(where: { $0.id == portfolio.id })?
            .tickers ?? []
    }

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            List {
                Section("Name") {
                    HStack(spacing: AppSpacing.sm) {
                        TextField("Portfolio name", text: $draftName)
                            .textFieldStyle(.roundedBorder)
                            .submitLabel(.done)
                            .onSubmit { saveName() }
                            .disabled(isSavingName)

                        if draftName.trimmingCharacters(in: .whitespaces) != portfolio.name {
                            Button("Save") { saveName() }
                                .disabled(isSavingName)
                        }
                    }

                    if let nameError {
                        Text(nameError)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.bearish)
                    }
                }
                .listRowBackground(AppColors.cardBackground)

                Section("Tickers") {
                    if liveTickers.isEmpty {
                        Text("This portfolio is empty. Add tickers from the Tracking screen.")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                            .listRowBackground(AppColors.cardBackground)
                    } else {
                        ForEach(liveTickers, id: \.self) { ticker in
                            Text(ticker)
                                .font(AppTypography.bodyEmphasis)
                                .foregroundColor(AppColors.textPrimary)
                                .listRowBackground(AppColors.cardBackground)
                        }
                        .onMove(perform: handleMoveTicker)
                        .onDelete(perform: handleDeleteTicker)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(AppColors.background)
            .environment(\.editMode, .constant(.active))
        }
        .navigationTitle(portfolio.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
    }

    private func saveName() {
        let trimmed = draftName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed != portfolio.name else {
            nameError = trimmed.isEmpty ? "Name cannot be empty." : nil
            return
        }
        let lower = trimmed.lowercased()
        if viewModel.portfolioStore.portfolios.contains(where: {
            $0.id != portfolio.id && $0.name.lowercased() == lower
        }) {
            nameError = "A portfolio with that name already exists."
            return
        }

        isSavingName = true
        nameError = nil
        Task { @MainActor in
            do {
                try await viewModel.renamePortfolio(id: portfolio.id, to: trimmed)
                isSavingName = false
            } catch {
                isSavingName = false
                nameError = "Couldn't save the new name."
                print("[PortfolioDetailEditor] ❌ Rename failed: \(error)")
            }
        }
    }

    private func handleMoveTicker(from source: IndexSet, to destination: Int) {
        var updated = liveTickers
        updated.move(fromOffsets: source, toOffset: destination)
        Task { @MainActor in
            do {
                try await viewModel.portfolioStore.setTickers(updated, in: portfolio.id)
            } catch {
                print("[PortfolioDetailEditor] ❌ Reorder tickers failed: \(error)")
            }
        }
    }

    private func handleDeleteTicker(at offsets: IndexSet) {
        let toRemove = offsets.compactMap { liveTickers[safe: $0] }
        Task { @MainActor in
            for ticker in toRemove {
                do {
                    try await viewModel.portfolioStore.removeTicker(ticker, from: portfolio.id)
                } catch {
                    print("[PortfolioDetailEditor] ❌ Remove \(ticker) failed: \(error)")
                }
            }
        }
    }
}

private extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}

#Preview {
    EditPortfolioSheet(viewModel: TrackingViewModel())
        .preferredColorScheme(.dark)
}
