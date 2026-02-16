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
                Image(systemName: "sparkles")
                    .font(.system(size: 16, weight: .semibold))

                Text(title)
                    .font(AppTypography.bodyBold)
            }
            .foregroundColor(AppColors.textPrimary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.lg)
            .background(
                LinearGradient(
                    colors: [
                        AppColors.primaryBlue,
                        AppColors.accentCyan
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
        .preferredColorScheme(.dark)
}
