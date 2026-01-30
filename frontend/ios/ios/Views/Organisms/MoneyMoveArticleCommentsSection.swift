//
//  MoneyMoveArticleCommentsSection.swift
//  ios
//
//  Organism: Comments section with comment cards and input
//

import SwiftUI

struct MoneyMoveArticleCommentsSection: View {
    let comments: [ArticleComment]
    let totalCount: Int
    var onAddCommentTapped: (() -> Void)?
    var onViewAllTapped: (() -> Void)?
    var onCommentLiked: ((ArticleComment) -> Void)?
    var onCommentReplied: ((ArticleComment) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            HStack {
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "bubble.left.and.bubble.right.fill")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(AppColors.primaryBlue)

                    Text("Comments")
                        .font(AppTypography.title3)
                        .foregroundColor(AppColors.textPrimary)

                    Text("(\(totalCount))")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                // View all button
                if comments.count < totalCount {
                    Button(action: { onViewAllTapped?() }) {
                        Text("View all")
                            .font(AppTypography.calloutBold)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                    .buttonStyle(PlainButtonStyle())
                }
            }

            // Add comment button
            Button(action: { onAddCommentTapped?() }) {
                HStack(spacing: AppSpacing.md) {
                    // Avatar placeholder
                    Circle()
                        .fill(AppColors.cardBackgroundLight)
                        .frame(width: 36, height: 36)
                        .overlay(
                            Image(systemName: "person.fill")
                                .font(.system(size: 14))
                                .foregroundColor(AppColors.textMuted)
                        )

                    Text("Add a comment...")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textMuted)

                    Spacer()

                    Image(systemName: "paperplane")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(AppSpacing.md)
                .background(AppColors.cardBackground)
                .cornerRadius(AppCornerRadius.large)
            }
            .buttonStyle(PlainButtonStyle())

            // Comment cards
            VStack(spacing: AppSpacing.md) {
                ForEach(comments) { comment in
                    ArticleCommentCard(
                        comment: comment,
                        onLikeTapped: { onCommentLiked?(comment) },
                        onReplyTapped: { onCommentReplied?(comment) }
                    )
                }
            }

            // Load more button
            if comments.count < totalCount {
                Button(action: { onViewAllTapped?() }) {
                    HStack {
                        Text("Load more comments")
                            .font(AppTypography.bodyBold)
                            .foregroundColor(AppColors.primaryBlue)

                        Image(systemName: "chevron.down")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundColor(AppColors.primaryBlue)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(AppColors.cardBackground)
                    .cornerRadius(AppCornerRadius.large)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
    }
}

#Preview {
    ScrollView {
        MoneyMoveArticleCommentsSection(
            comments: [
                ArticleComment(
                    authorName: "Alex Johnson",
                    authorAvatar: nil,
                    content: "Excellent breakdown of the current DeFi landscape! The data on portfolio fragility suggests wealth creation through early adoption needs more critical analysis.",
                    postedAt: Calendar.current.date(byAdding: .hour, value: -5, to: Date())!,
                    likeCount: 47,
                    replyCount: 8,
                    isVerified: false
                ),
                ArticleComment(
                    authorName: "Maya Patel",
                    authorAvatar: nil,
                    content: "As a traditional banker transitioning to fintech, this article perfectly captures the challenges we face.",
                    postedAt: Calendar.current.date(byAdding: .hour, value: -12, to: Date())!,
                    likeCount: 32,
                    replyCount: 3,
                    isVerified: true
                )
            ],
            totalCount: 124
        )
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
