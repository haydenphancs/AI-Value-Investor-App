//
//  SavedIndicator.swift
//  ios
//
//  Atom: Saved indicator with bookmark icon for chat history
//

import SwiftUI

struct SavedIndicator: View {
    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: "bookmark.fill")
                .font(.system(size: 10, weight: .medium))

            Text("Saved")
                .font(AppTypography.caption)
        }
        .foregroundColor(AppColors.textSecondary)
    }
}

#Preview {
    SavedIndicator()
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
