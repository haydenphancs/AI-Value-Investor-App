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
                    ProgressView()
                        .progressViewStyle(CircularProgressViewStyle(tint: .white))
                } else {
                    HStack(spacing: AppSpacing.sm) {
                        Image(systemName: "sparkles")
                            .font(.system(size: 16, weight: .semibold))

                        Text("Generate Analysis")
                            .font(AppTypography.headline)
                    }
                }

                Text("Uses \(cost.credits) Credits")
                    .font(AppTypography.caption)
                    .opacity(0.8)
            }
            .foregroundColor(AppColors.textPrimary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(isEnabled ? AppColors.primaryBlue : AppColors.textMuted)
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
    .preferredColorScheme(.dark)
}
