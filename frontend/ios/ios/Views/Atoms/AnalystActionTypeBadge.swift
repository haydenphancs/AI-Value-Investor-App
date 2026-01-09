//
//  AnalystActionTypeBadge.swift
//  ios
//
//  Badge displaying analyst action type (UPGRADE, DOWNGRADE, MAINTAIN, etc.)
//

import SwiftUI

struct AnalystActionTypeBadge: View {
    let actionType: AnalystActionType

    var body: some View {
        Text(actionType.rawValue)
            .font(AppTypography.captionBold)
            .foregroundColor(badgeTextColor)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(badgeBackground)
            .cornerRadius(AppCornerRadius.small)
    }

    private var badgeTextColor: Color {
        switch actionType {
        case .upgrade:
            return AppColors.bullish
        case .downgrade:
            return AppColors.bearish
        case .maintain, .initiated, .reiterated:
            return AppColors.textSecondary
        }
    }

    private var badgeBackground: Color {
        switch actionType {
        case .upgrade:
            return AppColors.bullish.opacity(0.15)
        case .downgrade:
            return AppColors.bearish.opacity(0.15)
        case .maintain, .initiated, .reiterated:
            return Color.clear
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            AnalystActionTypeBadge(actionType: .upgrade)
            AnalystActionTypeBadge(actionType: .downgrade)
            AnalystActionTypeBadge(actionType: .maintain)
            AnalystActionTypeBadge(actionType: .initiated)
            AnalystActionTypeBadge(actionType: .reiterated)
        }
        .padding()
    }
}
