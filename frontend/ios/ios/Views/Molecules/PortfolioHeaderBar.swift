//
//  PortfolioHeaderBar.swift
//  ios
//
//  Molecule: row at the top of the Assets tab that shows the active
//  portfolio's name on the left (tap → portfolio switcher) and a "..."
//  management menu on the right (sort, switch, new, edit).
//
//  Replaces the old standalone Sort button. Modeled on Apple Stocks: the
//  left-side "Holdings ⌄" chip switches portfolios in one tap; the right-
//  side ellipsis is the catch-all management surface.
//

import SwiftUI

struct PortfolioHeaderBar: View {
    @ObservedObject var viewModel: TrackingViewModel

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            portfolioPicker
            Spacer()
            optionsMenu
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Left: portfolio switcher

    private var portfolioPicker: some View {
        Menu {
            ForEach(viewModel.portfolioStore.portfolios) { portfolio in
                Button {
                    viewModel.setActivePortfolio(portfolio.id)
                } label: {
                    if portfolio.id == viewModel.portfolioStore.activePortfolioId {
                        Label(portfolio.name, systemImage: "checkmark")
                    } else {
                        Text(portfolio.name)
                    }
                }
            }

            if !viewModel.portfolioStore.portfolios.isEmpty {
                Divider()
            }

            Button {
                viewModel.openNewPortfolioSheet()
            } label: {
                Label("New Portfolio", systemImage: "plus")
            }
        } label: {
            HStack(spacing: AppSpacing.xs) {
                Text(viewModel.portfolioStore.activePortfolio?.name ?? "Holdings")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)

                Image(systemName: "chevron.down")
                    .font(AppTypography.iconXS).fontWeight(.semibold)
                    .foregroundColor(AppColors.textSecondary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    // MARK: - Right: management menu

    private var optionsMenu: some View {
        Menu {
            // Sort
            Menu {
                Picker("Sort", selection: sortOptionBinding) {
                    ForEach(AssetSortOption.allCases, id: \.self) { option in
                        Text(option.displayName).tag(option)
                    }
                }

                Divider()

                Button {
                    viewModel.toggleSort()
                } label: {
                    Label(
                        viewModel.sortAscending ? "Descending" : "Ascending",
                        systemImage: viewModel.sortAscending ? "arrow.down" : "arrow.up"
                    )
                }
            } label: {
                Label("Sort By", systemImage: "arrow.up.arrow.down")
            }

            Divider()

            Button {
                viewModel.openNewPortfolioSheet()
            } label: {
                Label("New Portfolio", systemImage: "plus")
            }

            Button {
                viewModel.openEditPortfolioSheet()
            } label: {
                Label("Edit Portfolios", systemImage: "pencil")
            }
        } label: {
            MoreOptionsButton()
        }
    }

    // MARK: - Bindings

    /// Two-way binding so the SwiftUI Picker can mutate the VM's persisted
    /// sort option through the existing `selectSortOption` setter.
    private var sortOptionBinding: Binding<AssetSortOption> {
        Binding(
            get: { viewModel.sortOption },
            set: { viewModel.selectSortOption($0) }
        )
    }
}

#Preview {
    PortfolioHeaderBar(viewModel: TrackingViewModel())
        .padding(.vertical)
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
