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

    private var gradientColors: [Color] {
        article.gradientColors.map { Color(hex: $0) }
    }

    var body: some View {
        Button(action: { onTap?() }) {
            VStack(alignment: .leading, spacing: 0) {
                // Gradient header
                ZStack(alignment: .topLeading) {
                    // Background gradient
                    LinearGradient(
                        colors: gradientColors,
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                    .frame(height: 80)

                    // Category icon
                    ZStack {
                        Circle()
                            .fill(Color.white.opacity(0.2))
                            .frame(width: 32, height: 32)

                        Image(systemName: article.category.iconName)
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(.white)
                    }
                    .padding(AppSpacing.md)
                }

                // Content
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    Text(article.title)
                        .font(AppTypography.bodyBold)
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
                                .font(.system(size: 10, weight: .medium))
                            Text("\(article.readTimeMinutes) min")
                                .font(AppTypography.caption)
                        }
                        .foregroundColor(AppColors.textMuted)

                        HStack(spacing: AppSpacing.xxs) {
                            Image(systemName: "eye")
                                .font(.system(size: 10, weight: .medium))
                            Text(article.viewCount)
                                .font(AppTypography.caption)
                        }
                        .foregroundColor(AppColors.textMuted)
                    }
                }
                .padding(AppSpacing.md)
            }
            .frame(width: 200, height: 200)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
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
    .preferredColorScheme(.dark)
}
