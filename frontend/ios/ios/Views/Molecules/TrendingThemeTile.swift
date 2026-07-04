//
//  TrendingThemeTile.swift
//  ios
//
//  Molecule: one tile in the "Emerging Frontiers" grid — a remote theme image
//  (with an accent-gradient fallback), a sign-coloured change chip, and the
//  Next-Wave title + stock count.
//

import SwiftUI

struct TrendingThemeTile: View {
    let theme: TrendingTheme
    var onTap: (() -> Void)? = nil

    private let imageSide: CGFloat = 42

    var body: some View {
        Button { onTap?() } label: {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .top) {
                    themeImage
                    Spacer()
                    // Hidden when the backend had no resolvable quotes (empty text);
                    // an empty capsule would otherwise render. Coloured by sign.
                    if !theme.changeText.isEmpty {
                        TintedTagBadge(
                            text: theme.changeText,
                            color: theme.isPositive ? AppColors.bullish : AppColors.bearish,
                            backgroundOpacity: 0.12, font: AppTypography.captionEmphasis
                        )
                    }
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

    // MARK: - Card image (remote, with an accent-gradient fallback)

    /// A remote Supabase Storage image when `imageUrl` is a valid http(s) URL;
    /// otherwise — nil/empty/loading/error — an accent gradient so the tile never
    /// shows an empty hole. Mirrors `LessonImageSlot`'s remote-image pattern.
    @ViewBuilder private var themeImage: some View {
        if let s = theme.imageUrl, s.hasPrefix("http"), let url = URL(string: s) {
            AsyncImage(url: url) { phase in
                if let image = phase.image {
                    image.resizable().aspectRatio(contentMode: .fill)
                } else {
                    accentFallback   // loading + error both fall back
                }
            }
            .frame(width: imageSide, height: imageSide)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        } else {
            accentFallback
        }
    }

    private var accentFallback: some View {
        RoundedRectangle(cornerRadius: 12, style: .continuous)
            .fill(
                LinearGradient(
                    colors: [theme.accent.opacity(0.9), theme.accent.opacity(0.32)],
                    startPoint: .topLeading, endPoint: .bottomTrailing
                )
            )
            .frame(width: imageSide, height: imageSide)
    }
}

#Preview {
    LazyVGrid(columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)], spacing: 12) {
        ForEach(MockHomeRepository.themes) { TrendingThemeTile(theme: $0) }
    }
    .padding()
    .background(AppColors.background)
}
