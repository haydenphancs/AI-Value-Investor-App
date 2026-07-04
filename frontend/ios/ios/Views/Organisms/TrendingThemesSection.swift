//
//  TrendingThemesSection.swift
//  ios
//
//  Organism: the "Emerging Frontiers" carousel. Tiles are grouped into columns
//  of two (a vertical pair); the columns scroll horizontally, so one swipe moves
//  a whole column — both stacked cards — at once.
//

import SwiftUI

struct TrendingThemesSection: View {
    let themes: [TrendingTheme]
    var onThemeTap: ((TrendingTheme) -> Void)? = nil

    /// Two themes per column. With the design's 6 themes this is 3 columns.
    private var columns: [[TrendingTheme]] {
        stride(from: 0, to: themes.count, by: 2).map {
            Array(themes[$0 ..< min($0 + 2, themes.count)])
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header (padded; the carousel below bleeds to the screen edges).
            VStack(alignment: .leading, spacing: 0) {
                Text("Emerging Frontiers")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                Text("The industries shaping the next decade")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.top, 4)
                    .padding(.bottom, 13)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, AppSpacing.lg)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 12) {
                    ForEach(columns.indices, id: \.self) { index in
                        VStack(spacing: 12) {
                            ForEach(columns[index]) { theme in
                                TrendingThemeTile(theme: theme) { onThemeTap?(theme) }
                            }
                        }
                        // Each column is one of two that fill the row, so its width
                        // matches the previous two-up card and a swipe advances a
                        // full column (both stacked cards) at a time.
                        .containerRelativeFrame(.horizontal) { length, _ in
                            (length - 2 * AppSpacing.lg - 12) / 2
                        }
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.viewAligned)
            .contentMargins(.horizontal, AppSpacing.lg, for: .scrollContent)
        }
    }
}

#Preview {
    TrendingThemesSection(themes: MockHomeRepository.themes)
        .padding(.vertical)
        .background(AppColors.background)
}
