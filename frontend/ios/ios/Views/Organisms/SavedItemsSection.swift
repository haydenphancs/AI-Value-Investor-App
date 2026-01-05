//
//  SavedItemsSection.swift
//  ios
//
//  Organism: Section displaying filtered list of saved items
//

import SwiftUI

struct SavedItemsSection: View {
    let items: [SavedItem]
    @Binding var selectedFilter: SavedFilterType
    var onItemAction: ((SavedItem) -> Void)?
    var onItemMoreOptions: ((SavedItem) -> Void)?
    var onFilterChange: ((SavedFilterType) -> Void)?

    private var filteredItems: [SavedItem] {
        switch selectedFilter {
        case .all:
            return items
        case .books:
            return items.filter { $0.type == .book }
        case .concepts:
            return items.filter { $0.type == .concept }
        case .reports:
            return items.filter { $0.type == .report }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Filter pills
            SavedFilterRow(
                selectedFilter: $selectedFilter,
                onFilterChange: onFilterChange
            )

            // Items list
            if filteredItems.isEmpty {
                emptyStateView
            } else {
                LazyVStack(spacing: AppSpacing.md) {
                    ForEach(filteredItems) { item in
                        SavedItemCard(
                            item: item,
                            onActionTap: {
                                onItemAction?(item)
                            },
                            onMoreOptions: {
                                onItemMoreOptions?(item)
                            }
                        )
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }

    private var emptyStateView: some View {
        VStack(spacing: AppSpacing.lg) {
            Image(systemName: "bookmark.slash")
                .font(.system(size: 48))
                .foregroundColor(AppColors.textMuted)

            Text("No \(selectedFilter.rawValue) saved")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            Text("Items you save will appear here")
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.xxxl)
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selectedFilter: SavedFilterType = .all

        var body: some View {
            ScrollView {
                SavedItemsSection(
                    items: SavedItem.sampleData,
                    selectedFilter: $selectedFilter
                )
            }
        }
    }

    return PreviewWrapper()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
