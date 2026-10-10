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
                .font(AppTypography.captionEmphasis)
                .foregroundColor(isFollowing ? AppColors.textSecondary : AppColors.primaryBlue)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                // Opaque in both states: `primaryBlue` on its own 15% tint is 4.19:1 in light
                // (test_ios_theme_parity §6c); on `cardBackgroundLight` it is audited ≥ 4.5.
                .background(AppColors.cardBackgroundLight)
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
}
