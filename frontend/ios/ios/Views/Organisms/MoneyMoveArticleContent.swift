//
//  MoneyMoveArticleContent.swift
//  ios
//
//  Organism: Main article content containing all sections
//

import SwiftUI
import UIKit

struct MoneyMoveArticleContent: View {
    let article: MoneyMoveArticle
    /// Narration playhead (seconds) when this article's audio is active, else nil (no highlight).
    var activeTime: Double? = nil
    @ObservedObject private var progress = MoneyMovesProgressStore.shared

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

            // End-of-article completion toggle (above Comments). Tap to complete; tap again to
            // mark it unread. Finishing the narration also completes it.
            if !article.slug.isEmpty {
                completionButton
                    .padding(.horizontal, AppSpacing.lg)
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

    private var isCompleted: Bool {
        progress.isCompleted(slug: article.slug)
    }

    /// Mirrors the book core's Complete button, but toggleable: tap once to complete, again to undo.
    private var completionButton: some View {
        Button {
            progress.toggleCompleted(slug: article.slug)
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        } label: {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: isCompleted ? "checkmark.circle.fill" : "checkmark.circle")
                    .font(AppTypography.iconSmall).fontWeight(.semibold)
                Text(isCompleted ? "Completed" : "Mark as Complete")
                    .font(AppTypography.bodyEmphasis)
            }
            .foregroundColor(isCompleted ? AppColors.bullish : .white)
            .frame(maxWidth: .infinity)
            .frame(height: 54)
            .background(
                Group {
                    if isCompleted {
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .strokeBorder(AppColors.bullish, lineWidth: 1.5)
                    } else {
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .fill(AppColors.bullish)
                    }
                }
            )
        }
        .buttonStyle(.plain)
        .animation(.easeInOut(duration: 0.2), value: isCompleted)
    }
}

#Preview {
    ScrollView {
        MoneyMoveArticleContent(article: MoneyMoveArticle.sampleDigitalFinance)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
