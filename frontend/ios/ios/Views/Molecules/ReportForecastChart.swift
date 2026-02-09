//
//  ReportForecastChart.swift
//  ios
//
//  Molecule: Revenue forecast bar chart with EPS line overlay and management guidance
//

import SwiftUI
import Charts

struct ReportForecastChart: View {
    let forecast: ReportRevenueForecast

    /// Scale EPS values into the revenue axis range so dots sit inside the bars
    private var epsScaleFactor: Double {
        let maxRevenue = forecast.projections.map(\.revenue).max() ?? 1
        let maxEPS = forecast.projections.map(\.eps).max() ?? 1
        // Place highest EPS dot at ~70% of max bar height
        return (maxRevenue * 0.70) / maxEPS
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Chart
            Chart {
                // Revenue bars
                ForEach(forecast.projections) { projection in
                    // Revenue bars
                    BarMark(
                        x: .value("Period", projection.period),
                        y: .value("Revenue", projection.revenue),
                        width: .ratio(0.5)
                    )
                    .foregroundStyle(
                        projection.isForecast
                            ? AppColors.primaryBlue.opacity(0.6)
                            : AppColors.primaryBlue
                    )
                    .cornerRadius(AppCornerRadius.small)
                    .annotation(position: .top, spacing: 4) {
                        Text(projection.revenueLabel)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }

                    // EPS line (scaled to revenue axis)
                    LineMark(
                        x: .value("Period", projection.period),
                        y: .value("EPS", projection.eps * epsScaleFactor)
                    )
                    .foregroundStyle(AppColors.accentYellow)
                    .lineStyle(StrokeStyle(lineWidth: 2))
                    .interpolationMethod(.catmullRom)

                    // EPS dots
                    PointMark(
                        x: .value("Period", projection.period),
                        y: .value("EPS", projection.eps * epsScaleFactor)
                    )
                    .foregroundStyle(AppColors.accentYellow)
                    .symbolSize(40)
                    .annotation(position: .bottom, spacing: 4) {
                        Text(projection.epsLabel)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.accentYellow)
                    }
                }
            }
            .chartYAxis(.hidden)
            .chartXAxis(.hidden)
            .frame(height: 140)

            // Legend row
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
                        .fill(AppColors.accentYellow)
                        .frame(width: 8, height: 8)
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
