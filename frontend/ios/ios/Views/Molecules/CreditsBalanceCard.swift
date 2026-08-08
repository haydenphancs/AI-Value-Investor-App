//
//  CreditsBalanceCard.swift
//  ios
//
//  Molecule: Credit balance card with gradient background
//

import SwiftUI

struct CreditsBalanceCard: View {
    let balance: CreditBalance
    var onAddCredits: (() -> Void)?

    // `alertOrangeFill` (#C2410C in BOTH modes), never `alertOrange` — the text token
    // lightens to #F97316 in dark, and ink on a fill that moves is the whole defect below.
    private let gradientColors = [
        AppColors.alertOrangeFill,
        AppColors.alertOrangeFill
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            //
            // `textOnAccent` (constant white), NOT `textPrimary`. `textPrimary` is #0F172A in
            // LIGHT and #FFFFFF in dark, so it INVERTED against a fill that did not: dark mode
            // rendered white-on-orange correctly while light mode rendered near-black on orange
            // at 3.43:1. White on `alertOrangeFill` is 5.18 in both.
            //
            // ⚠️ No `.opacity()` on the card body — white at 0.8 measures 3.85 on this fill,
            // which is below AA. The hierarchy comes from size and weight here instead. Inside
            // the scrimmed panel below there IS headroom (0.8 → 5.21) so it keeps its dimming.
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Credit Balance")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textOnAccent)

                    Text("Manage your research credits")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textOnAccent)
                }

                Spacer()

                // Credits icon
                Image(systemName: "creditcard.fill")
                    .font(AppTypography.iconXL)
                    .foregroundColor(AppColors.textOnAccent)
            }

            // Credits Display — sits on a 0.2 black scrim, so 0.8 still clears AA (5.21).
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                HStack(alignment: .lastTextBaseline, spacing: AppSpacing.sm) {
                    Text("\(balance.credits)")
                        .font(AppTypography.dataHero)
                        .foregroundColor(AppColors.textOnAccent)

                    Text("credits")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textOnAccent.opacity(0.8))
                }

                // `compositionSummary`, not `formattedRenewalDate`: the number above is the
                // COMBINED balance, and printing "Renews <date>" under it told a user who
                // bought a credit pack that their purchased credits expire. App Store
                // Guideline 3.1.1 forbids them expiring, so saying so is both false and a
                // review risk. This renders whichever halves actually exist.
                Text(balance.compositionSummary)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textOnAccent.opacity(0.8))
            }
            .padding(AppSpacing.md)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(Color.black.opacity(0.2))
            )

            // Add Credits Button
            Button(action: {
                onAddCredits?()
            }) {
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "plus")
                        .font(AppTypography.iconSmall).fontWeight(.semibold)

                    Text("Add More Credits")
                        .font(AppTypography.bodySmallEmphasis)
                }
                // The inverse CTA: a constant-white button carrying the brand orange, 5.18 in
                // both modes. It used to be `alertOrange` on `textPrimary`, i.e. BOTH halves
                // inverted — a white button with orange text in dark, a near-black button with
                // rust text in light (3.43). This is the dark-mode design, made mode-stable.
                .foregroundColor(AppColors.alertOrangeFill)
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.md)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .fill(AppColors.textOnAccent)
                )
            }
            .buttonStyle(PlainButtonStyle())
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge)
                .fill(
                    LinearGradient(
                        colors: gradientColors,
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
        )
    }
}

#Preview {
    CreditsBalanceCard(balance: .mock)
        .padding()
        .background(AppColors.background)
}
