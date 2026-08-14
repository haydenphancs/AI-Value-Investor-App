//
//  PersonalizationConsentSheet.swift
//  ios
//
//  The explicit opt-in for personalized explanations, offered at the moment it first
//  becomes available — right after an upgrade, since only Pro/Max have it applied.
//
//  WHY A SHEET AND NOT A CHECKBOX IN ONBOARDING: this is a consent artifact. It has to
//  show the disclosure, be an affirmative act, and be revocable — so it states plainly
//  what IS used, what is NOT, and where to turn it off. Implied consent buried in a
//  footer would be a materially weaker record for the one feature whose entire defence
//  is that the reader chose it.
//
//  The copy deliberately mirrors Terms §2 and the privacy policy. If those change, change
//  this too — a consent screen that describes something other than the published policy
//  is worse than no consent screen.
//

import SwiftUI

struct PersonalizationConsentSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var viewModel: PersonalizationConsentViewModel

    var body: some View {
        NavigationStack {
            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    Text("Want Cay AI to explain things your way?")
                        .font(AppTypography.title)
                        .foregroundColor(AppColors.textPrimary)

                    Text("You told us how you like to learn. Turn this on and Cay AI will use it to decide what to cover first and how to word an explanation.")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)

                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        bullet(
                            icon: "checkmark.circle",
                            tint: AppColors.gain,
                            title: "What it changes",
                            body: "Which information comes first, how technical the wording is, and how long answers are."
                        )
                        bullet(
                            icon: "xmark.circle",
                            tint: AppColors.loss,
                            title: "What it never changes",
                            body: "Our analysis, ratings, scores and estimates. Those are produced the same way for every user, and none of this makes anything a recommendation that a security suits you."
                        )
                        bullet(
                            icon: "lock",
                            tint: AppColors.textSecondary,
                            title: "What we don't ask",
                            body: "Your finances, risk tolerance, time horizon, tax situation or goals. We don't collect them, and Cay AI doesn't adapt its analysis to them."
                        )
                    }
                    .padding(AppSpacing.md)
                    .cardSurface()

                    Text("You can turn this off any time in Settings → AI & Research.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)

                    if let error = viewModel.errorMessage {
                        Text(error)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.loss)
                    }
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .safeAreaInset(edge: .bottom) {
                VStack(spacing: AppSpacing.sm) {
                    Button {
                        Task {
                            await viewModel.setConsent(true)
                            // Only leave on success — otherwise the reader would think
                            // they enabled something that never saved.
                            if viewModel.isOn { dismiss() }
                        }
                    } label: {
                        Text(viewModel.isSaving ? "Turning on…" : "Turn it on")
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textOnAccent)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, AppSpacing.md)
                            .background(
                                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                                    .fill(AppColors.primaryFill)
                            )
                    }
                    .buttonStyle(PlainButtonStyle())
                    .disabled(viewModel.isSaving)

                    // Declining must be exactly as easy as accepting, or the "explicit
                    // permission" this screen exists to record is not freely given.
                    Button("Not now") { dismiss() }
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .padding(.bottom, AppSpacing.sm)
                }
                .padding(.horizontal, AppSpacing.lg)
                .background(AppColors.background)
            }
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private func bullet(icon: String, tint: Color, title: String, body: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            Image(systemName: icon)
                .font(AppTypography.iconDefault)
                .foregroundColor(tint)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                Text(body)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

#Preview {
    PersonalizationConsentSheet(viewModel: PersonalizationConsentViewModel())
}
