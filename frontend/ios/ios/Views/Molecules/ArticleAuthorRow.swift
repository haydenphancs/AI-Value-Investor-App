//
//  ArticleAuthorRow.swift
//  ios
//
//  Molecule: Author information row with avatar, name, and follow button
//

import SwiftUI

struct ArticleAuthorRow: View {
    let author: ArticleAuthor
    var showFollowButton: Bool = true
    var isFollowing: Bool = false
    var onFollowTapped: (() -> Void)?
    var onAuthorTapped: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Avatar
            Button(action: { onAuthorTapped?() }) {
                ArticleAuthorAvatar(
                    name: author.name,
                    imageName: author.avatarName,
                    size: 44,
                    showVerifiedBadge: author.isVerified
                )
            }
            .buttonStyle(PlainButtonStyle())

            // Info
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                HStack(spacing: AppSpacing.xs) {
                    Text(author.name)
                        .font(AppTypography.headline)
                        .foregroundColor(AppColors.textPrimary)

                    if author.isVerified {
                        Image(systemName: "checkmark.seal.fill")
                            .font(.system(size: 12))
                            .foregroundColor(AppColors.primaryBlue)
                    }
                }

                Text(author.title)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            Spacer()

            // Follow button
            if showFollowButton {
                Button(action: { onFollowTapped?() }) {
                    Text(isFollowing ? "Following" : "Follow")
                        .font(AppTypography.captionBold)
                        .foregroundColor(isFollowing ? AppColors.textSecondary : .white)
                        .padding(.horizontal, AppSpacing.md)
                        .padding(.vertical, AppSpacing.sm)
                        .background(
                            Capsule()
                                .fill(isFollowing ? AppColors.cardBackgroundLight : AppColors.primaryBlue)
                        )
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.xl) {
        ArticleAuthorRow(
            author: ArticleAuthor(
                name: "The Alpha",
                avatarName: nil,
                title: "Investment Research",
                isVerified: true,
                followerCount: "45.2k"
            )
        )

        ArticleAuthorRow(
            author: ArticleAuthor(
                name: "Sarah Chen",
                avatarName: nil,
                title: "Market Analyst",
                isVerified: false,
                followerCount: "12.5k"
            ),
            isFollowing: true
        )
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
