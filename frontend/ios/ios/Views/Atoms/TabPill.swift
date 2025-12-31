//
//  TabPill.swift
//  ios
//
//  Atom: Tab pill button for segment control
//

import SwiftUI

struct TabPill: View {
    let title: String
    var isSelected: Bool = false
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Text(title)
                .font(AppTypography.callout)
                .fontWeight(isSelected ? .semibold : .regular)
                .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    Group {
                        if isSelected {
                            Capsule()
                                .fill(AppColors.cardBackgroundLight)
                        }
                    }
                )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    HStack(spacing: 0) {
        TabPill(title: "Research", isSelected: true)
        TabPill(title: "Reports", isSelected: false)
    }
    .padding(AppSpacing.xs)
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.pill)
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
