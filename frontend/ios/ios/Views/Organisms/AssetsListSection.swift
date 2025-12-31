//
//  AssetsListSection.swift
//  ios
//
//  Organism: Assets list with sort and add functionality
//

import SwiftUI

struct AssetsListSection: View {
    let assets: [TrackedAsset]
    var onSortTapped: (() -> Void)?
    var onAddTapped: (() -> Void)?
    var onAssetTapped: ((TrackedAsset) -> Void)?

    var body: some View {
        VStack(spacing: 0) {
            // Sort Button Row
            HStack {
                SortButton(onTap: onSortTapped)
                Spacer()
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.bottom, AppSpacing.md)

            // Assets List
            VStack(spacing: 0) {
                ForEach(assets) { asset in
                    AssetRow(asset: asset) {
                        onAssetTapped?(asset)
                    }

                    // Divider between items
                    if asset.id != assets.last?.id {
                        Divider()
                            .background(AppColors.cardBackgroundLight)
                            .padding(.leading, AppSpacing.lg)
                    }
                }
            }
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
            .padding(.horizontal, AppSpacing.lg)

            // Add New Button
            AddAssetButton(onTap: onAddTapped)
                .padding(.top, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        AssetsListSection(assets: TrackedAsset.sampleData)
    }
    .background(AppColors.background)
}
