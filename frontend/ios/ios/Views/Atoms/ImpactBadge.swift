//
//  ImpactBadge.swift
//  ios
//
//  Atom: Impact level badge for risk factors
//

import SwiftUI

struct ImpactBadge: View {
    let level: RiskFactor.ImpactLevel

    var body: some View {
        Text(level.rawValue)
            .font(AppTypography.captionEmphasis)
            .foregroundColor(level.color)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    // Opaque: loss/caution/primaryBlue on their own tint fail AA in light
                    // (test_ios_theme_parity §6c).
                    .fill(AppColors.cardBackgroundLight)
            )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ImpactBadge(level: .high)
        ImpactBadge(level: .medium)
        ImpactBadge(level: .variable)
    }
    .padding()
    .background(AppColors.background)
}
