//
//  GenerateAnalysisButton.swift
//  ios
//
//  Molecule: Generate analysis button with cost indicator
//
//  No spinner. This button used to take `isLoading`, which the live screen fed with
//  `isAtConcurrencyCap` — so at four reports in flight it became a disabled spinner
//  with nothing saying why (TestFlight 2026-08-27, research_reports E2). A spinner
//  reads as "busy, wait"; the cap is "you are at a limit". Progress lives on the
//  Reports tab's cards, one per run; the button only ever has to say whether a tap
//  can start another. At the cap it renders as a plain disabled control and the
//  section under it explains the limit.
//

import SwiftUI

struct GenerateAnalysisButton: View {
    let cost: AnalysisCost
    var isEnabled: Bool = true
    /// The per-user in-flight cap is reached. Disabled like any other reason, kept
    /// distinct so the section can render the at-cap notice beside it.
    var isAtCap: Bool = false
    var onTap: (() -> Void)?

    private var isInteractive: Bool { isEnabled && !isAtCap }

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(spacing: AppSpacing.xs) {
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: AppSymbols.ai)
                        .font(AppTypography.iconDefault).fontWeight(.semibold)

                    Text("Generate Analysis")
                        .font(AppTypography.headingSmall)
                }

                // ⚠️ NO `.opacity(0.8)`: it inherits `textOnAccent` from the modifier below and
                // sits on `primaryFill`, where white at 0.8 is 3.56:1 — below AA, and this is the
                // primary paid CTA. It measured 3.90 before the 2026-09 fill lightening, i.e. it
                // was already failing. `caption` against `headingSmall` is the hierarchy.
                Text("Uses \(cost.credits) Credits")
                    .font(AppTypography.caption)
            }
            // The disabled state was `textOnAccent` on `AppColors.textMuted` — a TEXT
            // token used as a fill, which lightens to #9CA3AF in dark and put the label
            // at 2.29:1. Disabled controls are WCAG-exempt, so the fix is to render the
            // disabled state AS disabled (a surface + `textDisabled`) rather than as an
            // enabled button whose ink happens to fail.
            .foregroundColor(isInteractive ? AppColors.textOnAccent : AppColors.textDisabled)
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(isInteractive ? AppColors.primaryFill : AppColors.cardBackgroundLight)
            )
        }
        .buttonStyle(PlainButtonStyle())
        .disabled(!isInteractive)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        GenerateAnalysisButton(cost: .standard, isEnabled: true)
        GenerateAnalysisButton(cost: .standard, isEnabled: false)
        GenerateAnalysisButton(cost: .standard, isAtCap: true)
    }
    .padding()
    .background(AppColors.background)
}
