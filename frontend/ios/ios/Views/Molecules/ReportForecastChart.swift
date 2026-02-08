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
            // Header
            HStack {
                Text("Revenue & EPS Forecast")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textSecondary)

                Spacer()
            }

            // Combined chart with bars and line
            Chart {
                // Revenue bars
                ForEach(forecast.projections) { projection in
                    BarMark(
                        x: .value("Period", projection.label),
                        y: .value("Revenue", projection.value),
                        width: .ratio(0.6)
                    )
                    .foregroundStyle(
                        projection.isForecast
                            ? AppColors.primaryBlue.opacity(0.6)
                            : AppColors.primaryBlue
                    )
                    .cornerRadius(AppCornerRadius.small)
                }
                
                // EPS line
                ForEach(forecast.epsProjections) { eps in
                    LineMark(
                        x: .value("Period", eps.label),
                        y: .value("EPS", eps.value * 20)  // Scale EPS to fit with revenue
                    )
                    .foregroundStyle(AppColors.growthYoYYellow)
                    .lineStyle(StrokeStyle(lineWidth: 2))
                    
                    PointMark(
                        x: .value("Period", eps.label),
                        y: .value("EPS", eps.value * 20)
                    )
                    .foregroundStyle(AppColors.growthYoYYellow)
                    .symbolSize(60)
                }
            }
            .chartYAxis(.hidden)
            .chartXAxis {
                AxisMarks { _ in
                    AxisValueLabel()
                        .font(AppTypography.caption)
                        .foregroundStyle(AppColors.textSecondary)
                }
            }
            .frame(height: 160)
            
            // EPS labels above chart area
            HStack(spacing: 0) {
                ForEach(forecast.epsProjections) { eps in
                    Text(eps.label)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.growthYoYYellow)
                        .frame(maxWidth: .infinity)
                }
            }

            // Legend
            HStack(spacing: AppSpacing.lg) {
                HStack(spacing: AppSpacing.xs) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(AppColors.primaryBlue)
                        .frame(width: 12, height: 12)
                    Text("Revenue: \(forecast.formattedCAGR)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
                
                HStack(spacing: AppSpacing.xs) {
                    Circle()
                        .fill(AppColors.growthYoYYellow)
                        .frame(width: 12, height: 12)
                    Text("EPS: \(forecast.formattedEPSGrowth)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
            }

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
