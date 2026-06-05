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
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            ReportForecastChart(forecast: forecast)

            // Insight — Stage-B narrative explaining WHY the forward
            // trajectory looks the way it does. Hidden when the backend
            // didn't produce one (older cached reports / fallback path).
            if let insight = forecast.insight, !insight.isEmpty {
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "sparkles.2")
                            .foregroundStyle(
                                LinearGradient(
                                    colors: [.indigo],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                )
                            )
                            .font(AppTypography.iconDefault).fontWeight(.semibold)

                        Text("Insight")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundStyle(LinearGradient(
                                colors: [.indigo, .cyan],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            ))
                    }

                    Text(insight)
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textSecondary)
                        .lineSpacing(3)
                }
                .padding(AppSpacing.md)
            }
        }
    }
}

#Preview {
    ReportFutureForecastSection(forecast: TickerReportData.sampleOracle.revenueForecast)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
