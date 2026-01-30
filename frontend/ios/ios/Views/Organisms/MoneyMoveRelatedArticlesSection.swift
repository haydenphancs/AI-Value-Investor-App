//
//  MoneyMoveRelatedArticlesSection.swift
//  ios
//
//  Organism: Related articles horizontal scroll section
//

import SwiftUI

struct MoneyMoveRelatedArticlesSection: View {
    let articles: [RelatedArticle]
    var onArticleTapped: ((RelatedArticle) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "newspaper.fill")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(AppColors.accentCyan)

                Text("Related Articles")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)
            }
            .padding(.horizontal, AppSpacing.lg)

            // Horizontal scroll
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.md) {
                    ForEach(articles) { article in
                        RelatedMoneyMoveCard(
                            article: article,
                            onTap: { onArticleTapped?(article) }
                        )
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

#Preview {
    MoneyMoveRelatedArticlesSection(
        articles: [
            RelatedArticle(
                title: "The FTX Collapse",
                subtitle: "What the failure of crypto's top exchange tells us.",
                category: .valueTraps,
                readTimeMinutes: 14,
                viewCount: "2.8M",
                gradientColors: ["DC2626", "991B1B"]
            ),
            RelatedArticle(
                title: "How Amazon Built Its Moat",
                subtitle: "The strategy behind unstoppable dominance.",
                category: .blueprints,
                readTimeMinutes: 12,
                viewCount: "3.1M",
                gradientColors: ["059669", "047857"]
            ),
            RelatedArticle(
                title: "How AI Is Revolutionizing Stock Market",
                subtitle: "From pattern recognition to predictive analytics.",
                category: .blueprints,
                readTimeMinutes: 16,
                viewCount: "1.9M",
                gradientColors: ["7C3AED", "5B21B6"]
            )
        ]
    )
    .padding(.vertical)
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
