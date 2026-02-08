//
//  ReportForecastChart.swift
//  ios
//
//  Molecule: Revenue forecast bar chart with CAGR and management guidance
//

import SwiftUI
import Charts

struct ReportForecastChart: View {
    let forecast: ReportRevenueForecast

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Revenue Forecast header
            HStack {
                Text("Revenue Forecast")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textSecondary)

                Spacer()
            }

            // CAGR badge
            Text(forecast.formattedCAGR)
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.bullish)

            // Bar chart
            Chart {
                ForEach(forecast.projections) { projection in
                    BarMark(
                        x: .value("Period", projection.label),
                        y: .value("Revenue", projection.value),
                        width: .ratio(0.5)
                    )
                    .foregroundStyle(
                        projection.isForecast
                            ? AppColors.primaryBlue.opacity(0.5)
                            : AppColors.primaryBlue
                    )
                    .cornerRadius(AppCornerRadius.small)
                    .annotation(position: .top) {
                        Text(projection.label)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }
            .chartYAxis(.hidden)
            .chartXAxis(.hidden)
            .frame(height: 120)

            // Management Guidance
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                HStack(spacing: AppSpacing.sm) {
                    Text("Management Guidance")
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.textSecondary)
                }

                ReportSentimentBadge(
                    text: forecast.managementGuidance.rawValue,
                    textColor: forecast.managementGuidance.color,
                    backgroundColor: forecast.managementGuidance.backgroundColor
                )

                if let quote = forecast.guidanceQuote {
                    HStack(alignment: .top, spacing: AppSpacing.sm) {
                        Rectangle()
                            .fill(AppColors.primaryBlue)
                            .frame(width: 2)

                        Text("\"\(quote)\"")
                            .font(AppTypography.subheadline)
                            .foregroundColor(AppColors.textSecondary)
                            .italic()
                    }
                    .padding(.top, AppSpacing.xs)
                }
            }
        }
    }
}

#Preview {
    ReportForecastChart(forecast: TickerReportData.sampleOracle.revenueForecast)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
