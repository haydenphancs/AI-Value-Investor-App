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
    var onAddTapped: (() -> Void)?
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
            .padding(.bottom, AppSpacing.md)

            // Assets List with swipe-to-delete
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

                Section {
                    Button(action: { onAddTapped?() }) {
                        HStack {
                            Spacer()
                            Image(systemName: "plus")
                                .font(AppTypography.iconMedium).fontWeight(.semibold)
                                .foregroundColor(AppColors.textMuted)
                            Spacer()
                        }
                    }
                    .listRowBackground(AppColors.cardBackground)
                }
            }
            .listStyle(InsetGroupedListStyle())
            .listSectionSpacing(AppSpacing.sm)
            .scrollContentBackground(.hidden)
            .scrollDisabled(true)
            .frame(height: CGFloat(assets.count) * 72 + 80)
        }
    }
}

#Preview {
    ScrollView {
        AssetsListSection(assets: TrackedAsset.sampleData)
    }
    .background(AppColors.background)
}
