//
//  LevelBadge.swift
//  ios
//
//  Atom: Badge showing investor level (Foundation, Analyst, Strategist, Master)//

import SwiftUI

struct LevelBadge: View {
    let level: InvestorLevel
    let isActive: Bool
    let isCompleted: Bool

    private var backgroundColor: Color {
        if isActive || isCompleted {
            return level.color
        }
        return AppColors.cardBackgroundLight
    }

    private var iconColor: Color {
        if isActive || isCompleted {
            return .white
        }
        return AppColors.textMuted
    }

    private var textColor: Color {
        if isActive || isCompleted {
            return AppColors.textPrimary
        }
        return AppColors.textMuted
    }

    var body: some View {
        VStack(spacing: AppSpacing.xs) {
            // Icon circle
            ZStack {
                Circle()
                    .fill(backgroundColor)
                    .frame(width: 44, height: 44)

                Image(systemName: level.iconName)
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(iconColor)
            }

            // Label
            Text(level.rawValue)
                .font(AppTypography.caption)
                .foregroundColor(textColor)
        }
    }
}

#Preview {
    HStack(spacing: AppSpacing.xl) {
        LevelBadge(level: .foundation, isActive: true, isCompleted: true)
        LevelBadge(level: .analyst, isActive: false, isCompleted: false)
        LevelBadge(level: .strategist, isActive: false, isCompleted: false)
        LevelBadge(level: .master, isActive: false, isCompleted: false)
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
