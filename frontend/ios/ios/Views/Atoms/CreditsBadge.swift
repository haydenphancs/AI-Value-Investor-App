//
//  CreditsBadge.swift
//  ios
//
//  Atom: Displays credit count with icon
//

import SwiftUI

struct CreditsBadge: View {
    let credits: Int
    var showIcon: Bool = true
    var style: CreditsBadgeStyle = .compact

    enum CreditsBadgeStyle {
        case compact
        case large
    }

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            if showIcon {
                Text("🎟️")
                    .font(style == .large ? .system(size: 16) : .system(size: 12))
            }

            Text(style == .large ? "\(credits)" : "You have \(credits) credits remaining")
                .font(style == .large ? AppTypography.largeTitle : AppTypography.caption)
                .fontWeight(style == .large ? .bold : .regular)
                .foregroundColor(style == .large ? AppColors.textPrimary : AppColors.textSecondary)

            if style == .large {
                Text("credits")
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        CreditsBadge(credits: 47, style: .compact)
        CreditsBadge(credits: 47, style: .large)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
