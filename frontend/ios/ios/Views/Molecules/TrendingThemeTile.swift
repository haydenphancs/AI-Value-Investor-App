//
//  TrendingThemeTile.swift
//  ios
//
//  Molecule: one tile in the "Emerging Frontiers" grid — the theme's remote hero
//  image as a full-bleed background across the top ~2/3 of the card (with an
//  accent-gradient fallback), the sign-coloured change chip overlaid on the image,
//  and the theme title + stock count on the card surface below.
//

import SwiftUI

struct TrendingThemeTile: View {
    let theme: TrendingTheme
    var onTap: (() -> Void)? = nil

    /// Height of the full-bleed image band. With the ~46pt text band below, the
    /// image occupies roughly two-thirds of the card, sitting above the title.
    private let imageHeight: CGFloat = 116

    var body: some View {
        Button { onTap?() } label: {
            VStack(spacing: 0) {
                imageBand
                textBand
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(AppColors.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: 15, style: .continuous))
            // Was a hand-rolled `textPrimary.opacity(0.05)` hairline — a third of the
            // app's edge in light, and the only Home card that had one at all. Now the
            // shared token, so the whole screen agrees: 14% ink in light, nothing in
            // dark (it previously kept a white 5% line there).
            .cardBorder(cornerRadius: 15)
        }
        .buttonStyle(.plain)
    }

    // MARK: - Image band (full-bleed hero across the top of the card)

    private var imageBand: some View {
        ZStack(alignment: .topTrailing) {
            themeImage

            // Overlaid on the image, top-right. A SOLID sign-coloured capsule with
            // white text (not a low-opacity tint) so it stays legible on any photo.
            // Hidden when the backend had no resolvable quotes (empty text).
            if !theme.changeText.isEmpty {
                Text(theme.changeText)
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(AppColors.textOnAccent)
                    .padding(.horizontal, 9)
                    .padding(.vertical, 4)
                    .background(
                        // FILL tokens, opaque. `bullish`/`bearish` are text-safe tokens —
                        // white on `bullish` #22C55E is 2.28:1 opaque and 2.60:1 at the old
                        // 0.92, and this capsule sits on a PHOTO, so there is no surface
                        // whose luminance can be assumed. Opaque `gainFill`/`lossFill`
                        // measure 5.42 / 5.55 under this ink regardless of the image.
                        Capsule().fill(theme.isPositive ? AppColors.gainFill : AppColors.lossFill)
                    )
                    .shadow(color: AppColors.shadowKey, radius: 3, y: 1)
                    .padding(10)
            }
        }
        .frame(height: imageHeight)
        .frame(maxWidth: .infinity)
        .clipped()
    }

    // MARK: - Text band (title + stock count on the card surface, below the image)

    private var textBand: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(theme.title)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(2)
                .multilineTextAlignment(.leading)

            Text(theme.count)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.top, 10)
        .padding(.bottom, 12)
    }

    // MARK: - Card image (remote, with an accent-gradient fallback)

    /// The theme's remote Supabase hero when `imageUrl` is a valid http(s) URL;
    /// otherwise — nil/empty/loading/error — an accent gradient so the tile never
    /// shows an empty hole. Fills the image band, cover-cropped.
    @ViewBuilder private var themeImage: some View {
        if let s = theme.imageUrl, s.hasPrefix("http"), let url = URL(string: s) {
            AsyncImage(url: url) { phase in
                if let image = phase.image {
                    image.resizable().aspectRatio(contentMode: .fill)
                } else {
                    accentFallback   // loading + error both fall back
                }
            }
        } else {
            accentFallback
        }
    }

    private var accentFallback: some View {
        LinearGradient(
            colors: [theme.accent.opacity(0.9), theme.accent.opacity(0.32)],
            startPoint: .topLeading, endPoint: .bottomTrailing
        )
    }
}

#Preview {
    LazyVGrid(columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)], spacing: 12) {
        ForEach(MockHomeRepository.themes) { TrendingThemeTile(theme: $0) }
    }
    .padding()
    .background(AppColors.background)
}
