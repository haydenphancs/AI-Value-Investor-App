//
//  RelatedMoneyMoveCard.swift
//  ios
//
//  Molecule: Compact card for related articles
//

import SwiftUI

struct RelatedMoneyMoveCard: View {
    let article: RelatedArticle
    var onTap: (() -> Void)?

    /// The card is a fixed 200pt wide, so the cover plate's ratio is derivable rather than
    /// guessed — and the two can never drift, which is what makes passing a ratio safe below.
    private static let coverWidth: CGFloat = 200
    private static let coverHeight: CGFloat = 80

    var body: some View {
        Button(action: { onTap?() }) {
            VStack(alignment: .leading, spacing: 0) {
                // Header: the referenced article's own cover plate when the seeder found one
                // (stamped from a title -> card-url map across every article, so no runtime
                // lookup here), otherwise the gradient + category glyph this always drew.
                ZStack(alignment: .topLeading) {
                    // ⚠️ `.frame(...)` ALONE IS NOT ENOUGH HERE — `.clipped()` is load-bearing,
                    // and leaving it off is what shipped as "the title got cut off".
                    //
                    // The plate's inner image is `.aspectRatio(contentMode: .fill)`, which
                    // reports a layout size that COVERS its proposal — 200x112 for a 16:9
                    // source asked for 200x80. The atom's own `clipShape` then clips to THAT
                    // oversized bound, i.e. does nothing, because `.aspectRatio(_:contentMode:)`
                    // reports its CHILD's size rather than the ratio-derived one. A bare
                    // `.frame(height:)` then merely CENTRES the oversized plate in the 80pt
                    // slot, so it bleeds ~16pt past both edges.
                    //
                    // The bottom bleed lands under the title, which draws on top of it — so the
                    // headline was legible over a dark plate and invisible over a light one.
                    // "The Home Depot vs. Lowe's" (white paint cans) vanished; "AMD vs. Intel"
                    // (dark CPU photo) did not, which is exactly why it read as a truncation
                    // bug rather than an overlap.
                    //
                    // `.clipped()` cuts to the frame's real 200x80 whatever the child reports.
                    // The other four callers of this atom get away without it only because they
                    // ask for 16/9 from 16:9 artwork, where the overflow happens to be zero.
                    MoneyMoveCoverImage(
                        url: article.imageCardUrl,
                        gradientColors: article.gradientColors,
                        cornerRadius: 0
                    )
                    .frame(width: Self.coverWidth, height: Self.coverHeight)
                    .clipped()

                    // The glyph is redundant once there is a picture, and its white-on-
                    // white.opacity(0.2) circle is unreadable over a light-ground plate.
                    if article.imageCardUrl == nil {
                        ZStack {
                            Circle()
                                .fill(Color.white.opacity(0.2))
                                .frame(width: 32, height: 32)

                            Image(systemName: article.category.iconName)
                                .font(AppTypography.iconSmall).fontWeight(.semibold)
                                .foregroundColor(AppColors.textOnAccent)
                        }
                        .padding(AppSpacing.md)
                    }
                }

                // Content
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    Text(article.title)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(2)

                    Text(article.subtitle)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(2)

                    Spacer(minLength: 0)

                    // Meta
                    HStack(spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.xxs) {
                            Image(systemName: "clock")
                                .font(AppTypography.iconTiny).fontWeight(.medium)
                            Text("\(article.readTimeMinutes) min")
                                .font(AppTypography.caption)
                        }
                        .foregroundColor(AppColors.textMuted)

                        if !article.viewCount.isEmpty {
                            HStack(spacing: AppSpacing.xxs) {
                                Image(systemName: "eye")
                                    .font(AppTypography.iconTiny).fontWeight(.medium)
                                Text(article.viewCount)
                                    .font(AppTypography.caption)
                            }
                            .foregroundColor(AppColors.textMuted)
                        }
                    }
                }
                .padding(AppSpacing.md)
            }
            // Height is a FLOOR, not a fixed size. See the header of RelatedTickerCard.swift
            // for the full rationale: a `.frame(height:)` centres an oversized child, so text
            // that outgrows the box bleeds off the top AND bottom edges. `maxHeight: .infinity`
            // lets the card take the height the parent HStack resolves, which keeps interior
            // Spacers working (so nothing moves at the default content size) and keeps every
            // card in the row the same height. Parent uses `HStack(alignment: .top)` to match.
            //
            // `.top` is required: the 80pt cover-image header must stay flush to the top edge.
            // 220, not 200. The floor is what the row actually resolves to — the horizontal
            // ScrollView proposes a definite height rather than nil, so the card sits ON the
            // minimum instead of growing to its ideal. At 200 the interior had 96pt for a
            // 2-line title + 2-line subtitle + the meta row, which needs ~102pt, so SwiftUI
            // truncated the TITLE to one line: "The Home Depot vs. Lo…". That is the second
            // half of the same TestFlight report — the headline was clipped by the plate AND
            // cut short by the box.
            .frame(minWidth: Self.coverWidth, maxWidth: Self.coverWidth,
                   minHeight: 220, maxHeight: .infinity, alignment: .top)
            .cardSurface(cornerRadius: AppCornerRadius.large)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: AppSpacing.md) {
            RelatedMoneyMoveCard(
                article: RelatedArticle(
                    title: "The FTX Collapse",
                    subtitle: "What the failure tells us about the future.",
                    category: .valueTraps,
                    readTimeMinutes: 14,
                    viewCount: "2.8M",
                    gradientColors: ["DC2626", "991B1B"]
                )
            )

            RelatedMoneyMoveCard(
                article: RelatedArticle(
                    title: "How Amazon Built Its Moat",
                    subtitle: "The strategy behind unstoppable dominance.",
                    category: .blueprints,
                    readTimeMinutes: 12,
                    viewCount: "3.1M",
                    gradientColors: ["059669", "047857"]
                )
            )
        }
        .padding()
    }
    .background(AppColors.background)
}
