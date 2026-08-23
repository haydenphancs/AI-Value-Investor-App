//
//  AIDataConsentView.swift
//  ios
//
//  Screen: the explicit consent step required before user-typed content is sent to a
//  third-party AI provider (App Review 5.1.2(i)).
//
//  Shown once, on the first chat send. Declining is a real option — it leaves the rest of
//  the app fully usable, because chat is the only feature that transmits user-typed text.
//

import SwiftUI

struct AIDataConsentView: View {
    /// Called when the user allows. The caller resumes the pending message.
    let onAllow: () -> Void
    /// Called when the user declines. The caller discards the pending message.
    let onDecline: () -> Void

    private struct Row: Identifiable {
        let id = UUID()
        let text: String
    }

    private let whatWeSend: [Row] = [
        Row(text: "The message you type, and the recent messages in that conversation."),
        Row(text: "Market data for whatever you're looking at, so the answer is relevant.")
    ]

    private let whatWeDont: [Row] = [
        Row(text: "Your name, email, or account identifier."),
        Row(text: "Your watchlist, portfolio, or holdings.")
    ]

    /// The accuracy / not-advice half of the gate.
    ///
    /// This screen used to cover PRIVACY only — what gets sent, what doesn't — and said nothing
    /// about the answers being generated, fallible, or not advice. The first-run
    /// `DisclaimerAcknowledgementView` does say it, but that is accepted once, long before
    /// anyone reads an AI answer about a specific stock. A disclaimer carries weight where the
    /// user is actually relying on the output, so it belongs on the gate into chat as well.
    ///
    /// ⚠️ Never name the model or the vendor here (CLAUDE.md invariant #7) — "a third-party AI
    /// provider", matching the wording already used above.
    private let whatItIsNot: [Row] = [
        Row(text: "Cay AI can be wrong. It can misread data, miss context, or state something "
                + "confidently that is already out of date. Verify anything you act on."),
        Row(text: "It isn't financial advice. Nothing Cay AI says is a recommendation to buy "
                + "or sell, and it isn't tailored to your situation."),
        Row(text: "Caydex is not a registered investment adviser or broker-dealer. Investing "
                + "carries risk, including loss of principal.")
    ]

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            VStack(spacing: 0) {
                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: AppSpacing.xl) {
                        VStack(alignment: .leading, spacing: AppSpacing.sm) {
                            Image(systemName: "sparkles.2")
                                .font(AppTypography.iconXXL)
                                .foregroundColor(AppColors.primaryBlue)

                            Text("Before you chat with Cay AI")
                                .font(AppTypography.titleLarge)
                                .foregroundColor(AppColors.textPrimary)

                            Text("To answer your questions, Cay AI sends what you type to a "
                                 + "third-party AI provider for processing. We need your "
                                 + "permission first.")
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(.top, AppSpacing.xxl)

                        group(title: "What gets sent", rows: whatWeSend, tint: AppColors.primaryBlue)
                        group(title: "What never gets sent", rows: whatWeDont, tint: AppColors.bullish)
                        // `caution` is a TEXT-role token (4.5:1). NOT `cautionGraphic` — the
                        // graphic tokens are chart-only and fail AA as text (ios-swiftui.md).
                        group(title: "What this is not", rows: whatItIsNot, tint: AppColors.caution)

                        Text("Your conversations are saved to your account so you can come "
                             + "back to them, and you can delete any conversation at any "
                             + "time. You can withdraw this permission in Settings — chat "
                             + "will stop working until you grant it again. Everything else "
                             + "in Caydex works either way.")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .fixedSize(horizontal: false, vertical: true)

                        // The full disclaimers, one tap from the screen that gates chat.
                        // Self-contained: presents DisclaimersView in its own sheet with a Done
                        // button, so it works inside this cover with no extra plumbing.
                        InlineDisclaimerNotice(
                            text: "Full risk and AI disclaimers",
                            linkLabel: "Read"
                        )
                        .frame(maxWidth: .infinity, alignment: .leading)

                        Spacer(minLength: AppSpacing.xl)
                    }
                    .padding(.horizontal, AppSpacing.xl)
                }

                VStack(spacing: AppSpacing.sm) {
                    Button(action: onAllow) {
                        Text("Allow and continue")
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textOnAccent)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, AppSpacing.lg)
                            .background(
                                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                                    .fill(AppColors.primaryFill)
                            )
                    }
                    .buttonStyle(.plain)

                    Button(action: onDecline) {
                        Text("Not now")
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textSecondary)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, AppSpacing.md)
                    }
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, AppSpacing.xl)
                .padding(.bottom, AppSpacing.xl)
            }
        }
        .interactiveDismissDisabled(true)
    }

    @ViewBuilder
    private func group(title: String, rows: [Row], tint: Color) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text(title.uppercased())
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textMuted)

            ForEach(rows) { row in
                HStack(alignment: .top, spacing: AppSpacing.md) {
                    // A dot, not a glyph. Nine different icons across three groups read as
                    // decoration competing with the text, and the "what this is not" set in
                    // particular (warning triangle, raised hand, columns) made a consent screen
                    // look like a wall of alarms. The tint still carries the grouping; the dot
                    // just stops shouting. `width: 24` matches the old icon frame so the text
                    // column does not shift.
                    Circle()
                        .fill(tint)
                        .frame(width: 6, height: 6)
                        .frame(width: 24, alignment: .center)
                        .padding(.top, 7)
                        .accessibilityHidden(true)
                    Text(row.text)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

#Preview {
    AIDataConsentView(onAllow: {}, onDecline: {})
}
