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
    /// This session has `activeCount` reports in flight and may not start another.
    /// Rendered as a disabled button UNDER an explanation — the cap used to arrive
    /// as a bare spinner (TestFlight 2026-08-27, research_reports E2).
    var isAtCap: Bool = false
    var activeCount: Int = 0
    var onGenerate: (() -> Void)?
    /// "View progress" on the at-cap notice — the caller flips to the Reports tab.
    var onViewProgress: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // Generate button
            GenerateAnalysisButton(
                cost: cost,
                isEnabled: isEnabled,
                isAtCap: isAtCap,
                onTap: onGenerate
            )

            if isAtCap {
                // Not a failure — `textMuted`, not `caution` (see the atom's note).
                InlineRetryNotice(
                    message: activeCount == 1
                        ? "1 analysis is running — wait for it to finish to start another."
                        : "\(activeCount) analyses are running — wait for one to finish to start another.",
                    systemImage: "hourglass",
                    iconColor: AppColors.textMuted,
                    retryTitle: "View progress",
                    onRetry: onViewProgress
                )
                .accessibilityIdentifier("research.generate.atCapNotice")
            }

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

        GenerateAnalysisSection(
            cost: .standard,
            remainingCredits: 120,
            isEnabled: false,
            isAtCap: true,
            activeCount: 4,
            onViewProgress: {}
        )
    }
    .padding(.vertical)
    .background(AppColors.background)
}
