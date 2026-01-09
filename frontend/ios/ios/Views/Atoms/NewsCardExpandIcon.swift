//
//  NewsCardExpandIcon.swift
//  ios
//
//  Atom: Expand/collapse chevron icon for news cards
//

import SwiftUI

struct NewsCardExpandIcon: View {
    let isExpanded: Bool
    var size: CGFloat = 16

    var body: some View {
        Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
            .font(.system(size: size, weight: .semibold))
            .foregroundColor(AppColors.textSecondary)
            .animation(.easeInOut(duration: 0.2), value: isExpanded)
    }
}

#Preview {
    HStack(spacing: AppSpacing.xxl) {
        VStack(spacing: AppSpacing.sm) {
            NewsCardExpandIcon(isExpanded: false)
            Text("Collapsed")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }

        VStack(spacing: AppSpacing.sm) {
            NewsCardExpandIcon(isExpanded: true)
            Text("Expanded")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
