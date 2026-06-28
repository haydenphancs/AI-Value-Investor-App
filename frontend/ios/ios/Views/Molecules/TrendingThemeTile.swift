//
//  TrendingThemeTile.swift
//  ios
//
//  Molecule: one tile in the "2026 Trending Themes" grid — an accent icon tile,
//  a green change chip, and the theme title + stock count.
//

import SwiftUI

struct TrendingThemeTile: View {
    let theme: TrendingTheme
    var onTap: (() -> Void)? = nil

    var body: some View {
        Button { onTap?() } label: {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .top) {
                    IconTile(systemName: theme.iconSystemName, accent: theme.accent,
                             size: 42, cornerRadius: 12, tintOpacity: 0.15, iconPointSize: 23)
                    Spacer()
                    TintedTagBadge(text: theme.changeText, color: AppColors.bullish,
                                   backgroundOpacity: 0.12, font: AppTypography.captionEmphasis)
                }

                Spacer(minLength: 12)

                Text(theme.title)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                Text(theme.count)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .padding(.top, 3)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .frame(minHeight: 122)
            .padding(14)
            .background(AppColors.cardBackground)
            .overlay(
                RoundedRectangle(cornerRadius: 15, style: .continuous)
                    .stroke(Color.white.opacity(0.05), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 15, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    LazyVGrid(columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)], spacing: 12) {
        ForEach(MockHomeRepository.themes) { TrendingThemeTile(theme: $0) }
    }
    .padding()
    .background(AppColors.background)
}
