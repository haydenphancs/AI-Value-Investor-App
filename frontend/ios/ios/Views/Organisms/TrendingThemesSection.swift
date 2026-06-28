//
//  TrendingThemesSection.swift
//  ios
//
//  Organism: the "2026 Trending Themes" two-column grid of megatrend tiles.
//

import SwiftUI

struct TrendingThemesSection: View {
    let themes: [TrendingTheme]
    var onSeeAll: (() -> Void)? = nil
    var onThemeTap: ((TrendingTheme) -> Void)? = nil

    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12)
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("2026 Trending Themes")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)
                Spacer()
                Button { onSeeAll?() } label: {
                    Text("See All")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .buttonStyle(.plain)
            }

            Text("Tap a megatrend to explore its leaders.")
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
                .padding(.top, 4)
                .padding(.bottom, 13)

            LazyVGrid(columns: columns, spacing: 12) {
                ForEach(themes) { theme in
                    TrendingThemeTile(theme: theme) { onThemeTap?(theme) }
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    TrendingThemesSection(themes: MockHomeRepository.themes)
        .padding(.vertical)
        .background(AppColors.background)
}
