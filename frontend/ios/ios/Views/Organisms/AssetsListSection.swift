//
//  AssetsListSection.swift
//  ios
//
//  Organism: Assets list with sort, swipe-to-delete, and add functionality
//  Matches the "Your Tickers" sheet design from UpdatesView
//

import SwiftUI

/// Carries the measured natural height of a single AssetRow up to the section.
private struct AssetRowHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

struct AssetsListSection: View {
    let assets: [TrackedAsset]
    var onAssetTapped: ((TrackedAsset) -> Void)?
    var onRemoveAsset: ((TrackedAsset) -> Void)?
    /// Long-press path that fully removes the ticker from the master watchlist
    /// and every portfolio (vs the swipe path which is portfolio-only).
    var onRemoveFromAll: ((TrackedAsset) -> Void)?

    // A `List` has no intrinsic height inside the outer ScrollView's LazyVStack,
    // so the section must size itself explicitly. Instead of a magic per-row
    // guess (the old `* 72 + 8`, which under-measured the real row and let the
    // next section overlap the last card), we measure ONE real AssetRow off
    // screen and multiply. Seeded with a close default so the very first layout
    // pass is already about right.
    @State private var rowHeight: CGFloat = 60

    // Vertical gap reserved below each card (the inter-card gap, and the gap
    // below the last card). Applied via listRowInsets and folded into the
    // self-sizing frame.
    private let rowGap: CGFloat = AppSpacing.sm

    var body: some View {
        // Assets List with swipe-to-delete. The PortfolioHeaderBar above
        // already shows the active portfolio name + "..." menu (which is where
        // sorting now lives); this section is just the rows.
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
                    // AssetRow paints its own rounded card now, so the row
                    // background is clear and there are no separators — each
                    // row reads as a standalone card like AlertCardView. The
                    // bottom inset is the inter-card gap.
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: 0, leading: AppSpacing.lg, bottom: rowGap, trailing: AppSpacing.lg))
                }
            }
        }
        // .plain (not InsetGrouped) so the rounded-card look comes entirely
        // from AssetRow — InsetGrouped imposes its own ~10pt corner radius and
        // section insets we can't control. .plain also makes the self-sizing
        // exact: each cell = measured row height + the bottom gap, no
        // uncontrollable section chrome.
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .scrollDisabled(true)
        // Self-sizing frame: each cell is one card plus its bottom gap. No magic
        // constant, no overlap. The small +xs absorbs any residual .plain inset.
        .frame(height: CGFloat(assets.count) * (rowHeight + rowGap) + AppSpacing.xs)
        // Measure a single real row off screen to drive `rowHeight`.
        .background(rowHeightProbe)
    }

    /// One hidden AssetRow at its natural height, reported via a preference.
    /// Lives in `.background`, so it never affects the List's own layout.
    private var rowHeightProbe: some View {
        AssetRow(asset: assets.first ?? Self.measurementSample)
            .fixedSize(horizontal: false, vertical: true)
            .background(
                GeometryReader { proxy in
                    Color.clear.preference(key: AssetRowHeightKey.self, value: proxy.size.height)
                }
            )
            .opacity(0)
            .allowsHitTesting(false)
            .onPreferenceChange(AssetRowHeightKey.self) { height in
                if height > 1 { rowHeight = height }
            }
    }

    /// Stand-in for the empty case so the probe always has something to measure.
    private static let measurementSample = TrackedAsset(
        ticker: "•",
        companyName: "•",
        price: 0,
        changePercent: 0,
        sparklineData: [],
        assetType: "stock",
        marketCap: nil,
        shares: nil,
        marketValue: nil,
        sector: nil,
        country: nil
    )
}

#Preview {
    ScrollView {
        AssetsListSection(assets: TrackedAsset.sampleData)
    }
    .background(AppColors.background)
}
