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
                            .font(AppTypography.calloutBold)
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
                            .font(.system(size: 14))
                        Text("\(insight.likesCount)")
                            .font(AppTypography.footnote)
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
                            .font(.system(size: 14))
                        Text("\(insight.commentsCount)")
                            .font(AppTypography.footnote)
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
                            .font(.system(size: 14))
                        Text("Share")
                            .font(AppTypography.footnote)
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
                .fill(AppColors.cardBackground)
        )
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.md) {
            ForEach(CommunityInsight.mockInsights) { insight in
                CommunityInsightRow(insight: insight)
            }
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
