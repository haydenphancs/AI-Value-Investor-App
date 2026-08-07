//
//  GenerateAnalysisButton.swift
//  ios
//
//  Molecule: Generate analysis button with cost indicator
//

import SwiftUI

struct GenerateAnalysisButton: View {
    let cost: AnalysisCost
    var isEnabled: Bool = true
    var isLoading: Bool = false
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(spacing: AppSpacing.xs) {
                if isLoading {
                    // `textOnAccent`, not a bare `.white`: this spinner sits on
                    // `primaryFill` (line 48), which is exactly the on-accent-ink case.
                    ProgressView()
                        .progressViewStyle(CircularProgressViewStyle(tint: AppColors.textOnAccent))
                } else {
                    HStack(spacing: AppSpacing.sm) {
                        Image(systemName: "sparkles.2")
                            .font(AppTypography.iconDefault).fontWeight(.semibold)

                        Text("Generate Analysis")
                            .font(AppTypography.headingSmall)
                    }
                }

                Text("Uses \(cost.credits) Credits")
                    .font(AppTypography.caption)
                    .opacity(0.8)
            }
            // The disabled state was `textOnAccent` on `AppColors.textMuted` — a TEXT
            // token used as a fill, which lightens to #9CA3AF in dark and put the label
            // at 2.29:1. Disabled controls are WCAG-exempt, so the fix is to render the
            // disabled state AS disabled (a surface + `textDisabled`) rather than as an
            // enabled button whose ink happens to fail.
            .foregroundColor(isEnabled ? AppColors.textOnAccent : AppColors.textDisabled)
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(isEnabled ? AppColors.primaryFill : AppColors.cardBackgroundLight)
            )
        }
        .buttonStyle(PlainButtonStyle())
        .disabled(!isEnabled || isLoading)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        GenerateAnalysisButton(cost: .standard, isEnabled: true)
        GenerateAnalysisButton(cost: .standard, isEnabled: false)
        GenerateAnalysisButton(cost: .standard, isLoading: true)
    }
    .padding()
    .background(AppColors.background)
}
