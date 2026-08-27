//
//  NewAnalysisButton.swift
//  ios
//
//  Organism: Floating action button for new analysis
//

import SwiftUI

struct NewAnalysisButton: View {
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: AppSymbols.ai)
                    .font(AppTypography.iconDefault).fontWeight(.semibold)

                Text("New Analysis")
                    .font(AppTypography.bodyEmphasis)
            }
            .foregroundColor(AppColors.textOnAccent)
            .padding(.horizontal, AppSpacing.xxl)
            .padding(.vertical, AppSpacing.md)
            .frame(maxWidth: .infinity)
            .background(
                // `*Fill`, not the text tokens: this button carries constant-white
                // `textOnAccent`, and both text tokens lighten in dark (primaryBlue #60A5FA,
                // accentCyan #06B6D4) where white on them is 2.24 / 2.43. The `*Fill` pair is
                // each token's LIGHT arm frozen, so light mode is byte-identical.
                LinearGradient(
                    colors: [AppColors.primaryFill, AppColors.accentCyanFill],
                    startPoint: .leading,
                    endPoint: .trailing
                )
            )
            .cornerRadius(AppCornerRadius.extraLarge)
//            .shadow(color: AppColors.primaryBlue.opacity(0.4), radius: 8, x: 0, y: 4)
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    VStack {
        Spacer()
        NewAnalysisButton()
    }
    .padding()
    .background(AppColors.background)
}
