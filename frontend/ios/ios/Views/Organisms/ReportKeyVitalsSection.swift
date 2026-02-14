//
//  ReportKeyVitalsSection.swift
//  ios
//
//  Organism: Horizontal scrolling Key Vitals section.
//  Only renders cards that the VitalRulesEngine has surfaced (non-nil).
//

import SwiftUI

struct ReportKeyVitalsSection: View {
    let vitals: ReportKeyVitals

    var body: some View {
        if vitals.hasAny {
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                Text("Key Vitals")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
                    .padding(.horizontal, AppSpacing.lg)

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: AppSpacing.md) {
                        if let valuation = vitals.valuation {
                            ReportValuationVitalCard(data: valuation)
                                .frame(width: 185)
                        }

                        if let moat = vitals.moat {
                            ReportMoatVitalCard(data: moat)
                                .frame(width: 185)
                        }

                        if let health = vitals.financialHealth {
                            ReportFinancialHealthVitalCard(data: health)
                                .frame(width: 185)
                        }

                        if let revenue = vitals.revenue {
                            ReportRevenueVitalCard(data: revenue)
                                .frame(width: 185)
                        }

                        if let insider = vitals.insider {
                            ReportInsiderVitalCard(data: insider)
                                .frame(width: 185)
                        }

                        if let macro = vitals.macro {
                            ReportMacroVitalCard(data: macro)
                                .frame(width: 185)
                        }

                        if let forecast = vitals.forecast {
                            ReportForecastVitalCard(data: forecast)
                                .frame(width: 185)
                        }

                        if let wallStreet = vitals.wallStreet {
                            ReportWallStreetVitalCard(data: wallStreet)
                                .frame(width: 185)
                        }
                    }
                    .padding(.horizontal, AppSpacing.lg)
                }
            }
        }
    }
}

#Preview {
    ReportKeyVitalsSection(vitals: TickerReportData.sampleOracle.keyVitals)
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
