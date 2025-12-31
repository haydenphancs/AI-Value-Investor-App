//
//  GenerateAnalysisSection.swift
//  ios
//
//  Organism: Generate analysis button with credits indicator
//

import SwiftUI

struct GenerateAnalysisSection: View {
    let cost: AnalysisCost
    let remainingCredits: Int
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

            // Credits remaining
            CreditsBadge(credits: remainingCredits, style: .compact)
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
    .preferredColorScheme(.dark)
}
