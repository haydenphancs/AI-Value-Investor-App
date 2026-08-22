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
//  Mirrors `AssetRow`'s geometry — 80pt ticker block, sparkline, trailing price — so the real
//  rows replace these without the list jumping.
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
                bar(width: 70, height: 10)
            }
            .frame(width: 80, alignment: .leading)

            bar(width: nil, height: 32)

            Spacer(minLength: AppSpacing.md)

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
