//
//  TargetSelectionSection.swift
//  ios
//
//  Organism: Target selection with search bar and quick ticker chips
//

import SwiftUI

struct TargetSelectionSection: View {
    @Binding var searchText: String
    let quickTickers: [QuickTicker]
    var onTickerSelected: ((QuickTicker) -> Void)?
    var onSearchSubmit: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section header
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text("Select Your Target:")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                Text("Choose a company or ticker symbol to analyze")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Search bar
            SearchBar(
                text: $searchText,
                placeholder: "Search stocks, ETFs, or crypto",
                onSubmit: onSearchSubmit
            )

            // Quick ticker chips
            HStack(spacing: AppSpacing.sm) {
                ForEach(quickTickers) { ticker in
                    TickerChip(
                        ticker: ticker,
                        isSelected: searchText.uppercased() == ticker.symbol
                    ) {
                        onTickerSelected?(ticker)
                    }
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    VStack {
        TargetSelectionSection(
            searchText: .constant(""),
            quickTickers: QuickTicker.defaults
        )
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
