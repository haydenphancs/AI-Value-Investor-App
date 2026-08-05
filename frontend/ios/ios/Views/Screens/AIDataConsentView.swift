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
        let icon: String
        let text: String
    }

    private let whatWeSend: [Row] = [
        Row(icon: "text.bubble",
            text: "The message you type, and the recent messages in that conversation."),
        Row(icon: "chart.line.uptrend.xyaxis",
            text: "Market data for whatever you're looking at, so the answer is relevant.")
    ]

    private let whatWeDont: [Row] = [
        Row(icon: "person.crop.circle.badge.xmark",
            text: "Your name, email, or account identifier."),
        Row(icon: "briefcase",
            text: "Your watchlist, portfolio, or holdings.")
    ]

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            VStack(spacing: 0) {
                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: AppSpacing.xl) {
                        VStack(alignment: .leading, spacing: AppSpacing.sm) {
                            Image(systemName: "sparkles")
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

                        Text("Your conversations are saved to your account so you can come "
                             + "back to them, and you can delete any conversation at any "
                             + "time. You can withdraw this permission in Settings — chat "
                             + "will stop working until you grant it again. Everything else "
                             + "in Caydex works either way.")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .fixedSize(horizontal: false, vertical: true)

                        Spacer(minLength: AppSpacing.xl)
                    }
                    .padding(.horizontal, AppSpacing.xl)
                }

                VStack(spacing: AppSpacing.sm) {
                    Button(action: onAllow) {
                        Text("Allow and continue")
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(.white)
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
                    Image(systemName: row.icon)
                        .font(AppTypography.iconDefault)
                        .foregroundColor(tint)
                        .frame(width: 24)
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
