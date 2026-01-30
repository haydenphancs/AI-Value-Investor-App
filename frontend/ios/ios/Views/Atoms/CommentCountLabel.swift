//
//  CommentCountLabel.swift
//  ios
//
//  Atom: Label showing comment count
//

import SwiftUI

struct CommentCountLabel: View {
    let count: Int
    var showIcon: Bool = true

    private var formattedCount: String {
        if count >= 1000 {
            return String(format: "%.1fk", Double(count) / 1000.0)
        }
        return "\(count)"
    }

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            if showIcon {
                Image(systemName: "bubble.left.fill")
                    .font(.system(size: 12, weight: .medium))
            }

            Text("\(formattedCount) Comments")
                .font(AppTypography.caption)
        }
        .foregroundColor(AppColors.textSecondary)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        CommentCountLabel(count: 124)
        CommentCountLabel(count: 5, showIcon: false)
        CommentCountLabel(count: 2500)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
