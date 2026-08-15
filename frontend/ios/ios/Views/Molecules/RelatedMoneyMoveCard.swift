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

    var body: some View {
        Button(action: { onTap?() }) {
            VStack(alignment: .leading, spacing: 0) {
                // Header: the referenced article's own cover plate when the seeder found one
                // (stamped from a title -> card-url map across every article, so no runtime
                // lookup here), otherwise the gradient + category glyph this always drew.
                ZStack(alignment: .topLeading) {
                    MoneyMoveCoverImage(
                        url: article.imageCardUrl,
                        gradientColors: article.gradientColors,
                        cornerRadius: 0
                    )
                    .frame(height: 80)

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
            .frame(width: 200, height: 200)
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
