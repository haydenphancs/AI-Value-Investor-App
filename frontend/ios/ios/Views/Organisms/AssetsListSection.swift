//
//  AssetsListSection.swift
//  ios
//
//  Organism: Assets list with sort, swipe-to-delete, and add functionality
//  Matches the "Your Tickers" sheet design from UpdatesView
//

import SwiftUI

struct AssetsListSection: View {
    let assets: [TrackedAsset]
    var onAssetTapped: ((TrackedAsset) -> Void)?
    var onRemoveAsset: ((TrackedAsset) -> Void)?
    /// Long-press path that fully removes the ticker from the master watchlist
    /// and every portfolio (vs the swipe path which is portfolio-only).
    var onRemoveFromAll: ((TrackedAsset) -> Void)?

    var body: some View {
        // Assets List with swipe-to-delete. The PortfolioHeaderBar above
        // already shows the active portfolio name + "..." menu (which is where
        // sorting now lives); this section is just the rows.
        // InsetGroupedListStyle adds its own ~20pt top inset we can't
        // control; negative top padding cancels it so the gap below the
        // PortfolioHeaderBar visually matches the gap above it.
        List {
            Section {
                ForEach(Array(assets.enumerated()), id: \.element.id) { index, asset in
                    AssetRow(asset: asset) {
                        onAssetTapped?(asset)
                    }
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        Button(role: .destructive) {
                            onRemoveAsset?(asset)
                        } label: {
                            Image(systemName: "trash.fill")
                        }
                    }
                    .contextMenu {
                        if onRemoveFromAll != nil {
                            Button(role: .destructive) {
                                onRemoveFromAll?(asset)
                            } label: {
                                Label("Remove from all portfolios", systemImage: "trash.slash")
                            }
                        }
                    }
                    .listRowBackground(AppColors.cardBackground)
                    .listRowSeparatorTint(AppColors.cardBackgroundLight)
                    .listRowSeparator(index == assets.count - 1 ? .hidden : .automatic)
                    .listRowInsets(EdgeInsets(top: 0, leading: AppSpacing.lg, bottom: 0, trailing: AppSpacing.lg))
                }
            }
        }
        .listStyle(InsetGroupedListStyle())
        .listSectionSpacing(AppSpacing.sm)
        .scrollContentBackground(.hidden)
        .scrollDisabled(true)
        .padding(.top, -AppSpacing.md)
        .frame(height: CGFloat(assets.count) * 72 + 8)
    }
}

#Preview {
    ScrollView {
        AssetsListSection(assets: TrackedAsset.sampleData)
    }
    .background(AppColors.background)
}
