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

    /// Direction colour for the percentage, deliberately NOT `accentColor`.
    ///
    /// `accentColor` switches to a TIER colour for Typical/Notable/Unusual/Extreme,
    /// which says how unusual the move was, not which way it went — so tinting the
    /// number with it would have rendered a +24% gain in `bearish` red under an
    /// "Extreme" tag. The sign carries the meaning; this only reinforces it.
    private var directionColor: Color {
        isPositive ? AppColors.gain : AppColors.loss
    }

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Text(tag)
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textPrimary)

            // The percentage was accepted as a parameter and then never drawn, so the
            // ONLY thing distinguishing a gain badge from a loss badge was the stroke
            // hue — a WCAG 1.4.1 failure (colour as the sole visual means), and the
            // exact case red/green colour blindness collapses. ~8% of men cannot
            // separate those two hues; the signed number is readable by everyone.
            //
            // Empty is tolerated rather than assumed away: the caller derives this
            // string from optional backend data, and an empty one must not leave a
            // dangling separator inside the capsule.
            if !percentage.isEmpty {
                Text(percentage)
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(directionColor)
            }
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
        // A tier tag on a POSITIVE move: the capsule stroke is the tier colour while
        // the number stays green, because the two say different things.
        PriceActionBadge(tag: "Extreme", percentage: "+31.7%", isPositive: true)
        // Missing percentage must not leave a dangling gap inside the capsule.
        PriceActionBadge(tag: "Earnings Beat", percentage: "", isPositive: true)
    }
    .padding()
    .background(AppColors.cardBackground)
}
