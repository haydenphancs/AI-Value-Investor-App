//
//  AnalystActionBadge.swift
//  ios
//
//  Badge showing analyst action counts (Upgrades, Maintains, Downgrades)
//

import SwiftUI

enum AnalystActionType: String {
    case upgrades = "Upgrades"
    case maintains = "Maintains"
    case downgrades = "Downgrades"

    var iconName: String {
        switch self {
        case .upgrades: return "arrow.up"
        case .maintains: return "equal"
        case .downgrades: return "arrow.down"
        }
    }

    var color: Color {
        switch self {
        case .upgrades: return AppColors.bullish
        case .maintains: return AppColors.textSecondary
        case .downgrades: return AppColors.bearish
        }
    }
}

struct AnalystActionBadge: View {
    let actionType: AnalystActionType
    let count: Int

    var body: some View {
        VStack(spacing: AppSpacing.xs) {
            // Icon and count
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: actionType.iconName)
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(actionType.color)

                Text("\(count)")
                    .font(AppTypography.footnoteBold)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Label
            Text(actionType.rawValue)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .stroke(AppColors.cardBackgroundLight, lineWidth: 1)
        )
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        HStack(spacing: AppSpacing.md) {
            AnalystActionBadge(actionType: .upgrades, count: 9)
            AnalystActionBadge(actionType: .maintains, count: 8)
            AnalystActionBadge(actionType: .downgrades, count: 2)
        }
        .padding()
    }
}
