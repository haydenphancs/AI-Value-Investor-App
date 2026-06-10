//
//  ReportForecastChart.swift
//  ios
//
//  Molecule: Revenue forecast bar chart with EPS line overlay. Now a FALLBACK —
//  the Future Forecast module shows the inline ReportEarningsTimelinePanel when
//  the report carries annual_timeline; this renders only for older reports that
//  predate it. Company Guidance was lifted up to ReportFutureForecastSection so
//  it shows in both paths.
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
                    BarMark(
                        x: .value("Period", projection.period),
                        y: .value("Revenue", projection.revenue),
                        width: .ratio(0.4)
                    )
                    .foregroundStyle(
                        projection.isForecast
                            ? AppColors.primaryBlue.opacity(0.6)
                            : AppColors.primaryBlue
                    )
                    .cornerRadius(AppCornerRadius.small)
                    .annotation(position: .top, spacing: 4) {
                        VStack(spacing: 1) {
                            if let yoy = projection.revenueYoYText {
                                Text(yoy)
                                    .font(AppTypography.caption)
                                    .foregroundColor(projection.revenueYoYColor)
                            }
                            Text(projection.revenueLabel)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }
                    }
                }
                
                // EPS line (scaled to revenue axis) - as a continuous series
                ForEach(forecast.projections) { projection in
                    LineMark(
                        x: .value("Period", projection.period),
                        y: .value("EPS", projection.eps * epsScaleFactor)
                    )
                    .foregroundStyle(AppColors.accentYellow)
                    .lineStyle(StrokeStyle(lineWidth: 2))
                    .interpolationMethod(.linear)
                }
                
                // EPS dots
                ForEach(forecast.projections) { projection in
                    PointMark(
                        x: .value("Period", projection.period),
                        y: .value("EPS", projection.eps * epsScaleFactor)
                    )
                    .foregroundStyle(AppColors.accentYellow)
                    .symbolSize(40)
                    .annotation(position: .bottom, spacing: 4) {
                        VStack(spacing: 1) {
                            Text(projection.epsLabel)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.accentYellow)
                            if let yoy = projection.epsYoYText {
                                Text(yoy)
                                    .font(AppTypography.caption)
                                    .foregroundColor(projection.epsYoYColor)
                            }
                        }
                    }
                }
            }
            .chartYAxis(.hidden)
            .chartXAxis {
                AxisMarks(values: .automatic) { _ in
                    AxisValueLabel()
                        .font(AppTypography.caption)
                        .foregroundStyle(AppColors.textSecondary)
                }
            }
            .frame(height: 140)

            // Legend row
            HStack(spacing: AppSpacing.lg) {
                HStack(spacing: AppSpacing.xs) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(AppColors.primaryBlue)
                        .frame(width: 12, height: 12)
                    Text("Revenue")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                HStack(spacing: AppSpacing.xs) {
                    Circle()
                        .fill(AppColors.accentYellow)
                        .frame(width: 8, height: 8)
                    Text("EPS")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
            }
            .frame(maxWidth: .infinity)
        }
    }
}

#Preview {
    ReportForecastChart(forecast: TickerReportData.sampleOracle.revenueForecast)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
