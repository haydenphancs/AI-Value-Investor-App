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
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            ForEach(factors) { factor in
                ReportCriticalFactorRow(factor: factor)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    ReportCriticalFactorsSection(factors: TickerReportData.sampleOracle.criticalFactors)
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
