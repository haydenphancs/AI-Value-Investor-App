//
//  ArticleHighlightCard.swift
//  ios
//
//  Molecule: Key highlight card with icon and description
//

import SwiftUI

struct ArticleHighlightCard: View {
    let highlight: ArticleHighlight

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Icon
            ArticleSectionIcon(icon: highlight.icon, size: 36)

            // Content
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(highlight.title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(highlight.description)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ArticleHighlightCard(
            highlight: ArticleHighlight(
                icon: "building.columns.fill",
                title: "The Alpha",
                description: "As technology becomes ubiquitous, decentralized finance (DeFi) is reshaping how we invest."
            )
        )

        ArticleHighlightCard(
            highlight: ArticleHighlight(
                icon: "chart.line.uptrend.xyaxis",
                title: "Key Trends",
                description: "The pace of banking innovation has never been faster."
            )
        )
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
