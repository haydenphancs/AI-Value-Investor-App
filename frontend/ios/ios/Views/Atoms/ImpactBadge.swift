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
            .font(AppTypography.captionBold)
            .foregroundColor(level.color)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(level.color.opacity(0.15))
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
    .preferredColorScheme(.dark)
}
