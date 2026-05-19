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

    /// Accent color picks tier color when the tag IS a tier label
    /// (Typical / Notable / Unusual / Extreme), and falls back to the
    /// direction-based green/red for event tags (Earnings Miss, FDA, etc.).
    private var accentColor: Color {
        switch tag {
        case "Typical":  return AppColors.textSecondary
        case "Notable":  return AppColors.primaryBlue
        case "Unusual":  return AppColors.alertOrange
        case "Extreme":  return AppColors.bearish
        default:         return isPositive ? AppColors.bullish : AppColors.bearish
        }
    }

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Text(tag)
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textPrimary)
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.sm)
        .background(
            Capsule()
                .fill(AppColors.cardBackgroundLight)
                .overlay(
                    Capsule()
                        .stroke(accentColor.opacity(0.35), lineWidth: 1)
                )
        )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        PriceActionBadge(tag: "Earnings Miss", percentage: "-12.4%", isPositive: false)
        PriceActionBadge(tag: "FDA Approval", percentage: "+24.1%", isPositive: true)
        PriceActionBadge(tag: "Typical", percentage: "+1.2%", isPositive: true)
        PriceActionBadge(tag: "Notable", percentage: "+3.5%", isPositive: true)
        PriceActionBadge(tag: "Unusual", percentage: "-7.8%", isPositive: false)
        PriceActionBadge(tag: "Extreme", percentage: "-15.4%", isPositive: false)
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
