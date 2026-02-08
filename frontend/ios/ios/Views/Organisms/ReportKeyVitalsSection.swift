//
//  ReportKeyVitalsSection.swift
//  ios
//
//  Organism: Horizontal scrolling Key Vitals section with 3 vital cards
//

import SwiftUI

struct ReportKeyVitalsSection: View {
    let vitals: ReportKeyVitals

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Key Vitals")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.md) {
                    ReportValuationVitalCard(data: vitals.valuation)
                        .frame(width: 165)

                    ReportMoatVitalCard(data: vitals.moat)
                        .frame(width: 165)

                    ReportFinancialHealthVitalCard(data: vitals.financialHealth)
                        .frame(width: 165)
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

#Preview {
    ReportKeyVitalsSection(vitals: TickerReportData.sampleOracle.keyVitals)
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
