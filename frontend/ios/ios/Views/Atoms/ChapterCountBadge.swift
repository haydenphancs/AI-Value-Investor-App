//
//  ChapterCountBadge.swift
//  ios
//
//  Atom: Badge showing number of chapters in a lesson or book
//

import SwiftUI

struct ChapterCountBadge: View {
    let count: Int
    var showIcon: Bool = true

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            if showIcon {
                Image(systemName: "book.closed.fill")
                    .font(.system(size: 10, weight: .medium))
            }

            Text("\(count) chapters")
                .font(AppTypography.caption)
        }
        .foregroundColor(AppColors.textSecondary)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ChapterCountBadge(count: 5)
        ChapterCountBadge(count: 12, showIcon: false)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
