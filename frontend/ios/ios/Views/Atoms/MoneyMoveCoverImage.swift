//
//  MoneyMoveCoverImage.swift
//  ios
//
//  Atom: a Money Moves cover plate — the generated artwork when we have it, the article's
//  own authored gradient when we don't. The single place an article plate is drawn, shared
//  by the article header, the See-All featured card, the catalog tile and the related tiles.
//
//  WHY AN ATOM WITH A URL API, NOT A MOLECULE TAKING AN ARTICLE:
//  it has to serve MoneyMoveArticle, MoneyMove AND RelatedArticle. The moment it takes
//  `article: MoneyMoveArticle` it becomes a molecule *and* it can only serve one of the
//  three. `BookCoverImage` is the precedent for the shape; the difference is that a book
//  cover resolves its own URL from a compiled title registry, while an article plate's URL
//  arrives at runtime inside the served `content` JSONB — which is precisely what lets new
//  artwork reach installed apps with no App Store release.
//
//  WHY NOT `LessonImageSlot`: that atom composites onto an always-white plate
//  (AppColors.mediaSurface) because Journey art is flat vector on pure #FFFFFF. These
//  plates are full-bleed editorial photographs and illustrations on their own designed
//  grounds — a white plate behind them would be a visible slab, not a mount.
//

import SwiftUI

struct MoneyMoveCoverImage: View {
    /// Public money-moves-images URL. Nil, empty, or non-http all fall back to the gradient.
    let url: String?
    /// The article's authored `heroGradientColors` (hex, no `#`). The fallback is deliberately
    /// the EXACT look these surfaces had before artwork existed, so a missing or failed plate
    /// degrades to the old design rather than to a hole.
    let gradientColors: [String]
    var cornerRadius: CGFloat = AppCornerRadius.large
    /// Nil means "fill whatever frame the parent gave me" — the article header sets its own
    /// height. The tiles pass 16/9.
    var aspectRatio: CGFloat? = nil
    /// The procedural grain that has always sat over the gradient. Kept for the fallback and
    /// dropped over real artwork, where it is just noise on a photograph.
    var showsGrainOnFallback: Bool = true

    private var remoteURL: URL? {
        guard let url, url.hasPrefix("http") else { return nil }
        return URL(string: url)
    }

    var body: some View {
        content
            .modifier(OptionalAspectRatio(ratio: aspectRatio))
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
            // The hairline is load-bearing in LIGHT mode and only there: eight of the
            // thirteen plates have a near-white ground, and a white-on-#F4F5F8 card has no
            // luminance edge to separate it from the page. `AppColors.cardEdge` is alpha 0
            // in dark by design, where the plate's own darkness does the work.
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius)
                    .strokeBorder(AppColors.cardEdge, lineWidth: 0.5)
            )
            // No alt text exists anywhere in the content JSON, so any label here would be
            // invented. The headline sits directly beside every one of these and is already
            // read aloud. Same call, same reason, as LessonImageSlot.
            .accessibilityHidden(true)
    }

    @ViewBuilder
    private var content: some View {
        if let remoteURL {
            AsyncImage(url: remoteURL) { phase in
                if let image = phase.image {
                    image.resizable().aspectRatio(contentMode: .fill)
                } else {
                    // Loading AND error both fall back, matching BookCoverImage and
                    // TrendingThemeTile. A cold or offline launch therefore shows exactly
                    // today's UI instead of a placeholder that flashes on every scroll.
                    fallback
                }
            }
        } else {
            fallback
        }
    }

    private var fallback: some View {
        ZStack {
            LinearGradient(
                colors: resolvedGradient,
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            if showsGrainOnFallback {
                GrainyTextureOverlay()
            }
        }
    }

    /// A blob that lost its gradient would otherwise render an empty `LinearGradient`, which
    /// draws nothing at all — a transparent hole rather than a fallback.
    private var resolvedGradient: [Color] {
        let mapped = gradientColors.filter { !$0.isEmpty }.map { Color(hex: $0) }
        guard mapped.count >= 2 else {
            return [AppColors.cardBackground, AppColors.cardBackgroundNested]
        }
        return mapped
    }
}

/// `.aspectRatio(nil, contentMode:)` is not expressible inline, and applying the modifier
/// unconditionally would force a ratio on a caller that sets its own height.
///
/// ⚠️ `.fit`, NOT `.fill`, and the difference is visible rather than theoretical. `.fill`
/// sizes the view to COVER the proposed space, so inside `MoneyMoveCard`'s `.frame(width: 200)`
/// the plate grew wider than the tile and hung out past its rounded corners on both sides.
/// `.fit` derives the height from the offered width, which is what a banner wants; the inner
/// image still uses `.fill` + `clipShape` so it crops rather than letterboxes.
private struct OptionalAspectRatio: ViewModifier {
    let ratio: CGFloat?

    func body(content: Content) -> some View {
        if let ratio {
            content.aspectRatio(ratio, contentMode: .fit)
        } else {
            content
        }
    }
}

#Preview("With artwork") {
    VStack(spacing: AppSpacing.lg) {
        MoneyMoveCoverImage(
            url: "https://example.com/nonexistent.jpg",
            gradientColors: ["DC2626", "991B1B", "7F1D1D"],
            aspectRatio: 16 / 9
        )
        MoneyMoveCoverImage(
            url: nil,
            gradientColors: ["059669", "047857", "064E3B"],
            aspectRatio: 16 / 9
        )
        MoneyMoveCoverImage(url: nil, gradientColors: [], aspectRatio: 16 / 9)
    }
    .padding()
    .background(AppColors.background)
}
