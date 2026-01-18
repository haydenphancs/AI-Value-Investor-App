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
    
    @State private var selectedPeriod: String?

    // Chart configuration
    private let chartHeight: CGFloat = 220
    private let yAxisWidth: CGFloat = 45
    
    // Computed property for selected data point
    private var selectedDataPoint: GrowthDataPoint? {
        guard let selectedPeriod = selectedPeriod else { return nil }
        return dataPoints.first { $0.period == selectedPeriod }
    }

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
    
    // Font sizes - Since we only show 5 labels for both annual and quarterly,
    // use larger sizes that match the original annual view
    private var labelFontSize: CGFloat {
        // Use 11pt for both since we're only showing 5 labels
        return 11
    }
    
    private var yoyFontSize: CGFloat {
        // Use 11pt (increased from 10pt) to make it more prominent
        return 11
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
                        width: dataPoints.count > 10 ? .fixed(12) : (dataPoints.count > 6 ? .fixed(18) : .automatic)
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
                    .interpolationMethod(.linear)
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
                    .interpolationMethod(.linear)
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
                
                // Selection indicator
                if let selectedDataPoint = selectedDataPoint {
                    RuleMark(x: .value("Selected", selectedDataPoint.period))
                        .foregroundStyle(AppColors.textPrimary.opacity(0.3))
                        .lineStyle(StrokeStyle(lineWidth: 2))
                        .annotation(position: .top, spacing: 8) {
                            selectionAnnotation(for: selectedDataPoint)
                        }
                }
            }
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .chartYScale(domain: minBarValue...maxBarValue)
            .chartXSelection(value: $selectedPeriod)
            .chartAngleSelection(value: $selectedPeriod)
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
        GeometryReader { geometry in
            let availableWidth = geometry.size.width - yAxisWidth
            let columnWidth = availableWidth / CGFloat(dataPoints.count)
            
            ZStack(alignment: .topLeading) {
                // Spacer for y-axis
                Color.clear
                    .frame(width: yAxisWidth, height: 1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                
                // Position labels
                ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                    if shouldShowLabel(for: dataPoint) {
                        Text(dataPoint.period)
                            .font(.system(size: labelFontSize, weight: .regular))
                            .foregroundColor(AppColors.textMuted)
                            .lineLimit(1)
                            .minimumScaleFactor(0.8)
                            .frame(width: columnWidth * 4, alignment: .leading)
                            .offset(x: yAxisWidth + (columnWidth * CGFloat(index)))
                    }
                }
            }
        }
        .frame(height: 20)
        .padding(.top, AppSpacing.sm)
    }

    // MARK: - YoY Percentage Labels

    private var yoyPercentageLabels: some View {
        GeometryReader { geometry in
            let availableWidth = geometry.size.width - yAxisWidth
            let columnWidth = availableWidth / CGFloat(dataPoints.count)
            
            ZStack(alignment: .topLeading) {
                // Yellow dot indicator
                GrowthLegendDot(color: AppColors.growthYoYYellow, size: 8)
                    .frame(width: yAxisWidth, alignment: .center)
                    .frame(maxWidth: .infinity, alignment: .leading)
                
                // Position YoY labels
                ForEach(Array(dataPoints.enumerated()), id: \.offset) { index, dataPoint in
                    if shouldShowLabel(for: dataPoint) {
                        Text(String(format: "%.1f%%", dataPoint.yoyChangePercent))
                            .font(.system(size: yoyFontSize, weight: .semibold))
                            .foregroundColor(dataPoint.yoyChangePercent >= 0 ? AppColors.bullish : AppColors.bearish)
                            .lineLimit(1)
                            .minimumScaleFactor(0.8)
                            .frame(width: columnWidth * 4, alignment: .leading)
                            .offset(x: yAxisWidth + (columnWidth * CGFloat(index)))
                    }
                }
            }
        }
        .frame(height: 20)
        .padding(.top, AppSpacing.sm)
    }
    
    // MARK: - Selection Annotation
    
    private func selectionAnnotation(for dataPoint: GrowthDataPoint) -> some View {
        VStack(spacing: 4) {
            Text(dataPoint.period)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textPrimary)
            
            Text(String(format: "%.1f%%", dataPoint.yoyChangePercent))
                .font(AppTypography.calloutBold)
                .foregroundColor(dataPoint.yoyChangePercent >= 0 ? AppColors.bullish : AppColors.bearish)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(AppColors.cardBackground)
                .shadow(color: Color.black.opacity(0.2), radius: 8, x: 0, y: 4)
        )
    }
    
    // MARK: - Helper Functions - Label Display
    
    private func shouldShowLabel(for dataPoint: GrowthDataPoint) -> Bool {
        // For quarterly data (more than 10 points), show only Q1 labels
        if dataPoints.count > 10 {
            return dataPoint.period.hasPrefix("Q1'")
        }
        // For annual data (5 or fewer points), show all labels
        return true
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

        ScrollView {
            GrowthSectionCard(
                growthData: GrowthSectionData.sampleData,
                onDetailTapped: {}
            )
            .padding()
        }
    }
}
