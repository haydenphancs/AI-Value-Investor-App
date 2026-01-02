//
//  BookmarkButton.swift
//  ios
//
//  Atom: Bookmark toggle button for saving content
//

import SwiftUI

struct BookmarkButton: View {
    let isBookmarked: Bool
    var size: CGFloat = 20
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark")
                .font(.system(size: size, weight: .medium))
                .foregroundColor(isBookmarked ? AppColors.primaryBlue : AppColors.textMuted)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    HStack(spacing: AppSpacing.xl) {
        BookmarkButton(isBookmarked: false)
        BookmarkButton(isBookmarked: true)
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
