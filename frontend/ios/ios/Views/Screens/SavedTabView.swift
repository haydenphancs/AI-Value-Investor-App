//
//  SavedTabView.swift
//  ios
//
//  Saved tab content view within the Learn/Wiser section
//

import SwiftUI

struct SavedTabView: View {
    @State private var selectedFilter: SavedFilterType = .all
    @State private var savedItems: [SavedItem] = SavedItem.sampleData
    @State private var storageInfo: StorageInfo = StorageInfo.sampleData

    var body: some View {
        ScrollView(showsIndicators: false) {
            VStack(spacing: AppSpacing.xxl) {
                // Saved items section with filters
                SavedItemsSection(
                    items: savedItems,
                    selectedFilter: $selectedFilter,
                    onItemAction: handleItemAction,
                    onItemMoreOptions: handleItemMoreOptions,
                    onFilterChange: handleFilterChange
                )
                .padding(.top, AppSpacing.md)

                // Storage card
                StorageCard(
                    storageInfo: storageInfo,
                    onUpgrade: handleUpgrade
                )
                .padding(.horizontal, AppSpacing.lg)

                // Bottom padding for tab bar
                Color.clear.frame(height: AppSpacing.xxxl)
            }
        }
        .refreshable {
            await refreshData()
        }
    }

    // MARK: - Action Handlers
    private func handleItemAction(_ item: SavedItem) {
        print("Action on item: \(item.title)")
    }

    private func handleItemMoreOptions(_ item: SavedItem) {
        print("More options for: \(item.title)")
    }

    private func handleFilterChange(_ filter: SavedFilterType) {
        print("Filter changed to: \(filter.rawValue)")
    }

    private func handleUpgrade() {
        print("Upgrade storage tapped")
    }

    private func refreshData() async {
        try? await Task.sleep(nanoseconds: 800_000_000)
        // Reload data here
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        SavedTabView()
    }
    .preferredColorScheme(.dark)
}
