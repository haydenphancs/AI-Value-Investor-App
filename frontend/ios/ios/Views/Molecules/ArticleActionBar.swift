//
//  ArticleActionBar.swift
//  ios
//
//  Molecule: Action bar with article interaction buttons
//

import SwiftUI

struct ArticleActionBar: View {
    var hasAudioVersion: Bool = true
    var isBookmarked: Bool = false
    var onListenTapped: (() -> Void)?
    var onShareTapped: (() -> Void)?
    var onBookmarkTapped: (() -> Void)?
    var onMoreTapped: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Listen button (if audio available)
            if hasAudioVersion {
                Button(action: { onListenTapped?() }) {
                    HStack(spacing: AppSpacing.sm) {
                        Image(systemName: "headphones")
                            .font(.system(size: 14, weight: .semibold))
                        Text("Listen")
                            .font(AppTypography.calloutBold)
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.sm)
                    .background(
                        LinearGradient(
                            colors: [AppColors.primaryBlue, Color(hex: "6366F1")],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .cornerRadius(AppCornerRadius.pill)
                }
                .buttonStyle(PlainButtonStyle())
            }

            Spacer()

            // Right side actions
            HStack(spacing: AppSpacing.lg) {
                // Share
                Button(action: { onShareTapped?() }) {
                    Image(systemName: "square.and.arrow.up")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())

                // Bookmark
                Button(action: { onBookmarkTapped?() }) {
                    Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(isBookmarked ? AppColors.primaryBlue : AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())

                // More options
                Button(action: { onMoreTapped?() }) {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(
            Rectangle()
                .fill(AppColors.cardBackground)
                .shadow(color: Color.black.opacity(0.2), radius: 8, y: -4)
        )
    }
}

#Preview {
    VStack {
        Spacer()
        ArticleActionBar(hasAudioVersion: true, isBookmarked: false)
        ArticleActionBar(hasAudioVersion: false, isBookmarked: true)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
