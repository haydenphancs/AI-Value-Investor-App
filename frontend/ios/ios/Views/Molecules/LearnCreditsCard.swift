//
//  LearnCreditsCard.swift
//  ios
//
//  Molecule: Credit balance card specifically for Learn section
//

import SwiftUI

struct LearnCreditsCard: View {
    let balance: CreditBalance
    var onAddCredits: (() -> Void)?

    // `alertOrangeFill` (#C2410C in BOTH modes), never `alertOrange` — see CreditsBalanceCard,
    // which this card duplicates almost line for line.
    private let gradientColors = [
        AppColors.alertOrangeFill,
        AppColors.alertOrangeFill
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header — `textOnAccent`, not `textPrimary`: the latter is #0F172A in LIGHT and
            // #FFFFFF in dark, so it inverted against a fill that did not (3.43 light).
            // ⚠️ No `.opacity()` on the card body — white at 0.8 is 3.85 here, below AA.
            Text("Credit Balance")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textOnAccent)

            Text("Manage your research credits")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textOnAccent)

            // Credits Display — on a 0.2 black scrim, so 0.8 still clears AA (5.21).
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                HStack(alignment: .lastTextBaseline, spacing: AppSpacing.sm) {
                    Text("\(balance.credits)")
                        .font(AppTypography.dataHero)
                        .foregroundColor(AppColors.textOnAccent)

                    Text("credits")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textOnAccent.opacity(0.8))
                }

                // See CreditsBalanceCard: the balance above is COMBINED, so an unconditional
                // "Renews <date>" claims purchased credits expire — which Guideline 3.1.1
                // forbids them from doing.
                Text(balance.compositionSummary)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textOnAccent.opacity(0.8))
            }
            .padding(AppSpacing.lg)
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
                // Inverse CTA: constant-white button carrying the brand orange, 5.18 both modes.
                // Was `alertOrange` on `textPrimary` — BOTH halves inverted, giving a near-black
                // button with rust text in light (3.43).
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
    LearnCreditsCard(balance: CreditBalance.mock)
        .padding()
        .background(AppColors.background)
}
