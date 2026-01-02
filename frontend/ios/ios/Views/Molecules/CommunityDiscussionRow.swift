//
//  CommunityDiscussionRow.swift
//  ios
//
//  Molecule: Row showing a community discussion post
//

import SwiftUI

struct CommunityDiscussionRow: View {
    let discussion: CommunityDiscussion
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(alignment: .top, spacing: AppSpacing.md) {
                // Avatar
                Circle()
                    .fill(avatarGradient)
                    .frame(width: 40, height: 40)
                    .overlay(
                        Text(discussion.authorName.prefix(1).uppercased())
                            .font(AppTypography.bodyBold)
                            .foregroundColor(.white)
                    )

                // Content
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    // Author and time
                    HStack {
                        Text(discussion.authorName)
                            .font(AppTypography.bodyBold)
                            .foregroundColor(AppColors.textPrimary)

                        Text(discussion.timeAgo)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }

                    // Content
                    Text(discussion.content)
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(3)
                        .multilineTextAlignment(.leading)

                    // Stats
                    HStack(spacing: AppSpacing.lg) {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "bubble.left")
                                .font(.system(size: 12))

                            Text(discussion.formattedReplies)
                                .font(AppTypography.caption)
                        }
                        .foregroundColor(AppColors.textMuted)

                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "heart")
                                .font(.system(size: 12))

                            Text(discussion.formattedLikes)
                                .font(AppTypography.caption)
                        }
                        .foregroundColor(AppColors.textMuted)
                    }
                }

                Spacer()
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(PlainButtonStyle())
    }

    private var avatarGradient: LinearGradient {
        // Generate consistent colors based on author name
        let hash = discussion.authorName.hashValue
        let hue1 = Double(abs(hash) % 360) / 360.0
        let hue2 = Double(abs(hash + 60) % 360) / 360.0

        return LinearGradient(
            colors: [
                Color(hue: hue1, saturation: 0.6, brightness: 0.7),
                Color(hue: hue2, saturation: 0.5, brightness: 0.5)
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ForEach(CommunityDiscussion.sampleData) { discussion in
            CommunityDiscussionRow(discussion: discussion)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
