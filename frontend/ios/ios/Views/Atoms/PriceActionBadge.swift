//
//  PriceActionBadge.swift
//  ios
//
//  Atom: Capsule badge showing a price catalyst tag and percentage change.
//  e.g. [ Earnings Miss  -12.4% ]
//

import SwiftUI

struct PriceActionBadge: View {
    let tag: String
    let percentage: String
    let isPositive: Bool

    private var percentColor: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Text(tag)
                .font(AppTypography.captionBold)
                .foregroundColor(AppColors.textPrimary)

            Text(percentage)
                .font(AppTypography.captionBold)
                .foregroundColor(percentColor)
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.sm)
        .background(
            Capsule()
                .fill(AppColors.cardBackgroundLight)
                .overlay(
                    Capsule()
                        .stroke(percentColor.opacity(0.25), lineWidth: 1)
                )
        )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        PriceActionBadge(tag: "Earnings Miss", percentage: "-12.4%", isPositive: false)
        PriceActionBadge(tag: "FDA Approval", percentage: "+24.1%", isPositive: true)
        PriceActionBadge(tag: "Normal", percentage: "+1.2%", isPositive: true)
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
