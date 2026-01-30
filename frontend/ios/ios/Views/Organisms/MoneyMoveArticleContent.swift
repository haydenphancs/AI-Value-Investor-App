//
//  MoneyMoveArticleContent.swift
//  ios
//
//  Organism: Main article content containing all sections
//

import SwiftUI

struct MoneyMoveArticleContent: View {
    let article: MoneyMoveArticle
    var onAuthorTapped: (() -> Void)?
    var onFollowTapped: (() -> Void)?
    var isFollowing: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxl) {
            // Author row
            ArticleAuthorRow(
                author: article.author,
                showFollowButton: true,
                isFollowing: isFollowing,
                onFollowTapped: onFollowTapped,
                onAuthorTapped: onAuthorTapped
            )
            .padding(.horizontal, AppSpacing.lg)

            // Key highlights
            if !article.keyHighlights.isEmpty {
                MoneyMoveArticleKeyHighlights(highlights: article.keyHighlights)
                    .padding(.horizontal, AppSpacing.lg)
            }

            // Statistics grid
            if !article.statistics.isEmpty {
                ArticleStatisticsGrid(statistics: article.statistics)
                    .padding(.horizontal, AppSpacing.lg)
            }

            // Article sections
            VStack(spacing: AppSpacing.lg) {
                ForEach(article.sections) { section in
                    MoneyMoveArticleSectionContent(section: section)
                }
            }
            .padding(.horizontal, AppSpacing.lg)

            // Comments section
            if !article.comments.isEmpty {
                MoneyMoveArticleCommentsSection(
                    comments: Array(article.comments.prefix(2)),
                    totalCount: article.commentCount
                )
                .padding(.horizontal, AppSpacing.lg)
            }

            // Related articles
            if !article.relatedArticles.isEmpty {
                MoneyMoveRelatedArticlesSection(articles: article.relatedArticles)
            }

            // Bottom padding
            Color.clear
                .frame(height: AppSpacing.xxxl)
        }
    }
}

#Preview {
    ScrollView {
        MoneyMoveArticleContent(article: MoneyMoveArticle.sampleDigitalFinance)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
