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

    /// "1 credit", never "1 credits".
    private var unit: String { credits == 1 ? "credit" : "credits" }

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            if showIcon {
                Text("🎟️")
                    .font(style == .large ? .system(size: 16) : .system(size: 12))
            }

            Text(style == .large ? "\(credits)" : "You have \(credits) \(unit) remaining")
                .font(style == .large ? AppTypography.titleLarge : AppTypography.caption)
                .fontWeight(style == .large ? .bold : .regular)
                .foregroundColor(style == .large ? AppColors.textPrimary : AppColors.textSecondary)

            if style == .large {
                Text(unit)
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
        CreditsBadge(credits: 1, style: .compact)
    }
    .padding()
    .background(AppColors.background)
}
