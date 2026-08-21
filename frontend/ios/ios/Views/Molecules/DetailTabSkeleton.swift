//
//  DetailTabSkeleton.swift
//  ios
//
//  Molecule: card-stack placeholder for a detail screen's TAB BODY while its data loads.
//
//  Sibling of `DetailHeaderChartSkeleton`, which covers the price-header + chart region
//  ABOVE the tab bar. Nothing covered the region below it: all five asset-detail screens
//  gate the Overview tab on `if let data = … { … }` with no `else`, so during the load —
//  and for the whole time after a failed load — the tab body rendered literally nothing.
//  The user saw a header, a tab bar, and then blank space with no spinner, no message and
//  no way to tell "still loading" from "this asset has no overview".
//
//  Reuses the `ShimmerEffect` atom so it reads as loading rather than as broken layout.
//

import SwiftUI

struct DetailTabSkeleton: View {

    /// Number of placeholder cards. Overview stacks several sections; a tab with one
    /// section should pass 1 so the skeleton does not over-promise.
    var cardCount: Int = 3

    private var bar: some View {
        RoundedRectangle(cornerRadius: 6, style: .continuous)
            .fill(AppColors.cardBackgroundLight)
    }

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            ForEach(0..<max(1, cardCount), id: \.self) { _ in
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    bar.frame(width: 130, height: 14)
                    HStack(spacing: AppSpacing.lg) {
                        bar.frame(height: 12)
                        bar.frame(height: 12)
                    }
                    bar.frame(height: 12)
                    bar.frame(width: 180, height: 12)
                }
                .padding(AppSpacing.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
                .cardSurface()
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
        .padding(.bottom, 120)
        .shimmer()
        .accessibilityLabel("Loading")
    }
}

#Preview {
    ScrollView { DetailTabSkeleton() }
        .background(AppColors.background)
}
