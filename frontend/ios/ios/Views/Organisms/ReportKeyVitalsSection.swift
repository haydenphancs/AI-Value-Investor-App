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
                HStack(alignment: .top, spacing: AppSpacing.md) {
                    ReportValuationVitalCard(data: vitals.valuation)
                        .frame(width: 185)

                    ReportMoatVitalCard(data: vitals.moat)
                        .frame(width: 185)

                    ReportFinancialHealthVitalCard(data: vitals.financialHealth)
                        .frame(width: 185)

                    ReportRevenueVitalCard(data: vitals.revenue)
                        .frame(width: 185)

                    ReportInsiderVitalCard(data: vitals.insider)
                        .frame(width: 185)

                    ReportMacroVitalCard(data: vitals.macro)
                        .frame(width: 185)

                    ReportForecastVitalCard(data: vitals.forecast)
                        .frame(width: 185)

                    ReportWallStreetVitalCard(data: vitals.wallStreet)
                        .frame(width: 185)
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
