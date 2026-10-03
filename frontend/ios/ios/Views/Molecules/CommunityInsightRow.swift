//
//  CommunityInsightRow.swift
//  ios
//
//  Molecule: Community insight/comment row with user info and engagement
//

import SwiftUI

struct CommunityInsightRow: View {
    let insight: CommunityInsight
    var onLike: (() -> Void)?
    var onComment: (() -> Void)?
    var onShare: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // User info header
            HStack(spacing: AppSpacing.sm) {
                UserAvatar(
                    name: insight.userName,
                    imageName: insight.userAvatarName,
                    size: 40
                )

                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: AppSpacing.sm) {
                        Text(insight.userName)
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.textPrimary)

                        Text(insight.timeAgo)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }
                }

                Spacer()
            }

            // Comment text
            Text(insight.comment)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)

            // Engagement buttons
            HStack(spacing: AppSpacing.xl) {
                // Like button
                Button(action: {
                    onLike?()
                }) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "heart")
                            .font(AppTypography.iconSmall)
                        Text("\(insight.likesCount)")
                            .font(AppTypography.labelSmall)
                    }
                    .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())

                // Comment button
                Button(action: {
                    onComment?()
                }) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "bubble.right")
                            .font(AppTypography.iconSmall)
                        Text("\(insight.commentsCount)")
                            .font(AppTypography.labelSmall)
                    }
                    .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())

                // Share button
                Button(action: {
                    onShare?()
                }) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "arrowshape.turn.up.right")
                            .font(AppTypography.iconSmall)
                        Text("Share")
                            .font(AppTypography.labelSmall)
                    }
                    .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())

                Spacer()
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
    }
}

#Preview {
    // A neutral layout sample, built here so nothing like it ships in a release build:
    // a placeholder user, no investor names, no endorsement of the product.
    ScrollView {
        CommunityInsightRow(
            insight: CommunityInsight(
                userName: "Preview User",
                userAvatarName: "",
                postedAt: Date().addingTimeInterval(-7200),
                comment: "Sample comment text, long enough to wrap onto a second line so the row's layout can be checked.",
                likesCount: 3,
                commentsCount: 1
            )
        )
        .padding()
    }
    .background(AppColors.background)
}
