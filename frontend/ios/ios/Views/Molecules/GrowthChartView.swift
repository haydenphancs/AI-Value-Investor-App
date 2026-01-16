//
//  GrowthChartView.swift
//  ios
//
//  Molecule: Combined bar and line chart displaying growth data using Swift Charts
//

import SwiftUI
import Charts

struct GrowthChartView: View {
    let dataPoints: [GrowthDataPoint]

    // Chart configuration
    private let chartHeight: CGFloat = 220
    private let yAxisWidth: CGFloat = 45

    // Computed properties for chart bounds
    private var maxBarValue: Double {
        (dataPoints.map { $0.value }.max() ?? 1) * 1.15
    }

    private var minBarValue: Double {
        0 // Start from 0 for bar charts
    }

    private var yoyValues: [Double] {
        dataPoints.map { $0.yoyChangePercent }
    }

    private var sectorValues: [Double] {
        dataPoints.map { $0.sectorAverageYoY }
    }

    private var allPercentValues: [Double] {
        yoyValues + sectorValues
    }

    private var maxYoY: Double {
        max((allPercentValues.max() ?? 10) * 1.2, 20)
    }

    private var minYoY: Double {
        min((allPercentValues.min() ?? -10) * 1.2, -20)
    }

    // Grid line values (4 horizontal lines)
    private var gridValues: [Double] {
        let step = maxBarValue / 4
        return [step, step * 2, step * 3]
    }

    var body: some View {
        VStack(spacing: 0) {
            // Chart with bars and lines
            chartContent

            // X-axis labels (periods)
            xAxisLabels

            // YoY percentage values below chart
            yoyPercentageLabels
        }
    }

    // MARK: - Chart Content

    private var chartContent: some View {
        HStack(alignment: .top, spacing: 0) {
            // Y-axis labels for bar values
            barYAxisLabels
                .frame(width: yAxisWidth)

            // Main chart area
            Chart {
                // Horizontal grid lines (behind everything)
                ForEach(gridValues, id: \.self) { value in
                    RuleMark(y: .value("Grid", value))
                        .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.5))
                        .lineStyle(StrokeStyle(lineWidth: 0.5))
                }

                // Bar marks for absolute values
                ForEach(dataPoints) { dataPoint in
                    BarMark(
                        x: .value("Period", dataPoint.period),
                        y: .value("Value", dataPoint.value),
                        width: dataPoints.count > 6 ? .fixed(20) : .automatic
                    )
                    .foregroundStyle(AppColors.growthBarBlue)
                    .cornerRadius(4)
                }

                // YoY line - single continuous line
                ForEach(dataPoints) { dataPoint in
                    LineMark(
                        x: .value("Period", dataPoint.period),
                        y: .value("YoY", normalizeYoY(dataPoint.yoyChangePercent)),
                        series: .value("Series", "YoY")
                    )
                    .foregroundStyle(AppColors.growthYoYYellow)
                    .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                    .interpolationMethod(.catmullRom)
                }

                // YoY points
                ForEach(dataPoints) { dataPoint in
                    PointMark(
                        x: .value("Period", dataPoint.period),
                        y: .value("YoY", normalizeYoY(dataPoint.yoyChangePercent))
                    )
                    .foregroundStyle(AppColors.growthYoYYellow)
                    .symbolSize(50)
                }

                // Sector average line - single continuous dashed line
                ForEach(dataPoints) { dataPoint in
                    LineMark(
                        x: .value("Period", dataPoint.period),
                        y: .value("Sector", normalizeYoY(dataPoint.sectorAverageYoY)),
                        series: .value("Series", "Sector")
                    )
                    .foregroundStyle(AppColors.growthSectorGray)
                    .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round, dash: [6, 4]))
                    .interpolationMethod(.catmullRom)
                }

                // Sector average points
                ForEach(dataPoints) { dataPoint in
                    PointMark(
                        x: .value("Period", dataPoint.period),
                        y: .value("Sector", normalizeYoY(dataPoint.sectorAverageYoY))
                    )
                    .foregroundStyle(AppColors.growthSectorGray)
                    .symbolSize(35)
                }
            }
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .chartYScale(domain: minBarValue...maxBarValue)
            .chartPlotStyle { plotArea in
                plotArea
                    .background(Color.clear)
            }
            .frame(height: chartHeight)
        }
    }

    // MARK: - Y-Axis Labels

    private var barYAxisLabels: some View {
        VStack {
            Text(formatLargeNumber(maxBarValue * 0.9))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(formatLargeNumber(maxBarValue * 0.6))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(formatLargeNumber(maxBarValue * 0.3))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text("0")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(height: chartHeight)
        .padding(.trailing, AppSpacing.xs)
    }

    // MARK: - X-Axis Labels

    private var xAxisLabels: some View {
        HStack(spacing: 0) {
            Spacer()
                .frame(width: yAxisWidth)

            ForEach(dataPoints) { dataPoint in
                Text(dataPoint.period)
                    .font(.system(size: dataPoints.count > 6 ? 9 : 11))
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
        }
        .padding(.top, AppSpacing.sm)
    }

    // MARK: - YoY Percentage Labels

    private var yoyPercentageLabels: some View {
        HStack(spacing: 0) {
            // Yellow dot indicator
            GrowthLegendDot(color: AppColors.growthYoYYellow, size: 8)
                .frame(width: yAxisWidth)

            ForEach(dataPoints) { dataPoint in
                Text(String(format: "%.1f%%", dataPoint.yoyChangePercent))
                    .font(.system(size: dataPoints.count > 6 ? 8 : 10))
                    .foregroundColor(dataPoint.yoyChangePercent >= 0 ? AppColors.bullish : AppColors.bearish)
                    .frame(maxWidth: .infinity)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
        }
        .padding(.top, AppSpacing.sm)
    }

    // MARK: - Helper Functions

    /// Normalize YoY percentage to fit within the bar chart's value range
    private func normalizeYoY(_ yoyPercent: Double) -> Double {
        // Map YoY percentage range to bar value range
        // YoY typically ranges from minYoY to maxYoY
        // We want to map this to approximately 20%-80% of the bar chart height
        let yoyRange = maxYoY - minYoY
        let normalizedYoY = (yoyPercent - minYoY) / yoyRange // 0 to 1
        let targetMin = maxBarValue * 0.15
        let targetMax = maxBarValue * 0.75
        return targetMin + normalizedYoY * (targetMax - targetMin)
    }

    private func formatLargeNumber(_ number: Double) -> String {
        let absNumber = abs(number)
        if absNumber >= 1_000_000_000_000 {
            return String(format: "%.0fT", number / 1_000_000_000_000)
        } else if absNumber >= 1_000_000_000 {
            return String(format: "%.0fB", number / 1_000_000_000)
        } else if absNumber >= 1_000_000 {
            return String(format: "%.0fM", number / 1_000_000)
        } else if absNumber >= 1_000 {
            return String(format: "%.0fK", number / 1_000)
        } else if absNumber >= 1 {
            return String(format: "%.1f", number)
        }
        return String(format: "%.2f", number)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            GrowthChartView(
                dataPoints: GrowthSectionData.sampleData.revenueAnnual
            )
            .padding()
        }
    }
}
