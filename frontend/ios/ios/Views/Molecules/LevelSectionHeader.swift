//
//  LevelSectionHeader.swift
//  ios
//
//  Molecule: Header for a level section with icon, title, and progress
//

import SwiftUI

struct LevelSectionHeader: View {
    let level: JourneyLevel
    let completed: Int
    let total: Int

    private var progress: Double {
        guard total > 0 else { return 0 }
        return Double(completed) / Double(total)
    }

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Level icon
            Text(level.iconName)
                .font(.system(size: 18))

            // Title and tagline
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Level \(level.rawValue): \(level.title)")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                Text(level.tagline)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            Spacer()
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        LevelSectionHeader(level: .foundation, completed: 1, total: 7)
        LevelSectionHeader(level: .analysis, completed: 0, total: 7)
        LevelSectionHeader(level: .strategies, completed: 3, total: 7)
        LevelSectionHeader(level: .mastery, completed: 6, total: 6)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
