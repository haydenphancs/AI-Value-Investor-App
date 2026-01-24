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
        HStack(spacing: AppSpacing.sm) {
            // Level icon
            Text(level.iconName)
                .font(.system(size: 18))

            // Level title
            Text("Level \(level.rawValue): \(level.title)")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

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
