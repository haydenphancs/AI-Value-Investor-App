//
//  GenerateAnalysisSection.swift
//  ios
//
//  Organism: Generate analysis button with credits indicator
//

import SwiftUI

struct GenerateAnalysisSection: View {
    let cost: AnalysisCost
    /// nil = balance not loaded / failed to load. The badge is hidden rather than
    /// showing a number the user doesn't actually have.
    let remainingCredits: Int?
    var isEnabled: Bool = true
    var isLoading: Bool = false
    var onGenerate: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // Generate button
            GenerateAnalysisButton(
                cost: cost,
                isEnabled: isEnabled,
                isLoading: isLoading,
                onTap: onGenerate
            )

            // Credits remaining — omitted entirely when unknown.
            if let remainingCredits {
                CreditsBadge(credits: remainingCredits, style: .compact)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        
    }
}

#Preview {
    VStack(spacing: AppSpacing.xxl) {
        GenerateAnalysisSection(
            cost: .standard,
            remainingCredits: 47,
            isEnabled: true
        )

        GenerateAnalysisSection(
            cost: .standard,
            remainingCredits: 3,
            isEnabled: false
        )
    }
    .padding(.vertical)
    .background(AppColors.background)
}
