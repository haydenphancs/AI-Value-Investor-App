//
//  MoneyMoveArticleContent.swift
//  ios
//
//  Organism: Main article content containing all sections
//

import SwiftUI

struct MoneyMoveArticleContent: View {
    let article: MoneyMoveArticle
    /// Narration playhead (seconds) when this article's audio is active, else nil (no highlight).
    var activeTime: Double? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxl) {
            // Key highlights
            if !article.keyHighlights.isEmpty {
                MoneyMoveArticleKeyHighlights(highlights: article.keyHighlights)
                    .padding(.horizontal, AppSpacing.lg)
            }

            // Article sections
            VStack(spacing: AppSpacing.lg) {
                ForEach(article.sections) { section in
                    MoneyMoveArticleSectionContent(section: section, activeTime: activeTime)
                }
            }

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
