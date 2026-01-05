//
//  FollowButton.swift
//  ios
//
//  Atom: Small button to follow/unfollow a person or entity
//

import SwiftUI

struct FollowButton: View {
    let isFollowing: Bool
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Text(isFollowing ? "Following" : "Follow")
                .font(AppTypography.captionBold)
                .foregroundColor(isFollowing ? AppColors.textSecondary : AppColors.primaryBlue)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    isFollowing
                        ? AppColors.cardBackgroundLight
                        : AppColors.primaryBlue.opacity(0.15)
                )
                .cornerRadius(AppCornerRadius.small)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        FollowButton(isFollowing: false)
        FollowButton(isFollowing: true)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
