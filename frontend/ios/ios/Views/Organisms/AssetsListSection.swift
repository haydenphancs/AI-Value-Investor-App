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
    var onSortTapped: (() -> Void)?
    var onAssetTapped: ((TrackedAsset) -> Void)?
    var onRemoveAsset: ((TrackedAsset) -> Void)?

    var body: some View {
        VStack(spacing: 0) {
            // Sort Button Row
            HStack {
                SortButton(onTap: onSortTapped)
                Spacer()
            }
            .padding(.horizontal, AppSpacing.lg)

            // Assets List with swipe-to-delete.
            // InsetGroupedListStyle adds its own ~20pt top inset we can't
            // control; negative top padding cancels it so the gap below the
            // Sort row visually matches the 8pt the parent adds above it.
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
            // Negative top padding cancels InsetGroupedListStyle's built-in
            // top inset so the gap under "Sort" matches the gap above it.
            // Bottom is left untouched — negative padding there leaks hit-
            // testing into the next section and freezes the outer ScrollView.
            .padding(.top, -AppSpacing.md)
            .frame(height: CGFloat(assets.count) * 72 + 8)
        }
    }
}

#Preview {
    ScrollView {
        AssetsListSection(assets: TrackedAsset.sampleData)
    }
    .background(AppColors.background)
}
