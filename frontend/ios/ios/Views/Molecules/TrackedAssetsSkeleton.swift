//
//  TrackedAssetsSkeleton.swift
//  ios
//
//  Molecule: placeholder holdings rows shown while the Tracking feed is in flight.
//
//  WHY THIS EXISTS. The Assets tab branched `filteredAssets.isEmpty ? AssetsPlaceholderCard :
//  AssetsListSection`, so for the ~0.5–1s of the first load a user WITH holdings was told
//  "No tickers yet — Add a ticker to start tracking prices". That is not a slow screen, it is a
//  screen stating something false about the user's own money. (Captured on device before this
//  change, under the LoadingOverlay that has since been removed.)
//
//  Mirrors `AssetRow`'s geometry — flexible ticker block, pinned sparkline, trailing price —
//  so the real rows replace these without the list jumping. The two widths are READ FROM
//  `AssetRow` rather than copied: this file previously carried a literal duplicate of its
//  hardcoded 80, and diverged the moment the row was rebalanced.
//

import SwiftUI

struct TrackedAssetsSkeleton: View {
    /// Enough to fill the visible list area without implying a specific holdings count.
    var rowCount: Int = 4

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            ForEach(0..<rowCount, id: \.self) { _ in
                row
            }
        }
        .shimmer()
        // One announcement for the whole block: VoiceOver reading four identical placeholder
        // rows says nothing useful, and the real list replaces this within a second.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading your holdings")
    }

    private var row: some View {
        HStack(spacing: AppSpacing.lg) {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                bar(width: 52, height: 14)
                // nil => fills the column. A fixed 96 was WIDER than the ~80pt the
                // column actually resolves to, so the name placeholder ran straight
                // into the sparkline placeholder -- same fill colour, so the two read
                // as one continuous bar, and then visibly split apart when data landed.
                bar(width: nil, height: 10)
            }
            // Mirrors AssetRow's flexible column and pinned chart — both read from
            // AssetRow's own constants so this can never drift from the row it stands in for.
            .frame(minWidth: AssetRow.tickerColumnMinWidth,
                   maxWidth: .infinity,
                   alignment: .leading)

            bar(width: AssetRow.sparklineWidth, height: 32)

            Spacer(minLength: AppSpacing.sm)

            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                bar(width: 64, height: 14)
                bar(width: 44, height: 10)
            }
        }
        .padding(.vertical, AppSpacing.md)
        .padding(.horizontal, AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
        .cardBorder(cornerRadius: AppCornerRadius.large)
    }

    private func bar(width: CGFloat?, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 4, style: .continuous)
            .fill(AppColors.cardBackgroundLight)
            .frame(width: width, height: height)
            .frame(maxWidth: width == nil ? .infinity : nil)
    }
}

#Preview {
    TrackedAssetsSkeleton()
        .padding(.horizontal, AppSpacing.lg)
        .background(AppColors.background)
}
