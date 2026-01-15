//
//  GrowthChart.swift
//  ios
//
//  Molecule: Growth bar chart showing yearly/quarterly data with YoY growth percentages
//

import SwiftUI
import Charts

struct GrowthChart: View {
    let data: [GrowthDataPoint]
    let metricType: GrowthMetricType
    let showSectorAverage: Bool

    private let chartHeight: CGFloat = 180

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // Chart
            Chart {
                ForEach(data) { point in
                    // Value bar
                    BarMark(
                        x: .value("Period", point.period),
                        y: .value("Value", abs(point.value))
                    )
                    .foregroundStyle(
                        LinearGradient(
                            colors: [
                                point.value >= 0 ? Color(hex: "3B82F6") : AppColors.bearish,
                                point.value >= 0 ? Color(hex: "60A5FA") : Color(hex: "F87171")
                            ],
                            startPoint: .bottom,
                            endPoint: .top
                        )
                    )
                    .cornerRadius(4)
                }

                // Sector average line (if enabled)
                if showSectorAverage {
                    ForEach(data) { point in
                        if let sectorAvg = point.sectorAverage {
                            PointMark(
                                x: .value("Period", point.period),
                                y: .value("Sector", sectorAvg * (data.map { $0.value }.max() ?? 1) / 20)
                            )
                            .foregroundStyle(AppColors.neutral)
                            .symbolSize(30)
                        }
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .automatic) { value in
                    AxisValueLabel()
                        .foregroundStyle(AppColors.textMuted)
                        .font(AppTypography.caption)
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading) { value in
                    AxisGridLine()
                        .foregroundStyle(AppColors.cardBackgroundLight)
                    AxisValueLabel()
                        .foregroundStyle(AppColors.textMuted)
                        .font(AppTypography.caption)
                }
            }
            .chartPlotStyle { plotArea in
                plotArea
                    .background(Color.clear)
            }
            .frame(height: chartHeight)

            // YoY Growth percentages row
            HStack(spacing: 0) {
                ForEach(data) { point in
                    FinancialGrowthBadge(value: point.yoyGrowth, style: .compact)
                        .frame(maxWidth: .infinity)
                }
            }

            // Legend
            HStack(spacing: AppSpacing.lg) {
                FinancialChartLegendItem(color: Color(hex: "3B82F6"), label: "YoY", style: .dot)
                FinancialChartLegendItem(color: Color(hex: "F59E0B"), label: "Value", style: .dot)
                if showSectorAverage {
                    FinancialChartLegendItem(color: AppColors.neutral, label: "Sector Average (YoY)", style: .dot)
                }
            }
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            GrowthChart(
                data: GrowthDataPoint.revenueSampleData,
                metricType: .revenue,
                showSectorAverage: true
            )
            .padding()
        }
    }
}
