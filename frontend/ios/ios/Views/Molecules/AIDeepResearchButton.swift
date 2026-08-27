//
//  AIDeepResearchButton.swift
//  ios
//
//  Molecule: AI Deep Research call-to-action button
//

import SwiftUI

struct AIDeepResearchButton: View {
    var title: String = "AI Analyst"
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.sm) {
                // Sparkle icon
                Image(systemName: AppSymbols.ai)
                    .font(AppTypography.iconDefault).fontWeight(.semibold)

                Text(title)
                    .font(AppTypography.bodyEmphasis)
            }
            // `textOnAccent` on `*Fill`, matching `NewAnalysisButton` — this is the same button.
            // Was `textPrimary` (#0F172A light / #FFFFFF dark) on the adaptive text tokens, so
            // BOTH halves moved: near-black on a dark blue-cyan gradient in light.
            .foregroundColor(AppColors.textOnAccent)
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.lg)
            .background(
                LinearGradient(
                    colors: [
                        AppColors.primaryFill,
                        AppColors.accentCyanFill
                    ],
                    startPoint: .leading,
                    endPoint: .trailing
                )
            )
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    AIDeepResearchButton()
        .padding()
        .background(AppColors.background)
}
