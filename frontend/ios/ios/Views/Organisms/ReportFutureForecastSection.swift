//
//  ReportFutureForecastSection.swift
//  ios
//
//  Organism: Future Forecast deep dive content with revenue chart and management guidance
//

import SwiftUI

struct ReportFutureForecastSection: View {
    let forecast: ReportRevenueForecast

    var body: some View {
        ReportForecastChart(forecast: forecast)
    }
}

#Preview {
    ReportFutureForecastSection(forecast: TickerReportData.sampleOracle.revenueForecast)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
