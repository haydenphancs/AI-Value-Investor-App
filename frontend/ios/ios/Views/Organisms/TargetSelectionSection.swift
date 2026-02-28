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
            Text("Select Your Target:")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)

            // Search bar
            SearchBar(
                text: $searchText,
                placeholder: "Find a company...",
                onSubmit: onSearchSubmit
            )

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
