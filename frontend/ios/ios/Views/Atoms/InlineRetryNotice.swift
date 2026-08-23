//
//  InlineRetryNotice.swift
//  ios
//
//  Atom: a bordered notice that states what went wrong and offers one retry, sized to sit
//  ALONGSIDE content rather than replace it.
//
//  Distinct from `ChartUnavailableView`, which is the same idea with no action and is meant
//  to fill the space its content would have occupied. This one exists because the recurring
//  mistake is the opposite: a partial failure that blanks a whole screen. Buy Credits showed
//  "No purchase options are available right now." on an empty page when StoreKit returned no
//  products, even though the backend had told it exactly which four packs exist and what each
//  one grants — so the screen said nothing at all rather than everything except the price.
//
//  Knows no domain type, so it is an Atom by the placement rule.
//

import SwiftUI

struct InlineRetryNotice: View {
    let message: String
    var systemImage: String = "exclamationmark.triangle"
    /// Defaults to the error reading. Pass `textMuted` when the notice is NOT a failure —
    /// an empty list or a "sign in to see this" prompt is not something that went wrong, and
    /// `caution` next to that copy reads as a bug the user should report.
    var iconColor: Color = AppColors.caution
    var retryTitle: String = "Try Again"
    /// Omit to render the notice with no action.
    var onRetry: (() -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            Image(systemName: systemImage)
                .font(AppTypography.iconSmall)
                .foregroundColor(iconColor)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(message)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    // No lineLimit: the message can be a server-supplied string and is read at
                    // every Dynamic Type size.
                    .fixedSize(horizontal: false, vertical: true)

                if let onRetry {
                    Button(retryTitle, action: onRetry)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }

            Spacer(minLength: 0)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .cardFill(AppColors.cardBackgroundNested)
        )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        InlineRetryNotice(
            message: "No purchase options are available right now.",
            onRetry: {}
        )
        InlineRetryNotice(message: "Couldn't reach the server.")
    }
    .padding()
    .background(AppColors.background)
}
