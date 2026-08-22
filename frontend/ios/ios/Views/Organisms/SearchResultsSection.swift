//
//  SearchResultsSection.swift
//  ios
//
//  Organism: the LIVE ticker/company results for whatever is currently typed.
//
//  Split out from RecentSearchesSection, which used to render these under a "Recent Searches"
//  heading — one array doing two unrelated jobs, which is why the history never existed.
//

import SwiftUI

struct SearchResultsSection: View {
    let items: [SearchResultItem]
    var onItemTapped: ((SearchResultItem) -> Void)?

    var body: some View {
        // No header and no empty state: while the user is typing, an empty result set is
        // transient (a debounce in flight, a half-typed symbol) and a "no results" slab
        // flashing between keystrokes reads as breakage. The Ask Cay AI row above is always
        // there, so the screen is never actually empty.
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                Text("Results")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)
                    .padding(.horizontal, AppSpacing.lg)

                VStack(spacing: 0) {
                    ForEach(items) { item in
                        SearchResultRow(item: item, onTap: { onItemTapped?(item) })

                        if item.id != items.last?.id {
                            Divider()
                                .overlay(AppColors.cardBackgroundLight.opacity(0.5))
                        }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

#Preview {
    ScrollView {
        SearchResultsSection(items: SearchResultItem.sampleData)
    }
    .background(AppColors.background)
}
