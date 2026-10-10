//
//  LessonCategoryBadge.swift
//  ios
//
//  Atom: Badge showing lesson category (e.g., Crypto)
//

import SwiftUI

struct LessonCategoryBadge: View {
    let category: LessonCategory

    var body: some View {
        if let badgeText = category.badgeText {
            Text(badgeText)
                .font(AppTypography.caption)
                .fontWeight(.medium)
                .foregroundColor(category.badgeColor)
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xxs)
                // Opaque: `caution` on its own 15% tint is 4.20:1 in light
                // (test_ios_theme_parity §6c).
                .background(AppColors.cardBackgroundLight)
                .cornerRadius(AppCornerRadius.small)
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        LessonCategoryBadge(category: .crypto)
        LessonCategoryBadge(category: .standard)
    }
    .padding()
    .background(AppColors.cardBackground)
}
