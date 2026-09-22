//
//  TechnicalSignalBadge.swift
//  ios
//
//  Badge displaying technical signal (Buy, Sell, Hold, etc.)
//

import SwiftUI

struct TechnicalSignalBadge: View {
    let title: String
    let signal: TechnicalSignal
    let indicatorCount: String
    var isSelected: Bool = false

    var body: some View {
        VStack(alignment: .center, spacing: AppSpacing.xs) {
            Text(title)
                .font(AppTypography.caption)
                .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textMuted)

            Text(indicatorCount)
                .font(AppTypography.caption)
                .foregroundColor(isSelected ? AppColors.textSecondary : AppColors.textMuted)
                .contentTransition(.numericText())
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.md)
        .padding(.horizontal, AppSpacing.sm)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(isSelected ? AppColors.cardBackgroundLight : AppColors.cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .stroke(isSelected ? AppColors.borderStrong : Color.clear, lineWidth: 2)
        )
        // The badge is tapped through an `onTapGesture` on the meter, so VoiceOver saw two
        // unlabeled text runs and no control. One element, a button, and the signal the
        // visual badge never prints ("Daily Signal, Buy, 12 of 13 indicators").
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title), \(signal.displayName), \(indicatorCount)")
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        HStack(spacing: AppSpacing.md) {
            TechnicalSignalBadge(
                title: "Daily Signal",
                signal: .buy,
                indicatorCount: "12 of 18 indicators",
                isSelected: true
            )

            TechnicalSignalBadge(
                title: "Weekly Signal",
                signal: .strongBuy,
                indicatorCount: "14 of 18 indicators",
                isSelected: false
            )
        }
        .padding()
    }
}
