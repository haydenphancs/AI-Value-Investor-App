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
                Image(systemName: "sparkles")
                    .font(.system(size: 16, weight: .semibold))

                Text("New Analysis")
                    .font(AppTypography.bodyBold)
            }
            .foregroundColor(.white)
            .padding(.horizontal, AppSpacing.xxl)
            .padding(.vertical, AppSpacing.md)
            .frame(maxWidth: .infinity)
            .background(
                LinearGradient(
                    colors: [AppColors.primaryBlue, AppColors.accentCyan],
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
