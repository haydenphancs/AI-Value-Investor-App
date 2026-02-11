//
//  ReportCriticalFactorsSection.swift
//  ios
//
//  Organism: Critical Factors to Watch section with all warning items
//

import SwiftUI

struct ReportCriticalFactorsSection: View {
    let factors: [CriticalFactor]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Critical Factors to Watch")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            // Combined card with dividers
            VStack(spacing: 0) {
                ForEach(Array(factors.enumerated()), id: \.element.id) { index, factor in
                    ReportCriticalFactorRow(factor: factor)
                    
                    // Add divider between items (but not after the last one)
                    if index < factors.count - 1 {
                        Divider()
                            .background(AppColors.textMuted.opacity(0.2))
                            .padding(.horizontal, AppSpacing.md)
                    }
                }
            }
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackground)
            )
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    ReportCriticalFactorsSection(factors: TickerReportData.sampleOracle.criticalFactors)
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
