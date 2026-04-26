//
//  PortfolioHeaderBar.swift
//  ios
//
//  Molecule: row at the top of the Assets tab. Modeled on Apple Stocks.
//  The left-side "Holdings ⌄" chip is the portfolio surface — switch the
//  active portfolio, create a new one, or open Edit Portfolios. The
//  right-side "..." menu is scoped to the active portfolio (sort + open
//  Manage Tickers to reorder/remove its tickers).
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

            Button {
                viewModel.openEditPortfolioSheet()
            } label: {
                Label("Edit Portfolios", systemImage: "pencil")
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
            // Sort options inline (no nested submenu) so the menu has a
            // single surface — iOS stacks nested submenus with a slight
            // offset that can't be aligned to the parent.
            Section("Sort By") {
                ForEach(AssetSortOption.allCases, id: \.self) { option in
                    Button {
                        viewModel.selectSortOption(option)
                    } label: {
                        if option == viewModel.sortOption {
                            Label(option.displayName, systemImage: "checkmark")
                        } else {
                            Text(option.displayName)
                        }
                    }
                }

                Button {
                    viewModel.toggleSort()
                } label: {
                    Label(
                        viewModel.sortAscending ? "Descending" : "Ascending",
                        systemImage: viewModel.sortAscending ? "arrow.down" : "arrow.up"
                    )
                }
            }

            Divider()

            Button {
                viewModel.openManageTickersSheet()
            } label: {
                Label("Manage Tickers", systemImage: "line.3.horizontal")
            }
            .disabled(viewModel.portfolioStore.activePortfolio == nil)
        } label: {
            MoreOptionsButton()
        }
    }
}

#Preview {
    PortfolioHeaderBar(viewModel: TrackingViewModel())
        .padding(.vertical)
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
