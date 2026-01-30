//
//  ArticleCommentCard.swift
//  ios
//
//  Molecule: Comment card with author info and engagement
//

import SwiftUI

struct ArticleCommentCard: View {
    let comment: ArticleComment
    var onLikeTapped: (() -> Void)?
    var onReplyTapped: (() -> Void)?
    var onAuthorTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header: Author info and time
            HStack(spacing: AppSpacing.sm) {
                // Avatar
                Button(action: { onAuthorTapped?() }) {
                    ArticleAuthorAvatar(
                        name: comment.authorName,
                        imageName: comment.authorAvatar,
                        size: 36,
                        showVerifiedBadge: comment.isVerified
                    )
                }
                .buttonStyle(PlainButtonStyle())

                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: AppSpacing.xs) {
                        Text(comment.authorName)
                            .font(AppTypography.bodyBold)
                            .foregroundColor(AppColors.textPrimary)

                        if comment.isVerified {
                            Image(systemName: "checkmark.seal.fill")
                                .font(.system(size: 10))
                                .foregroundColor(AppColors.primaryBlue)
                        }
                    }

                    Text(comment.timeAgo)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Spacer()

                // More options
                Button(action: {}) {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 16))
                        .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())
            }

            // Comment content
            Text(comment.content)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)

            // Engagement row
            HStack(spacing: AppSpacing.xl) {
                // Like button
                Button(action: { onLikeTapped?() }) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "heart")
                            .font(.system(size: 14, weight: .medium))
                        Text("\(comment.likeCount)")
                            .font(AppTypography.caption)
                    }
                    .foregroundColor(AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())

                // Reply button
                Button(action: { onReplyTapped?() }) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "bubble.left")
                            .font(.system(size: 14, weight: .medium))
                        Text("\(comment.replyCount) replies")
                            .font(AppTypography.caption)
                    }
                    .foregroundColor(AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())

                Spacer()
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ArticleCommentCard(
            comment: ArticleComment(
                authorName: "Alex Johnson",
                authorAvatar: nil,
                content: "Excellent breakdown of the current DeFi landscape! The data on portfolio fragility suggests wealth creation through early adoption needs more critical analysis.",
                postedAt: Calendar.current.date(byAdding: .hour, value: -5, to: Date())!,
                likeCount: 47,
                replyCount: 8,
                isVerified: false
            )
        )

        ArticleCommentCard(
            comment: ArticleComment(
                authorName: "Maya Patel",
                authorAvatar: nil,
                content: "As a traditional banker transitioning to fintech, this article perfectly captures the challenges we face.",
                postedAt: Calendar.current.date(byAdding: .hour, value: -12, to: Date())!,
                likeCount: 32,
                replyCount: 3,
                isVerified: true
            )
        )
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
