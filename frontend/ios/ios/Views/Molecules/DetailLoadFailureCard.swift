//
//  DetailLoadFailureCard.swift
//  ios
//
//  Molecule: what an asset-detail screen shows when its load FAILED.
//
//  Why this exists: the Crypto / ETF / Commodity detail screens each wrote a
//  perfectly good `errorMessage` in their ViewModel and then never rendered it —
//  `isLoading` flipped false, the data model stayed nil, and the view fell through
//  to a shimmer skeleton that never resolved. The user sat on a loading animation
//  forever with no error and no retry; the only escape was a pull-to-refresh that
//  isn't discoverable from a skeleton. A permanent shimmer reads as a hung app,
//  which is also one of the likelier things an App Review tester on flaky wifi sees.
//
//  This generalises `TickerDetailView.holdersUnavailableContent`, which already
//  solved exactly this once for the Holders tab. Domain-free (strings + a closure
//  in), so it sits at Molecule level and any screen with the same failure shape can
//  reuse it instead of writing a fourth copy.
//

import SwiftUI

struct DetailLoadFailureCard: View {
    /// Already mapped through `AppError` by the ViewModel — never a raw backend
    /// string (see .claude/rules/ios-swiftui.md).
    let message: String
    var title: String = "Couldn't load this page"
    var iconName: String = "exclamationmark.triangle"
    var isRetrying: Bool = false
    var onRetry: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            Image(systemName: iconName)
                .font(AppTypography.iconHero)
                .foregroundColor(AppColors.textMuted)

            Text(title)
                .font(AppTypography.titleCompact)
                .foregroundColor(AppColors.textPrimary)

            Text(message)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            if let onRetry {
                Button(action: onRetry) {
                    Text(isRetrying ? "Retrying…" : "Try Again")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.primaryBlue)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                        .overlay(
                            RoundedRectangle(cornerRadius: 8)
                                .stroke(AppColors.primaryBlue.opacity(0.5), lineWidth: 1)
                        )
                }
                .buttonStyle(PlainButtonStyle())
                .disabled(isRetrying)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, AppSpacing.xl)
        .padding(.vertical, AppSpacing.xxxl)
    }
}

#Preview {
    VStack(spacing: AppSpacing.xxl) {
        DetailLoadFailureCard(
            message: "Unable to connect. Check your internet connection.",
            onRetry: {}
        )

        DetailLoadFailureCard(
            message: "Retrying after a timeout.",
            isRetrying: true,
            onRetry: {}
        )
    }
    .background(AppColors.background)
}
